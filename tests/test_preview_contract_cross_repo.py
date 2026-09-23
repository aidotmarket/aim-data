"""Small temporary corpora test the gate before the backend anchor exists."""

import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.preview_contract_gate import (
    GateError,
    ACCEPT,
    REJECT,
    allowed_values,
    canonical,
    compare,
    digest,
    lock,
    model_registry,
    operation_tokens,
    verify_manifest,
    coverage,
    event_base,
    aim_transition,
)
from tests.preview_contract_runner import dispatch, error_rows, run
from tests.preview_fixture_factory import request_fixture, platform_material, p1
from app.services.preview_signing_service import (
    proof_bytes,
    commitment_bytes,
    public_bytes,
    fingerprint,
)
from app.services.preview_signing_service import seller_attestation_digest
from app.services.preview_package_service import sample_hash
from app.services.preview_content_policy import scan_attestation_digest
from app.services.dataset_merkle_service import encode_base64url
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def corpus(tmp_path, rows):
    root = tmp_path / "corpus"
    (root / "inputs").mkdir(parents=True)
    (root / "bytes").mkdir()
    manifest_rows = []
    for ident, op, value, expected in rows:
        input_path = f"inputs/{ident}.json"
        (root / input_path).write_bytes(canonical(value))
        row = {
            "id": ident,
            "operation": op,
            "input_path": input_path,
            "expected": expected,
            "models": sorted(operation_tokens({"id": ident, "operation": op})),
        }
        if expected == "accept":
            raw, _ = dispatch(row, value)
            path = f"bytes/{ident}.bin"
            (root / path).write_bytes(raw)
            row.update(bytes_path=path, byte_length=len(raw), sha256=digest(raw))
        else:
            row["expected_error_type"] = "literal_error"
        manifest_rows.append(row)
    manifest = {
        "schema_version": 1,
        "vectors": sorted(manifest_rows, key=lambda r: r["id"]),
    }
    (root / "manifest.json").write_bytes(canonical(manifest))
    manifest_digest = digest((root / "manifest.json").read_bytes())
    (root / "manifest.sha256").write_text(f"{manifest_digest}  manifest.json\n")
    return root, manifest_digest


def result(root, manifest_digest):
    rows = json.loads((root / "manifest.json").read_text())["vectors"]
    results = []
    observations = []
    for row in rows:
        value = json.loads((root / row["input_path"]).read_text())
        if row["expected"] == "accept":
            raw, seen = dispatch(row, value)
            results.append(
                {
                    "id": row["id"],
                    "status": "accept",
                    "bytes": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
                    "byte_length": len(raw),
                    "sha256": digest(raw),
                }
            )
            observations.extend(seen)
        else:
            with pytest.raises(Exception) as exc:
                dispatch(row, value)
            results.append(
                {"id": row["id"], "status": "reject", "errors": error_rows(exc.value)}
            )
    return {
        "repo_sha": "a" * 40,
        "manifest_sha256": manifest_digest,
        "results": results,
        "coverage_observations": sorted(
            observations, key=lambda x: (x["id"], x["input_path"], x["model"])
        ),
    }


def test_lock_canonical_and_exact(tmp_path):
    path = tmp_path / "lock.json"
    value = {"backend_sha": "a" * 40, "manifest_sha256": "b" * 64}
    path.write_bytes(canonical(value))
    assert lock("aim-data", path) == value
    for bad in (
        {**value, "extra": 1},
        {**value, "backend_sha": "HEAD"},
        {**value, "manifest_sha256": "0" * 64},
    ):
        path.write_bytes(canonical(bad))
        with pytest.raises(GateError):
            lock("aim-data", path)
    path.write_text('{"backend_sha":"a","backend_sha":"b"}\n')
    with pytest.raises(GateError, match="duplicate_key"):
        lock("aim-data", path)


def test_manifest_integrity_and_mutations(tmp_path):
    binding = request_fixture()["binding"]
    root, expected = corpus(
        tmp_path, [("binding-v2-approve-public-domain", "binding-model", binding, "accept")]
    )
    assert verify_manifest(root, expected)[0] == expected
    with pytest.raises(GateError, match="lock_manifest_digest"):
        verify_manifest(root, "c" * 64)
    byte_file = root / "bytes/binding-v2-approve-public-domain.bin"
    byte_file.write_bytes(byte_file.read_bytes() + b"x")
    with pytest.raises(GateError, match="byte_digest"):
        verify_manifest(root, expected)


