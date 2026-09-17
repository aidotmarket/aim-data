"""Closed metadata-only commitment models. Local records never enter these models."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Annotated
from pydantic import AfterValidator, BeforeValidator
from app.services.dataset_merkle_service import (
    canonical_rfc3339_utc,
    decode_base64url,
    canonical_json_bytes,
)
from uuid import UUID


SAFE_INTEGER = (1 << 53) - 1


class ClosedModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
        validate_assignment=True,
        revalidate_instances="always",
    )

    @model_validator(mode="before")
    @classmethod
    def safe_metadata(cls, value):
        from pydantic_core import PydanticCustomError

        def check(item):
            if type(item) is int and abs(item) > SAFE_INTEGER:
                raise PydanticCustomError("unsafe_integer", "unsafe_integer")
            if isinstance(item, dict):
                for child in item.values():
                    check(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    check(child)

        check(value)
        return value


class ProofSibling(ClosedModel):
    hash: str
    direction: Literal["left", "right"]

    @field_validator("hash")
    @classmethod
    def digest(cls, value):
        from app.services.dataset_merkle_service import decode_digest

        decode_digest(value)
        return value


class CommitmentProof(ClosedModel):
    base_row_digest: str
    duplicate_ordinal: int = Field(ge=0, le=SAFE_INTEGER)
    leaf_index: int = Field(ge=0, le=SAFE_INTEGER)
    tree_size: int = Field(ge=1, le=SAFE_INTEGER)
    siblings: list[ProofSibling] = Field(max_length=63)

    @field_validator("base_row_digest")
    @classmethod
    def digest(cls, value):
        from app.services.dataset_merkle_service import decode_digest

        decode_digest(value)
        return value

    @model_validator(mode="after")
    def valid_positions(self):
        if (
            self.leaf_index >= self.tree_size
            or self.duplicate_ordinal >= self.tree_size
        ):
            from pydantic_core import PydanticCustomError

            raise PydanticCustomError(
                "invalid_inclusion_proof", "invalid_inclusion_proof"
            )
        return self


class DatasetCommitment(ClosedModel):
    profile: Literal["aim-dataset-merkle-v1"] = "aim-dataset-merkle-v1"
    schema_digest: str
    dataset_merkle_root: str
    leaf_count: int = Field(ge=1, le=SAFE_INTEGER)

    @field_validator("schema_digest", "dataset_merkle_root")
    @classmethod
    def digest(cls, value):
        from app.services.dataset_merkle_service import decode_digest

        decode_digest(value)
        return value


class CommitmentProgress(ClosedModel):
    phase: Literal[
        "reading",
        "sorting",
        "building_tree",
        "selecting",
        "scanning",
        "hosting",
        "ready",
    ]
    records: int = Field(ge=0, le=SAFE_INTEGER)
    canonical_bytes: int = Field(ge=0, le=SAFE_INTEGER)
    elapsed_seconds: float = Field(ge=0)


# Chunk T wire fields; safe-integer admission is intentionally stricter than
# Chunk 0's signed-63-bit storage range (2a controller decision).


def uuid_text(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_uuid") from None


def timestamp_text(value):
    try:
        return canonical_rfc3339_utc(value)
    except (ValueError, TypeError):
        raise ValueError("invalid_timestamp") from None


def b64_size(value, size):
    if len(decode_base64url(value)) != size:
        raise ValueError("invalid_encoding")
    return value


UUIDText = Annotated[str, BeforeValidator(uuid_text)]
Timestamp = Annotated[str, BeforeValidator(timestamp_text)]
Code = Annotated[
    str, Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._:-]+$")
]
Digest = Annotated[
    str, Field(min_length=43, max_length=43), AfterValidator(lambda v: b64_size(v, 32))
]
Signature = Annotated[
    str, Field(min_length=86, max_length=86), AfterValidator(lambda v: b64_size(v, 64))
]
HexDigest = Annotated[
    str, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
]
SignerReference = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{64}$",
        min_length=101,
        max_length=101,
    ),
]


def reject_content(value, depth=0):
    if depth > 32:
        raise ValueError("metadata_depth")
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                not isinstance(key, str)
                or key.lower().startswith("raw_")
                or key.lower()
                in {
                    "row",
                    "rows",
                    "sample_values",
                    "approved_sample",
                    "approved_rows",
                    "rights_basis_text",
                    "rights_prose",
                    "scan_notes",
                    "notes",
                    "reason_text",
                    "source_path",
                    "credentials",
                    "token",
                    "attachments",
                    "content",
                }
            ):
                raise ValueError("content_carrier")
            reject_content(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            reject_content(child, depth + 1)
    elif isinstance(value, str) and len(value) > 2048:
        raise ValueError("metadata_string_limit")


class WireModel(ClosedModel):
    @model_validator(mode="before")
    @classmethod
    def metadata_only(cls, value):
        reject_content(value)
        return value


class DatasetPreviewProofContract(WireModel):
    proof_id: UUIDText
    base_row_digest: Digest
    duplicate_ordinal: int = Field(ge=0, le=SAFE_INTEGER)
    leaf_index: int = Field(ge=0, le=SAFE_INTEGER)
    tree_size: int = Field(ge=1, le=SAFE_INTEGER)
    siblings: list[ProofSibling] = Field(max_length=63)
    preview_package_url: str = Field(max_length=2048)
    package_media_type: Literal["application/vnd.aim.preview+json"]
    package_profile: Literal["aim-preview-package-v1", "aim-preview-package-v2"]
    package_byte_ceiling: int = Field(gt=0, le=1048576)
    scan_policy: Literal["aim-preview-policy-v1"]
    scan_policy_version: Annotated[
        str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    ]
    scan_verdict: Literal["passed"]
    scanned_at: Timestamp
    sampled_leaf_list_digest: Digest
    signer_reference: SignerReference
    signature_algorithm: Literal["ed25519"] = "ed25519"
    signature: Signature

    @field_validator("preview_package_url")
    @classmethod
    def origin(cls, value):
        from app.services.preview_origin_service import validate_url

        validate_url(value)
        return value

    @model_validator(mode="after")
    def positions(self):
        if (
            self.leaf_index >= self.tree_size
            or self.duplicate_ordinal >= self.tree_size
        ):
            raise ValueError("invalid_inclusion_proof")
        if (
            self.package_profile == "aim-preview-package-v1"
            and self.package_byte_ceiling > 131072
        ):
            raise ValueError("package_limit")
        return self


class DatasetCommitmentContract(WireModel):
    commitment_id: UUIDText
    listing_id: UUIDText
    seller_dataset_version: Code
    previous_commitment_id: UUIDText | None = None
    canonicalization_profile: Literal["aim-dataset-merkle-v1"] = "aim-dataset-merkle-v1"
    hash_algorithm: Literal["sha-256"] = "sha-256"
    schema_digest: Digest
    dataset_merkle_root: Digest
    leaf_count: int = Field(ge=1, le=SAFE_INTEGER)
    seller_attestation_digest: Digest
    aim_data_signer_reference: SignerReference
    signature_algorithm: Literal["ed25519"] = "ed25519"
    seller_signature: Signature
    signed_at: Timestamp
    proofs: list[DatasetPreviewProofContract] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def matching_proofs(self):
        if len({p.proof_id for p in self.proofs}) != len(self.proofs) or any(
            p.tree_size != self.leaf_count for p in self.proofs
        ):
            raise ValueError("proof_mismatch")
        if len({p.package_profile for p in self.proofs}) > 1:
            raise ValueError("mixed_profiles")
        if (
            self.proofs
            and self.proofs[0].package_profile == "aim-preview-package-v1"
            and len(self.proofs) > 50
        ):
            raise ValueError("proof_limit")
        if len(canonical_json_bytes([p.model_dump() for p in self.proofs])) > 262144:
            raise ValueError("manifest_limit")
        return self


class TransparencyCheckpointContract(WireModel):
    log_id: Annotated[
        str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    ]
    tree_size: int = Field(ge=1, le=SAFE_INTEGER)
    root_hash: Digest
    checkpoint_at: Timestamp
    key_id: Code
    public_key_algorithm: Literal["ed25519"] = "ed25519"
    signature: Signature


class TransparencyLogEntryContract(WireModel):
    aim_data_signer_reference: SignerReference
    appended_at: Timestamp
    canonicalization_profile: Literal["aim-dataset-merkle-v1"]
    commitment_id: UUIDText
    dataset_merkle_root: Digest
    hash_algorithm: Literal["sha-256"]
    leaf_count: int = Field(ge=1, le=SAFE_INTEGER)
    listing_id: UUIDText
    previous_commitment_id: UUIDText | None
    schema_digest: Digest
    seller_attestation_digest: Digest
    seller_dataset_version: Code
    seller_signature: Signature
    signature_algorithm: Literal["ed25519"]
    signed_at: Timestamp
    transparency_sequence: int = Field(ge=1, le=SAFE_INTEGER)


class TransparencyLogEvidenceContract(WireModel):
    entry: TransparencyLogEntryContract
    inclusion_path: list[ProofSibling] = Field(max_length=63)
    consistency_path: list[Digest] = Field(max_length=63)
    previous_tree_size: Annotated[int, Field(ge=1, le=SAFE_INTEGER)] | None
    previous_root: Digest | None

    @model_validator(mode="after")
    def predecessor(self):
        if (self.previous_tree_size is None) != (self.previous_root is None):
            raise ValueError("invalid_predecessor")
        if self.previous_tree_size is None and self.consistency_path:
            raise ValueError("invalid_predecessor")
        return self
