"""Neo4j graph writer.

All node and relationship writes use MERGE semantics so that re-ingesting the
same data is fully idempotent.  Batch writes are done with UNWIND for
throughput, chunked to avoid overly large Bolt messages.
"""
from __future__ import annotations

from typing import Sequence

from neo4j import AsyncDriver

from crawler.extractors import (
    Affiliation,
    Authorship,
    Author,
    Citation,
    Institution,
    Paper,
    PaperTopic,
    Topic,
)

_CHUNK_SIZE = 500


def _chunks(items: Sequence, size: int):
    """Yield successive *size*-length slices of *items*."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ---------------------------------------------------------------------------
# Node merges
# ---------------------------------------------------------------------------


async def merge_papers(driver: AsyncDriver, papers: Sequence[Paper]) -> None:
    """MERGE Paper nodes, setting all properties on each write."""
    if not papers:
        return
    query = """
    UNWIND $batch AS row
    MERGE (p:Paper {id: row.id})
    SET p.title            = row.title,
        p.abstract         = row.abstract,
        p.publication_year = row.publication_year,
        p.doi              = row.doi,
        p.citation_count   = row.citation_count,
        p.source           = row.source
    """
    batch = [
        {
            "id": p.id,
            "title": p.title,
            "abstract": p.abstract,
            "publication_year": p.publication_year,
            "doi": p.doi,
            "citation_count": p.citation_count,
            "source": p.source,
        }
        for p in papers
    ]
    async with driver.session() as session:
        for chunk in _chunks(batch, _CHUNK_SIZE):
            await session.run(query, batch=chunk)


async def merge_authors(driver: AsyncDriver, authors: Sequence[Author]) -> None:
    """MERGE Author nodes."""
    if not authors:
        return
    query = """
    UNWIND $batch AS row
    MERGE (a:Author {id: row.id})
    SET a.name           = row.name,
        a.orcid          = row.orcid,
        a.works_count    = row.works_count,
        a.citation_count = row.citation_count
    """
    batch = [
        {
            "id": a.id,
            "name": a.name,
            "orcid": a.orcid,
            "works_count": a.works_count,
            "citation_count": a.citation_count,
        }
        for a in authors
    ]
    async with driver.session() as session:
        for chunk in _chunks(batch, _CHUNK_SIZE):
            await session.run(query, batch=chunk)


async def merge_institutions(driver: AsyncDriver, institutions: Sequence[Institution]) -> None:
    """MERGE Institution nodes."""
    if not institutions:
        return
    query = """
    UNWIND $batch AS row
    MERGE (i:Institution {id: row.id})
    SET i.name             = row.name,
        i.country          = row.country,
        i.institution_type = row.institution_type
    """
    batch = [
        {
            "id": i.id,
            "name": i.name,
            "country": i.country,
            "institution_type": i.institution_type,
        }
        for i in institutions
    ]
    async with driver.session() as session:
        for chunk in _chunks(batch, _CHUNK_SIZE):
            await session.run(query, batch=chunk)


async def merge_topics(driver: AsyncDriver, topics: Sequence[Topic]) -> None:
    """MERGE Topic nodes."""
    if not topics:
        return
    query = """
    UNWIND $batch AS row
    MERGE (t:Topic {id: row.id})
    SET t.name   = row.name,
        t.domain = row.domain,
        t.field  = row.field
    """
    batch = [
        {
            "id": t.id,
            "name": t.name,
            "domain": t.domain,
            "field": t.field,
        }
        for t in topics
    ]
    async with driver.session() as session:
        for chunk in _chunks(batch, _CHUNK_SIZE):
            await session.run(query, batch=chunk)


# ---------------------------------------------------------------------------
# Relationship merges
# ---------------------------------------------------------------------------


async def merge_wrote_rels(driver: AsyncDriver, authorships: Sequence[Authorship]) -> None:
    """MERGE (Author)-[:WROTE]->(Paper) relationships."""
    if not authorships:
        return
    query = """
    UNWIND $batch AS row
    MERGE (a:Author {id: row.author_id})
    MERGE (p:Paper  {id: row.paper_id})
    MERGE (a)-[:WROTE]->(p)
    """
    batch = [
        {"author_id": rel.author_id, "paper_id": rel.paper_id}
        for rel in authorships
    ]
    async with driver.session() as session:
        for chunk in _chunks(batch, _CHUNK_SIZE):
            await session.run(query, batch=chunk)


async def merge_cites_rels(driver: AsyncDriver, citations: Sequence[Citation]) -> None:
    """MERGE (Paper)-[:CITES]->(Paper) relationships."""
    if not citations:
        return
    query = """
    UNWIND $batch AS row
    MERGE (src:Paper {id: row.source_id})
    MERGE (dst:Paper {id: row.cited_id})
    MERGE (src)-[:CITES]->(dst)
    """
    batch = [
        {"source_id": c.source_paper_id, "cited_id": c.cited_paper_id}
        for c in citations
    ]
    async with driver.session() as session:
        for chunk in _chunks(batch, _CHUNK_SIZE):
            await session.run(query, batch=chunk)


async def merge_affiliated_with_rels(
    driver: AsyncDriver, affiliations: Sequence[Affiliation]
) -> None:
    """MERGE (Author)-[:AFFILIATED_WITH {start_year, end_year, primary}]->(Institution).

    The AFFILIATED_WITH relationship carries temporal properties. MERGE matches
    on the pair (author, institution) only — properties are always SET so that
    re-ingestion refreshes them.
    """
    if not affiliations:
        return
    query = """
    UNWIND $batch AS row
    MERGE (a:Author      {id: row.author_id})
    MERGE (i:Institution {id: row.institution_id})
    MERGE (a)-[r:AFFILIATED_WITH]->(i)
    SET r.start_year = row.start_year,
        r.end_year   = row.end_year,
        r.primary    = row.primary
    """
    batch = [
        {
            "author_id": aff.author_id,
            "institution_id": aff.institution_id,
            "start_year": aff.start_year,
            "end_year": aff.end_year,
            "primary": aff.primary,
        }
        for aff in affiliations
    ]
    async with driver.session() as session:
        for chunk in _chunks(batch, _CHUNK_SIZE):
            await session.run(query, batch=chunk)


async def merge_has_topic_rels(driver: AsyncDriver, paper_topics: Sequence[PaperTopic]) -> None:
    """MERGE (Paper)-[:HAS_TOPIC]->(Topic) relationships."""
    if not paper_topics:
        return
    query = """
    UNWIND $batch AS row
    MERGE (p:Paper {id: row.paper_id})
    MERGE (t:Topic {id: row.topic_id})
    MERGE (p)-[:HAS_TOPIC]->(t)
    """
    batch = [
        {"paper_id": pt.paper_id, "topic_id": pt.topic_id}
        for pt in paper_topics
    ]
    async with driver.session() as session:
        for chunk in _chunks(batch, _CHUNK_SIZE):
            await session.run(query, batch=chunk)
