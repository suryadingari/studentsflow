"""Coordinates the existing versioned agents; business rules remain in those agents."""

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.agents.context import AgentContext
from app.agents.events import AgentEvent, AgentEventType, EventRecorder, InMemoryEventRecorder
from app.agents.exceptions import HopLimitExceeded
from app.agents.models import AgentInput, AgentOutput
from app.agents.registry import AgentRegistry
from app.agents.implementations.email import EmailApprovalService
from app.crawling.models import SourceDiscoveryOutput, SourceDiscoveryRequest, StudentCrawlerRequest
from app.orchestration.transitions import validate_transition
from app.schemas.canonical import CanonicalStudent, DeduplicationRequest, EnrichmentRequest
from app.schemas.email import (ApprovalAction, ApprovalActionType, EmailApprovalRequest, EmailDraft,
                               EmailDraftRequest, EmailDraftStatus)
from app.schemas.matching import MatchingRequest
from app.schemas.outreach import FollowUpRequest, FollowUpResult, OutreachRequest, OutreachResult
from app.schemas.student import ExtractedStudentInformation, StudentValidationResult, ValidationRequest
from app.schemas.workflow import (WorkflowMetrics, WorkflowRequest, WorkflowResult, WorkflowStatus,
                                  WorkflowStep, WorkflowStepStatus, WorkflowStepType, now_utc)
from app.db.persistence import PostgresWorkflowRepository


_SERVICE_AGENT = "workflow_orchestrator"
_SERVICE_VERSION = "1.0.0"


@dataclass
class _Runtime:
    result: WorkflowResult
    request: WorkflowRequest
    outputs: dict[str, Any] = field(default_factory=dict)
    validated: list[StudentValidationResult] = field(default_factory=list)
    canonical_students: list[CanonicalStudent] = field(default_factory=list)
    matches: dict[str, Any] = field(default_factory=dict)
    drafts: dict[str, EmailDraft] = field(default_factory=dict)
    draft_candidates: dict[str, tuple[CanonicalStudent, Any]] = field(default_factory=dict)
    outreach_results: list[OutreachResult] = field(default_factory=list)
    follow_up_results: list[FollowUpResult] = field(default_factory=list)
    hop_count: int = 0
    step_keys: dict[str, WorkflowStep] = field(default_factory=dict)


class InMemoryWorkflowStore:
    """Process-local workflow state; intentionally not durable in Step 10."""

    def __init__(self) -> None:
        self._runs: dict[str, _Runtime] = {}

    def add(self, runtime: _Runtime) -> None:
        self._runs[runtime.result.workflow_id] = runtime

    def get_runtime(self, workflow_id: str) -> _Runtime:
        try:
            return self._runs[workflow_id]
        except KeyError as error:
            raise KeyError(f"Unknown workflow: {workflow_id}") from error

    def get(self, workflow_id: str) -> WorkflowResult:
        return self.get_runtime(workflow_id).result.model_copy(deep=True)


