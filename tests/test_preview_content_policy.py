import hashlib
import json
from pathlib import Path

import pytest

from app.services import preview_content_policy as policy
from app.services.dataset_merkle_service import canonical_json_bytes, encode_base64url


CONTENT = [
    "2026-06-28", "Lisbon", "seller@example.test", "https://example.test/path",
    "word " * 2000, "550e8400-e29b-41d4-a716-446655440000", "a3" * 32,
    "+351 912 345 678", "-123.45", "a\x00b\u202eb", "api_key=synthetic",
    "<script>alert(1)</script>",
]


def package_fixture():
    return json.loads(Path("tests/fixtures/aim_preview_package_v2.json").read_text())


@pytest.mark.parametrize("value", CONTENT)
def test_content_never_refuses_preview(value):
    proofs = package_fixture()["entries"]
    result = policy.scan_selection(
        [{"value": value}], [proofs[0]], detector=object(),
        scanned_at="2026-09-17T12:00:00Z", rights_confirmed=True,
        public_preview_permission=True, restricted_content_confirmed=True,
        language="unsupported",
    )
    assert (result["scan_policy"], result["scan_policy_version"]) == (
        "aim-preview-policy-v2", "2.0.0"
    )
    assert result["scan_verdict"] == "passed"


def test_missing_detector_packages_and_versions_are_irrelevant(monkeypatch):
    monkeypatch.setattr("importlib.metadata.version", lambda _: (_ for _ in ()).throw(RuntimeError()))
    assert policy.detector_identity() == {}
    policy.check_text("synthetic@example.test\x00")


@pytest.mark.parametrize("value", [b"synthetic", {"a": [b"synthetic"]}, float("nan")])
def test_unsupported_values_remain_technical_refusals(value):
    with pytest.raises(policy.PolicyError):
        list(policy.walk_selection([{"x": value}]))


def test_depth_and_node_caps_remain():
    value = "x"
    for _ in range(17):
        value = [value]
    with pytest.raises(policy.PolicyError, match="depth_limit"):
        list(policy.walk_selection([{"x": value}]))
    with pytest.raises(policy.PolicyError, match="nodes_limit"):
        list(policy.walk_selection([{"x": list(range(10001))}]))


def test_scan_metadata_and_sampled_leaf_binding():
    fixture = package_fixture()
    vectors = json.loads(Path("tests/fixtures/aim_preview_package_vectors_v1.json").read_text())
    proofs = fixture["entries"]
    result = policy.scan_selection(
        [p["row"] for p in proofs], proofs, scanned_at="2026-09-17T12:00:00Z",
        rights_confirmed=True, public_preview_permission=True,
        restricted_content_confirmed=True,
    )
    assert result == {
        "scan_policy": "aim-preview-policy-v2", "scan_policy_version": "2.0.0",
        "scan_verdict": "passed", "scanned_at": "2026-09-17T12:00:00.000000Z",
        "sampled_leaf_list_digest": vectors["sampled_leaf_list_digest"],
    }


@pytest.mark.parametrize(
    "missing", ["rights_confirmed", "public_preview_permission", "restricted_content_confirmed"]
)
def test_each_explicit_confirmation_required(missing):
    values = dict(rights_confirmed=True, public_preview_permission=True,
                  restricted_content_confirmed=True)
    values[missing] = False
    with pytest.raises(policy.PolicyError, match="approval_required"):
        policy.scan_selection([{"value": "Lisbon"}], [{}],
                              scanned_at="2026-09-17T12:00:00Z", **values)


@pytest.fixture
def signed_proofs():
    proofs = [{k: v for k, v in p.items() if k != "row"} for p in package_fixture()["entries"]]
    digest = policy.sampled_leaf_list_digest(proofs)
    for proof in proofs:
        proof.update(
            preview_package_url="https://seller.example/previews/object.json",
            package_media_type="application/vnd.aim.preview+json",
            package_profile="aim-preview-package-v2", package_byte_ceiling=1048576,
            scan_policy=policy.POLICY, scan_policy_version=policy.VERSION,
            scan_verdict="passed", scanned_at="2026-09-17T12:00:00.000000Z",
            sampled_leaf_list_digest=digest,
            signer_reference="00000000-0000-0000-0000-000000000001:" + "a" * 64,
            signature_algorithm="ed25519", signature=encode_base64url(bytes(64)),
        )
    return proofs


def test_signed_attestation_digest_chain_unchanged(signed_proofs):
    assert policy.scan_attestation_digest(signed_proofs) == hashlib.sha256(
        b"aim-preview-scan-attestation-v1\0" + canonical_json_bytes(signed_proofs)
    ).hexdigest()
    signed_proofs[0]["row"] = {"synthetic": "private"}
    with pytest.raises(policy.PolicyError, match="unsigned_proofs"):
        policy.scan_attestation_digest(signed_proofs)


def test_v2_fixture_and_legacy_v1_input_are_pinned():
    emitted = json.loads(Path("tests/fixtures/aim_preview_policy_v2.json").read_text())
    legacy = json.loads(Path("tests/fixtures/aim_preview_policy_v1.json").read_text())
    assert (emitted["scan_policy"], emitted["scan_policy_version"]) == (policy.POLICY, policy.VERSION)
    assert emitted["rules"] == emitted["reason_codes"] == []
    assert (legacy["scan_policy"], legacy["scan_policy_version"]) == (
        "aim-preview-policy-v1", "1.0.0"
    )
