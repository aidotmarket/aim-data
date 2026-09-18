import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.routers import marketplace_publish as route
from app.services.preview_signing_service import (
    SigningError,
    construct_request,
    LocalCandidate,
)
from tests.preview_fixture_factory import request_fixture, material, p1
from tests import test_preview_signing_service as signing_tests

signer = signing_tests.signer


def test_constructor_uses_loaded_registered_key(signer):
    s, _ = signer
    _, c, b = material()
    ref = s.signer_reference
    c["aim_data_signer_reference"] = ref
    b["signer_reference"] = ref
    for proof in c["proofs"]:
        proof["signer_reference"] = ref
        proof.update(s.sign_proof(c, proof))
    c = s.sign_commitment(c)
    from app.services.preview_content_policy import scan_attestation_digest

    b["scan_attestation_digest"] = scan_attestation_digest(c["proofs"])
    r = construct_request(
        LocalCandidate.validate(b), c, c["proofs"], signer=s, approved_p1=p1(b)
    )
    assert r["binding"] == b
    with pytest.raises(SigningError, match="p1_reference_mismatch"):
        construct_request(
            LocalCandidate.validate(b), c, c["proofs"], signer=s, approved_p1={}
        )


def test_router_forwards_closed_signed_request():
    import httpx
    from unittest.mock import AsyncMock

    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.dependency_overrides[route.get_current_user] = lambda: {"id": "fixture"}
    response = httpx.Response(
        200,
        json={"decision_id": "00000000-0000-4000-8000-000000000033", "decision": "approve"},
        request=httpx.Request("POST", "https://api.ai.market"),
    )
    with TestClient(app) as client, patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response) as post:
        r = request_fixture()
        url = (
            "/api/marketplace/listings/"
            + r["binding"]["listing_id"]
            + "/at-a-glance/approve"
        )
        result = client.post(url, json=r, headers={"Authorization": "Bearer seller-token"})
        assert result.status_code == 200
        assert result.json()["decision"] == "approve"
        assert post.await_args.kwargs["content"]
        assert post.await_args.kwargs["headers"]["Authorization"] == "Bearer seller-token"
        r["binding"]["raw_marker"] = "ZERO_INGRESS_SYNTHETIC_MARKER"
        rejected = client.post(url, json=r, headers={"Authorization": "Bearer seller-token"})
        assert rejected.status_code == 422
        assert "ZERO_INGRESS" not in rejected.text
        assert post.await_count == 1


def test_legacy_rows_not_in_model():
    assert "DisclosureApprovedSample" not in vars(route)
    with pytest.raises(ValueError):
        route.DisclosureSnapshotProxyRequest.model_validate(dict(
            dataset_id="fixture",
            approved_fields={},
            sample_decision="approved_rows",
            approved_sample={
                "columns": ["x"],
                "rows": [{"x": "ZERO_INGRESS_SYNTHETIC_MARKER"}],
                "row_refs": ["0"],
            },
            ai_training_notification_ack=True,
            ai_training_notification_text="fixture",
            license="fixture",
            approval_source="aim_channel",
            source_publish_operation_id="fixture",
        ))


@pytest.mark.asyncio
async def test_row_replay_is_refused_before_network_or_local_persistence():
    from types import SimpleNamespace
    from starlette.requests import Request
    from fastapi import HTTPException
    from unittest.mock import Mock

    marker = "unique synthetic cell marker " + __import__("uuid").uuid4().hex[:8]
    # Existing on-disk data can bypass a new Pydantic constructor; runtime guard
    # must also prevent replay. The historical local audit remains untouched.
    body = route.DisclosureSnapshotProxyRequest.model_construct(
        sample_decision="approved_rows", approved_sample={"rows": [{"value": marker}]}
    )
    processing = Mock()
    with patch("httpx.AsyncClient") as network:
        with pytest.raises(HTTPException) as exc:
            await route.create_disclosure_snapshot(
                "listing-1",
                body,
                Request({"type": "http", "headers": []}),
                user=SimpleNamespace(),
                processing=processing,
            )
        assert exc.value.detail == "legacy_sample_unavailable"
        network.assert_not_called()
        processing.assert_not_called()
        processing.get_dataset.assert_not_called()


@pytest.mark.asyncio
async def test_column_export_has_no_local_values(monkeypatch):
    import json
    import httpx
    from unittest.mock import AsyncMock, Mock
    from app.models.listing_metadata_schemas import ColumnSummary, ListingMetadata
    from app.services.marketplace_push_service import MarketplacePushService

    marker = "unique_synthetic_cell_marker_failure"
    column = ColumnSummary(name="field", type="string", sample_values=[marker])
    metadata = ListingMetadata(title="Synthetic", description="Synthetic", column_summary=[column],
                               freshness_score=0.0, privacy_score=None)
    service = MarketplacePushService()
    service.api_key = "synthetic-test-key"
    monkeypatch.setattr(service, "_load_compliance_report", lambda path: None)
    monkeypatch.setattr(service, "_load_attestation", lambda path: None)
    monkeypatch.setattr(service, "_save_publish_result", Mock())
    response = httpx.Response(201, json={"id": "synthetic-listing"},
                              request=httpx.Request("POST", "https://market.example"))
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response) as post:
        await service.push_to_marketplace("synthetic", listing_metadata_override=metadata)
    post.assert_awaited_once()
    payload = post.call_args.kwargs["json"]
    assert payload["schema_info"]["columns"] == [{
        "name": "field", "type": "string", "null_percentage": 0.0, "uniqueness_ratio": 0.0,
    }]
    assert "sample_values" not in json.dumps(payload)
    assert marker not in json.dumps(payload)
    assert column.sample_values == [marker]  # seller-local values preserved


def test_manifest_budget_counts_duplicated_signed_evidence(monkeypatch):
    from app.services.preview_signing_service import manifest_budget, request_bytes
    from app.services import preview_package_service

    r = request_fixture()
    assert len(request_bytes(r)) < manifest_budget(r) < 262144
    original = preview_package_service.expected_manifest_fixture

    def excessive(*args):
        import json

        fixture = json.loads(original(*args))
        fixture["schema_descriptors"] = [["x" * 255, "string", False, {}]] * 1000
        return json.dumps(fixture).encode()

    monkeypatch.setattr(preview_package_service, "expected_manifest_fixture", excessive)
    with pytest.raises(SigningError, match="manifest_limit"):
        manifest_budget(r)


def test_legacy_http_rejection_never_reflects_marker():
    marker = "unique_synthetic_cell_marker_failure"
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.dependency_overrides[route.get_current_user] = lambda: {"id": "fixture"}
    app.dependency_overrides[route.get_processing_service] = lambda: None
    with TestClient(app) as client, patch("httpx.AsyncClient") as network:
        response = client.post(
            "/api/marketplace/listings/listing-1/disclosure-snapshots",
            json={
                "sample_decision": "approved_rows",
                "approved_sample": {"rows": [{"x": marker}]},
            },
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "legacy_sample_unavailable"}
        assert marker not in response.text
        network.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_none_parser_preserves_existing_contract():
    import json
    from starlette.requests import Request
    from tests.test_vz_publish_proxy import _disclosure_body

    body = _disclosure_body()
    request = Request({"type": "http", "headers": []})
    request._body = json.dumps(body.model_dump()).encode()
    parsed = await route._closed_legacy_none(request)
    assert parsed == body
