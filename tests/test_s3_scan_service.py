from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, select

from app.main import app
from app.models.dataset import DatasetRecord
from app.models.s3_connection import S3Connection
from app.models.s3_object_metadata import S3ObjectMetadata
from app.models.s3_scan_job import S3ScanJob
from app.routers import s3_connections
from app.services.s3_broker_client import S3BrokerError
from app.services import fulfillment_service, s3_scan_service
from app.services.fulfillment_service import FulfillmentService
from app.services.processing_service import ProcessingService
from app.services.s3_scan_service import S3ScanService


@pytest.fixture
def s3_engine():
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
    return engine


@pytest.fixture
def session_context(s3_engine, monkeypatch, tmp_path):
    @contextmanager
    def _session_context():
        with Session(s3_engine) as session:
            yield session

    monkeypatch.setattr(s3_scan_service, "get_session_context", _session_context)
    monkeypatch.setattr(s3_connections, "get_session_context", _session_context)
    monkeypatch.setattr(fulfillment_service, "get_session_context", _session_context)
    monkeypatch.setattr(ProcessingService, "_get_session", staticmethod(lambda: _session_context()))
    monkeypatch.setattr(s3_connections.settings, "upload_directory", str(tmp_path / "uploads"))
    monkeypatch.setattr(s3_connections.settings, "processed_directory", str(tmp_path / "processed"))
    processing = ProcessingService()
    monkeypatch.setattr(s3_connections, "get_processing_service", lambda: processing)
    return _session_context


@pytest.fixture
def client(session_context):
    return TestClient(app)


class FakeBroker:
    def __init__(self, error: Optional[Exception] = None):
        self.error = error
        self.calls = []
        self.pages = []

    def list_objects(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if kwargs.get("continuation_token"):
            return self.pages[1]
        return self.pages[0]

    def presign_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"status": "ok", "url": "https://presigned.example/report.csv"}


def _connection(**overrides) -> S3Connection:
    values = {
        "id": str(uuid4()),
        "owner_id": "mock_user_auth_disabled",
        "name": "Seller bucket",
        "bucket": "seller-bucket",
        "region": "us-east-1",
        "prefix": "exports/",
        "role_arn": "arn:aws:iam::210987654321:role/aim-data",
        "external_id": str(uuid4()),
        "status": "verified",
    }
    values.update(overrides)
    return S3Connection(**values)


def _add_connection(session_context, **overrides) -> S3Connection:
    connection = _connection(**overrides)
    with session_context() as session:
        session.add(connection)
        session.commit()
        session.refresh(connection)
        session.expunge(connection)
    return connection


def _page(*objects, truncated=False, token=None):
    response = {
        "status": "listed",
        "is_truncated": truncated,
        "objects": list(objects),
    }
    if token:
        response["next_continuation_token"] = token
    return response


def _object(key: str, size: int = 123):
    return {
        "key": key,
        "size": size,
        "etag": '"etag"',
        "last_modified": datetime(2026, 5, 29, tzinfo=timezone.utc).isoformat(),
    }


def test_start_scan_creates_running_job_without_broker_calls(session_context):
    connection = _add_connection(session_context)
    broker = FakeBroker()

    scan_job = S3ScanService(broker).start_scan(connection.id)

    assert scan_job.status == "running"
    assert scan_job.connection_id == connection.id
    assert scan_job.objects_enumerated == 0
    assert broker.calls == []
    with session_context() as session:
        stored = session.get(S3ScanJob, scan_job.id)
    assert stored.status == "running"


def test_run_scan_completes_existing_job(session_context):
    connection = _add_connection(session_context)
    scan_job_id = str(uuid4())
    scan_job = S3ScanJob(id=scan_job_id, connection_id=connection.id, status="running")
    with session_context() as session:
        session.add(scan_job)
        session.commit()

    broker = FakeBroker()
    broker.pages = [_page(_object("exports/a.csv"))]

    S3ScanService(broker).run_scan(scan_job_id)

    with session_context() as session:
        stored = session.get(S3ScanJob, scan_job_id)
        stored_connection = session.get(S3Connection, connection.id)

    assert stored.status == "completed"
    assert stored.objects_enumerated == 1
    assert stored.completed_at is not None
    assert stored.error_message is None
    assert stored_connection.last_scanned_at is not None


