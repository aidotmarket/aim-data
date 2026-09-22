"""Cross-repository contract for ai-market-backend preview refusals."""

import httpx
import pytest

from app.services.preview_marketplace_transport import (
    REFUSAL_MESSAGES,
    PreviewMarketplaceTransport,
    PreviewTransportError,
)


LISTING = "00000000-0000-4000-8000-000000000004"
CORRELATION_ID = "00000000-0000-4000-8000-000000000099"

# ERROR_STATUS_BY_CODE at ai-market-backend 801170e6 (merged to main as e165d023, PR #444). Only the 409, 413, and
# 422 refusal codes require seller-actionable AIM Data messages.
BACKEND_REFUSAL_CODES = {
    "stale_source_revision",
    "stale_summary",
    "stale_expected_head",
    "approval_binding_mismatch",
    "candidate_binding_mismatch",
    "commitment_binding_mismatch",
    "commitment_not_current",
    "summary_binding_mismatch",
    "approval_expired",
    "idempotency_conflict",
    "request_id_conflict",
    "registration_evidence_stale",
    "verified_sample_unavailable_above_25_column_cap",
    "dictionary_must_match_committed_dataset_schema_republish_through_aim_data",
    "approval_binding_invalid",
    "decision_mismatch",
    "disclosure_signature_invalid",
    "commitment_signature_invalid",
    "proof_signature_invalid",
    "signer_authority_invalid",
    "signer_evidence_invalid",
    "signer_evidence_missing",
    "signer_evidence_expired",
    "signer_fingerprint_mismatch",
    "registration_key_mismatch",
    "registration_owner_mismatch",
    "registration_inactive",
    "envelope_binding_mismatch",
    "contract_mismatch",
    "aggregate_hash_mismatch",
    "invalid_evidence_policy",
    "invalid_public_key",
    "invalid_preview_metadata",
    "metadata_bound_exceeded",
}


def transport_for(status: int, body: dict[str, str]) -> PreviewMarketplaceTransport:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json=body)
        )
    )
    return PreviewMarketplaceTransport(
        base_url="https://api.ai.market", token="seller-token", client=client
    )


def test_exact_backend_body_prefers_code_and_preserves_correlation_id():
    transport = transport_for(
        409,
        {
            "detail": "stale_source_revision",
            "code": "stale_source_revision",
            "correlation_id": CORRELATION_ID,
        },
    )

    with pytest.raises(PreviewTransportError) as caught:
        transport.preview_summary(LISTING)

    assert caught.value.code == "stale_source_revision"
    assert caught.value.correlation_id == CORRELATION_ID
    assert REFUSAL_MESSAGES["stale_source_revision"] in caught.value.message
    assert CORRELATION_ID in caught.value.message


def test_code_is_preferred_over_legacy_detail():
    transport = transport_for(
        409,
        {
            "detail": "legacy_code",
            "code": "stale_source_revision",
            "correlation_id": CORRELATION_ID,
        },
    )

    with pytest.raises(PreviewTransportError) as caught:
        transport.preview_summary(LISTING)

    assert caught.value.code == "stale_source_revision"


def test_every_backend_409_413_422_code_has_a_seller_message():
    assert BACKEND_REFUSAL_CODES <= REFUSAL_MESSAGES.keys()


def test_unexpected_503_surfaces_the_backend_reference():
    transport = transport_for(
        503,
        {
            "detail": "unexpected_error",
            "code": "unexpected_error",
            "correlation_id": CORRELATION_ID,
        },
    )

    with pytest.raises(PreviewTransportError) as caught:
        transport.preview_summary(LISTING)

    assert caught.value.code == "unexpected_error"
    assert caught.value.correlation_id == CORRELATION_ID
    assert caught.value.message == (
        f"ai.market could not complete this step (reference {CORRELATION_ID}); "
        "try again later"
    )
