"""Approved-draft execution through an injected email-provider abstraction."""

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.events import AgentEventType
from app.agents.exceptions import RetryableFailure
from app.agents.models import AgentInput
from app.agents.retry import RetryPolicy
from app.outreach.provider import EmailProvider, MockEmailProvider
from app.outreach.services import InMemoryOptOutService, InMemoryOutreachStore, InMemoryRateLimiter
from app.schemas.email import ApprovalActionType, EmailDraft, EmailDraftStatus
from app.schemas.outreach import (
    OutreachErrorCode, OutreachEvent, OutreachEventType, OutreachRequest,
    OutreachResult, OutreachState, OutreachStatus, ProviderMessage,
    ProviderResult, ProviderStatus,
)


EMAIL_PROVIDER_TOOL = "email_provider"
OPT_OUT_TOOL = "opt_out_service"
RATE_LIMITER_TOOL = "outreach_rate_limiter"
OUTREACH_STORE_TOOL = "outreach_event_store"
_EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


class OutreachAgent(BaseAgent):
    agent_name = "outreach"
    agent_version = "2.0.0"
    description = "Executes only approved drafts using an injected transport; default transport is mock-only."
    allowed_tools = frozenset({EMAIL_PROVIDER_TOOL, OPT_OUT_TOOL, RATE_LIMITER_TOOL, OUTREACH_STORE_TOOL})

    def __init__(self, *, provider: EmailProvider | None = None,
                 demo_provider: EmailProvider | None = None,
                 opt_out_service: InMemoryOptOutService | None = None,
                 rate_limiter: InMemoryRateLimiter | None = None,
                 outreach_store: InMemoryOutreachStore | None = None,
                 retry_policy: RetryPolicy | None = None, **kwargs: Any) -> None:
        super().__init__(retry_policy=retry_policy, **kwargs)
        self.provider = provider or MockEmailProvider()
        self.demo_provider = demo_provider or self.provider
        self.opt_out_service = opt_out_service or InMemoryOptOutService()
        self.rate_limiter = rate_limiter or InMemoryRateLimiter()
        self.outreach_store = outreach_store or InMemoryOutreachStore()

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> OutreachResult:
        request = agent_input.payload
        if not isinstance(request, OutreachRequest):
            request = OutreachRequest.model_validate(request)
        draft = request.draft
        provider = self.demo_provider if context.metadata.get("demo_mode", True) else self.provider
        self.require_tool(OUTREACH_STORE_TOOL, context)
        idem_key = _send_key(draft, request.campaign_id)
        outreach_id = "outreach-" + hashlib.sha256(idem_key.encode()).hexdigest()[:20]
        await self._emit(outreach_id, OutreachEventType.SEND_REQUESTED,
                         AgentEventType.OUTREACH_REQUESTED, context,
                         {"draft_id": draft.draft_id, "campaign_id": request.campaign_id})

        if (draft.status != EmailDraftStatus.APPROVED or not draft.approved_subject
                or not draft.approved_body or not _has_human_approval(draft)):
            return await self._blocked(outreach_id, idem_key, OutreachStatus.BLOCKED,
                                       OutreachErrorCode.NOT_APPROVED,
                                       "Draft is not approved with approved content.", context)
        recipient = draft.recipient
        if not _recipient_is_authorized(recipient):
            return await self._blocked(outreach_id, idem_key, OutreachStatus.BLOCKED,
                                       OutreachErrorCode.INVALID_RECIPIENT,
                                       "Draft does not contain a valid recipient with public/authorized contact provenance.", context)

        self.require_tool(OPT_OUT_TOOL, context)
        self.require_tool(RATE_LIMITER_TOOL, context)
        self.require_tool(EMAIL_PROVIDER_TOOL, context)
        approved_version = max(1, len(draft.versions))
        initial_state = OutreachState(
            outreach_id=outreach_id, idempotency_key=idem_key, draft_id=draft.draft_id,
            workflow_id=context.workflow_id,
            approved_version=approved_version, recipient=recipient.email,
            campaign_id=request.campaign_id, status=OutreachStatus.IN_PROGRESS,
        )
        reserve_state, state, existing_result = await self.outreach_store.reserve(initial_state)
        if reserve_state == "sent" and existing_result is not None:
            event = await self._emit(outreach_id, OutreachEventType.DUPLICATE_SEND,
                                     AgentEventType.OUTREACH_DUPLICATE_PREVENTED, context,
                                     {"draft_id": draft.draft_id, "send_key": idem_key[:16]})
            return OutreachResult(
                outreach_id=outreach_id, idempotency_key=idem_key,
                status=OutreachStatus.DUPLICATE_SEND,
                error_code=OutreachErrorCode.DUPLICATE_SEND,
                explanation="This approved message was already sent; the prior provider result is returned.",
                provider_result=existing_result, events=[*await self.outreach_store.events_for(outreach_id)],
            )
        if reserve_state == "in_progress":
            return await self._blocked(outreach_id, idem_key, OutreachStatus.IN_PROGRESS,
                                       OutreachErrorCode.SEND_IN_PROGRESS,
                                       "An identical send is already in progress.", context)

        if await self.opt_out_service.has_opted_out(recipient.email):
            await self.outreach_store.release(idem_key)
            await self.outreach_store.mark_opted_out(recipient.email, datetime.now(timezone.utc))
            return await self._blocked(outreach_id, idem_key, OutreachStatus.OPTED_OUT,
                                       OutreachErrorCode.OPTED_OUT,
                                       "Recipient opted out; no provider call was made.", context,
                                       event_type=OutreachEventType.OPTED_OUT)

        allowed, rate_reason = await self.rate_limiter.acquire(recipient.email)
        if not allowed:
            await self.outreach_store.release(idem_key)
            return await self._blocked(outreach_id, idem_key, OutreachStatus.RATE_LIMITED,
                                       OutreachErrorCode.RATE_LIMITED,
                                       rate_reason or "Rate limit exceeded.", context,
                                       event_type=OutreachEventType.RATE_LIMITED)

        message = ProviderMessage(
            draft_id=draft.draft_id, approved_version=approved_version,
            idempotency_key=idem_key, recipient=recipient.email,
            subject=draft.approved_subject, body=draft.approved_body,
        )
        last_provider_result: ProviderResult | None = None
        attempt_count = 0

        async def send_attempt() -> ProviderResult:
            nonlocal last_provider_result, attempt_count
            attempt_count += 1
            await self._emit(outreach_id, OutreachEventType.SEND_REQUESTED,
                             AgentEventType.OUTREACH_SEND_ATTEMPTED, context,
                             {"attempt": attempt_count})
            try:
                result = await provider.send(message)
            except Exception as error:
                result = ProviderResult(
                    status=ProviderStatus.FAILED, recipient=recipient.email,
                    subject=draft.approved_subject,
                    error=f"Provider raised {type(error).__name__}.",
                    provider_name=getattr(provider, "provider_name", "abstract"),
                )
            last_provider_result = result
            await self._emit(outreach_id, _provider_event(result.status),
                             _provider_audit(result.status), context,
                             {"provider": result.provider_name, "attempt": attempt_count,
                              "provider_message_id": result.provider_message_id})
            if result.status == ProviderStatus.TEMPORARY_FAILURE:
                raise RetryableFailure(result.error or "Temporary provider failure")
            return result

        async def record_retry(retry_count: int, delay_seconds: float) -> None:
            await self._record(AgentEventType.AGENT_RETRY, context,
                               {"retry_count": retry_count, "delay_seconds": delay_seconds,
                                "outreach_id": outreach_id})

        try:
            provider_result, provider_retries = await self.retry_policy.run(send_attempt, on_retry=record_retry)
        except RetryableFailure:
            provider_result = last_provider_result
            provider_retries = max(0, attempt_count - 1)
        if provider_result is None:
            await self.outreach_store.release(idem_key)
            return await self._blocked(outreach_id, idem_key, OutreachStatus.FAILED,
                                       OutreachErrorCode.PROVIDER_FAILURE,
                                       "Provider did not return a result.", context)

        await self.outreach_store.finish_attempt(outreach_id, provider_result)
        events = await self.outreach_store.events_for(outreach_id)
        if provider_result.status == ProviderStatus.SENT:
            status, code, explanation = OutreachStatus.SENT, None, "Mock/provider abstraction reported successful delivery submission."
        elif provider_result.status == ProviderStatus.TEMPORARY_FAILURE:
            status, code, explanation = OutreachStatus.TEMPORARY_FAILURE, OutreachErrorCode.TEMPORARY_PROVIDER_FAILURE, provider_result.error or "Temporary provider failure after retries."
        elif provider_result.status == ProviderStatus.PERMANENT_FAILURE:
            status, code, explanation = OutreachStatus.PERMANENT_FAILURE, OutreachErrorCode.PERMANENT_PROVIDER_FAILURE, provider_result.error or "Permanent provider failure."
        else:
            status, code, explanation = OutreachStatus.FAILED, OutreachErrorCode.PROVIDER_FAILURE, provider_result.error or "Provider failed to send."
        return OutreachResult(outreach_id=outreach_id, idempotency_key=idem_key,
                              status=status, error_code=code, explanation=explanation,
                              provider_result=provider_result, provider_attempts=attempt_count,
                              retry_count=provider_retries,
                              events=events)

    async def _blocked(self, outreach_id: str, key: str, status: OutreachStatus,
                       code: OutreachErrorCode, explanation: str, context: AgentContext,
                       *, event_type: OutreachEventType = OutreachEventType.BLOCKED) -> OutreachResult:
        await self._emit(outreach_id, event_type, AgentEventType.OUTREACH_BLOCKED,
                         context, {"reason_code": code.value})
        return OutreachResult(outreach_id=outreach_id, idempotency_key=key,
                              status=status, error_code=code, explanation=explanation,
                              events=await self.outreach_store.events_for(outreach_id))

    async def _emit(self, outreach_id: str, event_type: OutreachEventType,
                    audit_type: AgentEventType, context: AgentContext,
                    details: dict[str, Any]) -> OutreachEvent:
        event = await self.outreach_store.add_event(OutreachEvent(
            outreach_id=outreach_id, event_type=event_type, metadata=details,
        ))
        await self._record(audit_type, context, {"outreach_id": outreach_id, **details})
        return event


