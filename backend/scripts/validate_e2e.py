#!/usr/bin/env python3
"""End-to-end validation script for Phase 8.1 (10k papers) and 8.5 (100k papers).

Connects to a running ArticleGraph Docker Compose stack and:
  1. Starts a crawl job with configurable seed parameters
  2. Polls the crawl status until the job completes or reaches the target count
  3. Queries Postgres and Neo4j directly to compare paper counts
  4. Reports throughput, duration, and final statistics

Usage:
    # Phase 8.1 — crawl to 10k papers
    uv run python scripts/validate_e2e.py --target 10000

    # Phase 8.5 — crawl to 100k papers (wide date range for volume)
    uv run python scripts/validate_e2e.py --target 100000 --date-from 2020-01-01

    # Custom API URL
    uv run python scripts/validate_e2e.py --target 10000 --api-url http://localhost:8000

Exit codes:
    0  — validation passed
    1  — validation failed (count mismatch or unhealthy stack)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

import asyncpg
import httpx
from neo4j import AsyncGraphDatabase


# ---------------------------------------------------------------------------
# DB verification helpers
# ---------------------------------------------------------------------------


async def _count_postgres_papers(pg_url: str) -> int:
    conn = await asyncpg.connect(pg_url)
    try:
        return await conn.fetchval("SELECT COUNT(*) FROM papers_metadata")
    finally:
        await conn.close()


async def _count_neo4j_papers(uri: str, user: str, password: str) -> int:
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    try:
        async with driver.session() as session:
            result = await session.run("MATCH (p:Paper) RETURN count(p) AS cnt")
            record = await result.single()
            return int(record["cnt"]) if record else 0
    finally:
        await driver.close()


async def _verify_counts(
    pg_url: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
) -> dict[str, int]:
    pg_count, neo4j_count = await asyncio.gather(
        _count_postgres_papers(pg_url),
        _count_neo4j_papers(neo4j_uri, neo4j_user, neo4j_password),
    )
    return {"postgres": pg_count, "neo4j": neo4j_count}


# ---------------------------------------------------------------------------
# Main validation logic
# ---------------------------------------------------------------------------


def _build_seed(args: argparse.Namespace) -> dict[str, Any]:
    seed: dict[str, Any] = {}
    if args.date_from:
        seed["date_from"] = args.date_from
    if args.date_to:
        seed["date_to"] = args.date_to
    if args.topic_id:
        seed["topic_id"] = args.topic_id
    if args.institution_id:
        seed["institution_id"] = args.institution_id
    return seed


def _poll_until_done(
    client: httpx.Client,
    job_id: str,
    target: int,
    poll_interval: int,
    t0: float,
) -> tuple[str, int]:
    """Block until the crawl job finishes or exceeds `target` papers.

    Returns ``(final_status, records_processed)``.
    """
    terminal = {"completed", "stopped", "failed"}

    while True:
        resp = client.get(f"/crawls/{job_id}")
        resp.raise_for_status()
        job = resp.json()
        status: str = job["status"]
        records: int = job.get("records_processed", 0)
        elapsed = time.monotonic() - t0
        rate = records / elapsed if elapsed > 0 else 0.0

        print(
            f"  [poll] status={status:<12s}  records={records:>10,}  "
            f"elapsed={elapsed:>7.0f}s  rate={rate:>6.1f}/s"
        )

        if status in terminal:
            return status, records

        if records >= target:
            print(f"  [poll] reached target {target:,} — sending stop signal")
            stop_resp = client.post(f"/crawls/{job_id}/stop")
            if stop_resp.status_code not in (200, 409):
                print(f"  [warn] stop returned HTTP {stop_resp.status_code}")

        time.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ArticleGraph Phase 8 end-to-end validator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--target", type=int, default=10_000,
        help="Target paper count to validate against",
    )
    parser.add_argument(
        "--api-url", default="http://localhost:8000",
        help="Base URL of the running ArticleGraph API",
    )
    parser.add_argument(
        "--date-from", default="2023-01-01",
        help="OpenAlex filter: crawl works published/updated from this date",
    )
    parser.add_argument("--date-to", default=None, help="OpenAlex filter: upper date bound")
    parser.add_argument("--topic-id", default=None, help="OpenAlex topic ID filter (e.g. T10116)")
    parser.add_argument(
        "--institution-id", default=None, help="OpenAlex institution ID filter",
    )
    parser.add_argument(
        "--pg-url",
        default="postgresql://articlegraph:articlegraph@localhost:5432/articlegraph",
        help="asyncpg-compatible Postgres URL for count verification",
    )
    parser.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default="articlegraph")
    parser.add_argument(
        "--poll-interval", type=int, default=15,
        help="Seconds between crawl status polls",
    )
    args = parser.parse_args()

    seed = _build_seed(args)
    print(f"\nArticleGraph Phase 8 — End-to-End Validator")
    print(f"  API           : {args.api_url}")
    print(f"  Target papers : {args.target:,}")
    print(f"  Seed config   : {seed}")
    print()

    t0 = time.monotonic()

    with httpx.Client(base_url=args.api_url, timeout=30.0) as client:
        # 1. Health check
        try:
            health = client.get("/health")
            health.raise_for_status()
        except Exception as exc:
            print(f"[ERROR] API health check failed: {exc}")
            sys.exit(1)
        print("[ok] API is healthy")

        # 2. Start crawl
        try:
            resp = client.post("/crawls", json=seed)
            resp.raise_for_status()
        except Exception as exc:
            print(f"[ERROR] Failed to start crawl: {exc}")
            sys.exit(1)

        job_id: str = resp.json()["id"]
        print(f"[ok] Crawl job started: {job_id}")
        print()

        # 3. Poll until done
        final_status, records_processed = _poll_until_done(
            client, job_id, args.target, args.poll_interval, t0
        )

    elapsed_total = time.monotonic() - t0

    # 4. Verify Postgres / Neo4j counts match
    print()
    print("[verify] Querying Postgres and Neo4j for paper counts...")
    try:
        counts = asyncio.run(
            _verify_counts(args.pg_url, args.neo4j_uri, args.neo4j_user, args.neo4j_password)
        )
    except Exception as exc:
        print(f"[ERROR] Count verification failed: {exc}")
        sys.exit(1)

    pg_count = counts["postgres"]
    neo4j_count = counts["neo4j"]
    throughput = pg_count / elapsed_total if elapsed_total > 0 else 0.0

    # 5. Report
    print()
    print("=" * 62)
    print(f"  Final job status  : {final_status}")
    print(f"  Records processed : {records_processed:,}  (from crawl_state)")
    print(f"  Postgres papers   : {pg_count:,}")
    print(f"  Neo4j nodes       : {neo4j_count:,}  (includes citation stubs)")
    print(f"  Total duration    : {elapsed_total:.1f}s")
    print(f"  Throughput        : {throughput:.1f} papers/s")
    print("=" * 62)

    # 6. Assertions
    passed = True

    if pg_count < args.target:
        print(
            f"\n[WARN] Postgres count {pg_count:,} is below target {args.target:,}. "
            f"The crawl may have been stopped early or the source has fewer papers."
        )

    # Neo4j node count must be >= Postgres paper count.
    # Neo4j includes citation stub nodes (papers referenced but not yet crawled),
    # so its count is typically higher than Postgres.
    if neo4j_count < pg_count:
        print(
            f"\n[FAIL] Neo4j node count ({neo4j_count:,}) < Postgres count ({pg_count:,}). "
            f"This indicates Neo4j write failures. Check worker logs."
        )
        passed = False
    else:
        print(
            f"\n[ok] Neo4j nodes ({neo4j_count:,}) >= Postgres papers ({pg_count:,}) ✓"
        )

    if final_status == "failed":
        print(f"[FAIL] Crawl job ended with status 'failed'. Check worker logs for errors.")
        passed = False

    if passed:
        print("[ok] Validation passed!\n")
        sys.exit(0)
    else:
        print("[FAIL] Validation failed.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
