from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.routers import marketplace_publish
from app.services import registration_service
from app.services.s3_publish_source_resolver import (
    NotS3PublishSource,
    S3PublishSourceResolution,
    S3PublishSourceResolutionError,
)
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
            serial_id="11111111-2222-3333-4444-555555555555",
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
    monkeypatch.setattr(
        marketplace_publish,
        "resolve_s3_publish_source",
        lambda *_args, **_kwargs: NotS3PublishSource(),
    )
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
    assert "s3_connection" not in capture["kwargs"]["json"]
    assert response.status == "published"


def _publish_request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/marketplace/publish",
        "headers": [(b"authorization", b"Bearer seller-token")],
    })


def _publish_body(**overrides) -> marketplace_publish.MarketplacePublishRequest:
    data = {
        "title": "A file",
        "description": "Buyer-facing description",
        "tags": ["finance"],
        "category": "financial",
        "price_cents": 2500,
        "vz_dataset_id": "raw-file-1",
    }
    data.update(overrides)
    return marketplace_publish.MarketplacePublishRequest(**data)


def _patch_publish_dependencies(monkeypatch, *, store=None, capture=None, s3_source=None):
    private_key = Ed25519PrivateKey.generate()
    store = store or _Store()
    capture = capture if capture is not None else {}
    store.state.vz_install_id = "11111111-1111-4111-8111-111111111111"

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
        lambda *_args, **_kwargs: s3_source if s3_source is not None else NotS3PublishSource(),
    )
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(
            _MockResponse(201, {"listing_id": "listing-1", "marketplace_url": "https://ai.market/listings/listing-1"}),
            capture,
            **kwargs,
        ),
    )
    return store, capture


@pytest.mark.asyncio
async def test_s3_publish_attaches_s3_connection_and_hash_covers_it(monkeypatch):
    s3_source = S3PublishSourceResolution(
        bucket="seller-bucket",
        region="us-east-1",
        role_arn="arn:aws:iam::123456789012:role/aim-data-delivery",
        prefix="exports/dataset-1",
        serial_id="11111111-2222-3333-4444-555555555555",
    )
    _store, capture = _patch_publish_dependencies(monkeypatch, s3_source=s3_source)

    response = await marketplace_publish.publish_to_marketplace(
        _publish_body(),
        _publish_request(),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )

    payload = capture["kwargs"]["json"]
    assert response.status == "published"
    assert payload["s3_connection"] == {
        "bucket": "seller-bucket",
        "region": "us-east-1",
        "role_arn": "arn:aws:iam::123456789012:role/aim-data-delivery",
        "prefix": "exports/dataset-1",
        "serial_id": "11111111-2222-3333-4444-555555555555",
    }

    token = capture["kwargs"]["headers"]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False}, algorithms=["EdDSA"])
    assert claims["metadata_hash"] == marketplace_publish._jcs_hash(payload)

    changed_serial = {
        **payload,
        "s3_connection": {**payload["s3_connection"], "serial_id": "99999999-2222-3333-4444-555555555555"},
    }
    changed_prefix = {
        **payload,
        "s3_connection": {**payload["s3_connection"], "prefix": "exports/dataset-2"},
    }
    assert marketplace_publish._jcs_hash(changed_serial) != claims["metadata_hash"]
    assert marketplace_publish._jcs_hash(changed_prefix) != claims["metadata_hash"]


@pytest.mark.asyncio
async def test_non_s3_publish_attaches_no_s3_connection(monkeypatch):
    _store, capture = _patch_publish_dependencies(monkeypatch, s3_source=NotS3PublishSource())

    await marketplace_publish.publish_to_marketplace(
        _publish_body(),
        _publish_request(),
        user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
    )

    assert "s3_connection" not in capture["kwargs"]["json"]


