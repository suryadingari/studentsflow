"""Provider boundary. The only implementation here is non-sending and local."""

import hashlib
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Protocol

from app.schemas.outreach import ProviderMessage, ProviderResult, ProviderStatus


class EmailProvider(Protocol):
    provider_name: str

    async def send(self, message: ProviderMessage) -> ProviderResult: ...


class MockEmailProvider:
    """Simulates delivery outcomes; it has no network or email transport code."""

    provider_name = "mock"
    sends_real_email = False

    def __init__(
        self,
        outcomes: Sequence[ProviderStatus] = (ProviderStatus.SENT,),
        *,
        timestamp: datetime | None = None,
    ) -> None:
        self.outcomes = list(outcomes) or [ProviderStatus.SENT]
        self.timestamp = timestamp or datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.calls: list[ProviderMessage] = []

    async def send(self, message: ProviderMessage) -> ProviderResult:
        self.calls.append(message)
        index = len(self.calls) - 1
        status = self.outcomes[min(index, len(self.outcomes) - 1)]
        message_id = None
        if status == ProviderStatus.SENT:
            message_id = "mock-" + hashlib.sha256(message.idempotency_key.encode()).hexdigest()[:16]
        error = None if status == ProviderStatus.SENT else f"Mock provider simulated {status.value}."
        return ProviderResult(
            provider_message_id=message_id, status=status, timestamp=self.timestamp,
            recipient=message.recipient, subject=message.subject, error=error,
            provider_name=self.provider_name,
        )
