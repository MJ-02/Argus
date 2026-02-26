"""FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from neo4j import AsyncGraphDatabase
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.metrics import ACTIVE_CRAWL_JOBS, API_REQUEST_ERRORS_TOTAL
from api.routes.authors import router as authors_router
from api.routes.crawls import router as crawls_router
from api.routes.papers import router as papers_router
from db.models import CrawlJob
from shared.config import settings
from shared.logging import configure_logging

configure_logging("api")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    engine = create_async_engine(
        settings.postgres_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.neo4j_driver = driver

    logger.info("API starting up", extra={"postgres_url": settings.postgres_url})

    # Seed the active-crawl-jobs gauge from the current DB state on startup.
    try:
        async with session_factory() as session:
            count: int = (
                await session.execute(
                    select(func.count()).where(CrawlJob.status == "running")
                )
            ).scalar_one()
            ACTIVE_CRAWL_JOBS.set(count)
    except Exception:
        logger.warning("Could not seed active_crawl_jobs gauge on startup")

    yield

    logger.info("API shutting down")
    await driver.close()
    await engine.dispose()


app = FastAPI(title="argus API", version="0.1.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS — allow the Next.js dev server and any local origin
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Prometheus instrumentation (request latency, throughput)
# ---------------------------------------------------------------------------
Instrumentator(
    should_group_status_codes=False,
    excluded_handlers=["/metrics", "/health"],
).instrument(app)


# ---------------------------------------------------------------------------
# Middleware: track 4xx/5xx in a dedicated counter
# ---------------------------------------------------------------------------
@app.middleware("http")
async def _error_tracking_middleware(request: Request, call_next) -> Response:  # type: ignore[type-arg]
    response: Response = await call_next(request)
    if response.status_code >= 400:
        API_REQUEST_ERRORS_TOTAL.labels(
            method=request.method,
            path=request.url.path,
            status_code=str(response.status_code),
        ).inc()
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
app.include_router(papers_router)
app.include_router(authors_router)
app.include_router(crawls_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", tags=["observability"], include_in_schema=False)
async def metrics() -> Response:
    """Expose Prometheus metrics in text format."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