@pytest.mark.asyncio
async def test_resolver_error_fails_closed_without_publish(monkeypatch):
    store = _Store()
    capture: dict = {}
    private_key = Ed25519PrivateKey.generate()

    monkeypatch.setattr(marketplace_publish, "get_serial_store", lambda: store)
    monkeypatch.setattr(marketplace_publish, "_get_crypto", lambda: SimpleNamespace(
        get_or_create_keypairs=lambda: (private_key, None, None, None),
        has_platform_keys=lambda: True,
    ))
    monkeypatch.setattr(
        marketplace_publish,
        "ensure_vz_install_registered",
        AsyncMock(return_value="11111111-1111-4111-8111-111111111111"),
    )

    def _fail(*_args, **_kwargs):
        raise S3PublishSourceResolutionError("missing_serial_id")

    monkeypatch.setattr(marketplace_publish, "resolve_s3_publish_source", _fail)
    monkeypatch.setattr(
        marketplace_publish.httpx,
        "AsyncClient",
        lambda **kwargs: _MockAsyncClient(_MockResponse(201, {"listing_id": "listing-1"}), capture, **kwargs),
    )

    with pytest.raises(HTTPException) as exc_info:
        await marketplace_publish.publish_to_marketplace(
            _publish_body(),
            _publish_request(),
            user=SimpleNamespace(user_id="seller-uuid", key_id="ai_market_bearer"),
        )

    assert exc_info.value.status_code == 409
    assert "missing_serial_id" in exc_info.value.detail
    assert "s3_connection" not in str(exc_info.value.detail)
    assert capture == {}


def test_s3_connection_emit_allowlist_rejects_extra_field():
    with pytest.raises(ValidationError):
        marketplace_publish.S3ConnectionPublishEmit(
            bucket="seller-bucket",
            region="us-east-1",
            role_arn="arn:aws:iam::123456789012:role/aim-data-delivery",
            prefix="exports/dataset-1",
            serial_id="11111111-2222-3333-4444-555555555555",
            external_id="not-allowed",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "A file", "s3_connection": {"external_id": "not-allowed"}},
        {"title": "A file", "description": "source serial VZ-ABCDEFGH"},
    ],
)
def test_recursive_sensitive_assertion_rejects_planted_values(payload):
    with pytest.raises(HTTPException) as exc_info:
        marketplace_publish._assert_no_sensitive_publish_values(payload)

    assert exc_info.value.status_code == 409


def test_jcs_hash_parity_golden_with_s3_connection():
    payload = {
        "title": "A file",
        "description": "Buyer-facing description",
        "tags": ["finance"],
        "category": "financial",
        "pricing_type": "one_time",
        "price_cents": 2500,
        "vz_raw_listing_id": "raw-file-1",
        "download_channel": "docker",
        "s3_connection": {
            "bucket": "seller-bucket",
            "region": "us-east-1",
            "role_arn": "arn:aws:iam::123456789012:role/aim-data-delivery",
            "prefix": "exports/dataset-1",
            "serial_id": "11111111-2222-3333-4444-555555555555",
        },
    }
    # Mirrors backend VZPublishPayload.model_dump(exclude_none=True, exclude_unset=True, mode="json").
    expected_canonical = (
        b'{"category":"financial","description":"Buyer-facing description",'
        b'"download_channel":"docker","price_cents":2500,"pricing_type":"one_time",'
        b'"s3_connection":{"bucket":"seller-bucket","prefix":"exports/dataset-1",'
        b'"region":"us-east-1","role_arn":"arn:aws:iam::123456789012:role/aim-data-delivery",'
        b'"serial_id":"11111111-2222-3333-4444-555555555555"},"tags":["finance"],'
        b'"title":"A file","vz_raw_listing_id":"raw-file-1"}'
    )
    expected_hash = "aa8064fb47b4c8ea7e0bffb425d36922248e3a254697a0f8cc1029d9cae9fc31"

    assert marketplace_publish._jcs_canonical_bytes(payload) == expected_canonical
    assert marketplace_publish._jcs_hash(payload) == expected_hash


def test_jcs_hash_parity_golden_without_s3_connection():
    payload = {
        "title": "A file",
        "description": "Buyer-facing description",
        "tags": ["finance"],
        "category": "financial",
        "pricing_type": "one_time",
        "price_cents": 2500,
        "vz_raw_listing_id": "raw-file-1",
        "download_channel": "docker",
    }
    # Mirrors backend VZPublishPayload.model_dump(exclude_none=True, exclude_unset=True, mode="json").
    expected_canonical = (
        b'{"category":"financial","description":"Buyer-facing description",'
        b'"download_channel":"docker","price_cents":2500,"pricing_type":"one_time",'
        b'"tags":["finance"],"title":"A file","vz_raw_listing_id":"raw-file-1"}'
    )
    expected_hash = "902d261a556282241b708ad61d15ab82902f7042a78c7f9bf9fe82e04ed1b5f9"

    assert marketplace_publish._jcs_canonical_bytes(payload) == expected_canonical
    assert marketplace_publish._jcs_hash(payload) == expected_hash
