import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError
from types import SimpleNamespace

from app.routers import marketplace_publish
from app.core.errors.middleware import commitment_request_validation_error_handler
from app.models.listing_metadata_schemas import (
    DatasetCommitmentSubmission,
    SIGNED_AT_MAX_CLOCK_SKEW_SECONDS,
)
from app.services.dataset_canonicalization import canonicalize_row, compute_schema_digest, encode_base64url
from app.services.dataset_merkle_service import DatasetMerkleService
from app.services.marketplace_push_service import (
    CommitmentClientValidationError,
    MarketplacePushService,
    build_signed_commitment_submission,
    validate_commitment_submission,
)
from app.services.preview_content_policy import PreviewContentPolicy, PreviewRightsBasis
from app.services.preview_package_service import PreviewPackageService


SCHEMA = [
    {"name": "id", "type": "signed_integer", "nullable": False, "type_parameters": {}},
    {"name": "label", "type": "string", "nullable": False, "type_parameters": {}},
]


def _signed(*, at: datetime, version="v1", rows=None, commitment_id=None, key=None, proof_ids=None, listing_id=None):
    key = key or Ed25519PrivateKey.generate()
    commitment_id = commitment_id or uuid4()
    rows = rows or [{"id": 1, "label": "safe aggregate"}, {"id": 2, "label": "safe category"}]
    digest = compute_schema_digest(SCHEMA)
    records = [canonicalize_row(SCHEMA, row, schema_digest=digest) for row in rows]
    commitment = DatasetMerkleService().build(records, schema_digest=digest)
    package = PreviewPackageService().build(
        commitment,
        commitment_id=commitment_id,
        selected_leaf_indices=[0],
        selected_base_row_digests=[encode_base64url(commitment.leaves[0].record.base_row_digest)],
        proof_ids=proof_ids,
    )
    policy = PreviewContentPolicy(require_presidio=False).scan_rows(
        [package.body["entries"][0]["row"]],
        rights_basis=PreviewRightsBasis("seller owned", True, "seller_owned"),
        now=at,
    )
    payload = build_signed_commitment_submission(
        commitment=commitment,
        schema=SCHEMA,
        package=package,
        policy_result=policy,
        listing_id=listing_id or uuid4(),
        commitment_id=commitment_id,
        seller_dataset_version=version,
        preview_package_url="https://seller.example/preview.json",
        signer_reference="install-1",
        private_key=key,
        signed_at=at,
        now=at,
    )
    return payload, key, package


@pytest.mark.asyncio
async def test_exact_outbound_body_is_non_custodial_and_backend_acceptance_is_separate():
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    raw_customer_sentinel = "RAW-CUSTOMER-ROW-7c13f9"
    payload, key, package = _signed(
        at=now,
        rows=[{"id": 1, "label": raw_customer_sentinel}, {"id": 2, "label": "safe category"}],
    )
    canonical_row_bytes = next(
        leaf.record.canonical_row_bytes
        for leaf in DatasetMerkleService().build(
            [
                canonicalize_row(SCHEMA, {"id": 1, "label": raw_customer_sentinel}),
                canonicalize_row(SCHEMA, {"id": 2, "label": "safe category"}),
            ],
            schema_digest=compute_schema_digest(SCHEMA),
        ).leaves
        if raw_customer_sentinel.encode() in leaf.record.canonical_row_bytes
    )
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = await request.aread()
        captured["headers"] = dict(request.headers)
        return httpx.Response(201, json={"transparency_sequence": 7, "checkpoint_size": 8, "response_body": "ignored"})

    service = MarketplacePushService()
    service.base_url = "https://ai.market.test"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await service.push_dataset_commitment(
            payload,
            schema=SCHEMA,
            public_key=key.public_key(),
            expected_signer_reference="install-1",
            auth_headers={
                "Authorization": "Bearer marketplace-token",
                "X-Source-Path": "/private/customer/data.csv",
                "X-Source-Credentials": "source-secret-4d6a2f",
                "X-Package-Response-Body": "package-body-c1e8a7",
            },
            now=now,
            client=client,
        )
    wire = json.loads(captured["body"])
    wire_text = captured["body"].decode()
    assert wire == payload.model_dump(mode="json")
    assert raw_customer_sentinel not in wire_text
    assert canonical_row_bytes not in captured["body"]
    assert package.encoded not in captured["body"]
    assert "/private/customer/data.csv" not in wire_text
    assert "source-secret-4d6a2f" not in wire_text
    assert "package-body-c1e8a7" not in wire_text
    assert "row" not in wire and "rows" not in wire
    assert "x-source-path" not in captured["headers"]
    assert "x-source-credentials" not in captured["headers"]
    assert "x-package-response-body" not in captured["headers"]
    assert result == {
        "status": "accepted",
        "commitment_id": str(payload.commitment_id),
        "local_validation": "passed",
        "backend_accepted": True,
        "transparency_sequence": 7,
        "checkpoint_size": 8,
    }
    assert "response_body" not in result