def test_verified_peer_path_reaches_later_workflow_tests(tmp_path):
    root, expected = corpus(
        tmp_path,
        [("binding-v2-approve-public-domain", "binding-model", request_fixture()["binding"], "accept")],
    )
    github_env = tmp_path / "github-env"
    command = [
        sys.executable, "scripts/preview_contract_gate.py", "verify-manifest",
        "--corpus", str(root), "--expected", expected,
    ]
    environment = dict(os.environ, GITHUB_ENV=str(github_env))
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert github_env.read_text() == f"PREVIEW_CONTRACT_CORPUS={root.resolve()}\n"
    command[-1] = "c" * 64
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    assert result.returncode != 0
    assert github_env.read_text() == f"PREVIEW_CONTRACT_CORPUS={root.resolve()}\n"


@pytest.mark.parametrize("expected", ["accept", "reject"])
def test_manifest_rejects_digest_consistent_unknown_id(tmp_path, expected):
    ident = f"binding-unreviewed-{expected}"
    binding = request_fixture()["binding"]
    root, manifest_digest = corpus(
        tmp_path, [(ident, "binding-model", binding, expected)]
    )
    assert digest((root / "manifest.json").read_bytes()) == manifest_digest
    assert (root / "manifest.sha256").read_text() == (
        f"{manifest_digest}  manifest.json\n"
    )
    with pytest.raises(GateError, match="vector_id_inventory"):
        verify_manifest(root, manifest_digest)


def test_inventory_matches_reviewed_vector_counts():
    assert len(ACCEPT) == 36
    assert len(REJECT) == 45
    assert ACCEPT.isdisjoint(REJECT)


def test_manifest_rejects_symlink_and_extra_file(tmp_path):
    root, expected = corpus(
        tmp_path,
        [
            (
                "binding-v2-approve-public-domain",
                "binding-model",
                request_fixture()["binding"],
                "accept",
            )
        ],
    )
    (root / "extra").write_text("x")
    with pytest.raises(GateError, match="corpus_file_set"):
        verify_manifest(root, expected)
    (root / "extra").unlink()
    (root / "inputs/link.json").symlink_to(root / "manifest.json")
    with pytest.raises(GateError, match="symlink_in_corpus"):
        verify_manifest(root, expected)


@pytest.mark.parametrize(
    "op,value",
    [
        ("binding-model", lambda r: r["binding"]),
        ("proof-model", lambda r: r["proofs"][0]),
        ("commitment-model", lambda r: r["commitment"]),
        ("disclosure-preimage", lambda r: r["binding"]),
        ("request-model", lambda r: r),
        ("request-bytes", lambda r: r),
        ("platform-envelope-model", lambda r: platform_material(r)[1]),
        ("platform-envelope-preimage", lambda r: platform_material(r)[1]),
        ("local-candidate", lambda r: r["binding"]),
    ],
)
def test_every_direct_operation(op, value):
    request = request_fixture()
    ident = "request-none" if op.startswith("request-") else "synthetic-operation"
    row = {
        "id": ident,
        "operation": op,
        "models": sorted(operation_tokens({"id": ident, "operation": op})),
    }
    raw, seen = dispatch(row, value(request))
    assert isinstance(raw, bytes) and raw
    assert seen and seen[0]["fields"]


def test_compare_checks_expected_bytes_and_error_code(tmp_path):
    root, expected = corpus(
        tmp_path,
        [
            (
                "binding-v2-approve-public-domain",
                "binding-model",
                request_fixture()["binding"],
                "accept",
            )
        ],
    )
    left = result(root, expected)
    right = copy.deepcopy(left)
    p, q = tmp_path / "left.json", tmp_path / "right.json"
    p.write_bytes(canonical(left))
    q.write_bytes(canonical(right))
    assert compare(root, p, q) == 1
    right["results"][0]["bytes"] = "AAAA"
    q.write_bytes(canonical(right))
    with pytest.raises(GateError, match="bytes:"):
        compare(root, p, q)


