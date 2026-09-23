from tests import test_preview_package_service as package_tests
import copy
import json
from datetime import timedelta
from unittest.mock import Mock
import pytest
from app.services.preview_lifecycle import (
    PreviewJournal,
    LifecycleError,
    capture_rights,
    freshness,
    stale_at,
    withdrawal_candidate,
    refresh_candidate,
    supersession_candidate,
    validate_retirement_receipts,
)
from app.services.preview_signing_service import LocalCandidate
from tests.preview_fixture_factory import request_fixture, uid, NOW, STAMP
from tests.test_preview_origin_service import capture, URL, BROWSER


def ready(journal, candidate):
    key = journal.start(candidate)
    for state in ("selected", "scanned", "hosted", "ready_to_sign"):
        journal.transition(key, state)
    return key


def test_atomic_retry_and_recovery(tmp_path):
    j = PreviewJournal(tmp_path / "journal.sqlite")
    r = request_fixture()
    candidate = LocalCandidate.validate(r["binding"])
    key = ready(j, candidate)
    first = j.freeze(key, r)
    restarted = PreviewJournal(j.path)
    assert restarted.freeze(key, r) == first
    assert restarted.read(key)["state"] == "signed_candidate"
    changed = copy.deepcopy(r)
    changed["seller_signature"] = "A" * 86
    with pytest.raises(LifecycleError, match="request_id_conflict"):
        restarted.freeze(key, changed)
    j.transition(key, "submitted")
    assert j.read(key)["state"] == "submitted"
    assert j.read(key)["request"] == first[0]


def test_journal_round_trips_previous_commitment_id(tmp_path):
    request = request_fixture()
    request["commitment"]["previous_commitment_id"] = uid(40)
    journal = PreviewJournal(tmp_path / "journal.sqlite")
    key = ready(journal, LocalCandidate.validate(request["binding"]))
    journal.freeze(key, request)

    recovered = PreviewJournal(journal.path).read(key)

    assert json.loads(recovered["request"])["commitment"][
        "previous_commitment_id"
    ] == uid(40)


@pytest.mark.parametrize("profile", [None, "aim-approved-aggregates-v1", "aim-approved-aggregates-v2"])
def test_withdrawal_refresh_supersession(profile):
    r = request_fixture()
    old = r["binding"]
    if profile is not None:
        old["aggregate_hash_profile"] = profile
    w = withdrawal_candidate(
        old, disclosure_version=uid(30), request_id=uid(31), approved_at=STAMP
    ).binding()
    assert w["decision"] == "withdraw" and w["sample_hash"] is None
    assert w.get("aggregate_hash_profile") == profile
    assert ("aggregate_hash_profile" in w) == (profile is not None)
    assert (
        w["expected_current_disclosure_id"]
        == w["supersedes"]
        == old["disclosure_version"]
    )
    refreshed = refresh_candidate(
        old,
        disclosure_version=uid(32),
        request_id=uid(33),
        attested_at="2026-09-18T00:00:00.000000Z",
        cadence_days=7,
    ).binding()
    for field in (
        "commitment_id",
        "sample_hash",
        "rights_basis_digest",
        "public_preview_permission",
        "schema_descriptors",
    ):
        assert refreshed[field] == old[field]
    assert refreshed.get("aggregate_hash_profile") == profile
    assert ("aggregate_hash_profile" in refreshed) == (profile is not None)
    superseded = supersession_candidate(
        old, dict(old, disclosure_version=uid(34), request_id=uid(35))
    ).binding()
    assert superseded["supersedes"] == old["disclosure_version"]
    assert superseded.get("aggregate_hash_profile") == profile
    assert ("aggregate_hash_profile" in superseded) == (profile is not None)
    assert old == r["binding"]


@pytest.mark.parametrize(
    "cadence,days", [(None, 90), (1, 7), (3, 7), (4, 8), (45, 90), (46, 90)]
)
def test_stale_boundaries(cadence, days):
    threshold = NOW + timedelta(days=days)
    assert stale_at(STAMP, cadence) == threshold
    for delta, stale in [(-1, False), (0, True), (1, True)]:
        result = freshness(
            attested_at=STAMP,
            cadence_days=cadence,
            now=threshold + timedelta(microseconds=delta),
        )
        assert result["stale"] == stale
        assert result["eligible_by_time"] and result["freshness_expires_at"] is None
    assert not freshness(
        attested_at=STAMP,
        cadence_days=cadence,
        now=threshold,
        policy_expires_at=threshold,
    )["eligible_by_time"]
    assert not freshness(
        attested_at=STAMP, cadence_days=cadence, now=threshold, summary_expires_at=NOW
    )["eligible_by_time"]


