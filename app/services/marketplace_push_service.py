"""
Marketplace Push Service for AIM Data
=======================================
BQ-090: Push listing metadata, compliance report, and quality attestation
from local AIM Data instance to ai.market backend API.

Non-custodial: Only metadata is sent — never actual data rows.
Auth: Uses VECTORAIZ_INTERNAL_API_KEY (X-API-Key header) which maps
to the system user on ai.market.

Retry: Exponential backoff (3 attempts, 1s/2s/4s).
Conflict: 409 → PATCH update instead of POST create.
"""
import json
import logging
import asyncio
import hashlib
import hmac
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Mapping, Sequence
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.config import settings
from app.models.listing_metadata_schemas import (
    DatasetCommitmentSubmission,
    DatasetPreviewProofSubmission,
    ListingMetadata,
    SIGNED_AT_MAX_CLOCK_SKEW_SECONDS,
)
from app.models.compliance_schemas import ComplianceReport
from app.models.attestation_schemas import QualityAttestation
from app.services.dataset_canonicalization import (
    LogicalField,
    canonicalize_row,
    compute_schema_digest,
    decode_base64url,
    encode_base64url,
    jcs_canonical_bytes,
)
from app.services.dataset_merkle_service import (
    DatasetMerkleCommitment,
    compute_leaf_hash,
    verify_inclusion_proof,
)
from app.services.preview_content_policy import PreviewPolicyResult
from app.services.preview_package_service import (
    PACKAGE_MEDIA_TYPE,
    PACKAGE_PROFILE,
    PreviewPackage,
    PreviewPackageError,
    PreviewPackageService,
)

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # seconds: 1, 2, 4
REQUEST_TIMEOUT = 30.0
MAX_CLOCK_SKEW_SECONDS = SIGNED_AT_MAX_CLOCK_SKEW_SECONDS
MAX_CLOCK_SKEW = timedelta(seconds=MAX_CLOCK_SKEW_SECONDS)
MAX_ATTESTATION_AGE = timedelta(days=90)


