"""Closed metadata-only commitment models. Local records never enter these models."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SAFE_INTEGER = (1 << 53) - 1


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

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
    records: int = Field(ge=0)
    canonical_bytes: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
