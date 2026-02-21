"""Integration tests for the FastAPI API layer.

Spins up real Postgres and Neo4j containers via testcontainers, seeds test
data directly into the databases, and exercises every endpoint using an
httpx AsyncClient backed by ASGI transport.

Run with Docker available:
    uv run pytest tests/test_api.py -v
"""
from __future__ import annotations

import uuid
from typing import AsyncGenerator, Generator
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncGraphDatabase, AsyncDriver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.neo4j import Neo4jContainer
from testcontainers.postgres import PostgresContainer

from crawler.extractors import Author, Authorship, Citation, Paper, PaperTopic, Topic
from db.models import Base
from db.neo4j_writer import (
    merge_authors,
    merge_cites_rels,
    merge_has_topic_rels,
    merge_papers,
    merge_topics,
    merge_wrote_rels,
)
from db.postgres_writer import (
    create_crawl_job,
    update_crawl_job_status,
    upsert_authors,
    upsert_papers,
)

pytestmark = pytest.mark.asyncio(loop_scope="module")

# ---------------------------------------------------------------------------
# Container fixtures
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


@pytest.fixture(scope="module")
def neo4j_container() -> Generator[Neo4jContainer, None, None]:
    with Neo4jContainer("neo4j:5-community") as neo4j:
        yield neo4j


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def pg_session_factory(
    pg_async_url: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(pg_async_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture(scope="module")
async def neo4j_driver(
    neo4j_container: Neo4jContainer,
) -> AsyncGenerator[AsyncDriver, None]:
    driver = AsyncGraphDatabase.driver(
        neo4j_container.get_connection_url(),
        auth=(neo4j_container.username, neo4j_container.password),
    )
    async with driver.session() as s:
        for stmt in [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Paper) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Author) REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Topic) REQUIRE t.id IS UNIQUE",
        ]:
            await s.run(stmt)
    yield driver
    await driver.close()


# ---------------------------------------------------------------------------
# ASGI client fixture — patches app.state with test DB connections
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def client(
    pg_session_factory: async_sessionmaker[AsyncSession],
    neo4j_driver: AsyncDriver,
) -> AsyncGenerator[AsyncClient, None]:
    """Return an httpx AsyncClient wired to the FastAPI app with test databases.

    ``ASGITransport`` does not dispatch ASGI lifespan events, so we skip the
    app's lifespan entirely and populate ``app.state`` directly with the
    module-scoped test connections.
    """
    from api.main import app

    app.state.session_factory = pg_session_factory
    app.state.neo4j_driver = neo4j_driver

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _paper(idx: int = 1) -> Paper:
    return Paper(
        id=f"W{idx}",
        title=f"Paper {idx} title",
        abstract=f"Abstract for paper {idx}",
        publication_year=2020 + idx,
        doi=f"10.0000/test.{idx}",
        citation_count=idx * 5,
        source="openalex",
    )


