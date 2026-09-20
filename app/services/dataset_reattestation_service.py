"""Local unchanged-root verification and re-attestation construction."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from app.models.dataset_commitment_schemas import DatasetReattestationContract
from app.services.dataset_canonicalization import ParsingDeclaration
from app.services.dataset_merkle_service import (
    CommitmentValidationError,
    canonical_json_bytes,
    canonical_rfc3339_utc,
    encode_base64url,
    run_commitment_job,
)
from app.services.preview_build_service import BuildError, source_identity


COMMITMENT_METADATA_KEY = "published_dataset_commitment"
REATTESTATION_METADATA_KEY = "dataset_reattestation"
REQUIRED_COMMITMENT_FIELDS = (
    "commitment_id",
    "listing_id",
    "seller_dataset_version",
    "schema_digest",
    "dataset_merkle_root",
    "leaf_count",
    "sample_hash",
    "rights_basis_digest",
)

# Only failures that can plausibly succeed without changing the dataset are
# retryable. Canonicalization codes are otherwise deterministic consequences
# of the current local bytes and require a new published version.
TRANSIENT_REATTESTATION_CODES = frozenset(
    {
        "resource_limit",
        "disk_resource_limit",
        "cancelled",
        "worker_failed",
        "job_already_running",
        "source_changed",
        "unsafe_temp_directory",
        "source_unavailable",
    }
)


class ReattestationError(ValueError):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _local_failure(code: str) -> ReattestationError:
    if code in TRANSIENT_REATTESTATION_CODES:
        return ReattestationError(code, retryable=True)
    return ReattestationError("dataset_changed")


def _save(processing, record) -> None:
    storage_name = record.upload_path.name if record.upload_path else record.id
    processing._save_record(record, storage_name)


def persist_published_commitment(processing, record, job, commitment) -> None:
    """Persist producer-originated commitment inputs only after publish succeeds."""
    publication = job["publication"]
    rights = job["rights"]
    stored = {
        "commitment_id": commitment["commitment_id"],
        "listing_id": commitment["listing_id"],
        "seller_dataset_version": commitment["seller_dataset_version"],
        "schema_digest": commitment["schema_digest"],
        "dataset_merkle_root": commitment["dataset_merkle_root"],
        "leaf_count": commitment["leaf_count"],
        "sample_hash": publication["sample_hash"],
        "rights_basis_digest": rights["rights_basis_digest"],
        # Pin the original canonicalization inputs. They came from this build,
        # never from the marketplace.
        "parsing": job["parsing"],
        "schema_descriptors": job["descriptors"],
    }
    existing = record.metadata.get(COMMITMENT_METADATA_KEY)
    record.metadata[COMMITMENT_METADATA_KEY] = stored
    if not isinstance(existing, dict) or existing.get("commitment_id") != stored["commitment_id"]:
        record.metadata[REATTESTATION_METADATA_KEY] = {
            "status": "complete",
            "last_confirmed_at": commitment["signed_at"],
            "last_attempt_at": commitment["signed_at"],
            "last_error": None,
            "retryable": False,
            "progress": None,
        }
    _save(processing, record)


def stored_commitment(record, *, listing_id: str, commitment_id: str) -> dict:
    stored = record.metadata.get(COMMITMENT_METADATA_KEY)
    if not isinstance(stored, dict) or any(
        key not in stored for key in REQUIRED_COMMITMENT_FIELDS
    ):
        raise ReattestationError("published_commitment_unavailable")
    if stored["listing_id"] != listing_id or stored["commitment_id"] != commitment_id:
        raise ReattestationError("new_commitment_required")
    if not isinstance(stored.get("parsing"), dict) or not isinstance(
        stored.get("schema_descriptors"), list
    ):
        raise ReattestationError("published_commitment_unavailable")
    return stored


def persist_reattestation_state(processing, record, **changes) -> None:
    existing = record.metadata.get(REATTESTATION_METADATA_KEY)
    state = dict(existing) if isinstance(existing, dict) else {}
    state.update(changes)
    record.metadata[REATTESTATION_METADATA_KEY] = state
    _save(processing, record)


def verify_unchanged_dataset(
    processing,
    record,
    stored: dict,
    *,
    upload_root,
    temp_root,
) -> dict:
    """Re-read every local row using the original canonicalization contract."""
    try:
        path, source_digest = source_identity(record, upload_root)
    except BuildError as exc:
        raise _local_failure(exc.code) from None
    attempt_at = canonical_rfc3339_utc(datetime.now(timezone.utc))
    persist_reattestation_state(
        processing,
        record,
        status="checking",
        last_attempt_at=attempt_at,
        last_error=None,
        retryable=False,
        progress={
            "phase": "reading",
            "records": 0,
            "canonical_bytes": 0,
            "elapsed_seconds": 0,
        },
    )

    def progress(value):
        persist_reattestation_state(processing, record, progress=value)

    worker_root = (
        Path(temp_root).resolve()
        / "reattestation-builds"
        / hashlib.sha256(canonical_json_bytes([record.id])).hexdigest()
    )
    try:
        result = run_commitment_job(
            [path],
            ParsingDeclaration(**stored["parsing"]),
            stored["schema_descriptors"],
            worker_root,
            progress=progress,
        )
    except CommitmentValidationError as exc:
        raise _local_failure(exc.code) from None

    try:
        _, final_source_digest = source_identity(record, upload_root)
    except BuildError as exc:
        raise _local_failure(exc.code) from None
    if final_source_digest != source_digest:
        raise ReattestationError("source_changed", retryable=True)

    current = result["commitment"]
    if any(
        current[field] != stored[field]
        for field in ("schema_digest", "dataset_merkle_root", "leaf_count")
    ):
        raise ReattestationError("dataset_changed")
    return result


def build_signed_reattestation(
    stored: dict, signer, *, signed_at: str | None = None
) -> dict:
    """Sign freshness only; the stored publish-time rights digest is not re-verified."""
    signed_at = signed_at or canonical_rfc3339_utc(datetime.now(timezone.utc))
    dummy = encode_base64url(bytes(64))
    bound = {
        "listing_id": stored["listing_id"],
        "seller_dataset_version": stored["seller_dataset_version"],
        "schema_digest": stored["schema_digest"],
        "dataset_merkle_root": stored["dataset_merkle_root"],
        "leaf_count": stored["leaf_count"],
        "signed_at": signed_at,
    }
    contract = {
        "commitment_id": stored["commitment_id"],
        **bound,
        "seller_attestation": {
            **bound,
            "sample_hash": stored["sample_hash"],
            "rights_basis_digest": stored["rights_basis_digest"],
            "public_preview_permission": True,
            "metadata_accuracy_confirmed": True,
        },
        "aim_data_signer_reference": signer.signer_reference,
        "signature_algorithm": "ed25519",
        "seller_signature": dummy,
        "update_cadence_days": None,
    }
    DatasetReattestationContract.model_validate(contract)
    return signer.sign_reattestation(contract)
