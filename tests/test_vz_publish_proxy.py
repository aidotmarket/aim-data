from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.requests import Request

from app.routers import marketplace_publish
from app.services import registration_service
from app.services import serial_store


class _MockResponse:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


class _MockAsyncClient:
    def __init__(self, response: _MockResponse, capture: dict | None = None, **_kwargs):
        self._response = response
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        if self._capture is not None:
            self._capture["url"] = url
            self._capture["kwargs"] = kwargs
        return self._response


class _Store:
    def __init__(self):
        self.state = SimpleNamespace(
            vz_install_id=None,
            vz_install_token=None,
            ai_market_access_token="seller-token",
            ai_market_seller_id="seller-uuid",
            last_status_cache={},
        )

    def persist_vz_install(self, install_id: str, install_token: str | None = None):
        self.state.vz_install_id = install_id
        self.state.vz_install_token = install_token

    def save(self):
        pass


@pytest.mark.asyncio
async def test_ensure_vz_install_registered_posts_public_key_and_persists(monkeypatch):
    store = _Store()
    public_key = "a" * 43
    crypto = SimpleNamespace(get_public_keys_b64=lambda: (public_key, "x-public"))
    capture: dict = {}

    monkeypatch.setattr(serial_store, "get_serial_store", lambda: store)
    monkeypatch.setattr(registration_service.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(
        registration_service.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(
            _MockResponse(201, {"install_id": "install-uuid", "install_token": "vzi_token"}),
            capture,
            **kwargs,
        ),
    )

    install_id = await registration_service.ensure_vz_install_registered(
        crypto,
        access_token="seller-token",
        seller_id="seller-uuid",
    )

    assert install_id == "install-uuid"
    assert store.state.vz_install_id == "install-uuid"
    assert store.state.vz_install_token == "vzi_token"
    assert capture["url"] == "https://ai.market.test/api/v1/vz/register"
    assert capture["kwargs"]["json"] == {"public_key_b64": public_key}
    assert capture["kwargs"]["headers"]["Authorization"] == "Bearer seller-token"


@pytest.mark.asyncio
async def test_publish_proxy_jwt_iss_is_backend_install_id(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    store = _Store()
    store.state.vz_install_id = "11111111-1111-4111-8111-111111111111"
    capture: dict = {}

    monkeypatch.setattr(marketplace_publish, "get_serial_store", lambda: store)
    monkeypatch.setattr(marketplace_publish, "_get_crypto", lambda: SimpleNamespace(
        get_or_create_keypairs=lambda: (private_key, None, None, None),
        has_platform_keys=lambda: True,
    ))
    ensure_mock = AsyncMock(return_value=store.state.vz_install_id)
    monkeypatch.setattr(marketplace_publish, "ensure_vz_install_registered", ensure_mock)
    monkeypatch.setattr(marketplace_publish.settings, "ai_market_url", "https://ai.market.test")
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(
            _MockResponse(201, {"listing_id": "listing-1", "marketplace_url": "https://ai.market/listings/listing-1"}),
            capture,
            **kwargs,
        ),
    )

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/marketplace/publish",
        "headers": [(b"authorization", b"Bearer seller-token")],
    })
    body = marketplace_publish.MarketplacePublishRequest(
        title="A file",
        description="Buyer-facing description",
        tags=["finance"],
        category="financial",
        price_cents=2500,
        vz_dataset_id="raw-file-1",
    )

    response = await marketplace_publish.publish_to_marketplace(
        body,
        request,
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )

    token = capture["kwargs"]["headers"]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False}, algorithms=["EdDSA"])
    assert claims["iss"] == store.state.vz_install_id
    assert claims["sub"] == "seller-uuid"
    assert capture["kwargs"]["json"]["vz_raw_listing_id"] == "raw-file-1"
    assert response.status == "published"
