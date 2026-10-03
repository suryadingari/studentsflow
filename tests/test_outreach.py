import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.context import AgentContext
from app.agents.events import AgentEventType, InMemoryEventRecorder
from app.agents.exceptions import ToolPermissionDenied
from app.agents.implementations.follow_up import FollowUpAgent, OutreachEventService
from app.agents.implementations.outreach import (
    EMAIL_PROVIDER_TOOL, OPT_OUT_TOOL, OUTREACH_STORE_TOOL, RATE_LIMITER_TOOL,
    OutreachAgent,
)
from app.agents.models import AgentInput
from app.agents.retry import RetryPolicy
from app.outreach.provider import MockEmailProvider
from app.outreach.services import (
    InMemoryOptOutService, InMemoryOutreachStore, InMemoryRateLimiter,
)
from app.schemas.email import (ApprovalActionType, ApprovalRecord, EmailDraft,
                               EmailDraftStatus, EmailDraftVersion, EmailRecipient)
from app.schemas.outreach import (
    FollowUpPolicy, FollowUpRequest, FollowUpStatus, OptOutRequest,
    OutreachEventType, OutreachRequest, OutreachStatus, ProviderStatus,
)


def run(coro):
    return asyncio.run(coro)


SEND_TOOLS = frozenset({EMAIL_PROVIDER_TOOL, OPT_OUT_TOOL, RATE_LIMITER_TOOL, OUTREACH_STORE_TOOL})
FOLLOW_TOOLS = frozenset({OUTREACH_STORE_TOOL, OPT_OUT_TOOL})
FIXED_NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)


def approved_draft(*, status=EmailDraftStatus.APPROVED, recipient=True, draft_id="draft-1"):
    email_recipient = EmailRecipient(
        email="student@example.org", evidence_id="contact-evidence",
        source_url="https://public.example/profile", source_access="public",
    ) if recipient else None
    subject, body = "A research question", "Hello,\n\nWould you discuss your project?"
    return EmailDraft(
        draft_id=draft_id, request_fingerprint=f"fingerprint-{draft_id}",
        candidate_id="candidate-1", recipient=email_recipient,
        generated_subject=subject, generated_body=body,
        subject=subject, body=body,
        approved_subject=subject if status == EmailDraftStatus.APPROVED else None,
        approved_body=body if status == EmailDraftStatus.APPROVED else None,
        status=status,
        versions=[EmailDraftVersion(version_number=1, subject=subject, body=body,
                                    authored_by="email-agent")],
        approvals=([ApprovalRecord(action_id="human-approval", action=ApprovalActionType.APPROVE,
                                   actor_id="reviewer-1", version_number=1)]
                   if status == EmailDraftStatus.APPROVED else []),
    )


def send(draft, *, provider=None, store=None, opt_out=None, rate_limiter=None,
         recorder=None, campaign_id="campaign-1", retry_policy=None, context=None):
    agent = OutreachAgent(provider=provider, outreach_store=store,
                          opt_out_service=opt_out, rate_limiter=rate_limiter,
                          event_recorder=recorder, retry_policy=retry_policy)
    result = run(agent.execute(
        AgentInput[OutreachRequest](payload=OutreachRequest(draft=draft, campaign_id=campaign_id)),
        context or AgentContext(permitted_tools=SEND_TOOLS),
    ))
    return agent, result


def run_followup(outreach_id, store, opt_out, *, now, policy=None, recorder=None):
    agent = FollowUpAgent(outreach_store=store, opt_out_service=opt_out,
                          policy=policy or FollowUpPolicy(), event_recorder=recorder)
    result = run(agent.execute(
        AgentInput[FollowUpRequest](payload=FollowUpRequest(outreach_id=outreach_id, now=now)),
        AgentContext(permitted_tools=FOLLOW_TOOLS),
    ))
    assert result.success
    return result.result.plan


def test_approved_draft_uses_mock_provider_once():
    provider = MockEmailProvider(timestamp=FIXED_NOW)
    _, result = send(approved_draft(), provider=provider)
    assert result.success and result.result.status == OutreachStatus.SENT
    assert len(provider.calls) == 1
    assert provider.calls[0].recipient == "student@example.org"
    assert provider.sends_real_email is False


