"""Seller-local, deterministic v2 package construction and atomic export.

Use a live Chunk 2a private job/index; selection is fixed in ascending leaf order.
No broker, marketplace client, signing or provider write authority is involved.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.models.dataset_commitment_schemas import CommitmentProof
from app.services.dataset_canonicalization import CanonicalSchema
from app.services.dataset_merkle_service import (
    canonical_json_bytes,
    compute_base_row_digest,
    compute_leaf_hash,
    decode_digest,
    encode_base64url,
    verify_inclusion_proof,
)
from app.services.preview_content_policy import NumericText, scan_selection

PROFILE = "aim-preview-package-v2"
MEDIA_TYPE = "application/vnd.aim.preview+json"
OBJECT_METADATA = {"Content-Type": MEDIA_TYPE, "Cache-Control": "no-store"}
CAPS = {
    "rows": 100,
    "fields": 25,
    "canonical_bytes": 250000,
    "envelope_bytes": 1048576,
    "manifest_bytes": 262144,
    "siblings": 63,
    "depth": 16,
    "nodes": 10000,
}
ENVELOPE_KEYS = {
    "package_profile",
    "commitment_id",
    "schema_digest",
    "disclosure_version",
    "sample_hash",
    "entries",
}
ENTRY_KEYS = {
    "proof_id",
    "row",
    "base_row_digest",
    "duplicate_ordinal",
    "leaf_index",
    "tree_size",
    "siblings",
}


class PackageError(ValueError):
    pass


def limit(kind, amount):
    if type(amount) is not int or amount < 0 or amount > CAPS[kind]:
        raise PackageError(kind + "_limit")


def canonical_uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise PackageError("invalid_identity") from None
    return value


def value_budget(value):
    """Count every container/scalar/null in the whole envelope; keys are not nodes."""
    nodes = 0
    stack = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        limit("nodes", nodes)
        limit("depth", depth)
        if type(current) is dict:
            stack.extend((child, depth + 1) for child in current.values())
        elif type(current) is list:
            stack.extend((child, depth + 1) for child in current)
        elif current is not None and type(current) not in (str, int, bool):
            raise PackageError("invalid_value")
    return nodes


def sample_hash(entries):
    values = [
        [
            canonical_uuid(p["proof_id"]),
            decode_digest(p["base_row_digest"]).hex(),
            p["duplicate_ordinal"],
            p["leaf_index"],
        ]
        for p in entries
    ]
    return hashlib.sha256(
        b"aim-approved-sample-v1\0" + canonical_json_bytes(values)
    ).hexdigest()


def _logical_record(triples, fields, *, for_scan=False):
    result = {}
    for (name, tag, value), descriptor in zip(triples, fields, strict=True):
        if name != descriptor[0]:
            raise PackageError("invalid_index")
        if tag != "missing":
            result[name] = _logical_value(value, tag, descriptor[3], for_scan)
    return result


def _logical_value(value, tag, params, for_scan):
    if tag == "binary":
        raise PackageError("binary_selection")
    if value is None:
        return None
    if tag == "object":
        fields = [
            [f["name"], f["type"], f["nullable"], f["type_parameters"]]
            for f in params["object_fields"]
        ]
        return _logical_record(value, fields, for_scan=for_scan)
    if tag == "array":
        elem = params["element_type"]
        return [
            _logical_value(v, elem["type"], elem["type_parameters"], for_scan)
            for v in value
        ]
    if for_scan and tag in {"signed_integer", "decimal"}:
        return NumericText(value)
    return value


def validate_envelope(envelope, schema, root, *, manifest_bytes):
    """Validate all conjunctive budgets and identities before any publication."""
    try:
        if (
            type(envelope) is not dict
            or set(envelope) != ENVELOPE_KEYS
            or envelope["package_profile"] != PROFILE
        ):
            raise PackageError("invalid_envelope")
        canonical_uuid(envelope["commitment_id"])
        canonical_uuid(envelope["disclosure_version"])
        if envelope["schema_digest"] != encode_base64url(schema.digest):
            raise PackageError("schema_mismatch")
        entries = envelope["entries"]
        if type(entries) is not list or not entries:
            raise PackageError("invalid_selection")
        limit("rows", len(entries))
        limit("fields", len(schema.descriptors))
        limit("manifest_bytes", manifest_bytes)
        value_budget(envelope)
        ids, indices, sizes, canonical_bytes = set(), [], set(), 0
        for entry in entries:
            if type(entry) is not dict or set(entry) != ENTRY_KEYS:
                raise PackageError("invalid_entry")
            proof_id = canonical_uuid(entry["proof_id"])
            if proof_id in ids:
                raise PackageError("duplicate_proof")
            ids.add(proof_id)
            limit("siblings", len(entry["siblings"]))
            proof = CommitmentProof.model_validate(
                {k: v for k, v in entry.items() if k not in {"row", "proof_id"}}
            )
            indices.append(proof.leaf_index)
            sizes.add(proof.tree_size)
            limit("fields", len(entry["row"]))
            row_bytes = schema.canonical_row(entry["row"])
            # Binary is forbidden at any selected position, including null binary fields.
            _logical_record(json.loads(row_bytes), schema.descriptors)
            canonical_bytes += len(row_bytes)
            limit("canonical_bytes", canonical_bytes)
            if compute_base_row_digest(schema.digest, row_bytes) != decode_digest(
                proof.base_row_digest
            ):
                raise PackageError("row_mismatch")
            if not verify_inclusion_proof(
                compute_leaf_hash(proof.base_row_digest, proof.duplicate_ordinal),
                proof.leaf_index,
                proof.tree_size,
                entry["siblings"],
                root,
            ):
                raise PackageError("invalid_proof")
        if len(sizes) != 1 or indices != sorted(set(indices)):
            raise PackageError("invalid_selection")
        if envelope["sample_hash"] != sample_hash(entries):
            raise PackageError("sample_hash_mismatch")
        encoded = canonical_json_bytes(envelope)
        limit("envelope_bytes", len(encoded))
        return encoded
    except PackageError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise PackageError("invalid_package") from None


def decode_envelope(raw, *, schema, root, manifest_bytes, content_encoding="identity"):
    limit("envelope_bytes", len(raw))
    if content_encoding != "identity":
        raise PackageError("unsupported_encoding")

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise PackageError("duplicate_key")
            out[key] = value
        return out

    try:
        envelope = json.loads(raw, object_pairs_hook=pairs)
        validate_envelope(envelope, schema, root, manifest_bytes=manifest_bytes)
        return envelope
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise PackageError("invalid_package") from None


@dataclass(frozen=True)
class PreparedPackage:
    payload: bytes
    scan: dict


class CommitmentPreviewBuilder:
    def __init__(self, tree, descriptors):
        self.tree = tree
        self.schema = CanonicalSchema(descriptors)
        if self.schema.digest != tree.schema_digest:
            raise PackageError("schema_mismatch")

    def page(self, start=0, count=25):
        if (
            type(start) is not int
            or type(count) is not int
            or start < 0
            or not 1 <= count <= 100
        ):
            raise PackageError("invalid_selection")
        return [
            self._entry(i) for i in range(start, min(start + count, self.tree.count))
        ]

    def _entry(self, index):
        try:
            proof = self.tree.proof(index)
            with (self.tree.directory / "index").open("rb") as stream:
                stream.seek(index * 56)
                offset, length, ordinal, base = struct.unpack(
                    ">QIQ32s4x", stream.read(56)
                )
            if length > 8 * 1024**2:
                raise PackageError("invalid_index")
            with (self.tree.directory / "rows").open("rb") as stream:
                stream.seek(offset)
                header, row_bytes = stream.read(36), stream.read(length)
            if header != base + struct.pack(">I", length) or len(row_bytes) != length:
                raise PackageError("invalid_index")
            if compute_base_row_digest(self.schema.digest, row_bytes) != base:
                raise PackageError("invalid_index")
            row = _logical_record(json.loads(row_bytes), self.schema.descriptors)
            if self.schema.canonical_row(row) != row_bytes:
                raise PackageError("invalid_index")
            return {"row": row, **proof}
        except (OSError, ValueError, KeyError, struct.error):
            raise PackageError("invalid_index") from None

    def prepare(
        self,
        indices,
        *,
        proof_ids,
        commitment_id,
        disclosure_version,
        detector,
        scanned_at,
        rights_confirmed,
        manifest_bytes,
    ):
        if (
            not indices
            or any(type(i) is not int for i in indices)
            or list(indices) != sorted(set(indices))
            or len(indices) != len(proof_ids)
        ):
            raise PackageError("invalid_selection")
        limit("rows", len(indices))
        entries = [
            {"proof_id": pid, **self._entry(i)}
            for i, pid in zip(indices, proof_ids, strict=True)
        ]
        envelope = {
            "package_profile": PROFILE,
            "commitment_id": commitment_id,
            "schema_digest": encode_base64url(self.schema.digest),
            "disclosure_version": disclosure_version,
            "sample_hash": sample_hash(entries),
            "entries": entries,
        }
        payload = validate_envelope(
            envelope, self.schema, self.tree.root, manifest_bytes=manifest_bytes
        )
        rows = [
            _logical_record(
                json.loads(self.schema.canonical_row(e["row"])),
                self.schema.descriptors,
                for_scan=True,
            )
            for e in entries
        ]
        scan = scan_selection(
            rows,
            entries,
            detector=detector,
            scanned_at=scanned_at,
            rights_confirmed=rights_confirmed,
        )
        return PreparedPackage(payload, scan)


@contextlib.contextmanager
def directory_fd(path, *, private=False):
    """Traverse each component with O_NOFOLLOW, retaining the directory handle."""
    path = Path(path).absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            if component in {".", ".."}:
                raise PackageError("unsafe_directory")
            try:
                os.mkdir(component, 0o700 if private else 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        if private and (
            stat.S_IMODE(os.fstat(fd).st_mode) != 0o700
            or os.fstat(fd).st_uid != os.getuid()
        ):
            raise PackageError("unsafe_directory")
        yield fd
    except OSError:
        raise PackageError("publication_io") from None
    finally:
        os.close(fd)


def atomic_write(fd, name, data, mode=0o600):
    temporary = ".pending-" + uuid.uuid4().hex
    out = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd
    )
    try:
        with os.fdopen(out, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=fd)


class PublicationStore:
    """Private journal authorizes only exact hashes; export alone is not hosting."""

    def __init__(self, public_root, journal_root):
        self.public_root, self.journal_root = (
            Path(public_root).absolute(),
            Path(journal_root).absolute(),
        )
        if self.journal_root.resolve().is_relative_to(self.public_root.resolve()):
            raise PackageError("journal_in_public_root")
        with (
            directory_fd(self.public_root),
            directory_fd(self.journal_root, private=True),
        ):
            pass

    @contextlib.contextmanager
    def locked(self):
        with directory_fd(self.journal_root, private=True) as fd:
            lock = os.open(
                ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd
            )
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield fd
            finally:
                os.close(lock)

    @staticmethod
    def path(disclosure, digest):
        canonical_uuid(disclosure)
        if type(digest) is not str or re.fullmatch("[0-9a-f]{64}", digest) is None:
            raise PackageError("invalid_identity")
        return f"previews/{disclosure}/{digest}.json"

    def _read(self, fd, disclosure, digest):
        name = canonical_uuid(disclosure) + "-" + digest + ".json"
        self.path(disclosure, digest)
        try:
            source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(source, "rb") as stream:
                return json.loads(stream.read(8193))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise PackageError("invalid_journal") from None

    def export(self, package):
        if not isinstance(package, PreparedPackage):
            raise PackageError("approval_required")
        envelope = json.loads(package.payload)
        disclosure, digest = envelope["disclosure_version"], envelope["sample_hash"]
        relative = self.path(disclosure, digest)
        record = {
            "commitment_id": envelope["commitment_id"],
            "disclosure_version": disclosure,
            "schema_digest": envelope["schema_digest"],
            "sample_hash": digest,
            "package_sha256": hashlib.sha256(package.payload).hexdigest(),
            "byte_count": len(package.payload),
            "state": "exported",
        }
        with self.locked() as journal:
            old = self._read(journal, disclosure, digest)
            if old is not None and old != record:
                raise PackageError("immutable_publication")
            with directory_fd((self.public_root / relative).parent) as target:
                atomic_write(target, digest + ".json", package.payload, 0o644)
            atomic_write(
                journal,
                disclosure + "-" + digest + ".json",
                canonical_json_bytes(record),
            )
        return {**record, "object_metadata": dict(OBJECT_METADATA)}

    def retire(self, disclosure, digest):
        relative = self.path(disclosure, digest)
        with self.locked() as journal:
            record = self._read(journal, disclosure, digest)
            if record is None:
                raise PackageError("unknown_publication")
            record["state"] = "retired"
            # Tombstone first: a crash must never restore eligibility.
            atomic_write(
                journal,
                disclosure + "-" + digest + ".json",
                canonical_json_bytes(record),
            )
            with directory_fd((self.public_root / relative).parent) as target:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(digest + ".json", dir_fd=target)
                os.fsync(target)
        return record

    def read(self, disclosure, digest):
        relative = self.path(disclosure, digest)
        with self.locked() as journal:
            record = self._read(journal, disclosure, digest)
            if record is None:
                return 404, b""
            if record["state"] == "retired":
                return 410, b""
            try:
                with directory_fd((self.public_root / relative).parent) as target:
                    source = os.open(
                        digest + ".json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=target
                    )
                    with os.fdopen(source, "rb") as stream:
                        payload = stream.read(CAPS["envelope_bytes"] + 1)
                if (
                    len(payload) != record["byte_count"]
                    or hashlib.sha256(payload).hexdigest() != record["package_sha256"]
                ):
                    return 404, b""
                return 200, payload
            except (OSError, PackageError):
                return 404, b""
