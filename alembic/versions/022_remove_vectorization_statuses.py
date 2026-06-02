"""Remove vectorization-only dataset statuses.

Revision ID: 022_remove_vec_statuses
Revises: 021_s3_connection_owner_id
Create Date: 2026-06-01
"""

from typing import Sequence, Union

from alembic import op


revision: str = "022_remove_vec_statuses"
down_revision: Union[str, None] = "021_s3_connection_owner_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE dataset_records "
        "SET status = 'preview_ready' "
        "WHERE status IN ('ready', 'indexing', 's3_linked')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE dataset_records "
        "SET status = 'ready' "
        "WHERE status = 'preview_ready'"
    )
