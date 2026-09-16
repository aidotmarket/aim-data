import hashlib
import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.dataset_commitment_schemas import DatasetCommitment
from app.services import dataset_merkle_service as m
from app.services.dataset_canonicalization import ParsingDeclaration

FIXTURE = Path(__file__).parent / "fixtures" / "aim_dataset_merkle_v1.json"


def test_golden_byte_exact(tmp_path):
    assert (
        hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        == "f3e358d1e7ce7c836ce8604810675e0952201a7799b92d30858cd489906499af"
    )
    data = json.loads(FIXTURE.read_text())
    schema = m.compute_schema_digest(m.canonical_json_bytes(data["canonical_schema"]))
    assert m.encode_base64url(schema) == data["schema_digest"]
    leaves = []
    for row in data["rows"]:
        base = m.compute_base_row_digest(
            schema, m.canonical_json_bytes(row["canonical_row"])
        )
        assert m.encode_base64url(base) == row["base_row_digest"]
        leaf = m.compute_leaf_hash(base, row["duplicate_ordinal"])
        leaves.append(leaf)
        assert m.encode_base64url(leaf) == row["leaf_hash"]
    root = m.build_merkle_root(leaves)
    assert m.encode_base64url(root) == data["dataset_merkle_root"]
    for row in data["rows"]:
        assert m.verify_inclusion_proof(
            row["leaf_hash"], row["leaf_index"], 5, row["proof"], root
        )
        proof = m.build_inclusion_proof(leaves, row["leaf_index"])
        assert [
            {"hash": m.encode_base64url(p["hash"]), "direction": p["direction"]}
            for p in proof
        ] == row["proof"]
    with m.private_job(tmp_path / "jobs") as directory:
        tree = m.build_disk_tree(
            (
                m.canonical_json_bytes(row["canonical_row"])
                for row in reversed(data["rows"])
            ),
            schema,
            directory,
            budget=m.WorkerBudget(run_bytes=200),
        )
        assert tree.root == root
        for row in data["rows"]:
            assert tree.proof(row["leaf_index"]) == {
                k: row[k]
                for k in ("base_row_digest", "duplicate_ordinal", "leaf_index")
            } | {"tree_size": 5, "siblings": row["proof"]}
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir())
    assert not list((tmp_path / "jobs").glob("job-*"))


def test_log_checkpoint_vectors():
    log = json.loads(FIXTURE.read_text())["log"]
    leaves = []
    for entry in log["entries"]:
        raw = m.canonical_log_entry_bytes(
            {
                k: v
                for k, v in entry.items()
                if k not in {"canonical_entry_b64url", "leaf_hash"}
            }
        )
        assert m.encode_base64url(raw) == entry["canonical_entry_b64url"]
        leaf = m.compute_log_leaf_hash(raw)
        leaves.append(leaf)
        assert m.encode_base64url(leaf) == entry["leaf_hash"]
    assert m.encode_base64url(m.build_log_root(leaves)) == log["root"]
    checkpoint = log["checkpoint"]
    for vector in [checkpoint] + log["fractional_checkpoint_vectors"]:
        message = m.checkpoint_signing_bytes(
            checkpoint["log_id"], 2, log["root"], vector["checkpoint_at"]
        )
        assert m.encode_base64url(message) == vector["message_b64url"]
        assert m.verify_checkpoint_signature(
            checkpoint["public_key"], vector["signature"], message
        )


@pytest.mark.parametrize("size", range(1, 20))
def test_consistency(size):
    leaves = [hashlib.sha256(str(i).encode()).digest() for i in range(size)]
    for old in range(1, size + 1):
        proof = m.build_consistency_proof(leaves, old)
        assert m.verify_consistency_proof(
            old,
            size,
            m.build_merkle_root(leaves[:old]),
            m.build_merkle_root(leaves),
            proof,
        )
        if proof:
            assert not m.verify_consistency_proof(
                old,
                size,
                m.build_merkle_root(leaves[:old]),
                m.build_merkle_root(leaves),
                [b"\0" * 32] + proof[1:],
            )


