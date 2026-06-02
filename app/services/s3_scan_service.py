"""
S3 scan service for broker-backed, no-copy listing registration.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import select

from app.core.database import get_session_context
from app.models.s3_connection import S3Connection
from app.models.s3_object_metadata import S3ObjectMetadata
from app.models.s3_scan_job import S3ScanJob
from app.services.s3_broker_client import S3BrokerClient, S3BrokerError

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _content_type_for_key(object_key: str) -> str:
    return mimetypes.guess_type(object_key)[0] or "application/octet-stream"


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

    def scan_connection(self, connection_id: str) -> S3ScanJob:
        with get_session_context() as session:
            connection = session.get(S3Connection, connection_id)
            if connection is None:
                raise ValueError("S3 connection not found")

            scan_job = S3ScanJob(
                id=str(uuid.uuid4()),
                connection_id=connection.id,
                status="running",
                started_at=_now(),
                updated_at=_now(),
            )
            session.add(scan_job)
            session.commit()
            session.refresh(scan_job)

            try:
                if not connection.role_arn:
                    raise S3BrokerError("S3 connection role ARN is not configured.")

                enumerated = 0
                continuation_token: Optional[str] = None
                while True:
                    response = self.broker.list_objects(
                        role_arn=connection.role_arn,
                        region=connection.region,
                        bucket=connection.bucket,
                        prefix=connection.prefix,
                        continuation_token=continuation_token,
                    )
                    if response.get("status") != "listed":
                        raise S3BrokerError(str(response.get("error_message") or "S3 broker object listing failed."))

                    for item in response.get("objects", []):
                        object_key = item["key"]
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
                                size_bytes=int(item.get("size") or 0),
                                content_type=_content_type_for_key(object_key),
                                last_modified=_object_last_modified(item),
                                etag=item.get("etag", ""),
                            )
                        else:
                            existing.scan_job_id = scan_job.id
                            existing.size_bytes = int(item.get("size") or 0)
                            existing.content_type = _content_type_for_key(object_key)
                            existing.last_modified = _object_last_modified(item)
                            existing.etag = item.get("etag", "")
                            existing.updated_at = _now()

                        session.add(existing)
                        enumerated += 1

                    continuation_token = response.get("next_continuation_token")
                    scan_job.objects_enumerated = enumerated
                    scan_job.continuation_token = continuation_token
                    scan_job.updated_at = _now()
                    session.add(scan_job)
                    session.commit()

                    if not response.get("is_truncated"):
                        break

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
                return scan_job
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
                return scan_job
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
                return scan_job
