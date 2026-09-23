"""Seller-local preview lifecycle. Metadata journal is never platform authority."""

from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import unicodedata
from email.message import Message

from app.services.preview_signing_service import (
    LocalCandidate,
    request_bytes,
    request_digest,
    closed,
)
from app.models.preview_disclosure_schemas import DisclosureBinding
from app.services.dataset_merkle_service import (
    canonical_json_bytes,
    canonical_rfc3339_utc,
)
from app.services.preview_origin_service import capture_receipt, HEADER_KEYS


class LifecycleError(ValueError):
    pass


TRANSITIONS = {
    "building": {"selected", "invalidated"},
    "selected": {"scanned", "invalidated"},
    "scanned": {"hosted", "invalidated"},
    "hosted": {"ready_to_sign", "retirement_pending", "invalidated"},
    "ready_to_sign": {"signed_candidate", "invalidated", "retirement_pending"},
    "signed_candidate": {
        "submitted",
        "submission_unknown",
        "withdrawal_pending",
        "invalidated",
        "retirement_pending",
    },
    "submission_unknown": {"submitted", "withdrawal_pending", "retirement_pending"},
    "submitted": {"withdrawal_pending", "retirement_pending", "invalidated"},
    "withdrawal_pending": {"submission_unknown", "retirement_pending"},
    "retirement_pending": {"retired"},
    "invalidated": {"retirement_pending"},
    "retired": set(),
}


def capture_rights(text, code, permission):
    if (
        type(text) is not str
        or not text.strip()
        or len(text) > 4096
        or code not in {"owner", "licensed", "public_domain", "other_authorized"}
        or permission is not True
    ):
        raise LifecycleError("rights_required")
    return dict(
        rights_basis_digest=hashlib.sha256(
            b"aim-preview-rights-basis-v1\0"
            + unicodedata.normalize("NFC", text).encode("utf-8")
        ).hexdigest(),
        rights_basis_code=code,
        public_preview_permission=True,
    )


def stale_at(attested_at, cadence_days):
    stamp = datetime.fromisoformat(
        canonical_rfc3339_utc(attested_at).replace("Z", "+00:00")
    )
    if cadence_days is not None and (
        type(cadence_days) is not int or cadence_days <= 0
    ):
        raise LifecycleError("invalid_cadence")
    days = 90 if cadence_days is None else min(90, max(7, 2 * cadence_days))
    return stamp + timedelta(days=days)


def freshness(
    *,
    attested_at,
    cadence_days,
    now,
    approval_expires_at=None,
    policy_expires_at=None,
    summary_expires_at=None,
):
    threshold = stale_at(attested_at, cadence_days)
    expiries = [
        v
        for v in (approval_expires_at, policy_expires_at, summary_expires_at)
        if v is not None
    ]
    return {
        "stale": now >= threshold,
        "freshness_stale_at": canonical_rfc3339_utc(threshold),
        "freshness_expires_at": canonical_rfc3339_utc(policy_expires_at)
        if policy_expires_at
        else None,
        "eligible_by_time": all(now < v for v in expiries),
    }


GRANT_NULLS = (
    "sample_hash",
    "commitment_id",
    "schema_digest",
    "seller_dataset_version",
    "sampled_leaf_list_digest",
    "scan_attestation_digest",
    "rights_basis_digest",
    "rights_basis_code",
    "public_preview_permission",
    "preview_type",
    "content_type",
)


def _inherit_aggregate_hash_profile(candidate, prior):
    if "aggregate_hash_profile" in prior:
        candidate["aggregate_hash_profile"] = prior["aggregate_hash_profile"]
    else:
        candidate.pop("aggregate_hash_profile", None)


def withdrawal_candidate(prior, *, disclosure_version, request_id, approved_at):
    old = closed(DisclosureBinding, prior)
    b = dict(
        old,
        decision="withdraw",
        sample_decision="none",
        disclosure_version=disclosure_version,
        request_id=request_id,
        approved_at=approved_at,
        supersedes=old["disclosure_version"],
        expected_current_disclosure_id=old["disclosure_version"],
    )
    _inherit_aggregate_hash_profile(b, old)
    b.update({k: None for k in GRANT_NULLS})
    b.update(proof_ids=[], schema_descriptors=[], selected_fields=[])
    return LocalCandidate.validate(b)


