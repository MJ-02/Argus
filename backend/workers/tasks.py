"""Celery tasks for the argus crawler worker."""
from __future__ import annotations

import asyncio
import logging

from celery import Task
from neo4j import AsyncGraphDatabase
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from crawler.engine import SeedConfig, run_crawl
from shared.config import settings
from workers.celery_app import app

logger = logging.getLogger(__name__)


@app.task(bind=True, name="workers.tasks.crawl_work", max_retries=0)
def crawl_work(
    self: Task,
    job_id: str | None = None,
    seed_config: dict | None = None,
    incremental: bool = False,
) -> dict:
    """Run a crawl job to completion.

    Creates a new crawl job when *job_id* is ``None``; resumes the existing
    job otherwise.  All async resources (DB engine, Neo4j driver) are created
    fresh inside ``asyncio.run()`` so each task invocation owns an isolated
    event loop and connection pool.

    Args:
        job_id: Existing crawl job ID to resume, or ``None`` for a new job.
        seed_config: Serialisable :class:`~crawler.engine.SeedConfig` dict.
            ``None`` means no filters (fetch all works from OpenAlex).
        incremental: When ``True``, only works updated since the job's
            ``last_crawled_at`` are fetched.

    Returns:
        ``{"job_id": str, "status": "done"}``
    """
    config = SeedConfig.from_dict(seed_config or {})

    async def _run() -> str:
        engine = create_async_engine(
            settings.postgres_url,
            pool_size=5,
            max_overflow=10,
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
        try:
            async with session_factory() as session:
                return await run_crawl(
                    job_id=job_id,
                    seed_config=config,
                    pg_session=session,
                    neo4j_driver=driver,
                    incremental=incremental,
                )
        finally:
            await driver.close()
            await engine.dispose()

    final_job_id = asyncio.run(_run())
    logger.info("Crawl task finished", extra={"job_id": final_job_id})
    return {"job_id": final_job_id, "status": "done"}