def test_compare_rejects_custom_error_code_swap(tmp_path):
    request = copy.deepcopy(request_fixture())
    request["proofs"][0]["sampled_leaf_list_digest"] = "A" * 43
    request["commitment"]["proofs"][0]["sampled_leaf_list_digest"] = "A" * 43
    root, _ = corpus(
        tmp_path,
        [("proof-sampled-leaf-list-mismatch", "request-model", request, "reject")],
    )
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["vectors"][0].update(
        expected_error_type="value_error",
        expected_error_code="sampled_leaf_list_mismatch",
    )
    (root / "manifest.json").write_bytes(canonical(manifest))
    manifest_digest = digest((root / "manifest.json").read_bytes())
    (root / "manifest.sha256").write_text(f"{manifest_digest}  manifest.json\n")
    left = result(root, manifest_digest)
    right = copy.deepcopy(left)
    p, q = tmp_path / "left.json", tmp_path / "right.json"
    p.write_bytes(canonical(left))
    q.write_bytes(canonical(right))
    assert compare(root, p, q) == 1
    right["results"][0]["errors"][0]["code"] = "sampled_leaf_digest_mismatch"
    q.write_bytes(canonical(right))
    with pytest.raises(GateError, match="cross_error:"):
        compare(root, p, q)


def test_coverage_fails_on_small_corpus(tmp_path):
    root, expected = corpus(
        tmp_path,
        [
            (
                "binding-v2-approve-public-domain",
                "binding-model",
                request_fixture()["binding"],
                "accept",
            )
        ],
    )
    path = tmp_path / "result.json"
    path.write_bytes(canonical(result(root, expected)))
    with pytest.raises(GateError, match="coverage:"):
        coverage(path)


def test_runner_writes_canonical_result_from_temporary_corpus(tmp_path):
    root, expected = corpus(
        tmp_path,
        [
            (
                "binding-v2-approve-public-domain",
                "binding-model",
                request_fixture()["binding"],
                "accept",
            )
        ],
    )
    output = tmp_path / "result.json"
    repo = Path(__file__).resolve().parents[1]
    payload = run(repo, root, output)
    assert payload["manifest_sha256"] == expected
    assert output.read_bytes() == canonical(payload)
    assert payload["results"][0]["status"] == "accept"


def test_runner_script_works_from_unrelated_directory(tmp_path):
    root, _ = corpus(
        tmp_path,
        [
            (
                "binding-v2-approve-public-domain",
                "binding-model",
                request_fixture()["binding"],
                "accept",
            )
        ],
    )
    repo = Path(__file__).resolve().parents[1]
    output = tmp_path / "runner-result.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(repo / "tests/preview_contract_runner.py"),
            "--repo-root",
            str(repo),
            "--corpus",
            str(root),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text())["results"][0]["status"] == "accept"


