"""
S3 scan service for broker-backed, no-copy listing registration.
"""

from __future__ import annotations

import logging
import mimetypes
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import select

from app.core.database import get_session_context
from app.models.s3_connection import S3Connection
from app.models.s3_object_metadata import S3ObjectMetadata
from app.models.s3_scan_job import S3ScanJob
from app.services.s3_broker_client import S3BrokerClient, S3BrokerError, S3BrokerRateLimited

logger = logging.getLogger(__name__)

SAMPLE_MAX_OBJECTS = 1000
MAX_RATE_LIMIT_RETRIES = 5
RATE_LIMIT_RETRY_CAP_SECONDS = 60.0
INTER_PAGE_PACING_SECONDS = 0.05
RATE_LIMIT_EXHAUSTED_MESSAGE = (
    "Scan paused: the marketplace is rate-limiting bucket reads; it will resume automatically, retry shortly."
)

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _content_type_for_key(object_key: str) -> str:
    return mimetypes.guess_type(object_key)[0] or "application/octet-stream"


def _sampled_stats(
    *,
    object_count: int,
    total_size_bytes: int,
    type_histogram: dict[str, int],
    approximate: bool,
    sampled_object_count: int,
    target_prefix: Optional[str],
) -> dict[str, Any]:
    normalized_target = (target_prefix or "").strip().strip("/")
    return {
        "object_count": object_count,
        "total_size_bytes": total_size_bytes,
        "type_histogram": dict(sorted(type_histogram.items())),
        "approximate": approximate,
        "sample_coverage": "partial" if approximate else "full",
        "sampled_object_count": sampled_object_count,
        "target_prefix": normalized_target,
        "target_scope": "bucket_root" if normalized_target == "" else "prefix",
    }


def _object_last_modified(item: dict[str, Any]) -> datetime:
    value = item.get("last_modified") or item.get("LastModified")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return _now()
    return _now()