class WorkflowOrchestrator:
    """Runs the canonical sequence and pauses at the existing human approval service."""

    def __init__(self, *, registry: AgentRegistry,
                 approval_service: EmailApprovalService,
                 event_recorder: EventRecorder | None = None,
                 store: InMemoryWorkflowStore | None = None,
                 persistence: PostgresWorkflowRepository | None = None) -> None:
        self.registry = registry
        self.approval_service = approval_service
        self.event_recorder = event_recorder or InMemoryEventRecorder()
        self.store = store or InMemoryWorkflowStore()
        self.persistence = persistence

    async def start(self, request: WorkflowRequest) -> WorkflowResult:
        workflow_id = str(uuid4())
        requirement_id = request.requirement.requirement_id or str(uuid4())
        request = request.model_copy(deep=True)
        request.requirement.requirement_id = requirement_id
        now = now_utc()
        result = WorkflowResult(workflow_id=workflow_id, requirement_id=requirement_id,
                                status=WorkflowStatus.CREATED, created_at=now, updated_at=now)
        runtime = _Runtime(result=result, request=request)
        self.store.add(runtime)
        await self._persist(runtime)
        await self._audit(runtime, AgentEventType.WORKFLOW_CREATED, details={"requirement_id": requirement_id})
        self._transition(runtime, WorkflowStatus.RUNNING)
        await self._persist(runtime)
        await self._audit(runtime, AgentEventType.WORKFLOW_STARTED)
        try:
            await self._run_until_approval(runtime)
        except HopLimitExceeded as error:
            await self._critical_failure(runtime, "hop_limit_exceeded", str(error))
        except Exception as error:
            await self._critical_failure(runtime, type(error).__name__, str(error))
        return self.store.get(workflow_id)

    async def get(self, workflow_id: str) -> WorkflowResult:
        if self.persistence is not None:
            runtime = await self._load_runtime(workflow_id)
            return runtime.result.model_copy(deep=True)
        try:
            return self.store.get(workflow_id)
        except KeyError:
            runtime = await self._load_runtime(workflow_id)
            return runtime.result.model_copy(deep=True)

    async def pause_for_approval(self, workflow_id: str) -> WorkflowResult:
        runtime = await self._get_runtime(workflow_id)
        if runtime.result.status != WorkflowStatus.WAITING_FOR_APPROVAL:
            raise ValueError("Workflow is not waiting for human approval")
        return runtime.result.model_copy(deep=True)

    async def resume_after_approval(self, workflow_id: str, draft_id: str,
                                    action: ApprovalAction) -> WorkflowResult:
        runtime = await self._get_runtime(workflow_id)
        if runtime.result.status != WorkflowStatus.WAITING_FOR_APPROVAL:
            raise ValueError("Workflow is not waiting for human approval")
        if draft_id not in runtime.result.pending_draft_ids:
            raise ValueError("Draft is not pending approval in this workflow")
        draft = await self.approval_service.apply(
            EmailApprovalRequest(draft_id=draft_id, action=action),
            AgentContext(workflow_id=workflow_id, workflow_step_id=self._step_id_for(runtime, draft_id)),
        )
        runtime.drafts[draft_id] = draft
        runtime.result.drafts = [draft if item.draft_id == draft_id else item for item in runtime.result.drafts]
        await self._hydrate_draft_store(runtime)
        if action.action != ApprovalActionType.EDIT:
            runtime.result.pending_draft_ids.remove(draft_id)
        await self._audit(runtime, AgentEventType.WORKFLOW_APPROVAL_RECEIVED,
                          step_id=self._step_id_for(runtime, draft_id),
                          details={"draft_id": draft_id, "action": action.action.value,
                                   "actor_id": action.actor_id})
        if action.action == ApprovalActionType.EDIT:
            await self._persist(runtime)
            return self.store.get(workflow_id)
        if runtime.result.pending_draft_ids:
            await self._persist(runtime)
            return self.store.get(workflow_id)
        self._set_approval_step(runtime, WorkflowStepStatus.SUCCEEDED)
        self._transition(runtime, WorkflowStatus.RUNNING)
        await self._persist(runtime)
        await self._audit(runtime, AgentEventType.WORKFLOW_RESUMED)
        try:
            if any(d.status in {EmailDraftStatus.APPROVED, EmailDraftStatus.EDITED}
                   for d in runtime.drafts.values()):
                await self._run_outreach_and_follow_up(runtime)
            else:
                self._skip_outreach_steps(runtime, "No email draft was approved by a human reviewer.")
            await self._complete(runtime)
        except HopLimitExceeded as error:
            await self._critical_failure(runtime, "hop_limit_exceeded", str(error))
        except Exception as error:
            await self._critical_failure(runtime, type(error).__name__, str(error))
        return self.store.get(workflow_id)

    async def cancel(self, workflow_id: str, reason: str) -> WorkflowResult:
        runtime = await self._get_runtime(workflow_id)
        if not reason.strip():
            raise ValueError("Cancellation reason must not be blank")
        self._transition(runtime, WorkflowStatus.CANCELLED)
        runtime.result.cancellation_reason = reason.strip()
        runtime.result.current_step = None
        runtime.result.pending_draft_ids.clear()
        for step in runtime.result.steps:
            if step.state in {WorkflowStepStatus.PENDING, WorkflowStepStatus.WAITING}:
                step.state = WorkflowStepStatus.SKIPPED
                step.ended_at = now_utc()
        await self._audit(runtime, AgentEventType.WORKFLOW_CANCELLED,
                          details={"reason": reason.strip()})
        await self._persist(runtime)
        return self.store.get(workflow_id)

    async def _run_until_approval(self, runtime: _Runtime) -> None:
        request = runtime.request
        discovered: SourceDiscoveryOutput = await self._invoke(
            runtime, WorkflowStepType.SOURCE_DISCOVERY, "source_discovery",
            SourceDiscoveryRequest(requirement=request.requirement.raw_text or request.requirement.model_dump_json(),
                                   candidates=request.sources, permitted_domains=request.permitted_domains),
            logical_item_id="sources")
        runtime.result.source_discovery = discovered.model_copy(deep=True)
        crawls = []
        for source in discovered.candidates:
            if runtime.result.status != WorkflowStatus.RUNNING:
                return
            try:
                output = await self._invoke(
                    runtime, WorkflowStepType.STUDENT_CRAWLING, "student_crawler",
                    StudentCrawlerRequest(source_url=source.url,
                                          permitted_domains=request.permitted_domains,
                                          configuration=request.crawl_configuration),
                    logical_item_id=source.url,
                    tool_grants=request.permitted_tools)
                crawls.extend(output.results)
            except _ItemFailure as error:
                runtime.result.errors.append(f"Student crawler failed for {source.url}: {error}")
        # Preserve crawl provenance and errors but avoid retaining whole page bodies;
        # extracted claim snippets remain attached to their evidence records.
        runtime.result.crawl_results = [item.model_copy(update={"raw_content": None}, deep=True)
                                        for item in crawls]
        extracted: list[ExtractedStudentInformation] = []
        for crawl in (item for item in crawls if item.success):
            try:
                extracted.append(await self._invoke(
                    runtime, WorkflowStepType.EXTRACTION, "extraction", crawl,
                    logical_item_id=crawl.requested_url))
            except _ItemFailure as error:
                runtime.result.errors.append(str(error))
        runtime.result.extracted_profiles = [item.model_copy(deep=True) for item in extracted]
        runtime.result.metrics.candidates_discovered = len(extracted)
        for item in extracted:
            try:
                runtime.validated.append(await self._invoke(
                    runtime, WorkflowStepType.VALIDATION, "validation",
                    ValidationRequest(extracted=item, current_year=request.current_year,
                                      research_complete=bool(crawls) and all(crawl.success for crawl in crawls)),
                    logical_item_id=str(item.source_references[0].source_url) if item.source_references else "unknown"))
            except _ItemFailure as error:
                runtime.result.errors.append(str(error))
        runtime.result.metrics.candidates_validated = len(runtime.validated)
        runtime.result.validated_profiles = [item.model_copy(deep=True) for item in runtime.validated]
        dedup = await self._invoke(runtime, WorkflowStepType.DEDUPLICATION, "deduplication",
                                   DeduplicationRequest(profiles=[v.profile for v in runtime.validated]),
                                   logical_item_id="validated-profiles")
        runtime.canonical_students = dedup.canonical_students
        runtime.result.deduplication_decisions = [item.model_copy(deep=True) for item in dedup.decisions]
        runtime.result.canonical_students = [item.model_copy(deep=True) for item in runtime.canonical_students]
        enriched: list[CanonicalStudent] = []
        for student in runtime.canonical_students:
            try:
                item = await self._invoke(
                    runtime, WorkflowStepType.ENRICHMENT, "enrichment",
                    EnrichmentRequest(canonical_student=student, additions=[]),
                    logical_item_id=student.canonical_id)
                enriched.append(item.canonical_student)
            except _ItemFailure as error:
                runtime.result.errors.append(str(error))
                enriched.append(student)
        runtime.canonical_students = enriched
        matching = await self._invoke(runtime, WorkflowStepType.MATCHING, "matching",
                                      MatchingRequest(requirement=request.requirement,
                                                      candidates=runtime.canonical_students),
                                      logical_item_id="all-candidates")
        runtime.matches = {match.canonical_student_id: match for match in matching.candidate_matches}
        runtime.result.candidate_matches = [item.model_copy(deep=True) for item in matching.candidate_matches]
        runtime.result.metrics.candidates_matched = sum(
            1 for match in matching.candidate_matches if match.status.value == "match")
        for student in runtime.canonical_students:
            match = runtime.matches.get(student.canonical_id)
            if match is None:
                continue
            try:
                recipient = None
                contact = student.profile.public_contact
                if contact is not None:
                    from app.schemas.email import EmailRecipient
                    evidence = next((e for e in student.profile.evidence
                                     if e.claim_type.value == "public_contact"
                                     and e.evidence_id in contact.evidence_ids), None)
                    if evidence is not None:
                        recipient = EmailRecipient(email=str(contact.value), evidence_id=evidence.evidence_id)
                draft_result = await self._invoke(
                    runtime, WorkflowStepType.EMAIL_DRAFTING, "email",
                    EmailDraftRequest(request_id=f"{runtime.result.workflow_id}:{student.canonical_id}",
                                      requirement=request.requirement, candidate=student, match=match,
                                      recipient=recipient, signature=request.signature),
                    logical_item_id=student.canonical_id)
                draft = draft_result.draft
                runtime.drafts[draft.draft_id] = draft
                runtime.draft_candidates[draft.draft_id] = (student, match)
                runtime.result.drafts.append(draft)
                runtime.result.metrics.emails_drafted += 1
                if draft.status == EmailDraftStatus.PENDING_REVIEW:
                    runtime.result.pending_draft_ids.append(draft.draft_id)
            except _ItemFailure as error:
                runtime.result.errors.append(str(error))
        self._make_approval_step(runtime)
        if runtime.result.pending_draft_ids:
            self._transition(runtime, WorkflowStatus.WAITING_FOR_APPROVAL)
            runtime.result.current_step = WorkflowStepType.HUMAN_APPROVAL
            # Persist the approval step before audit events reference its step_id.
            await self._persist(runtime)
            for draft_id in runtime.result.pending_draft_ids:
                await self._audit(runtime, AgentEventType.WORKFLOW_APPROVAL_REQUESTED,
                                  step_id=self._step_id_for(runtime, draft_id), details={"draft_id": draft_id})
            await self._audit(runtime, AgentEventType.WORKFLOW_PAUSED,
                              step_id=runtime.result.steps[-1].step_id)
            self._metrics(runtime)
            await self._persist(runtime)
            return
        self._set_approval_step(runtime, WorkflowStepStatus.SUCCEEDED)
        self._skip_outreach_steps(runtime, "No draft is awaiting human approval.")
        await self._complete(runtime)

    async def _run_outreach_and_follow_up(self, runtime: _Runtime) -> None:
        for draft_id, draft in list(runtime.drafts.items()):
            if runtime.result.status != WorkflowStatus.RUNNING:
                return
            if draft.status not in {EmailDraftStatus.APPROVED, EmailDraftStatus.EDITED}:
                continue
            outreach: OutreachResult = await self._invoke(
                runtime, WorkflowStepType.OUTREACH, "outreach",
                OutreachRequest(draft=draft, campaign_id=runtime.request.campaign_id),
                logical_item_id=draft_id, tool_grants=runtime.request.permitted_tools)
            runtime.outreach_results.append(outreach)
            runtime.result.outreach_results.append(outreach.model_copy(deep=True))
            runtime.result.outreach_draft_ids[outreach.outreach_id] = draft_id
            if outreach.status.value == "sent":
                runtime.result.metrics.emails_sent += 1
            if outreach.success:
                follow_up: FollowUpResult = await self._invoke(
                    runtime, WorkflowStepType.FOLLOW_UP, "follow_up",
                    FollowUpRequest(outreach_id=outreach.outreach_id),
                    logical_item_id=outreach.outreach_id,
                    tool_grants=runtime.request.permitted_tools)
                runtime.follow_up_results.append(follow_up)
                runtime.result.follow_up_results.append(follow_up.model_copy(deep=True))
                runtime.result.metrics.follow_ups_planned += int(follow_up.plan is not None)
        runtime.result.metrics.emails_approved = sum(
            draft.status in {EmailDraftStatus.APPROVED, EmailDraftStatus.EDITED}
            for draft in runtime.drafts.values())

    async def _invoke(self, runtime: _Runtime, step_type: WorkflowStepType,
                      agent_name: str, payload: Any, *, logical_item_id: str,
                      tool_grants: frozenset[str] = frozenset()) -> Any:
        try:
            agent = self.registry.get(agent_name)
        except KeyError as error:
            step = WorkflowStep(workflow_id=runtime.result.workflow_id, step_type=step_type,
                                logical_item_id=logical_item_id, agent_name=agent_name,
                                state=WorkflowStepStatus.FAILED, started_at=now_utc(), ended_at=now_utc(),
                                error_category="agent_unavailable", error=f"No registered agent: {agent_name}")
            runtime.result.steps.append(step)
            runtime.result.metrics.steps_total += 1
            runtime.result.metrics.steps_failed += 1
            runtime.result.errors.append(step.error or str(error))
            await self._audit(runtime, AgentEventType.WORKFLOW_STEP_FAILED, step_id=step.step_id,
                              agent_name=agent_name, agent_version="unavailable",
                              details={"error_category": step.error_category})
            raise _ItemFailure(step.error or str(error)) from error
        key = f"{runtime.result.workflow_id}:{step_type.value}:{logical_item_id}"
        cached = runtime.step_keys.get(key)
        if cached and cached.state == WorkflowStepStatus.SUCCEEDED:
            return runtime.outputs[key]
        step = WorkflowStep(workflow_id=runtime.result.workflow_id, step_type=step_type,
                            logical_item_id=logical_item_id, agent_name=agent.agent_name,
                            agent_version=agent.agent_version, state=WorkflowStepStatus.RUNNING,
                            started_at=now_utc(), input_reference=key,
                            input_metadata={"payload_type": type(payload).__name__})
        runtime.result.steps.append(step)
        runtime.step_keys[key] = step
        runtime.result.current_step = step_type
        runtime.result.agent_versions[agent_name] = agent.agent_version
        # The audit row has a foreign key to workflow_steps, so persist the new
        # step before recording its start event.
        await self._persist(runtime)
        await self._audit(runtime, AgentEventType.WORKFLOW_STEP_STARTED, step_id=step.step_id,
                          agent_name=agent_name, agent_version=agent.agent_version)
        grants = frozenset(set(tool_grants) & set(agent.allowed_tools))
        context = AgentContext(workflow_id=runtime.result.workflow_id,
                               workflow_step_id=step.step_id, current_hop=runtime.hop_count,
                               max_hops=runtime.request.max_hops,
                               idempotency_key=key, permitted_tools=grants,
                               metadata={"demo_mode": runtime.request.demo_mode})
        if (agent_name == "outreach"
                and getattr(getattr(agent, "provider", None), "sends_real_email", None) is not False):
            step.state = WorkflowStepStatus.FAILED
            step.error_category = "unsafe_provider"
            step.error = "This workflow phase only permits an explicitly non-sending email provider"
            step.ended_at = now_utc()
            runtime.result.metrics.steps_total += 1
            runtime.result.metrics.steps_failed += 1
            runtime.result.errors.append(step.error)
            await self._audit(runtime, AgentEventType.WORKFLOW_STEP_FAILED, step_id=step.step_id,
                              agent_name=agent_name, agent_version=agent.agent_version,
                              details={"error_category": step.error_category})
            await self._persist(runtime)
            raise PermissionError(step.error)
        output: AgentOutput = await agent.execute(AgentInput(payload=payload), context)
        runtime.hop_count = context.current_hop
        step.ended_at = now_utc()
        step.retry_count = output.execution.retry_count
        step.execution_id = output.execution.execution_id
        step.duration_seconds = output.execution.duration_seconds
        runtime.result.metrics.steps_total += 1
        runtime.result.metrics.steps_retried += int(output.execution.retry_count > 0)
        typed_outcome = agent_name in {"email", "outreach", "follow_up"}
        crawler_partial_success = (agent_name == "student_crawler" and output.result is not None
                                   and any(item.success for item in getattr(output.result, "results", [])))
        if (not output.success and not typed_outcome and not crawler_partial_success) or output.result is None:
            step.state = WorkflowStepStatus.FAILED
            step.error = "; ".join(output.errors) or output.execution.error or "Agent returned no result"
            step.error_category = "agent_failure"
            runtime.result.errors.append(f"{agent_name} failed for {logical_item_id}: {step.error}")
            runtime.result.metrics.steps_failed += 1
            await self._audit(runtime, AgentEventType.WORKFLOW_STEP_FAILED, step_id=step.step_id,
                              agent_name=agent_name, agent_version=agent.agent_version,
                              details={"error_category": step.error_category})
            if "Hop limit exceeded" in step.error:
                raise HopLimitExceeded(step.error)
            raise _ItemFailure(step.error)
        value = output.result
        if agent_name == "student_crawler":
            for crawl_result in getattr(value, "results", []):
                if not crawl_result.success and crawl_result.error is not None:
                    runtime.result.errors.append(
                        f"Crawl item failed for {crawl_result.requested_url}: {crawl_result.error.message}")
        structured_failure = getattr(value, "success", True) is False
        crawler_partial_success = (agent_name == "student_crawler" and any(
            item.success for item in getattr(value, "results", [])))
        if typed_outcome or crawler_partial_success:
            structured_failure = False
        if structured_failure:
            step.state = WorkflowStepStatus.BLOCKED if typed_outcome else WorkflowStepStatus.FAILED
            step.error_category = "structured_agent_failure"
            step.error = getattr(value, "error_summary", None) or "Agent returned a structured unsuccessful result"
            if step.state == WorkflowStepStatus.FAILED:
                runtime.result.metrics.steps_failed += 1
            await self._audit(runtime, AgentEventType.WORKFLOW_STEP_FAILED, step_id=step.step_id,
                              agent_name=agent_name, agent_version=agent.agent_version,
                              details={"error_category": step.error_category})
            if typed_outcome:
                step.output_reference = key
                step.output_metadata = {"result_type": type(value).__name__, "outcome": "blocked"}
                runtime.outputs[key] = value
                await self._persist(runtime)
                return value
            raise _ItemFailure(step.error)
        step.state = WorkflowStepStatus.SUCCEEDED
        step.output_reference = key
        step.output_metadata = {"result_type": type(value).__name__}
        runtime.outputs[key] = value
        runtime.result.metrics.steps_succeeded += 1
        await self._audit(runtime, AgentEventType.WORKFLOW_STEP_COMPLETED, step_id=step.step_id,
                          agent_name=agent_name, agent_version=agent.agent_version)
        await self._persist(runtime)
        return value

    def _make_approval_step(self, runtime: _Runtime) -> None:
        email_version = self.registry.get_version("email")
        step = WorkflowStep(workflow_id=runtime.result.workflow_id,
                            step_type=WorkflowStepType.HUMAN_APPROVAL,
                            logical_item_id="drafts", agent_name="email_approval_service",
                            agent_version=email_version, state=WorkflowStepStatus.WAITING,
                            started_at=now_utc(), input_metadata={"draft_count": len(runtime.drafts)})
        runtime.result.steps.append(step)
        runtime.step_keys[f"{runtime.result.workflow_id}:approval"] = step
        runtime.result.agent_versions["email_approval_service"] = email_version
        runtime.result.metrics.steps_total += 1

    def _set_approval_step(self, runtime: _Runtime, state: WorkflowStepStatus) -> None:
        step = runtime.step_keys.get(f"{runtime.result.workflow_id}:approval")
        if step:
            if step.state == WorkflowStepStatus.WAITING and state == WorkflowStepStatus.SUCCEEDED:
                runtime.result.metrics.steps_succeeded += 1
            step.state = state
            step.ended_at = now_utc()

    def _skip_outreach_steps(self, runtime: _Runtime, reason: str) -> None:
        for step_type in (WorkflowStepType.OUTREACH, WorkflowStepType.FOLLOW_UP):
            step = WorkflowStep(workflow_id=runtime.result.workflow_id,
                                step_type=step_type, logical_item_id="approval-rejected",
                                agent_name="outreach" if step_type == WorkflowStepType.OUTREACH else "follow_up",
                                state=WorkflowStepStatus.SKIPPED, started_at=now_utc(), ended_at=now_utc(),
                                error=reason)
            runtime.result.steps.append(step)
            runtime.result.metrics.steps_total += 1

    def _step_id_for(self, runtime: _Runtime, draft_id: str) -> str | None:
        candidate = runtime.draft_candidates.get(draft_id)
        canonical_id = candidate[0].canonical_id if candidate else None
        for step in reversed(runtime.result.steps):
            if step.step_type == WorkflowStepType.EMAIL_DRAFTING and step.logical_item_id == canonical_id:
                return step.step_id
        return None

    async def _audit(self, runtime: _Runtime, event_type: AgentEventType, *,
                     step_id: str | None = None, agent_name: str = _SERVICE_AGENT,
                     agent_version: str = _SERVICE_VERSION,
                     details: dict[str, Any] | None = None) -> None:
        await self.event_recorder.record(AgentEvent(
            event_type=event_type, execution_id=runtime.result.workflow_id,
            workflow_id=runtime.result.workflow_id, workflow_step_id=step_id,
            agent_name=agent_name, agent_version=agent_version, details=details or {}))

    def _transition(self, runtime: _Runtime, target: WorkflowStatus) -> None:
        validate_transition(runtime.result.status, target)
        runtime.result.status = target
        runtime.result.updated_at = now_utc()

    async def _persist(self, runtime: _Runtime) -> None:
        if self.persistence is not None:
            await self.persistence.save_workflow(runtime.result, runtime.request,
                                                 hop_count=runtime.hop_count)

    async def _get_runtime(self, workflow_id: str) -> _Runtime:
        try:
            return self.store.get_runtime(workflow_id)
        except KeyError:
            return await self._load_runtime(workflow_id)

    async def _load_runtime(self, workflow_id: str) -> _Runtime:
        if self.persistence is None:
            raise KeyError(f"Unknown workflow: {workflow_id}")
        loaded = await self.persistence.load_workflow(workflow_id)
        if loaded is None:
            raise KeyError(f"Unknown workflow: {workflow_id}")
        request, result, hop_count = loaded
        runtime = _Runtime(result=result, request=request, validated=result.validated_profiles,
                           canonical_students=result.canonical_students,
                           matches={match.canonical_student_id: match for match in result.candidate_matches},
                           drafts={draft.draft_id: draft for draft in result.drafts},
                           outreach_results=result.outreach_results,
                           follow_up_results=result.follow_up_results, hop_count=hop_count)
        canonical = {student.canonical_id: student for student in result.canonical_students}
        for draft in result.drafts:
            candidate = canonical.get(draft.candidate_id)
            match = runtime.matches.get(draft.candidate_id)
            if candidate is not None and match is not None:
                runtime.draft_candidates[draft.draft_id] = (candidate, match)
        approval = next((step for step in reversed(result.steps)
                         if step.step_type == WorkflowStepType.HUMAN_APPROVAL), None)
        if approval:
            runtime.step_keys[f"{workflow_id}:approval"] = approval
        self.store.add(runtime)
        await self._hydrate_draft_store(runtime)
        return runtime

    async def _hydrate_draft_store(self, runtime: _Runtime) -> None:
        try:
            email_agent = self.registry.get("email")
        except KeyError:
            return
        draft_store = getattr(email_agent, "draft_store", None)
        if draft_store is not None:
            for draft in runtime.result.drafts:
                try:
                    await draft_store.update(draft.draft_id, lambda _current: (draft, True))
                except KeyError:
                    await draft_store.create_or_get(draft)

    async def _complete(self, runtime: _Runtime) -> None:
        self._transition(runtime, WorkflowStatus.COMPLETED)
        runtime.result.current_step = None
        self._metrics(runtime)
        await self._audit(runtime, AgentEventType.WORKFLOW_COMPLETED)
        await self._persist(runtime)

    async def _critical_failure(self, runtime: _Runtime, category: str, message: str) -> None:
        if runtime.result.status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED, WorkflowStatus.FAILED}:
            return
        self._transition(runtime, WorkflowStatus.FAILED)
        runtime.result.errors.append(f"Critical workflow failure ({category}): {message}")
        runtime.result.current_step = None
        self._metrics(runtime)
        await self._audit(runtime, AgentEventType.WORKFLOW_FAILED,
                          details={"error_category": category})
        await self._persist(runtime)

    def _metrics(self, runtime: _Runtime) -> None:
        runtime.result.updated_at = now_utc()
        runtime.result.metrics.total_duration_seconds = max(
            0.0, (runtime.result.updated_at - runtime.result.created_at).total_seconds())


class _ItemFailure(Exception):
    """Marks a candidate/source-local failure that need not abort sibling items."""
