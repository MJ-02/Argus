""" Retry and crash recovery validation tests.

Tests verify:
1. Resume after mid-crawl crash picks up from the persisted cursor (no data loss,
   no duplicate records).
2. A Postgres write failure during a page is caught, error state is persisted,
   and the job transitions to 'failed'.
3. A Neo4j write failure is handled identically to a Postgres failure.

These tests spin up real Postgres and Neo4j containers via testcontainers,
consistent with the approach used in test_crawler.py.

Run with Docker available:
    uv run pytest tests/test_observability.py -v
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator, Generator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from neo4j import AsyncDriver, AsyncGraphDatabase
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.neo4j import Neo4jContainer
from testcontainers.postgres import PostgresContainer

from crawler.engine import SeedConfig, run_crawl
from db.models import AuthorMetadata, Base, CrawlJob, CrawlState, InstitutionMetadata, PaperMetadata
from db.neo4j_init import CONSTRAINTS_AND_INDEXES

FIXTURES_DIR = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.asyncio(loop_scope="module")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


async def _mock_iter_works_two_pages(*args: Any, **kwargs: Any) -> AsyncGenerator:
    page1 = load_fixture("works_page_1.json")
    page2 = load_fixture("works_page_2.json")
    yield page1["results"], page1["meta"]["next_cursor"]
    yield page2["results"], "*"


async def _mock_iter_works_page1_only(*args: Any, **kwargs: Any) -> AsyncGenerator:
    page1 = load_fixture("works_page_1.json")
    yield page1["results"], page1["meta"]["next_cursor"]


async def _mock_iter_works_page2_only(*args: Any, **kwargs: Any) -> AsyncGenerator:
    """Simulate a resume that only fetches the second page (cursor already past page 1)."""
    page2 = load_fixture("works_page_2.json")
    yield page2["results"], "*"


# ---------------------------------------------------------------------------
# Postgres fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_container() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def pg_async_url(pg_container: PostgresContainer) -> str:
    url = pg_container.get_connection_url()
    if "+psycopg2" in url:
        return url.replace("+psycopg2", "+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest_asyncio.fixture(scope="module")
async def pg_engine(pg_async_url: str):
    engine = create_async_engine(pg_async_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="module")
async def pg_session_factory(pg_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=pg_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def pg_session(pg_session_factory: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession, None]:
    async with pg_session_factory() as session:
        yield session
    async with pg_session_factory() as cleanup:
        await cleanup.execute(
            text("TRUNCATE crawl_jobs, crawl_state, papers_metadata, authors_metadata, institutions_metadata CASCADE")
        )
        await cleanup.commit()


# ---------------------------------------------------------------------------
# Neo4j fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def neo4j_container() -> Generator[Neo4jContainer, None, None]:
    with Neo4jContainer("neo4j:5-community") as neo4j:
        yield neo4j


@pytest_asyncio.fixture(scope="module")
async def neo4j_driver(neo4j_container: Neo4jContainer) -> AsyncGenerator[AsyncDriver, None]:
    driver = AsyncGraphDatabase.driver(
        neo4j_container.get_connection_url(),
        auth=(neo4j_container.username, neo4j_container.password),
    )
    async with driver.session() as session:
        for stmt in CONSTRAINTS_AND_INDEXES:
            await session.run(stmt)
    yield driver
    await driver.close()


@pytest_asyncio.fixture
async def clean_neo4j(neo4j_driver: AsyncDriver):
    yield
    async with neo4j_driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


# ---------------------------------------------------------------------------
# Test 1: Resume after crash picks up from persisted cursor
# ---------------------------------------------------------------------------


async def test_resume_after_crash_picks_up_from_cursor(
    pg_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """Crash mid-crawl, then resume — final entity counts equal a full 2-page crawl
    and no duplicate records are created.

    Simulated flow:
      Run 1: page 1 succeeds, then raises RuntimeError (crash after page 1).
             Cursor is persisted at page1's next_cursor.
      Run 2: resume from that cursor — only page 2 is fetched.
    """
    page1 = load_fixture("works_page_1.json")
    page1_cursor = page1["meta"]["next_cursor"]

    # ------------------------------------------------------------------
    # Run 1: crashes after processing the first page
    # ------------------------------------------------------------------
    crash_call_count = 0

    async def _mock_crash_after_page1(*args: Any, **kwargs: Any) -> AsyncGenerator:
        nonlocal crash_call_count
        crash_call_count += 1
        yield page1["results"], page1_cursor
        raise RuntimeError("Simulated worker crash after page 1")

    seed = SeedConfig()

    with patch("crawler.engine.iter_works", _mock_crash_after_page1):
        with pytest.raises(RuntimeError, match="Simulated worker crash after page 1"):
            await run_crawl(
                job_id=None,
                seed_config=seed,
                pg_session=pg_session,
                neo4j_driver=neo4j_driver,
            )

    assert crash_call_count == 1

    # Retrieve job_id from the DB — run_crawl re-raises, so we can't capture it
    # directly from the return value when it fails.

    # Verify crash state: job failed, cursor and records persisted
    job_id: str
    async with pg_session_factory() as verify_session:
        job_result = await verify_session.execute(select(CrawlJob))
        jobs = job_result.scalars().all()
        assert len(jobs) == 1
        job = jobs[0]
        job_id = job.id
        assert job.status == "failed"

        result = await verify_session.execute(
            select(CrawlState).where(CrawlState.job_id == job_id)
        )
        state = result.scalar_one()
        assert state.cursor == page1_cursor, "Cursor must be saved after the successful page"
        assert state.records_processed == 3, "Page 1 has 3 papers"
        assert state.last_error is not None

    # Reset status to "stopped" so engine allows resume
    async with pg_session_factory() as fix_session:
        job_row = await fix_session.get(CrawlJob, job_id)
        assert job_row is not None
        job_row.status = "stopped"
        await fix_session.commit()

    # ------------------------------------------------------------------
    # Run 2: resume — only page 2 should be processed
    # ------------------------------------------------------------------
    async with pg_session_factory() as resume_session:
        with patch("crawler.engine.iter_works", _mock_iter_works_page2_only):
            await run_crawl(
                job_id=job_id,
                seed_config=seed,
                pg_session=resume_session,
                neo4j_driver=neo4j_driver,
            )

    # Final entity counts must equal a complete 2-page run
    async with pg_session_factory() as count_session:
        paper_count = await count_session.scalar(select(func.count(PaperMetadata.openalex_id)))
        author_count = await count_session.scalar(select(func.count(AuthorMetadata.openalex_id)))
        inst_count = await count_session.scalar(select(func.count(InstitutionMetadata.openalex_id)))

    assert paper_count == 4, f"Expected 4 papers total, got {paper_count}"
    assert author_count == 4, f"Expected 4 authors total, got {author_count}"
    assert inst_count == 1, f"Expected 1 institution, got {inst_count}"

    # Confirm job is now completed
    async with pg_session_factory() as final_session:
        final_job = await final_session.get(CrawlJob, job_id)
        assert final_job is not None
        assert final_job.status == "completed"

        result = await final_session.execute(
            select(CrawlState).where(CrawlState.job_id == job_id)
        )
        final_state = result.scalar_one()
        assert final_state.records_processed == 4


# ---------------------------------------------------------------------------
# Test 2: Postgres write failure — job transitions to 'failed', error persisted
# ---------------------------------------------------------------------------


async def test_postgres_write_failure_sets_job_failed(
    pg_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """When upsert_papers raises, the job status transitions to 'failed' and
    last_error is populated in crawl_state.
    """
    seed = SeedConfig()

    with (
        patch("crawler.engine.iter_works", _mock_iter_works_page1_only),
        patch(
            "crawler.engine.upsert_papers",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Postgres connection refused"),
        ),
    ):
        with pytest.raises(RuntimeError, match="Postgres connection refused"):
            await run_crawl(
                job_id=None,
                seed_config=seed,
                pg_session=pg_session,
                neo4j_driver=neo4j_driver,
            )

    # Retrieve the job_id from crawl_jobs (only one row should exist)
    async with pg_session_factory() as verify_session:
        result = await verify_session.execute(select(CrawlJob))
        jobs = result.scalars().all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.status == "failed"

        state_result = await verify_session.execute(
            select(CrawlState).where(CrawlState.job_id == job.id)
        )
        state = state_result.scalar_one()
        assert state.last_error is not None
        assert "Postgres connection refused" in state.last_error


# ---------------------------------------------------------------------------
# Test 3: Neo4j write failure — job transitions to 'failed', error persisted
# ---------------------------------------------------------------------------


async def test_neo4j_write_failure_sets_job_failed(
    pg_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """When merge_papers (Neo4j) raises, the job transitions to 'failed' and
    last_error is captured in crawl_state.
    """
    seed = SeedConfig()

    with (
        patch("crawler.engine.iter_works", _mock_iter_works_page1_only),
        patch(
            "crawler.engine.merge_papers",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Neo4j service unavailable"),
        ),
    ):
        with pytest.raises(RuntimeError, match="Neo4j service unavailable"):
            await run_crawl(
                job_id=None,
                seed_config=seed,
                pg_session=pg_session,
                neo4j_driver=neo4j_driver,
            )

    async with pg_session_factory() as verify_session:
        result = await verify_session.execute(select(CrawlJob))
        jobs = result.scalars().all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.status == "failed"

        state_result = await verify_session.execute(
            select(CrawlState).where(CrawlState.job_id == job.id)
        )
        state = state_result.scalar_one()
        assert state.last_error is not None
        assert "Neo4j service unavailable" in state.last_error


# ---------------------------------------------------------------------------
# Test 4: Aggregate metrics are accumulated in crawl_state.metrics
# ---------------------------------------------------------------------------


async def test_crawl_state_metrics_accumulated(
    pg_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """After a 2-page crawl, crawl_state.metrics reflects aggregate counters."""
    seed = SeedConfig()

    with patch("crawler.engine.iter_works", _mock_iter_works_two_pages):
        job_id = await run_crawl(
            job_id=None,
            seed_config=seed,
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
        )

    async with pg_session_factory() as verify_session:
        result = await verify_session.execute(
            select(CrawlState).where(CrawlState.job_id == job_id)
        )
        state = result.scalar_one()

    assert state.metrics is not None, "metrics must be populated after a successful crawl"
    metrics = state.metrics

    assert metrics["pages_processed"] == 2
    assert metrics["records_written"] == 4
    assert metrics["records_fetched"] >= 4
    assert metrics["errors"] == 0
    assert metrics["total_duration_ms"] >= 0
    assert "pg_duration_ms" in metrics
    assert "neo4j_duration_ms" in metrics
