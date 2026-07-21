from contextlib import contextmanager
from datetime import datetime, timezone
import json
from typing import Optional

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from app.auth.api_key_auth import AuthenticatedUser
from app.models.dataset import DatasetRecord
from app.models.s3_connection import S3Connection
from app.services.processing_service import ProcessingService
from app.services.s3_publish_source_resolver import (
    NotS3PublishSource,
    S3PublishSourceResolutionError,
    resolve_local_commitment_source,
    resolve_s3_connection_publish_source,
    resolve_s3_publish_source,
)
from app.services.serial_store import SerialState


USER_A = AuthenticatedUser(user_id="user-a", key_id="key-a", scopes=["read", "write"], valid=True)
SERIAL_STATE = SerialState(serial_id="11111111-2222-3333-4444-555555555555")


def test_local_commitment_source_returns_only_customer_local_path(tmp_path):
    source = tmp_path / "dataset.ndjson"
    source.write_text('{"id":1}\n')
    record = type("Record", (), {"processed_path": source, "upload_path": None})()
    processing = type("Processing", (), {"get_dataset": lambda self, dataset_id: record if dataset_id == "ds-1" else None})()
    resolution = resolve_local_commitment_source("ds-1", processing=processing)
    assert resolution.path == source.resolve()
    assert resolution.file_format == "ndjson"
    assert not hasattr(resolution, "source_credentials")


