"""Paper endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from neo4j import AsyncDriver
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, get_neo4j
from api.schemas import CitationEdge, CitationGraph, CitationNode, PaperOut, PaperPage
from db.models import PaperMetadata
from db.neo4j_queries import get_citation_graph, get_paper_node

router = APIRouter(prefix="/papers", tags=["papers"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_DEFAULT_DEPTH = 1
_MAX_DEPTH = 3


@router.get("/search", response_model=PaperPage)
async def search_papers(
    q: str | None = Query(None, description="Full-text search on title and abstract"),
    topic: str | None = Query(None, description="Filter by Topic ID"),
    year_from: int | None = Query(None, description="Minimum publication year (inclusive)"),
    year_to: int | None = Query(None, description="Maximum publication year (inclusive)"),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    neo4j: AsyncDriver = Depends(get_neo4j),
) -> PaperPage:
    stmt = select(PaperMetadata)

    if q:
        stmt = stmt.where(
            or_(
                PaperMetadata.title.ilike(f"%{q}%"),
                PaperMetadata.abstract.ilike(f"%{q}%"),
            )
        )
    if year_from is not None:
        stmt = stmt.where(PaperMetadata.publication_year >= year_from)
    if year_to is not None:
        stmt = stmt.where(PaperMetadata.publication_year <= year_to)

    if topic:
        topic_query = """
        MATCH (t:Topic {id: $topic_id})<-[:HAS_TOPIC]-(p:Paper)
        RETURN p.id AS id
        """
        async with neo4j.session() as neo4j_session:
            result = await neo4j_session.run(topic_query, topic_id=topic)
            paper_ids = [record["id"] async for record in result]
        if not paper_ids:
            return PaperPage(total=0, limit=limit, offset=offset, items=[])
        stmt = stmt.where(PaperMetadata.openalex_id.in_(paper_ids))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        stmt.order_by(PaperMetadata.citation_count.desc(), PaperMetadata.openalex_id.asc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()

    return PaperPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[_paper_row_to_out(r) for r in rows],
    )


@router.get("/{paper_id}", response_model=PaperOut)
async def get_paper(
    paper_id: str,
    db: AsyncSession = Depends(get_db),
    neo4j: AsyncDriver = Depends(get_neo4j),
) -> PaperOut:
    row = await db.get(PaperMetadata, paper_id)
    if row is not None:
        return _paper_row_to_out(row)
    node = await get_paper_node(neo4j, paper_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return PaperOut(
        id=node["id"],
        title=node["title"],
        abstract=node["abstract"],
        publication_year=node["publication_year"],
        doi=node["doi"],
        citation_count=node["citation_count"] or 0,
        source=node["source"],
        created_at=None,
        updated_at=None,
    )


@router.get("/{paper_id}/citations", response_model=CitationGraph)
async def get_citations(
    paper_id: str,
    depth: int = Query(_DEFAULT_DEPTH, ge=1, le=_MAX_DEPTH),
    db: AsyncSession = Depends(get_db),
    neo4j: AsyncDriver = Depends(get_neo4j),
) -> CitationGraph:
    row = await db.get(PaperMetadata, paper_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Paper not found")

    graph = await get_citation_graph(neo4j, paper_id, depth)
    return CitationGraph(
        nodes=[CitationNode(**n) for n in graph["nodes"]],
        edges=[CitationEdge(**e) for e in graph["edges"]],
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _paper_row_to_out(row: PaperMetadata) -> PaperOut:
    return PaperOut(
        id=row.openalex_id,
        title=row.title,
        abstract=row.abstract,
        publication_year=row.publication_year,
        doi=row.doi,
        citation_count=row.citation_count,
        source=row.source,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