def test_run_scan_broker_error_marks_existing_job_failed(session_context):
    connection = _add_connection(session_context)
    scan_job_id = str(uuid4())
    scan_job = S3ScanJob(id=scan_job_id, connection_id=connection.id, status="running")
    with session_context() as session:
        session.add(scan_job)
        session.commit()

    broker = FakeBroker(S3BrokerError("Confirm the trust policy and ExternalId."))

    S3ScanService(broker).run_scan(scan_job_id)

    with session_context() as session:
        stored = session.get(S3ScanJob, scan_job_id)

    assert stored.status == "failed"
    assert "Confirm the trust policy" in stored.error_message
    assert stored.completed_at is not None


def test_scan_persists_sample_and_exact_stats(session_context, monkeypatch):
    connection = _add_connection(session_context)
    broker = FakeBroker()
    broker.pages = [
        _page(_object("exports/a.csv"), truncated=True, token="next"),
        _page(_object("exports/b.json", 456)),
    ]
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: pytest.fail("boto3 must not be used"))

    scan_job = S3ScanService(broker).scan_connection(connection.id)

    assert scan_job.status == "completed"
    assert scan_job.objects_enumerated == 2
    assert scan_job.sampled_stats == {
        "object_count": 2,
        "total_size_bytes": 579,
        "type_histogram": {"application/json": 1, "text/csv": 1},
        "approximate": False,
        "sample_coverage": "full",
        "sampled_object_count": 2,
    }
    assert broker.calls[0]["bucket"] == "seller-bucket"
    assert broker.calls[0]["prefix"] == "exports/"
    assert broker.calls[1]["continuation_token"] == "next"
    with session_context() as session:
        objects = session.exec(select(S3ObjectMetadata).order_by(S3ObjectMetadata.object_key)).all()
        stored_connection = session.get(S3Connection, connection.id)
    assert [obj.object_key for obj in objects] == ["exports/a.csv", "exports/b.json"]
    assert objects[0].content_type == "text/csv"
    assert objects[1].size_bytes == 456
    assert stored_connection.last_scanned_at is not None


def test_large_scan_computes_exact_stats_and_caps_sample_rows(session_context, monkeypatch):
    connection = _add_connection(session_context)
    broker = FakeBroker()
    objects = [
        _object(
            f"exports/item-{idx:05d}.{'csv' if idx % 2 == 0 else 'json'}",
            idx + 1,
        )
        for idx in range(11_001)
    ]
    broker.pages = [
        _page(*objects[start : start + 1000], truncated=start + 1000 < len(objects), token=str(start + 1000))
        for start in range(0, len(objects), 1000)
    ]

    def list_objects(**kwargs):
        broker.calls.append(kwargs)
        token = kwargs.get("continuation_token")
        page_index = int(token or 0) // 1000
        return broker.pages[page_index]

    broker.list_objects = list_objects
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: pytest.fail("boto3 must not be used"))

    scan_job = S3ScanService(broker).scan_connection(connection.id)

    assert scan_job.status == "completed"
    assert scan_job.objects_enumerated == 11_001
    assert scan_job.sampled_stats == {
        "object_count": 11_001,
        "total_size_bytes": sum(range(1, 11_002)),
        "type_histogram": {"application/json": 5500, "text/csv": 5501},
        "approximate": False,
        "sample_coverage": "full",
        "sampled_object_count": 1000,
    }
    with session_context() as session:
        stored_objects = session.exec(select(S3ObjectMetadata)).all()
    assert len(stored_objects) == 1000
    assert len(broker.calls) == 12



