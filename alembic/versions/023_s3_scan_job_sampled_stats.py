"""Add sampled S3 scan aggregate stats.

Revision ID: 023_s3_scan_job_sampled_stats
Revises: 022_remove_vec_statuses
Create Date: 2026-06-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "023_s3_scan_job_sampled_stats"
down_revision: Union[str, None] = "022_remove_vec_statuses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("s3_scan_job", sa.Column("sampled_stats", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("s3_scan_job", "sampled_stats")
