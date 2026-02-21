"""Crawl management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from api.schemas import CrawlJobOut, StartCrawlIn
from crawler.engine import SeedConfig
from db.models import CrawlJob, CrawlState
from db.postgres_writer import create_crawl_job, update_crawl_job_status

router = APIRouter(prefix="/crawls", tags=["crawls"])

_STOPPABLE = {"pending", "running"}
_RESUMABLE = {"stopped", "failed"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_out(job: CrawlJob, state: CrawlState | None) -> CrawlJobOut:
    return CrawlJobOut(
        id=job.id,
        seed_config=job.seed_config,
        status=job.status,
        created_at=job.created_at,
        completed_at=job.completed_at,
        records_processed=state.records_processed if state else 0,
        last_crawled_at=state.last_crawled_at if state else None,
        cursor=state.cursor if state else None,
        last_error=state.last_error if state else None,
    )


async def _fetch(db: AsyncSession, job_id: str) -> tuple[CrawlJob, CrawlState | None]:
    job = await db.get(CrawlJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Crawl job not found")
    result = await db.execute(select(CrawlState).where(CrawlState.job_id == job_id))
    state = result.scalar_one_or_none()
    return job, state


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=CrawlJobOut, status_code=201)
async def start_crawl(body: StartCrawlIn, db: AsyncSession = Depends(get_db)) -> CrawlJobOut:
    from workers.celery_app import app as celery_app

    seed = SeedConfig(
        topic_id=body.topic_id,
        date_from=body.date_from,
        date_to=body.date_to,
        institution_id=body.institution_id,
        paper_ids=body.paper_ids,
    )
    job = await create_crawl_job(db, seed.to_dict())
    await db.commit()
    await db.refresh(job)

    celery_app.send_task(
        "workers.tasks.crawl_work",
        kwargs={
            "job_id": job.id,
            "seed_config": seed.to_dict(),
            "incremental": body.incremental,
        },
    )

    result = await db.execute(select(CrawlState).where(CrawlState.job_id == job.id))
    state = result.scalar_one_or_none()
    return _to_out(job, state)


@router.get("/{job_id}", response_model=CrawlJobOut)
async def get_crawl(job_id: str, db: AsyncSession = Depends(get_db)) -> CrawlJobOut:
    job, state = await _fetch(db, job_id)
    return _to_out(job, state)


@router.post("/{job_id}/stop", response_model=CrawlJobOut)
async def stop_crawl(job_id: str, db: AsyncSession = Depends(get_db)) -> CrawlJobOut:
    job, state = await _fetch(db, job_id)
    if job.status not in _STOPPABLE:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot stop a job with status '{job.status}'",
        )
    await update_crawl_job_status(db, job_id, "stopping")
    await db.commit()
    await db.refresh(job)
    return _to_out(job, state)


@router.post("/{job_id}/resume", response_model=CrawlJobOut)
async def resume_crawl(job_id: str, db: AsyncSession = Depends(get_db)) -> CrawlJobOut:
    from workers.celery_app import app as celery_app

    job, state = await _fetch(db, job_id)
    if job.status not in _RESUMABLE:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot resume a job with status '{job.status}'",
        )
    await update_crawl_job_status(db, job_id, "pending")
    await db.commit()
    await db.refresh(job)

    celery_app.send_task(
        "workers.tasks.crawl_work",
        kwargs={
            "job_id": job_id,
            "seed_config": job.seed_config,
            "incremental": False,
        },
    )

    return _to_out(job, state)