def test_rights_are_local():
    a = capture_rights("synthetic rights", "owner", True)
    assert "synthetic rights" not in str(a)
    assert capture_rights(" synthetic rights", "owner", True) != a
    with pytest.raises(LifecycleError):
        capture_rights("synthetic rights", "owner", False)


@pytest.mark.parametrize("allow_origin", [BROWSER, "*"])
def test_retirement_pending_recovery(tmp_path, allow_origin):
    j = PreviewJournal(tmp_path / "journal.sqlite")
    r = request_fixture()
    b = r["binding"]
    key = ready(j, LocalCandidate.validate(b))
    j.freeze(key, r)
    store = Mock()
    store.path.return_value = "previews/package.json"
    kwargs = dict(
        publication_store=store,
        disclosure_version=b["disclosure_version"],
        sample_hash=b["sample_hash"],
        url=URL,
        origin=BROWSER,
    )
    with pytest.raises(LifecycleError):
        j.retire(key, receipt_reader=lambda: [], **kwargs)
    assert j.read(key)["state"] == "retirement_pending"
    receipts = [
        capture(status=410, retired=True),
        capture(method="OPTIONS", status=204, retired=True),
    ]
    for receipt in receipts:
        receipt["headers"]["access-control-allow-origin"] = allow_origin
    assert j.retire(key, receipt_reader=lambda: receipts, **kwargs) == receipts
    assert j.read(key)["state"] == "retired"
    assert j.retire(key, receipt_reader=lambda: [], **kwargs) == receipts
    store.retire.assert_called_with(b["disclosure_version"], b["sample_hash"])
    calls = store.retire.call_count
    for change in (
        {"url": URL + "/wrong"},
        {"url": URL.replace("https://", "http://")},
        {"url": URL.replace("seller.example", "other.example")},
        {"url": URL + "?retry=1"},
        {"url": URL + "#wrong"},
        {"origin": "https://other.example"},
        {"sample_hash": "0" * 64},
    ):
        with pytest.raises(LifecycleError, match="retirement_url_mismatch|invalid_retirement_receipt|retirement_sample_mismatch"):
            j.retire(key, receipt_reader=lambda: pytest.fail("retry must use stored evidence"), **(kwargs | change))
    assert store.retire.call_count == calls
    assert j.read(key)["state"] == "retired"
    receipts[0]["body"] = "marker"
    with pytest.raises(LifecycleError):
        validate_retirement_receipts(receipts, url=URL, origin=BROWSER)


golden = package_tests.golden
builder = package_tests.builder
envelope = package_tests.envelope
pinned_identity = package_tests.pinned_identity


def test_2b_origin_retirement_removes_package_and_saved_copy_cannot_restore(
    tmp_path, builder, envelope
):
    from app.services.preview_package_service import PublicationStore, PackageError

    package = package_tests.prepare(builder, envelope)
    store = PublicationStore(tmp_path / "public", tmp_path / "origin-journal")
    store.export(package)
    b = dict(
        request_fixture()["binding"],
        disclosure_version=envelope["disclosure_version"],
        sample_hash=envelope["sample_hash"],
    )
    journal = PreviewJournal(tmp_path / "private" / "journal.sqlite")
    key = ready(journal, LocalCandidate.validate(b))
    assert store.read(b["disclosure_version"], b["sample_hash"])[0] == 200
    receipts = [
        capture(status=410, retired=True),
        capture(method="OPTIONS", status=204, retired=True),
    ]
    actual_url = "https://seller.example/" + store.path(
        b["disclosure_version"], b["sample_hash"]
    )
    for receipt in receipts:
        receipt["url"] = actual_url
    journal.retire(
        key,
        publication_store=store,
        disclosure_version=b["disclosure_version"],
        sample_hash=b["sample_hash"],
        url=actual_url,
        origin=BROWSER,
        receipt_reader=lambda: receipts,
    )
    assert store.read(b["disclosure_version"], b["sample_hash"]) == (410, b"")
    assert not (
        store.public_root / store.path(b["disclosure_version"], b["sample_hash"])
    ).exists()
    with pytest.raises(PackageError, match="immutable_publication"):
        store.export(package)
    assert journal.read(key)["state"] == "retired"


def test_existing_journal_migrates_without_inventing_retirement_origin(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    journal = PreviewJournal(path)
    request = request_fixture()
    key = ready(journal, LocalCandidate.validate(request["binding"]))
    journal.freeze(key, request)
    before = journal.read(key)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE candidates DROP COLUMN retirement_origin")
    reopened = PreviewJournal(path)
    assert reopened.read(key) == before
    assert reopened.read(key)["retirement_origin"] is None
