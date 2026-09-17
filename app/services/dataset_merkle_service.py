"""Cryptographic primitives for ``aim-dataset-merkle-v1`` and its log.

This module deliberately accepts digests and canonical bytes only. It has no
row-fetching, row-storage, or seller-origin networking surface.
"""

from __future__ import annotations

import contextlib
import fcntl
import heapq
import multiprocessing
import os
import shutil
import stat
import struct
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import base64
import binascii
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


SHA256_SIZE = 32
MAX_TREE_SIZE = (1 << 63) - 1
MAX_PROOF_SIBLINGS = 63
DATASET_PROFILE = "aim-dataset-merkle-v1"
_RFC3339_UTC_PATTERN = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)


class CommitmentValidationError(ValueError):
    """A stable, non-content validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_base64url(value: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise CommitmentValidationError("invalid_hash_encoding")
    try:
        ascii_value = value.encode("ascii")
    except UnicodeEncodeError:
        raise CommitmentValidationError("invalid_hash_encoding") from None
    padding = b"=" * ((4 - len(ascii_value) % 4) % 4)
    try:
        decoded = base64.b64decode(ascii_value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise CommitmentValidationError("invalid_hash_encoding") from None
    if encode_base64url(decoded) != value:
        raise CommitmentValidationError("invalid_hash_encoding")
    return decoded


def decode_digest(value: bytes | str, *, label: str = "digest") -> bytes:
    decoded = (
        bytes(value)
        if isinstance(value, (bytes, bytearray, memoryview))
        else decode_base64url(value)
    )
    if len(decoded) != SHA256_SIZE:
        raise CommitmentValidationError("invalid_hash_encoding")
    return decoded


def constant_time_digest_equal(left: bytes | str, right: bytes | str) -> bool:
    try:
        left_bytes = decode_digest(left)
        right_bytes = decode_digest(right)
    except (ValueError, CommitmentValidationError):
        return False
    return hmac.compare_digest(left_bytes, right_bytes)


def canonical_json_bytes(value: Any) -> bytes:
    """JCS safe-integer subset, controller ruling fba339b2; no row coercion."""

    def normalize(item):
        if item is None or type(item) is bool:
            return item
        if type(item) is int:
            if abs(item) > (1 << 53) - 1:
                raise CommitmentValidationError("unsafe_integer")
            return item
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise CommitmentValidationError("invalid_unicode") from None
            return item
        if isinstance(item, (list, tuple)):
            return [normalize(child) for child in item]
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise CommitmentValidationError("invalid_metadata")
            for key in item:
                normalize(key)
            keys = sorted(item)
            # The closed metadata vocabulary has ASCII object keys; user field
            # names live in arrays. Preserve reference v1 bytes and refuse any
            # generic object for which code-point and JCS UTF-16 orders differ.
            if keys != sorted(item, key=lambda k: k.encode("utf-16-be")):
                raise CommitmentValidationError("noncanonical_key_order")
            return {key: normalize(item[key]) for key in keys}
        raise CommitmentValidationError("invalid_metadata")

    try:
        return json.dumps(
            normalize(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except RecursionError:
        raise CommitmentValidationError("depth_limit") from None


def compute_schema_digest(canonical_schema_bytes: bytes) -> bytes:
    return hashlib.sha256(b"aim-schema-v1\0" + canonical_schema_bytes).digest()


def compute_base_row_digest(
    schema_digest: bytes | str, canonical_row_bytes: bytes
) -> bytes:
    return hashlib.sha256(
        b"aim-row-v1\0" + decode_digest(schema_digest) + b"\0" + canonical_row_bytes
    ).digest()


def compute_leaf_hash(base_row_digest: bytes | str, duplicate_ordinal: int) -> bytes:
    if not isinstance(duplicate_ordinal, int) or isinstance(duplicate_ordinal, bool):
        raise CommitmentValidationError("invalid_duplicate_ordinal")
    if duplicate_ordinal < 0 or duplicate_ordinal > MAX_TREE_SIZE:
        raise CommitmentValidationError("invalid_duplicate_ordinal")
    return hashlib.sha256(
        b"\x00aim-leaf-v1\0"
        + decode_digest(base_row_digest)
        + duplicate_ordinal.to_bytes(8, "big")
    ).digest()


def compute_node_hash(left_hash: bytes | str, right_hash: bytes | str) -> bytes:
    return hashlib.sha256(
        b"\x01" + decode_digest(left_hash) + decode_digest(right_hash)
    ).digest()


def largest_power_of_two_less_than(size: int) -> int:
    if type(size) is not int or size <= 1:
        raise CommitmentValidationError("invalid_tree_size")
    return 1 << ((size - 1).bit_length() - 1)


def build_merkle_root(leaves: Sequence[bytes | str]) -> bytes:
    if not leaves or len(leaves) > MAX_TREE_SIZE:
        raise CommitmentValidationError("invalid_tree_size")
    normalized = [decode_digest(leaf, label="leaf") for leaf in leaves]

    def root(start: int, size: int) -> bytes:
        if size == 1:
            return normalized[start]
        split = largest_power_of_two_less_than(size)
        return compute_node_hash(root(start, split), root(start + split, size - split))

    return root(0, len(normalized))


def build_inclusion_proof(
    leaves: Sequence[bytes | str], leaf_index: int
) -> list[dict[str, bytes | str]]:
    if (
        not leaves
        or len(leaves) > MAX_TREE_SIZE
        or leaf_index < 0
        or leaf_index >= len(leaves)
    ):
        raise CommitmentValidationError("invalid_inclusion_proof")
    normalized = [decode_digest(leaf, label="leaf") for leaf in leaves]

    def proof(start: int, size: int, index: int) -> list[dict[str, bytes | str]]:
        if size == 1:
            return []
        split = largest_power_of_two_less_than(size)
        if index < split:
            return proof(start, split, index) + [
                {
                    "hash": build_merkle_root(normalized[start + split : start + size]),
                    "direction": "right",
                }
            ]
        return proof(start + split, size - split, index - split) + [
            {
                "hash": build_merkle_root(normalized[start : start + split]),
                "direction": "left",
            }
        ]

    return proof(0, len(normalized), leaf_index)


def expected_proof_directions(leaf_index: int, tree_size: int) -> list[str]:
    if (
        type(tree_size) is not int
        or type(leaf_index) is not int
        or tree_size < 1
        or tree_size > MAX_TREE_SIZE
        or leaf_index < 0
        or leaf_index >= tree_size
    ):
        raise CommitmentValidationError("invalid_inclusion_proof")
    if tree_size == 1:
        return []
    split = largest_power_of_two_less_than(tree_size)
    if leaf_index < split:
        return expected_proof_directions(leaf_index, split) + ["right"]
    return expected_proof_directions(leaf_index - split, tree_size - split) + ["left"]


def verify_inclusion_proof(
    leaf_hash: bytes | str,
    leaf_index: int,
    tree_size: int,
    siblings: Sequence[Any],
    expected_root: bytes | str,
) -> bool:
    try:
        directions = expected_proof_directions(leaf_index, tree_size)
        if len(siblings) > MAX_PROOF_SIBLINGS or len(siblings) != len(directions):
            return False
        current = decode_digest(leaf_hash, label="leaf")
        for sibling, expected_direction in zip(siblings, directions, strict=True):
            if isinstance(sibling, dict):
                direction, sibling_hash = sibling.get("direction"), sibling.get("hash")
            else:
                direction = getattr(sibling, "direction", None)
                sibling_hash = getattr(sibling, "hash", None)
            if direction != expected_direction:
                return False
            decoded_sibling = decode_digest(sibling_hash, label="sibling")
            current = (
                compute_node_hash(decoded_sibling, current)
                if direction == "left"
                else compute_node_hash(current, decoded_sibling)
            )
        return constant_time_digest_equal(current, expected_root)
    except (TypeError, ValueError, CommitmentValidationError):
        return False


def canonical_log_entry_bytes(entry: dict[str, Any]) -> bytes:
    return canonical_json_bytes(entry)


def compute_log_entry_hash(entry_bytes: bytes) -> bytes:
    return hashlib.sha256(entry_bytes).digest()


def compute_log_leaf_hash(entry_bytes: bytes) -> bytes:
    return hashlib.sha256(b"\x00aim-log-leaf-v1\0" + entry_bytes).digest()


def build_log_root(log_leaves: Sequence[bytes | str]) -> bytes:
    return build_merkle_root(log_leaves)


def canonical_rfc3339_utc(value: datetime | str) -> str:
    """Render signed timestamps as UTC RFC3339 with exactly six fractional digits.

    This is the canonical signed-byte contract: aware datetimes are normalized
    to UTC, string inputs must already use the ``Z`` UTC form, and every output
    includes microseconds so equivalent inputs have one stable representation.
    """
    if isinstance(value, str):
        if _RFC3339_UTC_PATTERN.fullmatch(value) is None:
            raise ValueError("checkpoint timestamp must be UTC RFC3339")
        try:
            value = datetime.fromisoformat(f"{value[:-1]}+00:00")
        except ValueError as exc:
            raise ValueError("checkpoint timestamp must be UTC RFC3339") from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("checkpoint timestamp must be timezone aware")
    utc = value.astimezone(timezone.utc)
    return utc.isoformat(timespec="microseconds").replace("+00:00", "Z")


def checkpoint_signing_bytes(
    log_id: str,
    tree_size: int,
    root_hash: bytes | str,
    checkpoint_at: datetime | str,
) -> bytes:
    if (
        not isinstance(log_id, str)
        or not log_id
        or "\n" in log_id
        or type(tree_size) is not int
        or tree_size < 1
        or tree_size > MAX_TREE_SIZE
    ):
        raise CommitmentValidationError("invalid_checkpoint")
    return (
        "aim-transparency-checkpoint-v1\n"
        f"{log_id}\n"
        f"{tree_size}\n"
        f"{encode_base64url(decode_digest(root_hash))}\n"
        f"{canonical_rfc3339_utc(checkpoint_at)}\n"
    ).encode("utf-8")


def verify_checkpoint_signature(
    public_key: bytes | str,
    signature: bytes | str,
    message: bytes,
) -> bool:
    try:
        key_bytes = (
            bytes(public_key)
            if isinstance(public_key, (bytes, bytearray, memoryview))
            else decode_base64url(public_key)
        )
        signature_bytes = (
            bytes(signature)
            if isinstance(signature, (bytes, bytearray, memoryview))
            else decode_base64url(signature)
        )
        if len(key_bytes) != 32 or len(signature_bytes) != 64:
            return False
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature_bytes, message)
        return True
    except (ValueError, InvalidSignature):
        return False


def materialize_tree_nodes(
    leaves: Sequence[bytes | str],
) -> list[tuple[int, int, bytes]]:
    """Return immutable ``(zero_based_start, leaf_count, hash)`` nodes."""
    normalized = [decode_digest(leaf, label="leaf") for leaf in leaves]
    if not normalized:
        raise CommitmentValidationError("invalid_tree_size")
    nodes: list[tuple[int, int, bytes]] = []

    def visit(start: int, size: int) -> bytes:
        if size == 1:
            result = normalized[start]
        else:
            split = largest_power_of_two_less_than(size)
            result = compute_node_hash(
                visit(start, split), visit(start + split, size - split)
            )
        nodes.append((start, size, result))
        return result

    visit(0, len(normalized))
    return nodes


def build_consistency_proof(
    leaves: Sequence[bytes | str], old_size: int
) -> list[bytes]:
    normalized = [decode_digest(leaf, label="leaf") for leaf in leaves]
    new_size = len(normalized)
    if old_size < 1 or old_size > new_size:
        raise CommitmentValidationError("invalid_tree_size")
    if old_size == new_size:
        return []

    def subproof(start: int, first_size: int, size: int, complete: bool) -> list[bytes]:
        if first_size == size:
            return (
                []
                if complete
                else [build_merkle_root(normalized[start : start + size])]
            )
        split = largest_power_of_two_less_than(size)
        if first_size <= split:
            return subproof(start, first_size, split, complete) + [
                build_merkle_root(normalized[start + split : start + size])
            ]
        return subproof(start + split, first_size - split, size - split, False) + [
            build_merkle_root(normalized[start : start + split])
        ]

    return subproof(0, old_size, new_size, True)


def verify_consistency_proof(
    old_size: int,
    new_size: int,
    old_root: bytes | str,
    new_root: bytes | str,
    proof: Iterable[bytes | str],
) -> bool:
    try:
        old_root_bytes, new_root_bytes = (
            decode_digest(old_root),
            decode_digest(new_root),
        )
        hashes = [decode_digest(item) for item in proof]
        if old_size < 1 or old_size > new_size or new_size > MAX_TREE_SIZE:
            return False
        if old_size == new_size:
            return not hashes and hmac.compare_digest(old_root_bytes, new_root_bytes)
        fn, sn = old_size - 1, new_size - 1
        while fn & 1:
            fn >>= 1
            sn >>= 1
        if fn == 0:
            first_root = second_root = old_root_bytes
        else:
            if not hashes:
                return False
            first_root = second_root = hashes.pop(0)
        for item in hashes:
            if sn == 0:
                return False
            if (fn & 1) or fn == sn:
                first_root = compute_node_hash(item, first_root)
                second_root = compute_node_hash(item, second_root)
                while fn and not (fn & 1):
                    fn >>= 1
                    sn >>= 1
            else:
                second_root = compute_node_hash(second_root, item)
            fn >>= 1
            sn >>= 1
        return (
            sn == 0
            and hmac.compare_digest(first_root, old_root_bytes)
            and hmac.compare_digest(second_root, new_root_bytes)
        )
    except (TypeError, ValueError, CommitmentValidationError):
        return False


# Production path: external merge runs and disk-indexed tree. The small sequence
# primitives above intentionally remain reference-compatible fixture helpers.


@dataclass(frozen=True)
class WorkerBudget:
    rss_bytes: int = 512 * 1024 * 1024
    run_bytes: int = 64 * 1024 * 1024
    batch_bytes: int = 16 * 1024 * 1024
    record_bytes: int = 8 * 1024 * 1024
    disk_bytes: int = 20 * 1024**3
    reserve_bytes: int = 1024**3

    def validate(self):
        if any(type(v) is not int or v < 1 for v in vars(self).values()):
            raise CommitmentValidationError("invalid_budget")
        if (
            self.run_bytes > 64 * 1024**2
            or self.batch_bytes > 16 * 1024**2
            or self.record_bytes > 8 * 1024**2
        ):
            raise CommitmentValidationError("invalid_budget")


class _DiskBudget:
    def __init__(self, directory, budget, cancel):
        self.directory, self.budget, self.cancel = directory, budget, cancel
        self.used = 0
        self.free_remaining = shutil.disk_usage(directory).free - budget.reserve_bytes
        self.next_probe = 0

    def check(self, amount=0):
        if self.cancel is not None and self.cancel.is_set():
            raise CommitmentValidationError("cancelled")
        if self.used >= self.next_probe:
            self.free_remaining = (
                shutil.disk_usage(self.directory).free - self.budget.reserve_bytes
            )
            self.next_probe = self.used + 1024 * 1024
        if self.used + amount > self.budget.disk_bytes or amount > self.free_remaining:
            raise CommitmentValidationError("disk_resource_limit")
        self.free_remaining -= amount
        self.used += amount

    def remove(self, path):
        self.used -= path.stat().st_size
        self.next_probe = 0
        path.unlink()


def _private_file(path, mode="w+b"):
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    return os.fdopen(os.open(path, flags, 0o600), mode)


def _run_records(stream, limit):
    while header := stream.read(36):
        if len(header) != 36:
            raise CommitmentValidationError("invalid_spill")
        size = struct.unpack(">I", header[32:])[0]
        if size > limit:
            raise CommitmentValidationError("record_resource_limit")
        row = stream.read(size)
        if len(row) != size:
            raise CommitmentValidationError("invalid_spill")
        yield header[:32], row


def _write_record(stream, pair, disk):
    base, row = pair
    disk.check(36 + len(row))
    stream.write(base + struct.pack(">I", len(row)) + row)


class DiskTree:
    """Job-scoped, private index. Never persist or export its row-bearing files."""

    def __init__(self, directory, count, root, schema_digest, canonical_bytes):
        self.directory, self.count, self.root = directory, count, root
        self.schema_digest, self.canonical_bytes = schema_digest, canonical_bytes

    def proof(self, index):
        if type(index) is not int or not 0 <= index < self.count:
            raise CommitmentValidationError("invalid_inclusion_proof")
        siblings = []
        with (self.directory / "nodes").open("rb") as nodes:
            position, start, size = 0, 0, self.count
            while size > 1:
                split = largest_power_of_two_less_than(size)
                if index < start + split:
                    sibling_position = position + 2 * split
                    direction = "right"
                    position += 1
                    size = split
                else:
                    sibling_position = position + 1
                    direction = "left"
                    position += 2 * split
                    start += split
                    size -= split
                nodes.seek(sibling_position * 32)
                siblings.append(
                    {"hash": encode_base64url(nodes.read(32)), "direction": direction}
                )
        with (self.directory / "index").open("rb") as entries:
            entries.seek(index * 56)
            offset, length, ordinal, base = struct.unpack(">QIQ32s4x", entries.read(56))
        return {
            "base_row_digest": encode_base64url(base),
            "duplicate_ordinal": ordinal,
            "leaf_index": index,
            "tree_size": self.count,
            "siblings": list(reversed(siblings)),
        }

    def commitment(self):
        # This metadata serialization also enforces the controller safe range.
        result = {
            "profile": DATASET_PROFILE,
            "schema_digest": encode_base64url(self.schema_digest),
            "dataset_merkle_root": encode_base64url(self.root),
            "leaf_count": self.count,
        }
        canonical_json_bytes(result)
        return result


def build_disk_tree(
    canonical_rows, schema_digest, directory, *, budget=None, cancel=None, progress=None
):
    """Consume all rows, external-sort, build <=2N-1 disk hashes; no prefix result."""
    budget = budget or WorkerBudget()
    budget.validate()
    directory = Path(directory)
    disk = _DiskBudget(directory, budget, cancel)
    started, count, canonical_bytes = time.monotonic(), 0, 0

    def report(phase):
        if progress:
            progress(
                {
                    "phase": phase,
                    "records": count,
                    "canonical_bytes": canonical_bytes,
                    "elapsed_seconds": time.monotonic() - started,
                }
            )

    report("reading")
    runs, batch, used = [], [], 0

    def spill():
        nonlocal batch, used
        if not batch:
            return
        disk.check()
        batch.sort()
        path = directory / f"run-{len(runs)}"
        with _private_file(path) as stream:
            for item in batch:
                _write_record(stream, item, disk)
        runs.append(path)
        batch, used = [], 0

    for row in canonical_rows:
        disk.check()
        if not isinstance(row, bytes) or len(row) > budget.record_bytes:
            raise CommitmentValidationError("record_resource_limit")
        pair = (compute_base_row_digest(schema_digest, row), row)
        cost = sys.getsizeof(row) + sys.getsizeof(pair) + sys.getsizeof(pair[0]) + 16
        if used + cost > budget.run_bytes:
            spill()
        batch.append(pair)
        used += cost
        count += 1
        canonical_bytes += len(row)
        if count > MAX_TREE_SIZE:
            raise CommitmentValidationError("invalid_tree_size")
        if count % 10000 == 0:
            report("reading")
    spill()
    if not count:
        raise CommitmentValidationError("invalid_tree_size")
    report("sorting")
    generation = 0
    # Fixed fan-in bounds open files and retained maximum-sized records.
    while len(runs) > 1:
        merged = []
        for group_start in range(0, len(runs), 4):
            group = runs[group_start : group_start + 4]
            output = directory / f"merge-{generation}-{group_start}"
            with contextlib.ExitStack() as stack:
                streams = [stack.enter_context(path.open("rb")) for path in group]
                dest = stack.enter_context(_private_file(output))
                for pair in heapq.merge(
                    *[_run_records(stream, budget.record_bytes) for stream in streams]
                ):
                    _write_record(dest, pair, disk)
            for path in group:
                disk.remove(path)
            merged.append(output)
        runs = merged
        generation += 1
        report("sorting")
    report("building_tree")
    last, ordinal = None, 0
    with (
        runs[0].open("rb") as ordered,
        _private_file(directory / "leaves") as leaves,
        _private_file(directory / "index") as index,
    ):
        offset = 0
        for pair in _run_records(ordered, budget.record_bytes):
            base, row = pair
            ordinal = ordinal + 1 if pair == last else 0
            disk.check(32 + 56)
            leaves.write(compute_leaf_hash(base, ordinal))
            index.write(struct.pack(">QIQ32s4x", offset, len(row), ordinal, base))
            offset += 36 + len(row)
            last = pair
    os.rename(runs[0], directory / "rows")
    with (
        (directory / "leaves").open("rb") as leaves,
        _private_file(directory / "nodes") as nodes,
    ):
        disk.check((2 * count - 1) * 32)

        def build(start, size, position):
            disk.check()
            if size == 1:
                leaves.seek(start * 32)
                result = leaves.read(32)
            else:
                split = largest_power_of_two_less_than(size)
                result = compute_node_hash(
                    build(start, split, position + 1),
                    build(start + split, size - split, position + 2 * split),
                )
            nodes.seek(position * 32)
            nodes.write(result)
            return result

        root = build(0, count, 0)
    disk.remove(directory / "leaves")
    return DiskTree(directory, count, root, schema_digest, canonical_bytes)


@contextlib.contextmanager
def private_job(root):
    """Installation-wide lock and recovery. Root must be dedicated to this service."""
    root = Path(root).absolute()
    if root.resolve() != root:
        raise CommitmentValidationError("unsafe_temp_directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.getuid()
    ):
        raise CommitmentValidationError("unsafe_temp_directory")
    fd = os.open(root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CommitmentValidationError("job_already_running") from None
        # Lock excludes live jobs: these prefixes belong only to this service.
        for old in root.glob("job-*"):
            if old.is_symlink():
                old.unlink()
            elif old.is_dir():
                shutil.rmtree(old)
        directory = Path(tempfile.mkdtemp(prefix="job-", dir=root))
        try:
            yield directory
        finally:
            shutil.rmtree(directory)
    finally:
        os.close(fd)


def _worker(
    connection, cancel, directory, paths, declaration, descriptors, budget, indices
):
    from app.services.dataset_canonicalization import CanonicalSchema, iter_records

    try:
        schema = CanonicalSchema(descriptors)

        started = time.monotonic()
        snapshots = [(path, os.stat(path, follow_symlinks=False)) for path in paths]

        def rows():
            for path, _ in snapshots:
                for record in iter_records(Path(path), declaration, schema):
                    yield schema.canonical_row(
                        record,
                        text=declaration.format in {"csv", "tsv"},
                        source_timezone=declaration.source_timezone,
                    )

        tree = build_disk_tree(
            rows(),
            schema.digest,
            Path(directory),
            budget=budget,
            cancel=cancel,
            progress=lambda p: connection.send(("progress", p)),
        )
        connection.send(
            (
                "progress",
                {
                    "phase": "selecting",
                    "records": tree.count,
                    "canonical_bytes": tree.canonical_bytes,
                    "elapsed_seconds": time.monotonic() - started,
                },
            )
        )
        proofs = [tree.proof(index) for index in indices]
        for path, before in snapshots:
            after = os.stat(path, follow_symlinks=False)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise CommitmentValidationError("source_changed")

        if cancel.is_set():
            raise CommitmentValidationError("cancelled")
        connection.send(("result", {"commitment": tree.commitment(), "proofs": proofs}))
    except BaseException as exc:
        connection.send(
            (
                "error",
                exc.code
                if isinstance(exc, CommitmentValidationError)
                else "worker_failed",
            )
        )
    finally:
        connection.close()


def run_commitment_job(
    paths,
    declaration,
    descriptors,
    temp_root,
    *,
    indices=(),
    budget=None,
    cancel=None,
    progress=None,
    local_review=None,
):
    """Separate process, incremental RSS watchdog, private cleanup on every exit.

    Only closed digest/proof metadata crosses the pipe. No signing or publishing.
    Caller cancellation is a threading.Event-compatible object.
    """
    import psutil

    budget = budget or WorkerBudget()
    budget.validate()
    if len(indices) > 100 or len(set(indices)) != len(indices):
        raise CommitmentValidationError("invalid_selection")
    if not paths or len(set(map(str, paths))) != len(paths):
        raise CommitmentValidationError("invalid_source_manifest")
    with private_job(temp_root) as directory:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        worker_cancel = context.Event()
        process = context.Process(
            target=_worker,
            args=(
                child,
                worker_cancel,
                directory,
                tuple(map(str, paths)),
                declaration,
                descriptors,
                budget,
                tuple(indices),
            ),
        )
        process.start()
        child.close()
        monitor = psutil.Process(process.pid)
        initial_rss = monitor.memory_info().rss
        peak_rss = initial_rss
        result = None
        started = time.monotonic()
        last_bytes = 0
        try:
            while process.is_alive() or parent.poll():
                if cancel is not None and cancel.is_set():
                    worker_cancel.set()
                    raise CommitmentValidationError("cancelled")
                try:
                    current = monitor.memory_info().rss
                    peak_rss = max(peak_rss, current)
                    if current - initial_rss > budget.rss_bytes:
                        worker_cancel.set()
                        raise CommitmentValidationError("resource_limit")
                except psutil.NoSuchProcess:
                    pass
                if parent.poll(0.02):
                    try:
                        kind, payload = parent.recv()
                    except EOFError:
                        break
                    if kind == "error":
                        raise CommitmentValidationError(payload)
                    if kind == "progress":
                        last_bytes = payload["canonical_bytes"]
                        if progress:
                            progress(payload)
                    if kind == "result":
                        result = payload
            process.join(timeout=1)
            if result is None or process.exitcode != 0:
                raise CommitmentValidationError("worker_failed")
            if progress:
                progress(
                    {
                        "phase": "ready",
                        "records": result["commitment"]["leaf_count"],
                        "canonical_bytes": last_bytes,
                        "elapsed_seconds": time.monotonic() - started,
                    }
                )
            result["peak_rss_bytes"] = peak_rss
            result["incremental_rss_bytes"] = peak_rss - initial_rss
            if local_review is not None:
                # Keep the installation lock and private index only for this live
                # review session. Restart rebuilds; rows never enter the journal.
                tree = DiskTree(directory, result["commitment"]["leaf_count"],
                                decode_digest(result["commitment"]["dataset_merkle_root"]),
                                decode_digest(result["commitment"]["schema_digest"]), last_bytes)
                local_review(tree, result)
            return result
        finally:
            if process.is_alive():
                worker_cancel.set()
                process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join()
            parent.close()
            process.close()