def _send_key(draft: EmailDraft, campaign_id: str | None) -> str:
    version = max(1, len(draft.versions))
    material = json.dumps({
        "draft_id": draft.draft_id, "approved_version": version,
        "recipient": draft.recipient.email.lower() if draft.recipient else None,
        "campaign_id": campaign_id,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()


def _recipient_is_authorized(recipient) -> bool:
    return bool(
        recipient and _EMAIL_PATTERN.fullmatch(recipient.email)
        and recipient.evidence_id and recipient.source_url
        and recipient.source_access in {"public", "authorized", "public_or_authorized"}
    )


def _has_human_approval(draft: EmailDraft) -> bool:
    if not draft.approvals:
        return False
    last_action = draft.approvals[-1]
    return (
        last_action.action == ApprovalActionType.APPROVE
        and last_action.actor_id.strip().lower() not in {"email-agent", "outreach-agent", "system", "agent"}
        and last_action.version_number == max(1, len(draft.versions))
        and draft.approved_subject == draft.subject
        and draft.approved_body == draft.body
    )


def _provider_event(status: ProviderStatus) -> OutreachEventType:
    return {
        ProviderStatus.SENT: OutreachEventType.SENT,
        ProviderStatus.FAILED: OutreachEventType.FAILED,
        ProviderStatus.TEMPORARY_FAILURE: OutreachEventType.TEMPORARY_FAILURE,
        ProviderStatus.PERMANENT_FAILURE: OutreachEventType.PERMANENT_FAILURE,
    }[status]


def _provider_audit(status: ProviderStatus) -> AgentEventType:
    return (AgentEventType.OUTREACH_SEND_SUCCEEDED if status == ProviderStatus.SENT
            else AgentEventType.OUTREACH_SEND_FAILED)
