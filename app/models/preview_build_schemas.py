"""Seller-local options only. These DTOs are never platform requests."""
from typing import Literal
from pydantic import Field
from app.models.dataset_commitment_schemas import ClosedModel

class ParsingOptions(ClosedModel):
    format: Literal['csv', 'tsv', 'json-array', 'ndjson', 'parquet']
    encoding: Literal['utf-8'] | None = None
    delimiter: str | None = Field(None, max_length=1)
    quote: str | None = Field(None, max_length=1)
    escape: str | None = Field(None, max_length=1)
    header: bool | None = None
    locale: Literal['C'] | None = None
    null_token: str | None = Field(None, max_length=80)
    source_timezone: str | None = Field(None, max_length=6)

class CreateBuild(ClosedModel):
    dataset_id: str = Field(min_length=1, max_length=36)
    parsing: ParsingOptions | None = None
    schema_descriptors: list | None = Field(None, max_length=500)

class Selection(ClosedModel):
    leaf_indices: list[int] = Field(max_length=101)
    display_columns: list[str] = Field(max_length=26)

class Consent(ClosedModel):
    rights_basis: Literal['owner', 'licensed', 'public_domain', 'other_authorized']
    public_preview_permission: bool
    restricted_content_confirmed: bool

class PackageOptions(ClosedModel):
    destination: Literal['local', 'export']

class OriginOptions(ClosedModel):
    url: str = Field(min_length=1, max_length=2048)

class CandidateOptions(Consent):
    metadata_accuracy_confirmed: bool

class EmptyOptions(ClosedModel):
    pass

class MetadataApproval(ClosedModel):
    dataset_id: str = Field(min_length=1, max_length=36)
    approved_metadata_digest: str = Field(pattern=r'^[0-9a-f]{64}$', min_length=64, max_length=64)