class MarketplacePushError(Exception):
    """Raised when the marketplace push fails after all retries."""
    def __init__(self, message: str, status_code: Optional[int] = None, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class CommitmentClientValidationError(MarketplacePushError):
    """Distinct, stable client-side §3.6 validation failure (M6/M8)."""

    def __init__(self, code: str):
        super().__init__(code, detail={"error_code": code})
        self.code = code


def _b64decode(value: str, size: int) -> bytes:
    try:
        return decode_base64url(value, expected_size=size)
    except Exception as exc:
        raise CommitmentClientValidationError("invalid_hash_encoding") from exc


def _sign(private_key: Ed25519PrivateKey, prefix: bytes, body: Mapping[str, Any]) -> str:
    return encode_base64url(private_key.sign(prefix + jcs_canonical_bytes(body)))


def _verify_signature(
    public_key: Ed25519PublicKey,
    signature: str,
    prefix: bytes,
    body: Mapping[str, Any],
    *,
    code: str,
) -> None:
    try:
        public_key.verify(_b64decode(signature, 64), prefix + jcs_canonical_bytes(body))
    except (InvalidSignature, ValueError) as exc:
        raise CommitmentClientValidationError(code) from exc


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _attestation_payload(
    payload: DatasetCommitmentSubmission,
    *,
    metadata_accuracy_attested: bool = True,
    preview_disclosure_permitted: bool = True,
) -> dict[str, Any]:
    return {
        "listing_id": str(payload.listing_id),
        "seller_dataset_version": payload.seller_dataset_version,
        "canonicalization_profile": payload.canonicalization_profile,
        "schema_digest": payload.schema_digest,
        "dataset_merkle_root": payload.dataset_merkle_root,
        "leaf_count": payload.leaf_count,
        "metadata_accuracy_attested": metadata_accuracy_attested,
        "preview_disclosure_permitted": preview_disclosure_permitted,
        "attested_at": _timestamp_text(payload.signed_at),
    }


def _sampled_leaf_list(proofs: Sequence[DatasetPreviewProofSubmission]) -> list[dict[str, Any]]:
    return [
        {
            "proof_id": str(proof.proof_id),
            "base_row_digest": proof.base_row_digest,
            "duplicate_ordinal": proof.duplicate_ordinal,
            "leaf_index": proof.leaf_index,
            "tree_size": proof.tree_size,
        }
        for proof in proofs
    ]


def _sampled_leaf_list_digest(proofs: Sequence[DatasetPreviewProofSubmission]) -> str:
    return encode_base64url(
        hashlib.sha256(
            b"aim-sampled-leaf-list-v1\0" + jcs_canonical_bytes(_sampled_leaf_list(proofs))
        ).digest()
    )


def _scan_attestation_payload(proof: DatasetPreviewProofSubmission) -> dict[str, Any]:
    return {
        "scan_policy": proof.scan_policy,
        "scan_policy_version": proof.scan_policy_version,
        "scan_verdict": proof.scan_verdict,
        "scanned_at": _timestamp_text(proof.scanned_at),
        "sampled_leaf_list_digest": proof.sampled_leaf_list_digest,
        "signer_reference": proof.signer_reference,
    }


def _commitment_signature_payload(payload: DatasetCommitmentSubmission) -> dict[str, Any]:
    return payload.model_dump(mode="json", exclude={"seller_signature"})


def build_signed_commitment_submission(
    *,
    commitment: DatasetMerkleCommitment,
    schema: Sequence[LogicalField | Mapping[str, Any]],
    package: PreviewPackage,
    policy_result: PreviewPolicyResult,
    listing_id: UUID,
    commitment_id: UUID,
    seller_dataset_version: str,
    preview_package_url: str,
    signer_reference: str,
    private_key: Ed25519PrivateKey,
    signed_at: datetime,
    previous_commitment_id: UUID | None = None,
    previous_submission: DatasetCommitmentSubmission | None = None,
    now: datetime | None = None,
) -> DatasetCommitmentSubmission:
    """Build and sign only the closed non-content contract sent to ai.market."""
    if policy_result.verdict != "passed" or policy_result.row_count != len(package.body.get("entries", [])):
        raise CommitmentClientValidationError("scan_attestation_invalid")
    if not hmac.compare_digest(compute_schema_digest(schema), commitment.schema_digest):
        raise CommitmentClientValidationError("schema_digest_mismatch")
    if package.body.get("commitment_id") != str(commitment_id):
        raise CommitmentClientValidationError("preview_commitment_mismatch")
    if package.body.get("schema_digest") != encode_base64url(commitment.schema_digest):
        raise CommitmentClientValidationError("schema_digest_mismatch")
    try:
        PreviewPackageService.validate_package(package.body, expected_commitment_id=commitment_id)
    except PreviewPackageError as exc:
        raise CommitmentClientValidationError(exc.code) from exc
    # Validate the entire unsigned proof/row binding before any signature is
    # created.  The rows remain local and are discarded from the wire contract.
    seen_positions: set[int] = set()
    seen_leaf_identities: set[tuple[str, int]] = set()
    for entry in package.body["entries"]:
        leaf_index = entry["leaf_index"]
        if leaf_index in seen_positions:
            raise CommitmentClientValidationError("duplicate_leaf_index")
        seen_positions.add(leaf_index)
        leaf_identity = (entry["base_row_digest"], entry["duplicate_ordinal"])
        if leaf_identity in seen_leaf_identities:
            raise CommitmentClientValidationError("duplicate_leaf_identity")
        seen_leaf_identities.add(leaf_identity)
        local_record = canonicalize_row(
            schema,
            entry["row"],
            schema_digest=commitment.schema_digest,
        )
        if not hmac.compare_digest(
            local_record.base_row_digest,
            _b64decode(entry["base_row_digest"], 32),
        ):
            raise CommitmentClientValidationError("preview_row_digest_mismatch")
        leaf_hash = compute_leaf_hash(local_record.base_row_digest, entry["duplicate_ordinal"])
        if entry["tree_size"] != commitment.leaf_count or not verify_inclusion_proof(
            leaf_hash,
            leaf_index,
            entry["tree_size"],
            entry["siblings"],
            commitment.dataset_merkle_root,
        ):
            raise CommitmentClientValidationError("invalid_inclusion_proof")
    signed_at = signed_at.astimezone(timezone.utc) if signed_at.tzinfo else signed_at
    if signed_at.tzinfo is None:
        raise CommitmentClientValidationError("attestation_timestamp_timezone_required")
    validation_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _validate_signed_at(signed_at, validation_time)
    if policy_result.scanned_at > validation_time + MAX_CLOCK_SKEW:
        raise CommitmentClientValidationError("scan_timestamp_in_future")
    if policy_result.scanned_at < signed_at - MAX_ATTESTATION_AGE:
        raise CommitmentClientValidationError("scan_attestation_stale")
    if policy_result.scanned_at > signed_at + MAX_CLOCK_SKEW:
        raise CommitmentClientValidationError("scan_timestamp_after_signature")
    proof_dicts: list[dict[str, Any]] = []
    for entry in package.body["entries"]:
        proof_dicts.append(
            {
                "proof_id": entry["proof_id"],
                "base_row_digest": entry["base_row_digest"],
                "duplicate_ordinal": entry["duplicate_ordinal"],
                "leaf_index": entry["leaf_index"],
                "tree_size": entry["tree_size"],
                "siblings": entry["siblings"],
                "preview_package_url": preview_package_url,
                "package_media_type": PACKAGE_MEDIA_TYPE,
                "package_profile": PACKAGE_PROFILE,
                "package_byte_ceiling": len(package.encoded),
                "scan_policy": policy_result.policy,
                "scan_policy_version": policy_result.policy_version,
                "scan_verdict": "passed",
                "scanned_at": policy_result.scanned_at,
                "sampled_leaf_list_digest": encode_base64url(b"\0" * 32),
                "signer_reference": signer_reference,
                "signature_algorithm": "ed25519",
                "signature": encode_base64url(b"\0" * 64),
            }
        )
    provisional_proofs = [DatasetPreviewProofSubmission(**item) for item in proof_dicts]
    leaf_list_digest = _sampled_leaf_list_digest(provisional_proofs)
    for item in proof_dicts:
        item["sampled_leaf_list_digest"] = leaf_list_digest
    unsigned = DatasetCommitmentSubmission(
        commitment_id=commitment_id,
        listing_id=listing_id,
        seller_dataset_version=seller_dataset_version,
        previous_commitment_id=previous_commitment_id,
        canonicalization_profile="aim-dataset-merkle-v1",
        hash_algorithm="sha-256",
        schema_digest=encode_base64url(commitment.schema_digest),
        dataset_merkle_root=encode_base64url(commitment.dataset_merkle_root),
        leaf_count=commitment.leaf_count,
        seller_attestation_digest=encode_base64url(b"\0" * 32),
        aim_data_signer_reference=signer_reference,
        signature_algorithm="ed25519",
        seller_signature=encode_base64url(b"\0" * 64),
        signed_at=signed_at,
        proofs=proof_dicts,
    )
    attestation_digest = encode_base64url(
        hashlib.sha256(
            b"aim-seller-attestation-v1\0" + jcs_canonical_bytes(_attestation_payload(unsigned))
        ).digest()
    )
    unsigned.seller_attestation_digest = attestation_digest
    # M6: every check that does not require a signature runs before the first
    # signature is created. The final validation below only adds cryptographic
    # verification of the signatures produced after this point.
    validate_commitment_submission(
        unsigned,
        schema=schema,
        public_key=private_key.public_key(),
        expected_listing_id=listing_id,
        expected_signer_reference=signer_reference,
        now=validation_time,
        previous_submission=previous_submission,
        verify_signatures=False,
    )
    for proof in unsigned.proofs:
        proof.signature = _sign(
            private_key,
            b"aim-preview-scan-attestation-v1\0",
            _scan_attestation_payload(proof),
        )
    unsigned.seller_signature = _sign(
        private_key,
        b"aim-dataset-commitment-submission-v1\0",
        _commitment_signature_payload(unsigned),
    )
    validate_commitment_submission(
        unsigned,
        schema=schema,
        public_key=private_key.public_key(),
        expected_listing_id=listing_id,
        expected_signer_reference=signer_reference,
        now=validation_time,
        previous_submission=previous_submission,
    )
    return unsigned


def _validate_signed_at(signed_at: datetime, now: datetime) -> None:
    """Apply the explicit inclusive five-minute clock-skew contract (M7)."""
    if signed_at > now + MAX_CLOCK_SKEW:
        raise CommitmentClientValidationError("attestation_timestamp_in_future")
    if signed_at < now - MAX_ATTESTATION_AGE:
        raise CommitmentClientValidationError("attestation_stale")


def validate_commitment_submission(
    payload: DatasetCommitmentSubmission,
    *,
    schema: Sequence[LogicalField | Mapping[str, Any]] | None,
    public_key: Ed25519PublicKey,
    expected_listing_id: UUID,
    expected_signer_reference: str,
    now: datetime | None = None,
    update_cadence_days: int | None = None,
    previous_submission: DatasetCommitmentSubmission | None = None,
    verify_signatures: bool = True,
) -> None:
    """Enforce all client-verifiable §3.6 gates before any network call."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if len(jcs_canonical_bytes(payload.model_dump(mode="json"))) > 256 * 1024:
        raise CommitmentClientValidationError("proof_bound_exceeded")
    if payload.listing_id != expected_listing_id:
        raise CommitmentClientValidationError("commitment_listing_mismatch")
    if payload.aim_data_signer_reference != expected_signer_reference:
        raise CommitmentClientValidationError("signer_mismatch")
    if payload.canonicalization_profile != "aim-dataset-merkle-v1" or payload.hash_algorithm != "sha-256":
        raise CommitmentClientValidationError("unsupported_commitment_profile")
    signed_at = payload.signed_at.astimezone(timezone.utc)
    _validate_signed_at(signed_at, now)
    cadence_window = MAX_ATTESTATION_AGE
    if update_cadence_days is not None:
        if isinstance(update_cadence_days, bool) or update_cadence_days < 0:
            raise CommitmentClientValidationError("invalid_update_cadence")
        cadence_window = min(MAX_ATTESTATION_AGE, max(timedelta(days=7), timedelta(days=2 * update_cadence_days)))
    if signed_at < now - cadence_window:
        raise CommitmentClientValidationError("attestation_stale")
    if schema is not None:
        expected_schema_digest = compute_schema_digest(schema)
        if not hmac.compare_digest(expected_schema_digest, _b64decode(payload.schema_digest, 32)):
            raise CommitmentClientValidationError("schema_digest_mismatch")
    expected_attestation = hashlib.sha256(
        b"aim-seller-attestation-v1\0" + jcs_canonical_bytes(_attestation_payload(payload))
    ).digest()
    if not hmac.compare_digest(expected_attestation, _b64decode(payload.seller_attestation_digest, 32)):
        raise CommitmentClientValidationError("seller_attestation_invalid")
    expected_leaf_list_digest = _sampled_leaf_list_digest(payload.proofs)
    root = _b64decode(payload.dataset_merkle_root, 32)
    seen_leaf_positions: set[int] = set()
    seen_leaf_identities: set[tuple[str, int]] = set()
    seen_proof_ids: set[UUID] = set()
    for proof in payload.proofs:
        if proof.proof_id in seen_proof_ids:
            raise CommitmentClientValidationError("duplicate_proof_id")
        seen_proof_ids.add(proof.proof_id)
        if proof.leaf_index in seen_leaf_positions:
            raise CommitmentClientValidationError("duplicate_leaf_index")
        seen_leaf_positions.add(proof.leaf_index)
        leaf_identity = (proof.base_row_digest, proof.duplicate_ordinal)
        if leaf_identity in seen_leaf_identities:
            raise CommitmentClientValidationError("duplicate_leaf_identity")
        seen_leaf_identities.add(leaf_identity)
        if proof.tree_size != payload.leaf_count:
            raise CommitmentClientValidationError("proof_tree_mismatch")
        if proof.scanned_at > now + MAX_CLOCK_SKEW:
            raise CommitmentClientValidationError("scan_timestamp_in_future")
        if proof.scanned_at < signed_at - cadence_window or proof.scanned_at > signed_at + MAX_CLOCK_SKEW:
            raise CommitmentClientValidationError("scan_attestation_stale")
        if proof.sampled_leaf_list_digest != expected_leaf_list_digest:
            raise CommitmentClientValidationError("scan_leaf_list_mismatch")
        if verify_signatures:
            _verify_signature(
                public_key,
                proof.signature,
                b"aim-preview-scan-attestation-v1\0",
                _scan_attestation_payload(proof),
                code="scan_attestation_invalid",
            )
        leaf = compute_leaf_hash(_b64decode(proof.base_row_digest, 32), proof.duplicate_ordinal)
        if not verify_inclusion_proof(leaf, proof.leaf_index, proof.tree_size, proof.siblings, root):
            raise CommitmentClientValidationError("invalid_inclusion_proof")
    if verify_signatures:
        _verify_signature(
            public_key,
            payload.seller_signature,
            b"aim-dataset-commitment-submission-v1\0",
            _commitment_signature_payload(payload),
            code="device_signature_invalid",
        )
    if previous_submission is not None and previous_submission.seller_dataset_version != payload.seller_dataset_version:
        old_proof_ids = {proof.proof_id for proof in previous_submission.proofs}
        new_proof_ids = {proof.proof_id for proof in payload.proofs}
        if (
            previous_submission.commitment_id == payload.commitment_id
            or hmac.compare_digest(
                _b64decode(previous_submission.dataset_merkle_root, 32),
                _b64decode(payload.dataset_merkle_root, 32),
            )
            or old_proof_ids & new_proof_ids
        ):
            raise CommitmentClientValidationError("dataset_version_reuse")


def assert_non_custodial_commitment_wire(payload: Mapping[str, Any]) -> None:
    """Reject every raw-content carrier immediately before transport."""
    forbidden = {
        "row", "rows", "data", "content", "excerpt", "record", "records",
        "sample", "sample_data", "sample_rows", "source_path", "source_credentials",
        "query_string", "response_body", "package_response_body",
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower()
                if normalized in forbidden or normalized.startswith("raw_"):
                    raise CommitmentClientValidationError("raw_content_forbidden")
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)
        elif isinstance(value, (bytes, bytearray, memoryview)):
            raise CommitmentClientValidationError("raw_content_forbidden")

    walk(payload)


def load_listing_metadata(base_path: Path) -> ListingMetadata:
    """Load listing metadata from processing output."""
    meta_path = base_path / "listing_metadata.json"
    if not meta_path.exists():
        raise MarketplacePushError(
            f"Listing metadata not found at {meta_path}. "
            "Run the processing pipeline first."
        )
    with open(meta_path) as f:
        data = json.load(f)
    return ListingMetadata(**data)


def load_compliance_report(base_path: Path) -> Optional[ComplianceReport]:
    """Load compliance report if available."""
    report_path = base_path / "compliance_report.json"
    if not report_path.exists():
        logger.info("No compliance report found — pushing without compliance data")
        return None
    try:
        with open(report_path) as f:
            data = json.load(f)
        return ComplianceReport(**data)
    except Exception as e:
        logger.warning(f"Failed to load compliance report: {e}")
        return None


def load_attestation(base_path: Path) -> Optional[QualityAttestation]:
    """Load quality attestation if available."""
    att_path = base_path / "attestation.json"
    if not att_path.exists():
        logger.info("No attestation found — pushing without attestation data")
        return None
    try:
        with open(att_path) as f:
            data = json.load(f)
        return QualityAttestation(**data)
    except Exception as e:
        logger.warning(f"Failed to load attestation: {e}")
        return None


class MarketplacePushService:
    """
    Pushes processed dataset metadata to ai.market.
    """

    def __init__(self):
        self.base_url = settings.ai_market_url.rstrip("/")
        self.api_key = settings.internal_api_key
        if not self.api_key:
            logger.warning("VECTORAIZ_INTERNAL_API_KEY not set — marketplace push will fail auth")

    async def push_dataset_commitment(
        self,
        payload: DatasetCommitmentSubmission,
        *,
        schema: Sequence[LogicalField | Mapping[str, Any]],
        public_key: Ed25519PublicKey,
        expected_signer_reference: str,
        auth_headers: Mapping[str, str],
        now: datetime | None = None,
        update_cadence_days: int | None = None,
        previous_submission: DatasetCommitmentSubmission | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> Dict[str, Any]:
        """Validate locally, then send the closed contract without row content.

        A 2xx response is recorded only as backend acceptance; it is never used
        as evidence that the payload itself was valid (M6).
        """
        validate_commitment_submission(
            payload,
            schema=schema,
            public_key=public_key,
            expected_listing_id=payload.listing_id,
            expected_signer_reference=expected_signer_reference,
            now=now,
            update_cadence_days=update_cadence_days,
            previous_submission=previous_submission,
        )
        wire_payload = payload.model_dump(mode="json")
        assert_non_custodial_commitment_wire(wire_payload)
        allowed_headers = {
            key: value
            for key, value in auth_headers.items()
            if key.lower() in {"authorization", "x-api-key"}
        }
        headers = {**allowed_headers, "Content-Type": "application/json"}
        owned_client = client is None
        request_client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        try:
            response = await request_client.post(
                f"{self.base_url}/api/v1/listings/{payload.listing_id}/commitments",
                json=wire_payload,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise MarketplacePushError("commitment_push_timeout", detail={"error_code": "commitment_push_timeout"}) from exc
        except httpx.RequestError as exc:
            raise MarketplacePushError("commitment_push_unavailable", detail={"error_code": "commitment_push_unavailable"}) from exc
        finally:
            if owned_client:
                await request_client.aclose()
        if response.status_code not in (200, 201):
            error_code = "commitment_rejected"
            try:
                response_data = response.json()
                candidate = response_data.get("error_code") or response_data.get("detail")
                if isinstance(candidate, str) and candidate.replace("_", "").isalnum() and len(candidate) <= 80:
                    error_code = candidate
            except Exception:
                pass
            raise MarketplacePushError(error_code, status_code=response.status_code, detail={"error_code": error_code})
        response_data: Mapping[str, Any]
        try:
            parsed = response.json()
            response_data = parsed if isinstance(parsed, dict) else {}
        except Exception:
            response_data = {}
        return {
            "status": "accepted",
            "commitment_id": str(payload.commitment_id),
            "local_validation": "passed",
            "backend_accepted": True,
            "transparency_sequence": response_data.get("transparency_sequence"),
            "checkpoint_size": response_data.get("checkpoint_size"),
        }

    async def push_to_marketplace(
        self,
        dataset_id: str,
        price: float = 25.0,
        category: str = "tabular",
        model_provider: str = "local",
        listing_metadata_override: Optional[ListingMetadata] = None,
    ) -> Dict[str, Any]:
        """
        Push a processed dataset to ai.market.

        Reads listing_metadata.json, compliance report, and attestation
        from /data/processed/{dataset_id}/, builds the ListingCreate payload,
        and POSTs to the marketplace API.

        Args:
            dataset_id: The local dataset identifier.
            price: Listing price in USD (minimum $25).
            category: Primary category slug for the listing.
            model_provider: AI model provider used for analysis.

        Returns:
            Dict with marketplace listing ID, URL, and status.

        Raises:
            MarketplacePushError: If push fails after all retries.
        """
        base_path = Path(f"/data/processed/{dataset_id}")

        # 1. Load local processing results
        listing_metadata = listing_metadata_override or self._load_listing_metadata(base_path)
        compliance = self._load_compliance_report(base_path)
        attestation = self._load_attestation(base_path)

        # 2. Build ListingCreate payload
        payload = self._build_payload(
            listing_metadata=listing_metadata,
            compliance=compliance,
            attestation=attestation,
            price=price,
            category=category,
            model_provider=model_provider,
        )

        # 3. Push to ai.market with retry
        result = await self._push_with_retry(payload)

        # 4. Save result locally
        self._save_publish_result(base_path, result)

        logger.info(f"Dataset {dataset_id} published to ai.market: {result.get('listing_id', 'unknown')}")
        return result

    # ---- Data Loading ----

    def _load_listing_metadata(self, base_path: Path) -> ListingMetadata:
        """Load listing metadata from processing output."""
        return load_listing_metadata(base_path)

    def _load_compliance_report(self, base_path: Path) -> Optional[ComplianceReport]:
        """Load compliance report if available."""
        return load_compliance_report(base_path)

    def _load_attestation(self, base_path: Path) -> Optional[QualityAttestation]:
        """Load quality attestation if available."""
        return load_attestation(base_path)

    # ---- Payload Building ----

    def _build_payload(
        self,
        listing_metadata: ListingMetadata,
        compliance: Optional[ComplianceReport],
        attestation: Optional[QualityAttestation],
        price: float,
        category: str,
        model_provider: str,
    ) -> Dict[str, Any]:
        """Map local processing results to ai.market ListingCreate schema."""

        # Build schema_info from column summaries (no actual data)
        schema_info: Dict[str, Any] = {
            "columns": [
                {
                    "name": col.name,
                    "type": col.type,
                    "null_percentage": col.null_percentage,
                    "uniqueness_ratio": col.uniqueness_ratio,
                }
                for col in listing_metadata.column_summary
            ],
            "row_count": listing_metadata.row_count,
            "column_count": listing_metadata.column_count,
            "file_format": listing_metadata.file_format,
            "size_bytes": listing_metadata.size_bytes,
        }

        # Map privacy_score: canonical 0-10 scale throughout pipeline; null tolerated (publishes as 'not_scanned')
        privacy_score_payload = listing_metadata.privacy_score if listing_metadata.privacy_score is not None else None

        # Map compliance status
        compliance_status = "not_checked"
        compliance_details = None
        if compliance:
            if compliance.compliance_score >= 90:
                compliance_status = "low_risk"
            elif compliance.compliance_score >= 60:
                compliance_status = "medium_risk"
            else:
                compliance_status = "high_risk"
            compliance_details = {
                "score": compliance.compliance_score,
                "pii_entities": compliance.pii_entities_found,
                "flags": [f.model_dump() for f in compliance.flags],
            }

        # Use first data_category as primary category, rest as secondary
        primary_category = category
        secondary_categories = None
        if listing_metadata.data_categories:
            primary_category = listing_metadata.data_categories[0]
            if len(listing_metadata.data_categories) > 1:
                secondary_categories = listing_metadata.data_categories[1:]

        payload: Dict[str, Any] = {
            "title": listing_metadata.title[:255],
            "description": listing_metadata.description[:10000],
            "price": max(price, 25.0),
            "model_provider": model_provider,
            "category": primary_category,
            "secondary_categories": secondary_categories,
            "tags": listing_metadata.tags[:20],
            "schema_info": schema_info,
            "privacy_score": privacy_score_payload,
            "privacy_scan_status": "scanned" if privacy_score_payload is not None else "not_scanned",
            "compliance_status": compliance_status,
            "compliance_details": compliance_details,
            "data_format": listing_metadata.file_format or "parquet",
            "source_row_count": listing_metadata.row_count,
            "source_column_count": listing_metadata.column_count,
        }

        # Add attestation data if available
        if attestation:
            payload["schema_info"]["attestation"] = {
                "data_hash": attestation.data_hash,
                "attestation_hash": attestation.attestation_hash,
                "completeness_score": attestation.completeness_score,
                "type_consistency_score": attestation.type_consistency_score,
                "freshness_score": attestation.freshness_score,
                "quality_grade": attestation.quality_grade,
                "generated_at": attestation.generated_at,
            }

        return payload

    # ---- HTTP Push with Retry ----

    async def _push_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        POST to ai.market with exponential backoff retry.
        On 409 conflict, attempts PATCH update instead.
        """
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key or "",
        }
        create_url = f"{self.base_url}/api/v1/listings/"

        last_error: Optional[Exception] = None

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            for attempt in range(MAX_RETRIES):
                try:
                    response = await client.post(
                        create_url,
                        json=payload,
                        headers=headers,
                    )

                    if response.status_code == 201:
                        data = response.json()
                        return {
                            "status": "created",
                            "listing_id": data.get("id"),
                            "marketplace_url": f"{self.base_url}/listing/{data.get('id')}",
                            "published_at": datetime.now(timezone.utc).isoformat(),
                            "response": data,
                        }

                    if response.status_code == 409:
                        # Listing already exists — try update
                        logger.info("Listing already exists (409), attempting update...")
                        return await self._update_existing(client, headers, payload, response)

                    if response.status_code in (401, 403):
                        raise MarketplacePushError(
                            f"Authentication failed ({response.status_code}). "
                            "Check VECTORAIZ_INTERNAL_API_KEY.",
                            status_code=response.status_code,
                            detail=response.text,
                        )

                    if response.status_code >= 500:
                        # Server error — retry
                        last_error = MarketplacePushError(
                            f"Server error {response.status_code}",
                            status_code=response.status_code,
                            detail=response.text,
                        )
                        logger.warning(
                            f"Marketplace push attempt {attempt + 1}/{MAX_RETRIES} "
                            f"failed: {response.status_code}"
                        )
                    else:
                        # Client error (4xx, not 409) — don't retry
                        raise MarketplacePushError(
                            f"Marketplace rejected listing: {response.status_code}",
                            status_code=response.status_code,
                            detail=response.text,
                        )

                except httpx.RequestError as exc:
                    last_error = MarketplacePushError(
                        f"Network error: {exc}",
                        detail=str(exc),
                    )
                    logger.warning(
                        f"Marketplace push attempt {attempt + 1}/{MAX_RETRIES} "
                        f"network error: {exc}"
                    )

                # Exponential backoff
                if attempt < MAX_RETRIES - 1:
                    backoff = BACKOFF_BASE * (2 ** attempt)
                    logger.info(f"Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)

        raise MarketplacePushError(
            f"Marketplace push failed after {MAX_RETRIES} attempts",
            detail=str(last_error),
        )

    async def _update_existing(
        self,
        client: httpx.AsyncClient,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        conflict_response: httpx.Response,
    ) -> Dict[str, Any]:
        """
        Handle 409 conflict by extracting existing listing ID and PATCHing.
        """
        # Try to extract listing ID from the 409 response
        listing_id = None
        try:
            conflict_data = conflict_response.json()
            listing_id = conflict_data.get("existing_listing_id") or conflict_data.get("id")
        except Exception:
            pass

        if not listing_id:
            # If we can't get the ID from the 409, search for it by title
            logger.warning("No listing ID in 409 response — searching by title")
            listing_id = await self._find_listing_by_title(client, headers, payload["title"])

        if not listing_id:
            raise MarketplacePushError(
                "Listing conflict (409) but couldn't find existing listing to update",
                status_code=409,
            )

        # Build update payload (subset of fields that ListingUpdate accepts)
        update_payload = {
            k: v for k, v in payload.items()
            if k in (
                "title", "description", "price", "category",
                "secondary_categories", "tags", "model_provider",
            ) and v is not None
        }

        update_url = f"{self.base_url}/api/v1/listings/{listing_id}"
        response = await client.patch(update_url, json=update_payload, headers=headers)

        if response.status_code == 200:
            data = response.json()
            return {
                "status": "updated",
                "listing_id": listing_id,
                "marketplace_url": f"{self.base_url}/listing/{listing_id}",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "response": data,
            }

        raise MarketplacePushError(
            f"Failed to update existing listing {listing_id}: {response.status_code}",
            status_code=response.status_code,
            detail=response.text,
        )

    async def _find_listing_by_title(
        self,
        client: httpx.AsyncClient,
        headers: Dict[str, str],
        title: str,
    ) -> Optional[str]:
        """Search for a listing by title to resolve 409 conflicts."""
        try:
            search_url = f"{self.base_url}/api/v1/listings/mine"
            response = await client.get(search_url, headers=headers)
            if response.status_code == 200:
                listings = response.json()
                for listing in listings:
                    if listing.get("title") == title:
                        return listing.get("id")
        except Exception as e:
            logger.warning(f"Failed to search for existing listing: {e}")
        return None

    # ---- Result Persistence ----

    def _save_publish_result(self, base_path: Path, result: Dict[str, Any]) -> None:
        """Save publish result to local filesystem."""
        result_path = base_path / "publish_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        with open(result_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info(f"Publish result saved: {result_path}")


def get_marketplace_push_service() -> MarketplacePushService:
    """Factory function for dependency injection."""
    return MarketplacePushService()