def _author(idx: int = 1) -> Author:
    return Author(
        id=f"A{idx}",
        name=f"Author {idx}",
        orcid=f"0000-0000-0000-{idx:04d}",
        works_count=idx * 3,
        citation_count=idx * 10,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    async def test_health(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------


class TestGetPaper:
    async def test_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/papers/DOES_NOT_EXIST")
        assert resp.status_code == 404

    async def test_found(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            await upsert_papers(session, [_paper(200)])
            await session.commit()

        resp = await client.get("/papers/W200")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "W200"
        assert data["title"] == "Paper 200 title"
        assert data["citation_count"] == 1000


class TestSearchPapers:
    async def test_returns_results(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            await upsert_papers(session, [_paper(301), _paper(302)])
            await session.commit()

        resp = await client.get("/papers/search")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 2
        assert isinstance(body["items"], list)

    async def test_q_filter(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            unique = Paper(
                id="W_UNIQUE_SEARCH",
                title="Unique Transformer Attention Title",
                abstract=None,
                publication_year=2023,
                doi=None,
                citation_count=0,
                source="openalex",
            )
            await upsert_papers(session, [unique])
            await session.commit()

        resp = await client.get("/papers/search?q=Unique+Transformer+Attention")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        ids = [item["id"] for item in body["items"]]
        assert "W_UNIQUE_SEARCH" in ids

    async def test_year_range_filter(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            papers = [
                Paper(
                    id=f"W_YR_{y}",
                    title=f"Year filter paper {y}",
                    abstract=None,
                    publication_year=y,
                    doi=None,
                    citation_count=0,
                    source="openalex",
                )
                for y in (2000, 2010, 2020)
            ]
            await upsert_papers(session, papers)
            await session.commit()

        resp = await client.get("/papers/search?year_from=2010&year_to=2010")
        assert resp.status_code == 200
        body = resp.json()
        ids = [item["id"] for item in body["items"]]
        assert "W_YR_2010" in ids
        assert "W_YR_2000" not in ids
        assert "W_YR_2020" not in ids

    async def test_pagination(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            await upsert_papers(session, [_paper(401), _paper(402), _paper(403)])
            await session.commit()

        resp = await client.get("/papers/search?limit=2&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) <= 2
        assert body["limit"] == 2
        assert body["offset"] == 0

    async def test_topic_filter_no_results(self, client: AsyncClient) -> None:
        resp = await client.get("/papers/search?topic=T_NONEXISTENT")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestCitationGraph:
    async def test_paper_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/papers/MISSING_PAPER/citations")
        assert resp.status_code == 404

    async def test_depth_default(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
        neo4j_driver: AsyncDriver,
    ) -> None:
        root = _paper(501)
        cited1 = _paper(502)
        cited2 = _paper(503)

        async with pg_session_factory() as session:
            await upsert_papers(session, [root, cited1, cited2])
            await session.commit()

        await merge_papers(neo4j_driver, [root, cited1, cited2])
        await merge_cites_rels(
            neo4j_driver,
            [
                Citation(source_paper_id="W501", cited_paper_id="W502"),
                Citation(source_paper_id="W501", cited_paper_id="W503"),
            ],
        )

        resp = await client.get("/papers/W501/citations")
        assert resp.status_code == 200
        body = resp.json()
        node_ids = {n["id"] for n in body["nodes"]}
        assert "W501" in node_ids
        assert "W502" in node_ids
        assert "W503" in node_ids
        assert any(e["source"] == "W501" for e in body["edges"])

    async def test_depth_clamped(self, client: AsyncClient) -> None:
        # depth > 3 should be rejected
        resp = await client.get("/papers/W501/citations?depth=4")
        assert resp.status_code == 422

    async def test_depth_zero_rejected(self, client: AsyncClient) -> None:
        resp = await client.get("/papers/W501/citations?depth=0")
        assert resp.status_code == 422

    async def test_depth_three(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
        neo4j_driver: AsyncDriver,
    ) -> None:
        # Build chain: W601 -> W602 -> W603 -> W604
        papers = [_paper(i) for i in (601, 602, 603, 604)]
        async with pg_session_factory() as session:
            await upsert_papers(session, papers)
            await session.commit()

        await merge_papers(neo4j_driver, papers)
        await merge_cites_rels(
            neo4j_driver,
            [
                Citation("W601", "W602"),
                Citation("W602", "W603"),
                Citation("W603", "W604"),
            ],
        )

        resp = await client.get("/papers/W601/citations?depth=3")
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert {"W601", "W602", "W603", "W604"} == node_ids


# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------


class TestGetAuthor:
    async def test_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/authors/DOES_NOT_EXIST")
        assert resp.status_code == 404

    async def test_found(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            await upsert_authors(session, [_author(700)])
            await session.commit()

        resp = await client.get("/authors/A700")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "A700"
        assert data["name"] == "Author 700"


class TestGetAuthorPapers:
    async def test_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/authors/A_MISSING/papers")
        assert resp.status_code == 404

    async def test_returns_papers(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
        neo4j_driver: AsyncDriver,
    ) -> None:
        author = _author(800)
        papers = [_paper(801), _paper(802)]

        async with pg_session_factory() as session:
            await upsert_authors(session, [author])
            await upsert_papers(session, papers)
            await session.commit()

        await merge_authors(neo4j_driver, [author])
        await merge_papers(neo4j_driver, papers)
        await merge_wrote_rels(
            neo4j_driver,
            [
                Authorship(author_id="A800", paper_id="W801"),
                Authorship(author_id="A800", paper_id="W802"),
            ],
        )

        resp = await client.get("/authors/A800/papers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        ids = {item["id"] for item in body["items"]}
        assert ids == {"W801", "W802"}

    async def test_pagination(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
        neo4j_driver: AsyncDriver,
    ) -> None:
        author = _author(900)
        papers = [_paper(901), _paper(902), _paper(903)]

        async with pg_session_factory() as session:
            await upsert_authors(session, [author])
            await upsert_papers(session, papers)
            await session.commit()

        await merge_authors(neo4j_driver, [author])
        await merge_papers(neo4j_driver, papers)
        await merge_wrote_rels(
            neo4j_driver,
            [Authorship(author_id="A900", paper_id=f"W{i}") for i in (901, 902, 903)],
        )

        resp = await client.get("/authors/A900/papers?limit=2&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2


# ---------------------------------------------------------------------------
# Crawl management
# ---------------------------------------------------------------------------


class TestCrawlManagement:
    async def test_start_crawl(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        with patch("workers.celery_app.app.send_task", return_value=MagicMock()):
            resp = await client.post(
                "/crawls",
                json={"topic_id": "T1", "date_from": "2020-01-01"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["seed_config"]["topic_id"] == "T1"
        assert body["records_processed"] == 0

    async def test_get_crawl(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            job = await create_crawl_job(session, {"topic_id": "T_GET"})
            await session.commit()
            job_id = job.id

        resp = await client.get(f"/crawls/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == job_id

    async def test_get_crawl_not_found(self, client: AsyncClient) -> None:
        resp = await client.get(f"/crawls/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_stop_crawl(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            job = await create_crawl_job(session, {})
            await update_crawl_job_status(session, job.id, "running")
            await session.commit()
            job_id = job.id

        resp = await client.post(f"/crawls/{job_id}/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopping"

    async def test_stop_crawl_wrong_status(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            job = await create_crawl_job(session, {})
            await update_crawl_job_status(session, job.id, "completed")
            await session.commit()
            job_id = job.id

        resp = await client.post(f"/crawls/{job_id}/stop")
        assert resp.status_code == 409

    async def test_resume_crawl(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            job = await create_crawl_job(session, {"topic_id": "T_RESUME"})
            await update_crawl_job_status(session, job.id, "stopped")
            await session.commit()
            job_id = job.id

        with patch("workers.celery_app.app.send_task", return_value=MagicMock()):
            resp = await client.post(f"/crawls/{job_id}/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    async def test_resume_crawl_wrong_status(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with pg_session_factory() as session:
            job = await create_crawl_job(session, {})
            await update_crawl_job_status(session, job.id, "running")
            await session.commit()
            job_id = job.id

        resp = await client.post(f"/crawls/{job_id}/resume")
        assert resp.status_code == 409

    async def test_state_transition_start_stop_resume(
        self,
        client: AsyncClient,
        pg_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Full lifecycle: start -> stop -> resume."""
        with patch("workers.celery_app.app.send_task", return_value=MagicMock()):
            start_resp = await client.post("/crawls", json={"topic_id": "T_LIFECYCLE"})
        assert start_resp.status_code == 201
        job_id = start_resp.json()["id"]

        # Simulate worker setting status to running
        async with pg_session_factory() as session:
            await update_crawl_job_status(session, job_id, "running")
            await session.commit()

        stop_resp = await client.post(f"/crawls/{job_id}/stop")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "stopping"

        # Simulate worker acknowledging stop
        async with pg_session_factory() as session:
            await update_crawl_job_status(session, job_id, "stopped")
            await session.commit()

        with patch("workers.celery_app.app.send_task", return_value=MagicMock()):
            resume_resp = await client.post(f"/crawls/{job_id}/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "pending"
