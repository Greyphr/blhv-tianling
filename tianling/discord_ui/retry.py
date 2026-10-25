"""Capped retry with exponential backoff for Discord API calls (429 and 5xx only)."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


def is_retryable(exc: BaseException) -> bool:
    """429 (rate limited) and 5xx are transient; every other 4xx is a real error."""
    status = getattr(exc, "status", None)
    return isinstance(status, int) and (status == 429 or 500 <= status < 600)


async def with_retry(
    call: Callable[[], Awaitable[T]],
    *,
    attempts: int = 4,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``call`` and retry transient failures up to ``attempts`` times in total."""
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except Exception as exc:
            if attempt == attempts or not is_retryable(exc):
                raise
            hinted = getattr(exc, "retry_after", None)
            delay = (
                float(hinted)
                if isinstance(hinted, int | float)
                else base_delay * 2 ** (attempt - 1)
            )
            delay = min(max_delay, delay) + random.uniform(0, 0.1)
            log.warning(
                "discord call failed (%s); retry %d/%d in %.1fs", exc, attempt, attempts - 1, delay
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