class S3ScanService:
    """Enumerates seller-owned S3 objects through the ai.market S3 broker."""

    def __init__(self, broker: Optional[S3BrokerClient] = None) -> None:
        self.broker = broker or S3BrokerClient()

    def create_scan_job(self, connection_id: str) -> S3ScanJob:
        """Create a running scan job without touching the broker."""
        with get_session_context() as session:
            connection = session.get(S3Connection, connection_id)
            if connection is None:
                raise ValueError("S3 connection not found")

            started_at = _now()
            scan_job = S3ScanJob(
                id=str(uuid.uuid4()),
                connection_id=connection.id,
                status="running",
                started_at=started_at,
                updated_at=started_at,
            )
            session.add(scan_job)
            session.commit()
            session.refresh(scan_job)
            session.expunge(scan_job)
            return scan_job

    def run_scan_job(self, scan_job_id: str) -> None:
        self._run_scan_job(scan_job_id)

    def run_scan_job_for_prefix(self, scan_job_id: str, prefix: str) -> None:
        self._run_scan_job(scan_job_id, scan_prefix=prefix)

    def _run_scan_job(self, scan_job_id: str, scan_prefix: Optional[str] = None) -> None:
        with get_session_context() as session:
            scan_job = session.get(S3ScanJob, scan_job_id)
            if scan_job is None:
                logger.warning("s3_scan_job_not_found", extra={"scan_job_id": scan_job_id})
                return
            connection = session.get(S3Connection, scan_job.connection_id)

            connection_id = scan_job.connection_id
            try:
                if connection is None:
                    raise ValueError("S3 connection not found")
                if not connection.role_arn:
                    raise S3BrokerError("S3 connection role ARN is not configured.")

                enumerated = 0
                total_size_bytes = 0
                type_histogram: dict[str, int] = {}
                sampled_object_count = 0
                approximate = False
                continuation_token: Optional[str] = None
                target_prefix = scan_prefix if scan_prefix is not None else connection.prefix
                while True:
                    rate_limit_retries = 0
                    while True:
                        try:
                            response = self.broker.list_objects(
                                role_arn=connection.role_arn,
                                region=connection.region,
                                bucket=connection.bucket,
                                prefix=target_prefix,
                                continuation_token=continuation_token,
                                max_keys=1000,
                            )
                            break
                        except S3BrokerRateLimited as exc:
                            rate_limit_retries += 1
                            if rate_limit_retries > MAX_RATE_LIMIT_RETRIES:
                                raise S3BrokerError(RATE_LIMIT_EXHAUSTED_MESSAGE) from exc
                            time.sleep(min(exc.retry_after, RATE_LIMIT_RETRY_CAP_SECONDS))

                    if response.get("status") != "listed":
                        raise S3BrokerError(str(response.get("error_message") or "S3 broker object listing failed."))

                    for item in response.get("objects", []):
                        object_key = item["key"]
                        size_bytes = int(item.get("size") or 0)
                        content_type = _content_type_for_key(object_key)
                        total_size_bytes += size_bytes
                        type_histogram[content_type] = type_histogram.get(content_type, 0) + 1
                        enumerated += 1

                        if sampled_object_count < SAMPLE_MAX_OBJECTS:
                            existing = session.exec(
                                select(S3ObjectMetadata)
                                .where(S3ObjectMetadata.connection_id == connection.id)
                                .where(S3ObjectMetadata.object_key == object_key)
                            ).first()

                            if existing is None:
                                existing = S3ObjectMetadata(
                                    id=str(uuid.uuid4()),
                                    connection_id=connection.id,
                                    scan_job_id=scan_job.id,
                                    object_key=object_key,
                                    size_bytes=size_bytes,
                                    content_type=content_type,
                                    last_modified=_object_last_modified(item),
                                    etag=item.get("etag", ""),
                                )
                            else:
                                existing.scan_job_id = scan_job.id
                                existing.size_bytes = size_bytes
                                existing.content_type = content_type
                                existing.last_modified = _object_last_modified(item)
                                existing.etag = item.get("etag", "")
                                existing.updated_at = _now()

                            session.add(existing)
                            sampled_object_count += 1

                    continuation_token = response.get("next_continuation_token")
                    scan_job.objects_enumerated = enumerated
                    scan_job.continuation_token = continuation_token
                    scan_job.sampled_stats = _sampled_stats(
                        object_count=enumerated,
                        total_size_bytes=total_size_bytes,
                        type_histogram=type_histogram,
                        approximate=approximate,
                        sampled_object_count=sampled_object_count,
                        target_prefix=target_prefix,
                    )
                    scan_job.updated_at = _now()
                    session.add(scan_job)
                    session.commit()

                    if not response.get("is_truncated"):
                        break
                    time.sleep(INTER_PAGE_PACING_SECONDS)

                completed_at = _now()
                scan_job.status = "completed"
                scan_job.completed_at = completed_at
                scan_job.continuation_token = None
                scan_job.updated_at = completed_at
                connection.last_scanned_at = completed_at
                connection.continuation_token = None
                connection.updated_at = completed_at
                session.add(scan_job)
                session.add(connection)
                session.commit()
                session.refresh(scan_job)
                session.expunge(scan_job)
                return
            except S3BrokerError as exc:
                failed_at = _now()
                scan_job.status = "failed"
                scan_job.error_message = str(exc)
                scan_job.completed_at = failed_at
                scan_job.updated_at = failed_at
                session.add(scan_job)
                session.commit()
                session.refresh(scan_job)
                session.expunge(scan_job)
                return
            except Exception as exc:  # any other failure must fail-closed, not stick "running"
                logger.warning(
                    "s3_scan_failed",
                    extra={
                        "connection_id": connection_id,
                        "scan_job_id": scan_job.id,
                        "error_type": type(exc).__name__,
                    },
                )
                failed_at = _now()
                scan_job.status = "failed"
                scan_job.error_message = (
                    "Scan failed. Verify the connection's bucket and role permissions, then retry."
                )
                scan_job.completed_at = failed_at
                scan_job.updated_at = failed_at
                session.add(scan_job)
                session.commit()
                session.refresh(scan_job)
                session.expunge(scan_job)
                return

    def run_scan(self, scan_job_id: str) -> None:
        self.run_scan_job(scan_job_id)

    def scan_connection(self, connection_id: str) -> S3ScanJob:
        scan_job = self.create_scan_job(connection_id)
        self.run_scan_job(scan_job.id)
        with get_session_context() as session:
            completed_job = session.get(S3ScanJob, scan_job.id)
            if completed_job is None:
                raise ValueError("S3 scan job not found")
            session.expunge(completed_job)
            return completed_job
