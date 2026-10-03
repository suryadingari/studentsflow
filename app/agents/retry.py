import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from app.agents.exceptions import RetryableFailure

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 0
    initial_backoff_seconds: float = 0.0
    backoff_multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds cannot be negative")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be at least 1")

    async def run(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        on_retry: Callable[[int, float], Awaitable[None]] | None = None,
    ) -> tuple[T, int]:
        retries = 0
        while True:
            try:
                return await operation(), retries
            except RetryableFailure:
                if retries >= self.max_retries:
                    raise
                delay = self.initial_backoff_seconds * self.backoff_multiplier**retries
                retries += 1
                if on_retry is not None:
                    await on_retry(retries, delay)
                if delay:
                    await asyncio.sleep(delay)
