"""Initial schema: crawl_jobs, crawl_state, papers_metadata, authors_metadata, institutions_metadata

Revision ID: 0001
Revises:
Create Date: 2026-02-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("seed_config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "crawl_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["crawl_jobs.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_crawl_state_job_id", "crawl_state", ["job_id"])

    op.create_table(
        "papers_metadata",
        sa.Column("openalex_id", sa.String(32), primary_key=True, nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("doi", sa.String(255), nullable=True),
        sa.Column("citation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "authors_metadata",
        sa.Column("openalex_id", sa.String(32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(512), nullable=True),
        sa.Column("orcid", sa.String(64), nullable=True),
        sa.Column("works_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("citation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "institutions_metadata",
        sa.Column("openalex_id", sa.String(32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(512), nullable=True),
        sa.Column("country", sa.String(128), nullable=True),
        sa.Column("institution_type", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("institutions_metadata")
    op.drop_table("authors_metadata")
    op.drop_table("papers_metadata")
    op.drop_index("ix_crawl_state_job_id", "crawl_state")
    op.drop_table("crawl_state")
    op.drop_table("crawl_jobs")