@pytest.mark.parametrize("status", [
    EmailDraftStatus.DRAFT, EmailDraftStatus.PENDING_REVIEW,
    EmailDraftStatus.EDITED, EmailDraftStatus.REJECTED, EmailDraftStatus.BLOCKED,
])
def test_non_approved_status_never_calls_provider(status):
    provider = MockEmailProvider()
    _, result = send(approved_draft(status=status), provider=provider)
    assert not result.success
    assert result.result.status == OutreachStatus.BLOCKED
    assert result.result.error_code.value == "not_approved"
    assert provider.calls == []


def test_approved_status_without_human_approval_record_is_blocked():
    provider = MockEmailProvider()
    draft = approved_draft()
    draft.approvals.clear()
    _, result = send(draft, provider=provider)
    assert result.result.error_code.value == "not_approved"
    assert provider.calls == []


def test_missing_or_unauthorized_recipient_never_calls_provider():
    provider = MockEmailProvider()
    _, missing = send(approved_draft(recipient=False), provider=provider)
    assert missing.result.error_code.value == "invalid_recipient"
    invalid = approved_draft().model_copy(deep=True)
    invalid.recipient.source_access = "restricted"
    _, unauthorized = send(invalid, provider=provider)
    assert unauthorized.result.error_code.value == "invalid_recipient"
    assert provider.calls == []


def test_opted_out_recipient_is_blocked_before_provider_call():
    store = InMemoryOutreachStore()
    provider = MockEmailProvider()
    opt_out = InMemoryOptOutService(outreach_store=store)
    run(opt_out.record_opt_out(OptOutRequest(recipient="STUDENT@example.org", source="test")))
    _, result = send(approved_draft(), provider=provider, store=store, opt_out=opt_out)
    assert result.result.status == OutreachStatus.OPTED_OUT
    assert result.result.error_code.value == "opted_out"
    assert provider.calls == []


def test_successful_send_is_idempotent_across_repeated_processing():
    provider = MockEmailProvider(timestamp=FIXED_NOW)
    store = InMemoryOutreachStore()
    draft = approved_draft()
    _, first = send(draft, provider=provider, store=store)
    _, second = send(draft, provider=provider, store=store)
    assert first.result.status == OutreachStatus.SENT
    assert second.result.status == OutreachStatus.DUPLICATE_SEND
    assert second.result.provider_result.provider_message_id == first.result.provider_result.provider_message_id
    assert len(provider.calls) == 1


def test_failed_send_returns_structured_provider_failure():
    provider = MockEmailProvider([ProviderStatus.FAILED], timestamp=FIXED_NOW)
    _, result = send(approved_draft(), provider=provider)
    assert not result.success
    assert result.result.status == OutreachStatus.FAILED
    assert result.result.error_code.value == "provider_failure"


def test_temporary_provider_failure_uses_existing_retry_policy():
    provider = MockEmailProvider([ProviderStatus.TEMPORARY_FAILURE, ProviderStatus.SENT], timestamp=FIXED_NOW)
    _, result = send(approved_draft(), provider=provider,
                     retry_policy=RetryPolicy(max_retries=1))
    assert result.success and result.result.status == OutreachStatus.SENT
    assert result.result.provider_attempts == 2
    assert result.result.retry_count == 1
    assert result.execution.retry_count == 1
    assert len(provider.calls) == 2


def test_rate_limit_prevents_provider_call():
    provider = MockEmailProvider(timestamp=FIXED_NOW)
    limiter = InMemoryRateLimiter(max_messages=1, window_seconds=3600, clock=lambda: FIXED_NOW)
    store = InMemoryOutreachStore()
    _, first = send(approved_draft(draft_id="draft-a"), provider=provider,
                    rate_limiter=limiter, store=store)
    _, second = send(approved_draft(draft_id="draft-b"), provider=provider,
                     rate_limiter=limiter, store=store)
    assert first.result.status == OutreachStatus.SENT
    assert second.result.status == OutreachStatus.RATE_LIMITED
    assert second.result.error_code.value == "rate_limited"
    assert len(provider.calls) == 1


