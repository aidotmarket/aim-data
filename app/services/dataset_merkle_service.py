"""Full-dataset Merkle construction for ``aim-dataset-merkle-v1``."""
from __future__ import annotations

import hashlib
import heapq
import hmac
import os
import pickle
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from app.services.dataset_canonicalization import (
    CanonicalRecord,
    DatasetCanonicalizationError,
    encode_base64url,
)


MAX_TREE_SIZE = (1 << 63) - 1
MAX_PROOF_SIBLINGS = 63


class DatasetMerkleError(ValueError):
    """A stable, non-content Merkle construction failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def compute_leaf_hash(base_row_digest: bytes, duplicate_ordinal: int) -> bytes:
    if len(base_row_digest) != 32:
        raise DatasetMerkleError("invalid_hash_encoding")
    if isinstance(duplicate_ordinal, bool) or not isinstance(duplicate_ordinal, int) or not 0 <= duplicate_ordinal <= MAX_TREE_SIZE:
        raise DatasetMerkleError("invalid_duplicate_ordinal")
    return hashlib.sha256(
        b"\x00aim-leaf-v1\0" + base_row_digest + duplicate_ordinal.to_bytes(8, "big")
    ).digest()


def compute_node_hash(left: bytes, right: bytes) -> bytes:
    if len(left) != 32 or len(right) != 32:
        raise DatasetMerkleError("invalid_hash_encoding")
    return hashlib.sha256(b"\x01" + left + right).digest()


def largest_power_of_two_less_than(size: int) -> int:
    if size <= 1:
        raise DatasetMerkleError("invalid_tree_size")
    return 1 << ((size - 1).bit_length() - 1)


def build_merkle_root(leaves: Sequence[bytes]) -> bytes:
    if not leaves or len(leaves) > MAX_TREE_SIZE:
        raise DatasetMerkleError("invalid_tree_size")

    def root(start: int, size: int) -> bytes:
        if size == 1:
            leaf = leaves[start]
            if len(leaf) != 32:
                raise DatasetMerkleError("invalid_hash_encoding")
            return leaf
        split = largest_power_of_two_less_than(size)
        return compute_node_hash(root(start, split), root(start + split, size - split))

    return root(0, len(leaves))


def build_inclusion_proof(leaves: Sequence[bytes], leaf_index: int) -> list[dict[str, str]]:
    if not leaves or leaf_index < 0 or leaf_index >= len(leaves):
        raise DatasetMerkleError("invalid_inclusion_proof")

    def proof(start: int, size: int, index: int) -> list[dict[str, str]]:
        if size == 1:
            return []
        split = largest_power_of_two_less_than(size)
        if index < split:
            return proof(start, split, index) + [
                {
                    "hash": encode_base64url(build_merkle_root(leaves[start + split : start + size])),
                    "direction": "right",
                }
            ]
        return proof(start + split, size - split, index - split) + [
            {
                "hash": encode_base64url(build_merkle_root(leaves[start : start + split])),
                "direction": "left",
            }
        ]

    result = proof(0, len(leaves), leaf_index)
    if len(result) > MAX_PROOF_SIBLINGS:
        raise DatasetMerkleError("proof_bound_exceeded")
    return result


def verify_inclusion_proof(
    leaf_hash: bytes,
    leaf_index: int,
    tree_size: int,
    siblings: Sequence[dict[str, object] | object],
    expected_root: bytes,
) -> bool:
    try:
        if tree_size < 1 or tree_size > MAX_TREE_SIZE or not 0 <= leaf_index < tree_size:
            return False

        def directions(index: int, size: int) -> list[str]:
            if size == 1:
                return []
            split = largest_power_of_two_less_than(size)
            if index < split:
                return directions(index, split) + ["right"]
            return directions(index - split, size - split) + ["left"]

        expected_directions = directions(leaf_index, tree_size)
        if len(siblings) != len(expected_directions) or len(siblings) > MAX_PROOF_SIBLINGS:
            return False
        current = leaf_hash
        for sibling, expected_direction in zip(siblings, expected_directions):
            if isinstance(sibling, dict):
                direction, sibling_hash = sibling.get("direction"), sibling.get("hash")
            else:
                direction = getattr(sibling, "direction", None)
                sibling_hash = getattr(sibling, "hash", None)
            if direction != expected_direction:
                return False
            if isinstance(sibling_hash, str):
                from app.services.dataset_canonicalization import decode_base64url

                sibling_bytes = decode_base64url(sibling_hash, expected_size=32)
            else:
                sibling_bytes = bytes(sibling_hash)
            current = compute_node_hash(sibling_bytes, current) if direction == "left" else compute_node_hash(current, sibling_bytes)
        return len(expected_root) == 32 and hmac.compare_digest(current, expected_root)
    except (DatasetCanonicalizationError, DatasetMerkleError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class MerkleLeaf:
    record: CanonicalRecord
    duplicate_ordinal: int
    leaf_index: int
    leaf_hash: bytes


@dataclass(frozen=True)
class DatasetMerkleCommitment:
    schema_digest: bytes
    dataset_merkle_root: bytes
    leaves: tuple[MerkleLeaf, ...]

    @property
    def leaf_count(self) -> int:
        return len(self.leaves)

    def proof_for_index(self, leaf_index: int) -> list[dict[str, str]]:
        return build_inclusion_proof([leaf.leaf_hash for leaf in self.leaves], leaf_index)


def _record_sort_key(record: CanonicalRecord) -> tuple[bytes, bytes]:
    return record.base_row_digest, record.canonical_row_bytes


class _ExternalRecordSorter:
    """Bounded local sorter whose temporary files are always removed."""

    def __init__(self, source_path: Path, chunk_size: int) -> None:
        self.source_path = source_path
        self.chunk_size = max(1, chunk_size)
        self.workspace: Path | None = None
        self.chunks: list[Path] = []

    def __enter__(self) -> "_ExternalRecordSorter":
        parent = self.source_path.parent
        self.workspace = Path(tempfile.mkdtemp(prefix=".aim-merkle-", dir=parent))
        os.chmod(self.workspace, 0o700)
        return self

    def _spill(self, records: list[CanonicalRecord]) -> None:
        if self.workspace is None:
            raise DatasetMerkleError("temporary_workspace_unavailable")
        records.sort(key=_record_sort_key)
        path = self.workspace / f"chunk-{len(self.chunks):08d}.bin"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            for record in records:
                pickle.dump(record, handle, protocol=pickle.HIGHEST_PROTOCOL)
        self.chunks.append(path)

    @staticmethod
    def _read_chunk(path: Path) -> Iterator[CanonicalRecord]:
        with path.open("rb") as handle:
            while True:
                try:
                    yield pickle.load(handle)
                except EOFError:
                    return

    def sort(self, records: Iterable[CanonicalRecord]) -> Iterator[CanonicalRecord]:
        pending: list[CanonicalRecord] = []
        for record in records:
            pending.append(record)
            if len(pending) >= self.chunk_size:
                self._spill(pending)
                pending = []
        if pending:
            self._spill(pending)
        if not self.chunks:
            raise DatasetMerkleError("empty_dataset")
        iterators = [self._read_chunk(path) for path in self.chunks]
        heap: list[tuple[tuple[bytes, bytes], int, CanonicalRecord]] = []
        for index, iterator in enumerate(iterators):
            try:
                record = next(iterator)
            except StopIteration:
                continue
            heapq.heappush(heap, (_record_sort_key(record), index, record))
        while heap:
            _key, iterator_index, record = heapq.heappop(heap)
            yield record
            try:
                next_record = next(iterators[iterator_index])
            except StopIteration:
                continue
            heapq.heappush(heap, (_record_sort_key(next_record), iterator_index, next_record))

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        if self.workspace is not None:
            shutil.rmtree(self.workspace, ignore_errors=True)


class DatasetMerkleService:
    def __init__(self, *, external_sort_chunk_size: int = 50_000) -> None:
        self.external_sort_chunk_size = external_sort_chunk_size

    def build(
        self,
        records: Iterable[CanonicalRecord],
        *,
        schema_digest: bytes,
        source_path: Path | None = None,
    ) -> DatasetMerkleCommitment:
        if len(schema_digest) != 32:
            raise DatasetMerkleError("invalid_schema_digest")
        if source_path is None:
            sorted_records = sorted(records, key=_record_sort_key)
            return self._from_sorted_records(sorted_records, schema_digest)
        with _ExternalRecordSorter(source_path, self.external_sort_chunk_size) as sorter:
            return self._from_sorted_records(sorter.sort(records), schema_digest)

    @staticmethod
    def _from_sorted_records(
        sorted_records: Iterable[CanonicalRecord], schema_digest: bytes
    ) -> DatasetMerkleCommitment:
        leaves: list[MerkleLeaf] = []
        previous_key: tuple[bytes, bytes] | None = None
        duplicate_ordinal = 0
        for record in sorted_records:
            key = _record_sort_key(record)
            if key == previous_key:
                duplicate_ordinal += 1
            else:
                duplicate_ordinal = 0
                previous_key = key
            if len(leaves) >= MAX_TREE_SIZE:
                raise DatasetMerkleError("invalid_tree_size")
            leaves.append(
                MerkleLeaf(
                    record=record,
                    duplicate_ordinal=duplicate_ordinal,
                    leaf_index=len(leaves),
                    leaf_hash=compute_leaf_hash(record.base_row_digest, duplicate_ordinal),
                )
            )
        if not leaves:
            raise DatasetMerkleError("empty_dataset")
        root = build_merkle_root([leaf.leaf_hash for leaf in leaves])
        return DatasetMerkleCommitment(schema_digest, root, tuple(leaves))
