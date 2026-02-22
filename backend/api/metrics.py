"""Prometheus metric definitions for the ArticleGraph API.

All metrics are module-level singletons so they are registered once with the
default prometheus_client registry.  Import and update them from route handlers
or middleware — do **not** create new Gauge/Counter instances per request.

Usage::

    from api.metrics import PAPERS_TOTAL, ACTIVE_CRAWL_JOBS
    PAPERS_TOTAL.inc()
    ACTIVE_CRAWL_JOBS.set(3)
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

PAPERS_INGESTED_TOTAL = Counter(
    "articlegraph_papers_ingested_total",
    "Cumulative number of papers written to Postgres across all crawl jobs.",
)

AUTHORS_INGESTED_TOTAL = Counter(
    "articlegraph_authors_ingested_total",
    "Cumulative number of authors written to Postgres across all crawl jobs.",
)

INSTITUTIONS_INGESTED_TOTAL = Counter(
    "articlegraph_institutions_ingested_total",
    "Cumulative number of institutions written to Postgres across all crawl jobs.",
)

ACTIVE_CRAWL_JOBS = Gauge(
    "articlegraph_active_crawl_jobs",
    "Number of crawl jobs currently in 'running' state.",
)

CRAWL_PAGES_TOTAL = Counter(
    "articlegraph_crawl_pages_total",
    "Total number of OpenAlex pages fetched across all crawl jobs.",
    labelnames=["job_id"],
)

CRAWL_ERRORS_TOTAL = Counter(
    "articlegraph_crawl_errors_total",
    "Total number of page-level errors encountered during crawling.",
    labelnames=["job_id"],
)

CRAWL_PAGE_DURATION_SECONDS = Histogram(
    "articlegraph_crawl_page_duration_seconds",
    "Wall-clock time to fetch, extract, and write a single OpenAlex page.",
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)

API_REQUEST_ERRORS_TOTAL = Counter(
    "articlegraph_api_request_errors_total",
    "Total number of 4xx/5xx responses returned by the API.",
    labelnames=["method", "path", "status_code"],
)
