"""Neo4j read queries for the API layer."""
from __future__ import annotations

from neo4j import AsyncDriver


async def get_citation_graph(
    driver: AsyncDriver,
    paper_id: str,
    depth: int,
) -> dict:
    """Return ``{nodes: [...], edges: [...]}`` for the citation graph rooted at *paper_id*.

    Traverses outgoing CITES edges up to *depth* hops.  Depth must be an integer
    in [1, 3] — the caller is responsible for validation.

    Cypher variable-length path ranges do not support runtime-parameterised bounds,
    so *depth* is interpolated directly into the query string.  This is safe because
    the route layer enforces ``1 <= depth <= 3`` before calling this function.
    """
    # Neo4j 5 strict aggregation: aggregate first, then combine with the root node
    # in a subsequent WITH clause to avoid implicit grouping errors.
    node_query = f"""
    MATCH (root:Paper {{id: $paper_id}})
    OPTIONAL MATCH (root)-[:CITES*1..{depth}]->(cited:Paper)
    WITH root, collect(DISTINCT cited) AS cited_papers
    WITH [root] + cited_papers AS papers
    UNWIND papers AS p
    RETURN DISTINCT
        p.id               AS id,
        p.title            AS title,
        p.publication_year AS publication_year,
        p.citation_count   AS citation_count
    """

    edge_query = f"""
    MATCH (root:Paper {{id: $paper_id}})
    OPTIONAL MATCH (root)-[:CITES*1..{depth}]->(cited:Paper)
    WITH root, collect(DISTINCT cited) AS cited_papers
    WITH [root] + cited_papers AS papers
    WITH [p IN papers | p.id] AS ids
    MATCH (a:Paper)-[:CITES]->(b:Paper)
    WHERE a.id IN ids AND b.id IN ids
    RETURN DISTINCT a.id AS source, b.id AS target
    """

    async with driver.session() as session:
        node_result = await session.run(node_query, paper_id=paper_id)
        nodes = [
            {
                "id": record["id"],
                "title": record["title"],
                "publication_year": record["publication_year"],
                "citation_count": record["citation_count"],
            }
            async for record in node_result
        ]

        edge_result = await session.run(edge_query, paper_id=paper_id)
        edges = [
            {"source": record["source"], "target": record["target"]}
            async for record in edge_result
        ]

    return {"nodes": nodes, "edges": edges}


async def get_paper_node(driver: AsyncDriver, paper_id: str) -> dict | None:
    """Return basic paper properties from a Neo4j node, or None if not found."""
    query = """
    MATCH (p:Paper {id: $paper_id})
    RETURN p.id               AS id,
           p.title            AS title,
           p.publication_year AS publication_year,
           p.citation_count   AS citation_count,
           p.doi              AS doi,
           p.abstract         AS abstract,
           p.source           AS source
    """
    async with driver.session() as session:
        result = await session.run(query, paper_id=paper_id)
        record = await result.single()
        if record is None:
            return None
        return {
            "id": record["id"],
            "title": record["title"],
            "publication_year": record["publication_year"],
            "citation_count": record["citation_count"],
            "doi": record["doi"],
            "abstract": record["abstract"],
            "source": record["source"],
        }


async def get_papers_by_author(
    driver: AsyncDriver,
    author_id: str,
    limit: int,
    offset: int,
) -> list[str]:
    """Return paper IDs written by *author_id*, ordered by publication_year DESC."""
    query = """
    MATCH (a:Author {id: $author_id})-[:WROTE]->(p:Paper)
    RETURN p.id AS id
    ORDER BY p.publication_year DESC, p.id ASC
    SKIP $offset LIMIT $limit
    """
    async with driver.session() as session:
        result = await session.run(query, author_id=author_id, limit=limit, offset=offset)
        return [record["id"] async for record in result]


async def count_papers_by_author(driver: AsyncDriver, author_id: str) -> int:
    """Return the total number of papers written by *author_id* in Neo4j."""
    query = """
    MATCH (a:Author {id: $author_id})-[:WROTE]->(p:Paper)
    RETURN count(p) AS total
    """
    async with driver.session() as session:
        result = await session.run(query, author_id=author_id)
        record = await result.single()
        return int(record["total"]) if record else 0
