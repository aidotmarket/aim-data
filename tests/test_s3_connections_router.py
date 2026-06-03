from contextlib import contextmanager
from datetime import datetime, timezone
import json
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from app.auth.api_key_auth import AuthenticatedUser, get_current_user
from app.main import app
from app.models.dataset import DatasetRecord  # noqa: F401
from app.models.s3_connection import S3Connection
from app.models.s3_object_metadata import S3ObjectMetadata  # noqa: F401
from app.models.s3_scan_job import S3ScanJob  # noqa: F401
from app.routers import s3_connections
from app.services.processing_service import ProcessingService
from app.services.s3_broker_client import S3BrokerError
from app.services import s3_scan_service

USER_A = AuthenticatedUser(user_id="user-a", key_id="key-a", scopes=["read", "write"], valid=True)
USER_B = AuthenticatedUser(user_id="user-b", key_id="key-b", scopes=["read", "write"], valid=True)


class FakeS3BrokerClient:
    def __init__(self):
        self.external_id = "broker-derived-external-id"
        self.verify_result = {"status": "verified", "verified_at": datetime.now(timezone.utc).isoformat()}
        self.verify_error: Optional[Exception] = None
        self.external_id_error: Optional[Exception] = None
        self.verify_calls = []
        self.presign_calls = []
        self.external_id_calls = 0
        self.presign_result = {"status": "ok", "url": "https://presigned.example/object.csv"}

    def get_external_id(self):
        self.external_id_calls += 1
        if self.external_id_error:
            raise self.external_id_error
        return self.external_id

    def verify(self, **kwargs):
        self.verify_calls.append(kwargs)
        if self.verify_error:
            raise self.verify_error
        return self.verify_result

    def presign_object(self, **kwargs):
        self.presign_calls.append(kwargs)
        return self.presign_result


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
def client(s3_engine, monkeypatch, tmp_path):
    @contextmanager
    def _session_context():
        with Session(s3_engine) as session:
            yield session

    monkeypatch.setattr(s3_connections, "get_session_context", _session_context)
    monkeypatch.setattr(s3_scan_service, "get_session_context", _session_context)
    monkeypatch.setattr(ProcessingService, "_get_session", staticmethod(lambda: _session_context()))
    monkeypatch.setattr(s3_connections.settings, "upload_directory", str(tmp_path / "uploads"))
    monkeypatch.setattr(s3_connections.settings, "processed_directory", str(tmp_path / "processed"))
    processing = ProcessingService()
    monkeypatch.setattr(s3_connections, "get_processing_service", lambda: processing)
    monkeypatch.setattr(s3_connections.settings, "ai_market_aws_account_id", "123456789012")
    monkeypatch.setattr(s3_connections.settings, "ai_market_assume_role_principal_arn", None)
    broker = FakeS3BrokerClient()
    monkeypatch.setattr(s3_connections, "S3BrokerClient", lambda: broker)
    app.dependency_overrides[get_current_user] = lambda: USER_A
    test_client = TestClient(app)
    test_client.s3_broker = broker
    yield test_client
    app.dependency_overrides.pop(get_current_user, None)


def _create_connection(client: TestClient) -> dict:
    response = client.post(
        "/api/s3-connections/",
        json={
            "name": "Seller bucket",
            "bucket": "seller-bucket",
            "region": "us-east-1",
            "prefix": "exports/",
        },
    )
    assert response.status_code == 201
    return response.json()


def _configured_row(
    s3_engine,
    *,
    prefix: Optional[str] = "exports/",
    owner_id: Optional[str] = "user-a",
    status: str = "configured",
) -> S3Connection:
    connection = S3Connection(
        id=str(uuid4()),
        owner_id=owner_id,
        name="Seller bucket",
        bucket="seller-bucket",
        region="us-east-1",
        prefix=prefix,
        role_arn="arn:aws:iam::210987654321:role/aim-data",
        external_id="broker-derived-external-id",
        status=status,
    )
    with Session(s3_engine) as session:
        session.add(connection)
        session.commit()
        session.refresh(connection)
        session.expunge(connection)
    return connection


def test_post_creates_row_and_returns_substituted_policies(client):
    data = _create_connection(client)

    assert data["external_id"] == "broker-derived-external-id"
    assert data["status"] == "onboarding"
    assert client.s3_broker.external_id_calls == 1
    assert data["trust_policy"]["Statement"][0]["Principal"]["AWS"] == "arn:aws:iam::123456789012:root"
    assert data["trust_policy"]["Statement"][0]["Condition"]["StringEquals"]["sts:ExternalId"] == data["external_id"]
    assert data["permission_policy"]["Statement"][0]["Resource"] == "arn:aws:s3:::seller-bucket"
    assert data["permission_policy"]["Statement"][0]["Condition"]["StringLike"]["s3:prefix"] == ["exports/*"]
    assert data["permission_policy"]["Statement"][1]["Resource"] == "arn:aws:s3:::seller-bucket/exports/*"