def test_successful_send_records_sent_event_and_audit_events():
    provider, store, recorder = MockEmailProvider(timestamp=FIXED_NOW), InMemoryOutreachStore(), InMemoryEventRecorder()
    _, result = send(approved_draft(), provider=provider, store=store, recorder=recorder)
    assert OutreachEventType.SEND_REQUESTED in [item.event_type for item in result.result.events]
    assert OutreachEventType.SENT in [item.event_type for item in result.result.events]
    audit = [item.event_type for item in recorder.events]
    assert AgentEventType.OUTREACH_REQUESTED in audit
    assert AgentEventType.OUTREACH_SEND_ATTEMPTED in audit
    assert AgentEventType.OUTREACH_SEND_SUCCEEDED in audit


def test_reply_event_stops_and_cancels_follow_up():
    store, provider = InMemoryOutreachStore(), MockEmailProvider(timestamp=FIXED_NOW)
    opt_out = InMemoryOptOutService(outreach_store=store)
    _, sent = send(approved_draft(), provider=provider, store=store, opt_out=opt_out)
    outreach_id = sent.result.outreach_id
    due = run_followup(outreach_id, store, opt_out,
                       now=FIXED_NOW + timedelta(days=8))
    assert due.status == FollowUpStatus.DUE
    events = OutreachEventService(store)
    reply, created = run(events.record_reply(outreach_id, FIXED_NOW + timedelta(days=9), {"channel": "mock"}))
    assert created and reply.outreach_id == outreach_id
    stopped = run_followup(outreach_id, store, opt_out,
                           now=FIXED_NOW + timedelta(days=10))
    assert stopped.status == FollowUpStatus.CANCELLED
    assert "replied" in stopped.reason


def test_opt_out_after_send_stops_future_follow_up():
    store, provider = InMemoryOutreachStore(), MockEmailProvider(timestamp=FIXED_NOW)
    recorder = InMemoryEventRecorder()
    opt_out = InMemoryOptOutService(outreach_store=store, event_recorder=recorder)
    _, sent = send(approved_draft(), provider=provider, store=store, opt_out=opt_out)
    due = run_followup(sent.result.outreach_id, store, opt_out,
                       now=FIXED_NOW + timedelta(days=8))
    assert due.status == FollowUpStatus.DUE
    run(opt_out.record_opt_out(OptOutRequest(recipient="student@example.org", source="unsubscribe request")))
    persisted = run(store.get_follow_up(sent.result.outreach_id, due.follow_up_number))
    assert persisted.status == FollowUpStatus.CANCELLED
    plan = run_followup(sent.result.outreach_id, store, opt_out,
                        now=FIXED_NOW + timedelta(days=10))
    assert plan.status == FollowUpStatus.CANCELLED
    assert "opted out" in plan.reason
    assert AgentEventType.OUTREACH_OPT_OUT_RECORDED in [event.event_type for event in recorder.events]
    assert AgentEventType.FOLLOW_UP_CANCELLED in [event.event_type for event in recorder.events]


def test_permanent_provider_failure_does_not_schedule_follow_up():
    store, provider = InMemoryOutreachStore(), MockEmailProvider([ProviderStatus.PERMANENT_FAILURE])
    opt_out = InMemoryOptOutService(outreach_store=store)
    _, failed = send(approved_draft(), provider=provider, store=store, opt_out=opt_out)
    plan = run_followup(failed.result.outreach_id, store, opt_out, now=FIXED_NOW + timedelta(days=10))
    assert plan.status == FollowUpStatus.CANCELLED
    assert "permanently" in plan.reason


def test_follow_up_is_due_after_configured_delay_and_is_idempotent():
    store, provider = InMemoryOutreachStore(), MockEmailProvider(timestamp=FIXED_NOW)
    opt_out = InMemoryOptOutService(outreach_store=store)
    _, sent = send(approved_draft(), provider=provider, store=store, opt_out=opt_out)
    before = run_followup(sent.result.outreach_id, store, opt_out,
                          now=FIXED_NOW + timedelta(hours=23), policy=FollowUpPolicy(delay_hours=24))
    due = run_followup(sent.result.outreach_id, store, opt_out,
                       now=FIXED_NOW + timedelta(hours=24), policy=FollowUpPolicy(delay_hours=24))
    repeated = run_followup(sent.result.outreach_id, store, opt_out,
                            now=FIXED_NOW + timedelta(hours=25), policy=FollowUpPolicy(delay_hours=24))
    assert before.status == FollowUpStatus.NOT_DUE
    assert due.status == FollowUpStatus.DUE
    assert repeated.plan_id == due.plan_id
    assert len(provider.calls) == 1  # FollowUpAgent never sends.


