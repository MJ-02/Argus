"""Core crawl orchestration engine.

Ties together the OpenAlex client, extraction layer, and storage writers
into a resumable, idempotent crawl pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from neo4j import AsyncDriver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.client import OpenAlexClient
from crawler.extractors import (
    extract_affiliations,
    extract_author,
    extract_authorships,
    extract_citations,
    extract_institution,
    extract_paper,
    extract_paper_topics,
    extract_topic,
)
from crawler.openalex import WorksFilter, iter_works
from db.models import CrawlJob as CrawlJobModel, CrawlState
from db.neo4j_writer import (
    merge_affiliated_with_rels,
    merge_authors,
    merge_cites_rels,
    merge_has_topic_rels,
    merge_institutions,
    merge_papers,
    merge_topics,
    merge_wrote_rels,
)
from db.postgres_writer import (
    create_crawl_job,
    update_crawl_job_status,
    upsert_authors,
    upsert_crawl_state,
    upsert_institutions,
    upsert_papers,
)
from shared.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Seed config
# ---------------------------------------------------------------------------


@dataclass
class SeedConfig:
    """Seed configuration for a crawl job.

    Maps 1-to-1 with the ``seed_config`` JSON column in ``crawl_jobs``.
    """

    topic_id: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    institution_id: str | None = None
    paper_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "institution_id": self.institution_id,
            "paper_ids": self.paper_ids,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SeedConfig":
        return cls(
            topic_id=data.get("topic_id"),
            date_from=data.get("date_from"),
            date_to=data.get("date_to"),
            institution_id=data.get("institution_id"),
            paper_ids=data.get("paper_ids") or [],
        )

    def to_works_filter(self) -> WorksFilter:
        return WorksFilter(
            topic_id=self.topic_id,
            date_from=self.date_from,
            date_to=self.date_to,
            institution_id=self.institution_id,
            paper_ids=self.paper_ids,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_crawl_state(
    session: AsyncSession,
    job_id: str,
) -> tuple[str, int, datetime | None]:
    """Return ``(cursor, records_processed, last_crawled_at)`` for *job_id*.

    Falls back to ``("*", 0, None)`` when no state row exists yet.
    """
    result = await session.execute(
        select(CrawlState).where(CrawlState.job_id == job_id)
    )
    state = result.scalar_one_or_none()
    if state is None:
        return "*", 0, None
    return state.cursor or "*", state.records_processed or 0, state.last_crawled_at


async def _is_stop_requested(session: AsyncSession, job_id: str) -> bool:
    """Return ``True`` when the job status has been set to ``"stopping"``."""
    result = await session.execute(
        select(CrawlJobModel.status).where(CrawlJobModel.id == job_id)
    )
    status = result.scalar_one_or_none()
    return status == "stopping"


def _extract_page_entities(raw_works: list[dict[str, Any]]) -> dict[str, list]:
    """Extract all domain entities and relationships from a page of raw works.

    Deduplicates authors, institutions, and topics within the page using
    their IDs as dict keys.

    Returns a dict with keys:
        papers, authors, institutions, topics,
        authorships, affiliations, citations, paper_topics
    """
    papers = []
    authors_map: dict[str, Any] = {}
    institutions_map: dict[str, Any] = {}
    topics_map: dict[str, Any] = {}
    authorships = []
    affiliations = []
    citations = []
    paper_topics = []

    for raw in raw_works:
        try:
            paper = extract_paper(raw)
        except (ValueError, KeyError):
            logger.warning(
                "Skipping malformed work",
                extra={"raw_id": raw.get("id")},
            )
            continue

        papers.append(paper)
        citations.extend(extract_citations(raw))
        paper_topics.extend(extract_paper_topics(raw))
        authorships.extend(extract_authorships(raw))
        affiliations.extend(extract_affiliations(raw))

        for authorship in raw.get("authorships") or []:
            author_raw = authorship.get("author") or {}
            try:
                author = extract_author(author_raw)
                authors_map[author.id] = author
            except ValueError:
                pass

            for inst_raw in authorship.get("institutions") or []:
                try:
                    inst = extract_institution(inst_raw)
                    institutions_map[inst.id] = inst
                except ValueError:
                    pass

        for topic_raw in raw.get("topics") or []:
            try:
                topic = extract_topic(topic_raw)
                topics_map[topic.id] = topic
            except ValueError:
                pass

    return {
        "papers": papers,
        "authors": list(authors_map.values()),
        "institutions": list(institutions_map.values()),
        "topics": list(topics_map.values()),
        "authorships": authorships,
        "affiliations": affiliations,
        "citations": citations,
        "paper_topics": paper_topics,
    }


# ---------------------------------------------------------------------------
# Main crawl loop
# ---------------------------------------------------------------------------


async def run_crawl(
    job_id: str | None,
    seed_config: SeedConfig,
    pg_session: AsyncSession,
    neo4j_driver: AsyncDriver,
    *,
    incremental: bool = False,
    openalex_client: OpenAlexClient | None = None,
) -> str:
    """Run a full crawl job and return the final ``job_id``.

    If *job_id* is ``None`` a new crawl job is created in Postgres; otherwise
    the existing job is resumed from its persisted cursor.

    When *incremental* is ``True`` only works updated after
    ``crawl_state.last_crawled_at`` are fetched from OpenAlex.

    When *openalex_client* is provided it is used as-is (useful for tests);
    otherwise a fresh :class:`~crawler.client.OpenAlexClient` is created and
    managed as a context manager for the duration of the crawl.

    Args:
        job_id: Existing crawl job ID to resume, or ``None`` to create a new job.
        seed_config: Seed configuration defining the scope of the crawl.
        pg_session: Active SQLAlchemy async session (caller owns lifecycle).
        neo4j_driver: Active Neo4j async driver (caller owns lifecycle).
        incremental: Filter to works updated since ``last_crawled_at``.
        openalex_client: Optional pre-constructed client; caller owns lifecycle.

    Returns:
        The ``job_id`` (useful when a new job was created).

    Raises:
        ValueError: When a provided *job_id* does not exist in the database.
    """
    # -------------------------------------------------------------------------
    # 1. Load or create the crawl job
    # -------------------------------------------------------------------------
    if job_id is None:
        job = await create_crawl_job(pg_session, seed_config.to_dict())
        await pg_session.commit()
        job_id = job.id
        logger.info("Created crawl job", extra={"job_id": job_id})
    else:
        job_row = await pg_session.get(CrawlJobModel, job_id)
        if job_row is None:
            raise ValueError(f"CrawlJob {job_id!r} not found")
        logger.info("Resuming crawl job", extra={"job_id": job_id})

    cursor, records_processed, last_crawled_at = await _load_crawl_state(
        pg_session, job_id
    )

    await update_crawl_job_status(pg_session, job_id, "running")
    await pg_session.commit()

    # -------------------------------------------------------------------------
    # 2. Incremental filter
    # -------------------------------------------------------------------------
    updated_after: str | None = None
    if incremental and last_crawled_at is not None:
        updated_after = last_crawled_at.strftime("%Y-%m-%d")
        logger.info(
            "Incremental mode: filtering works updated after %s",
            updated_after,
            extra={"job_id": job_id},
        )

    works_filter = seed_config.to_works_filter()

    # -------------------------------------------------------------------------
    # 3. Client lifecycle
    # -------------------------------------------------------------------------
    managed = openalex_client is None
    client: OpenAlexClient = openalex_client or OpenAlexClient()

    try:
        if managed:
            await client.__aenter__()

        # ---------------------------------------------------------------------
        # 4. Page loop
        # ---------------------------------------------------------------------
        async for raw_works, page_cursor in iter_works(
            client,
            filter_params=works_filter,
            start_cursor=cursor,
            page_size=settings.crawl_page_size,
            updated_after=updated_after,
        ):
            page_start = datetime.now(timezone.utc)

            entities = _extract_page_entities(raw_works)
            page_count = len(entities["papers"])

            # Write to Postgres
            await upsert_papers(pg_session, entities["papers"])
            await upsert_authors(pg_session, entities["authors"])
            await upsert_institutions(pg_session, entities["institutions"])

            # Write to Neo4j
            await merge_papers(neo4j_driver, entities["papers"])
            await merge_authors(neo4j_driver, entities["authors"])
            await merge_institutions(neo4j_driver, entities["institutions"])
            await merge_topics(neo4j_driver, entities["topics"])
            await merge_wrote_rels(neo4j_driver, entities["authorships"])
            await merge_cites_rels(neo4j_driver, entities["citations"])
            await merge_affiliated_with_rels(neo4j_driver, entities["affiliations"])
            await merge_has_topic_rels(neo4j_driver, entities["paper_topics"])

            # Persist progress
            records_processed += page_count
            now = datetime.now(timezone.utc)
            await upsert_crawl_state(
                pg_session,
                job_id,
                cursor=page_cursor,
                last_crawled_at=now,
                records_processed=records_processed,
                last_error=None,
            )
            await pg_session.commit()

            duration_ms = int(
                (datetime.now(timezone.utc) - page_start).total_seconds() * 1000
            )
            logger.info(
                "Crawl page complete",
                extra={
                    "job_id": job_id,
                    "cursor": page_cursor,
                    "page_count": page_count,
                    "records_processed": records_processed,
                    "duration_ms": duration_ms,
                },
            )

            # Check for external stop signal
            if await _is_stop_requested(pg_session, job_id):
                logger.info(
                    "Stop signal received; halting crawl",
                    extra={"job_id": job_id},
                )
                await update_crawl_job_status(pg_session, job_id, "stopped")
                await pg_session.commit()
                return job_id

        # Crawl finished naturally
        await update_crawl_job_status(
            pg_session,
            job_id,
            "completed",
            completed_at=datetime.now(timezone.utc),
        )
        await pg_session.commit()
        logger.info(
            "Crawl completed",
            extra={"job_id": job_id, "total_records": records_processed},
        )

    except Exception as exc:
        logger.exception(
            "Crawl failed",
            extra={"job_id": job_id, "error": str(exc)},
        )
        try:
            await upsert_crawl_state(
                pg_session,
                job_id,
                cursor=cursor,
                records_processed=records_processed,
                last_error=str(exc),
            )
            await update_crawl_job_status(pg_session, job_id, "failed")
            await pg_session.commit()
        except Exception:
            logger.exception("Failed to persist error state", extra={"job_id": job_id})
        raise

    finally:
        if managed:
            await client.__aexit__(None, None, None)

    return job_id
