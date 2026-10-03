import asyncio
import time
from collections.abc import Awaitable, Callable


class PerDomainRateLimiter:
    """Process-local minimum interval limiter; does not retry blocked requests."""

    def __init__(
        self,
        minimum_interval_seconds: float,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if minimum_interval_seconds < 0:
            raise ValueError("minimum_interval_seconds cannot be negative")
        self.minimum_interval_seconds = minimum_interval_seconds
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, domain: str) -> None:
        async with self._lock:
            now = self._clock()
            previous = self._last_request.get(domain)
            if previous is not None:
                delay = self.minimum_interval_seconds - (now - previous)
                if delay > 0:
                    await self._sleep(delay)
            self._last_request[domain] = self._clock()