@pytest.fixture
def resolver_engine(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _session_context():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(ProcessingService, "_get_session", staticmethod(lambda: _session_context()))
    return engine


def _session_context_for(engine):
    @contextmanager
    def _session_context():
        with Session(engine) as session:
            yield session

    return _session_context


def _add_dataset(
    engine,
    *,
    dataset_id: str = "dataset-1",
    source_type: str = "s3",
    connection_id: Optional[str] = "connection-1",
    object_key: Optional[str] = "exports/dataset-1/report.csv",
) -> str:
    metadata = {"source_type": source_type}
    if connection_id is not None:
        metadata["source_connection_id"] = connection_id
    if object_key is not None:
        metadata["source_object_key"] = object_key

    row = DatasetRecord(
        id=dataset_id,
        original_filename="report.csv",
        storage_filename="dataset-1_report.csv",
        file_type="csv",
        status="preview_ready",
        metadata_json=json.dumps(metadata),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()
    return dataset_id


def _add_connection(
    engine,
    *,
    connection_id: str = "connection-1",
    owner_id: Optional[str] = "user-a",
    status: str = "verified",
    prefix: Optional[str] = "exports/dataset-1",
    role_arn: Optional[str] = "arn:aws:iam::123456789012:role/aim-data-delivery",
) -> None:
    row = S3Connection(
        id=connection_id,
        owner_id=owner_id,
        name="Seller bucket",
        bucket="seller-bucket",
        region="us-east-1",
        prefix=prefix,
        role_arn=role_arn,
        external_id="local-external-id",
        status=status,
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()


def _resolve(engine, dataset_id: str = "dataset-1", user=USER_A, serial_state=SERIAL_STATE):
    return resolve_s3_publish_source(
        dataset_id,
        user,
        serial_state,
        processing=ProcessingService(),
        session_context=_session_context_for(engine),
    )


def _resolve_connection(
    engine,
    connection_id: str = "connection-1",
    user=USER_A,
    serial_state=SERIAL_STATE,
    *,
    scope="prefix",
    allow_bucket_root=False,
):
    return resolve_s3_connection_publish_source(
        connection_id,
        user,
        serial_state,
        scope=scope,
        allow_bucket_root=allow_bucket_root,
        session_context=_session_context_for(engine),
    )


def test_owner_match_success_returns_validated_resolution(resolver_engine):
    _add_connection(resolver_engine)
    _add_dataset(resolver_engine)

    result = _resolve(resolver_engine)

    assert result.is_s3 is True
    assert result.bucket == "seller-bucket"
    assert result.region == "us-east-1"
    assert result.role_arn == "arn:aws:iam::123456789012:role/aim-data-delivery"
    assert result.prefix == "exports/dataset-1"
    assert result.serial_id == SERIAL_STATE.serial_id


def test_foreign_owner_fails_closed(resolver_engine):
    _add_connection(resolver_engine, owner_id="user-b")
    _add_dataset(resolver_engine)

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible"):
        _resolve(resolver_engine, user=USER_A)


def test_unverified_connection_fails_closed(resolver_engine):
    _add_connection(resolver_engine, status="configured")
    _add_dataset(resolver_engine)

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible"):
        _resolve(resolver_engine)


def test_object_key_outside_prefix_fails_closed(resolver_engine):
    _add_connection(resolver_engine, prefix="exports/dataset-1")
    _add_dataset(resolver_engine, object_key="exports/other/report.csv")

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible"):
        _resolve(resolver_engine)


@pytest.mark.parametrize(
    "object_key",
    [
        "exports/dataset-1/../other/secret.csv",
        "/exports/dataset-1/report.csv",
    ],
)
def test_invalid_object_key_fails_closed(resolver_engine, object_key):
    _add_connection(resolver_engine, prefix="exports/dataset-1")
    _add_dataset(resolver_engine, object_key=object_key)

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible") as exc_info:
        _resolve(resolver_engine)

    assert exc_info.value.reason == "invalid_object_key"


@pytest.mark.parametrize("prefix", [None, "", "/"])
def test_empty_or_bucket_root_prefix_fails_closed(resolver_engine, prefix):
    _add_connection(resolver_engine, prefix=prefix)
    _add_dataset(resolver_engine)

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible"):
        _resolve(resolver_engine)


def test_connection_publish_prefix_requires_non_root_prefix(resolver_engine):
    _add_connection(resolver_engine, prefix="/")

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible") as exc_info:
        _resolve_connection(resolver_engine, scope="prefix")

    assert exc_info.value.reason == "bucket_root_prefix"


def test_connection_publish_bucket_root_requires_explicit_allow(resolver_engine):
    _add_connection(resolver_engine, prefix=None)

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible") as exc_info:
        _resolve_connection(resolver_engine, scope="bucket_root", allow_bucket_root=False)

    assert exc_info.value.reason == "bucket_root_not_allowed"


def test_connection_publish_bucket_root_explicit_allow_returns_empty_prefix(resolver_engine):
    _add_connection(resolver_engine, prefix=None)

    result = _resolve_connection(resolver_engine, scope="bucket_root", allow_bucket_root=True)

    assert result.is_s3 is True
    assert result.bucket == "seller-bucket"
    assert result.prefix == ""
    assert result.role_arn == "arn:aws:iam::123456789012:role/aim-data-delivery"


@pytest.mark.parametrize(
    "connection_id,object_key",
    [
        (None, "exports/dataset-1/report.csv"),
        ("connection-1", None),
    ],
)
def test_missing_s3_provenance_fails_closed(resolver_engine, connection_id, object_key):
    _add_connection(resolver_engine)
    _add_dataset(resolver_engine, connection_id=connection_id, object_key=object_key)

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible"):
        _resolve(resolver_engine)


def test_unknown_serial_id_fails_closed(resolver_engine):
    _add_connection(resolver_engine)
    _add_dataset(resolver_engine)

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible"):
        _resolve(resolver_engine, serial_state=SerialState())


@pytest.mark.parametrize(
    "role_arn,prefix,object_key",
    [
        ("arn:aws:iam::12345678901:role/bad", "exports/dataset-1", "exports/dataset-1/report.csv"),
        ("arn:aws:iam::123456789012:role/aim-data-delivery", "/exports/dataset-1", "exports/dataset-1/report.csv"),
        ("arn:aws:iam::123456789012:role/aim-data-delivery", "exports/../dataset-1", "exports/../dataset-1/report.csv"),
        ("arn:aws:iam::123456789012:role/aim-data-delivery", "exports/*", "exports/*/report.csv"),
        ("arn:aws:iam::123456789012:role/aim-data-delivery", "exports/dataset 1", "exports/dataset 1/report.csv"),
    ],
)
def test_bad_role_arn_or_prefix_charset_fails_closed(resolver_engine, role_arn, prefix, object_key):
    _add_connection(resolver_engine, role_arn=role_arn, prefix=prefix)
    _add_dataset(resolver_engine, object_key=object_key)

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible"):
        _resolve(resolver_engine)


def test_non_s3_dataset_returns_not_s3_without_error(resolver_engine):
    _add_dataset(resolver_engine, source_type="upload", connection_id=None, object_key=None)

    result = _resolve(resolver_engine)

    assert isinstance(result, NotS3PublishSource)
    assert result.is_s3 is False


def test_missing_dataset_lookup_fails_closed(resolver_engine):
    with pytest.raises(S3PublishSourceResolutionError, match="not eligible"):
        _resolve(resolver_engine, dataset_id="missing-dataset")


def test_dataset_lookup_failed_does_not_chain_original_error():
    class FailingProcessing:
        def get_dataset(self, _dataset_id):
            raise RuntimeError("database unavailable")

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible") as exc_info:
        resolve_s3_publish_source(
            "dataset-1",
            USER_A,
            SERIAL_STATE,
            processing=FailingProcessing(),
            session_context=lambda: None,
        )

    assert exc_info.value.reason == "dataset_lookup_failed"
    assert exc_info.value.__cause__ is None


def test_connection_lookup_failed_does_not_chain_original_error(resolver_engine):
    _add_dataset(resolver_engine)

    @contextmanager
    def _failing_session_context():
        raise RuntimeError("database unavailable")
        yield

    with pytest.raises(S3PublishSourceResolutionError, match="not eligible") as exc_info:
        resolve_s3_publish_source(
            "dataset-1",
            USER_A,
            SERIAL_STATE,
            processing=ProcessingService(),
            session_context=_failing_session_context,
        )

    assert exc_info.value.reason == "connection_lookup_failed"
    assert exc_info.value.__cause__ is None
