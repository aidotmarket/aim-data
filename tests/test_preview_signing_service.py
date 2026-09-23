"""Synthetic keys only; no customer keystore is opened by these tests."""

from datetime import datetime, timedelta, timezone
import hashlib
import pytest
from pydantic import ValidationError
from app.core.crypto import DeviceCrypto
from app.models.dataset_commitment_schemas import DatasetReattestationContract
from app.services.preview_signing_service import (
    LocalCandidate,
    PreviewSigningService,
    SigningError,
    disclosure_bytes,
    public_bytes,
    fingerprint,
    commitment_bytes,
    reattestation_bytes,
)
import copy
import json
from pathlib import Path
from app.services.preview_signing_service import (
    verify_bytes,
    platform_envelope_bytes,
    verify_checkpoint,
    verify_authenticated_request,
    verify_log_evidence,
)
from app.services.dataset_merkle_service import (
    canonical_json_bytes,
    decode_base64url,
    encode_base64url,
    checkpoint_signing_bytes,
)
from tests.preview_fixture_factory import (
    signing_corpus,
    platform_material,
    request_fixture,
    uid,
    material,
)

NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
INSTALL = "00000000-0000-4000-8000-000000000001"
SELLER = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def signer(tmp_path):
    crypto = DeviceCrypto(str(tmp_path / "keystore.json"), "synthetic-test-passphrase")
    crypto._pbkdf2_iterations = 1  # test-only speed; production default untouched
    keys = crypto.get_or_create_keypairs()
    evidence = dict(
        install_id=INSTALL,
        seller_id=SELLER,
        fingerprint=fingerprint(public_bytes(keys[1])),
        status="active",
        observed_at=NOW,
    )
    service = PreviewSigningService(
        crypto,
        install_id=INSTALL,
        seller_id=SELLER,
        evidence_reader=lambda: evidence,
        evidence_max_age=timedelta(hours=1),
        clock=lambda: NOW,
    )
    return service, evidence


def test_existing_install_key_only(signer):
    s, e = signer
    assert s.signer_reference == INSTALL + ":" + e["fingerprint"]
    s.crypto.keystore_path.unlink()
    with pytest.raises(SigningError, match="signing_authority_unavailable"):
        s.signer_reference
    assert not s.crypto.keystore_path.exists()


def test_existing_install_key_signs_without_operator_evidence_file(tmp_path):
    crypto = DeviceCrypto(str(tmp_path / "keystore.json"), "synthetic-test-passphrase")
    crypto._pbkdf2_iterations = 1
    crypto.get_or_create_keypairs()
    service = PreviewSigningService(crypto, install_id=INSTALL, seller_id=SELLER)
    assert service.signer_reference.startswith(INSTALL + ":")


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "revoked"),
        ("status", "rotated"),
        ("fingerprint", "0" * 64),
        ("seller_id", INSTALL),
        ("install_id", SELLER),
        ("observed_at", NOW - timedelta(hours=1)),
        ("observed_at", NOW + timedelta(seconds=1)),
    ],
)
def test_registration_fail_closed(signer, field, value):
    s, e = signer
    e[field] = value
    with pytest.raises(SigningError):
        s.signer_reference


def test_missing_evidence(signer):
    s, e = signer
    e.clear()
    with pytest.raises(SigningError):
        s.signer_reference


def test_missing_passphrase_and_pending_rotation(signer):
    s, _ = signer
    s.crypto.keystore_path.with_suffix(".rotation-pending").touch()
    with pytest.raises(SigningError):
        s.signer_reference
    s.crypto.keystore_path.with_suffix(".rotation-pending").unlink()
    s.crypto._passphrase = b""
    with pytest.raises(SigningError):
        s.signer_reference


def contract_corpus():
    import os
    from scripts.preview_contract_gate import lock, verify_manifest

    path = os.environ.get("PREVIEW_CONTRACT_CORPUS")
    if not path:
        pytest.skip("explicit PREVIEW_CONTRACT_CORPUS required")
    corpus = Path(path)
    expected = lock("aim-data", "preview-contract-backend.lock.json")
    verify_manifest(corpus, expected["manifest_sha256"])
    return corpus


def test_golden_corpus():
    from tests.preview_contract_runner import dispatch
    from scripts.preview_contract_gate import read_json, verify_manifest

    corpus = contract_corpus()
    _, rows = verify_manifest(corpus)
    for row in rows:
        if row["expected"] != "accept" or row["operation"] not in {
            "request-bytes", "disclosure-preimage", "platform-envelope-preimage",
            "proof-model", "commitment-model",
        }:
            continue
        value = read_json(corpus / row["input_path"])
        raw, _ = dispatch(row, value)
        assert raw == (corpus / row["bytes_path"]).read_bytes(), row["id"]


