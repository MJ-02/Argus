"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from neo4j import AsyncGraphDatabase
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routes.authors import router as authors_router
from api.routes.crawls import router as crawls_router
from api.routes.papers import router as papers_router
from shared.config import settings


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

    yield

    await driver.close()
    await engine.dispose()


app = FastAPI(title="ArticleGraph API", version="0.1.0", lifespan=lifespan)

app.include_router(papers_router)
app.include_router(authors_router)
app.include_router(crawls_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