@pytest.mark.asyncio
async def test_invalid_local_payload_never_reaches_backend_even_if_backend_would_accept():
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    payload, key, _package = _signed(at=now)
    payload.proofs[0].siblings[0].direction = (
        "left" if payload.proofs[0].siblings[0].direction == "right" else "right"
    )
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, json={})

    service = MarketplacePushService()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CommitmentClientValidationError) as exc:
            await service.push_dataset_commitment(
                payload,
                schema=SCHEMA,
                public_key=key.public_key(),
                expected_signer_reference="install-1",
                auth_headers={},
                now=now,
                client=client,
            )
    assert exc.value.code == "invalid_inclusion_proof"
    assert called is False


def test_skew_stale_schema_signature_and_scan_failures_have_distinct_codes():
    signed_at = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    payload, key, _package = _signed(at=signed_at)

    with pytest.raises(CommitmentClientValidationError) as future:
        validate_commitment_submission(
            payload,
            schema=SCHEMA,
            public_key=key.public_key(),
            expected_listing_id=payload.listing_id,
            expected_signer_reference="install-1",
            now=signed_at - timedelta(minutes=6),
        )
    assert future.value.code == "attestation_timestamp_in_future"

    with pytest.raises(CommitmentClientValidationError) as stale:
        validate_commitment_submission(
            payload,
            schema=SCHEMA,
            public_key=key.public_key(),
            expected_listing_id=payload.listing_id,
            expected_signer_reference="install-1",
            now=signed_at + timedelta(days=91),
        )
    assert stale.value.code == "attestation_stale"

    wrong_schema = [{"name": "other", "type": "string", "nullable": False, "type_parameters": {}}]
    with pytest.raises(CommitmentClientValidationError) as schema_error:
        validate_commitment_submission(
            payload,
            schema=wrong_schema,
            public_key=key.public_key(),
            expected_listing_id=payload.listing_id,
            expected_signer_reference="install-1",
            now=signed_at,
        )
    assert schema_error.value.code == "schema_digest_mismatch"

    payload.proofs[0].sampled_leaf_list_digest = "A" * 43
    with pytest.raises(CommitmentClientValidationError) as scan:
        validate_commitment_submission(
            payload,
            schema=SCHEMA,
            public_key=key.public_key(),
            expected_listing_id=payload.listing_id,
            expected_signer_reference="install-1",
            now=signed_at,
        )
    assert scan.value.code == "scan_leaf_list_mismatch"


def test_clock_skew_bound_is_explicit_inclusive_and_stable():
    signed_at = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    payload, key, _package = _signed(at=signed_at)
    validate_commitment_submission(
        payload,
        schema=SCHEMA,
        public_key=key.public_key(),
        expected_listing_id=payload.listing_id,
        expected_signer_reference="install-1",
        now=signed_at - timedelta(minutes=5),
    )
    with pytest.raises(CommitmentClientValidationError) as exc:
        validate_commitment_submission(
            payload,
            schema=SCHEMA,
            public_key=key.public_key(),
            expected_listing_id=payload.listing_id,
            expected_signer_reference="install-1",
            now=signed_at - timedelta(minutes=5, microseconds=1),
        )
    assert exc.value.code == "attestation_timestamp_in_future"
    assert SIGNED_AT_MAX_CLOCK_SKEW_SECONDS == 300
    assert "300 seconds" in DatasetCommitmentSubmission.model_json_schema()["properties"]["signed_at"]["description"]


