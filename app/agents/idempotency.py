import asyncio
from typing import Protocol


class IdempotencyStore(Protocol):
    async def claim(self, key: str) -> bool: ...


class InMemoryIdempotencyStore:
    """Atomic process-local key claims; replace with durable storage later."""

    def __init__(self) -> None:
        self._keys: set[str] = set()
        self._lock = asyncio.Lock()

    async def claim(self, key: str) -> bool:
        async with self._lock:
            if key in self._keys:
                return False
            self._keys.add(key)
            return True
