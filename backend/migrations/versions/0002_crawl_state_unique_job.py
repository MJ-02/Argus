"""Add unique constraint on crawl_state.job_id (one state row per job).

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("uq_crawl_state_job_id", "crawl_state", ["job_id"])


def downgrade() -> None:
    op.drop_constraint("uq_crawl_state_job_id", "crawl_state", type_="unique")