def test_pre_sign_validation_fails_before_any_signature(monkeypatch):
    signed_at = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    commitment_id = uuid4()
    digest = compute_schema_digest(SCHEMA)
    commitment = DatasetMerkleService().build(
        [canonicalize_row(SCHEMA, {"id": 1, "label": "safe"}, schema_digest=digest)],
        schema_digest=digest,
    )
    package = PreviewPackageService().build(
        commitment,
        commitment_id=commitment_id,
        selected_leaf_indices=[0],
        selected_base_row_digests=[encode_base64url(commitment.leaves[0].record.base_row_digest)],
    )
    policy = PreviewContentPolicy(require_presidio=False).scan_rows(
        [package.body["entries"][0]["row"]],
        rights_basis=PreviewRightsBasis("seller owned", True, "seller_owned"),
        now=signed_at,
    )
    signed = False

    def forbidden_sign(*_args, **_kwargs):
        nonlocal signed
        signed = True
        raise AssertionError("signing must not start")

    monkeypatch.setattr("app.services.marketplace_push_service._sign", forbidden_sign)
    with pytest.raises(CommitmentClientValidationError) as exc:
        build_signed_commitment_submission(
            commitment=commitment,
            schema=SCHEMA,
            package=package,
            policy_result=policy,
            listing_id=uuid4(),
            commitment_id=commitment_id,
            seller_dataset_version="v1",
            preview_package_url="https://seller.example/preview.json",
            signer_reference="install-1",
            private_key=Ed25519PrivateKey.generate(),
            signed_at=signed_at + timedelta(minutes=5, microseconds=1),
            now=signed_at,
        )
    assert exc.value.code == "attestation_timestamp_in_future"
    assert signed is False


def test_changed_dataset_version_cannot_reuse_root_payload_or_proof_ids():
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    previous, key, _ = _signed(at=now, version="v1")
    current, _, _ = _signed(at=now, version="v2", key=key, listing_id=previous.listing_id)
    with pytest.raises(CommitmentClientValidationError) as exc:
        validate_commitment_submission(
            current,
            schema=SCHEMA,
            public_key=key.public_key(),
            expected_listing_id=previous.listing_id,
            expected_signer_reference="install-1",
            now=now,
            previous_submission=previous,
        )
    assert exc.value.code == "dataset_version_reuse"


@pytest.mark.parametrize("code", ["schema_digest_mismatch", "dataset_version_reuse"])
def test_conflict_commitment_codes_map_to_409_with_stable_code(code):
    exc = marketplace_publish._commitment_http_exception(code)
    assert exc.status_code == 409
    assert exc.detail == code


@pytest.mark.asyncio
async def test_raw_content_validation_error_preserves_stable_422_envelope():
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    payload, _key, _package = _signed(at=now)
    wire = payload.model_dump(mode="json")
    wire["raw_content"] = "must-not-survive-validation"
    try:
        marketplace_publish.LocalCommitmentSubmitRequest.model_validate(
            {"dataset_id": "ds-1", "schema": SCHEMA, "commitment": wire}
        )
    except ValidationError as exc:
        validation_error = RequestValidationError(exc.errors())
    else:
        raise AssertionError("raw commitment content must fail validation")
    request = Request({"type": "http", "method": "POST", "path": "/api/marketplace/listings/x/commitments"})
    response = await commitment_request_validation_error_handler(request, validation_error)
    assert response.status_code == 422
    assert json.loads(response.body) == {"detail": "raw_content_forbidden"}


