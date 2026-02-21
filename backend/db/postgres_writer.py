"""Postgres upsert writer.

All entity writes use INSERT ... ON CONFLICT DO UPDATE semantics so that
crawler retries and incremental re-ingestion are fully idempotent and never
raise constraint violations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.extractors import Author, Institution, Paper
from db.models import AuthorMetadata, CrawlJob, CrawlState, InstitutionMetadata, PaperMetadata

# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------


async def upsert_papers(session: AsyncSession, papers: Sequence[Paper]) -> None:
    """Upsert a batch of papers into ``papers_metadata``."""
    if not papers:
        return
    rows = [
        {
            "openalex_id": p.id,
            "title": p.title,
            "abstract": p.abstract,
            "publication_year": p.publication_year,
            "doi": p.doi,
            "citation_count": p.citation_count,
            "source": p.source,
        }
        for p in papers
    ]
    stmt = pg_insert(PaperMetadata).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["openalex_id"],
        set_={
            "title": stmt.excluded.title,
            "abstract": stmt.excluded.abstract,
            "publication_year": stmt.excluded.publication_year,
            "doi": stmt.excluded.doi,
            "citation_count": stmt.excluded.citation_count,
            "source": stmt.excluded.source,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def upsert_paper(session: AsyncSession, paper: Paper) -> None:
    await upsert_papers(session, [paper])


# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------


async def upsert_authors(session: AsyncSession, authors: Sequence[Author]) -> None:
    """Upsert a batch of authors into ``authors_metadata``."""
    if not authors:
        return
    rows = [
        {
            "openalex_id": a.id,
            "name": a.name,
            "orcid": a.orcid,
            "works_count": a.works_count,
            "citation_count": a.citation_count,
        }
        for a in authors
    ]
    stmt = pg_insert(AuthorMetadata).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["openalex_id"],
        set_={
            "name": stmt.excluded.name,
            "orcid": stmt.excluded.orcid,
            "works_count": stmt.excluded.works_count,
            "citation_count": stmt.excluded.citation_count,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def upsert_author(session: AsyncSession, author: Author) -> None:
    await upsert_authors(session, [author])


# ---------------------------------------------------------------------------
# Institutions
# ---------------------------------------------------------------------------


async def upsert_institutions(session: AsyncSession, institutions: Sequence[Institution]) -> None:
    """Upsert a batch of institutions into ``institutions_metadata``."""
    if not institutions:
        return
    rows = [
        {
            "openalex_id": i.id,
            "name": i.name,
            "country": i.country,
            "institution_type": i.institution_type,
        }
        for i in institutions
    ]
    stmt = pg_insert(InstitutionMetadata).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["openalex_id"],
        set_={
            "name": stmt.excluded.name,
            "country": stmt.excluded.country,
            "institution_type": stmt.excluded.institution_type,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def upsert_institution(session: AsyncSession, institution: Institution) -> None:
    await upsert_institutions(session, [institution])


# ---------------------------------------------------------------------------
# Crawl jobs
# ---------------------------------------------------------------------------


async def create_crawl_job(
    session: AsyncSession,
    seed_config: dict,
    *,
    job_id: str | None = None,
) -> CrawlJob:
    """Insert a new crawl job record and return it.

    A fresh :class:`CrawlState` row is also created so the job can be updated
    via :func:`upsert_crawl_state` immediately.
    """
    job = CrawlJob(
        id=job_id or str(uuid.uuid4()),
        seed_config=seed_config,
        status="pending",
    )
    session.add(job)
    await session.flush()  # populate server defaults (created_at) before adding state

    state = CrawlState(
        job_id=job.id,
        cursor=None,
        last_crawled_at=None,
        records_processed=0,
        last_error=None,
    )
    session.add(state)
    await session.flush()
    return job


async def update_crawl_job_status(
    session: AsyncSession,
    job_id: str,
    status: str,
    *,
    completed_at: datetime | None = None,
) -> None:
    """Set the status (and optionally completed_at) of a crawl job."""
    job = await session.get(CrawlJob, job_id)
    if job is None:
        raise ValueError(f"CrawlJob {job_id!r} not found")
    job.status = status
    if completed_at is not None:
        job.completed_at = completed_at
    await session.flush()


# ---------------------------------------------------------------------------
# Crawl state
# ---------------------------------------------------------------------------


async def upsert_crawl_state(
    session: AsyncSession,
    job_id: str,
    *,
    cursor: str | None = None,
    last_crawled_at: datetime | None = None,
    records_processed: int = 0,
    last_error: str | None = None,
) -> None:
    """Upsert the crawl state row for *job_id*.

    Uses the ``uq_crawl_state_job_id`` unique constraint (migration 0002) so
    that repeated calls are idempotent regardless of how many times the page
    loop re-persists progress.
    """
    stmt = pg_insert(CrawlState).values(
        job_id=job_id,
        cursor=cursor,
        last_crawled_at=last_crawled_at,
        records_processed=records_processed,
        last_error=last_error,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_crawl_state_job_id",
        set_={
            "cursor": stmt.excluded.cursor,
            "last_crawled_at": stmt.excluded.last_crawled_at,
            "records_processed": stmt.excluded.records_processed,
            "last_error": stmt.excluded.last_error,
        },
    )
    await session.execute(stmt)
