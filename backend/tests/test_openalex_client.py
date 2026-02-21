"""Unit tests for the OpenAlex async HTTP client and works endpoint.

httpx transports are mocked with respx so no real network calls are made.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from crawler.client import OpenAlexClient, _TokenBucket
from crawler.openalex import WorksFilter, fetch_works_page, iter_works

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(base_url: str = "https://api.openalex.org", **kwargs: Any) -> OpenAlexClient:
    """Return an OpenAlexClient pointed at the given base URL."""
    return OpenAlexClient(base_url=base_url, rate_limit=1000, **kwargs)


# ---------------------------------------------------------------------------
# _TokenBucket
# ---------------------------------------------------------------------------


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_does_not_block_when_tokens_available(self) -> None:
        bucket = _TokenBucket(rate=10)
        # Should complete without sleeping when bucket is full.
        with patch("crawler.client.asyncio.sleep") as mock_sleep:
            await bucket.acquire()
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocks_when_tokens_exhausted(self) -> None:
        bucket = _TokenBucket(rate=1)
        # Drain the bucket.
        with patch("crawler.client.asyncio.sleep"):
            for _ in range(10):
                await bucket.acquire()

        # Next acquire must sleep to wait for refill.
        with patch("crawler.client.asyncio.sleep") as mock_sleep:
            await bucket.acquire()
            mock_sleep.assert_called_once()
            wait = mock_sleep.call_args[0][0]
            assert wait > 0


# ---------------------------------------------------------------------------
# OpenAlexClient — context manager and User-Agent
# ---------------------------------------------------------------------------


class TestOpenAlexClientContextManager:
    @pytest.mark.asyncio
    async def test_raises_if_used_outside_context_manager(self) -> None:
        client = _make_client()
        with pytest.raises(RuntimeError, match="async context manager"):
            await client.get("/works")

    @pytest.mark.asyncio
    @respx.mock
    async def test_sets_polite_pool_user_agent(self) -> None:
        route = respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json={"results": [], "meta": {"next_cursor": None}})
        )

        async with _make_client() as client:
            await client.get("/works")

        assert route.called
        sent_ua = route.calls[0].request.headers["user-agent"]
        assert "mailto:" in sent_ua

    @pytest.mark.asyncio
    @respx.mock
    async def test_closes_underlying_client_on_exit(self) -> None:
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json={"results": [], "meta": {}})
        )

        async with _make_client() as client:
            inner = client._client
            assert inner is not None

        assert client._client is None


# ---------------------------------------------------------------------------
# OpenAlexClient — retry logic
# ---------------------------------------------------------------------------


class TestOpenAlexClientRetry:
    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_429_then_succeeds(self) -> None:
        page = load_fixture("works_page_1.json")
        calls = 0

        def side_effect(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                return httpx.Response(429)
            return httpx.Response(200, json=page)

        respx.get("https://api.openalex.org/works").mock(side_effect=side_effect)

        with patch("crawler.client.asyncio.sleep"):
            async with _make_client(max_retries=5) as client:
                data = await client.get("/works")

        assert data["results"] == page["results"]
        assert calls == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_500_then_succeeds(self) -> None:
        page = load_fixture("works_page_1.json")
        call_count = 0

        def side_effect(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=page) if call_count >= 2 else httpx.Response(500)

        respx.get("https://api.openalex.org/works").mock(side_effect=side_effect)

        with patch("crawler.client.asyncio.sleep"):
            async with _make_client(max_retries=5) as client:
                data = await client.get("/works")

        assert len(data["results"]) == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_after_max_retries_exhausted(self) -> None:
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(429)
        )

        with patch("crawler.client.asyncio.sleep"):
            async with _make_client(max_retries=3) as client:
                with pytest.raises(httpx.HTTPStatusError):
                    await client.get("/works")

    @pytest.mark.asyncio
    @respx.mock
    async def test_does_not_retry_on_404(self) -> None:
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(404)
        )

        async with _make_client(max_retries=5) as client:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.get("/works")

        assert exc_info.value.response.status_code == 404

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_network_error_then_succeeds(self) -> None:
        page = load_fixture("works_page_1.json")
        call_count = 0

        def side_effect(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ConnectError("Connection refused")
            return httpx.Response(200, json=page)

        respx.get("https://api.openalex.org/works").mock(side_effect=side_effect)

        with patch("crawler.client.asyncio.sleep"):
            async with _make_client(max_retries=5) as client:
                data = await client.get("/works")

        assert data["results"] == page["results"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_backoff_wait_doubles_each_attempt(self) -> None:
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(429)
        )

        sleep_calls: list[float] = []

        async def capture_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with patch("crawler.client.asyncio.sleep", side_effect=capture_sleep):
            async with _make_client(max_retries=4) as client:
                with pytest.raises(httpx.HTTPStatusError):
                    await client.get("/works")

        # All 4 attempts sleep before their continue/retry: 1, 2, 4, 8.
        assert sleep_calls == [1, 2, 4, 8]


# ---------------------------------------------------------------------------
# WorksFilter
# ---------------------------------------------------------------------------


class TestWorksFilter:
    def test_returns_none_when_no_filters_set(self) -> None:
        f = WorksFilter()
        assert f.to_filter_string() is None

    def test_topic_filter(self) -> None:
        f = WorksFilter(topic_id="T10116")
        assert f.to_filter_string() == "topics.id:T10116"

    def test_date_range_filter(self) -> None:
        f = WorksFilter(date_from="2020-01-01", date_to="2023-12-31")
        result = f.to_filter_string()
        assert "from_publication_date:2020-01-01" in result
        assert "to_publication_date:2023-12-31" in result

    def test_institution_filter(self) -> None:
        f = WorksFilter(institution_id="I27837315")
        assert f.to_filter_string() == "institutions.id:I27837315"

    def test_paper_ids_filter_joins_with_pipe(self) -> None:
        f = WorksFilter(paper_ids=["W111", "W222", "W333"])
        result = f.to_filter_string()
        assert "openalex_id:W111|W222|W333" in result

    def test_combined_filters_joined_with_comma(self) -> None:
        f = WorksFilter(topic_id="T10116", date_from="2020-01-01", institution_id="I999")
        result = f.to_filter_string()
        parts = result.split(",")
        assert len(parts) == 3
        assert "topics.id:T10116" in parts
        assert "from_publication_date:2020-01-01" in parts
        assert "institutions.id:I999" in parts


# ---------------------------------------------------------------------------
# fetch_works_page
# ---------------------------------------------------------------------------


class TestFetchWorksPage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_works_and_next_cursor(self) -> None:
        page = load_fixture("works_page_1.json")
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json=page)
        )

        async with _make_client() as client:
            works, next_cursor = await fetch_works_page(client, cursor="*")

        assert len(works) == 3
        assert next_cursor == page["meta"]["next_cursor"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_none_next_cursor_on_last_page(self) -> None:
        page = load_fixture("works_page_2.json")
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json=page)
        )

        async with _make_client() as client:
            works, next_cursor = await fetch_works_page(client, cursor="some-cursor")

        assert next_cursor is None
        assert len(works) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_filter_param(self) -> None:
        page = load_fixture("works_page_2.json")
        route = respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json=page)
        )

        f = WorksFilter(topic_id="T10116", date_from="2020-01-01")

        async with _make_client() as client:
            await fetch_works_page(client, filter_params=f)

        qs = dict(route.calls[0].request.url.params)
        assert "filter" in qs
        assert "topics.id:T10116" in qs["filter"]
        assert "from_publication_date:2020-01-01" in qs["filter"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_appends_updated_after_to_filter(self) -> None:
        page = load_fixture("works_page_2.json")
        route = respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json=page)
        )

        async with _make_client() as client:
            await fetch_works_page(client, updated_after="2024-01-01")

        qs = dict(route.calls[0].request.url.params)
        assert "from_updated_date:2024-01-01" in qs["filter"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_cursor_param(self) -> None:
        page = load_fixture("works_page_2.json")
        route = respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json=page)
        )

        async with _make_client() as client:
            await fetch_works_page(client, cursor="abc123")

        qs = dict(route.calls[0].request.url.params)
        assert qs["cursor"] == "abc123"

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_page_size_param(self) -> None:
        page = load_fixture("works_page_2.json")
        route = respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json=page)
        )

        async with _make_client() as client:
            await fetch_works_page(client, page_size=50)

        qs = dict(route.calls[0].request.url.params)
        assert qs["per-page"] == "50"


# ---------------------------------------------------------------------------
# iter_works
# ---------------------------------------------------------------------------


class TestIterWorks:
    @pytest.mark.asyncio
    @respx.mock
    async def test_paginates_through_all_pages(self) -> None:
        page1 = load_fixture("works_page_1.json")
        page2 = load_fixture("works_page_2.json")

        call_count = 0

        def side_effect(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=page1 if call_count == 1 else page2)

        respx.get("https://api.openalex.org/works").mock(side_effect=side_effect)

        collected: list[dict] = []
        cursors: list[str] = []

        async with _make_client() as client:
            async for works, cursor in iter_works(client):
                collected.extend(works)
                cursors.append(cursor)

        assert len(collected) == 4  # 3 from page1 + 1 from page2
        assert call_count == 2
        assert cursors[0] == "*"

    @pytest.mark.asyncio
    @respx.mock
    async def test_stops_when_no_next_cursor(self) -> None:
        page = load_fixture("works_page_2.json")
        respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json=page)
        )

        pages: list[list[dict]] = []

        async with _make_client() as client:
            async for works, _ in iter_works(client):
                pages.append(works)

        assert len(pages) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_resumes_from_given_start_cursor(self) -> None:
        page = load_fixture("works_page_2.json")
        route = respx.get("https://api.openalex.org/works").mock(
            return_value=httpx.Response(200, json=page)
        )

        async with _make_client() as client:
            async for _ in iter_works(client, start_cursor="resume-cursor-xyz"):
                break

        qs = dict(route.calls[0].request.url.params)
        assert qs["cursor"] == "resume-cursor-xyz"

    @pytest.mark.asyncio
    @respx.mock
    async def test_passes_filter_and_updated_after_to_each_page(self) -> None:
        page1 = load_fixture("works_page_1.json")
        page2 = load_fixture("works_page_2.json")

        call_count = 0

        def side_effect(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=page1 if call_count == 1 else page2)

        route = respx.get("https://api.openalex.org/works").mock(side_effect=side_effect)

        f = WorksFilter(topic_id="T10116")

        async with _make_client() as client:
            async for _ in iter_works(client, filter_params=f, updated_after="2024-01-01"):
                pass

        for call in route.calls:
            qs = dict(call.request.url.params)
            assert "topics.id:T10116" in qs["filter"]
            assert "from_updated_date:2024-01-01" in qs["filter"]
