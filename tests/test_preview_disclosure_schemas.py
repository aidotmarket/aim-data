import copy
import pytest
from app.models.preview_disclosure_schemas import PreviewDisclosureRequest
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