def test_trust_policy_uses_configured_principal_and_external_id(monkeypatch):
    configured_principal = "arn:aws:iam::123456789012:role/aim-data-assumer"
    s3_connection = S3Connection(
        id=str(uuid4()),
        name="Seller bucket",
        bucket="seller-bucket",
        region="us-east-1",
        prefix=None,
        external_id="external-123",
    )
    monkeypatch.setattr(s3_connections.settings, "ai_market_assume_role_principal_arn", configured_principal)

    policy = s3_connections._trust_policy(s3_connection)
    statement = policy["Statement"][0]

    assert statement["Principal"]["AWS"] == configured_principal
    assert statement["Condition"]["StringEquals"]["sts:ExternalId"] == "external-123"


def test_trust_policy_falls_back_to_account_root_and_external_id(monkeypatch):
    s3_connection = S3Connection(
        id=str(uuid4()),
        name="Seller bucket",
        bucket="seller-bucket",
        region="us-east-1",
        prefix=None,
        external_id="external-456",
    )
    monkeypatch.setattr(s3_connections.settings, "ai_market_assume_role_principal_arn", None)
    monkeypatch.setattr(s3_connections.settings, "ai_market_aws_account_id", "123456789012")

    policy = s3_connections._trust_policy(s3_connection)
    statement = policy["Statement"][0]

    assert statement["Principal"]["AWS"] == "arn:aws:iam::123456789012:root"
    assert statement["Condition"]["StringEquals"]["sts:ExternalId"] == "external-456"


def test_permission_policy_prefix_scopes_to_folder_children():
    s3_connection = S3Connection(
        id=str(uuid4()),
        name="Seller bucket",
        bucket="seller-bucket",
        region="us-east-1",
        prefix="reports",
        external_id="external-789",
    )

    policy = s3_connections._permission_policy(s3_connection)
    list_statement = policy["Statement"][0]
    get_statement = policy["Statement"][1]

    assert list_statement["Condition"]["StringLike"]["s3:prefix"] == ["reports/*"]
    assert get_statement["Resource"] == "arn:aws:s3:::seller-bucket/reports/*"
    assert not fnmatchcase("reports-archive/file.csv", list_statement["Condition"]["StringLike"]["s3:prefix"][0])
    assert not fnmatchcase("arn:aws:s3:::seller-bucket/reports-archive/file.csv", get_statement["Resource"])


def test_post_stamps_owner_id(client, s3_engine):
    data = _create_connection(client)

    with Session(s3_engine) as session:
        stored = session.get(S3Connection, data["id"])
        assert stored.owner_id == "user-a"
        assert stored.external_id == "broker-derived-external-id"


def test_post_fails_closed_when_broker_external_id_unavailable(client):
    client.s3_broker.external_id_error = S3BrokerError("AIM Data is not connected/activated.")

    response = client.post(
        "/api/s3-connections/",
        json={
            "name": "Seller bucket",
            "bucket": "seller-bucket",
            "region": "us-east-1",
        },
    )

    assert response.status_code == 409
    assert "not connected/activated" in response.json()["detail"]


def test_get_lists_only_caller_owned_rows(client, s3_engine):
    created = _create_connection(client)
    foreign = _configured_row(s3_engine, owner_id="user-b")

    response = client.get("/api/s3-connections/")

    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [created["id"]]
    assert foreign.id not in [row["id"] for row in response.json()]
    assert "trust_policy" not in response.json()[0] or response.json()[0]["trust_policy"] is None


def test_unauthenticated_request_returns_401(client, monkeypatch):
    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setenv("VECTORAIZ_AUTH_ENABLED", "true")

    response = client.get("/api/s3-connections/")

    assert response.status_code == 401


def test_get_missing_returns_404(client):
    response = client.get(f"/api/s3-connections/{uuid4()}")

    assert response.status_code == 404


def test_put_role_arn_rejects_malformed_and_accepts_valid(client):
    created = _create_connection(client)

    bad = client.put(f"/api/s3-connections/{created['id']}/role-arn", json={"role_arn": "bad"})
    assert bad.status_code == 400

    good = client.put(
        f"/api/s3-connections/{created['id']}/role-arn",
        json={"role_arn": "arn:aws:iam::210987654321:role/aim-data"},
    )
    assert good.status_code == 200
    assert good.json()["role_arn"] == "arn:aws:iam::210987654321:role/aim-data"
    assert good.json()["status"] == "configured"


def test_verify_success_sets_verified_and_last_scanned_at(client, s3_engine, monkeypatch):
    connection = _configured_row(s3_engine)
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: pytest.fail("boto3 must not be used"))

    response = client.post(f"/api/s3-connections/{connection.id}/verify")

    assert response.status_code == 200
    assert response.json()["status"] == "verified"
    assert response.json()["verified_at"]
    assert client.s3_broker.verify_calls == [
        {
            "role_arn": connection.role_arn,
            "region": connection.region,
            "bucket": connection.bucket,
            "prefix": connection.prefix,
        }
    ]
    with Session(s3_engine) as session:
        stored = session.get(S3Connection, connection.id)
        assert stored.status == "verified"
        assert stored.last_scanned_at is not None


