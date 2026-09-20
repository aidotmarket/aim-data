"""
Marketplace Publish Router
==========================

BQ-VZ-PUBLISH Phase 3: Proxies listing publish requests from VZ frontend
to ai.market backend, signing with Ed25519 JWT.

Flow: VZ Frontend -> VZ Backend (this router) -> ai.market Backend
The Ed25519 private key lives on VZ backend only.
"""

import hashlib
import logging
import re
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL
from typing import Annotated, Any, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth.api_key_auth import get_current_user
from app.config import settings
from app.core.channel_config import CHANNEL
from app.core.crypto import DeviceCrypto
from app.core.database import get_session_context
from app.models.dataset import DatasetRecord, DatasetMember
from app.models.published_manifest import PublishedManifest
from app.services.dataset_manifest import build_manifest
from app.services.sample_upload_client import validate_sample_members, upload_samples
from app.services.marketplace_push_service import upload_member_chunks
from app.models.s3_connection import S3Connection
from app.models.s3_object_metadata import S3ObjectMetadata
from app.models.s3_scan_job import S3ScanJob
from app.services.registration_service import ensure_vz_install_registered
from app.services.listing_versioning import build_version_prefix
from app.services.marketplace_action_signer import (
    build_action_jwt,
    canonical_json_bytes,
    canonical_payload_hash,
)
from app.services.processing_service import ProcessingService, get_processing_service
from app.services.s3_publish_source_resolver import (
    NotS3PublishSource,
    S3PublishSourceResolution,
    S3PublishSourceResolutionError,
    resolve_s3_publish_source,
)
from app.services.serial_store import get_serial_store
from sqlmodel import select

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class MarketplacePublishRequest(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=5000)
    tags: list[str] = Field(default_factory=list)
    category: Optional[str] = None
    pricing_type: Literal["one_time", "subscription"] = "one_time"
    price_cents: int = Field(..., ge=0)
    row_count: Optional[int] = None
    column_names: Optional[list[str]] = None
    column_types: Optional[list[str]] = None
    file_format: Optional[str] = None
    file_size_bytes: Optional[int] = None
    schema_info: Optional[dict[str, Any]] = None
    compliance_details: Optional[dict[str, Any]] = None
    compliance_status: Optional[str] = None
    privacy_score: Optional[float] = Field(None, ge=0, le=10)
    secondary_categories: Optional[list[str]] = None
    model_provider: Optional[str] = None
    vz_dataset_id: str  # local VZ dataset ID, becomes vz_raw_listing_id


class MarketplacePublishResponse(BaseModel):
    status: str
    listing_id: Optional[str] = None
    marketplace_url: Optional[str] = None
    error: Optional[str] = None


class DisclosureSnapshotProxyRequest(BaseModel):
    model_config = {"extra": "forbid", "hide_input_in_errors": True}
    dataset_id: str = Field(..., min_length=1)
    approved_fields: dict[str, Any]
    sample_decision: Literal["none", "member_files"]
    approved_sample: None = None
    ai_training_notification_ack: bool
    ai_training_notification_text: str = Field(..., min_length=1)
    license: str = Field(..., min_length=1)
    approval_source: Literal["aim_channel"]
    source_publish_operation_id: str = Field(..., min_length=1)


async def _closed_legacy_none(request: Request):
    """Do not let FastAPI reflect rejected historical row payloads in 422s."""
    try:
        raw = await request.body()
        if len(raw) > 262144:
            raise ValueError
        from app.services.dataset_canonicalization import _pairs
        from app.models.dataset_commitment_schemas import reject_content
        data = json.loads(raw, object_pairs_hook=_pairs)
        if data.get("sample_decision") not in (("none", "member_files") if settings.multi_file_datasets_enabled else ("none",)) or data.get("approved_sample") is not None:
            raise ValueError
        reject_content(data.get("approved_fields"))
        return DisclosureSnapshotProxyRequest.model_validate(data)
    except Exception:
        raise HTTPException(status_code=422, detail="legacy_sample_unavailable") from None


class DisclosureSnapshotProxyResponse(BaseModel):
    status: str
    listing_id: str
    disclosure_version: Optional[str] = None


class DatasetReattestationProxyRequest(BaseModel):
    model_config = {"extra": "forbid", "hide_input_in_errors": True}
    dataset_id: str = Field(..., min_length=1)


class DatasetReattestationProxyResponse(BaseModel):
    status: Literal["complete"]
    listing_id: str
    commitment_id: str
    attestation_id: str
    signed_at: str
    retryable: Literal[False] = False


def _reattestation_error(status_code: int, code: str, message: str, retryable: bool = False):
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "retryable": retryable},
    )


class VersionPublishEmit(BaseModel):
    """Strict allowlist for the receiver's VersionPublish contract."""

    model_config = {"extra": "forbid"}

    version_label: str = Field(..., min_length=1, max_length=64)
    object_count: int = Field(..., ge=0)
    total_size_bytes: int = Field(..., ge=0)
    manifest_hash: str = Field(..., min_length=1, max_length=256)
    source_kind: Literal["s3", "aim_data_local"] = "s3"
    members_total: Optional[int] = Field(None, ge=1)
    sample_members_total: Optional[int] = Field(None, ge=0)
    members_upload_id: Optional[UUID] = None


class S3ConnectionPublishEmit(BaseModel):
    """Strict allowlist for the receiver's S3ConnectionPublish contract."""

    model_config = {"extra": "forbid"}

    bucket: str
    region: str
    role_arn: str
    prefix: str
    serial_id: str


class MarketplaceVersionPublishRequest(MarketplacePublishRequest):
    s3_connection_id: Optional[str] = None
    scan_job_id: Optional[str] = None
    version_label: str = Field(..., min_length=1, max_length=64)


