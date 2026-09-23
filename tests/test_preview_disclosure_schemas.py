import copy
import pytest
from pydantic import ValidationError
from app.models.preview_disclosure_schemas import DisclosureBinding, PreviewDisclosureRequest
from app.services.dataset_canonicalization import CanonicalSchema
from app.services.dataset_merkle_service import encode_base64url
from app.models.dataset_commitment_schemas import DatasetPreviewProofContract, DatasetCommitmentContract
from app.models.preview_disclosure_schemas import PlatformEnvelope
from app.services.preview_signing_service import request_bytes, SigningError
from tests.preview_fixture_factory import request_fixture


def test_closed_request_roundtrip():
    r = request_fixture()
    assert PreviewDisclosureRequest(**r).model_dump(mode="json") == r
    assert b'"listing_version_id":null' in request_bytes(r)


@pytest.mark.parametrize(
    "key",
    [
        "raw_secret",
        "rows",
        "rights_prose",
        "scan_notes",
        "unknown",
        "freshness_expires_at",
        "signer_keys",
    ],
)
@pytest.mark.parametrize("location", ["request", "binding", "commitment", "proof"])
def test_reject_carriers(key, location):
    r = request_fixture()
    target = {
        "request": r,
        "binding": r["binding"],
        "commitment": r["commitment"],
        "proof": r["proofs"][0],
    }[location]
    target[key] = "ZERO_INGRESS_SYNTHETIC_MARKER"
    with pytest.raises(SigningError) as e:
        request_bytes(r)
    assert str(e.value) == "contract_mismatch"


@pytest.mark.parametrize(
    "field,value",
    [
        ("rights_basis_code", "I own all the rights"),
        ("content_revision", "x" * 256),
        ("update_cadence_days", 0),
        ("public_preview_permission", False),
    ],
)
def test_invalid_binding(field, value):
    r = request_fixture()
    r["binding"][field] = value
    with pytest.raises(SigningError):
        request_bytes(r)


def test_mismatched_proof_order():
    r = copy.deepcopy(request_fixture())
    r["proofs"] = list(reversed(r["proofs"]))
    with pytest.raises(SigningError):
        request_bytes(r)


def _error(value, model=PreviewDisclosureRequest):
    with pytest.raises(ValidationError) as caught:
        model.model_validate(value)
    return [(item["type"], str(item.get("ctx", {}).get("error"))) for item in caught.value.errors()]


@pytest.mark.parametrize("member", [
    "preview_package_url", "package_media_type", "package_profile",
    "package_byte_ceiling", "scan_policy", "scan_policy_version",
    "scanned_at", "scan_verdict", "signer_reference",
])
def test_raw_package_mismatch_precedes_nested_validation(member):
    r = copy.deepcopy(request_fixture())
    r["proofs"][1][member] = "invalid-synthetic-value"
    assert _error(r) == [("value_error", "package_mismatch")]


def test_sampled_leaf_mismatch_has_exact_code():
    r = copy.deepcopy(request_fixture())
    wrong = "A" * 43
    r["proofs"][0]["sampled_leaf_list_digest"] = wrong
    r["commitment"]["proofs"][0]["sampled_leaf_list_digest"] = wrong
    assert _error(r) == [("value_error", "sampled_leaf_list_mismatch")]


@pytest.mark.parametrize("descriptor", [
    ["data", "binary", False, {}],
    ["data", "array", False, {"element_type": {"type": "binary", "type_parameters": {}}}],
    ["data", "object", False, {"object_fields": [{"name": "inner", "type": "binary", "nullable": False, "type_parameters": {}}]}],
    ["data", "object", False, {"object_fields": [{"name": "inner", "type": "array", "nullable": False, "type_parameters": {"element_type": {"type": "binary", "type_parameters": {}}}}]}],
])
def test_recursive_binary_is_forbidden(descriptor):
    b = copy.deepcopy(request_fixture()["binding"])
    b["schema_descriptors"] = [descriptor]
    b["schema_digest"] = encode_base64url(CanonicalSchema([descriptor]).digest)
    b["selected_fields"] = ["data"]
    assert _error(b, DisclosureBinding) == [("value_error", "binary_sample_forbidden")]