def test_candidate_accepts_aggregate_hash_profile_and_round_trips():
    _, _, binding = material()
    binding["aggregate_hash_profile"] = "aim-approved-aggregates-v2"

    assert LocalCandidate.validate(binding).binding() == binding


def test_legacy_binding_omits_aggregate_hash_profile_from_signed_wire():
    _, _, binding = material()
    candidate = LocalCandidate.validate(binding)

    assert b"aggregate_hash_profile" not in candidate.binding_bytes
    assert b"aggregate_hash_profile" not in disclosure_bytes(binding)


def test_profiled_binding_preserves_field_across_candidate_bytes():
    _, _, binding = material()
    binding["aggregate_hash_profile"] = "aim-approved-aggregates-v1"
    candidate = LocalCandidate.validate(binding)

    assert b'"aggregate_hash_profile":"aim-approved-aggregates-v1"' in (
        candidate.binding_bytes
    )
    round_tripped = LocalCandidate.validate(json.loads(candidate.binding_bytes))
    assert round_tripped.binding() == binding


def test_candidate_rejects_unknown_aggregate_hash_profile():
    _, _, binding = material()
    binding["aggregate_hash_profile"] = "aim-approved-aggregates-v3"

    with pytest.raises(SigningError, match="contract_mismatch"):
        LocalCandidate.validate(binding)


def reattestation_fixture(reference):
    from app.services.dataset_merkle_service import encode_base64url

    bound = dict(
        listing_id=uid(4),
        seller_dataset_version="fixture-v1",
        schema_digest=encode_base64url(bytes([3]) * 32),
        dataset_merkle_root=encode_base64url(bytes([4]) * 32),
        leaf_count=2,
        signed_at="2026-09-17T00:00:00.000000Z",
    )
    return dict(
        commitment_id=uid(3),
        **bound,
        seller_attestation=dict(
            **bound,
            sample_hash="1" * 64,
            rights_basis_digest="2" * 64,
            public_preview_permission=True,
            metadata_accuracy_confirmed=True,
        ),
        aim_data_signer_reference=reference,
        signature_algorithm="ed25519",
        seller_signature="A" * 86,
        update_cadence_days=None,
    )


def test_reattestation_preimage_golden_vector():
    value = reattestation_fixture(uid(1) + ":" + "a" * 64)
    expected = (
        b"aim-dataset-reattestation-signature-v1\0"
        b'{"aim_data_signer_reference":"00000000-0000-4000-8000-000000000001:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"commitment_id":"00000000-0000-4000-8000-000000000003","dataset_merkle_root":"BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ",'
        b'"leaf_count":2,"listing_id":"00000000-0000-4000-8000-000000000004","schema_digest":"AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM",'
        b'"seller_attestation":{"dataset_merkle_root":"BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ","leaf_count":2,'
        b'"listing_id":"00000000-0000-4000-8000-000000000004","metadata_accuracy_confirmed":true,"public_preview_permission":true,'
        b'"rights_basis_digest":"2222222222222222222222222222222222222222222222222222222222222222",'
        b'"sample_hash":"1111111111111111111111111111111111111111111111111111111111111111",'
        b'"schema_digest":"AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM","seller_dataset_version":"fixture-v1",'
        b'"signed_at":"2026-09-17T00:00:00.000000Z"},"seller_dataset_version":"fixture-v1","signature_algorithm":"ed25519",'
        b'"signed_at":"2026-09-17T00:00:00.000000Z","update_cadence_days":null}'
    )
    actual = reattestation_bytes(value)
    assert actual == expected
    assert hashlib.sha256(actual).hexdigest() == "641f97b6a56cdb3b64b8135b4878f692ca647dccb4e62e6bd926d5d34deb1b41"