class MarketplaceVersionPublishResponse(MarketplacePublishResponse):
    version_id: Optional[str] = None
    version_label: str
    version_status: Optional[str] = None
    quarantine_reason: Optional[str] = None


class MarketplaceVersionConfirmResponse(BaseModel):
    version_id: str
    listing_id: str
    version_label: str
    status: str
    quarantine_reason: Optional[str] = None
    result: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VZ_SERIAL_RE = re.compile(r"\bVZ-[A-Za-z0-9][A-Za-z0-9_-]{6,}\b")
HMAC_HEX_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")
ATTESTATION_HASH_RE = re.compile(r"^(?:[a-fA-F0-9]{64}|[a-fA-F0-9]{128})$")
ATTESTATION_HASH_PATHS = {
    ("schema_info", "attestation", "data_hash"),
    ("schema_info", "attestation", "attestation_hash"),
}


def _jcs_canonical_bytes(body: dict) -> bytes:
    """RFC 8785 JCS-style canonical bytes used by sender/backend parity tests."""
    return canonical_json_bytes(body)


def _jcs_hash(body: dict) -> str:
    """RFC 8785 JCS-style canonical hash (sorted keys, compact separators)."""
    return canonical_payload_hash(body)


def _build_jwt(seller_id: str, install_id: str, metadata_hash: str, ed_priv) -> str:
    """Create a short-lived EdDSA JWT for the publish action."""
    return build_action_jwt(
        seller_id=seller_id,
        install_id=install_id,
        action="publish_listing",
        payload_hash=metadata_hash,
        private_key=ed_priv,
        hash_claim="metadata_hash",
    )


def _get_crypto() -> DeviceCrypto:
    """Get an initialized DeviceCrypto instance."""
    if not settings.keystore_passphrase:
        raise HTTPException(
            status_code=503,
            detail="Keystore passphrase not configured — cannot sign marketplace requests",
        )
    return DeviceCrypto(
        keystore_path=settings.keystore_path,
        passphrase=settings.keystore_passphrase,
    )


def _build_s3_connection_emit(resolution: S3PublishSourceResolution) -> dict[str, str]:
    """Build the exact allowlisted s3_connection block sent to ai.market."""
    return S3ConnectionPublishEmit(
        bucket=resolution.bucket,
        region=resolution.region,
        role_arn=resolution.role_arn,
        prefix=resolution.prefix,
        serial_id=str(resolution.serial_id),
    ).model_dump(mode="json")


def _build_version_emit(version: VersionPublishEmit) -> dict[str, Any]:
    if version.source_kind == "s3":
        if version.members_total is not None or version.members_upload_id is not None or version.sample_members_total is not None:
            raise HTTPException(422, "members fields require aim_data_local")
        return version.model_dump(mode="json", exclude={"source_kind", "members_total", "sample_members_total", "members_upload_id"})
    if not settings.multi_file_datasets_enabled:
        raise HTTPException(404, "Not found")
    if version.members_total is None or version.members_upload_id is None or version.sample_members_total is None:
        raise HTTPException(422, "Local version requires members_total, sample_members_total and members_upload_id")
    if version.members_total - version.sample_members_total < 1:
        raise HTTPException(409, "paid_set_required: at least one non-sample data member is required")
    return version.model_dump(mode="json")


def _build_publish_payload(
    body: MarketplacePublishRequest,
    s3_source: NotS3PublishSource | S3PublishSourceResolution,
    versions: Optional[list[VersionPublishEmit]] = None,
) -> dict[str, Any]:
    """Build the single canonical publish payload used for wire body and hash."""
    payload = body.model_dump(exclude_none=True)
    payload["vz_raw_listing_id"] = payload.pop("vz_dataset_id")
    payload["download_channel"] = CHANNEL.value
    if isinstance(s3_source, S3PublishSourceResolution):
        payload["s3_connection"] = _build_s3_connection_emit(s3_source)
    if versions:
        payload["versions"] = [_build_version_emit(version) for version in versions]
        if any(version.source_kind == "aim_data_local" for version in versions):
            payload["agent_version"] = settings.app_version
    return payload


def _manifest_hash_for_scan_rows(rows: list[S3ObjectMetadata]) -> str:
    """Scan-snapshot digest including scan-time last_modified/etag metadata.

    Receivers treat this as an opaque immutability key for the version label,
    not as a digest of current live S3 state.
    """
    manifest = [
        {
            "etag": row.etag,
            "key": row.object_key,
            "last_modified": row.last_modified.isoformat(),
            "size": row.size_bytes,
        }
        for row in rows
    ]
    return _jcs_hash({"objects": manifest})


def _response_version(data, label):
    if isinstance(data.get("version"), dict):
        return data["version"]
    return next((v for v in data.get("versions", []) if v.get("version_label") == label), {})


def _local_member_profiles(metadata, members, registration_to_published_index=None):
    """Project chunk B's stored outcomes onto the Gate 2 section 3a carrier."""
    directory = metadata.get("directory_profile")
    outcomes = directory.get("members") if isinstance(directory, dict) else None
    if not isinstance(outcomes, dict):
        return []
    source_indices = {published: int(registration) for registration, published in
                      (registration_to_published_index or {}).items()}
    profiles = []
    for member in members:
        if member["role"] != "data":
            continue
        outcome = outcomes.get(str(source_indices.get(member["index"], member["index"])))
        if not isinstance(outcome, dict) or outcome.get("status") != "profiled":
            continue
        profile = outcome.get("profile")
        columns = profile.get("columns") if isinstance(profile, dict) else None
        if not isinstance(columns, list) or any(
            not isinstance(column, dict)
            or not isinstance(column.get("name"), str)
            or not isinstance(column.get("type"), str)
            for column in columns
        ):
            continue
        profiles.append({"index": member["index"], "columns": [
            {"name": column["name"], "type": column["type"]} for column in columns
        ]})
    return profiles


