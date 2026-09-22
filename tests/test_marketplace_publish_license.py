from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.routers import marketplace_publish as publish
from app.auth.api_key_auth import get_current_user
from app.services.preview_marketplace_transport import REFUSAL_MESSAGES
from app.services.s3_publish_source_resolver import NotS3PublishSource


def _selection(**overrides):
    value = {
        "kind": "standard",
        "version": "1.0",
        "license_document_id": None,
        "license_sha256": "a" * 64,
        "rider_sha256": None,
        "covenant_code": "marketplace-listing",
        "covenant_version": "1.0",
        "covenant_sha256": "b" * 64,
        "seller_acceptance": {
            "signer_name": "Ada Seller",
            "signer_title": "Director",
            "authority_confirmed": True,
        },
    }
    value.update(overrides)
    return value


def _body(**overrides):
    value = {
        "title": "A file",
        "description": "Buyer-facing description",
        "price_cents": 2500,
        "vz_dataset_id": "dataset-1",
    }
    value.update(overrides)
    return publish.MarketplacePublishRequest(**value)


def test_license_selection_is_carried_verbatim_into_signed_publish_payload():
    selection = _selection(ai_training=False)
    payload = publish._build_publish_payload(
        _body(license_selection=selection), NotS3PublishSource()
    )

    assert payload["license_selection"] == selection


def test_omitted_ai_training_becomes_literal_true_before_send():
    payload = publish._build_publish_payload(
        _body(license_selection=_selection()), NotS3PublishSource()
    )

    assert payload["license_selection"]["ai_training"] is True


def test_older_marketplace_publish_shape_omits_license_selection_entirely():
    payload = publish._build_publish_payload(_body(), NotS3PublishSource())

    assert "license_selection" not in payload


@pytest.mark.asyncio
async def test_local_receiver_carries_license_selection_to_marketplace(monkeypatch):
    captured = {}

    async def proxy(body, request, user):
        captured["body"] = body
        return {"status": "published", "listing_id": "listing-1"}

    monkeypatch.setattr(publish, "publish_via_signed_proxy", proxy)
    body = _body(license_selection=_selection(ai_training=False))
    response = await publish.publish_to_marketplace(
        body,
        Request({"type": "http", "method": "POST", "path": "/api/marketplace/publish", "headers": []}),
        user=SimpleNamespace(user_id="seller-1"),
        processing=SimpleNamespace(get_dataset=lambda _dataset_id: None),
    )

    assert response.listing_id == "listing-1"
    assert captured["body"].license_selection.model_dump() == _selection(ai_training=False)


@pytest.mark.asyncio
async def test_custom_license_upload_uses_marketplace_transport(monkeypatch):
    captured = {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured.update(url=url, **kwargs)
            return SimpleNamespace(
                status_code=201,
                text="",
                json=lambda: {
                    "id": "document-1",
                    "title": "terms.txt",
                    "content_type": "text/plain",
                    "size_bytes": 12,
                    "source_sha256": "d" * 64,
                    "license_sha256": "c" * 64,
                    "status": "active",
                },
            )

    monkeypatch.setattr(publish, "_seller_auth_headers", lambda _request: {"Authorization": "Bearer seller"})
    monkeypatch.setattr(publish.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = await publish.upload_custom_license(
        Request({"type": "http", "method": "POST", "path": "/api/marketplace/licenses/custom", "headers": []}),
        UploadFile(filename="terms.txt", file=BytesIO(b"Custom terms")),
        "terms.txt",
        False,
        user=SimpleNamespace(user_id="seller-1"),
    )

    assert captured["url"].endswith("/api/v1/licenses/custom")
    assert captured["headers"] == {"Authorization": "Bearer seller"}
    assert captured["files"]["upload"][1] == b"Custom terms"
    assert captured["data"] == {"title": "terms.txt", "ai_training": "false"}
    assert result == {
        "id": "document-1", "title": "terms.txt", "content_type": "text/plain",
        "size_bytes": 12, "source_sha256": "d" * 64,
        "license_sha256": "c" * 64, "status": "active",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,expected", [
    ({"master": {"enabled": False}, "listing_licenses": True}, True),
    ({"master": {"enabled": True}}, False),
    ({"listing_license": True, "license_selection": True, "enabled": True,
      "capabilities": {"listing_licenses": True}}, False),
    ({"listing_licenses": False}, False),
])
async def test_capability_reads_only_backend_listing_licenses(monkeypatch, payload, expected):
    urls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **_kwargs):
            urls.append(url)
            return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr(publish, "get_serial_store", lambda: SimpleNamespace(
        state=SimpleNamespace(ai_market_access_token=None)))
    monkeypatch.setattr(publish.httpx, "AsyncClient", lambda **_kwargs: Client())
    assert await publish._marketplace_supports_license_selection() is expected
    assert urls == [publish.settings.ai_market_url.rstrip("/") + "/api/v1/seller-workspace/capabilities"]


@pytest.mark.asyncio
async def test_selection_options_use_versioned_backend_document_routes(monkeypatch):
    paths = []

    async def document(path):
        paths.append(path)
        return {"sha256": "a" * 64, "full_text": "Exact terms", "summary": []}

    monkeypatch.setattr(publish, "_public_license_document", document)
    await publish.license_selection_options(ai_training=False, user=SimpleNamespace())
    assert set(paths) == {
        "standard/1.0/no-ai-training", "marketplace-listing/1.0",
        "ai-training-rider/1.0/not-permitted",
    }
    assert publish._license_url(paths[0]).startswith(publish.settings.ai_market_url.rstrip("/") + "/api/v1/licenses/")


@pytest.mark.parametrize(
    "code",
    [
        "SELLER_TERMS_ACCEPTANCE_PENDING",
        "LICENSE_ACCEPTANCE_INVALID",
        "LICENSE_LANGUAGE_NOT_ENGLISH",
        "LICENSE_ACCEPTANCE_STALE",
        "LICENSE_RIDER_ACCEPTANCE_STALE",
        "dataset_version_content_mismatch",
    ],
)
def test_publish_refusals_have_typed_seller_messages(code):
    detail = publish._marketplace_refusal_detail(code)

    assert detail == {"code": code, "message": REFUSAL_MESSAGES[code]}


@pytest.mark.parametrize("upstream_body,upstream_status,expected_status,expected_code", [
    ({"detail": {"code": "LICENSE_LANGUAGE_NOT_ENGLISH"}}, 422, 422, "LICENSE_LANGUAGE_NOT_ENGLISH"),
    ({"id": "document-1", "license_sha256": "c" * 64}, 201, 502, None),
])
def test_custom_upload_route_preserves_backend_refusal_and_rejects_wrong_body(
    monkeypatch, upstream_body, upstream_status, expected_status, expected_code,
):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            assert url == publish._license_url("custom")
            assert kwargs["data"]["ai_training"] == "true"
            return SimpleNamespace(status_code=upstream_status, text="", json=lambda: upstream_body)

    monkeypatch.setattr(publish, "_seller_auth_headers", lambda _request: {"Authorization": "Bearer seller"})
    monkeypatch.setattr(publish.httpx, "AsyncClient", lambda **_kwargs: Client())
    app = FastAPI()
    app.include_router(publish.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="seller-1")
    response = TestClient(app).post(
        "/api/marketplace/licenses/custom",
        data={"title": "terms.txt", "ai_training": "true"},
        files={"upload": ("terms.txt", b"Custom terms", "text/plain")},
    )
    assert response.status_code == expected_status
    if expected_code:
        assert response.json()["detail"]["code"] == expected_code
    else:
        assert response.json()["detail"] == "ai.market returned an invalid custom licence record"
