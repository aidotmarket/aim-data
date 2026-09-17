"""Retain registration-to-published indices alongside frozen source paths."""
from alembic import op
import sqlalchemy as sa

revision = "027_bq_published_indices_s1717"
down_revision = "026_bq_published_manifests_s1717"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("published_manifests", sa.Column(
        "registration_to_published_index", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("published_manifests", "registration_to_published_index")