def _local_metadata(record):
    if record is None:
        raise HTTPException(409, "dataset_removed_during_publish")
    try:
        metadata = json.loads(record.metadata_json or "{}")
    except (TypeError, ValueError):
        raise HTTPException(409, "local_publish_metadata_invalid") from None
    if not isinstance(metadata, dict) or not isinstance(metadata.get("local_publish", {}), dict):
        raise HTTPException(409, "local_publish_metadata_invalid")
    return metadata


def _local_publish_snapshot(dataset_id, version_label=None):
    if not settings.multi_file_datasets_enabled:
        return None
    with get_session_context() as session:
        record = session.get(DatasetRecord, dataset_id)
        metadata = _local_metadata(record)
        if not record.root_path:
            return None
        if metadata.get("source_type") == "s3":
            return None
        if session.exec(select(S3ObjectMetadata).where(S3ObjectMetadata.dataset_id == dataset_id)).first():
            return None
        progress = metadata.get("local_publish", {})
        retained = session.get(PublishedManifest, (progress.get("version_id", ""), progress.get("manifest_hash", ""))) if progress else None
        if retained and progress.get("status") == "pending_members":
            manifest = build_manifest(retained.members)
            index_mapping = retained.registration_to_published_index or {str(m["index"]): m["index"] for m in retained.members}
            root = retained.root_path
            label = progress["version_label"]
            offset = progress.get("offset", 0)
            accepted_samples = progress.get("accepted_sample_indices", [])
        else:
            rows = session.exec(select(DatasetMember).where(DatasetMember.dataset_id == dataset_id,
                DatasetMember.status != "removed").order_by(DatasetMember.index)).all()
            try:
                if any(m.status == "missing" for m in rows):
                    raise ValueError("missing dataset member; re-register before publish")
                index_mapping = {str(row.index): position for position, row in enumerate(rows)}
                manifest = build_manifest([dict(row.model_dump(), index=position)
                                           for position, row in enumerate(rows)])
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            root = str(Path(record.root_path).resolve())
            label = version_label or f"manifest-{manifest['manifest_hash'][:32]}"
            offset = 0
            accepted_samples = []
        if manifest["data_member_count"] - manifest["sample_member_count"] < 1:
            raise HTTPException(409, "paid_set_required: data_member_count - sample_member_count must be >= 1")
        try:
            validate_sample_members(manifest["members"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        upload_id = uuid5(NAMESPACE_URL, canonical_json_bytes([dataset_id, root, label, manifest["manifest_hash"]]).decode())
        return {"dataset_id": dataset_id, "root_path": root, "manifest": manifest, "offset": offset,
            "accepted_sample_indices": accepted_samples,
            "registration_to_published_index": index_mapping,
            "member_profiles": _local_member_profiles(metadata, manifest["members"], index_mapping),
            "retained_version_id": progress.get("version_id") if retained and progress.get("status") == "pending_members" else None,
            "version": VersionPublishEmit(version_label=label, object_count=manifest["data_member_count"],
                total_size_bytes=manifest["total_data_bytes"], manifest_hash=manifest["manifest_hash"],
                source_kind="aim_data_local", members_total=manifest["member_count"],
                sample_members_total=manifest["sample_member_count"], members_upload_id=upload_id)}


def _record_local_publish(local, listing_id, version_id, status):
    """Record the publish and frozen bytes authority in ONE transaction."""
    with get_session_context() as session:
        record = session.get(DatasetRecord, local["dataset_id"])
        if record is None:
            raise HTTPException(409, "dataset_removed_during_publish")
        key = (version_id, local["manifest"]["manifest_hash"])
        existing = session.get(PublishedManifest, key)
        if existing and (existing.root_path != local["root_path"] or existing.members != local["manifest"]["members"]):
            raise HTTPException(409, "published_manifest_conflict")
        if not existing:
            session.add(PublishedManifest(listing_version_id=version_id, manifest_hash=key[1],
                dataset_id=record.id, root_path=local["root_path"], members=local["manifest"]["members"],
                registration_to_published_index=local["registration_to_published_index"]))
        metadata = _local_metadata(record)
        metadata["local_publish"] = {"version_id": version_id, "manifest_hash": key[1],
            "version_label": local["version"].version_label, "status": status,
            "offset": local.get("offset", 0),
            "accepted_sample_indices": local.get("accepted_sample_indices", [])}
        record.metadata_json = json.dumps(metadata)
        record.listing_id = listing_id
        session.add(record)
        session.commit()


def _local_progress(dataset_id, offset, status, sample_index=None):
    with get_session_context() as session:
        record = session.get(DatasetRecord, dataset_id)
        metadata = _local_metadata(record)
        if not metadata.get("local_publish"):
            raise HTTPException(409, "local_publish_progress_missing")
        metadata["local_publish"].update(offset=offset, status=status)
        if sample_index is not None:
            accepted = set(metadata["local_publish"].get("accepted_sample_indices", []))
            accepted.add(sample_index)
            metadata["local_publish"]["accepted_sample_indices"] = sorted(accepted)
        record.metadata_json = json.dumps(metadata)
        session.add(record)
        session.commit()


def _manifest_hash_for_scan_job(session, scan_job: S3ScanJob) -> str:
    rows = session.exec(
        select(S3ObjectMetadata)
        .where(S3ObjectMetadata.scan_job_id == scan_job.id)
        .order_by(S3ObjectMetadata.object_key)
    ).all()
    return _manifest_hash_for_scan_rows(rows)


def _version_emit_from_scan(
    *,
    connection_id: str,
    scan_job_id: str,
    version_label: str,
    user,
) -> VersionPublishEmit:
    with get_session_context() as session:
        connection = session.get(S3Connection, connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="S3 connection not found")
        if connection.owner_id != user.user_id:
            raise HTTPException(status_code=403, detail="S3 connection is not owned by this user")
        try:
            version_prefix = build_version_prefix(str(connection.prefix or ""), version_label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid version prefix: {exc}") from exc

        scan_job = session.get(S3ScanJob, scan_job_id)
        if scan_job is None or scan_job.connection_id != connection_id:
            raise HTTPException(status_code=404, detail="S3 version scan job not found")
        if scan_job.status != "completed":
            raise HTTPException(status_code=409, detail="S3 version scan must complete before publish")
        rows = session.exec(
            select(S3ObjectMetadata)
            .where(S3ObjectMetadata.scan_job_id == scan_job.id)
            .order_by(S3ObjectMetadata.object_key)
        ).all()
        if not rows:
            raise HTTPException(status_code=409, detail="S3 version scan found no objects under the version prefix")
        if any(not row.object_key.startswith(version_prefix) for row in rows):
            raise HTTPException(status_code=409, detail="S3 version scan does not match the requested version prefix")

        return VersionPublishEmit(
            version_label=version_label,
            object_count=len(rows),
            total_size_bytes=sum(row.size_bytes for row in rows),
            manifest_hash=_manifest_hash_for_scan_rows(rows),
        )


def _assert_no_sensitive_publish_values(payload: dict[str, Any]) -> None:
    """Reject accidental serial/hash material in seller-controlled publish fields."""

    def _check_string(candidate: str) -> None:
        if VZ_SERIAL_RE.search(candidate) or HMAC_HEX_RE.search(candidate):
            raise HTTPException(status_code=409, detail="Publish payload contains sensitive material")

    def _check_attestation_hash(value: Any) -> None:
        if not isinstance(value, str) or not ATTESTATION_HASH_RE.fullmatch(value):
            raise HTTPException(status_code=409, detail="Publish payload contains invalid attestation hash")

    def _walk(value: Any, path: tuple[str, ...]) -> None:
        if path in ATTESTATION_HASH_PATHS:
            _check_attestation_hash(value)
            return
        if isinstance(value, str):
            _check_string(value)
            return
        if isinstance(value, dict):
            for key, child in value.items():
                _walk(child, (*path, str(key)))
            return
        if isinstance(value, list):
            for child in value:
                _walk(child, path)

    for key in (
        "title",
        "description",
        "category",
        "vz_raw_listing_id",
        "tags",
        "schema_info",
        "compliance_details",
        "compliance_status",
        "secondary_categories",
        "model_provider",
    ):
        if key in payload:
            _walk(payload[key], (key,))


def _seller_auth_headers(request: Request) -> dict[str, str]:
    store = get_serial_store()
    incoming_auth = request.headers.get("Authorization", "")
    if incoming_auth.startswith("Bearer "):
        return {"Authorization": incoming_auth}
    if store.state.ai_market_access_token:
        return {"Authorization": f"Bearer {store.state.ai_market_access_token}"}
    incoming_api_key = request.headers.get("X-API-Key")
    if incoming_api_key:
        return {"X-API-Key": incoming_api_key}
    return {}


def _preview_signer(owner: str):
    from app.services.preview_signing_service import PreviewSigningService

    registration = get_serial_store().state
    if (
        not registration.vz_install_id
        or not registration.ai_market_seller_id
        or registration.ai_market_seller_id != owner
    ):
        raise _reattestation_error(
            403,
            "registration_owner_mismatch",
            "This AIM Data install is registered to a different seller. Sign in with the matching ai.market account.",
        )
    return PreviewSigningService(
        _get_crypto(),
        install_id=registration.vz_install_id,
        seller_id=owner,
    )


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_jcs_canonical_bytes(value)).hexdigest()


def _persist_disclosure_decision(
    processing: ProcessingService,
    record,
    *,
    status: Literal["draft", "publish_pending", "snapshot_pending", "complete", "failed"],
    listing_id: str,
    body: DisclosureSnapshotProxyRequest,
    disclosure_version: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    if settings.multi_file_datasets_enabled:
        with get_session_context() as session:
            current = session.get(DatasetRecord, record.id)
            progress = _local_metadata(current).get("local_publish")
            if progress:
                record.metadata["local_publish"] = progress
    existing = record.metadata.get("disclosure_decision")
    created_at = existing.get("created_at") if isinstance(existing, dict) else None
    decision = {
        "status": status,
        "source_publish_operation_id": body.source_publish_operation_id,
        "listing_id": listing_id,
        "disclosure_version": disclosure_version,
        "approved_fields_hash": _payload_hash(body.approved_fields),
        "sample_decision": body.sample_decision,
        "approved_sample_hash": None,
        "approved_sample_row_count": 0,
        "approved_sample_columns": [],
        "ai_training_notification_text": body.ai_training_notification_text,
        "license": body.license,
        "created_at": created_at or now,
        "updated_at": now,
        "last_error": last_error,
    }
    if status == "snapshot_pending":
        decision["approved_payload_replay"] = body.model_dump(exclude={"dataset_id"}, exclude_none=False)
    record.metadata["disclosure_decision"] = decision
    record.listing_id = listing_id
    storage_fn = record.upload_path.name if record.upload_path else record.id
    processing._save_record(record, storage_fn)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/marketplace/publish", response_model=MarketplacePublishResponse)
async def publish_to_marketplace(
    body: MarketplacePublishRequest,
    request: Request,
    user=Depends(get_current_user),
    processing: ProcessingService = Depends(get_processing_service),
):
    """Publish a dataset listing to ai.market via signed JWT proxy."""
    data = await publish_via_signed_proxy(body, request, user)
    listing_id = data.get("listing_id")
    if listing_id and not (settings.multi_file_datasets_enabled and data.get("version")) and hasattr(processing, "get_dataset"):
        record = processing.get_dataset(body.vz_dataset_id)
        if record:
            record.listing_id = str(listing_id)
            storage_fn = record.upload_path.name if record.upload_path else record.id
            processing._save_record(record, storage_fn)
    return MarketplacePublishResponse(
        status=data.get("status", "published"),
        listing_id=listing_id,
        marketplace_url=data.get("marketplace_url"),
        error=data.get("version", {}).get("quarantine_reason"),
    )


@router.post(
    "/marketplace/listings/{listing_id}/disclosure-snapshots",
    response_model=DisclosureSnapshotProxyResponse,
)
async def create_disclosure_snapshot(
    listing_id: str,
    body: Annotated[DisclosureSnapshotProxyRequest, Depends(_closed_legacy_none)],
    request: Request,
    user=Depends(get_current_user),
    processing: ProcessingService = Depends(get_processing_service),
):
    """Forward a seller-authorized disclosure snapshot request to ai.market."""
    if body.sample_decision not in (("none", "member_files") if settings.multi_file_datasets_enabled else ("none",)) or body.approved_sample is not None:
        raise HTTPException(status_code=422, detail="legacy_sample_unavailable")
    if not listing_id.strip():
        raise HTTPException(status_code=422, detail="listing_id is required")

    record = processing.get_dataset(body.dataset_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Dataset '{body.dataset_id}' not found")

    payload = body.model_dump(exclude={"dataset_id"}, exclude_none=False)
    if payload["sample_decision"] == "none":
        payload["approved_sample"] = None
    _persist_disclosure_decision(
        processing,
        record,
        status="snapshot_pending",
        listing_id=listing_id,
        body=body,
        last_error=None,
    )

    auth_headers = _seller_auth_headers(request)
    if not auth_headers:
        raise HTTPException(status_code=409, detail="Seller ai.market token or API key not available")

    url = f"{settings.ai_market_url}/api/v1/listings/{listing_id}/disclosure-snapshots"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    **auth_headers,
                    "Content-Type": "application/json",
                },
            )
    except httpx.ConnectError as exc:
        _persist_disclosure_decision(processing, record, status="snapshot_pending", listing_id=listing_id, body=body, last_error=str(exc))
        raise HTTPException(status_code=502, detail="Cannot reach ai.market — disclosure snapshot pending")
    except httpx.TimeoutException as exc:
        _persist_disclosure_decision(processing, record, status="snapshot_pending", listing_id=listing_id, body=body, last_error=str(exc))
        raise HTTPException(status_code=504, detail="ai.market disclosure snapshot timed out")

    try:
        data = resp.json()
    except Exception:
        data = {}
    if resp.status_code not in (200, 201):
        detail = data.get("detail") or data.get("error") or resp.text or f"ai.market returned {resp.status_code}"
        _persist_disclosure_decision(processing, record, status="snapshot_pending", listing_id=listing_id, body=body, last_error=str(detail))
        raise HTTPException(status_code=resp.status_code, detail=detail)

    disclosure_version = data.get("disclosure_version")
    try:
        _persist_disclosure_decision(
            processing,
            record,
            status="complete",
            listing_id=listing_id,
            body=body,
            disclosure_version=str(disclosure_version) if disclosure_version else None,
            last_error=None,
        )
    except Exception as exc:
        logger.exception("Disclosure snapshot succeeded but local audit persistence failed")
        raise HTTPException(
            status_code=500,
            detail="Disclosure status unknown: ai.market accepted the snapshot, but AIM Data could not store the local audit record.",
        ) from exc

    return DisclosureSnapshotProxyResponse(
        status="complete",
        listing_id=listing_id,
        disclosure_version=str(disclosure_version) if disclosure_version else None,
    )


@router.post(
    "/marketplace/listings/{listing_id}/commitments/{commitment_id}/reattest",
    response_model=DatasetReattestationProxyResponse,
)
async def reattest_dataset_commitment(
    listing_id: str,
    commitment_id: str,
    body: DatasetReattestationProxyRequest,
    request: Request,
    user=Depends(get_current_user),
    processing: ProcessingService = Depends(get_processing_service),
):
    """Recompute the local commitment before signing an unchanged-root claim."""
    from app.services.dataset_reattestation_service import (
        ReattestationError,
        build_signed_reattestation,
        persist_reattestation_state,
        stored_commitment,
        verify_unchanged_dataset,
    )
    from app.services.preview_signing_service import SigningError

    record = processing.get_dataset(body.dataset_id)
    if not record:
        raise _reattestation_error(
            404, "dataset_not_found", "The local dataset could not be found."
        )
    if record.listing_id != listing_id:
        raise _reattestation_error(
            409,
            "new_commitment_required",
            "The listing no longer matches this dataset. Publish a new version.",
        )
    owner = getattr(user, "user_id", None)
    if not owner or record.metadata.get("preview_owner_id") != owner:
        raise _reattestation_error(
            403,
            "dataset_owner_unverified",
            "This signed-in seller does not own the local dataset.",
        )

    try:
        stored = stored_commitment(
            record, listing_id=listing_id, commitment_id=commitment_id
        )
    except ReattestationError as exc:
        raise _reattestation_error(
            409,
            exc.code,
            "The published commitment is unavailable or no longer matches. Publish a new version.",
        ) from None

    auth_headers = _seller_auth_headers(request)
    if not auth_headers:
        raise _reattestation_error(
            403,
            "seller_auth_required",
            "Your ai.market sign-in has expired or is unavailable. Sign in and confirm again.",
        )

    try:
        signer = await run_in_threadpool(_preview_signer, owner)
        await run_in_threadpool(signer.check_available)
        await run_in_threadpool(
            verify_unchanged_dataset,
            processing,
            record,
            stored,
            upload_root=settings.upload_directory,
            temp_root=settings.data_directory,
        )
        payload = await run_in_threadpool(build_signed_reattestation, stored, signer)
    except ReattestationError as exc:
        changed = exc.code in {"dataset_changed", "new_commitment_required"}
        persist_reattestation_state(
            processing,
            record,
            status="new_commitment_required" if changed else "retryable_error",
            last_error=(
                "The data changed. Publish a new version."
                if changed
                else "The local freshness check could not finish. Try again."
            ),
            retryable=not changed,
            progress=None,
        )
        raise HTTPException(
            status_code=409 if changed else 503,
            detail={
                "code": exc.code,
                "message": (
                    "The data changed. Publish a new version."
                    if changed
                    else "The local freshness check could not finish. Try again."
                ),
                "retryable": not changed,
            },
        ) from None
    except SigningError as exc:
        persist_reattestation_state(
            processing,
            record,
            status="retryable_error",
            last_error=str(exc),
            retryable=True,
            progress=None,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": str(exc),
                "message": "The signing authority is unavailable. Try again.",
                "retryable": True,
            },
        ) from None
    except HTTPException as exc:
        retryable = exc.status_code >= 500
        persist_reattestation_state(
            processing,
            record,
            status="retryable_error" if retryable else "failed",
            last_error=str(exc.detail),
            retryable=retryable,
            progress=None,
        )
        raise

    url = (
        f"{settings.ai_market_url}/api/v1/listings/{listing_id}"
        f"/commitments/{commitment_id}/reattest"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={**auth_headers, "Content-Type": "application/json"},
            )
    except httpx.TimeoutException as exc:
        persist_reattestation_state(
            processing,
            record,
            status="retryable_error",
            last_error=str(exc),
            retryable=True,
            progress=None,
        )
        raise HTTPException(
            status_code=504,
            detail={
                "code": "timeout",
                "message": "ai.market timed out. Try again.",
                "retryable": True,
            },
        ) from None
    except httpx.TransportError as exc:
        persist_reattestation_state(
            processing,
            record,
            status="retryable_error",
            last_error=str(exc)[:500],
            retryable=True,
            progress=None,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "transport_error",
                "message": "The connection to ai.market was interrupted. Try again.",
                "retryable": True,
            },
        ) from None

    try:
        data = response.json()
    except Exception:
        data = {}
    detail = data.get("detail") or data.get("error") or response.text
    if response.status_code == 409 and detail == "new_commitment_required":
        persist_reattestation_state(
            processing,
            record,
            status="new_commitment_required",
            last_error="The data changed. Publish a new version.",
            retryable=False,
            progress=None,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "new_commitment_required",
                "message": "The data changed. Publish a new version.",
                "retryable": False,
            },
        )
    if response.status_code not in (200, 201):
        retryable = response.status_code >= 500
        persist_reattestation_state(
            processing,
            record,
            status="retryable_error" if retryable else "failed",
            last_error=str(detail or f"ai.market returned {response.status_code}"),
            retryable=retryable,
            progress=None,
        )
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "code": "reattestation_rejected",
                "message": str(detail or f"ai.market returned {response.status_code}"),
                "retryable": retryable,
            },
        )

    signed_at = str(data.get("signed_at") or payload["signed_at"])
    raw_attestation_id = data.get("attestation_id")
    if not raw_attestation_id:
        message = "ai.market did not return an attestation ID. Try again."
        persist_reattestation_state(
            processing,
            record,
            status="retryable_error",
            last_attempt_at=payload["signed_at"],
            last_error=message,
            retryable=True,
            progress=None,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "upstream_invalid_response",
                "message": message,
                "retryable": True,
            },
        )
    attestation_id = str(raw_attestation_id)
    persist_reattestation_state(
        processing,
        record,
        status="complete",
        last_confirmed_at=signed_at,
        last_attempt_at=payload["signed_at"],
        last_error=None,
        retryable=False,
        progress=None,
        attestation_id=attestation_id,
    )
    return DatasetReattestationProxyResponse(
        status="complete",
        listing_id=listing_id,
        commitment_id=commitment_id,
        attestation_id=attestation_id,
        signed_at=signed_at,
        retryable=False,
    )


