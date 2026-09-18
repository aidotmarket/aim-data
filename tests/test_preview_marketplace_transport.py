"""HTTP fakes mirror backend S1716 response shapes; no row payloads or live writes."""

import httpx
import pytest

from app.services.preview_marketplace_transport import (
    MARKETPLACE_AUTH_LOCAL_STATUS,
    REFUSAL_MESSAGES,
    PreviewMarketplaceTransport,
    PreviewTransportError,
)
from tests.preview_fixture_factory import request_fixture


LISTING = "00000000-0000-4000-8000-000000000004"


def test_summary_candidate_submit_and_live_manifest_sequence():
    signed = request_fixture()
    calls = []
    summary = {
        "summary_id": signed["binding"]["summary_id"],
        "source_revision": signed["binding"]["source_revision"],
        "summary_hash": signed["binding"]["summary_hash"],
        "render_hash": signed["binding"]["render_hash"],
        "state": "pending",
        "status": "pending",
        "at_a_glance": {"profile": "aim-listing-enrichment-profile-v2"},
        "approval_text": "Approve the current summary.",
    }

    def handler(request):
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/at-a-glance/preview") and request.method == "GET":
            value = dict(summary)
            if sum(path.endswith("/at-a-glance/preview") for _, path, _ in calls) > 1:
                value.update(state="approved", status="approved")
            return httpx.Response(200, json=value)
        if request.url.path.endswith("/at-a-glance/approve"):
            return httpx.Response(200, json={
                "decision_id": signed["binding"]["request_id"],
                "disclosure_version": signed["binding"]["disclosure_version"],
                "decision": "approve",
            })
        if request.url.path.endswith("/at-a-glance/preview") and request.method == "POST":
            return httpx.Response(200, json={"binding": signed["binding"]})
        if request.url.path == f"/api/v1/listings/{LISTING}":
            return httpx.Response(200, json={"id": LISTING, "slug": "synthetic-listing"})
        if request.url.path.endswith("/preview-manifest"):
            return httpx.Response(200, json={
                "profile": "aim-listing-preview-v1",
                "disclosure_version": signed["binding"]["disclosure_version"],
            })
        raise AssertionError(request.url.path)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = PreviewMarketplaceTransport(base_url="https://api.ai.market", token="seller-token", client=client)
    assert transport.ensure_summary(LISTING)["state"] == "approved"
    assert transport.allocate(LISTING, signed["binding"]) == signed["binding"]
    assert transport.submit(LISTING, signed)["decision"] == "approve"
    live = transport.live_state(LISTING)
    assert live == {
        "state": "visible",
        "listing_url": "https://ai.market/listings/synthetic-listing",
        "manifest_url": "https://api.ai.market/api/v1/public/listings/synthetic-listing/preview-manifest",
        "disclosure_version": signed["binding"]["disclosure_version"],
    }
    assert all(auth == "Bearer seller-token" for _, _, auth in calls)


@pytest.mark.parametrize(
    "code,expected",
    [
        ("stale_expected_head", "Refresh its live state"),
        ("signer_authority_invalid", "register this install again"),
        ("summary_schema_mismatch", "Update At a glance"),
    ],
)
def test_backend_refusal_codes_have_exact_seller_fixes(code, expected):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(409, json={"detail": code})
    ))
    transport = PreviewMarketplaceTransport(base_url="https://api.ai.market", token="seller-token", client=client)
    with pytest.raises(PreviewTransportError) as caught:
        transport.preview_summary(LISTING)
    assert caught.value.code == code
    assert expected in caught.value.message


@pytest.mark.parametrize("code", sorted(REFUSAL_MESSAGES))
def test_every_stable_backend_refusal_has_a_specific_fix(code):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(409, json={"detail": code})
    ))
    transport = PreviewMarketplaceTransport(
        base_url="https://api.ai.market", token="seller-token", client=client
    )
    with pytest.raises(PreviewTransportError) as caught:
        transport.preview_summary(LISTING)
    assert caught.value.message == REFUSAL_MESSAGES[code]
    assert code not in caught.value.message


def test_missing_seller_session_never_opens_network():
    transport = PreviewMarketplaceTransport(base_url="https://api.ai.market", token="", client=None)
    transport.token = None
    with pytest.raises(PreviewTransportError, match="seller_session_required") as caught:
        transport.preview_summary(LISTING)
    assert caught.value.message == "Sign in to ai.market in AIM Data, then try again."
    assert caught.value.status == MARKETPLACE_AUTH_LOCAL_STATUS


@pytest.mark.parametrize(
    "upstream_status,expected_code",
    [(401, "seller_session_expired"), (403, "listing_not_owned")],
)
def test_marketplace_auth_failures_use_reserved_local_status(
    upstream_status, expected_code
):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(upstream_status, json={"detail": "denied"})
        )
    )
    transport = PreviewMarketplaceTransport(
        base_url="https://api.ai.market", token="seller-token", client=client
    )
    with pytest.raises(PreviewTransportError) as caught:
        transport.preview_summary(LISTING)
    assert caught.value.code == expected_code
    assert caught.value.status == MARKETPLACE_AUTH_LOCAL_STATUS