def test_opaque_dataset_version_cannot_carry_a_source_path():
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="invalid_seller_dataset_version"):
        _signed(at=now, version="/private/customer/data.csv")


@pytest.mark.asyncio
async def test_local_build_route_reads_full_dataset_and_returns_hostable_package(monkeypatch, tmp_path):
    source = tmp_path / "customer.csv"
    source.write_text("id,label\n1,first safe row\n2,second safe row\n", encoding="utf-8")
    listing_id = uuid4()
    record = SimpleNamespace(
        processed_path=None,
        upload_path=source,
        listing_id=str(listing_id),
        metadata={},
    )
    processing = SimpleNamespace(get_dataset=lambda dataset_id: record if dataset_id == "ds-1" else None)
    key = Ed25519PrivateKey.generate()
    crypto = SimpleNamespace(get_or_create_keypairs=lambda: (key, key.public_key(), None, None))

    async def signer(*_args, **_kwargs):
        return "install-1"

    monkeypatch.setattr(marketplace_publish, "_get_crypto", lambda: crypto)
    monkeypatch.setattr(marketplace_publish, "_ensure_commitment_signer", signer)
    monkeypatch.setattr(
        marketplace_publish,
        "PreviewContentPolicy",
        lambda: PreviewContentPolicy(require_presidio=False),
    )
    original_iter_commitment_records = marketplace_publish.DuckDBService.iter_commitment_records

    def iter_commitment_records_with_parsed_schema(service, filepath, **kwargs):
        assert all(isinstance(field, marketplace_publish.LogicalField) for field in kwargs["schema"])
        return original_iter_commitment_records(service, filepath, **kwargs)

    monkeypatch.setattr(
        marketplace_publish.DuckDBService,
        "iter_commitment_records",
        iter_commitment_records_with_parsed_schema,
    )
    body = marketplace_publish.LocalCommitmentBuildRequest.model_validate(
        {
            "dataset_id": "ds-1",
            "schema": SCHEMA,
            "seller_dataset_version": "v1",
            "selected_source_row_indices": [0],
            "selected_base_row_digests": [
                encode_base64url(canonicalize_row(SCHEMA, {"id": "1", "label": "first safe row"}).base_row_digest)
            ],
            "preview_package_url": "https://seller.example/preview.json",
            "rights_basis": {
                "basis": "seller owned",
                "public_preview_permitted": True,
                "copyright_status": "seller_owned",
            },
            "csv_options": {
                "encoding": "utf-8",
                "delimiter": ",",
                "quotechar": "\"",
                "header": True,
                "locale": "C",
                "null_token": "NULL",
            },
        }
    )
    result = await marketplace_publish.build_dataset_commitment(
        str(listing_id),
        body,
        SimpleNamespace(headers={}),
        user=SimpleNamespace(user_id="seller-1", key_id="ai_market_bearer"),
        processing=processing,
    )
    assert result["status"] == "built_locally"
    assert result["leaf_count"] == 2
    assert result["preview_package"]["entries"][0]["row"]["label"] == "first safe row"
    wire_text = json.dumps(result["commitment"])
    assert "first safe row" not in wire_text
    assert "second safe row" not in wire_text
    assert str(source) not in wire_text