@router.post("/marketplace/versions/publish", response_model=MarketplaceVersionPublishResponse)
async def publish_version_to_marketplace(
    body: MarketplaceVersionPublishRequest,
    request: Request,
    user=Depends(get_current_user),
):
    """Publish a new dataset version to ai.market via the signed publish proxy."""
    local = _local_publish_snapshot(body.vz_dataset_id, body.version_label)
    if local is not None:
        version = local["version"]
    else:
        if not body.s3_connection_id or not body.scan_job_id:
            raise HTTPException(422, "S3 connection and scan job are required")
        version = _version_emit_from_scan(
            connection_id=body.s3_connection_id,
            scan_job_id=body.scan_job_id,
            version_label=body.version_label,
            user=user,
        )
    publish_body = MarketplacePublishRequest(
        **body.model_dump(exclude={"s3_connection_id", "scan_job_id", "version_label"})
    )
    data = await publish_via_signed_proxy(publish_body, request, user, versions=[version])
    version_data = data.get("version") if isinstance(data.get("version"), dict) else {}
    versions_data = data.get("versions") if isinstance(data.get("versions"), list) else []
    if not version_data and versions_data:
        version_data = next(
            (item for item in versions_data if item.get("version_label") == body.version_label),
            versions_data[0],
        )
    return MarketplaceVersionPublishResponse(
        status=data.get("status", "published"),
        version_id=version_data.get("version_id"),
        listing_id=data.get("listing_id"),
        marketplace_url=data.get("marketplace_url"),
        version_label=body.version_label,
        version_status=version_data.get("status"),
        quarantine_reason=version_data.get("quarantine_reason"),
    )


