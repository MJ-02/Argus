"""Pydantic request/response models for the argus API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class PaperOut(BaseModel):
    id: str
    title: str | None
    abstract: str | None
    publication_year: int | None
    doi: str | None
    citation_count: int
    source: str | None
    created_at: datetime | None
    updated_at: datetime | None


class PaperPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[PaperOut]


class AuthorOut(BaseModel):
    id: str
    name: str | None
    orcid: str | None
    works_count: int
    citation_count: int
    created_at: datetime
    updated_at: datetime


class CitationNode(BaseModel):
    id: str
    title: str | None
    publication_year: int | None
    citation_count: int | None


class CitationEdge(BaseModel):
    source: str
    target: str


class CitationGraph(BaseModel):
    nodes: list[CitationNode]
    edges: list[CitationEdge]


class StartCrawlIn(BaseModel):
    topic_id: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    institution_id: str | None = None
    paper_ids: list[str] = []
    incremental: bool = False


class CrawlJobOut(BaseModel):
    id: str
    seed_config: dict[str, Any]
    status: str
    created_at: datetime
    completed_at: datetime | None
    records_processed: int
    last_crawled_at: datetime | None
    cursor: str | None
    last_error: str | None
    metrics: dict[str, Any] | None = None