def test_reattestation_non_null_cadence_preimage_golden_vector():
    value = reattestation_fixture(uid(1) + ":" + "a" * 64)
    value["update_cadence_days"] = 30
    actual = reattestation_bytes(value)
    expected = (
        b"aim-dataset-reattestation-signature-v1\0"
        b'{"aim_data_signer_reference":"00000000-0000-4000-8000-000000000001:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"commitment_id":"00000000-0000-4000-8000-000000000003","dataset_merkle_root":"BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ",'
        b'"leaf_count":2,"listing_id":"00000000-0000-4000-8000-000000000004","schema_digest":"AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM",'
        b'"seller_attestation":{"dataset_merkle_root":"BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ","leaf_count":2,'
        b'"listing_id":"00000000-0000-4000-8000-000000000004","metadata_accuracy_confirmed":true,"public_preview_permission":true,'
        b'"rights_basis_digest":"2222222222222222222222222222222222222222222222222222222222222222",'
        b'"sample_hash":"1111111111111111111111111111111111111111111111111111111111111111",'
        b'"schema_digest":"AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM","seller_dataset_version":"fixture-v1",'
        b'"signed_at":"2026-09-17T00:00:00.000000Z"},"seller_dataset_version":"fixture-v1","signature_algorithm":"ed25519",'
        b'"signed_at":"2026-09-17T00:00:00.000000Z","update_cadence_days":30}'
    )
    assert actual == expected
    assert hashlib.sha256(actual).hexdigest() == "da6dcd085830aa95457a83a7e920a46a3415ade472888621a10c821853f3f577"


def test_reattestation_rejects_nested_binding_mismatch():
    value = reattestation_fixture(uid(1) + ":" + "a" * 64)
    value["seller_attestation"]["schema_digest"] = "A" * 43
    with pytest.raises(ValidationError, match="reattestation_binding_mismatch"):
        DatasetReattestationContract.model_validate(value)


def test_reattestation_round_trip_and_commitment_domain_isolation(signer):
    service, _ = signer
    value = reattestation_fixture(service.signer_reference)
    signed = service.sign_reattestation(value)
    raw_key = public_bytes(service._keys()[1])
    assert verify_bytes(raw_key, signed["seller_signature"], reattestation_bytes(signed))

    key, commitment, _ = material()
    commitment_signature = commitment["seller_signature"]
    corresponding = reattestation_fixture(commitment["aim_data_signer_reference"])
    corresponding["schema_digest"] = commitment["schema_digest"]
    corresponding["dataset_merkle_root"] = commitment["dataset_merkle_root"]
    corresponding["seller_attestation"]["schema_digest"] = commitment["schema_digest"]
    corresponding["seller_attestation"]["dataset_merkle_root"] = commitment["dataset_merkle_root"]
    raw_fixture_key = public_bytes(key.public_key())
    from app.services.dataset_merkle_service import encode_base64url
    reattestation_signature = encode_base64url(key.sign(reattestation_bytes(corresponding)))
    assert not verify_bytes(raw_fixture_key, reattestation_signature, commitment_bytes(commitment))
    assert not verify_bytes(raw_fixture_key, commitment_signature, reattestation_bytes(corresponding))
    reattestation_preimage = reattestation_bytes(corresponding)
    commitment_preimage = commitment_bytes(commitment)
    same_reattestation_payload_wrong_domain = (
        b"aim-dataset-commitment-signature-v1\0"
        + reattestation_preimage.split(b"\0", 1)[1]
    )
    same_commitment_payload_wrong_domain = (
        b"aim-dataset-reattestation-signature-v1\0"
        + commitment_preimage.split(b"\0", 1)[1]
    )
    assert not verify_bytes(
        raw_fixture_key,
        encode_base64url(key.sign(same_reattestation_payload_wrong_domain)),
        reattestation_preimage,
    )
    assert not verify_bytes(
        raw_fixture_key,
        encode_base64url(key.sign(same_commitment_payload_wrong_domain)),
        commitment_preimage,
    )


def test_commitment_previous_id_matches_backend_closed_contract_bytes():
    key, commitment, _ = material()
    commitment["previous_commitment_id"] = uid(40)
    expected_contract = {
        field: commitment[field]
        for field in (
            "commitment_id",
            "listing_id",
            "seller_dataset_version",
            "previous_commitment_id",
            "canonicalization_profile",
            "hash_algorithm",
            "schema_digest",
            "dataset_merkle_root",
            "leaf_count",
            "seller_attestation_digest",
            "aim_data_signer_reference",
            "signature_algorithm",
            "signed_at",
            "proofs",
        )
    }
    backend_bytes = (
        b"aim-dataset-commitment-signature-v1\0"
        + canonical_json_bytes(expected_contract)
    )
    signature = key.sign(commitment_bytes(commitment))

    assert commitment_bytes(commitment) == backend_bytes
    assert verify_bytes(
        public_bytes(key.public_key()),
        encode_base64url(signature),
        backend_bytes,
    )