@router.post("/marketplace/versions/{version_id}/confirm", response_model=MarketplaceVersionConfirmResponse)
async def confirm_marketplace_version(
    version_id: str,
    request: Request,
    user=Depends(get_current_user),
):
    """Approve a receiver-quarantined version for activation."""
    store = get_serial_store()
    incoming_auth = request.headers.get("Authorization", "")
    incoming_bearer = incoming_auth.removeprefix("Bearer ").strip() if incoming_auth.startswith("Bearer ") else None
    seller_token = store.state.ai_market_access_token or incoming_bearer
    if not seller_token:
        raise HTTPException(status_code=409, detail="Seller ai.market token not available")

    url = f"{settings.ai_market_url}/api/v1/vz/versions/{version_id}/confirm"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {seller_token}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Cannot reach ai.market — check network connectivity")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="ai.market request timed out")

    try:
        data = resp.json()
    except Exception:
        data = {}
    if resp.status_code not in (200, 201):
        detail = data.get("detail") or data.get("error") or resp.text or f"ai.market returned {resp.status_code}"
        raise HTTPException(status_code=resp.status_code, detail=detail)

    status = str(data.get("status") or "")
    if status == "superseded":
        result = "confirmed_but_superseded"
    else:
        result = "confirmed"
    return MarketplaceVersionConfirmResponse(
        version_id=str(data.get("version_id") or version_id),
        listing_id=str(data.get("listing_id") or ""),
        version_label=str(data.get("version_label") or ""),
        status=status,
        quarantine_reason=data.get("quarantine_reason"),
        result=result,
    )


