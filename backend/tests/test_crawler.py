"""Integration tests for the crawler engine (Phase 5.5).

Spins up real Postgres and Neo4j via testcontainers, runs the crawl engine
with a mocked iter_works generator, and asserts that both databases contain
the expected entity counts and relationships.

Run with Docker available:
    uv run pytest tests/test_crawler.py -v
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Generator
from unittest.mock import patch

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

# All tests in this module share one event loop (module-scoped fixtures +
# function-scoped tests must not cross loop boundaries).
pytestmark = pytest.mark.asyncio(loop_scope="module")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


async def _mock_iter_works_two_pages(*args: Any, **kwargs: Any) -> AsyncGenerator:
    """Yield the two fixture pages as if returned by OpenAlex."""
    page1 = load_fixture("works_page_1.json")
    page2 = load_fixture("works_page_2.json")
    yield page1["results"], "*"
    yield page2["results"], page1["meta"]["next_cursor"]


async def _mock_iter_works_one_page(*args: Any, **kwargs: Any) -> AsyncGenerator:
    """Yield only the first fixture page (simulates an incremental run)."""
    page1 = load_fixture("works_page_1.json")
    yield page1["results"], "*"


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
    # Truncate all tables between tests for isolation
    async with pg_session_factory() as cleanup:
        await cleanup.execute(text("TRUNCATE crawl_jobs, crawl_state, papers_metadata, authors_metadata, institutions_metadata CASCADE"))
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
    # Apply constraints
    async with driver.session() as session:
        for stmt in CONSTRAINTS_AND_INDEXES:
            await session.run(stmt)
    yield driver
    await driver.close()


@pytest_asyncio.fixture
async def clean_neo4j(neo4j_driver: AsyncDriver):
    """Wipe all nodes/rels before each test for isolation."""
    yield
    async with neo4j_driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


# ---------------------------------------------------------------------------
# Test: full crawl — entity counts and relationships
# ---------------------------------------------------------------------------


async def test_full_crawl_postgres_counts(
    pg_session: AsyncSession,
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """Full 2-page crawl populates Postgres with correct entity counts."""
    seed = SeedConfig()

    with patch("crawler.engine.iter_works", _mock_iter_works_two_pages):
        job_id = await run_crawl(
            job_id=None,
            seed_config=seed,
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
        )

    assert job_id is not None

    # papers: 3 from page 1 + 1 from page 2
    count = await pg_session.scalar(select(func.count(PaperMetadata.openalex_id)))
    assert count == 4

    # authors: Vaswani, Shazeer, Devlin (page 1) + Hochreiter (page 2)
    count = await pg_session.scalar(select(func.count(AuthorMetadata.openalex_id)))
    assert count == 4

    # institutions: Google Brain (deduplicated across pages)
    count = await pg_session.scalar(select(func.count(InstitutionMetadata.openalex_id)))
    assert count == 1


async def test_full_crawl_job_completed(
    pg_session: AsyncSession,
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """Crawl job status is set to 'completed' after a successful run."""
    with patch("crawler.engine.iter_works", _mock_iter_works_two_pages):
        job_id = await run_crawl(
            job_id=None,
            seed_config=SeedConfig(),
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
        )

    job = await pg_session.get(CrawlJob, job_id)
    assert job is not None
    assert job.status == "completed"
    assert job.completed_at is not None


async def test_full_crawl_state_persisted(
    pg_session: AsyncSession,
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """Crawl state records total records_processed and a non-null cursor."""
    with patch("crawler.engine.iter_works", _mock_iter_works_two_pages):
        job_id = await run_crawl(
            job_id=None,
            seed_config=SeedConfig(),
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
        )

    result = await pg_session.execute(
        select(CrawlState).where(CrawlState.job_id == job_id)
    )
    state = result.scalar_one()
    assert state.records_processed == 4
    assert state.cursor is not None
    assert state.last_crawled_at is not None
    assert state.last_error is None


async def test_full_crawl_neo4j_paper_nodes(
    pg_session: AsyncSession,
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """Neo4j contains the expected Paper nodes with properties set."""
    with patch("crawler.engine.iter_works", _mock_iter_works_two_pages):
        await run_crawl(
            job_id=None,
            seed_config=SeedConfig(),
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
        )

    async with neo4j_driver.session() as s:
        result = await s.run("MATCH (p:Paper) RETURN count(p) AS cnt")
        record = await result.single()
        # 4 real papers + W2011534025 created as a citation stub
        assert record["cnt"] >= 4


async def test_full_crawl_neo4j_relationships(
    pg_session: AsyncSession,
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """Neo4j contains WROTE, CITES, AFFILIATED_WITH, and HAS_TOPIC relationships."""
    with patch("crawler.engine.iter_works", _mock_iter_works_two_pages):
        await run_crawl(
            job_id=None,
            seed_config=SeedConfig(),
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
        )

    async with neo4j_driver.session() as s:
        # WROTE: Vaswani->W2741809807, Shazeer->W2741809807,
        #        Devlin->W2950377191, Hochreiter->W1982574323  (4 total)
        r = await s.run("MATCH ()-[:WROTE]->() RETURN count(*) AS cnt")
        rec = await r.single()
        assert rec["cnt"] == 4

        # CITES: W2741809807->W1982574323, W2741809807->W2011534025,
        #        W2950377191->W2741809807  (3 total)
        r = await s.run("MATCH ()-[:CITES]->() RETURN count(*) AS cnt")
        rec = await r.single()
        assert rec["cnt"] == 3

        # AFFILIATED_WITH: Vaswani->GoogleBrain, Shazeer->GoogleBrain,
        #                   Devlin->GoogleBrain  (3 unique)
        r = await s.run("MATCH ()-[:AFFILIATED_WITH]->() RETURN count(*) AS cnt")
        rec = await r.single()
        assert rec["cnt"] == 3

        # HAS_TOPIC: W2741809807->T10116, W2950377191->T10116,
        #            W2950377191->T11832, W1982574323->T11832  (4 total)
        r = await s.run("MATCH ()-[:HAS_TOPIC]->() RETURN count(*) AS cnt")
        rec = await r.single()
        assert rec["cnt"] == 4


# ---------------------------------------------------------------------------
# Test: idempotent re-run (upsert semantics)
# ---------------------------------------------------------------------------


async def test_idempotent_crawl(
    pg_session: AsyncSession,
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """Running the same crawl twice produces no duplicates in either database."""
    seed = SeedConfig()

    with patch("crawler.engine.iter_works", _mock_iter_works_two_pages):
        await run_crawl(
            job_id=None,
            seed_config=seed,
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
        )
        # Run again — a second independent job over the same data
        await run_crawl(
            job_id=None,
            seed_config=seed,
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
        )

    paper_count = await pg_session.scalar(select(func.count(PaperMetadata.openalex_id)))
    assert paper_count == 4

    author_count = await pg_session.scalar(select(func.count(AuthorMetadata.openalex_id)))
    assert author_count == 4

    async with neo4j_driver.session() as s:
        r = await s.run("MATCH (p:Paper) WHERE p.id IN ['W2741809807','W2950377191','W3098374985','W1982574323'] RETURN count(p) AS cnt")
        rec = await r.single()
        assert rec["cnt"] == 4


# ---------------------------------------------------------------------------
# Test: resume from persisted cursor
# ---------------------------------------------------------------------------


async def test_resume_from_cursor(
    pg_session: AsyncSession,
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """Resuming a job passes the persisted cursor to iter_works."""
    from db.postgres_writer import create_crawl_job, upsert_crawl_state

    # Create a job whose state already has page 1's cursor persisted
    job = await create_crawl_job(pg_session, SeedConfig().to_dict())
    await pg_session.commit()

    page1_cursor = load_fixture("works_page_1.json")["meta"]["next_cursor"]
    await upsert_crawl_state(
        pg_session,
        job.id,
        cursor=page1_cursor,
        records_processed=3,
    )
    await pg_session.commit()

    captured_start_cursors: list[str] = []

    async def mock_iter_works_capture(*args: Any, **kwargs: Any) -> AsyncGenerator:
        captured_start_cursors.append(kwargs.get("start_cursor", args[2] if len(args) > 2 else "*"))
        page2 = load_fixture("works_page_2.json")
        yield page2["results"], page1_cursor

    with patch("crawler.engine.iter_works", mock_iter_works_capture):
        await run_crawl(
            job_id=job.id,
            seed_config=SeedConfig(),
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
        )

    # iter_works must have been called with the persisted cursor, not "*"
    assert captured_start_cursors == [page1_cursor]

    # Final records_processed = existing 3 + 1 from page 2
    result = await pg_session.execute(
        select(CrawlState).where(CrawlState.job_id == job.id)
    )
    state = result.scalar_one()
    assert state.records_processed == 4


# ---------------------------------------------------------------------------
# Test: incremental mode passes updated_after to iter_works
# ---------------------------------------------------------------------------


async def test_incremental_mode_passes_updated_after(
    pg_session: AsyncSession,
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """Incremental mode derives updated_after from last_crawled_at and passes it."""
    from db.postgres_writer import create_crawl_job, upsert_crawl_state

    last_crawled = datetime(2024, 1, 10, 0, 0, 0, tzinfo=timezone.utc)

    job = await create_crawl_job(pg_session, SeedConfig().to_dict())
    await pg_session.commit()
    await upsert_crawl_state(
        pg_session,
        job.id,
        cursor="*",
        last_crawled_at=last_crawled,
        records_processed=0,
    )
    await pg_session.commit()

    captured_kwargs: list[dict] = []

    async def mock_iter_works_capture(*args: Any, **kwargs: Any) -> AsyncGenerator:
        captured_kwargs.append(kwargs)
        page1 = load_fixture("works_page_1.json")
        yield page1["results"], "*"

    with patch("crawler.engine.iter_works", mock_iter_works_capture):
        await run_crawl(
            job_id=job.id,
            seed_config=SeedConfig(),
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
            incremental=True,
        )

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["updated_after"] == "2024-01-10"


# ---------------------------------------------------------------------------
# Test: stop signal halts the crawl
# ---------------------------------------------------------------------------


async def test_stop_signal_halts_crawl(
    pg_session: AsyncSession,
    neo4j_driver: AsyncDriver,
    clean_neo4j: None,
) -> None:
    """When job status is set to 'stopping', the engine stops after the page."""
    from db.postgres_writer import create_crawl_job

    job = await create_crawl_job(pg_session, SeedConfig().to_dict())
    await pg_session.commit()

    pages_processed = 0

    async def mock_iter_works_two_pages(*args: Any, **kwargs: Any) -> AsyncGenerator:
        page1 = load_fixture("works_page_1.json")
        page2 = load_fixture("works_page_2.json")
        yield page1["results"], "*"
        yield page2["results"], page1["meta"]["next_cursor"]

    async def mock_is_stop_requested(session: AsyncSession, jid: str) -> bool:
        # Signal stop after the first page
        nonlocal pages_processed
        pages_processed += 1
        return pages_processed >= 1

    with (
        patch("crawler.engine.iter_works", mock_iter_works_two_pages),
        patch("crawler.engine._is_stop_requested", mock_is_stop_requested),
    ):
        await run_crawl(
            job_id=job.id,
            seed_config=SeedConfig(),
            pg_session=pg_session,
            neo4j_driver=neo4j_driver,
        )

    # Job should be stopped, not completed
    stopped_job = await pg_session.get(CrawlJob, job.id)
    assert stopped_job is not None
    assert stopped_job.status == "stopped"

    # Only the first page's papers should be in Postgres
    count = await pg_session.scalar(select(func.count(PaperMetadata.openalex_id)))
    assert count == 3
