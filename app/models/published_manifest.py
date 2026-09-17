"""D0 immutable local snapshots, independent of live registrations/deletion."""
from datetime import datetime, timezone

from sqlalchemy import Column, JSON, Text
from sqlmodel import Field, SQLModel


class PublishedManifest(SQLModel, table=True):
    __tablename__ = "published_manifests"

    listing_version_id: str = Field(primary_key=True, max_length=36)
    manifest_hash: str = Field(primary_key=True, max_length=64)
    dataset_id: str = Field(index=True, max_length=36)
    root_path: str = Field(sa_column=Column(Text, nullable=False))
    members: list[dict] = Field(sa_column=Column(JSON, nullable=False))
    registration_to_published_index: dict[str, int] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
