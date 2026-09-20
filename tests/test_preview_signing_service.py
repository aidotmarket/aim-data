"""Synthetic keys only; no customer keystore is opened by these tests."""

from datetime import datetime, timedelta, timezone
import hashlib
import pytest
from pydantic import ValidationError
from app.core.crypto import DeviceCrypto
from app.models.dataset_commitment_schemas import DatasetReattestationContract
from app.services.preview_signing_service import (
    PreviewSigningService,
    SigningError,
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
    checkpoint_signing_bytes,
)
from tests.preview_fixture_factory import (
    signing_corpus,
    all_requests,
    platform_material,
    request_fixture,
    test_key,
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


def test_golden_corpus():
    expected = json.loads(
        Path("tests/fixtures/aim_preview_signing_v1.json").read_bytes()
    )
    assert signing_corpus() == expected
    assert all_requests() == json.loads(
        Path("tests/fixtures/aim_preview_requests_v1.json").read_bytes()
    )
    for row in expected["signatures"]:
        assert verify_bytes(
            decode_base64url(row["public_key"]),
            row["signature"],
            bytes.fromhex(row["signed_bytes_hex"]),
        )
        assert not verify_bytes(
            public_bytes(test_key(64).public_key()),
            row["signature"],
            bytes.fromhex(row["signed_bytes_hex"]),
        )


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


def test_node_independently_reconstructs_and_verifies_f2_bytes():
    import subprocess

    script = r"""
const fs = require('fs'), crypto = require('crypto');
const corpus=JSON.parse(fs.readFileSync('tests/fixtures/aim_preview_signing_v1.json'));
const requests=JSON.parse(fs.readFileSync('tests/fixtures/aim_preview_requests_v1.json'));
function canon(v) { if (v===null || typeof v!=='object') return JSON.stringify(v); if(Array.isArray(v)) return '['+v.map(canon).join(',')+']'; return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canon(v[k])).join(',')+'}'; }
function without(v,k) { const o={...v};delete o[k];return o; }
for (const row of corpus.signatures) {
 let object, domain;
 const c=requests.approve.commitment;
 if(row.name.startsWith('proof-')) { const p=c.proofs[Number(row.name.slice(-1))]; object={};for(const k of ['commitment_id','listing_id','seller_dataset_version','schema_digest','dataset_merkle_root'])object[k]=c[k];object.proof=without(p,'signature');domain='aim-preview-proof-signature-v1'; }
 else if(row.name==='commitment') {object=without(c,'seller_signature');domain='aim-dataset-commitment-signature-v1';}
 else if(row.name==='platform-envelope') {object=without(corpus.platform_envelope,'signature');domain='aim-preview-platform-envelope-v1';}
 else if(row.name!=='checkpoint') {object=requests[row.name].binding;domain='aim-preview-disclosure-signature-v1';}
 let bytes;
 if(row.name==='checkpoint') { const cp=corpus.checkpoint;bytes=Buffer.from(`aim-transparency-checkpoint-v1\n${cp.log_id}\n${cp.tree_size}\n${cp.root_hash}\n${cp.checkpoint_at}\n`);}
 else bytes=Buffer.concat([Buffer.from(domain+'\0'),Buffer.from(canon(object))]);
 if(bytes.toString('hex')!==row.signed_bytes_hex) throw Error('preimage mismatch: '+row.name);
 const der=Buffer.concat([Buffer.from('302a300506032b6570032100','hex'),Buffer.from(row.public_key,'base64url')]);
 const key=crypto.createPublicKey({key:der,format:'der',type:'spki'});
 if(!crypto.verify(null,bytes,key,Buffer.from(row.signature,'base64url')))throw Error('signature mismatch');
}
console.log(corpus.signatures.length+' F2 fixtures independently reconstructed and verified');
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "10 F2 fixtures" in result.stdout


def test_pinned_differential_corpus_python_and_node():
    import hashlib
    import subprocess
    from app.services.dataset_merkle_service import CommitmentValidationError
    from tests.preview_differential_corpus import FIXTURE, differential_corpus, preimage

    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FIXTURE.with_suffix(".sha256").read_text().split()[0]
    corpus = json.loads(raw)
    assert differential_corpus() == corpus
    digests = {}
    for vector in corpus["valid"]:
        message = preimage(vector)
        assert message.hex() == vector["signed_bytes_hex"]
        digest = __import__("hashlib").sha256(message).hexdigest()
        assert digest == vector["signed_bytes_sha256"]
        assert verify_bytes(decode_base64url(vector["public_key"]), vector["signature"], message)
        digests[vector["name"]] = digest
    for vector in corpus["must_reject"]:
        with pytest.raises(CommitmentValidationError, match="^" + vector["error"] + "$"):
            preimage(vector)
    result = subprocess.run(["node", "tests/preview_differential_check.cjs"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    node = json.loads(result.stdout)
    assert node["digests"] == digests, "BLOCKER: Python/Node differential disagreement"
    assert node["rejected"] == [v["name"] for v in corpus["must_reject"]]