def test_recursive_public_schema_is_accepted():
    descriptor = ["data", "object", False, {"object_fields": [{"name": "inner", "type": "array", "nullable": False, "type_parameters": {"element_type": {"type": "string", "type_parameters": {}}}}]}]
    b = copy.deepcopy(request_fixture()["binding"])
    b["schema_descriptors"] = [descriptor]
    b["schema_digest"] = encode_base64url(CanonicalSchema([descriptor]).digest)
    b["selected_fields"] = ["data"]
    assert DisclosureBinding.model_validate(b).selected_fields == ["data"]


@pytest.mark.parametrize("model,source,path", [
    (PreviewDisclosureRequest, "request", ("profile",)),
    *[(DisclosureBinding, "binding", (field,)) for field in (
        "profile", "decision", "preview_type", "content_type", "sample_decision",
        "aggregate_hash_profile", "rights_basis_code", "signature_algorithm", "signature_profile",
    )],
    *[(DatasetPreviewProofContract, "proof", (field,)) for field in (
        "package_media_type", "package_profile", "scan_policy", "scan_verdict", "signature_algorithm",
    )],
    (DatasetPreviewProofContract, "proof", ("siblings", 0, "direction")),
    *[(DatasetCommitmentContract, "commitment", (field,)) for field in (
        "canonicalization_profile", "hash_algorithm", "signature_algorithm",
    )],
    *[(PlatformEnvelope, "envelope", (field,)) for field in ("profile", "signature_algorithm")],
    *[(PlatformEnvelope, "envelope", ("signer_keys", 0, field)) for field in ("algorithm", "status")],
])
def test_literal_errors_are_exact(model, source, path):
    from tests.preview_fixture_factory import platform_material

    request = request_fixture()
    value = {
        "request": request,
        "binding": request["binding"],
        "proof": request["proofs"][0],
        "commitment": request["commitment"],
        "envelope": platform_material(request)[1],
    }[source]
    target = value
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = "invalid-synthetic-value"
    assert _error(value, model)[0][0] == "literal_error"


def test_signing_normalizes_uuid_time_and_preserves_nulls():
    from app.models.dataset_commitment_schemas import TransparencyCheckpointContract
    from app.services.preview_signing_service import serialize
    from tests.preview_fixture_factory import platform_material

    _, _, cp, _ = platform_material()
    cp["checkpoint_at"] = "2026-09-17T00:00:00Z"
    assert b"2026-09-17T00:00:00.000000Z" in serialize(
        TransparencyCheckpointContract, cp
    )
    r = request_fixture()
    r["binding"]["listing_id"] = r["binding"]["listing_id"].upper()
    assert b'"listing_version_id":null' in request_bytes(r)
    r["binding"]["update_cadence_days"] = 2**53
    with pytest.raises(SigningError):
        request_bytes(r)


def test_attestation_is_closed_metadata_and_requires_confirmations():
    from app.services.preview_signing_service import seller_attestation_digest

    r = request_fixture()
    c = r["commitment"]
    b = r["binding"]
    a = {
        k: c[k]
        for k in (
            "listing_id",
            "seller_dataset_version",
            "schema_digest",
            "dataset_merkle_root",
            "leaf_count",
            "signed_at",
        )
    }
    a.update(
        sample_hash=b["sample_hash"],
        rights_basis_digest=b["rights_basis_digest"],
        public_preview_permission=True,
        metadata_accuracy_confirmed=True,
    )
    assert seller_attestation_digest(a) == c["seller_attestation_digest"]
    a["metadata_accuracy_confirmed"] = False
    with pytest.raises(SigningError):
        seller_attestation_digest(a)
