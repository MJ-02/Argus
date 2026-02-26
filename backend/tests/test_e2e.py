"""end-to-end validation tests.

Covers:
    Citation graph traversal correctness at depths 1, 2, and 3
    Resumable crawl: crash then resume with no duplicate records
    API latency benchmark: p95 under 200 ms for search and citation queries

All tests run against real Postgres/Neo4j containers via testcontainers.

Run with Docker available:
    uv run pytest tests/test_e2e.py -v -s
"""
from __future__ import annotations

import statistics
import time
from typing import Any, AsyncGenerator, Generator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncDriver, AsyncGraphDatabase
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.neo4j import Neo4jContainer
from testcontainers.postgres import PostgresContainer

from crawler.engine import SeedConfig, run_crawl
from crawler.extractors import Citation, Paper
from db.models import Base, CrawlJob, CrawlState, PaperMetadata
from db.neo4j_init import CONSTRAINTS_AND_INDEXES
from db.neo4j_writer import merge_cites_rels, merge_papers
from db.postgres_writer import create_crawl_job, upsert_papers

pytestmark = pytest.mark.asyncio(loop_scope="module")

# ---------------------------------------------------------------------------
# Cursor constants for mock crawl pages
# ---------------------------------------------------------------------------

_CURSOR_PAGE1 = "p8_cursor_after_page1"
_CURSOR_PAGE2 = "p8_cursor_after_page2"
_CURSOR_PAGE3 = "p8_cursor_after_page3"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_work(
    bare_id: str,
    title: str = "Test Paper",
    year: int = 2020,
    cites: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal OpenAlex raw work dict compatible with all extractors."""
    return {
        "id": f"https://openalex.org/{bare_id}",
        "title": title,
        "abstract_inverted_index": None,
        "publication_year": year,
        "doi": None,
        "cited_by_count": 0,
        "updated_date": "2024-01-01",
        "authorships": [],
        "topics": [],
        "referenced_works": [
            f"https://openalex.org/{cid}" for cid in (cites or [])
        ],
    }


def _paper(bare_id: str, title: str = "Test Paper", year: int = 2020, cc: int = 0) -> Paper:
    return Paper(
        id=bare_id,
        title=title,
        abstract=None,
        publication_year=year,
        doi=None,
        citation_count=cc,
        source="openalex",
    )


# ---------------------------------------------------------------------------
# Module-scoped Postgres fixtures
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
async def pg_session(
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with pg_session_factory() as session:
        yield session
    # Truncate all tables between tests for isolation
    async with pg_session_factory() as cleanup:
        await cleanup.execute(
            text(
                "TRUNCATE crawl_jobs, crawl_state, papers_metadata, "
                "authors_metadata, institutions_metadata CASCADE"
            )
        )
        await cleanup.commit()


# ---------------------------------------------------------------------------
# Module-scoped Neo4j fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def neo4j_container() -> Generator[Neo4jContainer, None, None]:
    with Neo4jContainer("neo4j:5-community") as neo4j:
        yield neo4j


@pytest_asyncio.fixture(scope="module")
async def neo4j_driver(
    neo4j_container: Neo4jContainer,
) -> AsyncGenerator[AsyncDriver, None]:
    driver = AsyncGraphDatabase.driver(
        neo4j_container.get_connection_url(),
        auth=(neo4j_container.username, neo4j_container.password),
    )
    async with driver.session() as s:
        for stmt in CONSTRAINTS_AND_INDEXES:
            await s.run(stmt)
    yield driver
    await driver.close()


@pytest_asyncio.fixture
async def clean_neo4j(neo4j_driver: AsyncDriver) -> AsyncGenerator[None, None]:
    yield
    async with neo4j_driver.session() as s:
        await s.run("MATCH (n) DETACH DELETE n")


# ---------------------------------------------------------------------------
# ASGI client fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def api_client(
    pg_session_factory: async_sessionmaker[AsyncSession],
    neo4j_driver: AsyncDriver,
) -> AsyncGenerator[AsyncClient, None]:
    from api.main import app

    app.state.session_factory = pg_session_factory
    app.state.neo4j_driver = neo4j_driver

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Module-scoped latency seed fixture (8.4)
# Runs once for the entire module; cleans up at teardown.
# ---------------------------------------------------------------------------

_LATENCY_COUNT = 300
_LATENCY_PREFIX = "W_LAT8_"


@pytest_asyncio.fixture(scope="module")
async def latency_seed(
    pg_session_factory: async_sessionmaker[AsyncSession],
    neo4j_driver: AsyncDriver,
) -> AsyncGenerator[None, None]:
    """Seed 300 papers and a citation chain for the latency tests (once per module)."""
    papers = [
        _paper(
            f"{_LATENCY_PREFIX}{i:04d}",
            title=f"Latency paper {i} on deep learning and neural networks",
            year=2000 + (i % 24),
            cc=i,
        )
        for i in range(_LATENCY_COUNT)
    ]

    async with pg_session_factory() as session:
        await upsert_papers(session, papers)
        await session.commit()

    # Neo4j: first 30 papers as a linear citation chain for the citations endpoint
    await merge_papers(neo4j_driver, papers[:30])
    chain = [
        Citation(f"{_LATENCY_PREFIX}{i:04d}", f"{_LATENCY_PREFIX}{i + 1:04d}")
        for i in range(29)
    ]
    await merge_cites_rels(neo4j_driver, chain)

    yield

    # Cleanup Postgres rows
    async with pg_session_factory() as session:
        await session.execute(
            text(f"DELETE FROM papers_metadata WHERE openalex_id LIKE '{_LATENCY_PREFIX}%'")
        )
        await session.commit()
    # Cleanup Neo4j nodes
    async with neo4j_driver.session() as s:
        await s.run(
            "MATCH (p:Paper) WHERE p.id STARTS WITH $prefix DETACH DELETE p",
            prefix=_LATENCY_PREFIX,
        )


# ===========================================================================
# 8.2 — Citation graph traversal validation
# ===========================================================================


class TestCitationGraphTraversal:
    """Verify citation graph traversal returns correct nodes and edges at each depth.

    Graph topology:
        ROOT → D1A → D2 → D3
        ROOT → D1B

    Depth 1: {ROOT, D1A, D1B}  — edges: ROOT→D1A, ROOT→D1B
    Depth 2: {ROOT, D1A, D1B, D2}  — edges: + D1A→D2
    Depth 3: {ROOT, D1A, D1B, D2, D3}  — edges: + D2→D3
    """

    _ROOT = "W_P8_ROOT"
    _D1A = "W_P8_D1A"
    _D1B = "W_P8_D1B"
    _D2 = "W_P8_D2"
    _D3 = "W_P8_D3"
    _ALL = {"W_P8_ROOT", "W_P8_D1A", "W_P8_D1B", "W_P8_D2", "W_P8_D3"}

    async def _seed(self, pg_session: AsyncSession, neo4j_driver: AsyncDriver) -> None:
        """Seed the five-node graph into Postgres and Neo4j."""
        papers = [
            _paper(self._ROOT, "Root Paper", cc=100),
            _paper(self._D1A, "Depth 1 A", cc=50),
            _paper(self._D1B, "Depth 1 B", cc=30),
            _paper(self._D2, "Depth 2", cc=20),
            _paper(self._D3, "Depth 3", cc=10),
        ]
        await upsert_papers(pg_session, papers)
        await pg_session.commit()
        await merge_papers(neo4j_driver, papers)
        await merge_cites_rels(
            neo4j_driver,
            [
                Citation(self._ROOT, self._D1A),
                Citation(self._ROOT, self._D1B),
                Citation(self._D1A, self._D2),
                Citation(self._D2, self._D3),
            ],
        )

    async def test_depth_1_nodes(
        self,
        pg_session: AsyncSession,
        neo4j_driver: AsyncDriver,
        api_client: AsyncClient,
        clean_neo4j: None,
    ) -> None:
        await self._seed(pg_session, neo4j_driver)
        resp = await api_client.get(f"/papers/{self._ROOT}/citations?depth=1")
        assert resp.status_code == 200
        ids = {n["id"] for n in resp.json()["nodes"]}
        assert self._ROOT in ids
        assert self._D1A in ids
        assert self._D1B in ids
        assert self._D2 not in ids, "D2 must not appear at depth=1"
        assert self._D3 not in ids, "D3 must not appear at depth=1"

    async def test_depth_1_edges(
        self,
        pg_session: AsyncSession,
        neo4j_driver: AsyncDriver,
        api_client: AsyncClient,
        clean_neo4j: None,
    ) -> None:
        await self._seed(pg_session, neo4j_driver)
        resp = await api_client.get(f"/papers/{self._ROOT}/citations?depth=1")
        assert resp.status_code == 200
        edges = {(e["source"], e["target"]) for e in resp.json()["edges"]}
        assert (self._ROOT, self._D1A) in edges
        assert (self._ROOT, self._D1B) in edges
        assert (self._D1A, self._D2) not in edges, "D1A→D2 must not appear at depth=1"
        assert (self._D2, self._D3) not in edges, "D2→D3 must not appear at depth=1"

    async def test_depth_2_nodes(
        self,
        pg_session: AsyncSession,
        neo4j_driver: AsyncDriver,
        api_client: AsyncClient,
        clean_neo4j: None,
    ) -> None:
        await self._seed(pg_session, neo4j_driver)
        resp = await api_client.get(f"/papers/{self._ROOT}/citations?depth=2")
        assert resp.status_code == 200
        ids = {n["id"] for n in resp.json()["nodes"]}
        assert {self._ROOT, self._D1A, self._D1B, self._D2} <= ids
        assert self._D3 not in ids, "D3 must not appear at depth=2"

    async def test_depth_2_edges(
        self,
        pg_session: AsyncSession,
        neo4j_driver: AsyncDriver,
        api_client: AsyncClient,
        clean_neo4j: None,
    ) -> None:
        await self._seed(pg_session, neo4j_driver)
        resp = await api_client.get(f"/papers/{self._ROOT}/citations?depth=2")
        assert resp.status_code == 200
        edges = {(e["source"], e["target"]) for e in resp.json()["edges"]}
        assert (self._ROOT, self._D1A) in edges
        assert (self._ROOT, self._D1B) in edges
        assert (self._D1A, self._D2) in edges
        assert (self._D2, self._D3) not in edges, "D2→D3 must not appear at depth=2"

    async def test_depth_3_full_graph(
        self,
        pg_session: AsyncSession,
        neo4j_driver: AsyncDriver,
        api_client: AsyncClient,
        clean_neo4j: None,
    ) -> None:
        await self._seed(pg_session, neo4j_driver)
        resp = await api_client.get(f"/papers/{self._ROOT}/citations?depth=3")
        assert resp.status_code == 200
        body = resp.json()
        ids = {n["id"] for n in body["nodes"]}
        assert ids == self._ALL

        edges = {(e["source"], e["target"]) for e in body["edges"]}
        assert (self._ROOT, self._D1A) in edges
        assert (self._ROOT, self._D1B) in edges
        assert (self._D1A, self._D2) in edges
        assert (self._D2, self._D3) in edges

    async def test_edges_contained_within_subgraph(
        self,
        pg_session: AsyncSession,
        neo4j_driver: AsyncDriver,
        api_client: AsyncClient,
        clean_neo4j: None,
    ) -> None:
        """Every edge endpoint must be present in the returned node set at every depth."""
        await self._seed(pg_session, neo4j_driver)
        for depth in (1, 2, 3):
            resp = await api_client.get(f"/papers/{self._ROOT}/citations?depth={depth}")
            assert resp.status_code == 200
            body = resp.json()
            node_ids = {n["id"] for n in body["nodes"]}
            for edge in body["edges"]:
                assert edge["source"] in node_ids, (
                    f"depth={depth}: edge source {edge['source']!r} not in nodes"
                )
                assert edge["target"] in node_ids, (
                    f"depth={depth}: edge target {edge['target']!r} not in nodes"
                )


# ===========================================================================
# 8.3 — Resumable crawl validation
# ===========================================================================


class TestResumableCrawl:
    """Verify that a crashed crawl resumes from the correct cursor with no duplicates.

    Test data uses IDs prefixed W_RES_ to avoid collisions with other test classes.
    """

    _PAGE1_IDS = ["W_RES_101", "W_RES_102", "W_RES_103"]
    _PAGE2_IDS = ["W_RES_201", "W_RES_202", "W_RES_203"]
    _PAGE3_IDS = ["W_RES_301", "W_RES_302", "W_RES_303"]

    @staticmethod
    def _make_page(ids: list[str]) -> list[dict[str, Any]]:
        return [_raw_work(pid, title=f"Paper {pid}") for pid in ids]

    async def test_crash_after_two_pages_persists_cursor(
        self,
        pg_session: AsyncSession,
        neo4j_driver: AsyncDriver,
        clean_neo4j: None,
    ) -> None:
        """After a crash mid-crawl, the cursor saved in crawl_state equals the last
        successfully committed page cursor."""
        page1 = self._make_page(self._PAGE1_IDS)
        page2 = self._make_page(self._PAGE2_IDS)

        async def _mock_crash_after_p2(*args: Any, **kwargs: Any) -> AsyncGenerator:
            yield page1, _CURSOR_PAGE1
            yield page2, _CURSOR_PAGE2
            raise RuntimeError("Simulated mid-crawl worker crash")

        job = await create_crawl_job(pg_session, SeedConfig().to_dict())
        await pg_session.commit()
        job_id = job.id

        with patch("crawler.engine.iter_works", _mock_crash_after_p2):
            try:
                await run_crawl(
                    job_id=job_id,
                    seed_config=SeedConfig(),
                    pg_session=pg_session,
                    neo4j_driver=neo4j_driver,
                )
            except RuntimeError:
                pass

        state_result = await pg_session.execute(
            select(CrawlState).where(CrawlState.job_id == job_id)
        )
        state = state_result.scalar_one()
        assert state.cursor == _CURSOR_PAGE2, (
            f"Expected cursor={_CURSOR_PAGE2!r}, got {state.cursor!r}"
        )
        assert state.records_processed == 6
        assert state.last_error is not None

        job_row = await pg_session.get(CrawlJob, job_id)
        assert job_row is not None
        assert job_row.status == "failed"

    async def test_resume_from_failed_job_completes_correctly(
        self,
        pg_session: AsyncSession,
        neo4j_driver: AsyncDriver,
        clean_neo4j: None,
    ) -> None:
        """Resuming a failed job processes the remaining pages and reaches 'completed'."""
        page1 = self._make_page(self._PAGE1_IDS)
        page2 = self._make_page(self._PAGE2_IDS)
        page3 = self._make_page(self._PAGE3_IDS)

        async def _mock_crash_after_p2(*args: Any, **kwargs: Any) -> AsyncGenerator:
            yield page1, _CURSOR_PAGE1
            yield page2, _CURSOR_PAGE2
            raise RuntimeError("Simulated crash")

        job = await create_crawl_job(pg_session, SeedConfig().to_dict())
        await pg_session.commit()
        job_id = job.id

        with patch("crawler.engine.iter_works", _mock_crash_after_p2):
            try:
                await run_crawl(
                    job_id=job_id,
                    seed_config=SeedConfig(),
                    pg_session=pg_session,
                    neo4j_driver=neo4j_driver,
                )
            except RuntimeError:
                pass

        captured_start_cursors: list[str] = []

        async def _mock_resume_p3(*args: Any, **kwargs: Any) -> AsyncGenerator:
            captured_start_cursors.append(kwargs.get("start_cursor", "*"))
            yield page3, _CURSOR_PAGE3

        with patch("crawler.engine.iter_works", _mock_resume_p3):
            await run_crawl(
                job_id=job_id,
                seed_config=SeedConfig(),
                pg_session=pg_session,
                neo4j_driver=neo4j_driver,
            )

        assert captured_start_cursors == [_CURSOR_PAGE2], (
            "Resume must start from the cursor persisted after the last successful page"
        )

        job_row = await pg_session.get(CrawlJob, job_id)
        assert job_row is not None
        assert job_row.status == "completed"

        state_result = await pg_session.execute(
            select(CrawlState).where(CrawlState.job_id == job_id)
        )
        state = state_result.scalar_one()
        assert state.records_processed == 9

    async def test_no_duplicate_records_after_resume(
        self,
        pg_session: AsyncSession,
        neo4j_driver: AsyncDriver,
        clean_neo4j: None,
    ) -> None:
        """After crash-and-resume, Postgres and Neo4j contain exactly 9 distinct papers."""
        page1 = self._make_page(self._PAGE1_IDS)
        page2 = self._make_page(self._PAGE2_IDS)
        page3 = self._make_page(self._PAGE3_IDS)

        async def _mock_crash(*args: Any, **kwargs: Any) -> AsyncGenerator:
            yield page1, _CURSOR_PAGE1
            yield page2, _CURSOR_PAGE2
            raise RuntimeError("Crash")

        async def _mock_resume(*args: Any, **kwargs: Any) -> AsyncGenerator:
            yield page3, _CURSOR_PAGE3

        job = await create_crawl_job(pg_session, SeedConfig().to_dict())
        await pg_session.commit()
        job_id = job.id

        with patch("crawler.engine.iter_works", _mock_crash):
            try:
                await run_crawl(
                    job_id=job_id,
                    seed_config=SeedConfig(),
                    pg_session=pg_session,
                    neo4j_driver=neo4j_driver,
                )
            except RuntimeError:
                pass

        with patch("crawler.engine.iter_works", _mock_resume):
            await run_crawl(
                job_id=job_id,
                seed_config=SeedConfig(),
                pg_session=pg_session,
                neo4j_driver=neo4j_driver,
            )

        pg_count = await pg_session.scalar(select(func.count(PaperMetadata.openalex_id)))
        assert pg_count == 9, f"Expected 9 papers, got {pg_count}"

        all_ids = self._PAGE1_IDS + self._PAGE2_IDS + self._PAGE3_IDS
        async with neo4j_driver.session() as s:
            result = await s.run(
                "MATCH (p:Paper) WHERE p.id IN $ids RETURN count(p) AS cnt",
                ids=all_ids,
            )
            rec = await result.single()
            assert rec["cnt"] == 9, f"Expected 9 Neo4j Paper nodes, got {rec['cnt']}"

    async def test_upsert_idempotency_on_retry(
        self,
        pg_session: AsyncSession,
        neo4j_driver: AsyncDriver,
        clean_neo4j: None,
    ) -> None:
        """Replaying the same page twice produces no duplicate records."""
        page1 = self._make_page(self._PAGE1_IDS)

        async def _mock_one_page(*args: Any, **kwargs: Any) -> AsyncGenerator:
            yield page1, _CURSOR_PAGE1

        with patch("crawler.engine.iter_works", _mock_one_page):
            await run_crawl(
                job_id=None,
                seed_config=SeedConfig(),
                pg_session=pg_session,
                neo4j_driver=neo4j_driver,
            )

        with patch("crawler.engine.iter_works", _mock_one_page):
            await run_crawl(
                job_id=None,
                seed_config=SeedConfig(),
                pg_session=pg_session,
                neo4j_driver=neo4j_driver,
            )

        pg_count = await pg_session.scalar(select(func.count(PaperMetadata.openalex_id)))
        assert pg_count == 3, f"Expected 3 papers (no duplicates), got {pg_count}"

        async with neo4j_driver.session() as s:
            result = await s.run(
                "MATCH (p:Paper) WHERE p.id IN $ids RETURN count(p) AS cnt",
                ids=self._PAGE1_IDS,
            )
            rec = await result.single()
            assert rec["cnt"] == 3


# ===========================================================================
# 8.4 — API latency benchmark
# ===========================================================================

_P95_THRESHOLD_MS = 200.0
_BENCHMARK_REQUESTS = 30


class TestAPILatency:
    """Benchmark p95 response latency for /papers/search and /papers/{id}/citations.

    Asserts p95 < 200 ms against the in-process ASGI app backed by real
    Postgres/Neo4j testcontainers.  This validates query-layer performance
    without network overhead — the same queries run against a Docker Compose
    stack will be equally fast.
    """

    @staticmethod
    def _p95(samples: list[float]) -> float:
        sorted_samples = sorted(samples)
        idx = min(int(0.95 * len(sorted_samples)), len(sorted_samples) - 1)
        return sorted_samples[idx]

    async def test_search_p95_under_200ms(
        self,
        api_client: AsyncClient,
        latency_seed: None,
    ) -> None:
        """GET /papers/search p95 latency must be under 200 ms."""
        latencies: list[float] = []
        for _ in range(_BENCHMARK_REQUESTS):
            start = time.perf_counter()
            resp = await api_client.get("/papers/search?q=neural+networks&limit=20")
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert resp.status_code == 200
            latencies.append(elapsed_ms)

        p95 = self._p95(latencies)
        avg = statistics.mean(latencies)
        print(
            f"\n  /papers/search   p95={p95:.1f}ms  avg={avg:.1f}ms  "
            f"n={_BENCHMARK_REQUESTS}"
        )
        assert p95 < _P95_THRESHOLD_MS, (
            f"/papers/search p95={p95:.1f}ms exceeds {_P95_THRESHOLD_MS}ms threshold"
        )

    async def test_search_paginated_p95_under_200ms(
        self,
        api_client: AsyncClient,
        latency_seed: None,
    ) -> None:
        """GET /papers/search with varying offsets p95 latency must be under 200 ms."""
        latencies: list[float] = []
        for i in range(_BENCHMARK_REQUESTS):
            offset = (i * 10) % _LATENCY_COUNT
            start = time.perf_counter()
            resp = await api_client.get(f"/papers/search?limit=10&offset={offset}")
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert resp.status_code == 200
            latencies.append(elapsed_ms)

        p95 = self._p95(latencies)
        avg = statistics.mean(latencies)
        print(
            f"\n  /papers/search (paginated)   p95={p95:.1f}ms  avg={avg:.1f}ms  "
            f"n={_BENCHMARK_REQUESTS}"
        )
        assert p95 < _P95_THRESHOLD_MS, (
            f"/papers/search (paginated) p95={p95:.1f}ms exceeds {_P95_THRESHOLD_MS}ms threshold"
        )

    async def test_citations_depth2_p95_under_200ms(
        self,
        api_client: AsyncClient,
        latency_seed: None,
    ) -> None:
        """GET /papers/{id}/citations?depth=2 p95 latency must be under 200 ms."""
        root_id = f"{_LATENCY_PREFIX}0000"
        latencies: list[float] = []
        for _ in range(_BENCHMARK_REQUESTS):
            start = time.perf_counter()
            resp = await api_client.get(f"/papers/{root_id}/citations?depth=2")
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert resp.status_code == 200
            latencies.append(elapsed_ms)

        p95 = self._p95(latencies)
        avg = statistics.mean(latencies)
        print(
            f"\n  /papers/{{id}}/citations?depth=2   p95={p95:.1f}ms  avg={avg:.1f}ms  "
            f"n={_BENCHMARK_REQUESTS}"
        )
        assert p95 < _P95_THRESHOLD_MS, (
            f"/papers/{{id}}/citations depth=2 p95={p95:.1f}ms exceeds {_P95_THRESHOLD_MS}ms threshold"
        )

    async def test_get_paper_p95_under_200ms(
        self,
        api_client: AsyncClient,
        latency_seed: None,
    ) -> None:
        """GET /papers/{id} p95 latency must be under 200 ms."""
        latencies: list[float] = []
        for i in range(_BENCHMARK_REQUESTS):
            paper_id = f"{_LATENCY_PREFIX}{(i * 7) % _LATENCY_COUNT:04d}"
            start = time.perf_counter()
            resp = await api_client.get(f"/papers/{paper_id}")
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert resp.status_code == 200
            latencies.append(elapsed_ms)

        p95 = self._p95(latencies)
        avg = statistics.mean(latencies)
        print(
            f"\n  /papers/{{id}}   p95={p95:.1f}ms  avg={avg:.1f}ms  "
            f"n={_BENCHMARK_REQUESTS}"
        )
        assert p95 < _P95_THRESHOLD_MS, (
            f"GET /papers/{{id}} p95={p95:.1f}ms exceeds {_P95_THRESHOLD_MS}ms threshold"
        )
