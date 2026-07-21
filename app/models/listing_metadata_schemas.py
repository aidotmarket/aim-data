"""
Pydantic Schemas for Listing Metadata Generation
=================================================
BQ-085: Transform AIM Data processing results into marketplace-ready metadata.
"""
import base64
import binascii
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional, List
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ColumnSummary(BaseModel):
    name: str
    type: str
    null_percentage: float = 0.0
    uniqueness_ratio: float = 0.0
    sample_values: List[str] = Field(default_factory=list)


class ListingMetadata(BaseModel):
    title: str = Field(..., description="Auto-generated listing title")
    description: str = Field(..., description="Human-readable description from column profiles")
    tags: List[str] = Field(default_factory=list, description="Tags from column names + semantic types")
    column_summary: List[ColumnSummary] = Field(default_factory=list)
    row_count: int = 0
    column_count: int = 0
    file_format: str = ""
    size_bytes: int = 0
    freshness_score: float = Field(0.0, description="0.0-1.0 based on file modification time")
    privacy_score: Optional[float] = Field(None, ge=0.0, le=10.0, description="0-10 scale, 10.0 = no PII detected, 0.0 = high PII risk; None = not scanned")
    data_categories: List[str] = Field(default_factory=list, description="Inferred data categories")
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# BQ-LISTING-ENRICHMENT-S1294 C2: closed non-content wire contracts.
MAX_TREE_SIZE = (1 << 63) - 1
MAX_PROOF_SIBLINGS = 63
MAX_PREVIEW_PROOFS = 50
FORBIDDEN_COMMITMENT_KEYS = {
    "row",
    "rows",
    "data",
    "content",
    "excerpt",
    "record",
    "records",
    "sample",
    "sample_data",
    "sample_rows",
    "blob",
    "blobs",
    "attachment",
    "attachments",
    "source_path",
    "source_credentials",
    "query_string",
    "response_body",
}


def _reject_commitment_carriers(value: Any) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", exclude_none=True)
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_COMMITMENT_KEYS or normalized.startswith("raw_"):
                raise ValueError("raw_content_forbidden")
            _reject_commitment_carriers(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_commitment_carriers(child)
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("raw_content_forbidden")
    elif isinstance(value, str) and len(value.encode("utf-8")) > 4_096:
        raise ValueError("bounded_string_exceeded")


def _decode_b64(value: str, expected_size: int) -> str:
    if not isinstance(value, str) or not value or "=" in value:
        raise ValueError("invalid_hash_encoding")
    try:
        raw = value.encode("ascii")
        decoded = base64.b64decode(raw + b"=" * ((4 - len(raw) % 4) % 4), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ValueError("invalid_hash_encoding") from exc
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value or len(decoded) != expected_size:
        raise ValueError("invalid_hash_encoding")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp_timezone_required")
    return value.astimezone(timezone.utc)


class ClosedCommitmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def reject_raw_content(cls, value: Any) -> Any:
        _reject_commitment_carriers(value)
        return value


class CommitmentProofSibling(ClosedCommitmentModel):
    hash: str
    direction: Literal["left", "right"]

    @field_validator("hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        return _decode_b64(value, 32)


class DatasetPreviewProofSubmission(ClosedCommitmentModel):
    proof_id: UUID
    base_row_digest: str
    duplicate_ordinal: int = Field(ge=0, le=MAX_TREE_SIZE)
    leaf_index: int = Field(ge=0, le=MAX_TREE_SIZE - 1)
    tree_size: int = Field(ge=1, le=MAX_TREE_SIZE)
    siblings: List[CommitmentProofSibling] = Field(max_length=MAX_PROOF_SIBLINGS)
    preview_package_url: str = Field(max_length=2_048)
    package_media_type: Literal["application/vnd.aim.preview+json"]
    package_profile: Literal["aim-preview-package-v1"]
    package_byte_ceiling: int = Field(gt=0, le=128 * 1024)
    scan_policy: Literal["aim-preview-policy-v1"]
    scan_policy_version: str = Field(min_length=1, max_length=80)
    scan_verdict: Literal["passed"]
    scanned_at: datetime
    sampled_leaf_list_digest: str
    signer_reference: str = Field(min_length=1, max_length=255)
    signature_algorithm: Literal["ed25519"] = "ed25519"
    signature: str

    _scanned_at_utc = field_validator("scanned_at")(_utc)

    @field_validator("base_row_digest", "sampled_leaf_list_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return _decode_b64(value, 32)

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, value: str) -> str:
        return _decode_b64(value, 64)

    @field_validator("preview_package_url")
    @classmethod
    def validate_origin_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("preview_origin_https_required")
        if parsed.query or parsed.fragment:
            raise ValueError("preview_origin_query_forbidden")
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> "DatasetPreviewProofSubmission":
        if self.leaf_index >= self.tree_size or self.duplicate_ordinal >= self.tree_size:
            raise ValueError("invalid_inclusion_proof")
        return self


class DatasetCommitmentSubmission(ClosedCommitmentModel):
    commitment_id: UUID
    listing_id: UUID
    seller_dataset_version: str = Field(min_length=1, max_length=255)
    previous_commitment_id: Optional[UUID] = None
    canonicalization_profile: Literal["aim-dataset-merkle-v1"] = "aim-dataset-merkle-v1"
    hash_algorithm: Literal["sha-256"] = "sha-256"
    schema_digest: str
    dataset_merkle_root: str
    leaf_count: int = Field(ge=1, le=MAX_TREE_SIZE)
    seller_attestation_digest: str
    aim_data_signer_reference: str = Field(min_length=1, max_length=255)
    signature_algorithm: Literal["ed25519"] = "ed25519"
    seller_signature: str
    signed_at: datetime
    proofs: List[DatasetPreviewProofSubmission] = Field(default_factory=list, max_length=MAX_PREVIEW_PROOFS)

    _signed_at_utc = field_validator("signed_at")(_utc)

    @field_validator("seller_dataset_version")
    @classmethod
    def validate_opaque_version(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,254}", value) is None:
            raise ValueError("invalid_seller_dataset_version")
        return value

    @field_validator("schema_digest", "dataset_merkle_root", "seller_attestation_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return _decode_b64(value, 32)

    @field_validator("seller_signature")
    @classmethod
    def validate_signature(cls, value: str) -> str:
        return _decode_b64(value, 64)

    @model_validator(mode="after")
    def validate_proofs(self) -> "DatasetCommitmentSubmission":
        seen: set[UUID] = set()
        for proof in self.proofs:
            if proof.tree_size != self.leaf_count:
                raise ValueError("proof_tree_mismatch")
            if proof.proof_id in seen:
                raise ValueError("duplicate_proof_id")
            seen.add(proof.proof_id)
        return self
