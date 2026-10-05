import re
import json
from typing import Any

from app.agents.context import AgentContext
from app.agents.models import AgentInput, AgentOutput
from app.agents.exceptions import NonRetryableFailure
from app.crawling.policy import DomainPolicy
from app.schemas.student import ExtractedStudentInformation, StudentValidationResult
from app.schemas.email import EmailDraftStatus, ApprovalActionType


class SourcePolicyGuardrail:
    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        payload = agent_input.payload
        if hasattr(payload, "source_url"):
            if hasattr(payload, "permitted_domains"):
                try:
                    DomainPolicy(payload.permitted_domains).validate(payload.source_url)
                except Exception as e:
                    raise NonRetryableFailure(f"Source URL violates domain policy: {e}")
        elif hasattr(payload, "candidates") and hasattr(payload, "permitted_domains"):
            policy = DomainPolicy(payload.permitted_domains)
            for candidate in payload.candidates:
                try:
                    policy.validate(candidate.url)
                except Exception as e:
                    raise NonRetryableFailure(f"Candidate URL violates domain policy: {e}")

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        pass


class CrawlBudgetGuardrail:
    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        payload = agent_input.payload
        if hasattr(payload, "configuration"):
            conf = payload.configuration
            if hasattr(conf, "max_pages") and conf.max_pages > 25:
                raise NonRetryableFailure(f"max_pages exceeds budget: {conf.max_pages} > 25")
            if hasattr(conf, "max_depth") and conf.max_depth > 5:
                raise NonRetryableFailure(f"max_depth exceeds budget: {conf.max_depth} > 5")

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        pass


class AgentHopGuardrail:
    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        if context.current_hop >= context.max_hops:
            raise NonRetryableFailure(
                f"Hop limit exceeded. Current: {context.current_hop}, Max: {context.max_hops}"
            )

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        pass


class EvidenceRequiredGuardrail:
    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        pass

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        if not output.success or not output.result:
            return
        result = output.result
        if isinstance(result, ExtractedStudentInformation):
            if result.profile.name and not result.profile.name.evidence_ids:
                raise NonRetryableFailure("Missing evidence for extracted name")
            if result.profile.university and not result.profile.university.evidence_ids:
                raise NonRetryableFailure("Missing evidence for extracted university")
        elif isinstance(result, StudentValidationResult):
            for claim in result.validated_claims:
                if not claim.evidence_ids and claim.status == "supported":
                    raise NonRetryableFailure(f"Missing evidence for supported claim: {claim.claim_type}")


class NoHallucinationGuardrail:
    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        pass

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        if not output.success or not output.result:
            return
        result = output.result
        
        def check_profile(profile, evidence_list):
            valid_evidence_ids = {e.evidence_id for e in evidence_list}
            facts = [profile.name, profile.university, profile.degree, profile.branch,
                     profile.expected_graduation_year, profile.graduation_year,
                     profile.current_academic_year, profile.location, profile.github,
                     profile.portfolio, profile.public_contact, *profile.skills,
                     *profile.projects, *profile.research_interests]
            for fact in facts:
                if fact is not None:
                    for eid in fact.evidence_ids:
                        if eid not in valid_evidence_ids:
                            raise NonRetryableFailure(f"Hallucinated evidence ID found: {eid}")

        if isinstance(result, ExtractedStudentInformation):
            check_profile(result.profile, result.evidence)
        elif isinstance(result, StudentValidationResult):
            check_profile(result.profile, result.evidence_references)


class HumanApprovalGuardrail:
    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        payload = agent_input.payload
        if hasattr(payload, "draft"):
            draft = payload.draft
            if getattr(draft, "status", None) != EmailDraftStatus.APPROVED:
                raise NonRetryableFailure("Email draft is not approved.")
            
            has_human_approval = False
            if hasattr(draft, "approvals") and draft.approvals:
                last_action = draft.approvals[-1]
                if (last_action.action == ApprovalActionType.APPROVE
                    and last_action.actor_id.strip().lower() not in {"email-agent", "outreach-agent", "system", "agent"}):
                    has_human_approval = True
                    
            if not has_human_approval:
                raise NonRetryableFailure("Email draft lacks human approval.")

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        pass


class OptOutGuardrail:
    def __init__(self, opt_out_service: Any = None):
        self.opt_out_service = opt_out_service

    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        payload = agent_input.payload
        if hasattr(payload, "draft") and getattr(payload.draft, "recipient", None):
            email = payload.draft.recipient.email
            if self.opt_out_service:
                if await self.opt_out_service.has_opted_out(email):
                    raise NonRetryableFailure("Recipient has opted out.")

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        pass


class SecretLoggingGuardrail:
    SECRET_PATTERN = re.compile(
        r'(?i)\b(password|secret|token|api[_-]?key|apikey)\b\s*[:=]\s*'
        r'["\']?[a-zA-Z0-9_\-\.]{8,}["\']?|\bbearer\s+[a-zA-Z0-9_\-\.]{12,}'
    )

    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        pass

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        try:
            metadata_str = json.dumps(output.metadata)
            if output.result:
                if hasattr(output.result, "model_dump_json"):
                    result_str = output.result.model_dump_json()
                else:
                    result_str = str(output.result)
            else:
                result_str = ""
        except (TypeError, ValueError):
            # Unserializable output is a guardrail failure; silently skipping the
            # inspection would turn serialization failures into a fail-open path.
            raise NonRetryableFailure("Output could not be inspected for secrets.")
        if self.SECRET_PATTERN.search(metadata_str) or self.SECRET_PATTERN.search(result_str):
            raise NonRetryableFailure("Output contains suspected secret material.")


class DuplicateWorkGuardrail:
    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        if not context.idempotency_key:
            raise NonRetryableFailure("Idempotency key is missing from context.")
            
    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        pass


class ResearchBudgetGuardrail:
    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        payload = agent_input.payload
        if hasattr(payload, "max_iterations") and payload.max_iterations > 10:
            raise NonRetryableFailure("Research iteration budget exceeds the maximum of 10")
        if hasattr(payload, "max_sources") and payload.max_sources > 50:
            raise NonRetryableFailure("Research source budget exceeds the maximum of 50")
        if hasattr(payload, "max_pages") and payload.max_pages > 100:
            raise NonRetryableFailure("Research page budget exceeds the maximum of 100")
        if hasattr(payload, "max_duration_seconds") and payload.max_duration_seconds > 900:
            raise NonRetryableFailure("Research duration budget exceeds the maximum of 900 seconds")
        if hasattr(payload, "iteration_count") and payload.iteration_count > 10:
            raise NonRetryableFailure(f"Research iteration budget exceeded: {payload.iteration_count}")
        if hasattr(payload, "source_count") and payload.source_count > 50:
            raise NonRetryableFailure(f"Research source budget exceeded: {payload.source_count}")
        if hasattr(payload, "page_count") and payload.page_count > 100:
            raise NonRetryableFailure(f"Research page budget exceeded: {payload.page_count}")

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        pass