def mutations(value, path=()):
    """Visit EVERY protected field, nested scalar, and nonempty collection order."""
    if isinstance(value, dict):
        for key, child in value.items():
            # A missing included field is a distinct excluded-field attack.
            changed = copy.deepcopy(value)
            del changed[key]
            yield path + (key, "<omitted>"), changed
            for subpath, replacement in mutations(child, path + (key,)):
                changed = copy.deepcopy(value)
                changed[key] = replacement
                yield subpath, changed
    elif isinstance(value, list):
        if len(value) > 1:
            yield path + ("<order>",), list(reversed(value))
        if not value:
            yield path, ["mutation"]
        for i, child in enumerate(value):
            for subpath, replacement in mutations(child, path + (i,)):
                changed_list = copy.deepcopy(value)
                changed_list[i] = replacement
                yield subpath, changed_list
    else:
        replacement = (
            (not value)
            if isinstance(value, bool)
            else value + 1
            if type(value) is int
            else "mutation"
            if value is None
            else value + "x"
        )
        yield path, replacement


def test_every_protected_preimage_field_mutation():
    corpus = signing_corpus()
    count = 0
    for row in corpus["signatures"]:
        message = bytes.fromhex(row["signed_bytes_hex"])
        key = decode_base64url(row["public_key"])
        if row["name"] == "checkpoint":
            lines = message.decode().split("\n")
            for index in range(1, 5):
                changed = lines[:]
                changed[index] += "1"
                assert not verify_bytes(
                    key, row["signature"], "\n".join(changed).encode()
                )
                count += 1
        else:
            domain, raw = message.split(b"\0", 1)
            obj = json.loads(raw)
            for path, changed in mutations(obj):
                assert not verify_bytes(
                    key,
                    row["signature"],
                    domain + b"\0" + canonical_json_bytes(changed),
                ), (row["name"], path)
                count += 1
        assert not verify_bytes(key, row["signature"], b"wrong-domain\0" + message)
    assert count == 758


def test_checkpoint_excluded_fields_validated_independently():
    key, env, cp, log = platform_material()
    trusted = {cp["key_id"]: public_bytes(key.public_key())}
    assert verify_checkpoint(cp, trusted)
    changed = dict(cp, key_id="unknown")
    assert checkpoint_signing_bytes(
        changed["log_id"],
        changed["tree_size"],
        changed["root_hash"],
        changed["checkpoint_at"],
    ) == checkpoint_signing_bytes(
        cp["log_id"], cp["tree_size"], cp["root_hash"], cp["checkpoint_at"]
    )
    with pytest.raises(SigningError):
        verify_checkpoint(changed, trusted)
    with pytest.raises(SigningError):
        verify_checkpoint(dict(cp, public_key_algorithm="rsa"), trusted)
    with pytest.raises(SigningError):
        verify_checkpoint(cp, {**trusted, "alias": trusted[cp["key_id"]]})


@pytest.mark.parametrize(
    "case",
    [
        "revoked",
        "rotated",
        "fingerprint",
        "missing",
        "unused",
        "duplicate",
        "expired",
        "wrong_owner",
        "unknown_platform",
    ],
)
def test_authenticated_signer_evidence_fail_closed(case):
    key, env, cp, log = platform_material()
    r = request_fixture()
    trusted = {env["key_id"]: public_bytes(key.public_key())}
    assert verify_authenticated_request(env, r, trusted, now=NOW)
    if case in {"revoked", "rotated"}:
        env["signer_keys"][0]["status"] = case
    elif case == "fingerprint":
        env["signer_keys"][0]["fingerprint"] = "0" * 64
    elif case == "missing":
        env["signer_keys"] = []
    elif case == "unused":
        env["signer_keys"].append(dict(env["signer_keys"][0], key_id=uid(90)))
    elif case == "duplicate":
        env["signer_keys"] *= 2
    elif case == "expired":
        env["signer_keys"][0]["valid_until"] = "2026-09-16T12:00:00.000000Z"
    elif case == "wrong_owner":
        env["binding"] = dict(env["binding"], seller_id=uid(90))
    else:
        env["key_id"] = "unknown"
    # Trusted platform signing cannot make invalid evidence authorize display.
    try:
        from app.services.dataset_merkle_service import encode_base64url

        env["signature"] = encode_base64url(key.sign(platform_envelope_bytes(env)))
    except SigningError:
        pass
    with pytest.raises(SigningError):
        verify_authenticated_request(env, r, trusted, now=NOW)


def test_log_fixture_and_mutations():
    key, env, cp, log = platform_material()
    c = request_fixture()["commitment"]
    assert verify_log_evidence(log, cp, c)
    subsequent = dict(log, previous_tree_size=1, previous_root=cp["root_hash"])
    assert verify_log_evidence(subsequent, cp, c, trusted_previous=(1, cp["root_hash"]))
    with pytest.raises(SigningError):
        verify_log_evidence(log, cp, c, trusted_previous=(1, cp["root_hash"]))
    for path, changed in mutations(log):
        with pytest.raises((SigningError, ValueError, KeyError)):
            verify_log_evidence(changed, cp, c)


