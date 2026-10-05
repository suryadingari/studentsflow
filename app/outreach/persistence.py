"""PostgreSQL-backed consent, send idempotency, event, reply, and follow-up stores."""

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.persistence import validate_storage_url, _safe_details
from app.agents.events import AgentEvent, AgentEventType
from app.models import (FollowUpPlanRecord, OptOutRecordModel, OutreachEventRecord,
                        OutreachRecord, ReplyRecordModel)
from app.outreach.services import InMemoryOptOutService, InMemoryOutreachStore
from app.schemas.outreach import (FollowUpPlan, OptOutRecord, OptOutRequest, OutreachEvent,
                                  OutreachState, OutreachStatus, ProviderResult, ReplyRecord)


class PersistentOutreachStore(InMemoryOutreachStore):
    """Database-backed send reservation and follow-up state with an in-process cache."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], database_url: str) -> None:
        super().__init__()
        self.sessions = sessions
        self.database_url = database_url

    def _check(self) -> None:
        validate_storage_url(self.database_url, allow_sqlite=True)

    async def reserve(self, state: OutreachState):
        self._check()
        async with self.sessions.begin() as session:
            row = await session.scalar(select(OutreachRecord).where(
                OutreachRecord.idempotency_key == state.idempotency_key).with_for_update())
            if row is not None:
                existing = OutreachState.model_validate(row.state_json)
                if row.status == OutreachStatus.SENT.value and row.provider_result_json:
                    return "sent", existing, ProviderResult.model_validate(row.provider_result_json)
                if row.status == OutreachStatus.IN_PROGRESS.value:
                    return "in_progress", existing, None
                row.status = OutreachStatus.IN_PROGRESS.value
                row.state_json = state.model_dump(mode="json")
            else:
                # Draft/workflow rows are committed before this side-effect boundary.
                row = OutreachRecord(
                    outreach_id=state.outreach_id, workflow_id=state.workflow_id,
                    draft_id=state.draft_id, idempotency_key=state.idempotency_key,
                    status=state.status.value, recipient=state.recipient,
                    state_json=state.model_dump(mode="json"))
                session.add(row)
        result = await super().reserve(state)
        await self._flush_cached_events(state.outreach_id)
        return result

    async def finish_attempt(self, outreach_id: str, result: ProviderResult) -> OutreachState:
        state = await super().finish_attempt(outreach_id, result)
        self._check()
        async with self.sessions.begin() as session:
            row = await session.get(OutreachRecord, outreach_id)
            if row is None:
                raise KeyError(f"Unknown persisted outreach: {outreach_id}")
            row.status = state.status.value
            row.provider = result.provider_name
            row.provider_message_id = result.provider_message_id
            row.provider_result_json = result.model_dump(mode="json")
            row.state_json = state.model_dump(mode="json")
            row.sent_at = state.sent_at
        return state

    async def get_state(self, outreach_id: str) -> OutreachState:
        try:
            return await super().get_state(outreach_id)
        except KeyError:
            self._check()
            async with self.sessions() as session:
                row = await session.get(OutreachRecord, outreach_id)
                if row is None:
                    raise KeyError(f"Unknown outreach: {outreach_id}")
                state = OutreachState.model_validate(row.state_json)
            await self._cache_state(state)
            return state

    async def state_by_key(self, idempotency_key: str):
        found = await super().state_by_key(idempotency_key)
        if found is not None:
            return found
        self._check()
        async with self.sessions() as session:
            row = await session.scalar(select(OutreachRecord).where(
                OutreachRecord.idempotency_key == idempotency_key))
            state = OutreachState.model_validate(row.state_json) if row else None
        if state:
            await self._cache_state(state)
        return state

    async def add_event(self, event: OutreachEvent) -> OutreachEvent:
        result = await super().add_event(event)
        self._check()
        async with self.sessions() as session:
            exists = await session.get(OutreachRecord, event.outreach_id)
        if exists is not None:
            await self._persist_event(event)
        return result

    async def _flush_cached_events(self, outreach_id: str) -> None:
        for event in await super().events_for(outreach_id):
            await self._persist_event(event)

    async def _persist_event(self, event: OutreachEvent) -> None:
        async with self.sessions.begin() as session:
            if await session.get(OutreachEventRecord, event.event_id) is None:
                session.add(OutreachEventRecord(
                    event_id=event.event_id, outreach_id=event.outreach_id,
                    event_type=event.event_type.value, occurred_at=event.occurred_at,
                    provider=event.provider, provider_event_type=event.provider_event_type,
                    metadata_json=_safe_details(event.metadata)))

    async def events_for(self, outreach_id: str) -> list[OutreachEvent]:
        try:
            cached = await super().events_for(outreach_id)
            if cached:
                return cached
        except KeyError:
            pass
        self._check()
        async with self.sessions() as session:
            rows = (await session.scalars(select(OutreachEventRecord).where(
                OutreachEventRecord.outreach_id == outreach_id))).all()
            return [OutreachEvent(event_id=row.event_id, outreach_id=row.outreach_id,
                                   event_type=row.event_type, occurred_at=row.occurred_at,
                                   provider=row.provider, provider_event_type=row.provider_event_type,
                                   metadata=row.metadata_json) for row in rows]

    async def record_reply(self, record: ReplyRecord):
        await self.get_state(record.outreach_id)
        stored, created = await super().record_reply(record)
        if created:
            self._check()
            async with self.sessions.begin() as session:
                session.add(ReplyRecordModel(outreach_id=record.outreach_id,
                                             occurred_at=record.occurred_at,
                                             metadata_json=_safe_details(record.metadata)))
                row = await session.get(OutreachRecord, record.outreach_id)
                row.state_json = (await super().get_state(record.outreach_id)).model_dump(mode="json")
                plans = (await session.scalars(select(FollowUpPlanRecord).where(
                    FollowUpPlanRecord.outreach_id == record.outreach_id))).all()
                for plan in plans:
                    if plan.status in {"due", "not_due"}:
                        plan.status = "cancelled"
                        plan.reason = "Recipient replied; follow-up stopped."
            for event in await super().events_for(record.outreach_id):
                await self._persist_event(event)
        return stored, created

    async def mark_opted_out(self, recipient: str, occurred_at):
        self._check()
        async with self.sessions() as session:
            rows = (await session.scalars(select(OutreachRecord))).all()
            to_cache = [OutreachState.model_validate(row.state_json) for row in rows
                        if row.recipient.strip().lower() == recipient.strip().lower()]
        for state in to_cache:
            await self._cache_state(state)
        changed = await super().mark_opted_out(recipient, occurred_at)
        async with self.sessions.begin() as session:
            for state in to_cache:
                row = await session.get(OutreachRecord, state.outreach_id)
                live = await super().get_state(state.outreach_id)
                row.state_json = live.model_dump(mode="json")
            affected_ids = {state.outreach_id for state in to_cache}
            plans = (await session.scalars(select(FollowUpPlanRecord))).all()
            for plan in plans:
                if plan.outreach_id in affected_ids and plan.status in {"due", "not_due"}:
                    plan.status = "cancelled"
                    plan.reason = "Recipient opted out; follow-up stopped."
        return changed

    async def set_campaign_cancelled(self, campaign_id: str) -> int:
        self._check()
        async with self.sessions() as session:
            rows = (await session.scalars(select(OutreachRecord))).all()
            states = [OutreachState.model_validate(row.state_json) for row in rows]
        for state in states:
            if state.campaign_id == campaign_id:
                await self._cache_state(state)
        count = await super().set_campaign_cancelled(campaign_id)
        if count:
            async with self.sessions.begin() as session:
                for state in states:
                    if state.campaign_id == campaign_id:
                        row = await session.get(OutreachRecord, state.outreach_id)
                        row.state_json = (await super().get_state(state.outreach_id)).model_dump(mode="json")
                cancelled_ids = {state.outreach_id for state in states
                                 if state.campaign_id == campaign_id}
                plans = (await session.scalars(select(FollowUpPlanRecord))).all()
                for plan in plans:
                    if plan.outreach_id in cancelled_ids and plan.status in {"due", "not_due"}:
                        plan.status = "cancelled"
                        plan.reason = "Campaign was cancelled; follow-up stopped."
        return count

    async def save_follow_up(self, plan: FollowUpPlan) -> FollowUpPlan:
        stored = await super().save_follow_up(plan)
        self._check()
        async with self.sessions.begin() as session:
            row = await session.get(FollowUpPlanRecord, plan.plan_id)
            values = {"outreach_id": plan.original_outreach_id,
                      "follow_up_number": plan.follow_up_number, "recipient": plan.recipient,
                      "due_at": plan.due_at, "status": plan.status.value, "reason": plan.reason}
            if row is None:
                session.add(FollowUpPlanRecord(plan_id=plan.plan_id, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
        return stored

    async def get_follow_up(self, outreach_id: str, number: int):
        cached = await super().get_follow_up(outreach_id, number)
        if cached is not None:
            return cached
        self._check()
        async with self.sessions() as session:
            row = await session.scalar(select(FollowUpPlanRecord).where(
                FollowUpPlanRecord.outreach_id == outreach_id,
                FollowUpPlanRecord.follow_up_number == number))
            return _follow_up(row) if row else None

    async def cancel_open_follow_ups(self, outreach_id: str, reason: str, occurred_at):
        self._check()
        async with self.sessions() as session:
            rows = (await session.scalars(select(FollowUpPlanRecord).where(
                FollowUpPlanRecord.outreach_id == outreach_id))).all()
        for row in rows:
            if row.status in {"due", "not_due"}:
                async with self._lock:
                    self._plans[(outreach_id, row.follow_up_number)] = _follow_up(row)
        changed = await super().cancel_open_follow_ups(outreach_id, reason, occurred_at)
        if changed:
            async with self.sessions.begin() as session:
                for plan in changed:
                    row = await session.get(FollowUpPlanRecord, plan.plan_id)
                    row.status, row.reason = plan.status.value, plan.reason
        return changed

    async def _cache_state(self, state: OutreachState) -> None:
        async with self._lock:
            self._states[state.outreach_id] = state.model_copy(deep=True)
            self._key_to_outreach[state.idempotency_key] = state.outreach_id


class PersistentOptOutService(InMemoryOptOutService):
    """Monotonic durable opt-out service; checks PostgreSQL on every send decision."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], database_url: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sessions = sessions
        self.database_url = database_url

    async def has_opted_out(self, recipient: str) -> bool:
        if await super().has_opted_out(recipient):
            return True
        validate_storage_url(self.database_url, allow_sqlite=True)
        async with self.sessions() as session:
            return await session.get(OptOutRecordModel, recipient.strip().lower()) is not None

    async def record_opt_out(self, request: OptOutRequest):
        validate_storage_url(self.database_url, allow_sqlite=True)
        recipient = request.recipient.strip().lower()
        async with self.sessions.begin() as session:
            row = await session.get(OptOutRecordModel, recipient)
            created = row is None
            if row is None:
                row = OptOutRecordModel(recipient=recipient,
                                        occurred_at=datetime.now(timezone.utc),
                                        source=request.source, reason=request.reason)
                session.add(row)
            record = OptOutRecord(recipient=recipient, occurred_at=row.occurred_at,
                                  source=row.source, reason=row.reason)
        async with self._lock:
            self._records[recipient] = record
        # In-memory parent propagates the opt-out to cached outreach/follow-up state.
        cancelled_ids = []
        if self.outreach_store is not None:
            cancelled_ids = await self.outreach_store.mark_opted_out(recipient, record.occurred_at)
        if created and self.event_recorder is not None:
            ref = hashlib.sha256(recipient.encode()).hexdigest()[:16]
            await self.event_recorder.record(AgentEvent(
                event_type=AgentEventType.OUTREACH_OPT_OUT_RECORDED,
                execution_id=f"optout-{ref}", workflow_id=None, workflow_step_id=None,
                agent_name="opt_out_service", agent_version="1.0.0",
                details={"recipient_reference": ref, "source": request.source or "unspecified"}))
            for outreach_id in cancelled_ids:
                await self.event_recorder.record(AgentEvent(
                    event_type=AgentEventType.FOLLOW_UP_CANCELLED,
                    execution_id=f"optout-{ref}", workflow_id=None, workflow_step_id=None,
                    agent_name="opt_out_service", agent_version="1.0.0",
                    details={"outreach_id": outreach_id, "reason": "recipient opted out"}))
        return record.model_copy(deep=True), created


def _follow_up(row: FollowUpPlanRecord) -> FollowUpPlan:
    return FollowUpPlan(plan_id=row.plan_id, original_outreach_id=row.outreach_id,
                        recipient=row.recipient, due_at=row.due_at,
                        follow_up_number=row.follow_up_number, reason=row.reason,
                        status=row.status)
