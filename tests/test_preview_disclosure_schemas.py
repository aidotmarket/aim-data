import copy
import pytest
from app.models.preview_disclosure_schemas import PreviewDisclosureRequest
from app.services.preview_signing_service import request_bytes, SigningError
from tests.preview_fixture_factory import request_fixture


def test_closed_request_roundtrip():
    r = request_fixture()
    assert PreviewDisclosureRequest(**r).model_dump(mode="json") == r
    assert b'"listing_version_id":null' in request_bytes(r)


@pytest.mark.parametrize("key", ["raw_secret", "rows", "rights_prose", "scan_notes", "unknown", "freshness_expires_at", "signer_keys"])
@pytest.mark.parametrize("location", ["request", "binding", "commitment", "proof"])
def test_reject_carriers(key, location):
    r = request_fixture()
    target = {"request": r, "binding": r["binding"], "commitment": r["commitment"], "proof": r["proofs"][0]}[location]
    target[key] = "ZERO_INGRESS_SYNTHETIC_MARKER"
    with pytest.raises(SigningError) as e:
        request_bytes(r)
    assert str(e.value) == "contract_mismatch"


@pytest.mark.parametrize("field,value", [("rights_basis_code", "I own all the rights"), ("content_revision", "x"*256), ("update_cadence_days", 0), ("public_preview_permission", False)])
def test_invalid_binding(field, value):
    r = request_fixture(); r["binding"][field] = value
    with pytest.raises(SigningError):
        request_bytes(r)


def test_mismatched_proof_order():
    r = copy.deepcopy(request_fixture()); r["proofs"] = list(reversed(r["proofs"]))
    with pytest.raises(SigningError):
        request_bytes(r)
