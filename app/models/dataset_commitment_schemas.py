"""Closed metadata-only commitment models. Local records never enter these models."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

SAFE_INTEGER = (1 << 53) - 1


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


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
    records: int = Field(ge=0)
    canonical_bytes: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
