"""Plans at most a conservative follow-up; this agent cannot send messages."""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.events import AgentEvent, AgentEventType, EventRecorder
from app.agents.models import AgentInput
from app.outreach.services import InMemoryOptOutService, InMemoryOutreachStore
from app.schemas.outreach import (
    FollowUpPlan, FollowUpPolicy, FollowUpRequest, FollowUpResult,
    FollowUpStatus, OutreachEventType, OutreachStatus,
    ReplyRecord, ReplyStatus, OutreachEvent,
)


class FollowUpAgent(BaseAgent):
    agent_name = "follow_up"
    agent_version = "2.0.0"
    description = "Evaluates outreach state and plans a single configurable follow-up; never sends email."
    allowed_tools = frozenset({"outreach_event_store", "opt_out_service"})

    def __init__(self, *, outreach_store: InMemoryOutreachStore,
                 opt_out_service: InMemoryOptOutService,
                 policy: FollowUpPolicy | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.outreach_store = outreach_store
        self.opt_out_service = opt_out_service
        self.policy = policy or FollowUpPolicy()

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> FollowUpResult:
        request = agent_input.payload
        if not isinstance(request, FollowUpRequest):
            request = FollowUpRequest.model_validate(request)
        self.require_tool("outreach_event_store", context)
        self.require_tool("opt_out_service", context)
        try:
            state = await self.outreach_store.get_state(request.outreach_id)
        except KeyError as error:
            return FollowUpResult(plan=None, explanation=str(error))
        now = request.now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            return FollowUpResult(plan=None, explanation="Follow-up evaluation time must include a timezone.")

        reason = None
        reply_status = ReplyStatus.NO_REPLY
        if state.replied_at is not None:
            reason = "Recipient replied; follow-up stopped."
            reply_status = ReplyStatus.REPLIED
        elif state.opted_out_at is not None or await self.opt_out_service.has_opted_out(state.recipient):
            reason = "Recipient opted out; follow-up stopped."
            reply_status = ReplyStatus.OPTED_OUT
        elif state.campaign_cancelled:
            reason = "Campaign was cancelled; follow-up stopped."
        elif state.status == OutreachStatus.PERMANENT_FAILURE:
            reason = "Original outreach failed permanently; follow-up is not scheduled."
        elif state.status != OutreachStatus.SENT or state.sent_at is None:
            reason = "Original outreach has no successful send; follow-up is not scheduled."

        if reason:
            closed = await self.outreach_store.cancel_open_follow_ups(state.outreach_id, reason, now)
            if closed:
                plan = closed[-1]
            else:
                existing = (await self.outreach_store.get_follow_up(
                    state.outreach_id, state.follow_up_count
                ) if state.follow_up_count else None)
                if existing and existing.status == FollowUpStatus.CANCELLED:
                    return FollowUpResult(plan=existing, explanation=reason,
                                          reply_status=reply_status)
                plan = self._plan(state.outreach_id, state.recipient,
                                  max(1, state.follow_up_count + 1), None,
                                  reason, FollowUpStatus.CANCELLED)
                plan = await self.outreach_store.save_follow_up(plan)
            await self._audit(context, AgentEventType.FOLLOW_UP_CANCELLED,
                              {"outreach_id": state.outreach_id,
                               "follow_up_number": plan.follow_up_number})
            return FollowUpResult(plan=plan, explanation=reason, reply_status=reply_status)

        # A previously-created due plan remains the same plan; reevaluation must
        # not create a second follow-up task or consume another follow-up slot.
        if state.follow_up_count:
            existing = await self.outreach_store.get_follow_up(state.outreach_id, state.follow_up_count)
            if existing and existing.status == FollowUpStatus.DUE:
                return FollowUpResult(plan=existing, explanation="The existing follow-up plan remains due.",
                                      reply_status=ReplyStatus.NO_REPLY)

        number = state.follow_up_count + 1
        if number > self.policy.max_follow_ups:
            reason = "Configured follow-up limit has been reached."
            plan = self._plan(state.outreach_id, state.recipient, number, None,
                              reason, FollowUpStatus.CANCELLED)
            plan = await self.outreach_store.save_follow_up(plan)
            await self._audit(context, AgentEventType.FOLLOW_UP_CANCELLED,
                              {"outreach_id": state.outreach_id, "follow_up_number": number})
            return FollowUpResult(plan=plan, explanation=reason, reply_status=ReplyStatus.NO_REPLY)

        due_at = state.sent_at + timedelta(hours=self.policy.delay_hours * number)
        if now < due_at:
            plan = self._plan(state.outreach_id, state.recipient, number, due_at,
                              "Waiting for recipient reply; follow-up delay has not elapsed.",
                              FollowUpStatus.NOT_DUE)
            return FollowUpResult(plan=plan, explanation=plan.reason, reply_status=ReplyStatus.NO_REPLY)

        plan = self._plan(state.outreach_id, state.recipient, number, due_at,
                          "No reply or opt-out was recorded by the configured follow-up time.",
                          FollowUpStatus.DUE)
        plan = await self.outreach_store.save_follow_up(plan)
        await self._audit(context, AgentEventType.FOLLOW_UP_PLANNED,
                          {"outreach_id": state.outreach_id,
                           "follow_up_number": number, "due_at": due_at.isoformat()})
        return FollowUpResult(plan=plan, explanation=plan.reason, reply_status=ReplyStatus.NO_REPLY)

    @staticmethod
    def _plan(outreach_id: str, recipient: str, number: int, due_at: datetime | None,
              reason: str, status: FollowUpStatus) -> FollowUpPlan:
        plan_id = "followup-" + hashlib.sha256(f"{outreach_id}:{number}".encode()).hexdigest()[:18]
        return FollowUpPlan(plan_id=plan_id, original_outreach_id=outreach_id,
                            recipient=recipient, due_at=due_at,
                            follow_up_number=number, reason=reason, status=status)

    async def _audit(self, context: AgentContext, event_type: AgentEventType,
                     details: dict[str, Any]) -> None:
        await self._record(event_type, context, details)


class OutreachEventService:
    """Local/mock event intake for replies; it does not connect to an inbox."""

    def __init__(self, outreach_store: InMemoryOutreachStore,
                 *, event_recorder: EventRecorder | None = None) -> None:
        self.outreach_store = outreach_store
        self.event_recorder = event_recorder

    async def record_reply(self, outreach_id: str, timestamp: datetime | None = None,
                           metadata: dict[str, Any] | None = None):
        record, created = await self.outreach_store.record_reply(ReplyRecord(
            outreach_id=outreach_id, occurred_at=timestamp or datetime.now(timezone.utc),
            metadata=metadata or {},
        ))
        if created and self.event_recorder is not None:
            await self.event_recorder.record(AgentEvent(
                event_type=AgentEventType.OUTREACH_REPLY_RECORDED,
                execution_id=f"reply-{outreach_id}", workflow_id=None,
                workflow_step_id=None, agent_name="outreach_event_service",
                agent_version="1.0.0", details={"outreach_id": outreach_id},
            ))
            await self.event_recorder.record(AgentEvent(
                event_type=AgentEventType.FOLLOW_UP_CANCELLED,
                execution_id=f"reply-{outreach_id}", workflow_id=None,
                workflow_step_id=None, agent_name="outreach_event_service",
                agent_version="1.0.0", details={"outreach_id": outreach_id, "reason": "recipient replied"},
            ))
        return record, created

    async def cancel_campaign(self, campaign_id: str) -> int:
        count = await self.outreach_store.set_campaign_cancelled(campaign_id)
        if count and self.event_recorder is not None:
            await self.event_recorder.record(AgentEvent(
                event_type=AgentEventType.FOLLOW_UP_CANCELLED,
                execution_id=f"campaign-cancel-{hashlib.sha256(campaign_id.encode()).hexdigest()[:12]}",
                workflow_id=campaign_id, workflow_step_id=None,
                agent_name="outreach_event_service", agent_version="1.0.0",
                details={"campaign_id": campaign_id, "cancelled_outreaches": count},
            ))
        return count

    async def record_provider_event(self, outreach_id: str, event_type: OutreachEventType,
                                    *, provider: str, provider_event_type: str,
                                    metadata: dict[str, Any] | None = None):
        if event_type not in {OutreachEventType.DELIVERED, OutreachEventType.BOUNCED}:
            raise ValueError("only delivery or bounce events are accepted by this method")
        event = await self.outreach_store.add_event(OutreachEvent(
            outreach_id=outreach_id, event_type=event_type, provider=provider,
            provider_event_type=provider_event_type, metadata=metadata or {},
        ))
        if self.event_recorder is not None:
            audit_type = (AgentEventType.OUTREACH_DELIVERED if event_type == OutreachEventType.DELIVERED
                          else AgentEventType.OUTREACH_BOUNCED)
            await self.event_recorder.record(AgentEvent(
                event_type=audit_type, execution_id=f"provider-event-{event.event_id}",
                workflow_id=None, workflow_step_id=None,
                agent_name="outreach_event_service", agent_version="1.0.0",
                details={"outreach_id": outreach_id, "provider": provider,
                         "provider_event_type": provider_event_type},
            ))
        return event
