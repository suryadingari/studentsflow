"""In-memory consent, rate-limit, event, and outreach-state services."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.agents.events import AgentEvent, AgentEventType, EventRecorder
from app.schemas.outreach import (
    FollowUpPlan, OptOutRecord, OptOutRequest, OutreachEvent, OutreachEventType,
    OutreachState, OutreachStatus, ProviderResult, ReplyRecord,
)


def _recipient_key(value: str) -> str:
    return value.strip().lower()


class InMemoryOptOutService:
    """Monotonic opt-out store: this phase intentionally provides no re-subscribe."""

    def __init__(self, *, event_recorder: EventRecorder | None = None,
                 outreach_store: "InMemoryOutreachStore | None" = None) -> None:
        self._records: dict[str, OptOutRecord] = {}
        self._lock = asyncio.Lock()
        self.event_recorder = event_recorder
        self.outreach_store = outreach_store

    async def has_opted_out(self, recipient: str) -> bool:
        async with self._lock:
            return _recipient_key(recipient) in self._records

    async def record_opt_out(self, request: OptOutRequest) -> tuple[OptOutRecord, bool]:
        key = _recipient_key(request.recipient)
        async with self._lock:
            if key in self._records:
                return self._records[key].model_copy(deep=True), False
            record = OptOutRecord(recipient=key, source=request.source, reason=request.reason)
            self._records[key] = record
        cancelled_ids = (await self.outreach_store.mark_opted_out(key, record.occurred_at)
                         if self.outreach_store is not None else [])
        if self.event_recorder is not None:
            hashed_recipient = hashlib.sha256(key.encode()).hexdigest()[:16]
            await self.event_recorder.record(AgentEvent(
                event_type=AgentEventType.OUTREACH_OPT_OUT_RECORDED,
                execution_id=f"optout-{hashed_recipient}", workflow_id=None,
                workflow_step_id=None, agent_name="opt_out_service", agent_version="1.0.0",
                details={"recipient_reference": hashed_recipient, "source": request.source or "unspecified"},
            ))
            for outreach_id in cancelled_ids:
                await self.event_recorder.record(AgentEvent(
                    event_type=AgentEventType.FOLLOW_UP_CANCELLED,
                    execution_id=f"optout-{hashed_recipient}", workflow_id=None,
                    workflow_step_id=None, agent_name="opt_out_service", agent_version="1.0.0",
                    details={"outreach_id": outreach_id, "reason": "recipient opted out"},
                ))
        return record.model_copy(deep=True), True


class InMemoryRateLimiter:
    """Sliding-window limiter with optional per-recipient and per-domain ceilings."""

    def __init__(self, *, max_messages: int = 50, window_seconds: int = 3600,
                 per_recipient_limit: int | None = None, per_domain_limit: int | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        if max_messages < 1 or window_seconds < 1:
            raise ValueError("rate limits must be positive")
        self.max_messages = max_messages
        self.window = timedelta(seconds=window_seconds)
        self.per_recipient_limit = per_recipient_limit
        self.per_domain_limit = per_domain_limit
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._timestamps: list[tuple[datetime, str, str]] = []
        self._lock = asyncio.Lock()

    async def acquire(self, recipient: str) -> tuple[bool, str | None]:
        now = self.clock()
        recipient_key = _recipient_key(recipient)
        domain = recipient_key.rsplit("@", 1)[-1] if "@" in recipient_key else ""
        async with self._lock:
            self._timestamps = [(stamp, rcpt, dom) for stamp, rcpt, dom in self._timestamps
                                if now - stamp < self.window]
            if len(self._timestamps) >= self.max_messages:
                return False, "global message rate limit exceeded"
            if self.per_recipient_limit is not None and sum(rcpt == recipient_key for _, rcpt, _ in self._timestamps) >= self.per_recipient_limit:
                return False, "recipient message rate limit exceeded"
            if self.per_domain_limit is not None and sum(dom == domain for _, _, dom in self._timestamps) >= self.per_domain_limit:
                return False, "recipient domain rate limit exceeded"
            self._timestamps.append((now, recipient_key, domain))
            return True, None


class InMemoryOutreachStore:
    """Atomic process-local store for send keys, events, replies, and plans."""

    def __init__(self) -> None:
        self._states: dict[str, OutreachState] = {}
        self._key_to_outreach: dict[str, str] = {}
        self._inflight: set[str] = set()
        self._sent_results: dict[str, ProviderResult] = {}
        self._events: list[OutreachEvent] = []
        self._replies: dict[str, ReplyRecord] = {}
        self._plans: dict[tuple[str, int], FollowUpPlan] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, state: OutreachState) -> tuple[str, OutreachState, ProviderResult | None]:
        async with self._lock:
            key = state.idempotency_key
            existing_id = self._key_to_outreach.get(key)
            if existing_id is None:
                self._key_to_outreach[key] = state.outreach_id
                self._states[state.outreach_id] = state.model_copy(deep=True)
                existing_id = state.outreach_id
            current = self._states[existing_id]
            if key in self._sent_results:
                return "sent", current.model_copy(deep=True), self._sent_results[key].model_copy(deep=True)
            if key in self._inflight:
                return "in_progress", current.model_copy(deep=True), None
            self._inflight.add(key)
            return "reserved", current.model_copy(deep=True), None

    async def finish_attempt(self, outreach_id: str, result: ProviderResult) -> OutreachState:
        async with self._lock:
            state = self._states[outreach_id]
            state.provider_result = result
            if result.status.value == "sent":
                state.status = OutreachStatus.SENT
                state.sent_at = result.timestamp
                self._sent_results[state.idempotency_key] = result.model_copy(deep=True)
            elif result.status.value == "temporary_failure":
                state.status = OutreachStatus.TEMPORARY_FAILURE
            elif result.status.value == "permanent_failure":
                state.status = OutreachStatus.PERMANENT_FAILURE
            else:
                state.status = OutreachStatus.FAILED
            self._inflight.discard(state.idempotency_key)
            return state.model_copy(deep=True)

    async def release(self, idempotency_key: str) -> None:
        async with self._lock:
            self._inflight.discard(idempotency_key)

    async def get_state(self, outreach_id: str) -> OutreachState:
        async with self._lock:
            if outreach_id not in self._states:
                raise KeyError(f"Unknown outreach: {outreach_id}")
            return self._states[outreach_id].model_copy(deep=True)

    async def state_by_key(self, idempotency_key: str) -> OutreachState | None:
        async with self._lock:
            outreach_id = self._key_to_outreach.get(idempotency_key)
            return self._states[outreach_id].model_copy(deep=True) if outreach_id else None

    async def events_for(self, outreach_id: str) -> list[OutreachEvent]:
        async with self._lock:
            return [item.model_copy(deep=True) for item in self._events if item.outreach_id == outreach_id]

    async def add_event(self, event: OutreachEvent) -> OutreachEvent:
        async with self._lock:
            self._events.append(event.model_copy(deep=True))
            return event

    async def record_reply(self, record: ReplyRecord) -> tuple[ReplyRecord, bool]:
        async with self._lock:
            if record.outreach_id not in self._states:
                raise KeyError(f"Unknown outreach: {record.outreach_id}")
            state = self._states[record.outreach_id]
            if state.sent_at is None:
                raise ValueError("a reply can only be recorded for a successfully sent outreach")
            existing = self._replies.get(record.outreach_id)
            if existing:
                return existing.model_copy(deep=True), False
            self._replies[record.outreach_id] = record.model_copy(deep=True)
            state.replied_at = record.occurred_at
            self._events.append(OutreachEvent(
                outreach_id=record.outreach_id, event_type=OutreachEventType.REPLY_RECEIVED,
                occurred_at=record.occurred_at, metadata=record.metadata,
            ))
            for key, plan in list(self._plans.items()):
                if plan.original_outreach_id == record.outreach_id and plan.status.value in {"due", "not_due"}:
                    plan.status = type(plan.status).CANCELLED
                    plan.reason = "Recipient replied; follow-up stopped."
                    self._plans[key] = plan
                    self._events.append(OutreachEvent(
                        outreach_id=record.outreach_id,
                        event_type=OutreachEventType.FOLLOW_UP_CANCELLED,
                        occurred_at=record.occurred_at,
                        metadata={"follow_up_number": plan.follow_up_number,
                                  "reason": "recipient replied"},
                    ))
            return record.model_copy(deep=True), True

    async def mark_opted_out(self, recipient: str, occurred_at: datetime) -> list[str]:
        key = _recipient_key(recipient)
        cancelled_ids: list[str] = []
        async with self._lock:
            for state in self._states.values():
                if _recipient_key(state.recipient) == key and state.opted_out_at is None:
                    state.opted_out_at = occurred_at
                    self._events.append(OutreachEvent(
                        outreach_id=state.outreach_id, event_type=OutreachEventType.OPTED_OUT,
                        occurred_at=occurred_at, metadata={"source": "opt_out_service"},
                    ))
                    for key, plan in list(self._plans.items()):
                        if plan.original_outreach_id == state.outreach_id and plan.status.value in {"due", "not_due"}:
                            plan.status = type(plan.status).CANCELLED
                            plan.reason = "Recipient opted out; follow-up stopped."
                            self._plans[key] = plan
                            self._events.append(OutreachEvent(
                                outreach_id=state.outreach_id,
                                event_type=OutreachEventType.FOLLOW_UP_CANCELLED,
                                occurred_at=occurred_at,
                                metadata={"follow_up_number": plan.follow_up_number,
                                          "reason": "recipient opted out"},
                            ))
                            cancelled_ids.append(state.outreach_id)
        return cancelled_ids

    async def set_campaign_cancelled(self, campaign_id: str) -> int:
        changed = 0
        async with self._lock:
            for state in self._states.values():
                if state.campaign_id == campaign_id and not state.campaign_cancelled:
                    state.campaign_cancelled = True
                    self._events.append(OutreachEvent(
                        outreach_id=state.outreach_id, event_type=OutreachEventType.FOLLOW_UP_CANCELLED,
                        metadata={"reason": "campaign cancelled"},
                    ))
                    for key, plan in list(self._plans.items()):
                        if plan.original_outreach_id == state.outreach_id and plan.status.value in {"due", "not_due"}:
                            plan.status = type(plan.status).CANCELLED
                            plan.reason = "Campaign was cancelled; follow-up stopped."
                            self._plans[key] = plan
                            self._events.append(OutreachEvent(
                                outreach_id=state.outreach_id,
                                event_type=OutreachEventType.FOLLOW_UP_CANCELLED,
                                metadata={"follow_up_number": plan.follow_up_number,
                                          "reason": "campaign cancelled"},
                            ))
                    changed += 1
        return changed

    async def get_follow_up(self, outreach_id: str, number: int) -> FollowUpPlan | None:
        async with self._lock:
            item = self._plans.get((outreach_id, number))
            return item.model_copy(deep=True) if item else None

    async def save_follow_up(self, plan: FollowUpPlan) -> FollowUpPlan:
        async with self._lock:
            key = (plan.original_outreach_id, plan.follow_up_number)
            if key in self._plans:
                return self._plans[key].model_copy(deep=True)
            self._plans[key] = plan.model_copy(deep=True)
            state = self._states[plan.original_outreach_id]
            if plan.status.value == "due":
                state.follow_up_count = max(state.follow_up_count, plan.follow_up_number)
            event_type = (OutreachEventType.FOLLOW_UP_PLANNED if plan.status.value == "due"
                          else OutreachEventType.FOLLOW_UP_CANCELLED if plan.status.value == "cancelled"
                          else None)
            if event_type:
                self._events.append(OutreachEvent(
                    outreach_id=plan.original_outreach_id, event_type=event_type,
                    occurred_at=plan.due_at or datetime.now(timezone.utc),
                    metadata={"follow_up_number": plan.follow_up_number, "reason": plan.reason},
                ))
            return plan.model_copy(deep=True)

    async def cancel_open_follow_ups(self, outreach_id: str, reason: str,
                                     occurred_at: datetime) -> list[FollowUpPlan]:
        changed: list[FollowUpPlan] = []
        async with self._lock:
            for key, plan in list(self._plans.items()):
                if plan.original_outreach_id == outreach_id and plan.status.value in {"due", "not_due"}:
                    plan.status = type(plan.status).CANCELLED
                    plan.reason = reason
                    self._plans[key] = plan
                    changed.append(plan.model_copy(deep=True))
                    self._events.append(OutreachEvent(
                        outreach_id=outreach_id, event_type=OutreachEventType.FOLLOW_UP_CANCELLED,
                        occurred_at=occurred_at,
                        metadata={"follow_up_number": plan.follow_up_number, "reason": reason},
                    ))
        return changed
