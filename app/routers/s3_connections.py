"""
S3 Connection Router
====================

Customer-facing S3 STS connection setup and verification endpoints.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import select

from app.config import settings
from app.core.database import get_session_context
from app.models.s3_connection import S3Connection
from app.models.s3_scan_job import S3ScanJob
from app.models.s3_object_metadata import S3ObjectMetadata

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


def _policy_prefix(prefix: Optional[str]) -> str:
    return prefix or ""


def _trust_policy(connection: S3Connection) -> Dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "AWS": f"arn:aws:iam::{settings.ai_market_aws_account_id}:user/ai-market-backend-sts",
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
                "s3:prefix": [f"{prefix}*"],
            },
        }
        get_resource = f"arn:aws:s3:::{connection.bucket}/{prefix}*"

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


def _boto3_client(service_name: str, **kwargs):
    import boto3

    return boto3.client(service_name, **kwargs)


@router.get("/config", summary="Get S3 connection setup config")
async def get_config() -> S3ConfigResponse:
    return S3ConfigResponse(aws_account_id=settings.ai_market_aws_account_id)


@router.post("/", status_code=201, summary="Create S3 connection")
async def create_connection(body: S3ConnectionCreate) -> S3ConnectionResponse:
    connection = S3Connection(
        id=str(uuid.uuid4()),
        name=body.name,
        bucket=body.bucket,
        region=body.region,
        prefix=body.prefix or None,
        external_id=str(uuid.uuid4()),
        status="onboarding",
    )
    with get_session_context() as session:
        session.add(connection)
        session.commit()
        session.refresh(connection)
        return _to_response(connection, include_policies=True)


@router.get("/", summary="List S3 connections")
async def list_connections() -> List[S3ConnectionResponse]:
    with get_session_context() as session:
        rows = session.exec(select(S3Connection)).all()
        return [_to_response(row) for row in rows]


@router.get("/{connection_id}", summary="Get S3 connection")
async def get_connection(connection_id: str) -> S3ConnectionResponse:
    with get_session_context() as session:
        connection = session.get(S3Connection, connection_id)
        if not connection:
            raise HTTPException(status_code=404, detail="S3 connection not found")
        return _to_response(connection, include_policies=True)


@router.put("/{connection_id}/role-arn", summary="Set S3 role ARN")
async def set_role_arn(connection_id: str, body: S3ConnectionRoleArn) -> S3ConnectionResponse:
    if not ROLE_ARN_RE.match(body.role_arn):
        raise HTTPException(status_code=400, detail="Invalid IAM role ARN")

    with get_session_context() as session:
        connection = session.get(S3Connection, connection_id)
        if not connection:
            raise HTTPException(status_code=404, detail="S3 connection not found")

        connection.role_arn = body.role_arn
        connection.status = "configured"
        connection.error_message = None
        connection.updated_at = datetime.now(timezone.utc)
        session.add(connection)
        session.commit()
        session.refresh(connection)
        return _to_response(connection)


@router.post("/{connection_id}/verify", summary="Verify S3 connection")
async def verify_connection(connection_id: str) -> S3VerifyResponse:
    with get_session_context() as session:
        connection = session.get(S3Connection, connection_id)
        if not connection:
            raise HTTPException(status_code=404, detail="S3 connection not found")
        if not connection.role_arn:
            raise HTTPException(status_code=400, detail="S3 connection role ARN is not configured")

        try:
            sts_client = _boto3_client("sts", region_name=connection.region)
            assumed = sts_client.assume_role(
                RoleArn=connection.role_arn,
                RoleSessionName="aim-data-verify",
                ExternalId=connection.external_id,
            )
            credentials = assumed["Credentials"]
            s3_client = _boto3_client(
                "s3",
                region_name=connection.region,
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
            )
            s3_client.list_objects_v2(
                Bucket=connection.bucket,
                Prefix=connection.prefix or "",
                MaxKeys=1,
            )
        except Exception as exc:
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


@router.post("/{connection_id}/scan", summary="Scan the S3 bucket and record objects")
async def scan_connection(connection_id: str) -> Dict[str, Any]:
    """Assume the connection role and enumerate bucket objects into S3ObjectMetadata.

    Reuses the exact STS assume-role + s3 client path proven by verify_connection.
    A fresh scan clears prior object metadata for this connection so the seller
    always sees the current bucket contents.
    """
    import mimetypes

    with get_session_context() as session:
        connection = session.get(S3Connection, connection_id)
        if not connection:
            raise HTTPException(status_code=404, detail="S3 connection not found")
        if not connection.role_arn:
            raise HTTPException(status_code=400, detail="S3 connection role ARN is not configured")

        # Clear prior object metadata for a clean re-scan (history of scan jobs is kept).
        for old in session.exec(
            select(S3ObjectMetadata).where(S3ObjectMetadata.connection_id == connection_id)
        ).all():
            session.delete(old)
        session.commit()

        scan_job = S3ScanJob(id=str(uuid.uuid4()), connection_id=connection.id, status="running")
        session.add(scan_job)
        session.commit()
        session.refresh(scan_job)

        try:
            sts_client = _boto3_client("sts", region_name=connection.region)
            assumed = sts_client.assume_role(
                RoleArn=connection.role_arn,
                RoleSessionName="aim-data-scan",
                ExternalId=connection.external_id,
            )
            creds = assumed["Credentials"]
            s3_client = _boto3_client(
                "s3",
                region_name=connection.region,
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )
            paginator = s3_client.get_paginator("list_objects_v2")
            count = 0
            for page in paginator.paginate(Bucket=connection.bucket, Prefix=connection.prefix or ""):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.endswith("/"):
                        continue  # skip folder placeholder keys
                    ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
                    session.add(S3ObjectMetadata(
                        id=str(uuid.uuid4()),
                        connection_id=connection.id,
                        scan_job_id=scan_job.id,
                        object_key=key,
                        size_bytes=int(obj.get("Size", 0)),
                        content_type=ctype[:128],
                        last_modified=obj.get("LastModified") or datetime.now(timezone.utc),
                        etag=str(obj.get("ETag", "")).strip('"')[:128],
                    ))
                    count += 1
        except Exception as exc:  # noqa: BLE001
            now = datetime.now(timezone.utc)
            scan_job.status = "error"
            scan_job.error_message = str(exc)
            scan_job.completed_at = now
            scan_job.updated_at = now
            session.add(scan_job)
            session.commit()
            raise HTTPException(status_code=502, detail=f"Scan failed: {exc}")

        now = datetime.now(timezone.utc)
        scan_job.status = "completed"
        scan_job.completed_at = now
        scan_job.objects_enumerated = count
        scan_job.updated_at = now
        connection.last_scanned_at = now
        connection.updated_at = now
        session.add(scan_job)
        session.add(connection)
        session.commit()
        return {"scan_job_id": scan_job.id, "status": "completed", "objects_enumerated": count}


@router.get("/{connection_id}/objects", summary="List scanned S3 objects")
async def list_connection_objects(connection_id: str) -> List[Dict[str, Any]]:
    with get_session_context() as session:
        connection = session.get(S3Connection, connection_id)
        if not connection:
            raise HTTPException(status_code=404, detail="S3 connection not found")
        rows = session.exec(
            select(S3ObjectMetadata).where(S3ObjectMetadata.connection_id == connection_id)
        ).all()
        return [
            {
                "id": r.id,
                "object_key": r.object_key,
                "size_bytes": r.size_bytes,
                "content_type": r.content_type,
                "etag": r.etag,
                "dataset_id": r.dataset_id,
                "scan_job_id": r.scan_job_id,
            }
            for r in rows
        ]


@router.delete("/{connection_id}", status_code=204, summary="Delete S3 connection")
async def delete_connection(connection_id: str) -> Response:
    with get_session_context() as session:
        connection = session.get(S3Connection, connection_id)
        if not connection:
            raise HTTPException(status_code=404, detail="S3 connection not found")
        session.delete(connection)
        session.commit()
    return Response(status_code=204)
