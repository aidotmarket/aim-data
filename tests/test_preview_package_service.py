import hashlib
import json
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services.dataset_canonicalization import CanonicalSchema
from app.services.dataset_merkle_service import build_disk_tree, canonical_json_bytes
from app.services.preview_package_service import (
    CAPS,
    CommitmentPreviewBuilder,
    PackageError,
    PublicationStore,
    decode_envelope,
    limit,
    sample_hash,
    validate_envelope,
    value_budget,
)

FIXTURES = Path("tests/fixtures")


@pytest.fixture
def golden():
    return json.loads((FIXTURES / "aim_dataset_merkle_v1.json").read_text())


@pytest.fixture
def envelope():
    return json.loads((FIXTURES / "aim_preview_package_v2.json").read_text())


@pytest.fixture
def builder(tmp_path, golden):
    schema = CanonicalSchema(golden["canonical_schema"])
    private = tmp_path / "tree"
    private.mkdir()
    tree = build_disk_tree(
        (canonical_json_bytes(r["canonical_row"]) for r in golden["rows"]),
        schema.digest,
        private,
    )
    return CommitmentPreviewBuilder(tree, schema.descriptors)


def prepare(builder, envelope):
    return builder.prepare(
        list(range(5)),
        proof_ids=[e["proof_id"] for e in envelope["entries"]],
        commitment_id=envelope["commitment_id"],
        disclosure_version=envelope["disclosure_version"],
        detector=Mock(),
        scanned_at="2026-09-17T12:00:00Z",
        rights_confirmed=True,
        public_preview_permission=True,
        restricted_content_confirmed=True,
        package_url="https://seller.example/p",
        manifest_bytes=20000,
    )


def test_byte_exact_golden(builder, envelope):
    package = prepare(builder, envelope)
    assert package.payload == (FIXTURES / "aim_preview_package_v2.json").read_bytes()
    assert "name" not in envelope["entries"][0]["row"]
    assert envelope["entries"][1]["row"]["name"] is None
    assert builder.page(2, 2)[0]["row"] == builder.page(2, 2)[1]["row"]
    assert (
        builder.page(2, 2)[0]["duplicate_ordinal"]
        != builder.page(2, 2)[1]["duplicate_ordinal"]
    )


def test_sample_hash_vectors(envelope):
    vectors = json.loads((FIXTURES / "aim_preview_package_vectors_v1.json").read_text())
    assert sample_hash(envelope["entries"]) == vectors["sample_hash"]
    assert (
        sample_hash(list(reversed(envelope["entries"])))
        == vectors["reversed_sample_hash"]
    )
    assert (
        hashlib.sha256(bytes.fromhex(vectors["sample_preimage_hex"])).hexdigest()
        == vectors["sample_hash"]
    )


@pytest.mark.parametrize("kind", list(CAPS))
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_all_cap_comparisons(kind, offset):
    value = CAPS[kind] + offset
    if offset > 0:
        with pytest.raises(PackageError, match=kind + "_limit"):
            limit(kind, value)
    else:
        limit(kind, value)


@pytest.mark.parametrize("nodes", [9999, 10000, 10001])
def test_total_node_boundaries(nodes):
    value = [None] * (nodes - 1)
    if nodes > 10000:
        with pytest.raises(PackageError, match="nodes_limit"):
            value_budget(value)
    else:
        assert value_budget(value) == nodes


@pytest.mark.parametrize("depth", [15, 16, 17])
def test_depth_boundaries(depth):
    value = None
    for _ in range(depth):
        value = [value]
    if depth > 16:
        with pytest.raises(PackageError, match="depth_limit"):
            value_budget(value)
    else:
        value_budget(value)


@pytest.mark.parametrize("count", [99, 100, 101])
def test_real_row_boundaries(tmp_path, count):
    schema = CanonicalSchema([["value", "string", False, {}]])
    tree = build_disk_tree(
        (schema.canonical_row({"value": "oats"}) for _ in range(count)),
        schema.digest,
        tmp_path,
    )
    builder = CommitmentPreviewBuilder(tree, schema.descriptors)
    kwargs = dict(
        proof_ids=[str(uuid.UUID(int=i + 100)) for i in range(count)],
        commitment_id=str(uuid.UUID(int=1)),
        disclosure_version=str(uuid.UUID(int=2)),
        detector=Mock(),
        scanned_at="2026-09-17T00:00:00Z",
        rights_confirmed=True,
        public_preview_permission=True,
        restricted_content_confirmed=True,
        package_url="https://seller.example/p",
        manifest_bytes=200000,
    )
    if count > 100:
        with pytest.raises(PackageError, match="rows_limit"):
            builder.prepare(list(range(count)), **kwargs)
    else:
        # Row count alone passes; the maximal duplicated manifest does not.
        limit("rows", count)
        with pytest.raises(PackageError, match="manifest_bytes_limit"):
            builder.prepare(list(range(count)), **kwargs)


