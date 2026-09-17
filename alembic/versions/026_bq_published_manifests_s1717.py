"""Retain published member snapshots separately from mutable registrations."""
from alembic import op
import sqlalchemy as sa

revision = "026_bq_published_manifests_s1717"
down_revision = "025_bq_multi_file_datasets_s1717"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "published_manifests",
        sa.Column("listing_version_id", sa.String(36), primary_key=True),
        sa.Column("manifest_hash", sa.String(64), primary_key=True),
        sa.Column("dataset_id", sa.String(36), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("members", sa.JSON(), nullable=False),
        sa.Column("registration_to_published_index", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_published_manifests_dataset_id", "published_manifests", ["dataset_id"])


def downgrade():
    op.drop_index("ix_published_manifests_dataset_id", table_name="published_manifests")
    op.drop_table("published_manifests")
