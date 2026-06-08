"""
ClipForge — Shared retry helper for external HTTP APIs.

A transient 5xx / connection blip from ElevenLabs / OpenAI / Anthropic /
Ollama must not kill a 10-minute pipeline run. This wraps an async callable
with exponential backoff + jitter. 4xx errors (auth, quota, bad request)
are NEVER retried — those won't fix themselves.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger("clipforge.retry")
T = TypeVar("T")

# Retry on these HTTP statuses (server-side / rate limit) and connection errors.
_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 4,
    base_delay: float = 1.5,
    label: str = "api call",
) -> T:
    """Retry an async callable with exponential backoff + jitter.

    `fn` must raise httpx.HTTPStatusError (i.e. call response.raise_for_status())
    for HTTP errors so we can inspect the status code. Non-retryable errors
    (4xx other than 429) propagate immediately. Raises the LAST exception if
    all attempts are exhausted.
    """
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status not in _RETRYABLE_HTTP_STATUS or attempt == max_attempts:
                raise
            last = e
            await _sleep_backoff(attempt, base_delay, label, f"HTTP {status}")
        except _RETRYABLE_EXC as e:
            if attempt == max_attempts:
                raise
            last = e
            await _sleep_backoff(attempt, base_delay, label, type(e).__name__)
    if last:
        raise last
    raise RuntimeError(f"{label}: retry loop exited without success")


async def _sleep_backoff(attempt: int, base_delay: float, label: str, why: str) -> None:
    wait = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
    logger.warning(f"{label}: {why} on attempt {attempt}, retrying in {wait:.1f}s")
    await asyncio.sleep(wait)
