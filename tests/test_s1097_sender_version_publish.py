from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session
from starlette.requests import Request

from app.auth.api_key_auth import AuthenticatedUser
from app.models.s3_connection import S3Connection
from app.models.s3_object_metadata import S3ObjectMetadata
from app.models.s3_scan_job import S3ScanJob
from app.routers import marketplace_publish, s3_connections
from app.services.listing_versioning import build_version_prefix
from app.services.s3_publish_source_resolver import S3PublishSourceResolution


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "version_prefix_vectors.json"
PINNED_FIXTURE_SHA256 = "e626b68a463fd764399789a240f06ed610a7c1b6ddd1d34d915eeb644e299013"
USER_A = AuthenticatedUser(user_id="user-a", key_id="key-a", scopes=["read", "write"], valid=True)


class _MockResponse:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


class _MockAsyncClient:
    def __init__(self, responses: list[_MockResponse], capture: list[dict], **_kwargs):
        self._responses = responses
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        self._capture.append({"url": url, "kwargs": kwargs})
        return self._responses.pop(0)


class _Store:
    def __init__(self):
        self.state = SimpleNamespace(
            vz_install_id="11111111-1111-4111-8111-111111111111",
            vz_install_token=None,
            ai_market_access_token="seller-token",
            ai_market_seller_id="seller-uuid",
            serial_id="11111111-2222-3333-4444-555555555555",
            last_status_cache={},
        )


@pytest.fixture
def s3_engine(monkeypatch):
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

    monkeypatch.setattr(marketplace_publish, "get_session_context", _session_context)
    monkeypatch.setattr(s3_connections, "get_session_context", _session_context)
    return engine


def _add_completed_version_scan(engine) -> tuple[S3Connection, S3ScanJob]:
    now = datetime.now(timezone.utc)
    connection = S3Connection(
        id="connection-1",
        owner_id="user-a",
        name="Seller bucket",
        bucket="seller-bucket",
        region="us-east-1",
        prefix="exports/dataset-1",
        role_arn="arn:aws:iam::123456789012:role/aim-data",
        external_id="external-id",
        status="verified",
    )
    scan_job = S3ScanJob(
        id="scan-1",
        connection_id=connection.id,
        status="completed",
        started_at=now,
        completed_at=now,
        objects_enumerated=2,
        sampled_stats={
            "object_count": 2,
            "total_size_bytes": 30,
            "type_histogram": {"text/csv": 2},
            "approximate": False,
            "sample_coverage": "full",
            "sampled_object_count": 2,
        },
    )
    rows = [
        S3ObjectMetadata(
            id="object-1",
            connection_id=connection.id,
            scan_job_id=scan_job.id,
            object_key="exports/dataset-1/v2/a.csv",
            size_bytes=10,
            content_type="text/csv",
            last_modified=now,
            etag="etag-a",
        ),
        S3ObjectMetadata(
            id="object-2",
            connection_id=connection.id,
            scan_job_id=scan_job.id,
            object_key="exports/dataset-1/v2/b.csv",
            size_bytes=20,
            content_type="text/csv",
            last_modified=now,
            etag="etag-b",
        ),
    ]
    with Session(engine) as session:
        session.add(connection)
        session.add(scan_job)
        for row in rows:
            session.add(row)
        session.commit()
        session.refresh(connection)
        session.refresh(scan_job)
        session.expunge(connection)
        session.expunge(scan_job)
    return connection, scan_job


def _publish_request(path: str = "/api/marketplace/versions/publish") -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"authorization", b"Bearer seller-token")],
    })


def _version_publish_body(**overrides) -> marketplace_publish.MarketplaceVersionPublishRequest:
    data = {
        "title": "Dataset",
        "description": "Buyer-facing description",
        "tags": ["finance"],
        "category": "financial",
        "price_cents": 2500,
        "vz_dataset_id": "dataset-1",
        "s3_connection_id": "connection-1",
        "scan_job_id": "scan-1",
        "version_label": "v2",
    }
    data.update(overrides)
    return marketplace_publish.MarketplaceVersionPublishRequest(**data)


