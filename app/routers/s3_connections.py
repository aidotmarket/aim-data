"""
S3 Connection Router
====================

Customer-facing S3 STS connection setup and verification endpoints.
"""

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiofiles
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import select

from app.auth.api_key_auth import AuthenticatedUser, get_current_user
from app.config import settings
from app.core.database import get_session_context
from app.models.dataset import DatasetRecord
from app.models.s3_connection import S3Connection
from app.models.s3_object_metadata import S3ObjectMetadata
from app.models.s3_scan_job import S3ScanJob
from app.routers.datasets import process_dataset_task
from app.services.processing_service import DatasetRecord as ProcessingDatasetRecord
from app.services.processing_service import get_processing_service
from app.services.s3_broker_client import S3BrokerClient, S3BrokerError
from app.services.s3_scan_service import S3ScanService

router = APIRouter()

ROLE_ARN_RE = re.compile(r"^arn:aws:iam::\d{12}:role/.+$")


class S3ConnectionCreate(BaseModel):
    name: str = Field(..., max_length=255)
    bucket: str = Field(..., max_length=255)
    region: str = Field(..., max_length=64)
    prefix: Optional[str] = Field(default=None, max_length=512)


class S3ConnectionRoleArn(BaseModel):
    role_arn: str


class S3ConnectionResponse(BaseModel):
    id: str
    name: str
    bucket: str
    region: str
    prefix: Optional[str] = None
    role_arn: Optional[str] = None
    external_id: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    last_scanned_at: Optional[str] = None
    created_at: str
    updated_at: str
    trust_policy: Optional[Dict[str, Any]] = None
    permission_policy: Optional[Dict[str, Any]] = None


class S3VerifyResponse(BaseModel):
    status: str
    error_message: Optional[str] = None
    verified_at: Optional[str] = None


class S3ConfigResponse(BaseModel):
    aws_account_id: str


class S3ScanJobResponse(BaseModel):
    id: str
    connection_id: str
    status: str
    started_at: str
    completed_at: Optional[str] = None
    continuation_token: Optional[str] = None
    error_message: Optional[str] = None
    objects_enumerated: int
    created_at: str
    updated_at: str


class S3ObjectMetadataResponse(BaseModel):
    id: str
    connection_id: str
    scan_job_id: str
    object_key: str
    size_bytes: int
    content_type: str
    last_modified: str
    etag: str
    dataset_id: Optional[str] = None
    created_at: str
    updated_at: str


class S3ObjectsResponse(BaseModel):
    items: List[S3ObjectMetadataResponse]
    limit: int
    offset: int
    total: int


class S3ObjectRegisterRequest(BaseModel):
    dataset_id: Optional[str] = None
    listing_id: Optional[str] = None


class S3DatasetResponse(BaseModel):
    id: str
    original_filename: str
    storage_filename: str
    file_type: str
    file_size_bytes: int
    status: str
    listing_id: Optional[str] = None
    created_at: str
    updated_at: str


class S3ObjectRegisterResponse(BaseModel):
    dataset: S3DatasetResponse
    object: S3ObjectMetadataResponse


def _policy_prefix(prefix: Optional[str]) -> str:
    return (prefix or "").rstrip("/")


def _trust_policy(connection: S3Connection) -> Dict[str, Any]:
    principal_arn = (settings.ai_market_assume_role_principal_arn or "").strip()
    trusted_principal = principal_arn or f"arn:aws:iam::{settings.ai_market_aws_account_id}:root"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "AWS": trusted_principal,
                },
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "sts:ExternalId": connection.external_id,
                    },
                },
            }
        ],
    }


def _permission_policy(connection: S3Connection) -> Dict[str, Any]:
    prefix = _policy_prefix(connection.prefix)
    list_statement: Dict[str, Any] = {
        "Effect": "Allow",
        "Action": ["s3:ListBucket"],
        "Resource": f"arn:aws:s3:::{connection.bucket}",
    }
    get_resource = f"arn:aws:s3:::{connection.bucket}/*"
    if prefix:
        list_statement["Condition"] = {
            "StringLike": {
                "s3:prefix": [f"{prefix}/*"],
            },
        }
        get_resource = f"arn:aws:s3:::{connection.bucket}/{prefix}/*"

    return {
        "Version": "2012-10-17",
        "Statement": [
            list_statement,
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": get_resource,
            },
        ],
    }


