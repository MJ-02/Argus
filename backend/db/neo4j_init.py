"""
One-shot script that applies Neo4j constraints and indexes.

Run after Neo4j is healthy:
    python -m db.neo4j_init

Also executed by the `neo4j-init` Docker Compose service on first startup.
"""
import asyncio
import logging
import os
import time

from neo4j import AsyncGraphDatabase

logger = logging.getLogger(__name__)

CONSTRAINTS_AND_INDEXES = [
    "CREATE CONSTRAINT paper_id_unique IF NOT EXISTS FOR (p:Paper) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT author_id_unique IF NOT EXISTS FOR (a:Author) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT institution_id_unique IF NOT EXISTS FOR (i:Institution) REQUIRE i.id IS UNIQUE",
    "CREATE CONSTRAINT topic_id_unique IF NOT EXISTS FOR (t:Topic) REQUIRE t.id IS UNIQUE",
    "CREATE INDEX paper_year_idx IF NOT EXISTS FOR (p:Paper) ON (p.publication_year)",
    "CREATE INDEX paper_citation_count_idx IF NOT EXISTS FOR (p:Paper) ON (p.citation_count)",
]


async def apply(uri: str, user: str, password: str, max_retries: int = 10) -> None:
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    try:
        for attempt in range(1, max_retries + 1):
            try:
                async with driver.session() as session:
                    for stmt in CONSTRAINTS_AND_INDEXES:
                        await session.run(stmt)
                        logger.info("applied: %s", stmt.split("FOR")[0].strip())
                logger.info("Neo4j schema initialised successfully")
                return
            except Exception as exc:
                if attempt == max_retries:
                    raise
                wait = min(2 ** attempt, 30)
                logger.warning(
                    "Neo4j not ready (attempt %d/%d): %s — retrying in %ds",
                    attempt, max_retries, exc, wait,
                )
                time.sleep(wait)
    finally:
        await driver.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(
        apply(
            uri=os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
            user=os.environ.get("NEO4J_USER", "neo4j"),
            password=os.environ.get("NEO4J_PASSWORD", "argus-1234"),
        )
    )