def _patch_publish_dependencies(monkeypatch, responses: list[_MockResponse], capture: list[dict]):
    private_key = Ed25519PrivateKey.generate()
    store = _Store()
    monkeypatch.setattr(marketplace_publish, "get_serial_store", lambda: store)
    monkeypatch.setattr(marketplace_publish, "_get_crypto", lambda: SimpleNamespace(
        get_or_create_keypairs=lambda: (private_key, None, None, None),
        has_platform_keys=lambda: True,
    ))
    monkeypatch.setattr(
        marketplace_publish,
        "ensure_vz_install_registered",
        AsyncMock(return_value=store.state.vz_install_id),
    )
    monkeypatch.setattr(marketplace_publish.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(
        marketplace_publish,
        "resolve_s3_publish_source",
        lambda *_args, **_kwargs: S3PublishSourceResolution(
            bucket="seller-bucket",
            region="us-east-1",
            role_arn="arn:aws:iam::123456789012:role/aim-data",
            prefix="exports/dataset-1",
            serial_id="11111111-2222-3333-4444-555555555555",
        ),
    )
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(responses, capture, **kwargs),
    )
    return private_key


def test_version_prefix_vector_fixture_checksum_is_pinned() -> None:
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == PINNED_FIXTURE_SHA256


@pytest.mark.parametrize("vector", json.loads(FIXTURE_PATH.read_text()))
def test_build_version_prefix_matches_shared_vectors(vector: dict) -> None:
    if vector["valid"]:
        assert build_version_prefix(vector["parent_prefix"], vector["version_label"]) == vector["expected_prefix"]
    else:
        with pytest.raises(ValueError):
            build_version_prefix(vector["parent_prefix"], vector["version_label"])


def test_version_publish_hash_parity_fixture() -> None:
    payload = {
        "title": "Dataset",
        "description": "Buyer-facing description",
        "tags": ["finance"],
        "category": "financial",
        "pricing_type": "one_time",
        "price_cents": 2500,
        "vz_raw_listing_id": "dataset-1",
        "download_channel": "direct",
        "s3_connection": {
            "bucket": "seller-bucket",
            "region": "us-east-1",
            "role_arn": "arn:aws:iam::123456789012:role/aim-data",
            "prefix": "exports/dataset-1",
            "serial_id": "11111111-2222-3333-4444-555555555555",
        },
        "versions": [
            {
                "version_label": "v2",
                "object_count": 2,
                "total_size_bytes": 30,
                "manifest_hash": "f" * 64,
            }
        ],
    }

    sender_hash = marketplace_publish._jcs_hash(payload)
    receiver_canonical_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    assert sender_hash == receiver_canonical_hash
    assert sender_hash == "5425c224c41a20ba4c99e03a35e83cf3e5bd4e2ac8108fbb3118f9dfb101136b"


def test_no_versions_legacy_publish_payload_is_unchanged() -> None:
    body = marketplace_publish.MarketplacePublishRequest(
        title="Dataset",
        description="Buyer-facing description",
        tags=["finance"],
        category="financial",
        price_cents=2500,
        vz_dataset_id="dataset-1",
    )
    s3_source = S3PublishSourceResolution(
        bucket="seller-bucket",
        region="us-east-1",
        role_arn="arn:aws:iam::123456789012:role/aim-data",
        prefix="exports/dataset-1",
        serial_id="11111111-2222-3333-4444-555555555555",
    )

    payload = marketplace_publish._build_publish_payload(body, s3_source)

    assert payload == {
        "title": "Dataset",
        "description": "Buyer-facing description",
        "tags": ["finance"],
        "category": "financial",
        "pricing_type": "one_time",
        "price_cents": 2500,
        "vz_raw_listing_id": "dataset-1",
            "download_channel": "direct",
        "s3_connection": {
            "bucket": "seller-bucket",
            "region": "us-east-1",
            "role_arn": "arn:aws:iam::123456789012:role/aim-data",
            "prefix": "exports/dataset-1",
            "serial_id": "11111111-2222-3333-4444-555555555555",
        },
    }