@pytest.mark.parametrize("count", [24, 25, 26])
def test_complete_schema_field_boundaries(tmp_path, count):
    schema = CanonicalSchema([[f"f{i:02}", "string", True, {}] for i in range(count)])
    tree = build_disk_tree([schema.canonical_row({})], schema.digest, tmp_path)
    builder = CommitmentPreviewBuilder(tree, schema.descriptors)
    kwargs = dict(
        proof_ids=[str(uuid.UUID(int=3))],
        commitment_id=str(uuid.UUID(int=1)),
        disclosure_version=str(uuid.UUID(int=2)),
        detector=Mock(),
        scanned_at="2026-09-17T00:00:00Z",
        rights_confirmed=True,
        public_preview_permission=True,
        restricted_content_confirmed=True,
        package_url="https://seller.example/p",
        manifest_bytes=1000,
    )
    if count > 25:
        with pytest.raises(PackageError, match="fields_limit"):
            builder.prepare([0], **kwargs)
    else:
        builder.prepare([0], **kwargs)


@pytest.mark.parametrize("size", [1048575, 1048576, 1048577])
def test_received_envelope_boundaries(envelope, golden, size):
    raw = canonical_json_bytes(envelope)
    raw += b" " * (size - len(raw))
    kwargs = dict(
        schema=CanonicalSchema(golden["canonical_schema"]),
        root=golden["dataset_merkle_root"],
        manifest_bytes=1000,
    )
    if size > 1048576:
        with pytest.raises(PackageError, match="envelope_bytes_limit"):
            decode_envelope(raw, **kwargs)
    else:
        assert decode_envelope(raw, **kwargs) == envelope


@pytest.mark.parametrize(
    "change",
    [
        "extra",
        "row",
        "hash",
        "order",
        "duplicate",
        "tree",
        "schema",
        "sibling",
        "profile",
    ],
)
def test_envelope_mutations(envelope, golden, change):
    if change == "extra":
        envelope["notes"] = "synthetic"
    if change == "row":
        envelope["entries"][0]["row"]["id"] = "77"
    if change == "hash":
        envelope["sample_hash"] = "0" * 64
    if change == "order":
        envelope["entries"].reverse()
    if change == "duplicate":
        envelope["entries"][1]["proof_id"] = envelope["entries"][0]["proof_id"]
    if change == "tree":
        envelope["entries"][0]["tree_size"] = 6
    if change == "schema":
        envelope["schema_digest"] = "A" * 43
    if change == "sibling":
        envelope["entries"][0]["siblings"][0]["notes"] = "synthetic"
    if change == "profile":
        envelope["package_profile"] = "aim-preview-package-v1"
    with pytest.raises(PackageError):
        validate_envelope(
            envelope,
            CanonicalSchema(golden["canonical_schema"]),
            golden["dataset_merkle_root"],
            manifest_bytes=1000,
        )


def test_duplicate_keys_and_compression(envelope, golden):
    raw = canonical_json_bytes(envelope)
    kwargs = dict(
        schema=CanonicalSchema(golden["canonical_schema"]),
        root=golden["dataset_merkle_root"],
        manifest_bytes=1000,
    )
    with pytest.raises(PackageError):
        decode_envelope(b'{"sample_hash":"hidden",' + raw[1:], **kwargs)
    with pytest.raises(PackageError, match="unsupported_encoding"):
        decode_envelope(raw, content_encoding="gzip", **kwargs)
    with pytest.raises(PackageError, match="manifest_bytes_limit"):
        validate_envelope(
            envelope, kwargs["schema"], kwargs["root"], manifest_bytes=262145
        )


def test_invalid_selection(builder, envelope):
    with pytest.raises(PackageError, match="invalid_selection"):
        builder.prepare(
            [1, 0],
            proof_ids=[p["proof_id"] for p in envelope["entries"][:2]],
            commitment_id=envelope["commitment_id"],
            disclosure_version=envelope["disclosure_version"],
            detector=Mock(),
            scanned_at="2026-09-17T00:00:00Z",
            rights_confirmed=True,
            public_preview_permission=True,
            restricted_content_confirmed=True,
            package_url="https://seller.example/p",
            manifest_bytes=1000,
        )


def test_journal_export_retirement_and_marker(tmp_path, builder, envelope, caplog):
    package = prepare(builder, envelope)
    store = PublicationStore(tmp_path / "public", tmp_path / "private")
    record = store.export(package)
    args = (envelope["disclosure_version"], envelope["sample_hash"])
    assert store.read(*args) == (200, package.payload)
    assert record["state"] == "exported"
    for entry in envelope["entries"]:
        for value in entry["row"].values():
            if isinstance(value, str) and len(value) > 1:
                assert value not in json.dumps(record) + caplog.text
    assert (tmp_path / "private").stat().st_mode & 0o777 == 0o700
    store.retire(*args)
    assert store.read(*args) == (410, b"")
    with pytest.raises(PackageError, match="immutable_publication"):
        store.export(package)


def test_public_root_isolation(tmp_path):
    with pytest.raises(PackageError, match="journal_in_public_root"):
        PublicationStore(tmp_path / "public", tmp_path / "public" / "private")
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PackageError):
        PublicationStore(tmp_path / "link" / "public", tmp_path / "private")


