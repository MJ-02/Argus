"""Integration tests for Postgres and Neo4j writers.

Each test module-scoped fixture spins up a real database container via
testcontainers.  Tests verify upsert idempotency, crawl-state management, and
Neo4j relationship properties.

Run with Docker available:
    uv run pytest tests/test_writers.py -v
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase, AsyncDriver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.neo4j import Neo4jContainer
from testcontainers.postgres import PostgresContainer

from crawler.extractors import (
    Affiliation,
    Author,
    Authorship,
    Citation,
    Institution,
    Paper,
    PaperTopic,
    Topic,
)
from db.models import Base
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
    upsert_author,
    upsert_authors,
    upsert_crawl_state,
    upsert_institution,
    upsert_institutions,
    upsert_paper,
    upsert_papers,
)

# All test coroutines and async fixtures in this module share one event loop so
# that module-scoped fixtures (engine, Neo4j driver) and function-scoped tests
# operate on the same loop — avoiding "Future attached to a different loop".
pytestmark = pytest.mark.asyncio(loop_scope="module")

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
    # testcontainers may return postgresql+psycopg2://... or postgresql://...
    # Normalise to postgresql+asyncpg:// for SQLAlchemy async engine
    if "+psycopg2" in url:
        return url.replace("+psycopg2", "+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest_asyncio.fixture(scope="module")
async def pg_session_factory(pg_async_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(pg_async_url, echo=False)
    async with engine.begin() as conn:
        # create_all picks up UniqueConstraint from the model, including uq_crawl_state_job_id
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def session(pg_session_factory: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession, None]:
    async with pg_session_factory() as s:
        yield s
        # async_sessionmaker.__aexit__ rolls back any uncommitted state and closes


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
    # Apply constraints so MERGE can use indexes
    async with driver.session() as s:
        for stmt in [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Paper) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Author) REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (i:Institution) REQUIRE i.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Topic) REQUIRE t.id IS UNIQUE",
        ]:
            await s.run(stmt)
    yield driver
    await driver.close()


# ---------------------------------------------------------------------------
# Sample data helpers
# ---------------------------------------------------------------------------


def make_paper(idx: int = 1) -> Paper:
    return Paper(
        id=f"W{idx}",
        title=f"Paper {idx}",
        abstract=f"Abstract {idx}",
        publication_year=2020 + idx,
        doi=f"10.0000/test.{idx}",
        citation_count=idx * 10,
        source="openalex",
    )


def make_author(idx: int = 1) -> Author:
    return Author(
        id=f"A{idx}",
        name=f"Author {idx}",
        orcid=f"0000-0000-0000-{idx:04d}",
        works_count=idx * 5,
        citation_count=idx * 20,
    )


def make_institution(idx: int = 1) -> Institution:
    return Institution(
        id=f"I{idx}",
        name=f"University {idx}",
        country="US",
        institution_type="education",
    )


def make_topic(idx: int = 1) -> Topic:
    return Topic(id=f"T{idx}", name=f"Topic {idx}", domain="Science", field="CS")


# ---------------------------------------------------------------------------
# Postgres: paper upsert tests
# ---------------------------------------------------------------------------


class TestPostgresPaperUpsert:
    async def test_insert_paper(self, session: AsyncSession) -> None:
        paper = make_paper(101)
        await upsert_paper(session, paper)
        await session.commit()

        result = await session.execute(text("SELECT COUNT(*) FROM papers_metadata WHERE openalex_id = 'W101'"))
        assert result.scalar() == 1

    async def test_upsert_is_idempotent(self, session: AsyncSession) -> None:
        paper = make_paper(102)
        await upsert_paper(session, paper)
        await session.commit()
        # Insert again with updated citation count
        paper.citation_count = 999
        await upsert_paper(session, paper)
        await session.commit()

        result = await session.execute(text("SELECT citation_count FROM papers_metadata WHERE openalex_id = 'W102'"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == 999

        count = await session.execute(text("SELECT COUNT(*) FROM papers_metadata WHERE openalex_id = 'W102'"))
        assert count.scalar() == 1

    async def test_batch_upsert(self, session: AsyncSession) -> None:
        papers = [make_paper(200 + i) for i in range(5)]
        await upsert_papers(session, papers)
        await session.commit()

        result = await session.execute(
            text("SELECT COUNT(*) FROM papers_metadata WHERE openalex_id LIKE 'W20%'")
        )
        assert result.scalar() == 5

    async def test_empty_batch_is_noop(self, session: AsyncSession) -> None:
        # Should not raise
        await upsert_papers(session, [])

    async def test_paper_with_null_fields(self, session: AsyncSession) -> None:
        paper = Paper(id="W999", title=None, abstract=None, publication_year=None, doi=None, citation_count=0)
        await upsert_paper(session, paper)
        await session.commit()

        result = await session.execute(text("SELECT title, abstract FROM papers_metadata WHERE openalex_id = 'W999'"))
        row = result.fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] is None


# ---------------------------------------------------------------------------
# Postgres: author upsert tests
# ---------------------------------------------------------------------------


class TestPostgresAuthorUpsert:
    async def test_insert_author(self, session: AsyncSession) -> None:
        author = make_author(101)
        await upsert_author(session, author)
        await session.commit()

        result = await session.execute(text("SELECT name FROM authors_metadata WHERE openalex_id = 'A101'"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == "Author 101"

    async def test_upsert_author_is_idempotent(self, session: AsyncSession) -> None:
        author = make_author(102)
        await upsert_author(session, author)
        await session.commit()
        author.works_count = 9999
        await upsert_author(session, author)
        await session.commit()

        result = await session.execute(text("SELECT works_count FROM authors_metadata WHERE openalex_id = 'A102'"))
        assert result.scalar() == 9999

        count = await session.execute(text("SELECT COUNT(*) FROM authors_metadata WHERE openalex_id = 'A102'"))
        assert count.scalar() == 1

    async def test_batch_upsert_authors(self, session: AsyncSession) -> None:
        authors = [make_author(300 + i) for i in range(3)]
        await upsert_authors(session, authors)
        await session.commit()

        result = await session.execute(
            text("SELECT COUNT(*) FROM authors_metadata WHERE openalex_id LIKE 'A30%'")
        )
        assert result.scalar() == 3


# ---------------------------------------------------------------------------
# Postgres: institution upsert tests
# ---------------------------------------------------------------------------


class TestPostgresInstitutionUpsert:
    async def test_insert_institution(self, session: AsyncSession) -> None:
        inst = make_institution(101)
        await upsert_institution(session, inst)
        await session.commit()

        result = await session.execute(
            text("SELECT name FROM institutions_metadata WHERE openalex_id = 'I101'")
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "University 101"

    async def test_upsert_institution_is_idempotent(self, session: AsyncSession) -> None:
        inst = make_institution(102)
        await upsert_institution(session, inst)
        await session.commit()
        inst.country = "GB"
        await upsert_institution(session, inst)
        await session.commit()

        result = await session.execute(
            text("SELECT country FROM institutions_metadata WHERE openalex_id = 'I102'")
        )
        assert result.scalar() == "GB"

        count = await session.execute(
            text("SELECT COUNT(*) FROM institutions_metadata WHERE openalex_id = 'I102'")
        )
        assert count.scalar() == 1

    async def test_batch_upsert_institutions(self, session: AsyncSession) -> None:
        insts = [make_institution(400 + i) for i in range(4)]
        await upsert_institutions(session, insts)
        await session.commit()

        result = await session.execute(
            text("SELECT COUNT(*) FROM institutions_metadata WHERE openalex_id LIKE 'I40%'")
        )
        assert result.scalar() == 4


# ---------------------------------------------------------------------------
# Postgres: crawl job and state tests
# ---------------------------------------------------------------------------


class TestPostgresCrawlManagement:
    async def test_create_crawl_job(self, session: AsyncSession) -> None:
        seed = {"type": "topic", "topic_id": "T001"}
        job = await create_crawl_job(session, seed)
        await session.commit()

        result = await session.execute(
            text(f"SELECT status FROM crawl_jobs WHERE id = '{job.id}'")
        )
        assert result.scalar() == "pending"

    async def test_crawl_job_creates_state_row(self, session: AsyncSession) -> None:
        seed = {"type": "topic", "topic_id": "T002"}
        job = await create_crawl_job(session, seed)
        await session.commit()

        result = await session.execute(
            text(f"SELECT COUNT(*) FROM crawl_state WHERE job_id = '{job.id}'")
        )
        assert result.scalar() == 1

    async def test_update_crawl_job_status(self, session: AsyncSession) -> None:
        seed = {"type": "topic", "topic_id": "T003"}
        job = await create_crawl_job(session, seed)
        await session.commit()

        await update_crawl_job_status(session, job.id, "running")
        await session.commit()

        result = await session.execute(
            text(f"SELECT status FROM crawl_jobs WHERE id = '{job.id}'")
        )
        assert result.scalar() == "running"

    async def test_update_crawl_job_completed(self, session: AsyncSession) -> None:
        seed = {"type": "topic", "topic_id": "T004"}
        job = await create_crawl_job(session, seed)
        await session.commit()

        completed = datetime(2025, 1, 1, tzinfo=timezone.utc)
        await update_crawl_job_status(session, job.id, "completed", completed_at=completed)
        await session.commit()

        result = await session.execute(
            text(f"SELECT status, completed_at FROM crawl_jobs WHERE id = '{job.id}'")
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "completed"
        assert row[1] is not None

    async def test_upsert_crawl_state_idempotent(self, session: AsyncSession) -> None:
        seed = {"type": "topic", "topic_id": "T005"}
        job = await create_crawl_job(session, seed)
        await session.commit()

        await upsert_crawl_state(
            session, job.id, cursor="cursor_abc", records_processed=100
        )
        await session.commit()
        await upsert_crawl_state(
            session, job.id, cursor="cursor_xyz", records_processed=200
        )
        await session.commit()

        result = await session.execute(
            text(f"SELECT cursor, records_processed FROM crawl_state WHERE job_id = '{job.id}'")
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "cursor_xyz"
        assert row[1] == 200

        count = await session.execute(
            text(f"SELECT COUNT(*) FROM crawl_state WHERE job_id = '{job.id}'")
        )
        assert count.scalar() == 1

    async def test_update_crawl_job_unknown_id_raises(self, session: AsyncSession) -> None:
        with pytest.raises(ValueError, match="not found"):
            await update_crawl_job_status(session, "non-existent-id", "running")


# ---------------------------------------------------------------------------
# Neo4j: node merge tests
# ---------------------------------------------------------------------------


class TestNeo4jNodeMerges:
    async def test_merge_paper_creates_node(self, neo4j_driver: AsyncDriver) -> None:
        await merge_papers(neo4j_driver, [make_paper(1001)])

        async with neo4j_driver.session() as s:
            result = await s.run("MATCH (p:Paper {id: 'W1001'}) RETURN p.title AS title")
            record = await result.single()
        assert record is not None
        assert record["title"] == "Paper 1001"

    async def test_merge_paper_is_idempotent(self, neo4j_driver: AsyncDriver) -> None:
        paper = make_paper(1002)
        await merge_papers(neo4j_driver, [paper])
        paper.citation_count = 9999
        await merge_papers(neo4j_driver, [paper])

        async with neo4j_driver.session() as s:
            result = await s.run(
                "MATCH (p:Paper {id: 'W1002'}) RETURN count(p) AS cnt, p.citation_count AS cc"
            )
            record = await result.single()
        assert record is not None
        assert record["cnt"] == 1
        assert record["cc"] == 9999

    async def test_merge_author_creates_node(self, neo4j_driver: AsyncDriver) -> None:
        await merge_authors(neo4j_driver, [make_author(1001)])

        async with neo4j_driver.session() as s:
            result = await s.run("MATCH (a:Author {id: 'A1001'}) RETURN a.name AS name")
            record = await result.single()
        assert record is not None
        assert record["name"] == "Author 1001"

    async def test_merge_institution_creates_node(self, neo4j_driver: AsyncDriver) -> None:
        await merge_institutions(neo4j_driver, [make_institution(1001)])

        async with neo4j_driver.session() as s:
            result = await s.run("MATCH (i:Institution {id: 'I1001'}) RETURN i.name AS name")
            record = await result.single()
        assert record is not None
        assert record["name"] == "University 1001"

    async def test_merge_topic_creates_node(self, neo4j_driver: AsyncDriver) -> None:
        await merge_topics(neo4j_driver, [make_topic(1001)])

        async with neo4j_driver.session() as s:
            result = await s.run("MATCH (t:Topic {id: 'T1001'}) RETURN t.name AS name")
            record = await result.single()
        assert record is not None
        assert record["name"] == "Topic 1001"

    async def test_merge_empty_batch_is_noop(self, neo4j_driver: AsyncDriver) -> None:
        await merge_papers(neo4j_driver, [])
        await merge_authors(neo4j_driver, [])
        await merge_institutions(neo4j_driver, [])
        await merge_topics(neo4j_driver, [])


# ---------------------------------------------------------------------------
# Neo4j: relationship merge tests
# ---------------------------------------------------------------------------


class TestNeo4jRelationshipMerges:
    async def test_merge_wrote_rel(self, neo4j_driver: AsyncDriver) -> None:
        await merge_papers(neo4j_driver, [make_paper(2001)])
        await merge_authors(neo4j_driver, [make_author(2001)])
        await merge_wrote_rels(neo4j_driver, [Authorship(author_id="A2001", paper_id="W2001")])

        async with neo4j_driver.session() as s:
            result = await s.run(
                "MATCH (a:Author {id: 'A2001'})-[:WROTE]->(p:Paper {id: 'W2001'}) RETURN count(*) AS cnt"
            )
            record = await result.single()
        assert record is not None
        assert record["cnt"] == 1

    async def test_merge_wrote_rel_is_idempotent(self, neo4j_driver: AsyncDriver) -> None:
        await merge_papers(neo4j_driver, [make_paper(2002)])
        await merge_authors(neo4j_driver, [make_author(2002)])
        rel = Authorship(author_id="A2002", paper_id="W2002")
        await merge_wrote_rels(neo4j_driver, [rel])
        await merge_wrote_rels(neo4j_driver, [rel])

        async with neo4j_driver.session() as s:
            result = await s.run(
                "MATCH (:Author {id: 'A2002'})-[r:WROTE]->(:Paper {id: 'W2002'}) RETURN count(r) AS cnt"
            )
            record = await result.single()
        assert record is not None
        assert record["cnt"] == 1

    async def test_merge_cites_rel(self, neo4j_driver: AsyncDriver) -> None:
        await merge_papers(neo4j_driver, [make_paper(3001), make_paper(3002)])
        await merge_cites_rels(neo4j_driver, [Citation(source_paper_id="W3001", cited_paper_id="W3002")])

        async with neo4j_driver.session() as s:
            result = await s.run(
                "MATCH (src:Paper {id: 'W3001'})-[:CITES]->(dst:Paper {id: 'W3002'}) RETURN count(*) AS cnt"
            )
            record = await result.single()
        assert record is not None
        assert record["cnt"] == 1

    async def test_merge_affiliated_with_properties(self, neo4j_driver: AsyncDriver) -> None:
        """AFFILIATED_WITH relationship must carry start_year, end_year, primary."""
        await merge_authors(neo4j_driver, [make_author(4001)])
        await merge_institutions(neo4j_driver, [make_institution(4001)])
        aff = Affiliation(
            author_id="A4001",
            institution_id="I4001",
            start_year=2015,
            end_year=2020,
            primary=True,
        )
        await merge_affiliated_with_rels(neo4j_driver, [aff])

        async with neo4j_driver.session() as s:
            result = await s.run(
                "MATCH (:Author {id: 'A4001'})-[r:AFFILIATED_WITH]->(:Institution {id: 'I4001'}) "
                "RETURN r.start_year AS sy, r.end_year AS ey, r.primary AS pr"
            )
            record = await result.single()
        assert record is not None
        assert record["sy"] == 2015
        assert record["ey"] == 2020
        assert record["pr"] is True

    async def test_merge_affiliated_with_updates_properties(self, neo4j_driver: AsyncDriver) -> None:
        """Re-ingesting with new property values must update the relationship."""
        await merge_authors(neo4j_driver, [make_author(4002)])
        await merge_institutions(neo4j_driver, [make_institution(4002)])
        aff = Affiliation(author_id="A4002", institution_id="I4002", start_year=2010, end_year=None, primary=False)
        await merge_affiliated_with_rels(neo4j_driver, [aff])
        # Re-ingest with updated properties
        aff.primary = True
        aff.end_year = 2022
        await merge_affiliated_with_rels(neo4j_driver, [aff])

        async with neo4j_driver.session() as s:
            result = await s.run(
                "MATCH (:Author {id: 'A4002'})-[r:AFFILIATED_WITH]->(:Institution {id: 'I4002'}) "
                "RETURN count(r) AS cnt, r.primary AS pr, r.end_year AS ey"
            )
            record = await result.single()
        assert record is not None
        assert record["cnt"] == 1
        assert record["pr"] is True
        assert record["ey"] == 2022

    async def test_merge_has_topic_rel(self, neo4j_driver: AsyncDriver) -> None:
        await merge_papers(neo4j_driver, [make_paper(5001)])
        await merge_topics(neo4j_driver, [make_topic(5001)])
        await merge_has_topic_rels(neo4j_driver, [PaperTopic(paper_id="W5001", topic_id="T5001")])

        async with neo4j_driver.session() as s:
            result = await s.run(
                "MATCH (:Paper {id: 'W5001'})-[:HAS_TOPIC]->(:Topic {id: 'T5001'}) RETURN count(*) AS cnt"
            )
            record = await result.single()
        assert record is not None
        assert record["cnt"] == 1

    async def test_cites_rel_merges_stub_nodes(self, neo4j_driver: AsyncDriver) -> None:
        """Citing a paper not yet ingested should create a stub Paper node."""
        citation = Citation(source_paper_id="W6001", cited_paper_id="W6002_stub")
        await merge_cites_rels(neo4j_driver, [citation])

        async with neo4j_driver.session() as s:
            result = await s.run("MATCH (p:Paper {id: 'W6002_stub'}) RETURN count(p) AS cnt")
            record = await result.single()
        assert record is not None
        assert record["cnt"] == 1