@pytest.mark.asyncio
async def test_version_scan_starts_background_scan_of_derived_prefix(s3_engine, monkeypatch) -> None:
    connection, _scan_job = _add_completed_version_scan(s3_engine)
    created_job = S3ScanJob(
        id="scan-new",
        connection_id=connection.id,
        status="running",
        started_at=datetime.now(timezone.utc),
    )

    class FakeScanService:
        def create_scan_job(self, connection_id: str) -> S3ScanJob:
            assert connection_id == connection.id
            return created_job

        def run_scan_job_for_prefix(self, scan_job_id: str, prefix: str) -> None:
            pass

    monkeypatch.setattr(s3_connections, "S3ScanService", FakeScanService)
    background_tasks = BackgroundTasks()

    response = await s3_connections.scan_version_prefix(
        connection.id,
        s3_connections.S3VersionScanRequest(version_label="v2"),
        background_tasks,
        user=USER_A,
    )

    assert response.version_prefix == "exports/dataset-1/v2/"
    assert response.scan_job.id == "scan-new"
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].args == ("scan-new", "exports/dataset-1/v2/")


@pytest.mark.asyncio
async def test_publish_quarantine_confirm_round_trip_and_superseded_honesty(s3_engine, monkeypatch) -> None:
    _add_completed_version_scan(s3_engine)
    capture: list[dict] = []
    responses = [
        _MockResponse(
            201,
            {
                "listing_id": "listing-1",
                "marketplace_url": "https://ai.market/listings/listing-1",
                "versions": [
                    {
                        "version_id": "version-1",
                        "version_label": "v2",
                        "status": "quarantined",
                        "quarantine_reason": "baseline_percentage_swing",
                    }
                ],
            },
        ),
        _MockResponse(
            200,
            {
                "version_id": "version-1",
                "listing_id": "listing-1",
                "version_label": "v2",
                "status": "superseded",
                "quarantine_reason": None,
            },
        ),
    ]
    _patch_publish_dependencies(monkeypatch, responses, capture)

    publish_response = await marketplace_publish.publish_version_to_marketplace(
        _version_publish_body(),
        _publish_request(),
        user=SimpleNamespace(user_id="user-a", key_id="ai_market_bearer"),
    )
    confirm_response = await marketplace_publish.confirm_marketplace_version(
        "version-1",
        _publish_request("/api/marketplace/versions/version-1/confirm"),
        user=SimpleNamespace(user_id="user-a", key_id="ai_market_bearer"),
    )

    publish_payload = capture[0]["kwargs"]["json"]
    assert publish_response.version_status == "quarantined"
    assert publish_response.quarantine_reason == "baseline_percentage_swing"
    assert publish_payload["versions"][0]["version_label"] == "v2"
    assert publish_payload["versions"][0]["object_count"] == 2
    assert publish_payload["versions"][0]["total_size_bytes"] == 30
    token = capture[0]["kwargs"]["headers"]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False}, algorithms=["EdDSA"])
    assert claims["metadata_hash"] == marketplace_publish._jcs_hash(publish_payload)

    assert capture[1]["url"] == "https://ai.market.test/api/v1/vz/versions/version-1/confirm"
    assert confirm_response.status == "superseded"
    assert confirm_response.result == "confirmed_but_superseded"


def test_version_publish_rejects_scan_rows_outside_requested_prefix(s3_engine) -> None:
    _add_completed_version_scan(s3_engine)

    with pytest.raises(HTTPException) as exc_info:
        marketplace_publish._version_emit_from_scan(
            connection_id="connection-1",
            scan_job_id="scan-1",
            version_label="v3",
            user=SimpleNamespace(user_id="user-a"),
        )

    assert exc_info.value.status_code == 409