@pytest.mark.parametrize("size", [249999, 250000, 250001])
def test_combined_canonical_byte_boundary(tmp_path, size):
    # 20 complete rows x 25 fields: each string remains below the policy's 500.
    fields = [f"f{i:02}" for i in range(25)]
    schema = CanonicalSchema([[name, "string", False, {}] for name in fields])
    rows = [{name: "" for name in fields} for _ in range(20)]
    overhead = sum(len(schema.canonical_row(row)) for row in rows)
    per_cell, remainder = divmod(size - overhead, 500)
    for index, (row, name) in enumerate((r, n) for r in rows for n in fields):
        row[name] = "x" * (per_cell + (index < remainder))
    assert max(len(v) for row in rows for v in row.values()) <= 500
    assert sum(len(schema.canonical_row(row)) for row in rows) == size
    tree = build_disk_tree(
        (schema.canonical_row(row) for row in rows), schema.digest, tmp_path
    )
    builder = CommitmentPreviewBuilder(tree, schema.descriptors)
    kwargs = dict(
        proof_ids=[str(uuid.UUID(int=i + 100)) for i in range(20)],
        commitment_id=str(uuid.UUID(int=1)),
        disclosure_version=str(uuid.UUID(int=2)),
        detector=Mock(),
        scanned_at="2026-09-17T00:00:00Z",
        rights_confirmed=True,
        public_preview_permission=True,
        restricted_content_confirmed=True,
        package_url="https://seller.example/p",
        manifest_bytes=262144,
    )
    if size > 250000:
        with pytest.raises(PackageError, match="canonical_bytes_limit"):
            builder.prepare(list(range(20)), **kwargs)
    else:
        builder.prepare(list(range(20)), **kwargs)


def test_unique_cell_marker_absent_from_journal_and_errors(tmp_path, caplog):
    marker = "unique synthetic cell marker " + uuid.uuid4().hex[:8]
    schema = CanonicalSchema([["value", "string", False, {}]])
    private = tmp_path / "tree"
    private.mkdir()
    tree = build_disk_tree(
        [schema.canonical_row({"value": marker})], schema.digest, private
    )
    builder = CommitmentPreviewBuilder(tree, schema.descriptors)
    package = builder.prepare(
        [0],
        proof_ids=[str(uuid.UUID(int=3))],
        commitment_id=str(uuid.UUID(int=1)),
        disclosure_version=str(uuid.UUID(int=2)),
        detector=Mock(),
        scanned_at="2026-09-17T00:00:00Z",
        rights_confirmed=True,
        public_preview_permission=True,
        restricted_content_confirmed=True,
        package_url="https://seller.example/p",
        manifest_bytes=1000,
    )
    store = PublicationStore(tmp_path / "public", tmp_path / "journal")
    record = store.export(package)
    assert marker in package.payload.decode()
    journal_text = "".join(p.read_text() for p in (tmp_path / "journal").glob("*.json"))
    assert marker not in journal_text + json.dumps(record) + caplog.text
    envelope = json.loads(package.payload)
    envelope["entries"][0]["row"]["unknown"] = marker
    with pytest.raises(PackageError) as exc:
        validate_envelope(
            envelope,
            schema,
            tree.root,
            manifest_bytes=1000,
        )
    assert marker not in str(exc.value) + caplog.text


def test_failed_policy_writes_nothing(tmp_path, builder, envelope):
    from app.services.preview_content_policy import PolicyError

    detector = Mock()
    detector.scan_complete_selection.side_effect = RuntimeError(
        "unique synthetic cell marker"
    )
    with pytest.raises(PolicyError, match="detector_unavailable"):
        builder.prepare(
            [0],
            proof_ids=[envelope["entries"][0]["proof_id"]],
            commitment_id=envelope["commitment_id"],
            disclosure_version=envelope["disclosure_version"],
            detector=detector,
            scanned_at="2026-09-17T00:00:00Z",
            rights_confirmed=True,
            public_preview_permission=True,
            restricted_content_confirmed=True,
            package_url="https://seller.example/p",
            manifest_bytes=1000,
        )
    assert not (tmp_path / "public").exists()


def test_tampered_index_and_published_file(tmp_path, builder, envelope):
    package = prepare(builder, envelope)
    store = PublicationStore(tmp_path / "public", tmp_path / "journal")
    store.export(package)
    args = (envelope["disclosure_version"], envelope["sample_hash"])
    (store.public_root / store.path(*args)).write_bytes(b"unique synthetic cell marker")
    assert store.read(*args) == (404, b"")
    (builder.tree.directory / "rows").write_bytes(b"corrupted")
    with pytest.raises(PackageError, match="invalid_index"):
        builder.page()


def test_unscanned_payload_cannot_be_marked_prepared():
    from app.services.preview_package_service import PreparedPackage

    with pytest.raises(PackageError, match="approval_required"):
        PreparedPackage(b"unscanned synthetic cell marker", {})