def _to_response(connection: S3Connection, include_policies: bool = False) -> S3ConnectionResponse:
    response = S3ConnectionResponse(
        id=connection.id,
        name=connection.name,
        bucket=connection.bucket,
        region=connection.region,
        prefix=connection.prefix,
        role_arn=connection.role_arn,
        external_id=connection.external_id,
        status=connection.status,
        error_message=connection.error_message,
        last_scanned_at=connection.last_scanned_at.isoformat() if connection.last_scanned_at else None,
        created_at=connection.created_at.isoformat(),
        updated_at=connection.updated_at.isoformat(),
    )
    if include_policies:
        response.trust_policy = _trust_policy(connection)
        response.permission_policy = _permission_policy(connection)
    return response


def _scan_job_response(scan_job: S3ScanJob) -> S3ScanJobResponse:
    return S3ScanJobResponse(
        id=scan_job.id,
        connection_id=scan_job.connection_id,
        status=scan_job.status,
        started_at=scan_job.started_at.isoformat(),
        completed_at=scan_job.completed_at.isoformat() if scan_job.completed_at else None,
        continuation_token=scan_job.continuation_token,
        error_message=scan_job.error_message,
        objects_enumerated=scan_job.objects_enumerated,
        created_at=scan_job.created_at.isoformat(),
        updated_at=scan_job.updated_at.isoformat(),
    )


def _object_response(metadata: S3ObjectMetadata) -> S3ObjectMetadataResponse:
    return S3ObjectMetadataResponse(
        id=metadata.id,
        connection_id=metadata.connection_id,
        scan_job_id=metadata.scan_job_id,
        object_key=metadata.object_key,
        size_bytes=metadata.size_bytes,
        content_type=metadata.content_type,
        last_modified=metadata.last_modified.isoformat(),
        etag=metadata.etag,
        dataset_id=metadata.dataset_id,
        created_at=metadata.created_at.isoformat(),
        updated_at=metadata.updated_at.isoformat(),
    )


def _dataset_response(dataset: DatasetRecord | ProcessingDatasetRecord) -> S3DatasetResponse:
    storage_filename = getattr(dataset, "storage_filename", None)
    if storage_filename is None and getattr(dataset, "upload_path", None) is not None:
        storage_filename = dataset.upload_path.name
    status = dataset.status.value if hasattr(dataset.status, "value") else dataset.status
    return S3DatasetResponse(
        id=dataset.id,
        original_filename=dataset.original_filename,
        storage_filename=storage_filename or "",
        file_type=dataset.file_type,
        file_size_bytes=dataset.file_size_bytes,
        status=status,
        listing_id=dataset.listing_id,
        created_at=dataset.created_at.isoformat(),
        updated_at=dataset.updated_at.isoformat(),
    )


def _dataset_file_type(object_key: str) -> str:
    extension = os.path.splitext(object_key)[1].lstrip(".").lower()
    return extension or "unknown"


def _user_can_access_connection(connection: S3Connection, user: AuthenticatedUser) -> bool:
    if connection.owner_id == user.user_id:
        return True
    if connection.owner_id is None and "admin" in user.scopes:
        return True
    return False


def _get_connection_for_user(session, connection_id: str, user: AuthenticatedUser) -> S3Connection:
    connection = session.get(S3Connection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="S3 connection not found")
    if not _user_can_access_connection(connection, user):
        raise HTTPException(status_code=403, detail="S3 connection is not owned by this user")
    return connection


async def _download_presigned_object(url: str, destination) -> int:
    bytes_written = 0
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async with aiofiles.open(destination, "wb") as out_file:
                async for chunk in response.aiter_bytes(chunk_size=settings.chunk_size):
                    if not chunk:
                        continue
                    bytes_written += len(chunk)
                    await out_file.write(chunk)
    return bytes_written


@router.get("/config", summary="Get S3 connection setup config")
async def get_config(
    user: AuthenticatedUser = Depends(get_current_user),
) -> S3ConfigResponse:
    return S3ConfigResponse(aws_account_id=settings.ai_market_aws_account_id)


