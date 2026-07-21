import json

import pytest

from app.services.dataset_canonicalization import canonicalize_row, compute_schema_digest, encode_base64url
from app.services.dataset_merkle_service import DatasetMerkleService, verify_inclusion_proof


def _vector_commitment(rows=None, *, source_path=None, chunk_size=50_000):
    vector = json.load(open("tests/fixtures/aim_dataset_merkle_v1.json"))
    schema = [
        {"name": item[0], "type": item[1], "nullable": item[2], "type_parameters": item[3]}
        for item in vector["canonical_schema"]
    ]
    rows = rows or [
        {"id": 3},
        {"id": 2, "name": None},
        {"id": 1, "name": "Cafe\u0301"},
        {"id": 1, "name": "Café"},
        {"id": 4, "name": "Z"},
    ]
    digest = compute_schema_digest(schema)
    records = (canonicalize_row(schema, row, schema_digest=digest) for row in rows)
    return vector, DatasetMerkleService(external_sort_chunk_size=chunk_size).build(
        records, schema_digest=digest, source_path=source_path
    )


def test_golden_root_duplicate_ordinals_and_all_proofs():
    vector, commitment = _vector_commitment()
    assert encode_base64url(commitment.dataset_merkle_root) == vector["dataset_merkle_root"]
    assert [leaf.duplicate_ordinal for leaf in commitment.leaves] == [0, 0, 0, 1, 0]
    for leaf in commitment.leaves:
        proof = commitment.proof_for_index(leaf.leaf_index)
        assert verify_inclusion_proof(
            leaf.leaf_hash, leaf.leaf_index, commitment.leaf_count, proof, commitment.dataset_merkle_root
        )


def test_reordering_matches_but_duplicate_count_value_and_membership_change_root():
    _, baseline = _vector_commitment()
    rows = [leaf.record.row for leaf in reversed(baseline.leaves)]
    _, reordered = _vector_commitment(rows)
    _, fewer_duplicates = _vector_commitment([row for index, row in enumerate(rows) if index != 1])
    _, value_changed = _vector_commitment([{**row, "name": "changed"} if row.get("id") == 4 else row for row in rows])
    _, membership_added = _vector_commitment([*rows, {"id": 5, "name": "new"}])
    assert reordered.dataset_merkle_root == baseline.dataset_merkle_root
    assert fewer_duplicates.dataset_merkle_root != baseline.dataset_merkle_root
    assert value_changed.dataset_merkle_root != baseline.dataset_merkle_root
    assert membership_added.dataset_merkle_root != baseline.dataset_merkle_root


def test_schema_change_alters_root_even_when_values_match():
    _, baseline = _vector_commitment()
    schema = [
        {"name": "id", "type": "signed_integer", "nullable": True, "type_parameters": {}},
        {"name": "name", "type": "string", "nullable": True, "type_parameters": {}},
    ]
    digest = compute_schema_digest(schema)
    rows = [leaf.record.row for leaf in baseline.leaves]
    changed = DatasetMerkleService().build(
        [canonicalize_row(schema, row, schema_digest=digest) for row in rows],
        schema_digest=digest,
    )
    assert changed.dataset_merkle_root != baseline.dataset_merkle_root


def test_external_sort_temp_files_are_removed_on_success_and_interruption(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("[]")
    _, commitment = _vector_commitment(source_path=source, chunk_size=1)
    assert commitment.leaf_count == 5
    assert not list(tmp_path.glob(".aim-merkle-*"))

    vector = json.load(open("tests/fixtures/aim_dataset_merkle_v1.json"))
    schema = [{"name": item[0], "type": item[1], "nullable": item[2], "type_parameters": item[3]} for item in vector["canonical_schema"]]
    digest = compute_schema_digest(schema)

    def interrupted():
        yield canonicalize_row(schema, {"id": 1, "name": "x"}, schema_digest=digest)
        raise RuntimeError("interrupt")

    with pytest.raises(RuntimeError):
        DatasetMerkleService(external_sort_chunk_size=1).build(interrupted(), schema_digest=digest, source_path=source)
    assert not list(tmp_path.glob(".aim-merkle-*"))
