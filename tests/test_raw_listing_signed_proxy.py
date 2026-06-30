from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_session_context
from app.models.raw_listing import RawListing
from app.routers import marketplace_publish, raw_listings
from app.routers.raw_listings import router as raw_listings_router
from app.services import raw_listing_service
from app.services.s3_publish_source_resolver import NotS3PublishSource


class _MockResponse:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


class _MockAsyncClient:
    def __init__(self, response: _MockResponse, capture: dict, **_kwargs):
        self._response = response
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        self._capture.setdefault("calls", []).append({"url": url, **kwargs})
        return self._response


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
def client():
    app = FastAPI()
    app.include_router(raw_listings_router, prefix="/api/raw")
    return TestClient(app)


@pytest.fixture
def raw_listing(client, tmp_path):
    raw_path = tmp_path / "customers.csv"
    raw_path.write_text("segment,spend\nenterprise,100\n")
    file_resp = client.post("/api/raw/files", json={"file_path": str(raw_path)})
    file_resp.raise_for_status()
    listing_resp = client.post(
        "/api/raw/listings",
        json={
            "raw_file_id": file_resp.json()["id"],
            "title": "Raw Customer Spend",
            "description": "Raw customer spend export.",
            "category": "analytics",
            "tags": ["raw", "csv"],
            "price_cents": 1200,
        },
    )
    listing_resp.raise_for_status()
    return listing_resp.json()


def _patch_signed_proxy(monkeypatch, *, response: _MockResponse | None = None):
    private_key = Ed25519PrivateKey.generate()
    store = _Store()
    capture: dict = {}
    resolved = {}

    monkeypatch.setattr(raw_listing_service.RawListingService, "_create_marketplace_listing", pytest.fail)
    monkeypatch.setattr(marketplace_publish, "get_serial_store", lambda: store)
    monkeypatch.setattr(
        marketplace_publish,
        "_get_crypto",
        lambda: SimpleNamespace(
            get_or_create_keypairs=lambda: (private_key, None, None, None),
            has_platform_keys=lambda: True,
        ),
    )
    monkeypatch.setattr(
        marketplace_publish,
        "ensure_vz_install_registered",
        AsyncMock(return_value=store.state.vz_install_id),
    )

    def _resolve(dataset_id, user, state):
        resolved["dataset_id"] = dataset_id
        resolved["user"] = user
        resolved["state"] = state
        return NotS3PublishSource()

    monkeypatch.setattr(marketplace_publish, "resolve_s3_publish_source", _resolve)
    monkeypatch.setattr(marketplace_publish.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(
            response or _MockResponse(201, {"listing_id": "signed-listing-1"}),
            capture,
            **kwargs,
        ),
    )
    return store, capture, resolved


def test_raw_publish_routes_through_signed_proxy_and_persists_listing_id(client, raw_listing, monkeypatch):
    store, capture, resolved = _patch_signed_proxy(monkeypatch)

    resp = client.post(
        f"/api/raw/listings/{raw_listing['id']}/publish",
        headers={"Authorization": "Bearer seller-token"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "listed"
    assert data["marketplace_listing_id"] == "signed-listing-1"

    assert len(capture["calls"]) == 1
    call = capture["calls"][0]
    assert call["url"] == "https://ai.market.test/api/v1/vz/publish"
    assert "/api/v1/listings/" not in call["url"]
    payload = call["json"]
    assert payload["vz_raw_listing_id"] == raw_listing["id"]
    assert payload["title"] == "Raw Customer Spend"
    assert payload["description"] == "Raw customer spend export."
    assert payload["tags"] == ["raw", "csv"]
    assert payload["category"] == "analytics"
    assert payload["pricing_type"] == "one_time"
    assert payload["price_cents"] == 1200
    assert payload["file_size_bytes"] > 0
    assert payload["file_format"] is not None
    assert payload["schema_info"]["type"] == "raw_file"
    assert payload["schema_info"]["filename"] == "customers.csv"
    assert payload["schema_info"]["file_size_bytes"] == payload["file_size_bytes"]
    assert payload["compliance_status"] == "not_checked"
    assert payload["download_channel"]
    assert "privacy_scan_status" not in payload
    assert "s3_connection" not in payload
    assert resolved["dataset_id"] == raw_listing["id"]

    token = call["headers"]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False}, algorithms=["EdDSA"])
    assert claims["iss"] == store.state.vz_install_id
    assert claims["sub"] == "seller-uuid"
    assert claims["metadata_hash"] == marketplace_publish._jcs_hash(payload)

    with get_session_context() as session:
        persisted = session.get(RawListing, raw_listing["id"])
        assert persisted.marketplace_listing_id == "signed-listing-1"
        assert persisted.status == "listed"


def test_raw_publish_rejects_non_active_seller_without_listing(client, raw_listing, monkeypatch):
    _store, _capture, _resolved = _patch_signed_proxy(
        monkeypatch,
        response=_MockResponse(403, {"detail": "Seller must be active to publish"}),
    )

    resp = client.post(f"/api/raw/listings/{raw_listing['id']}/publish")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Seller must be active to publish"
    with get_session_context() as session:
        persisted = session.get(RawListing, raw_listing["id"])
        assert persisted.status == "draft"
        assert persisted.marketplace_listing_id is None


def test_raw_publish_guard_runs_before_signed_proxy(client, raw_listing, monkeypatch):
    _patch_signed_proxy(monkeypatch)
    publish_resp = client.post(f"/api/raw/listings/{raw_listing['id']}/publish")
    assert publish_resp.status_code == 200

    async def _publish(*_args, **_kwargs):
        raise AssertionError("publish_via_signed_proxy should not be called")

    monkeypatch.setattr(raw_listings, "publish_via_signed_proxy", _publish)
    resp = client.post(f"/api/raw/listings/{raw_listing['id']}/publish")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Listing is already published"