def test_cleanup_failure_startup_cancel(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir(mode=0o700)
    old = root / "job-abandoned"
    old.mkdir()
    (old / "rows").write_bytes(b"private")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    (root / "job-symlink").symlink_to(outside)
    with pytest.raises(m.CommitmentValidationError, match="cancelled"):
        with m.private_job(root) as directory:
            assert directory.stat().st_mode & 0o777 == 0o700
            assert not old.exists()
            cancel = threading.Event()
            cancel.set()
            m.build_disk_tree([b"[]"], b"\0" * 32, directory, cancel=cancel)
    assert (outside / "keep").exists()
    assert not list(root.glob("job-*"))


def test_worker(tmp_path):
    path = tmp_path / "source"
    path.write_text('{"id":1}\n{"id":2}\n')
    phases = []
    result = m.run_commitment_job(
        [path],
        ParsingDeclaration("ndjson", encoding="utf-8"),
        [["id", "signed_integer", False, {}]],
        tmp_path / "jobs",
        indices=[0, 1],
        progress=lambda p: phases.append(p["phase"]),
    )
    assert result["commitment"]["leaf_count"] == 2
    assert phases == ["reading", "sorting", "building_tree", "selecting"]
    for proof in result["proofs"]:
        assert m.verify_inclusion_proof(
            m.compute_leaf_hash(proof["base_row_digest"], proof["duplicate_ordinal"]),
            proof["leaf_index"],
            2,
            proof["siblings"],
            result["commitment"]["dataset_merkle_root"],
        )
    assert not list((tmp_path / "jobs").glob("job-*"))


def test_closed_model():
    good = {
        "schema_digest": m.encode_base64url(b"\0" * 32),
        "dataset_merkle_root": m.encode_base64url(b"\0" * 32),
        "leaf_count": 1,
    }
    assert DatasetCommitment(**good)
    for carrier in ["row", "rows", "sample_values", "path", "canonical_bytes"]:
        with pytest.raises(ValidationError):
            DatasetCommitment(**good, **{carrier: "PRIVATE"})


@pytest.mark.parametrize("siblings", [62, 63, 64])
def test_proof_cap(siblings):
    assert not m.verify_inclusion_proof(
        b"\0" * 32,
        0,
        1,
        [{"hash": b"\0" * 32, "direction": "right"}] * siblings,
        b"\0" * 32,
    )


def test_disk_budget(tmp_path):
    with m.private_job(tmp_path / "jobs") as directory:
        with pytest.raises(m.CommitmentValidationError, match="disk_resource_limit"):
            m.build_disk_tree(
                [b"[]"], b"\0" * 32, directory, budget=m.WorkerBudget(disk_bytes=1)
            )


@pytest.mark.parametrize("depth", [62, 63, 64])
def test_maximum_valid_proof_depth(depth):
    size = (1 << depth) - 1
    if depth == 64:
        assert not m.verify_inclusion_proof(b"\0" * 32, 0, size, [], b"\0" * 32)
        return
    directions = m.expected_proof_directions(0, size)
    assert len(directions) == depth
    root = b"\0" * 32
    siblings = []
    for direction in directions:
        sibling = b"\1" * 32
        siblings.append({"hash": sibling, "direction": direction})
        root = m.compute_node_hash(root, sibling)
    assert m.verify_inclusion_proof(b"\0" * 32, 0, size, siblings, root)


def test_worker_rss_failure_and_cleanup(tmp_path):
    path = tmp_path / "source"
    path.write_text('{"x":"hello"}\n' * 10000)
    with pytest.raises(m.CommitmentValidationError, match="resource_limit"):
        m.run_commitment_job(
            [path],
            ParsingDeclaration("ndjson", encoding="utf-8"),
            [["x", "string", False, {}]],
            tmp_path / "jobs",
            budget=m.WorkerBudget(rss_bytes=1),
        )
    assert not list((tmp_path / "jobs").glob("job-*"))


def test_cancel_mid_merge(tmp_path):
    path = tmp_path / "source"
    with path.open("w") as stream:
        for i in range(10000):
            stream.write('{"x":' + str(i) + "}\n")
    cancel = threading.Event()
    phases = []

    def progress(p):
        phases.append(p["phase"])
        if phases.count("sorting") == 2:
            cancel.set()

    with pytest.raises(m.CommitmentValidationError, match="cancelled"):
        m.run_commitment_job(
            [path],
            ParsingDeclaration("ndjson", encoding="utf-8"),
            [["x", "signed_integer", False, {}]],
            tmp_path / "jobs",
            budget=m.WorkerBudget(run_bytes=4096),
            cancel=cancel,
            progress=progress,
        )
    assert phases.count("sorting") >= 2
    assert not list((tmp_path / "jobs").glob("job-*"))


def test_worker_changed_source_after_read(tmp_path):
    path = tmp_path / "source"
    path.write_text('{"x":1}\n' * 10000)

    def progress(p):
        if p["phase"] == "sorting":
            path.write_text('{"x":2}\n')

    with pytest.raises(m.CommitmentValidationError, match="source_changed"):
        m.run_commitment_job(
            [path],
            ParsingDeclaration("ndjson", encoding="utf-8"),
            [["x", "signed_integer", False, {}]],
            tmp_path / "jobs",
            budget=m.WorkerBudget(run_bytes=4096),
            progress=progress,
        )
    assert not list((tmp_path / "jobs").glob("job-*"))


def test_lock_excludes_second_job(tmp_path):
    with m.private_job(tmp_path / "jobs"):
        with pytest.raises(m.CommitmentValidationError, match="job_already_running"):
            with m.private_job(tmp_path / "jobs"):
                raise AssertionError("second worker entered")


def test_model_unsafe_integer_no_echo():
    with pytest.raises(ValidationError) as exc:
        DatasetCommitment(
            schema_digest=m.encode_base64url(b"\0" * 32),
            dataset_merkle_root=m.encode_base64url(b"\0" * 32),
            leaf_count=2**53,
        )
    assert exc.value.errors()[0]["type"] == "unsafe_integer"
    assert str(2**53) not in str(exc.value)


def test_large_record_and_wide_schema(tmp_path):
    from app.services.dataset_canonicalization import CanonicalSchema

    schema = CanonicalSchema([[f"f{i}", "string", False, {}] for i in range(500)])
    row = schema.canonical_row({f"f{i}": "x" * 1000 for i in range(500)})
    with m.private_job(tmp_path / "jobs") as directory:
        tree = m.build_disk_tree(
            [row] * 9,
            schema.digest,
            directory,
            budget=m.WorkerBudget(run_bytes=1024 * 1024),
        )
        assert tree.count == 9
        assert tree.proof(8)["duplicate_ordinal"] == 8
    with m.private_job(tmp_path / "jobs") as directory:
        with pytest.raises(m.CommitmentValidationError, match="record_resource_limit"):
            m.build_disk_tree([b"x" * (8 * 1024 * 1024 + 1)], schema.digest, directory)


def test_no_symlink_source_and_private_root(tmp_path):
    path = tmp_path / "file"
    path.write_text('{"x":1}\n')
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(m.CommitmentValidationError, match="invalid_source"):
        m.run_commitment_job(
            [link],
            ParsingDeclaration("ndjson", encoding="utf-8"),
            [["x", "signed_integer", False, {}]],
            tmp_path / "jobs",
        )
    root = tmp_path / "public"
    root.mkdir(mode=0o755)
    with pytest.raises(m.CommitmentValidationError, match="unsafe_temp_directory"):
        with m.private_job(root):
            raise AssertionError("public temporary root admitted")


def test_closed_progress_and_proof_positions():
    from app.models.dataset_commitment_schemas import (
        CommitmentProgress,
        CommitmentProof,
    )

    assert CommitmentProgress(
        phase="reading", records=0, canonical_bytes=0, elapsed_seconds=0.1
    )
    with pytest.raises(ValidationError, match="invalid_inclusion_proof"):
        CommitmentProof(
            base_row_digest=m.encode_base64url(b"\0" * 32),
            duplicate_ordinal=0,
            leaf_index=1,
            tree_size=1,
            siblings=[],
        )