async def publish_via_signed_proxy(
    body: MarketplacePublishRequest,
    request: Request,
    user,
    versions: Optional[list[VersionPublishEmit]] = None,
    s3_source_override: Optional[S3PublishSourceResolution] = None,
) -> dict[str, Any]:
    """Publish a dataset listing to ai.market via the canonical signed proxy."""
    # D3 bounds precede even registration/signing. No new reads on flag-off.
    local = _local_publish_snapshot(body.vz_dataset_id, versions[0].version_label if versions else None)
    if local is not None:
        versions = [local["version"]]
        schema_info = dict(body.schema_info or {})
        schema_info.pop("member_profiles", None)
        if local["member_profiles"]:
            schema_info["member_profiles"] = local["member_profiles"]
        body = body.model_copy(update={"schema_info": schema_info})
    # 1. Load crypto + keypairs
    crypto = _get_crypto()
    ed_priv, _ed_pub, _x_priv, _x_pub = crypto.get_or_create_keypairs()

    # 2. Resolve install_id (iss) and seller_id (sub)
    store = get_serial_store()
    cached = store.state.last_status_cache or {}
    seller_id = (
        store.state.ai_market_seller_id
        or (user.user_id if getattr(user, "key_id", "") == "ai_market_bearer" else None)
        or cached.get("gateway_user_id")
    )
    if not seller_id:
        raise HTTPException(
            status_code=409,
            detail="Seller identity not available — sign in with ai.market before publishing",
        )

    incoming_auth = request.headers.get("Authorization", "")
    incoming_bearer = incoming_auth.removeprefix("Bearer ").strip() if incoming_auth.startswith("Bearer ") else None
    install_id = await ensure_vz_install_registered(
        crypto,
        access_token=store.state.ai_market_access_token or incoming_bearer,
        seller_id=str(seller_id),
    )
    if not install_id:
        raise HTTPException(
            status_code=409,
            detail="VZ install registration not available — sign in with ai.market and try publishing again",
        )

    # 3. Resolve S3 provenance, then build the canonical payload for ai.market.
    # SECURITY: s3_source_override MUST be a resolution produced by an ownership-validating
    # resolver (e.g. resolve_s3_connection_publish_source, which enforces owner_id, verified
    # status, role_arn, and scope authorization). Callers must never pass an unvalidated
    # override -- doing so would bypass ownership/eligibility checks.
    if s3_source_override is not None:
        if not isinstance(s3_source_override, S3PublishSourceResolution):
            raise HTTPException(status_code=500, detail="Invalid publish source override")
        s3_source: NotS3PublishSource | S3PublishSourceResolution = s3_source_override
    else:
        try:
            s3_source = resolve_s3_publish_source(body.vz_dataset_id, user, store.state)
        except S3PublishSourceResolutionError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "S3-sourced dataset is not eligible for scoped-credential publish yet; "
                    f"verify the S3 connection and dataset source, then retry. reason={exc.reason}"
                ),
            )

    payload = _build_publish_payload(body, s3_source, versions=versions)
    _assert_no_sensitive_publish_values(payload)

    # 4. JCS hash + JWT
    metadata_hash = _jcs_hash(payload)
    token = _build_jwt(str(seller_id), install_id, metadata_hash, ed_priv)

    # 5. POST to ai.market
    url = f"{settings.ai_market_url}/api/v1/vz/publish"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Cannot reach ai.market — check network connectivity")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="ai.market request timed out")

    # 6. Return response
    if resp.status_code in (200, 201):
        data = resp.json()
        if local is None:
            return data
        # Retry the same publish if the receiver omitted its assigned key.
        for attempt in range(2):
            version_data = _response_version(data, local["version"].version_label)
            if version_data.get("version_id"):
                break
            async with httpx.AsyncClient(timeout=30.0) as client:
                retry = await client.post(url, json=payload, headers={
                    "Authorization": f"Bearer {_build_jwt(str(seller_id), install_id, metadata_hash, ed_priv)}",
                    "Content-Type": "application/json",
                })
            if retry.status_code not in (200, 201):
                raise HTTPException(retry.status_code, "local_publish_retry_failed")
            data = retry.json()
        version_data = _response_version(data, local["version"].version_label)
        if not version_data.get("version_id") or not data.get("listing_id"):
            raise HTTPException(502, "local_publish_missing_version_id; retry publish")
        version_id = str(version_data["version_id"])
        if local.get("retained_version_id") != version_id:
            local["offset"] = 0
            local["accepted_sample_indices"] = []
        _record_local_publish(local, str(data["listing_id"]), version_id, version_data.get("status", "pending_members"))

        async def post(path, signed_payload, *, action, content=None):
            signed = build_action_jwt(seller_id=str(seller_id), install_id=install_id,
                action=action, payload_hash=_jcs_hash(signed_payload), private_key=ed_priv,
                hash_claim="metadata_hash")
            headers = {"Authorization": f"Bearer {signed}"}
            if content is None:
                headers["Content-Type"] = "application/json"
                transport = {"json": signed_payload}
            else:
                headers.update({"Content-Type": "application/octet-stream",
                                "Content-Length": str(signed_payload["size_bytes"])})
                transport = {"content": content}
            try:
                async with httpx.AsyncClient(timeout=settings.sample_upload_timeout_s if content is not None else 30.0) as client:
                    response = await client.post(settings.ai_market_url.rstrip("/") + path,
                        headers=headers, **transport)
            except httpx.RequestError as exc:
                raise HTTPException(502, "pending_members: upload interrupted; retry publish") from exc
            if response.status_code not in (200, 201):
                try:
                    detail = response.json().get("detail", "pending_members")
                except ValueError:
                    detail = "pending_members"
                raise HTTPException(response.status_code, detail)
            return response.json()

        def checkpoint(offset, result):
            _local_progress(body.vz_dataset_id, offset, result.get("status", "pending_members"))

        def sample_checkpoint(index, result):
            _local_progress(body.vz_dataset_id, len(local["manifest"]["members"]),
                result.get("status", "pending_members"), sample_index=index)

        # A repeated publish returns the existing version status. In particular,
        # recovering a lost final-sample ACK must not resend to an active version.
        if version_data.get("status") == "pending_members":
            result = await upload_member_chunks(version_id=version_id,
                members_upload_id=local["version"].members_upload_id, members=local["manifest"]["members"],
                post=post, checkpoint=checkpoint, start_offset=local.get("offset", 0))
            version_data.update(result)
        if version_data.get("status") == "pending_members":
            try:
                sample_result = await upload_samples(dataset_id=body.vz_dataset_id, root_path=local["root_path"],
                    version_id=version_id, members_upload_id=local["version"].members_upload_id,
                    members=local["manifest"]["members"], post=post,
                    accepted_indices=local.get("accepted_sample_indices", []), checkpoint=sample_checkpoint)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            if sample_result:
                version_data.update(sample_result)
        _local_progress(body.vz_dataset_id, len(local["manifest"]["members"]), version_data.get("status", "pending_members"))
        data["version"] = version_data
        status = version_data.get("status")
        data["status"] = "published" if status == "active" else status if status in ("quarantined", "superseded") else "pending_members"
        return data

    # Error passthrough
    try:
        err_data = resp.json()
        detail = err_data.get("detail") or err_data.get("error") or str(err_data)
    except Exception:
        detail = resp.text or f"ai.market returned {resp.status_code}"

    logger.warning("ai.market publish failed (%d): %s", resp.status_code, detail)
    raise HTTPException(status_code=resp.status_code, detail=detail)


