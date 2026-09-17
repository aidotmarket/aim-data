"""Directory records and live members; no data migration or flag-off behaviour change."""
from alembic import op
import sqlalchemy as sa

revision = "025_bq_multi_file_datasets_s1717"
down_revision = "024_bq_data_verification_s1590"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("dataset_records", sa.Column("root_path", sa.String(4096), nullable=True))
    op.create_table(
        "dataset_members",
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("dataset_records.id"), primary_key=True),
        sa.Column("index", sa.Integer(), primary_key=True),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("detected_type", sa.String(32), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="data"),
        sa.Column("is_sample", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="current"),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("mtime", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("dataset_id", "relative_path", name="uq_dataset_member_path"),
        sa.CheckConstraint("role IN ('data','documentation','other')", name="ck_member_role"),
        sa.CheckConstraint("status IN ('current','removed','missing','unsupported')", name="ck_member_status"),
        sa.CheckConstraint("NOT is_sample OR role = 'data'", name="ck_member_sample_role"),
        sa.CheckConstraint('"index" >= 0 AND size_bytes >= 0', name="ck_member_nonnegative"),
    )


def downgrade():
    op.drop_table("dataset_members")
    op.drop_column("dataset_records", "root_path")
