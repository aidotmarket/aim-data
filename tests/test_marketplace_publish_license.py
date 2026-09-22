from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from starlette.requests import Request

from app.routers import marketplace_publish as publish
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
                    "license_document_id": "document-1",
                    "license_sha256": "c" * 64,
                    "full_text": "Custom terms",
                },
            )

    monkeypatch.setattr(publish, "_seller_auth_headers", lambda _request: {"Authorization": "Bearer seller"})
    monkeypatch.setattr(publish.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = await publish.upload_custom_license(
        Request({"type": "http", "method": "POST", "path": "/api/marketplace/licenses/custom", "headers": []}),
        UploadFile(filename="terms.txt", file=BytesIO(b"Custom terms")),
        "terms.txt",
        user=SimpleNamespace(user_id="seller-1"),
    )

    assert captured["url"].endswith("/licenses/custom")
    assert captured["headers"] == {"Authorization": "Bearer seller"}
    assert captured["files"]["file"][1] == b"Custom terms"
    assert result["license_document_id"] == "document-1"
    assert result["sha256"] == "c" * 64


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