def test_rescan_is_idempotent_and_preserves_dataset_id(session_context, monkeypatch):
    connection = _add_connection(session_context)
    dataset = DatasetRecord(
        id=str(uuid4()),
        original_filename="a.csv",
        storage_filename="exports/a.csv",
        file_type="csv",
        file_size_bytes=123,
        status="preview_ready",
    )
    dataset_id = dataset.id
    scan_job = S3ScanJob(id=str(uuid4()), connection_id=connection.id, status="completed")
    original_object_id = str(uuid4())
    with session_context() as session:
        session.add(dataset)
        session.add(scan_job)
        session.add(
            S3ObjectMetadata(
                id=original_object_id,
                connection_id=connection.id,
                scan_job_id=scan_job.id,
                object_key="exports/a.csv",
                size_bytes=1,
                content_type="text/csv",
                last_modified=datetime.now(timezone.utc),
                etag="old",
                dataset_id=dataset.id,
            )
        )
        session.commit()

    broker = FakeBroker()
    broker.pages = [_page(_object("exports/a.csv", 999))]

    S3ScanService(broker).scan_connection(connection.id)

    with session_context() as session:
        objects = session.exec(select(S3ObjectMetadata)).all()
    assert len(objects) == 1
    assert objects[0].id == original_object_id
    assert objects[0].dataset_id == dataset_id
    assert objects[0].size_bytes == 999


def test_scan_broker_error_marks_failed_without_raw_aws_internals(session_context):
    connection = _add_connection(session_context)
    raw = "An error occurred (AccessDenied) when calling the AssumeRole operation"
    broker = FakeBroker(S3BrokerError("Confirm the trust policy and ExternalId."))

    scan_job = S3ScanService(broker).scan_connection(connection.id)

    assert scan_job.status == "failed"
    assert "Confirm the trust policy" in scan_job.error_message
    assert raw not in scan_job.error_message
    with session_context() as session:
        stored = session.get(S3ScanJob, scan_job.id)
    assert stored.status == "failed"


