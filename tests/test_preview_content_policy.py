import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services import preview_content_policy as policy
from app.services.pii_service import PIIService, DEFAULT_ENTITIES


@pytest.fixture
def detector(monkeypatch):
    monkeypatch.setattr(policy, "detector_identity", lambda: policy.DETECTOR_IDENTITY)
    service = PIIService()
    service._analyzer = Mock(supported_languages=["en"])
    service._analyzer.analyze.return_value = []
    return service


@pytest.mark.parametrize("position", range(100))
@pytest.mark.parametrize("nested", [False, True])
def test_every_row_and_nested_position(detector, position, nested):
    rows = [{"value": "oats"} for _ in range(100)]
    marker = "synthetic@example.test"
    rows[position] = {"value": [{"inside": marker}]} if nested else {"value": marker}
    detector._analyzer.analyze.side_effect = lambda **kw: (
        [object()] if kw["text"] == marker else []
    )
    with pytest.raises(policy.PolicyError, match="^personal_data$"):
        detector.scan_complete_selection(rows)
    for call in detector._analyzer.analyze.call_args_list:
        assert call.kwargs["entities"] == DEFAULT_ENTITIES
        assert call.kwargs["score_threshold"] == 0.5


def test_keys_array_elements_and_no_length_skip(detector):
    detector.scan_complete_selection(
        [{"outer": [{"key": "x" * 11000}, 123, None, True]}]
    )
    assert [c.kwargs["text"] for c in detector._analyzer.analyze.call_args_list] == [
        "outer",
        "key",
        "x" * 11000,
        "123",
    ]


@pytest.mark.parametrize(
    "value",
    [
        "api_key=synthetic",
        "".join(["-----BEGIN ", "PRIVATE ", "KEY-----"]),
        "AKIA" + "A" * 16,
        "postgres://u:p@host/db",
        "ghp_" + "a" * 25,
        "abcdefghijklmnopqrstuvwxyz0123456789",
        "=SUM(A1)",
        "  +cmd",
        "-cmd",
        "@sum",
        "<script>alert(1)</script>",
        "onclick=run()",
        "javascript:run()",
        "https://example.test/path",
        "www.example.test",
        "[click](target)",
        "mailto:synthetic@example.test",
        "x" * 501,
        "word " * 81,
        "copyright owner",
        "all rights reserved",
        "excerpt from a book",
        "licensed under X",
        "a\x00b",
        "a\u202eb",
        "a\u2066b",
        "Sub Auto_Open",
        "123-45-6789",
        "synthetic@example.test",
        "192.0.2.1",
    ],
    ids=lambda x: "rule-" + str(len(x)),
)
def test_frozen_predicates(value):
    with pytest.raises(policy.PolicyError) as exc:
        policy.check_text(value)
    assert value not in str(exc.value)


def test_numeric_negative_vs_formula():
    rows = [{"value": policy.NumericText("-3.25")}]
    for value, numeric in policy.walk_selection(rows):
        policy.check_text(value, numeric)
    with pytest.raises(policy.PolicyError, match="formula"):
        policy.check_text("-3.25")


@pytest.mark.parametrize(
    "value", ["https:example.test", "sk-proj-synthetic", "sk-synthetic"]
)
def test_scheme_and_known_token_formats(value):
    with pytest.raises(policy.PolicyError):
        policy.check_text(value)


@pytest.mark.parametrize("value", [b"synthetic", {"a": [b"synthetic"]}, float("nan")])
def test_unsupported_values(value):
    with pytest.raises(policy.PolicyError):
        list(policy.walk_selection([{"x": value}]))


def test_detector_unavailable_safe_errors(detector, caplog):
    marker = "unique_synthetic_cell_marker_failure"
    detector._analyzer.analyze.side_effect = RuntimeError(marker)
    with pytest.raises(policy.PolicyError, match="^detector_unavailable$") as exc:
        detector.scan_complete_selection([{"x": marker}])
    assert marker not in str(exc.value) + caplog.text
    assert exc.value.__suppress_context__
    with pytest.raises(policy.PolicyError):
        detector.scan_complete_selection([{"x": "oats"}], language="unsupported")


def test_unknown_model_identity_fails_closed(monkeypatch):
    monkeypatch.setattr("importlib.metadata.version", lambda _: "unknown")
    with pytest.raises(policy.PolicyError, match="detector_unavailable"):
        policy.detector_identity()