def test_verify_broker_failure_sets_error(client, s3_engine):
    connection = _configured_row(s3_engine)
    client.s3_broker.verify_error = S3BrokerError("S3 broker authentication failed.")

    response = client.post(f"/api/s3-connections/{connection.id}/verify")

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "S3 broker authentication failed" in response.json()["error_message"]


def test_verify_broker_error_result_sets_error(client, s3_engine):
    connection = _configured_row(s3_engine)
    client.s3_broker.verify_result = {"status": "error", "error_message": "s3:AccessDenied"}

    response = client.post(f"/api/s3-connections/{connection.id}/verify")

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "s3:AccessDenied" in response.json()["error_message"]


def test_user_cannot_get_scan_or_list_objects_on_foreign_connection(client, s3_engine, monkeypatch):
    connection = _configured_row(s3_engine, owner_id="user-b")
    scan_job = S3ScanJob(id=str(uuid4()), connection_id=connection.id, status="completed")
    with Session(s3_engine) as session:
        session.add(scan_job)
        session.add(
            S3ObjectMetadata(
                id=str(uuid4()),
                connection_id=connection.id,
                scan_job_id=scan_job.id,
                object_key="exports/foreign.csv",
                size_bytes=1,
                content_type="text/csv",
                last_modified=datetime.now(timezone.utc),
                etag="etag",
            )
        )
        session.commit()

    scan_called = False

    def _scan_connection(_self, _connection_id):
        nonlocal scan_called
        scan_called = True
        raise AssertionError("foreign connection should be rejected before scan")

    monkeypatch.setattr(s3_connections.S3ScanService, "scan_connection", _scan_connection)

    assert client.get(f"/api/s3-connections/{connection.id}").status_code == 403
    assert client.post(f"/api/s3-connections/{connection.id}/scan").status_code == 403
    assert client.get(f"/api/s3-connections/{connection.id}/objects").status_code == 403
    assert scan_called is False


def test_register_s3_object_downloads_stages_and_enqueues_processing(client, s3_engine, monkeypatch):
    connection = _configured_row(s3_engine, status="verified")
    connection_id = connection.id
    scan_job = S3ScanJob(id=str(uuid4()), connection_id=connection.id, status="completed")
    object_metadata = S3ObjectMetadata(
        id=str(uuid4()),
        connection_id=connection.id,
        scan_job_id=scan_job.id,
        object_key="exports/report.csv",
        size_bytes=17,
        content_type="text/csv",
        last_modified=datetime.now(timezone.utc),
        etag="etag",
    )
    object_id = object_metadata.id
    with Session(s3_engine) as session:
        session.add(scan_job)
        session.add(object_metadata)
        session.commit()

    object_bytes = b"name,value\nAda,1\n"
    download_calls = []

    class FakeStreamResponse:
        def __init__(self, url: str):
            self.url = url
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

    enqueued = []

    async def fake_process_dataset_task(dataset_id: str):
        enqueued.append(dataset_id)

    monkeypatch.setattr(s3_connections.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(s3_connections, "process_dataset_task", fake_process_dataset_task)

    response = client.post(
        f"/api/s3-connections/{connection_id}/objects/{object_id}/register",
        json={"listing_id": "listing-123", "dataset_id": "draft-dataset"},
    )

    assert response.status_code == 200
    payload = response.json()
    dataset_id = payload["dataset"]["id"]
    assert dataset_id
    assert payload["object"]["dataset_id"] == dataset_id
    assert payload["dataset"]["listing_id"] == "listing-123"
    assert client.s3_broker.presign_calls == [
        {
            "role_arn": connection.role_arn,
            "region": connection.region,
            "bucket": connection.bucket,
            "object_key": "exports/report.csv",
        }
    ]
    assert download_calls == [("GET", "https://presigned.example/object.csv")]
    assert enqueued == [dataset_id]

    processing = ProcessingService()
    record = processing.get_dataset(dataset_id)
    assert record is not None
    assert record.upload_path is not None
    assert Path(record.upload_path).read_bytes() == object_bytes
    assert record.file_size_bytes == len(object_bytes)
    assert record.metadata["source_type"] == "s3"
    assert record.metadata["source_connection_id"] == connection_id
    assert record.metadata["source_object_key"] == "exports/report.csv"
    assert record.metadata["content_type"] == "text/csv"

    with Session(s3_engine) as session:
        stored_dataset = session.get(DatasetRecord, dataset_id)
        assert stored_dataset is not None
        assert stored_dataset.listing_id == "listing-123"
        assert json.loads(stored_dataset.metadata_json)["source_type"] == "s3"
        stored_object = session.get(S3ObjectMetadata, object_id)
        assert stored_object.dataset_id == dataset_id


def test_delete_removes_row(client):
    created = _create_connection(client)

    deleted = client.delete(f"/api/s3-connections/{created['id']}")
    assert deleted.status_code == 204

    missing = client.get(f"/api/s3-connections/{created['id']}")
    assert missing.status_code == 404
