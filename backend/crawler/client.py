from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from shared.config import settings

logger = logging.getLogger(__name__)

OPENALEX_BASE_URL = "https://api.openalex.org"
MAX_RETRIES = 5
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class _TokenBucket:
    """Async token bucket for rate limiting.

    Refills at `rate` tokens per second and blocks callers until a token
    is available, ensuring the downstream request rate never exceeds `rate`.
    """

    def __init__(self, rate: float) -> None:
        self._rate = rate
        self._tokens = float(rate)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


class OpenAlexClient:
    """Async HTTP client for the OpenAlex REST API.

    - Sets the polite-pool ``User-Agent`` header (email included).
    - Enforces a token-bucket rate limit (default: 10 req/s).
    - Retries on ``429`` and ``5xx`` responses with exponential backoff.

    Usage::

        async with OpenAlexClient() as client:
            data = await client.get("/works", params={"filter": "topics.id:T123"})
    """

    def __init__(
        self,
        base_url: str = OPENALEX_BASE_URL,
        rate_limit: int | None = None,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._base_url = base_url
        self._rate_limiter = _TokenBucket(rate_limit if rate_limit is not None else settings.openalex_rate_limit)
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OpenAlexClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "User-Agent": f"ArticleGraph/0.1 (mailto:{settings.openalex_email})",
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a rate-limited GET request with automatic retry.

        Args:
            path: API path relative to the base URL (e.g. ``"/works"``).
            params: Query parameters to include in the request.

        Returns:
            Parsed JSON response as a dict.

        Raises:
            httpx.HTTPStatusError: When a non-retryable HTTP error is received
                or all retries are exhausted.
            httpx.RequestError: When a network error persists after all retries.
            RuntimeError: If called outside an async context manager.
        """
        if self._client is None:
            raise RuntimeError("OpenAlexClient must be used as an async context manager")

        await self._rate_limiter.acquire()

        last_exc: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                response = await self._client.get(path, params=params)

                if response.status_code in RETRY_STATUSES:
                    wait = 2**attempt
                    logger.warning(
                        "Retrying request after %s response",
                        response.status_code,
                        extra={
                            "status_code": response.status_code,
                            "attempt": attempt + 1,
                            "max_retries": self._max_retries,
                            "wait_seconds": wait,
                            "path": path,
                            "params": params,
                        },
                    )
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    await asyncio.sleep(wait)
                    continue

                response.raise_for_status()
                return response.json()  # type: ignore[no-any-return]

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in RETRY_STATUSES:
                    raise
                last_exc = exc
                wait = 2**attempt
                logger.warning(
                    "Retrying request after HTTP error",
                    extra={
                        "status_code": exc.response.status_code,
                        "attempt": attempt + 1,
                        "max_retries": self._max_retries,
                        "wait_seconds": wait,
                        "path": path,
                        "params": params,
                    },
                )
                await asyncio.sleep(wait)

            except httpx.RequestError as exc:
                last_exc = exc
                wait = 2**attempt
                logger.warning(
                    "Retrying request after network error",
                    extra={
                        "error": str(exc),
                        "attempt": attempt + 1,
                        "max_retries": self._max_retries,
                        "wait_seconds": wait,
                        "path": path,
                        "params": params,
                    },
                )
                await asyncio.sleep(wait)

        raise last_exc or RuntimeError(
            f"Request to {path} failed after {self._max_retries} attempts"
        )