def test_scan_metadata_and_digest(detector):
    fixture = json.loads(Path("tests/fixtures/aim_preview_package_v2.json").read_text())
    vectors = json.loads(
        Path("tests/fixtures/aim_preview_package_vectors_v1.json").read_text()
    )
    proofs = fixture["entries"]
    result = policy.scan_selection(
        [p["row"] for p in proofs],
        proofs,
        detector=detector,
        scanned_at="2026-09-17T12:00:00Z",
        rights_confirmed=True,
        public_preview_permission=True,
        restricted_content_confirmed=True,
    )
    assert result == {
        "scan_policy": policy.POLICY,
        "scan_policy_version": "1.0.0",
        "scan_verdict": "passed",
        "scanned_at": "2026-09-17T12:00:00.000000Z",
        "sampled_leaf_list_digest": vectors["sampled_leaf_list_digest"],
    }
    with pytest.raises(policy.PolicyError, match="approval_required"):
        policy.scan_selection(
            [], [], detector=detector, scanned_at="bad", rights_confirmed=False
        )
    with pytest.raises(policy.PolicyError):
        policy.scan_attestation_digest(proofs)


@pytest.fixture
def signed_proofs():
    from app.services.dataset_merkle_service import encode_base64url

    fixture = json.loads(Path("tests/fixtures/aim_preview_package_v2.json").read_text())
    proofs = [{k: v for k, v in p.items() if k != "row"} for p in fixture["entries"]]
    for p in proofs:
        p.update(
            preview_package_url="https://seller.example/previews/object.json",
            package_media_type="application/vnd.aim.preview+json",
            package_profile="aim-preview-package-v2",
            package_byte_ceiling=1048576,
            scan_policy=policy.POLICY,
            scan_policy_version=policy.VERSION,
            scan_verdict="passed",
            scanned_at="2026-09-17T12:00:00.000000Z",
            sampled_leaf_list_digest=policy.sampled_leaf_list_digest(proofs),
            signer_reference="00000000-0000-0000-0000-000000000001:" + "a" * 64,
            signature_algorithm="ed25519",
            signature=encode_base64url(bytes(64)),
        )
    return proofs


def test_signed_attestation_digest(signed_proofs):
    import hashlib
    from app.services.dataset_merkle_service import canonical_json_bytes

    proofs = signed_proofs
    assert (
        policy.scan_attestation_digest(proofs)
        == hashlib.sha256(
            b"aim-preview-scan-attestation-v1\0" + canonical_json_bytes(proofs)
        ).hexdigest()
    )
    proofs[0]["row"] = {"synthetic": "private"}
    with pytest.raises(policy.PolicyError):
        policy.scan_attestation_digest(proofs)


def test_frozen_shared_policy_corpus():
    corpus = json.loads(Path("tests/fixtures/aim_preview_policy_v1.json").read_text())
    assert corpus["scan_policy"] == policy.POLICY
    assert corpus["scan_policy_version"] == policy.VERSION
    assert corpus["detector_identity"] == policy.DETECTOR_IDENTITY
    for case in corpus["cases"]:
        with pytest.raises(policy.PolicyError):
            policy.check_text(case.get("value") or "".join(case["parts"]))
    for value in corpus["safe_cases"]:
        policy.check_text(value)


@pytest.mark.parametrize(
    "missing",
    ["rights_confirmed", "public_preview_permission", "restricted_content_confirmed"],
)
def test_each_explicit_consent_required(detector, missing):
    consents = dict(
        rights_confirmed=True,
        public_preview_permission=True,
        restricted_content_confirmed=True,
    )
    consents[missing] = False
    with pytest.raises(policy.PolicyError, match="approval_required"):
        policy.scan_selection(
            [{"value": "oats"}],
            [{}],
            detector=detector,
            scanned_at="2026-09-17T12:00:00Z",
            **consents,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("preview_package_url", "https://other.example/preview.json"),
        ("package_media_type", "application/json"),
        ("package_profile", "aim-preview-package-v1"),
        ("package_byte_ceiling", 1000000),
        ("scan_policy", "other-policy"),
        ("scan_policy_version", "1.0.1"),
        ("scan_verdict", "failed"),
        ("scanned_at", "2026-09-17T12:01:00.000000Z"),
        ("sampled_leaf_list_digest", "A" * 43),
        ("signer_reference", "00000000-0000-0000-0000-000000000002:" + "a" * 64),
        ("signer_reference", "00000000-0000-0000-0000-000000000001:" + "b" * 64),
        ("signature_algorithm", "other-algorithm"),
    ],
)
def test_mixed_attestation_rejected(signed_proofs, field, value):
    # URL, ceiling, time and signer variants are individually valid records.
    signed_proofs[1][field] = value
    with pytest.raises(policy.PolicyError, match="^unsigned_proofs$"):
        policy.scan_attestation_digest(signed_proofs)
