from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from crawler.client import OpenAlexClient

logger = logging.getLogger(__name__)

WORKS_PATH = "/works"

# Fields to select from OpenAlex — keeps payload small and focused on what
# the extraction layer needs.
_WORKS_SELECT = (
    "id,title,abstract_inverted_index,publication_year,doi,"
    "cited_by_count,authorships,topics,referenced_works,updated_date"
)


@dataclass
class WorksFilter:
    """Filter parameters for the OpenAlex ``/works`` endpoint.

    All fields are optional.  Only non-None / non-empty fields are included
    in the generated filter string.
    """

    topic_id: str | None = None
    """OpenAlex topic ID, e.g. ``"T10116"``."""

    date_from: str | None = None
    """Earliest publication date, ISO format ``YYYY-MM-DD``."""

    date_to: str | None = None
    """Latest publication date, ISO format ``YYYY-MM-DD``."""

    institution_id: str | None = None
    """OpenAlex institution ID, e.g. ``"I27837315"``."""

    paper_ids: list[str] = field(default_factory=list)
    """Explicit list of OpenAlex work IDs to fetch, e.g. ``["W2741809807"]``."""

    def to_filter_string(self) -> str | None:
        """Build the OpenAlex filter query string from set fields.

        Returns ``None`` when no filters are set so callers can omit the
        ``filter`` param entirely.
        """
        parts: list[str] = []

        if self.topic_id:
            parts.append(f"topics.id:{self.topic_id}")
        if self.date_from:
            parts.append(f"from_publication_date:{self.date_from}")
        if self.date_to:
            parts.append(f"to_publication_date:{self.date_to}")
        if self.institution_id:
            parts.append(f"institutions.id:{self.institution_id}")
        if self.paper_ids:
            ids = "|".join(self.paper_ids)
            parts.append(f"openalex_id:{ids}")

        return ",".join(parts) if parts else None


async def fetch_works_page(
    client: OpenAlexClient,
    cursor: str = "*",
    filter_params: WorksFilter | None = None,
    page_size: int = 200,
    updated_after: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch a single page of works from the OpenAlex ``/works`` endpoint.

    Args:
        client: An initialised :class:`OpenAlexClient` context.
        cursor: OpenAlex cursor string. Use ``"*"`` for the first page.
        filter_params: Optional entity-level filters.
        page_size: Number of results per page (max 200).
        updated_after: ISO date string; when set, only works updated after
            this date are returned (used for incremental crawling).

    Returns:
        A ``(works, next_cursor)`` tuple.  ``next_cursor`` is ``None`` when
        this is the last page.
    """
    params: dict[str, Any] = {
        "cursor": cursor,
        "per-page": page_size,
        "select": _WORKS_SELECT,
    }

    filter_parts: list[str] = []

    if filter_params:
        f = filter_params.to_filter_string()
        if f:
            filter_parts.append(f)

    if updated_after:
        filter_parts.append(f"from_updated_date:{updated_after}")

    if filter_parts:
        params["filter"] = ",".join(filter_parts)

    data = await client.get(WORKS_PATH, params=params)

    works: list[dict[str, Any]] = data.get("results", [])
    next_cursor: str | None = data.get("meta", {}).get("next_cursor")

    logger.info(
        "Fetched works page",
        extra={
            "cursor": cursor,
            "count": len(works),
            "has_next": next_cursor is not None,
        },
    )

    return works, next_cursor


async def iter_works(
    client: OpenAlexClient,
    filter_params: WorksFilter | None = None,
    start_cursor: str = "*",
    page_size: int = 200,
    updated_after: str | None = None,
) -> AsyncGenerator[tuple[list[dict[str, Any]], str], None]:
    """Async generator that pages through all matching works.

    Yields ``(works, cursor)`` for each page, where ``cursor`` is the cursor
    value that was used to fetch that page (useful for persisting crawl state).

    Iteration stops when OpenAlex returns no ``next_cursor``.

    Args:
        client: An initialised :class:`OpenAlexClient` context.
        filter_params: Optional entity-level filters.
        start_cursor: Cursor to resume from; ``"*"`` starts from the beginning.
        page_size: Number of results per page (max 200).
        updated_after: ISO date string for incremental crawl mode.
    """
    cursor: str | None = start_cursor

    while cursor is not None:
        works, next_cursor = await fetch_works_page(
            client,
            cursor=cursor,
            filter_params=filter_params,
            page_size=page_size,
            updated_after=updated_after,
        )
        yield works, cursor
        cursor = next_cursor