def test_register_endpoint_links_object_and_creates_dataset(client, session_context, monkeypatch):
    connection = _add_connection(session_context)
    scan_job = S3ScanJob(id=str(uuid4()), connection_id=connection.id, status="completed")
    metadata = S3ObjectMetadata(
        id=str(uuid4()),
        connection_id=connection.id,
        scan_job_id=scan_job.id,
        object_key="exports/report.csv",
        size_bytes=789,
        content_type="text/csv",
        last_modified=datetime.now(timezone.utc),
        etag='"etag"',
    )
    metadata_id = metadata.id
    with session_context() as session:
        session.add(scan_job)
        session.add(metadata)
        session.commit()

    broker = FakeBroker()
    object_bytes = b"name,value\nAda,1\n"
    download_calls = []
    enqueued = []

    class FakeStreamResponse:
        def __init__(self, url: str):
            self.response = httpx.Response(200, request=httpx.Request("GET", url))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            self.response.raise_for_status()

        async def aiter_bytes(self, chunk_size: int):
            for start in range(0, len(object_bytes), chunk_size):
                yield object_bytes[start : start + chunk_size]

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str):
            download_calls.append((method, url))
            return FakeStreamResponse(url)

    async def fake_process_dataset_task(dataset_id: str):
        enqueued.append(dataset_id)

    monkeypatch.setattr(s3_connections, "S3BrokerClient", lambda: broker)
    monkeypatch.setattr(s3_connections.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(s3_connections, "process_dataset_task", fake_process_dataset_task)

    response = client.post(
        f"/api/s3-connections/{connection.id}/objects/{metadata_id}/register",
        json={"listing_id": "lst_123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dataset"]["original_filename"] == "report.csv"
    assert body["dataset"]["file_type"] == "csv"
    assert body["dataset"]["file_size_bytes"] == len(object_bytes)
    assert body["dataset"]["status"] == "uploaded"
    assert body["dataset"]["storage_filename"].endswith("_report.csv")
    assert body["dataset"]["listing_id"] == "lst_123"
    assert body["object"]["dataset_id"] == body["dataset"]["id"]
    assert broker.calls == [
        {
            "role_arn": connection.role_arn,
            "region": connection.region,
            "bucket": connection.bucket,
            "object_key": "exports/report.csv",
        }
    ]
    assert download_calls == [("GET", "https://presigned.example/report.csv")]
    assert enqueued == [body["dataset"]["id"]]

    with session_context() as session:
        dataset = session.get(DatasetRecord, body["dataset"]["id"])
    metadata_json = json.loads(dataset.metadata_json)
    assert metadata_json["source_type"] == "s3"
    assert metadata_json["source_object_key"] == "exports/report.csv"
    assert Path(s3_connections.settings.upload_directory, dataset.storage_filename).read_bytes() == object_bytes


def test_objects_endpoint_paginates_and_filters(client, session_context):
    connection = _add_connection(session_context)
    dataset = DatasetRecord(
        id=str(uuid4()),
        original_filename="linked.csv",
        storage_filename="exports/linked.csv",
        file_type="csv",
        file_size_bytes=1,
        status="preview_ready",
    )
    scan_job = S3ScanJob(id=str(uuid4()), connection_id=connection.id, status="completed")
    with session_context() as session:
        session.add(dataset)
        session.add(scan_job)
        session.add(
            S3ObjectMetadata(
                id=str(uuid4()),
                connection_id=connection.id,
                scan_job_id=scan_job.id,
                object_key="exports/linked.csv",
                size_bytes=1,
                content_type="text/csv",
                last_modified=datetime.now(timezone.utc),
                etag="etag",
                dataset_id=dataset.id,
            )
        )
        session.add(
            S3ObjectMetadata(
                id=str(uuid4()),
                connection_id=connection.id,
                scan_job_id=scan_job.id,
                object_key="exports/unlinked.csv",
                size_bytes=1,
                content_type="text/csv",
                last_modified=datetime.now(timezone.utc),
                etag="etag",
            )
        )
        session.commit()

    response = client.get(f"/api/s3-connections/{connection.id}/objects", params={"dataset_linked": False})

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["object_key"] == "exports/unlinked.csv"


def test_fulfillment_resolves_registered_s3_dataset(session_context):
    connection = _add_connection(session_context)
    dataset = DatasetRecord(
        id=str(uuid4()),
        original_filename="listing.csv",
        storage_filename="exports/listing.csv",
        file_type="csv",
        file_size_bytes=321,
        status="preview_ready",
        listing_id="listing-123",
    )
    dataset_id = dataset.id
    scan_job = S3ScanJob(id=str(uuid4()), connection_id=connection.id, status="completed")
    metadata = S3ObjectMetadata(
        id=str(uuid4()),
        connection_id=connection.id,
        scan_job_id=scan_job.id,
        object_key="exports/listing.csv",
        size_bytes=321,
        content_type="text/csv",
        last_modified=datetime.now(timezone.utc),
        etag="etag",
        dataset_id=dataset.id,
    )
    metadata_id = metadata.id
    with session_context() as session:
        session.add(dataset)
        session.add(scan_job)
        session.add(metadata)
        session.commit()

    service = FulfillmentService(SimpleNamespace())
    found_dataset, file_path = service._find_dataset("listing-123")
    s3_object = service._find_s3_object(found_dataset)

    assert file_path is None
    assert found_dataset.id == dataset_id
    assert s3_object is not None
    found_connection, found_metadata = s3_object
    assert found_connection.id == connection.id
    assert found_metadata.id == metadata_id


def test_register_rejects_unowned_existing_dataset(client, session_context):
    """A connection cannot attach its object to a dataset it does not own (S729 sec review)."""
    connection = _add_connection(session_context)
    foreign = DatasetRecord(
        id=str(uuid4()),
        original_filename="foreign.csv",
        storage_filename="foreign.csv",
        file_type="csv",
        file_size_bytes=1,
        status="preview_ready",
        listing_id="foreign-listing",
    )
    scan_job = S3ScanJob(id=str(uuid4()), connection_id=connection.id, status="completed")
    metadata = S3ObjectMetadata(
        id=str(uuid4()),
        connection_id=connection.id,
        scan_job_id=scan_job.id,
        object_key="exports/mine.csv",
        size_bytes=1,
        content_type="text/csv",
        last_modified=datetime.now(timezone.utc),
        etag="etag",
    )
    metadata_id = metadata.id
    with session_context() as session:
        session.add(foreign)
        session.add(scan_job)
        session.add(metadata)
        session.commit()

    resp = client.post(
        f"/api/s3-connections/{connection.id}/objects/{metadata_id}/register",
        json={"listing_id": "foreign-listing"},
    )
    assert resp.status_code == 403
