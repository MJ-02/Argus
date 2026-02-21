"""Author endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from neo4j import AsyncDriver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, get_neo4j
from api.schemas import AuthorOut, PaperOut, PaperPage
from db.models import AuthorMetadata, PaperMetadata
from db.neo4j_queries import count_papers_by_author, get_papers_by_author

router = APIRouter(prefix="/authors", tags=["authors"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


@router.get("/{author_id}", response_model=AuthorOut)
async def get_author(author_id: str, db: AsyncSession = Depends(get_db)) -> AuthorOut:
    row = await db.get(AuthorMetadata, author_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Author not found")
    return AuthorOut(
        id=row.openalex_id,
        name=row.name,
        orcid=row.orcid,
        works_count=row.works_count,
        citation_count=row.citation_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/{author_id}/papers", response_model=PaperPage)
async def get_author_papers(
    author_id: str,
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    neo4j: AsyncDriver = Depends(get_neo4j),
) -> PaperPage:
    author = await db.get(AuthorMetadata, author_id)
    if author is None:
        raise HTTPException(status_code=404, detail="Author not found")

    total = await count_papers_by_author(neo4j, author_id)
    paper_ids = await get_papers_by_author(neo4j, author_id, limit=limit, offset=offset)

    if not paper_ids:
        return PaperPage(total=total, limit=limit, offset=offset, items=[])

    stmt = select(PaperMetadata).where(PaperMetadata.openalex_id.in_(paper_ids))
    rows = (await db.execute(stmt)).scalars().all()

    paper_map = {r.openalex_id: r for r in rows}
    items = [
        PaperOut(
            id=r.openalex_id,
            title=r.title,
            abstract=r.abstract,
            publication_year=r.publication_year,
            doi=r.doi,
            citation_count=r.citation_count,
            source=r.source,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for pid in paper_ids
        if (r := paper_map.get(pid)) is not None
    ]

    return PaperPage(total=total, limit=limit, offset=offset, items=items)