def refresh_candidate(
    prior, *, disclosure_version, request_id, attested_at, cadence_days
):
    old = closed(DisclosureBinding, prior)
    if old["sample_decision"] != "approved":
        raise LifecycleError("refresh_requires_approval")
    b = dict(
        old,
        decision="approve",
        disclosure_version=disclosure_version,
        request_id=request_id,
        approved_at=attested_at,
        last_attested_by_seller_at=attested_at,
        update_cadence_days=cadence_days,
        supersedes=old["disclosure_version"],
        expected_current_disclosure_id=old["disclosure_version"],
    )
    _inherit_aggregate_hash_profile(b, old)
    if b["last_attested_by_seller_at"] <= old["last_attested_by_seller_at"]:
        raise LifecycleError("refresh_time_not_new")
    return LocalCandidate.validate(b)


def supersession_candidate(prior, replacement):
    old, b = closed(DisclosureBinding, prior), closed(DisclosureBinding, replacement)
    if (
        b["seller_id"] != old["seller_id"]
        or b["listing_id"] != old["listing_id"]
        or b["request_id"] == old["request_id"]
        or b["sample_decision"] != "approved"
    ):
        raise LifecycleError("supersession_mismatch")
    b.update(
        supersedes=old["disclosure_version"],
        expected_current_disclosure_id=old["disclosure_version"],
    )
    _inherit_aggregate_hash_profile(b, old)
    return LocalCandidate.validate(b)


def validate_retirement_receipts(receipts, *, url, origin):
    if not isinstance(receipts, list) or len(receipts) != 2:
        raise LifecycleError("retirement_evidence_missing")
    checked = []
    for receipt, method in zip(receipts, ("GET", "OPTIONS")):
        if (
            set(receipt)
            != {"url", "method", "status", "captured_at", "headers", "no_set_cookie"}
            or receipt["url"] != url
            or receipt["method"] != method
            or receipt["no_set_cookie"] is not True
            or set(receipt["headers"]) != set(HEADER_KEYS)
        ):
            raise LifecycleError("invalid_retirement_receipt")
        headers = Message()
        for key, value in receipt["headers"].items():
            if value is not None:
                if type(value) is not str:
                    raise LifecycleError("invalid_retirement_receipt")
                headers[key] = value
        result = capture_receipt(
            url,
            method,
            receipt["status"],
            headers,
            captured_at=receipt["captured_at"],
            origin=origin,
            retired=True,
        )
        if result != receipt:
            raise LifecycleError("invalid_retirement_receipt")
        checked.append(result)
    return checked