@pytest.mark.asyncio
async def test_rotation_stays_pending_until_registered_readback(signer, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from app.services import registration_service as registration

    s, e = signer
    old_reference = s.signer_reference
    response = MagicMock(status_code=200)
    response.json.return_value = {"install_id": INSTALL}
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = response
    monkeypatch.setattr(registration.httpx, "AsyncClient", lambda **kw: client)
    assert (
        await registration.rotate_preview_install_key(
            s.crypto, install_id=INSTALL, access_token="synthetic-token"
        )
        == "rotation_pending"
    )
    with pytest.raises(SigningError):
        s.signer_reference
    with pytest.raises(SigningError):
        registration.activate_preview_install_rotation(
            s.crypto,
            evidence=e,
            install_id=INSTALL,
            seller_id=SELLER,
            now=NOW,
            max_age=timedelta(hours=1),
        )
    import json

    staged = json.loads(
        s.crypto.keystore_path.with_suffix(".rotation-staged").read_bytes()
    )
    e["fingerprint"] = fingerprint(bytes.fromhex(staged["ed25519_public_key"]))
    registration.activate_preview_install_rotation(
        s.crypto,
        evidence=e,
        install_id=INSTALL,
        seller_id=SELLER,
        now=NOW,
        max_age=timedelta(hours=1),
    )
    assert s.signer_reference != old_reference
    assert client.post.call_args.args[0].endswith("/api/v1/vz/rotate-key")
    assert set(client.post.call_args.kwargs["json"]) == {
        "install_id",
        "new_public_key_b64",
    }
    client.delete.return_value = MagicMock(status_code=204)
    assert (
        await registration.revoke_preview_install(
            s.crypto, install_id=INSTALL, access_token="synthetic-token"
        )
        == "revoked"
    )
    with pytest.raises(SigningError):
        s.signer_reference


@pytest.mark.asyncio
async def test_rotation_timeout_keeps_original_and_blocks(signer, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services import registration_service as registration
    import httpx

    s, _ = signer
    old = s.crypto.keystore_path.read_bytes()
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.side_effect = httpx.TimeoutException("synthetic-private-marker")
    monkeypatch.setattr(registration.httpx, "AsyncClient", lambda **kw: client)
    with pytest.raises(SigningError, match="^rotation_pending$"):
        await registration.rotate_preview_install_key(
            s.crypto, install_id=INSTALL, access_token="synthetic-token"
        )
    assert s.crypto.keystore_path.read_bytes() == old
    with pytest.raises(SigningError):
        s.signer_reference


def test_node_independently_reconstructs_and_verifies_contract_bytes():
    import subprocess

    corpus = contract_corpus()
    result = subprocess.run(
        ["node", "tests/preview_differential_check.cjs", str(corpus)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["signatures"] > 0


def test_node_rejects_corrupt_proof_signature_before_byte_comparison(tmp_path):
    import shutil
    import subprocess
    from scripts.preview_contract_gate import canonical

    corpus = tmp_path / "corpus"
    shutil.copytree(contract_corpus(), corpus)
    input_path = corpus / "inputs/proof-v1-policy-v1.json"
    proof = json.loads(input_path.read_bytes())
    proof["signature"] = (
        "A" if proof["signature"][0] != "A" else "B"
    ) + proof["signature"][1:]
    input_path.write_bytes(canonical(proof))
    result = subprocess.run(
        ["node", "tests/preview_differential_check.cjs", str(corpus)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "signature: proof-v1-policy-v1" in result.stderr


def test_pinned_differential_corpus_python_and_node():
    import subprocess
    from tests.preview_contract_runner import run
    from scripts.preview_contract_gate import verify_manifest

    corpus = contract_corpus()  # explicit --contract-corpus workflow peer checkout
    digest, rows = verify_manifest(corpus)
    with __import__("tempfile").TemporaryDirectory() as directory:
        output = Path(directory) / "aim.json"
        result = run(".", corpus, output)
    assert result["manifest_sha256"] == digest
    assert [r["id"] for r in result["results"]] == [r["id"] for r in rows]
    assert all(r["status"] == row["expected"] for row, r in zip(rows, result["results"]))
    node = subprocess.run(
        ["node", "tests/preview_differential_check.cjs", str(corpus)],
        capture_output=True, text=True,
    )
    assert node.returncode == 0, node.stderr
    checks = json.loads(node.stdout)
    assert checks["digests"] == {
        row["id"]: row["sha256"] for row in rows if row["expected"] == "accept"
    }