def test_coverage_script_works_from_repo_and_unrelated_directory(tmp_path):
    # This payload exercises the CLI import path with a complete field walk.
    observations = []
    for token, model in model_registry().items():
        variants = [{}]
        for name, field in model.model_fields.items():
            values = allowed_values(field.annotation)
            for value in values or {"synthetic"}:
                variants.append({name: value})
        for variant in variants:
            ident = f"synthetic-{len(observations)}"
            fields = []
            for name, field in sorted(model.model_fields.items()):
                if name in variant:
                    value = variant[name]
                elif field.is_required():
                    values = allowed_values(field.annotation)
                    value = next(iter(values)) if values else "synthetic"
                else:
                    fields.append({"name": name, "present": False})
                    continue
                fields.append({"name": name, "present": True, "value": value})
            observations.append(
                {
                    "id": ident,
                    "model": token,
                    "input_path": "",
                    "direct": True,
                    "fields": fields,
                }
            )
    payload = {
        "repo_sha": "a" * 40,
        "manifest_sha256": "b" * 64,
        "results": [
            {"id": observation["id"], "status": "accept"}
            for observation in observations
        ],
        "coverage_observations": observations,
    }
    path = tmp_path / "coverage-result.json"
    path.write_bytes(canonical(payload))
    repo = Path(__file__).resolve().parents[1]
    for cwd, script in (
        (repo, Path("scripts/preview_contract_gate.py")),
        (tmp_path, repo / "scripts/preview_contract_gate.py"),
    ):
        completed = subprocess.run(
            [sys.executable, str(script), "coverage", "--result", str(path)],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == str(len(observations))


def test_exact_literal_error():
    value = request_fixture()["binding"]
    value["decision"] = "synthetic-unknown"
    row = {
        "id": "literal-binding-decision",
        "operation": "binding-model",
        "models": ["shared.DisclosureBinding"],
    }
    with pytest.raises(Exception) as exc:
        dispatch(row, value)
    assert error_rows(exc.value)[0]["type"] == "literal_error"


def test_construct_request_operation_uses_runner_local_signer():
    request = copy.deepcopy(request_fixture())
    key = Ed25519PrivateKey.from_private_bytes(
        hashlib.sha256(
            b"preview-contract-cross-repo-v1 synthetic signing seed"
        ).digest()
    )
    ref = (
        request["binding"]["signer_reference"].split(":")[0]
        + ":"
        + fingerprint(public_bytes(key.public_key()))
    )
    request["binding"]["signer_reference"] = ref
    commitment = request["commitment"]
    commitment["aim_data_signer_reference"] = ref
    for proof in commitment["proofs"]:
        proof["signer_reference"] = ref
        proof["signature"] = encode_base64url(key.sign(proof_bytes(commitment, proof)))
    binding = request["binding"]
    binding["sample_hash"] = sample_hash(commitment["proofs"])
    binding["scan_attestation_digest"] = scan_attestation_digest(commitment["proofs"])
    commitment["seller_attestation_digest"] = seller_attestation_digest(
        {
            **{
                name: commitment[name]
                for name in (
                    "listing_id",
                    "seller_dataset_version",
                    "schema_digest",
                    "dataset_merkle_root",
                    "leaf_count",
                    "signed_at",
                )
            },
            "sample_hash": binding["sample_hash"],
            "rights_basis_digest": binding["rights_basis_digest"],
            "public_preview_permission": True,
            "metadata_accuracy_confirmed": True,
        }
    )
    commitment["seller_signature"] = encode_base64url(
        key.sign(commitment_bytes(commitment))
    )
    envelope = {
        "candidate": request["binding"],
        "commitment": commitment,
        "proofs": commitment["proofs"],
        "approved_p1": p1(request["binding"]),
    }
    row = {
        "id": "request-approve-v2",
        "operation": "request-bytes",
        "models": ["aim_data.construct_request", "shared.PreviewDisclosureRequest"],
    }
    raw, observations = dispatch(row, envelope)
    assert b'"seller_signature"' in raw
    assert any(
        item["model"] == "shared.PreviewDisclosureRequest" for item in observations
    )
    envelope["approved_p1"]["aggregate_hash"] = "0" * 64
    with pytest.raises(ValueError, match="p1_reference_mismatch"):
        dispatch(row, envelope)


def _git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _repo(path):
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "synthetic@example.test")
    _git(path, "config", "user.name", "Synthetic")
    return path


def _commit(path, name, content):
    (path / name).write_text(content)
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "synthetic")
    return _git(path, "rev-parse", "HEAD")


def test_aim_transition_and_immutable_event_bases(tmp_path, monkeypatch):
    peer = _repo(tmp_path / "peer")
    root, manifest_digest = corpus(
        tmp_path,
        [
            (
                "binding-v2-approve-public-domain",
                "binding-model",
                request_fixture()["binding"],
                "accept",
            )
        ],
    )
    target = peer / "tests/fixtures/preview/cross_repo_contract/v1"
    target.parent.mkdir(parents=True)
    root.rename(target)
    _git(peer, "add", ".")
    _git(peer, "commit", "-qm", "anchor")
    anchor = _git(peer, "rev-parse", "HEAD")
    own = _repo(tmp_path / "own")
    base = _commit(own, "README", "base\n")
    lock_path = own / "preview-contract-backend.lock.json"
    lock_path.write_bytes(
        canonical({"backend_sha": anchor, "manifest_sha256": manifest_digest})
    )
    _git(own, "add", ".")
    _git(own, "commit", "-qm", "pin")
    head = _git(own, "rev-parse", "HEAD")
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"ref": "refs/heads/main", "before": base, "after": head})
    )
    monkeypatch.chdir(own)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    assert event_base(event) == (base, head)
    assert aim_transition(event, peer) == "anchor"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    event.write_text(json.dumps({"pull_request": {"base": {"sha": base}}}))
    assert event_base(event) == (base, head)
    assert aim_transition(event, peer) == "anchor"
    # A manual rerun uses the same event payload and pinned base.
    assert event_base(event) == (base, head)
    non_anchor = _commit(peer, "README", "unrelated\n")
    lock_path.write_bytes(
        canonical({"backend_sha": non_anchor, "manifest_sha256": manifest_digest})
    )
    _git(own, "add", ".")
    _git(own, "commit", "-qm", "bad pin")
    with pytest.raises(GateError, match="non_anchor_pin"):
        aim_transition(event, peer)