def test_follow_up_limit_reached_creates_no_due_plan():
    store, provider = InMemoryOutreachStore(), MockEmailProvider(timestamp=FIXED_NOW)
    opt_out = InMemoryOptOutService(outreach_store=store)
    _, sent = send(approved_draft(), provider=provider, store=store, opt_out=opt_out)
    plan = run_followup(sent.result.outreach_id, store, opt_out,
                       now=FIXED_NOW + timedelta(days=8),
                       policy=FollowUpPolicy(delay_hours=24, max_follow_ups=0))
    assert plan.status == FollowUpStatus.CANCELLED
    assert "limit" in plan.reason
    assert len(provider.calls) == 1


def test_cancelled_campaign_cancels_existing_follow_up():
    store, provider = InMemoryOutreachStore(), MockEmailProvider(timestamp=FIXED_NOW)
    opt_out, recorder = InMemoryOptOutService(outreach_store=store), InMemoryEventRecorder()
    _, sent = send(approved_draft(), provider=provider, store=store, opt_out=opt_out)
    due = run_followup(sent.result.outreach_id, store, opt_out,
                       now=FIXED_NOW + timedelta(days=8))
    event_service = OutreachEventService(store, event_recorder=recorder)
    assert run(event_service.cancel_campaign("campaign-1")) == 1
    persisted = run(store.get_follow_up(sent.result.outreach_id, due.follow_up_number))
    assert persisted.status == FollowUpStatus.CANCELLED
    assert AgentEventType.FOLLOW_UP_CANCELLED in [event.event_type for event in recorder.events]


def test_delivery_and_bounce_events_are_provider_neutral_records():
    store = InMemoryOutreachStore()
    provider = MockEmailProvider(timestamp=FIXED_NOW)
    opt_out = InMemoryOptOutService(outreach_store=store)
    _, sent = send(approved_draft(), provider=provider, store=store, opt_out=opt_out)
    recorder = InMemoryEventRecorder()
    service = OutreachEventService(store, event_recorder=recorder)
    delivered = run(service.record_provider_event(
        sent.result.outreach_id, OutreachEventType.DELIVERED,
        provider="mock", provider_event_type="accepted", metadata={"code": 250},
    ))
    assert delivered.provider_event_type == "accepted"
    assert AgentEventType.OUTREACH_DELIVERED in [event.event_type for event in recorder.events]


def test_follow_up_agent_cannot_use_email_provider():
    store, provider = InMemoryOutreachStore(), MockEmailProvider(timestamp=FIXED_NOW)
    opt_out = InMemoryOptOutService(outreach_store=store)
    _, sent = send(approved_draft(), provider=provider, store=store, opt_out=opt_out)
    agent = FollowUpAgent(outreach_store=store, opt_out_service=opt_out)
    with pytest.raises(ToolPermissionDenied):
        agent.require_tool(EMAIL_PROVIDER_TOOL, AgentContext(permitted_tools=SEND_TOOLS))
    assert len(provider.calls) == 1
    assert EMAIL_PROVIDER_TOOL not in agent.allowed_tools


def test_duplicate_opt_out_is_idempotent():
    recorder = InMemoryEventRecorder()
    service = InMemoryOptOutService(event_recorder=recorder)
    request = OptOutRequest(recipient="Student@example.org", source="mock")
    first, created_first = run(service.record_opt_out(request))
    second, created_second = run(service.record_opt_out(request))
    assert created_first and not created_second
    assert first.recipient == second.recipient == "student@example.org"
    assert len([e for e in recorder.events if e.event_type == AgentEventType.OUTREACH_OPT_OUT_RECORDED]) == 1


def test_outreach_agent_requires_explicit_granted_tool_permissions():
    provider = MockEmailProvider()
    _, result = send(approved_draft(), provider=provider,
                     context=AgentContext(permitted_tools=frozenset()))
    assert not result.success
    assert provider.calls == []