@pytest.mark.asyncio
async def test_local_build_route_rejects_ambiguous_csv_null_token_before_parsing(monkeypatch, tmp_path):
    source = tmp_path / "customer.csv"
    source.write_text('id,label\n1,""\n', encoding="utf-8")
    listing_id = uuid4()
    record = SimpleNamespace(
        processed_path=None,
        upload_path=source,
        listing_id=str(listing_id),
        metadata={},
    )
    processing = SimpleNamespace(get_dataset=lambda dataset_id: record if dataset_id == "ds-1" else None)
    body = marketplace_publish.LocalCommitmentBuildRequest.model_validate(
        {
            "dataset_id": "ds-1",
            "schema": SCHEMA,
            "seller_dataset_version": "v1",
            "selected_source_row_indices": [0],
            "selected_base_row_digests": [encode_base64url(b"x" * 32)],
            "preview_package_url": "https://seller.example/preview.json",
            "rights_basis": {
                "basis": "seller owned",
                "public_preview_permitted": True,
                "copyright_status": "seller_owned",
            },
            "csv_options": {
                "encoding": "utf-8",
                "delimiter": ",",
                "quotechar": "\"",
                "header": True,
                "locale": "C",
                "null_token": "",
            },
        }
    )

    def parsing_must_not_start(*_args, **_kwargs):
        raise AssertionError("invalid CSV options must fail at the route boundary")

    monkeypatch.setattr(
        marketplace_publish.DuckDBService,
        "iter_commitment_records",
        parsing_must_not_start,
    )

    with pytest.raises(HTTPException) as exc:
        await marketplace_publish.build_dataset_commitment(
            str(listing_id),
            body,
            SimpleNamespace(headers={}),
            user=SimpleNamespace(user_id="seller-1", key_id="ai_market_bearer"),
            processing=processing,
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == "ambiguous_csv_null_token"


@pytest.mark.asyncio
async def test_commitment_sample_capture_binds_reviewed_content_to_base_digest(tmp_path):
    source = tmp_path / "customer.csv"
    source.write_text("id,label\n1,first safe row\n2,second safe row\n", encoding="utf-8")
    record = SimpleNamespace(processed_path=None, upload_path=source, metadata={})
    processing = SimpleNamespace(get_dataset=lambda dataset_id: record if dataset_id == "ds-1" else None)
    body = marketplace_publish.LocalCommitmentSampleRequest.model_validate(
        {
            "schema": SCHEMA,
            "rows": [
                {"id": 1, "label": "first safe row"},
                {"id": 2, "label": "second safe row"},
            ],
        }
    )
    result = await marketplace_publish.capture_commitment_sample(
        "ds-1",
        body,
        processing=processing,
    )
    assert [sample["source_row_index"] for sample in result["samples"]] == [0, 1]
    assert all("row" not in sample for sample in result["samples"])
    expected = canonicalize_row(SCHEMA, {"id": 1, "label": "first safe row"})
    assert result["samples"][0]["base_row_digest"] == encode_base64url(expected.base_row_digest)


@pytest.mark.asyncio
async def test_local_build_rejects_sample_digest_mismatch_before_proof_assembly(tmp_path):
    source = tmp_path / "customer.csv"
    source.write_text("id,label\n1,first safe row\n2,second safe row\n", encoding="utf-8")
    listing_id = uuid4()
    record = SimpleNamespace(
        processed_path=None,
        upload_path=source,
        listing_id=str(listing_id),
        metadata={},
    )
    processing = SimpleNamespace(get_dataset=lambda dataset_id: record if dataset_id == "ds-1" else None)
    wrong_reviewed_row = canonicalize_row(SCHEMA, {"id": 2, "label": "second safe row"})
    body = marketplace_publish.LocalCommitmentBuildRequest.model_validate(
        {
            "dataset_id": "ds-1",
            "schema": SCHEMA,
            "seller_dataset_version": "v1",
            "selected_source_row_indices": [0],
            "selected_base_row_digests": [encode_base64url(wrong_reviewed_row.base_row_digest)],
            "preview_package_url": "https://seller.example/preview.json",
            "rights_basis": {
                "basis": "seller owned",
                "public_preview_permitted": True,
                "copyright_status": "seller_owned",
            },
            "csv_options": {
                "encoding": "utf-8",
                "delimiter": ",",
                "quotechar": "\"",
                "header": True,
                "locale": "C",
                "null_token": "NULL",
            },
        }
    )
    with pytest.raises(HTTPException) as exc:
        await marketplace_publish.build_dataset_commitment(
            str(listing_id),
            body,
            SimpleNamespace(headers={}),
            user=SimpleNamespace(user_id="seller-1", key_id="ai_market_bearer"),
            processing=processing,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "sample_row_digest_mismatch"