@router.post("/", status_code=201, summary="Create S3 connection")
async def create_connection(
    body: S3ConnectionCreate,
    user: AuthenticatedUser = Depends(get_current_user),
) -> S3ConnectionResponse:
    try:
        external_id = S3BrokerClient().get_external_id()
    except S3BrokerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    connection = S3Connection(
        id=str(uuid.uuid4()),
        owner_id=user.user_id,
        name=body.name,
        bucket=body.bucket,
        region=body.region,
        prefix=body.prefix or None,
        external_id=external_id,
        status="onboarding",
    )
    with get_session_context() as session:
        session.add(connection)
        session.commit()
        session.refresh(connection)
        return _to_response(connection, include_policies=True)


@router.get("/", summary="List S3 connections")
async def list_connections(user: AuthenticatedUser = Depends(get_current_user)) -> List[S3ConnectionResponse]:
    with get_session_context() as session:
        rows = session.exec(select(S3Connection).where(S3Connection.owner_id == user.user_id)).all()
        return [_to_response(row) for row in rows]


@router.get("/{connection_id}", summary="Get S3 connection")
async def get_connection(
    connection_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> S3ConnectionResponse:
    with get_session_context() as session:
        connection = _get_connection_for_user(session, connection_id, user)
        return _to_response(connection, include_policies=True)


@router.put("/{connection_id}/role-arn", summary="Set S3 role ARN")
async def set_role_arn(
    connection_id: str,
    body: S3ConnectionRoleArn,
    user: AuthenticatedUser = Depends(get_current_user),
) -> S3ConnectionResponse:
    if not ROLE_ARN_RE.match(body.role_arn):
        raise HTTPException(status_code=400, detail="Invalid IAM role ARN")

    with get_session_context() as session:
        connection = _get_connection_for_user(session, connection_id, user)

        connection.role_arn = body.role_arn
        connection.status = "configured"
        connection.error_message = None
        connection.updated_at = datetime.now(timezone.utc)
        session.add(connection)
        session.commit()
        session.refresh(connection)
        return _to_response(connection)


@router.post("/{connection_id}/verify", summary="Verify S3 connection")
async def verify_connection(
    connection_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> S3VerifyResponse:
    with get_session_context() as session:
        connection = _get_connection_for_user(session, connection_id, user)
        if not connection.role_arn:
            raise HTTPException(status_code=400, detail="S3 connection role ARN is not configured")

        try:
            result = S3BrokerClient().verify(
                role_arn=connection.role_arn,
                region=connection.region,
                bucket=connection.bucket,
                prefix=connection.prefix,
            )
            if result.get("status") != "verified":
                raise S3BrokerError(str(result.get("error_message") or "S3 broker verification failed."))
        except S3BrokerError as exc:
            connection.status = "error"
            connection.error_message = str(exc)
            connection.updated_at = datetime.now(timezone.utc)
            session.add(connection)
            session.commit()
            return S3VerifyResponse(status=connection.status, error_message=connection.error_message)

        verified_at = datetime.now(timezone.utc)
        connection.status = "verified"
        connection.last_scanned_at = verified_at
        connection.error_message = None
        connection.updated_at = verified_at
        session.add(connection)
        session.commit()
        return S3VerifyResponse(status=connection.status, verified_at=verified_at.isoformat())


@router.post("/{connection_id}/scan", summary="Scan S3 connection objects")
async def scan_connection(
    connection_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> S3ScanJobResponse:
    with get_session_context() as session:
        _get_connection_for_user(session, connection_id, user)
    try:
        scan_job = S3ScanService().scan_connection(connection_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="S3 connection not found") from None
    return _scan_job_response(scan_job)


@router.get("/{connection_id}/scan/{scan_job_id}", summary="Get S3 scan job")
async def get_scan_job(
    connection_id: str,
    scan_job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> S3ScanJobResponse:
    with get_session_context() as session:
        _get_connection_for_user(session, connection_id, user)
        scan_job = session.get(S3ScanJob, scan_job_id)
        if scan_job is None or scan_job.connection_id != connection_id:
            raise HTTPException(status_code=404, detail="S3 scan job not found")
        return _scan_job_response(scan_job)


@router.get("/{connection_id}/objects", summary="List scanned S3 objects")
async def list_objects(
    connection_id: str,
    limit: int = 100,
    offset: int = 0,
    dataset_linked: Optional[bool] = None,
    user: AuthenticatedUser = Depends(get_current_user),
) -> S3ObjectsResponse:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be non-negative")

    with get_session_context() as session:
        _get_connection_for_user(session, connection_id, user)

        stmt = select(S3ObjectMetadata).where(S3ObjectMetadata.connection_id == connection_id)
        if dataset_linked is True:
            stmt = stmt.where(S3ObjectMetadata.dataset_id.is_not(None))
        elif dataset_linked is False:
            stmt = stmt.where(S3ObjectMetadata.dataset_id.is_(None))

        rows = session.exec(stmt.order_by(S3ObjectMetadata.object_key)).all()
        page = rows[offset : offset + limit]
        return S3ObjectsResponse(
            items=[_object_response(row) for row in page],
            limit=limit,
            offset=offset,
            total=len(rows),
        )


@router.post("/{connection_id}/objects/{object_id}/register", summary="Register scanned S3 object as dataset")
async def register_object(
    connection_id: str,
    object_id: str,
    body: S3ObjectRegisterRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
) -> S3ObjectRegisterResponse:
    processing = get_processing_service()
    record: Optional[ProcessingDatasetRecord] = None

    with get_session_context() as session:
        connection = _get_connection_for_user(session, connection_id, user)
        if connection.status != "verified" or not connection.role_arn:
            raise HTTPException(status_code=409, detail="S3 connection must be verified before registering objects")

        metadata = session.get(S3ObjectMetadata, object_id)
        if metadata is None or metadata.connection_id != connection_id:
            raise HTTPException(status_code=404, detail="S3 object metadata not found")

        referenced_dataset: Optional[DatasetRecord] = None
        if body.dataset_id:
            referenced_dataset = session.get(DatasetRecord, body.dataset_id)
        elif body.listing_id:
            referenced_dataset = session.exec(select(DatasetRecord).where(DatasetRecord.listing_id == body.listing_id)).first()
        if referenced_dataset is not None:
            owned = session.exec(
                select(S3ObjectMetadata)
                .where(S3ObjectMetadata.dataset_id == referenced_dataset.id)
                .where(S3ObjectMetadata.connection_id == connection_id)
            ).first()
            if owned is None:
                raise HTTPException(status_code=403, detail="Dataset is not owned by this connection")

        object_key = metadata.object_key
        content_type = metadata.content_type
        source_connection_id = metadata.connection_id
        try:
            presigned = S3BrokerClient().presign_object(
                role_arn=connection.role_arn,
                region=connection.region,
                bucket=connection.bucket,
                object_key=object_key,
            )
        except S3BrokerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    original_filename = os.path.basename(object_key) or object_key
    record = processing.create_dataset(
        original_filename=original_filename,
        file_type=_dataset_file_type(object_key),
    )

    try:
        bytes_written = await _download_presigned_object(str(presigned["url"]), record.upload_path)
        record.file_size_bytes = bytes_written
        record.metadata.update(
            {
                "source_type": "s3",
                "source_connection_id": source_connection_id,
                "source_object_key": object_key,
                "content_type": content_type,
            }
        )
        record.listing_id = body.listing_id
        processing._save_record(record, record.upload_path.name)

        with get_session_context() as session:
            metadata = session.get(S3ObjectMetadata, object_id)
            if metadata is None or metadata.connection_id != connection_id:
                raise HTTPException(status_code=404, detail="S3 object metadata not found")
            metadata.dataset_id = record.id
            metadata.updated_at = datetime.now(timezone.utc)
            session.add(metadata)
            session.commit()
            session.refresh(metadata)

        background_tasks.add_task(process_dataset_task, record.id)

        return S3ObjectRegisterResponse(
            dataset=_dataset_response(record),
            object=_object_response(metadata),
        )
    except HTTPException:
        if record is not None:
            processing.delete_dataset(record.id)
        raise
    except httpx.HTTPStatusError as exc:
        if record is not None:
            processing.delete_dataset(record.id)
        raise HTTPException(
            status_code=502,
            detail=f"S3 object download failed with HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        if record is not None:
            processing.delete_dataset(record.id)
        raise HTTPException(status_code=502, detail="S3 object download failed") from exc
    except Exception as exc:
        if record is not None:
            processing.delete_dataset(record.id)
        raise HTTPException(status_code=500, detail=f"S3 object registration failed: {exc}") from exc


@router.delete("/{connection_id}", status_code=204, summary="Delete S3 connection")
async def delete_connection(
    connection_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    with get_session_context() as session:
        connection = _get_connection_for_user(session, connection_id, user)
        session.delete(connection)
        session.commit()
    return Response(status_code=204)
