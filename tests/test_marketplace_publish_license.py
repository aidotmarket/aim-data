from io import BytesIO
import hashlib
import json
from pathlib import Path
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
    model = publish.LicensedMarketplacePublishRequest if "license_selection" in overrides else publish.MarketplacePublishRequest
    return model(**value)


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


def test_flag_off_openapi_and_route_inventory_equal_exact_base():
    from app.main import app

    pinned = json.loads((Path(__file__).parent / "fixtures/s1735_aim_data_base_parity.json").read_text())
    canonical = json.dumps(app.openapi(), sort_keys=True, separators=(",", ":")).encode()
    routes = sorted([route.path, sorted(getattr(route, "methods", []) or [])] for route in app.routes)
    assert pinned["base_sha"] == "6529e1ed4d5983a87a9b1e2faadda32e18d4cf76"
    assert hashlib.sha256(canonical).hexdigest() == pinned["openapi_sha256"]
    assert routes == pinned["routes"]
    assert "license_selection" not in app.openapi()["components"]["schemas"]["MarketplacePublishRequest"]["properties"]


@pytest.mark.asyncio
async def test_publish_status_flag_off_matches_base_body(monkeypatch):
    app = FastAPI()
    app.state.listing_licenses_enabled = False
    request = Request({"type": "http", "method": "GET", "path": "/api/marketplace/publish-status", "headers": [], "app": app})
    monkeypatch.setattr(publish.settings, "keystore_passphrase", "test-passphrase")
    monkeypatch.setattr(publish, "_get_crypto", lambda: SimpleNamespace(
        get_or_create_keypairs=lambda: None, has_platform_keys=lambda: True))
    body = await publish.publish_status(user=SimpleNamespace(), request=request)
    assert json.dumps(body, separators=(",", ":")).encode() == b'{"can_publish":true,"reason":null}'
    app.state.listing_licenses_enabled = True
    assert (await publish.publish_status(user=SimpleNamespace(), request=request))["listing_licenses"] is True


def test_capability_on_mounts_licensed_routes_and_request_schema():
    app = FastAPI()
    app.include_router(publish.router, prefix="/api")
    publish.activate_listing_license_routes(app, [])
    paths = app.openapi()["paths"]
    assert "/api/marketplace/licenses/selection-options" in paths
    assert "/api/marketplace/licenses/custom" in paths
    request_schema = paths["/api/marketplace/publish"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert request_schema.endswith("/LicensedMarketplacePublishRequest")
    assert "license_selection" in app.openapi()["components"]["schemas"]["LicensedMarketplacePublishRequest"]["required"]


def test_capability_on_publish_route_forwards_exact_selection(monkeypatch):
    from app.services.processing_service import get_processing_service

    captured = {}

    async def proxy(body, request, user):
        captured["selection"] = body.license_selection.model_dump()
        return {"status": "published"}

    monkeypatch.setattr(publish, "publish_via_signed_proxy", proxy)
    app = FastAPI()
    app.include_router(publish.router, prefix="/api")
    publish.activate_listing_license_routes(app, [])
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="seller-1")
    app.dependency_overrides[get_processing_service] = lambda: SimpleNamespace(get_dataset=lambda _id: None)
    client = TestClient(app)
    assert client.post("/api/marketplace/publish", json={"title": "A file", "description": "Description",
        "price_cents": 2500, "vz_dataset_id": "dataset-1"}).status_code == 422
    response = client.post("/api/marketplace/publish", json={"title": "A file", "description": "Description",
        "price_cents": 2500, "vz_dataset_id": "dataset-1", "license_selection": _selection(ai_training=False)})
    assert response.status_code == 200
    assert captured["selection"] == _selection(ai_training=False)


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
    # Captured via TestClient from backend 61f7547c0c1192d82a1a4f2e59b7ae65d4fb1f47.
    captured = json.loads((Path(__file__).parent / "fixtures/s1735_chunk_e_license_documents.json").read_text())
    paths = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            path = url.split("/api/v1/licenses/", 1)[1]
            paths.append(path)
            assert kwargs["params"] == {"format": "json"}
            return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, json=lambda: captured[path])

    monkeypatch.setattr(publish.httpx, "AsyncClient", lambda **_kwargs: Client())
    app = FastAPI()
    app.include_router(publish.license_router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="seller-1")
    response = TestClient(app).get("/api/marketplace/licenses/selection-options?ai_training=false")
    assert response.status_code == 200
    result = response.json()
    assert set(paths) == set(captured)
    assert result == {"standard": captured[paths[0]], "covenant": captured[paths[1]], "rider": captured[paths[2]]}


@pytest.mark.asyncio
async def test_document_contract_refuses_plain_text_and_wrong_json(monkeypatch):
    class Client:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, **kwargs):
            assert kwargs["params"] == {"format": "json"}
            return self.response

    monkeypatch.setattr(publish.httpx, "AsyncClient", lambda **_kwargs: Client(
        SimpleNamespace(status_code=200, headers={"content-type": "text/plain"}, json=lambda: (_ for _ in ()).throw(ValueError()))))
    with pytest.raises(publish.HTTPException) as exc:
        await publish._public_license_document("marketplace-listing/1.0")
    assert exc.value.status_code == 502

    captured = json.loads((Path(__file__).parent / "fixtures/s1735_chunk_e_license_documents.json").read_text())
    for change in ({"parameters": {"ai_training": True}}, {"text": "unexpected"}, {"sha256": "bad"}):
        wrong = {**captured["marketplace-listing/1.0"], **change}
        with pytest.raises(publish.HTTPException) as exc:
            publish._normalize_license_document(wrong, "marketplace-listing/1.0")
        assert exc.value.status_code == 502
    wrong = {**captured["standard/1.0/no-ai-training"], "parameters": {"ai_training": 0}}
    with pytest.raises(publish.HTTPException) as exc:
        publish._normalize_license_document(wrong, "standard/1.0/no-ai-training")
    assert exc.value.status_code == 502


@pytest.mark.parametrize(
    "code",
    [
        "SELLER_TERMS_ACCEPTANCE_PENDING",
        "LICENSE_SELECTION_REQUIRED",
        "LICENSE_SELECTION_INVALID",
        "LICENSE_ACCEPTANCE_INVALID",
        "LICENSE_DOCUMENT_INVALID",
        "LICENSE_AUTHORITY_REQUIRED",
        "LICENSE_LANGUAGE_NOT_ENGLISH",
        "LICENSE_ACCEPTANCE_STALE",
        "LICENSE_RIDER_ACCEPTANCE_STALE",
        "LICENSE_SIZE_INVALID",
        "LICENSE_MIME_MISMATCH",
        "LICENSE_MALWARE_SCAN_UNAVAILABLE",
        "LICENSE_MALWARE_DETECTED",
        "LICENSE_SECRET_DETECTED",
        "LICENSE_PDF_INVALID",
        "LICENSE_PDF_ACTIVE_CONTENT",
        "LICENSE_TEXT_INVALID_UTF8",
        "LICENSE_TEXT_NOT_NFC",
        "LICENSE_PROHIBITED_TERMS",
        "LICENSE_UPLOAD_UNAVAILABLE",
        "GATEWAY_LICENSE_UPGRADE_REQUIRED",
        "LICENSE_ACCEPTANCE_REQUIRED",
        "LICENSE_TERMINATED",
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
    app.include_router(publish.license_router, prefix="/api")
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
