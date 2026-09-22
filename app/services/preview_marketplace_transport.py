"""Metadata-only transport for the live ai.market verified-preview contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from app.config import settings
from app.services.serial_store import get_serial_store


REFUSAL_MESSAGES = {
    "SELLER_TERMS_ACCEPTANCE_PENDING": "Accept the current ai.market seller terms, then try publishing again.",
    "SELLER_LEGAL_IDENTITY_REQUIRED": "Add your legal name and jurisdiction to your seller profile, then try publishing again.",
    "LICENSE_ACCEPTANCE_INVALID": "The licence choice is incomplete or invalid. Review the licence and confirmation, then try again.",
    "LICENSE_SELECTION_REQUIRED": "Choose a licence and confirm the marketplace covenant before publishing.",
    "LICENSE_SELECTION_INVALID": "The licence choice is invalid. Review the document and confirmation, then try again.",
    "LICENSE_DOCUMENT_INVALID": "The custom licence is unavailable or invalid. Upload a valid document and try again.",
    "LICENSE_AUTHORITY_REQUIRED": "Confirm that you have authority to offer this data under the selected licence.",
    "LICENSE_LANGUAGE_NOT_ENGLISH": "The seller licence must have English as its canonical language. Upload an English licence and try again.",
    "LICENSE_ACCEPTANCE_STALE": "The licence terms changed. Review the current terms and confirm them again.",
    "LICENSE_RIDER_ACCEPTANCE_STALE": "The AI-training rider changed. Review the current rider and confirm it again.",
    "LICENSE_SIZE_INVALID": "The custom licence must be 1 MiB or smaller. Choose a smaller document.",
    "LICENSE_MIME_MISMATCH": "The file type does not match its contents. Upload a UTF-8 text file or PDF.",
    "LICENSE_MALWARE_SCAN_UNAVAILABLE": "The licence security scan is unavailable. Try again later.",
    "LICENSE_MALWARE_DETECTED": "The licence file failed the security scan. Upload a clean document.",
    "LICENSE_SECRET_DETECTED": "The licence appears to contain a secret. Remove it and upload again.",
    "LICENSE_PDF_INVALID": "The PDF could not be validated. Upload a valid PDF or UTF-8 text file.",
    "LICENSE_PDF_ACTIVE_CONTENT": "The PDF contains active content. Remove it and upload a static document.",
    "LICENSE_TEXT_INVALID_UTF8": "The licence text must use UTF-8. Convert it and upload again.",
    "LICENSE_TEXT_NOT_NFC": "The licence text must use normalized Unicode. Normalize it and upload again.",
    "LICENSE_PROHIBITED_TERMS": "The licence conflicts with marketplace rules. Revise the terms and upload again.",
    "LICENSE_UPLOAD_UNAVAILABLE": "The custom licence could not be stored. Try again later.",
    "GATEWAY_LICENSE_UPGRADE_REQUIRED": "Update the publishing client to support the current licence choice.",
    "LICENSE_ACCEPTANCE_REQUIRED": "Review and accept the current licence before continuing.",
    "LICENSE_TERMINATED": "This licence has ended. Choose a current listing or contact the seller.",
    "dataset_version_content_mismatch": "The dataset content changed after this version was prepared. Reprocess the current dataset version and publish again.",
    "stale_source_revision": "The listing changed. Refresh At a glance, approve the current version, and try again.",
    "stale_summary": "At a glance is no longer current. Review and approve the current summary, then try again.",
    "stale_expected_head": "The verified preview changed on ai.market. Refresh its live state and try again.",
    "approval_binding_invalid": "The preview approval does not match this request. Refresh At a glance and prepare the preview again.",
    "candidate_binding_mismatch": "The marketplace candidate expired or changed. Refresh and prepare the signed preview again.",
    "commitment_binding_mismatch": "The commitment does not match the current preview. Rebuild and submit it again.",
    "commitment_not_current": "A newer dataset commitment is current. Rebuild the preview from the current dataset.",
    "summary_binding_mismatch": "At a glance no longer matches this preview. Review the current summary and prepare the preview again.",
    "approval_expired": "The preview approval expired. Refresh the attestation and submit again.",
    "idempotency_conflict": "At a glance already used this request identifier for different data. Refresh At a glance and retry.",
    "request_id_conflict": "This request identifier was already used for different preview data. Refresh and try again.",
    "registration_evidence_stale": "This install registration is out of date. Sign in and register this install again.",
    "verified_sample_unavailable_above_25_column_cap": "Verified previews support at most 25 fields. Select a dataset version with 25 or fewer fields.",
    "dictionary_must_match_committed_dataset_schema_republish_through_aim_data": "The data dictionary does not match the committed dataset schema. Republish the dataset through AIM Data.",
    "decision_mismatch": "The marketplace received the wrong preview action. Retry the requested action.",
    "disclosure_signature_invalid": "The preview signature was rejected. Register this install key again and retry.",
    "commitment_signature_invalid": "The dataset commitment signature was rejected. Rebuild and submit the preview again.",
    "proof_signature_invalid": "A selected-row proof signature was rejected. Rebuild and submit the preview again.",
    "signer_authority_invalid": "This AIM Data install is no longer active. Sign in and register this install again.",
    "signer_evidence_invalid": "The install registration evidence was rejected. Sign in and register this install again.",
    "signer_evidence_missing": "The install registration evidence is missing. Sign in and register this install again.",
    "signer_evidence_expired": "The install registration evidence expired. Sign in and register this install again.",
    "signer_fingerprint_mismatch": "The registered install key does not match this AIM Data key. Sign in and register this install again.",
    "registration_key_mismatch": "The registered install key does not match this request. Sign in and register this install again.",
    "registration_owner_mismatch": "This install is registered to a different seller. Sign in with the listing owner and register it again.",
    "registration_inactive": "This install registration is inactive. Sign in and register this install again.",
    "envelope_binding_mismatch": "The signed preview envelope does not match this listing. Rebuild the preview and try again.",
    "contract_mismatch": "The signed preview contract is not current. Update AIM Data and rebuild the preview.",
    "aggregate_hash_mismatch": "The preview summary hash does not match the signed request. Refresh At a glance and rebuild the preview.",
    "invalid_evidence_policy": "The preview evidence policy is invalid. Update AIM Data and rebuild the preview.",
    "invalid_public_key": "The install public key is invalid. Sign in and register this install again.",
    "invalid_preview_metadata": "The preview request is invalid. Update AIM Data, rebuild the preview, and try again.",
    "metadata_bound_exceeded": "The preview metadata is too large. Reduce the selected metadata and rebuild the preview.",
    "approval_binding_mismatch": "The listing metadata changed while the preview was being approved. Refresh and try again.",
    "summary_schema_mismatch": "The selected fields no longer match At a glance. Update At a glance or rebuild the preview.",
    "preview_contract_invalid": "The marketplace rejected the preview contract. Update AIM Data and rebuild the preview.",
    "summary_approval_incomplete": "At a glance was not approved. Review the current summary and retry.",
}

# Upstream marketplace authorization is a separate credential boundary from the
# caller's AIM Data authentication. Reserve 409 locally so the frontend never
# interprets an ai.market 401/403 as a reason to clear the AIM Data session.
MARKETPLACE_AUTH_LOCAL_STATUS = 409
MARKETPLACE_AUTH_UPSTREAM_STATUSES = frozenset({401, 403})


@dataclass(frozen=True)
class PreviewTransportError(ValueError):
    code: str
    message: str
    status: int = 409
    correlation_id: str | None = None

    def __str__(self) -> str:
        return self.code


class PreviewMarketplaceTransport:
    """Call current backend routes without ever carrying seller row data."""

    def __init__(self, *, base_url: str | None = None, token: str | None = None, client=None):
        state = get_serial_store().state
        self.base_url = (base_url or settings.ai_market_url).rstrip("/")
        self.token = token or state.ai_market_access_token
        self.client = client

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
                 *, missing_ok: bool = False) -> dict[str, Any] | None:
        if not self.token:
            raise PreviewTransportError(
                "seller_session_required",
                "Sign in to ai.market in AIM Data, then try again.",
                MARKETPLACE_AUTH_LOCAL_STATUS,
            )
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            if self.client is not None:
                response = self.client.request(method, self.base_url + path, json=body, headers=headers)
            else:
                with httpx.Client(timeout=30.0) as client:
                    response = client.request(method, self.base_url + path, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise PreviewTransportError(
                "marketplace_timeout", "ai.market timed out. Retry this preview action.", 504
            ) from exc
        except httpx.RequestError as exc:
            raise PreviewTransportError(
                "marketplace_unreachable",
                "AIM Data could not reach ai.market. Check the connection and retry.",
                502,
            ) from exc
        if missing_ok and response.status_code == 404:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            try:
                error_body = response.json()
            except (ValueError, AttributeError):
                error_body = {}
            if not isinstance(error_body, dict):
                error_body = {}
            detail = error_body.get("detail")
            body_code = error_body.get("code")
            correlation_id = error_body.get("correlation_id")
            if not isinstance(correlation_id, str) or not correlation_id:
                correlation_id = None
            code = (
                body_code
                if isinstance(body_code, str) and body_code
                else detail
                if isinstance(detail, str) and detail
                else f"marketplace_http_{response.status_code}"
            )
            if response.status_code == 401:
                code = "seller_session_expired"
                message = "Your ai.market session expired. Sign in again, then retry."
            elif response.status_code == 403:
                code = "listing_not_owned"
                message = "This seller account does not own the listing. Sign in with the listing owner."
            elif response.status_code == 404:
                code = "listing_not_found"
                message = "The ai.market listing no longer exists. Publish the dataset again, then prepare a new preview."
            elif response.status_code == 422 and code.startswith("marketplace_http_"):
                code = "preview_contract_invalid"
                message = REFUSAL_MESSAGES[code]
            elif response.status_code == 503 and code == "unexpected_error":
                reference = correlation_id or "unavailable"
                message = f"ai.market could not complete this step (reference {reference}); try again later"
            else:
                message = REFUSAL_MESSAGES.get(
                    code, f"ai.market refused the preview ({code}). Refresh the listing and retry."
                )
            if correlation_id and not (
                response.status_code == 503 and code == "unexpected_error"
            ):
                message = f"{message} Reference: {correlation_id}."
            local_status = (
                MARKETPLACE_AUTH_LOCAL_STATUS
                if response.status_code in MARKETPLACE_AUTH_UPSTREAM_STATUSES
                else response.status_code
            )
            raise PreviewTransportError(code, message, local_status, correlation_id)
        try:
            data = response.json()
        except ValueError as exc:
            raise PreviewTransportError(
                "marketplace_response_invalid",
                "ai.market returned an invalid response. Retry this preview action.",
                502,
            ) from exc
        if not isinstance(data, dict):
            raise PreviewTransportError(
                "marketplace_response_invalid",
                "ai.market returned an invalid response. Retry this preview action.",
                502,
            )
        return data

    def ensure_summary(self, listing_id: str) -> dict[str, Any]:
        path = f"/api/v1/listings/{listing_id}/at-a-glance"
        summary = self.preview_summary(listing_id)
        assert summary is not None
        if summary.get("state") != "approved" and summary.get("status") != "approved":
            request = {
                "summary_id": summary["summary_id"],
                "source_revision": summary["source_revision"],
                "summary_hash": summary["summary_hash"],
                "render_hash": summary["render_hash"],
                "request_id": str(uuid4()),
                "sample_decision": "none",
            }
            self._request("POST", path + "/approve", request)
            summary = self._request("GET", path + "/preview")
            assert summary is not None
            if summary.get("state") != "approved" and summary.get("status") != "approved":
                raise PreviewTransportError(
                    "summary_approval_incomplete",
                    "At a glance was not approved. Review it and retry the verified preview.",
                )
        return summary

    def preview_summary(self, listing_id: str) -> dict[str, Any]:
        result = self._request("GET", f"/api/v1/listings/{listing_id}/at-a-glance/preview")
        assert result is not None
        return result

    def allocate(self, listing_id: str, binding: dict[str, Any]) -> dict[str, Any]:
        result = self._request(
            "POST", f"/api/v1/listings/{listing_id}/at-a-glance/preview", {"binding": binding}
        )
        if not isinstance(result, dict) or not isinstance(result.get("binding"), dict):
            raise PreviewTransportError(
                "marketplace_response_invalid",
                "ai.market did not return a preview candidate. Retry this preview action.",
                502,
            )
        return result["binding"]

    def submit(self, listing_id: str, request: dict[str, Any], *, withdraw: bool = False) -> dict[str, Any]:
        action = "withdraw" if withdraw else "approve"
        result = self._request("POST", f"/api/v1/listings/{listing_id}/at-a-glance/{action}", request)
        assert result is not None
        return result

    def live_state(self, listing_id: str) -> dict[str, Any]:
        listing = self._request("GET", f"/api/v1/listings/{listing_id}")
        assert listing is not None
        slug = listing.get("slug")
        if not isinstance(slug, str) or not slug:
            raise PreviewTransportError(
                "listing_link_unavailable",
                "ai.market did not return the listing link. Refresh the listing and retry.",
                502,
            )
        manifest_path = f"/api/v1/public/listings/{slug}/preview-manifest"
        manifest = self._request("GET", manifest_path, missing_ok=True)
        return {
            "state": "visible" if manifest else "pending",
            "listing_url": f"https://ai.market/listings/{slug}",
            "manifest_url": self.base_url + manifest_path,
            "disclosure_version": manifest.get("disclosure_version") if manifest else None,
        }