class PreviewJournal:
    """SQLite transactions retain exact bytes; no retry rebuilt from UI state.

    Marketplace candidate bytes and requests survive retries and process restarts.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise LifecycleError("journal_permissions")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise LifecycleError("journal_permissions")
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS candidates (
                  seller TEXT NOT NULL, listing TEXT NOT NULL, request_id TEXT NOT NULL,
                  kind TEXT NOT NULL, state TEXT NOT NULL, candidate BLOB NOT NULL,
                  request BLOB, digest TEXT, receipts BLOB, retirement_origin TEXT,
                  PRIMARY KEY(seller,listing,request_id));
                CREATE TABLE IF NOT EXISTS transitions (
                  seller TEXT, listing TEXT, request_id TEXT, old_state TEXT, new_state TEXT);
            """)
            # Preserve existing local journals; old receipts lack exact-origin
            # evidence and fail closed on idempotent retries.
            if "retirement_origin" not in {
                row[1] for row in db.execute("PRAGMA table_info(candidates)")
            }:
                db.execute("ALTER TABLE candidates ADD COLUMN retirement_origin TEXT")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def start(self, candidate):
        b = candidate.binding()
        key = (b["seller_id"], b["listing_id"], b["request_id"])
        with self._db() as db:
            old = db.execute(
                "SELECT candidate FROM candidates WHERE seller=? AND listing=? AND request_id=?",
                key,
            ).fetchone()
            if old:
                if bytes(old[0]) != candidate.binding_bytes:
                    raise LifecycleError("request_id_conflict")
                return key
            db.execute(
                "INSERT INTO candidates(seller,listing,request_id,kind,state,candidate) VALUES (?,?,?,?,?,?)",
                (*key, candidate.kind, "building", candidate.binding_bytes),
            )
        return key

    def _transition(self, db, key, new):
        row = db.execute(
            "SELECT state,kind,request FROM candidates WHERE seller=? AND listing=? AND request_id=?",
            key,
        ).fetchone()
        if not row or new not in TRANSITIONS.get(row[0], set()):
            raise LifecycleError("invalid_transition")
        if new == "signed_candidate" and row[2] is None:
            raise LifecycleError("request_missing")
        db.execute(
            "UPDATE candidates SET state=? WHERE seller=? AND listing=? AND request_id=?",
            (new, *key),
        )
        db.execute("INSERT INTO transitions VALUES (?,?,?,?,?)", (*key, row[0], new))

    def transition(self, key, state):
        if state in {"signed_candidate", "retired"}:
            raise LifecycleError("evidence_required")
        with self._db() as db:
            self._transition(db, key, state)

    def freeze(self, key, request):
        raw, digest = request_bytes(request), request_digest(request)
        b = request["binding"]
        if key != (b["seller_id"], b["listing_id"], b["request_id"]):
            raise LifecycleError("request_identity_mismatch")
        with self._db() as db:
            old = db.execute(
                "SELECT candidate,request,digest FROM candidates WHERE seller=? AND listing=? AND request_id=?",
                key,
            ).fetchone()
            if not old or bytes(old[0]) != LocalCandidate.validate(b).binding_bytes:
                raise LifecycleError("candidate_changed")
            if old[1] is not None:
                if bytes(old[1]) != raw or old[2] != digest:
                    raise LifecycleError("request_id_conflict")
                return bytes(old[1]), old[2]
            db.execute(
                "UPDATE candidates SET request=?,digest=? WHERE seller=? AND listing=? AND request_id=?",
                (raw, digest, *key),
            )
            self._transition(db, key, "signed_candidate")
        return raw, digest

    def read(self, key):
        with self._db() as db:
            row = db.execute(
                "SELECT state,candidate,request,digest,receipts,retirement_origin FROM candidates WHERE seller=? AND listing=? AND request_id=?",
                key,
            ).fetchone()
        if not row:
            raise LifecycleError("candidate_missing")
        return dict(zip(("state", "candidate", "request", "digest", "receipts", "retirement_origin"), row))

    def retire(
        self,
        key,
        *,
        publication_store,
        disclosure_version,
        sample_hash,
        url,
        origin,
        receipt_reader,
    ):
        # Pending persists before origin mutation. A failed receipt read never
        # records retirement success; an idempotent retry can finish recovery.
        record = self.read(key)
        b = json.loads(record["candidate"])
        from urllib.parse import urlsplit

        if urlsplit(url).path.lstrip("/") != publication_store.path(
            disclosure_version, sample_hash
        ):
            raise LifecycleError("retirement_url_mismatch")
        if b["decision"] != "approve" or sample_hash != b["sample_hash"]:
            raise LifecycleError("retirement_sample_mismatch")
        if record["state"] == "retired":
            if record["retirement_origin"] != origin:
                raise LifecycleError("invalid_retirement_receipt")
            return validate_retirement_receipts(
                json.loads(record["receipts"]), url=url, origin=origin
            )
        if record["state"] != "retirement_pending":
            self.transition(key, "retirement_pending")
        publication_store.retire(disclosure_version, sample_hash)
        receipts = validate_retirement_receipts(
            receipt_reader(), url=url, origin=origin
        )
        with self._db() as db:
            db.execute(
                "UPDATE candidates SET receipts=?,retirement_origin=? WHERE seller=? AND listing=? AND request_id=?",
                (canonical_json_bytes(receipts), origin, *key),
            )
            self._transition(db, key, "retired")
        return receipts
