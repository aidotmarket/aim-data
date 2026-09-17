import os
from pathlib import Path
from datetime import datetime, timezone
from itertools import combinations
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.database import get_session_context
from app.models.dataset import DatasetRecord
from app.models.s3_connection import S3Connection
from app.models.s3_object_metadata import S3ObjectMetadata
from app.models.s3_scan_job import S3ScanJob
from app.services import source_artifact_resolver as resolver
from app.services.data_verification.connectors.eolymp_v1 import object_commitment
from app.services.marketplace_action_signer import canonical_json_bytes


def _dataset(*, listing_id: str, dataset_id: str, filename: str, processed_path=None):
    record = DatasetRecord(
        id=dataset_id,
        original_filename=filename,
        storage_filename=filename,
        file_type="csv",
        status="preview_ready",
        listing_id=listing_id,
        processed_path=processed_path,
    )
    with get_session_context() as session:
        session.add(record)
        session.commit()
    return record


def test_local_resolver_pins_preferred_artifact_and_detects_selection_change(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    processed = tmp_path / "processed"
    uploads.mkdir()
    processed.mkdir()
    monkeypatch.setattr(resolver.settings, "upload_directory", str(uploads))
    monkeypatch.setattr(resolver.settings, "processed_directory", str(processed))
    dataset_id = f"ds-{uuid4()}"
    listing_id = f"listing-{uuid4()}"
    upload = uploads / "source.csv"
    upload.write_bytes(b"id\n1\n")
    _dataset(listing_id=listing_id, dataset_id=dataset_id, filename=upload.name)

    first = resolver.resolve_source_artifact(listing_id)
    assert first is not None and first.local_path == os.path.realpath(upload)
    first_commitment = first.locator_commitment(b"c" * 32)

    preferred = processed / f"{dataset_id}.parquet"
    preferred.write_bytes(b"PAR1different")
    second = resolver.resolve_source_artifact(listing_id)
    assert second is not None
    assert second.locator_commitment(b"c" * 32) != first_commitment
    with pytest.raises(resolver.StaleArtifactIdentityError):
        resolver.assert_artifact_still_pinned(first)


def test_local_resolver_detects_in_place_content_replacement(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    processed = tmp_path / "processed"
    uploads.mkdir()
    processed.mkdir()
    monkeypatch.setattr(resolver.settings, "upload_directory", str(uploads))
    monkeypatch.setattr(resolver.settings, "processed_directory", str(processed))
    path = uploads / "source.csv"
    path.write_bytes(b"id\n1\n")
    listing_id = f"listing-{uuid4()}"
    _dataset(listing_id=listing_id, dataset_id=f"ds-{uuid4()}", filename=path.name)
    pinned = resolver.resolve_source_artifact(listing_id)
    path.write_bytes(b"id\n2\n3\n")
    with pytest.raises(resolver.StaleArtifactIdentityError):
        resolver.assert_artifact_still_pinned(pinned)


def test_s3_resolver_commitment_changes_with_hmac_key_and_registered_object(tmp_path, monkeypatch):
    monkeypatch.setattr(resolver.settings, "upload_directory", str(tmp_path / "uploads"))
    monkeypatch.setattr(resolver.settings, "processed_directory", str(tmp_path / "processed"))
    dataset_id = f"ds-{uuid4()}"
    listing_id = f"listing-{uuid4()}"
    _dataset(listing_id=listing_id, dataset_id=dataset_id, filename="unused.csv")
    connection = S3Connection(
        id=f"conn-{uuid4()}", name="registered", bucket="bucket-a", region="us-east-1",
        role_arn="arn:aws:iam::123456789012:role/aim-data", external_id=str(uuid4()), status="configured",
    )
    job = S3ScanJob(id=f"scan-{uuid4()}", connection_id=connection.id)
    metadata = S3ObjectMetadata(
        id=f"obj-{uuid4()}", connection_id=connection.id, scan_job_id=job.id,
        object_key="registered/source.csv", size_bytes=10, content_type="text/csv",
        last_modified=datetime.now(timezone.utc), etag="etag-a", dataset_id=dataset_id,
    )
    metadata_id = metadata.id
    with get_session_context() as session:
        session.add(connection)
        session.add(job)
        session.add(metadata)
        session.commit()
    pinned = resolver.resolve_source_artifact(listing_id)
    assert pinned is not None
    before = pinned.locator_commitment(b"c" * 32)
    assert pinned.locator_commitment(b"d" * 32) != before
    with get_session_context() as session:
        row = session.get(S3ObjectMetadata, metadata_id)
        row.object_key = "registered/changed.csv"
        session.add(row)
        session.commit()
    changed = resolver.resolve_source_artifact(listing_id)
    assert changed.locator_commitment(b"c" * 32) != before
    with pytest.raises(resolver.StaleArtifactIdentityError):
        resolver.assert_artifact_still_pinned(pinned)


def test_cloud_visible_commitments_offer_no_dictionary_equality_or_ordering_oracle():
    dataset = SimpleNamespace()
    artifacts = [
        resolver.ResolvedArtifact(
            listing_id="listing-local-1",
            source_handle_id="dataset-local-1",
            dataset=dataset,
            kind="local",
            local_path="/srv/aim-data/customers/acme/orders-2026.parquet",
        ),
        resolver.ResolvedArtifact(
            listing_id="listing-local-2",
            source_handle_id="dataset-local-2",
            dataset=dataset,
            kind="local",
            local_path="/Users/alice/Library/Application Support/AIM Data/private/customers.csv",
        ),
        resolver.ResolvedArtifact(
            listing_id="listing-s3-1",
            source_handle_id="dataset-s3-1",
            dataset=dataset,
            kind="s3",
            connection=SimpleNamespace(id="conn-prod-eu-west-1", bucket="acme-private-analytics"),
            metadata=SimpleNamespace(object_key="exports/2026/08/customers.parquet"),
        ),
        resolver.ResolvedArtifact(
            listing_id="listing-s3-2",
            source_handle_id="dataset-s3-2",
            dataset=dataset,
            kind="s3",
            connection=SimpleNamespace(id="conn-finance-us-east-1", bucket="finance-ledger-private"),
            metadata=SimpleNamespace(object_key="daily/2026-08-24/ledger.jsonl"),
        ),
    ]
    object_names = [
        "warehouse_prod.public.customers",
        "analytics.sales.orders_2026",
        "archives/2026-08-24/customers.csv",
        "registered-root",
        "objects/private/customer-events.jsonl",
    ]
    commitment_keys = [bytes([value]) * 32 for value in (11, 22, 33)]

    commitments_by_key = []
    for key in commitment_keys:
        commitments_by_key.append(
            [artifact.locator_commitment(key) for artifact in artifacts]
            + [object_commitment(key, b"dataset-handle-private", name) for name in object_names]
        )

    visible = canonical_json_bytes(commitments_by_key)
    raw_dictionary = [
        artifact.canonical_locator_bytes() for artifact in artifacts
    ] + [name.encode("utf-8") for name in object_names]
    assert all(candidate not in visible for candidate in raw_dictionary)

    for position in range(len(commitments_by_key[0])):
        assert len({commitments[position] for commitments in commitments_by_key}) == len(commitment_keys)
    for left, right in combinations(commitments_by_key, 2):
        assert set(left).isdisjoint(right)
        assert any(
            (left[first] < left[second]) != (right[first] < right[second])
            for first in range(len(left))
            for second in range(first + 1, len(left))
        )

    attacker_keys = [bytes([value]) * 32 for value in (44, 55)]
    attacker_guesses = {
        artifact.locator_commitment(key)
        for key in attacker_keys
        for artifact in artifacts
    } | {
        object_commitment(key, b"dataset-handle-private", name)
        for key in attacker_keys
        for name in object_names
    }
    assert all(attacker_guesses.isdisjoint(commitments) for commitments in commitments_by_key)


def test_directory_member_original_bytes_no_parquet_fallback(tmp_path, monkeypatch):
    from app.models.dataset import DatasetMember
    monkeypatch.setattr(resolver.settings, 'multi_file_datasets_enabled', True)
    root = tmp_path / 'original'; root.mkdir()
    original = root / 'input.csv'; original.write_bytes(b'x\n1\n')
    parquet = tmp_path / 'preferred.parquet'; parquet.write_bytes(b'PAR1different')
    dataset = DatasetRecord(id='directory-bytes', original_filename=root.name,
                            storage_filename=root.name, file_type='directory', root_path=str(root),
                            processed_path=str(parquet))
    member = DatasetMember(dataset_id=dataset.id, index=0, relative_path=original.name,
                           size_bytes=4, detected_type='csv', sha256='0' * 64)
    args = dict(upload_directory=str(tmp_path), processed_directory=str(tmp_path), member=member)
    assert resolver._resolve_file_path(dataset, **args) == str(original)
    original.unlink()
    assert resolver._resolve_file_path(dataset, **args) is None
    original.symlink_to(parquet)
    assert resolver._resolve_file_path(dataset, **args) is None


def test_directory_member_unicode_path(tmp_path, monkeypatch):
    from app.models.dataset import DatasetMember
    monkeypatch.setattr(resolver.settings, 'multi_file_datasets_enabled', True)
    path = tmp_path / 'e\u0301.csv'; path.write_bytes(b'original')
    dataset = DatasetRecord(id='unicode', original_filename='root', storage_filename='root',
                            file_type='directory', root_path=str(tmp_path))
    member = DatasetMember(dataset_id=dataset.id, index=0, relative_path='é.csv', detected_type='csv')
    assert Path(resolver.resolve_member_path(dataset, member)).read_bytes() == b'original'


def test_directory_golden_vectors_and_content_invariants():
    import json, hashlib, copy
    from app.services.dataset_manifest import build_manifest, directory_locator_bytes, directory_content_sha256
    fixture = json.loads((Path(__file__).parent / "fixtures/multi_file_datasets/directory_verification_golden.json").read_text())
    assert hashlib.sha256((Path(__file__).parent / "fixtures/multi_file_datasets/directory_verification_golden.json").read_bytes()).hexdigest() == "bec2796490db4734cba66aeee9b60ea7490e20fda61966910a54083b363429c9"
    rows = fixture["members"]
    key = bytes.fromhex(fixture["commitment_key_hex"])
    assert build_manifest(rows)["manifest_hash"] == fixture["manifest_hash"]
    locator = directory_locator_bytes(fixture["root_path"], fixture["manifest_hash"])
    assert locator.hex() == fixture["locator_preimage_hex"]
    import hmac
    assert hmac.new(key, locator, hashlib.sha256).hexdigest() == fixture["artifact_locator_commitment"]
    assert directory_content_sha256(rows) == fixture["content_sha256"]
    def ids(members, root=fixture["root_path"], commitment_key=key):
        return {m["relative_path"]: object_commitment(commitment_key,
            b"local_directory_object\0" + os.fsencode(root),
            "member\0" + m["relative_path"] + "\0" + m["sha256"])
            for m in members if m["role"] == "data"}
    assert ids(rows) == fixture["object_ids"]
    changed = copy.deepcopy(rows)
    changed.reverse()
    for i, row in enumerate(changed):
        row["index"] = i
        if row["role"] != "data": row["sha256"] = "a" * 64
        else: row["is_sample"] = True
    assert directory_content_sha256(changed) == directory_content_sha256(rows)
    assert ids(changed) == ids(rows)
    assert build_manifest(changed)["manifest_hash"] != fixture["manifest_hash"]
    swapped = copy.deepcopy(rows)
    for field in ("size_bytes", "sha256"):
        swapped[0][field], swapped[1][field] = swapped[1][field], swapped[0][field]
    assert directory_content_sha256(swapped) != directory_content_sha256(rows)
    assert all(ids(swapped)[m["relative_path"]] != ids(rows)[m["relative_path"]] for m in rows[:2])
    assert set(ids(rows, commitment_key=b"d"*32).values()).isdisjoint(ids(rows).values())
    assert set(ids(rows, root="/moved/root").values()).isdisjoint(ids(rows).values())
    changed = copy.deepcopy(rows); changed[0]["role"] = "other"
    assert directory_content_sha256(changed) != directory_content_sha256(rows)


def test_retained_directory_composite_resolution_reupload_and_parity(tmp_path, monkeypatch):
    import json
    from sqlmodel import SQLModel
    from app.core.database import get_engine
    from app.models.published_manifest import PublishedManifest
    from app.services import data_verification_local_service as local
    from app.services.dataset_manifest import build_manifest
    SQLModel.metadata.create_all(get_engine())
    monkeypatch.setattr(resolver.settings, "multi_file_datasets_enabled", True)
    rows = json.loads((Path(__file__).parent / "fixtures/multi_file_datasets/directory_verification_golden.json").read_text())["members"]
    manifest_hash = build_manifest(rows)["manifest_hash"]
    listing, other_listing, dataset_id, other_id, v1, v2, other_v = (str(uuid4()) for _ in range(7))
    root1, root2 = tmp_path / "old", tmp_path / "reuploaded"
    root1.mkdir(); root2.mkdir()
    _dataset(listing_id=listing, dataset_id=dataset_id, filename="old")
    _dataset(listing_id=other_listing, dataset_id=other_id, filename="other")
    with get_session_context() as session:
        for version, ds, root in ((v1,dataset_id,root1),(v2,dataset_id,root2),(other_v,other_id,root2)):
            session.add(PublishedManifest(listing_version_id=version, manifest_hash=manifest_hash,
                dataset_id=ds, root_path=str(root), members=rows))
        live = session.get(DatasetRecord, dataset_id)
        live.root_path = str(root2); live.file_type = "directory"
        session.add(live); session.commit()
    old = local.resolve_source_artifact(listing, v1, manifest_hash)
    current = local.resolve_source_artifact(listing, v2, manifest_hash)
    other = local.resolve_source_artifact(other_listing, other_v, manifest_hash)
    assert old.root_path == str(root1) and current.root_path == other.root_path == str(root2)
    assert old.resolved_object_count() == 3
    # Same hash alone cannot establish order/scan parity; the composite is authoritative.
    def binding(artifact): return artifact.listing_version_id, artifact.manifest_hash
    assert binding(old) == binding(local.resolve_source_artifact(listing, v1, manifest_hash))
    assert binding(old) != binding(current)
    assert old.canonical_locator_bytes() != current.canonical_locator_bytes()
    for args in [(listing, v1, "f"*64), (listing, str(uuid4()), manifest_hash), (other_listing, v1, manifest_hash)]:
        with pytest.raises(resolver.ArtifactResolutionError): local.resolve_source_artifact(*args)
    monkeypatch.setattr(resolver.settings, "multi_file_datasets_enabled", False)
    with pytest.raises(resolver.ArtifactResolutionError, match="disabled"):
        local.resolve_source_artifact(listing, v1, manifest_hash)