@router.get("/marketplace/publish-status")
async def publish_status(dataset_id: Optional[str] = None, user=Depends(get_current_user)):
    """Check if this AIM Data installation is ready to publish to ai.market."""
    progress = {}
    if settings.multi_file_datasets_enabled and dataset_id:
        with get_session_context() as session:
            record = session.get(DatasetRecord, dataset_id)
            progress = _local_metadata(record).get("local_publish", {})
            if progress.get("status") != "pending_members":
                progress = {}
    # Must have keystore passphrase
    if not settings.keystore_passphrase:
        return {**progress, "can_publish": False, "reason": "Keystore passphrase not configured"}

    # Must have keypairs
    try:
        crypto = _get_crypto()
        crypto.get_or_create_keypairs()
    except Exception as e:
        return {**progress, "can_publish": False, "reason": f"Keypair error: {e}"}

    # Must have device registration (platform keys)
    if not crypto.has_platform_keys():
        return {**progress, "can_publish": False, "reason": "Device not registered with ai.market"}

    return {**progress, "can_publish": True, "reason": None}


@router.post("/marketplace/listings/{listing_id}/at-a-glance/approve")
@router.post("/marketplace/listings/{listing_id}/at-a-glance/withdraw")
async def prepare_preview_disclosure(listing_id: str, request: Request, user=Depends(get_current_user)):
    """Validate locally, then forward the signed metadata-only disclosure."""
    from app.services.preview_signing_service import request_bytes, SigningError
    try:
        raw = await request.body()
        if len(raw) > 262144:
            raise SigningError("manifest_limit")
        import json
        from app.services.dataset_canonicalization import _pairs
        data = json.loads(raw, object_pairs_hook=_pairs)
        request_bytes(data)
        if data["binding"]["listing_id"] != listing_id:
            raise SigningError("listing_mismatch")
    except Exception:
        raise HTTPException(status_code=422, detail="preview_contract_invalid") from None
    action = "withdraw" if request.url.path.endswith("/withdraw") else "approve"
    headers = _seller_auth_headers(request)
    if not headers:
        raise HTTPException(status_code=401, detail="seller_session_required")
    headers["Content-Type"] = "application/json"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.ai_market_url.rstrip('/')}/api/v1/listings/{listing_id}/at-a-glance/{action}",
                content=raw,
                headers=headers,
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="marketplace_timeout") from None
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="marketplace_unreachable") from None
    if not 200 <= response.status_code < 300:
        try:
            detail = response.json().get("detail", "marketplace_preview_refused")
        except ValueError:
            detail = "marketplace_preview_refused"
        raise HTTPException(status_code=response.status_code, detail=detail)
    return response.json()


# Seller-local preview jobs share the authenticated marketplace namespace.
from app.routers.preview_builds import router as preview_builds_router  # noqa: E402
router.include_router(preview_builds_router, prefix="/marketplace")
