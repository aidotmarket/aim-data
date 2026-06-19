"""
S3 publish source resolver for scoped-credential eligibility.

Satisfies S711 C2c2 step 3 only: detect S3 provenance and validate the
owner-scoped connection/prefix/serial inputs before any publish payload work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional, Union

from sqlmodel import select

from app.auth.api_key_auth import AuthenticatedUser
from app.core.database import get_session_context
from app.models.s3_connection import S3Connection
from app.services.processing_service import ProcessingService, get_processing_service
from app.services.serial_store import SerialState


ROLE_ARN_RE = re.compile(r"^arn:aws:iam::\d{12}:role/.+$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9!_.'()/-]+$")
WILDCARD_CHARS = {"*", "?", "[", "]"}


class S3PublishSourceResolutionError(RuntimeError):
    """Retryable fail-closed error for incomplete S3 publish provenance."""

    retryable = True

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(
            "S3-sourced dataset is not eligible for scoped-credential publish yet; "
            "verify the S3 connection and dataset source, then retry."
        )


@dataclass(frozen=True)
class NotS3PublishSource:
    """Dataset is not S3-sourced (§C.3)."""

    is_s3: Literal[False] = False


@dataclass(frozen=True)
class S3PublishSourceResolution:
    """Validated S3-source resolution for later payload construction (§C.3/§C.4a)."""

    bucket: str
    region: str
    role_arn: str
    prefix: str
    serial_id: str
    is_s3: Literal[True] = True


PublishSourceResolution = Union[NotS3PublishSource, S3PublishSourceResolution]


def resolve_s3_publish_source(
    dataset_id: str,
    user: AuthenticatedUser,
    serial_state: SerialState,
    *,
    processing: Optional[ProcessingService] = None,
    session_context=None,
) -> PublishSourceResolution:
    """
    Detect and validate S3 publish provenance for ``dataset_id``.

    Spec coverage:
    - §C.3: one detector path; S3 iff dataset metadata ``source_type == "s3"``.
    - CM7: connection resolution is owner-scoped to ``S3Connection.owner_id``.
    - §C.4a: non-root dedicated prefix and object-key prefix bounding.
    - CM4: local role ARN and prefix input conformance.
    - CM3/CM2: fail closed with a non-secret, retryable error for incomplete
      provenance; no role ARN, serial string, or serial mapping is logged.
    """

    processing = processing or get_processing_service()
    session_context = session_context or get_session_context

    try:
        record = processing.get_dataset(dataset_id)
    except Exception:
        raise S3PublishSourceResolutionError("dataset_lookup_failed")
    if record is None:
        raise S3PublishSourceResolutionError("dataset_lookup_failed")

    metadata = getattr(record, "metadata", None)
    if not isinstance(metadata, dict) or metadata.get("source_type") != "s3":
        return NotS3PublishSource()

    connection_id = metadata.get("source_connection_id")
    object_key = metadata.get("source_object_key")
    if not connection_id:
        raise S3PublishSourceResolutionError("missing_source_connection_id")
    if not object_key:
        raise S3PublishSourceResolutionError("missing_source_object_key")

    serial_id = getattr(serial_state, "serial_id", None)
    if not serial_id:
        raise S3PublishSourceResolutionError("missing_serial_id")

    try:
        with session_context() as session:
            connection = session.exec(
                select(S3Connection)
                .where(S3Connection.id == str(connection_id))
                .where(S3Connection.owner_id == user.user_id)
            ).first()
            if connection is None:
                raise S3PublishSourceResolutionError("connection_unavailable")
            return _validate_connection_resolution(connection, str(object_key), str(serial_id))
    except S3PublishSourceResolutionError:
        raise
    except Exception:
        raise S3PublishSourceResolutionError("connection_lookup_failed")


def _validate_connection_resolution(
    connection: S3Connection,
    object_key: str,
    serial_id: str,
) -> S3PublishSourceResolution:
    if connection.status != "verified":
        raise S3PublishSourceResolutionError("connection_unverified")

    if not connection.role_arn or not ROLE_ARN_RE.match(connection.role_arn):
        raise S3PublishSourceResolutionError("invalid_role_arn")

    prefix = _validate_prefix(connection.prefix)
    if ".." in object_key or object_key.startswith("/"):
        raise S3PublishSourceResolutionError("invalid_object_key")
    if not object_key.startswith(f"{prefix}/"):
        raise S3PublishSourceResolutionError("object_key_outside_prefix")

    return S3PublishSourceResolution(
        bucket=connection.bucket,
        region=connection.region,
        role_arn=connection.role_arn,
        prefix=prefix,
        serial_id=serial_id,
    )


def _validate_prefix(raw_prefix: Optional[str]) -> str:
    if raw_prefix is None:
        raise S3PublishSourceResolutionError("missing_prefix")

    prefix = raw_prefix.strip().rstrip("/")
    if not prefix or prefix == "/":
        raise S3PublishSourceResolutionError("bucket_root_prefix")
    if prefix.startswith("/"):
        raise S3PublishSourceResolutionError("invalid_prefix")
    if ".." in prefix:
        raise S3PublishSourceResolutionError("invalid_prefix")
    if any(char in prefix for char in WILDCARD_CHARS):
        raise S3PublishSourceResolutionError("invalid_prefix")
    if not PREFIX_RE.fullmatch(prefix):
        raise S3PublishSourceResolutionError("invalid_prefix")
    return prefix
