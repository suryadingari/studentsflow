"""Evidence-rule validation kept separate from extraction."""

from collections import defaultdict
from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.models import AgentInput
from app.schemas.student import (
    AIInterestStatus, AIEvidenceCategory, ClaimType, ClaimValidationStatus,
    ConflictingClaim, Evidence, EvidenceStrength, ExtractedStudentInformation,
    FinalYearStatus, StudentProfile, StudentValidationResult, ValidatedClaim,
    ValidationRequest,
)


_AI_DIRECT_CATEGORIES = {
    AIEvidenceCategory.AI_PROJECT, AIEvidenceCategory.MACHINE_LEARNING,
    AIEvidenceCategory.DEEP_LEARNING, AIEvidenceCategory.COMPUTER_VISION,
    AIEvidenceCategory.NLP, AIEvidenceCategory.GENERATIVE_AI,
    AIEvidenceCategory.AI_RESEARCH, AIEvidenceCategory.AI_PUBLICATION,
    AIEvidenceCategory.AI_INTERNSHIP, AIEvidenceCategory.AI_HACKATHON,
    AIEvidenceCategory.AI_GITHUB, AIEvidenceCategory.AI_PORTFOLIO,
    AIEvidenceCategory.EXPLICIT_AI_INTEREST, AIEvidenceCategory.OTHER_AI_ACTIVITY,
}


class ValidationAgent(BaseAgent):
    agent_name = "validation"
    agent_version = "2.0.0"
    description = "Evaluates extracted claims against their preserved evidence and reports uncertainty/conflicts."
    allowed_tools = frozenset()

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> StudentValidationResult:
        request = agent_input.payload
        if isinstance(request, ExtractedStudentInformation):
            request = ValidationRequest(extracted=request)
        elif not isinstance(request, ValidationRequest):
            request = ValidationRequest.model_validate(request)
        extracted = request.extracted
        evidence = extracted.evidence
        grouped: dict[ClaimType, list[Evidence]] = defaultdict(list)
        for item in evidence:
            grouped[item.claim_type].append(item)

        validated: list[ValidatedClaim] = []
        unsupported: list[ValidatedClaim] = []
        conflicts: list[ConflictingClaim] = []
        conflict_types: set[ClaimType] = set()
        for claim_type, items in grouped.items():
            unique: dict[str, list[Evidence]] = defaultdict(list)
            for item in items:
                unique[str(item.claim_value)].append(item)
            if len(unique) > 1:
                conflict_types.add(claim_type)
                conflicts.append(ConflictingClaim(
                    claim_type=claim_type,
                    values=[items[0].claim_value for items in unique.values()],
                    evidence_ids=[item.evidence_id for item in items],
                ))
                for item in items:
                    unsupported.append(ValidatedClaim(
                        claim_type=claim_type, value=item.claim_value,
                        status=ClaimValidationStatus.CONFLICTING,
                        evidence_ids=[item.evidence_id],
                        explanation="Sources contain different values; neither value was selected.",
                    ))
            else:
                for item in items:
                    is_weak_ai_mention = item.claim_type == ClaimType.AI_INTEREST and item.strength == EvidenceStrength.LOW
                    status = ClaimValidationStatus.INSUFFICIENT if is_weak_ai_mention else ClaimValidationStatus.SUPPORTED
                    claim = ValidatedClaim(
                        claim_type=claim_type, value=item.claim_value, status=status,
                        evidence_ids=[item.evidence_id],
                        explanation=("Keyword mention lacks evidence of the student's own AI interest or activity."
                                     if is_weak_ai_mention else "Claim is directly supported by the cited source text."),
                    )
                    (unsupported if is_weak_ai_mention else validated).append(claim)

        # An expected and an attained graduation year are distinct claims, but
        # different values cannot both describe the same student's graduation.
        expected_years = grouped.get(ClaimType.EXPECTED_GRADUATION_YEAR, [])
        attained_years = grouped.get(ClaimType.GRADUATION_YEAR, [])
        if expected_years and attained_years and any(
            expected_item.claim_value != attained_item.claim_value
            for expected_item in expected_years for attained_item in attained_years
        ):
            conflict_types.update({ClaimType.EXPECTED_GRADUATION_YEAR, ClaimType.GRADUATION_YEAR})
            combined = [*expected_years, *attained_years]
            conflicts.append(ConflictingClaim(
                claim_type=ClaimType.EXPECTED_GRADUATION_YEAR,
                values=[item.claim_value for item in combined],
                evidence_ids=[item.evidence_id for item in combined],
            ))
            conflict_ids = {item.evidence_id for item in combined}
            for claim in list(validated):
                if any(item_id in conflict_ids for item_id in claim.evidence_ids):
                    validated.remove(claim)
                    unsupported.append(claim.model_copy(update={
                        "status": ClaimValidationStatus.CONFLICTING,
                        "explanation": "Expected and attained graduation-year sources disagree; neither value was selected.",
                    }))

        final_status = FinalYearStatus.FINAL_YEAR_UNVERIFIED
        explanations: list[str] = []
        final_evidence = grouped.get(ClaimType.ACADEMIC_YEAR, [])
        expected = grouped.get(ClaimType.EXPECTED_GRADUATION_YEAR, [])
        graduated = grouped.get(ClaimType.GRADUATION_YEAR, [])
        year_sources = [*expected, *graduated]
        if ClaimType.ACADEMIC_YEAR in conflict_types or ClaimType.EXPECTED_GRADUATION_YEAR in conflict_types or ClaimType.GRADUATION_YEAR in conflict_types:
            final_status = FinalYearStatus.CONFLICTING_EVIDENCE
            explanations.append("Final-year evidence conflicts across sources; no graduation claim was resolved.")
        else:
            academic_values = [str(item.claim_value).lower() for item in final_evidence]
            explicit_final = any("final year" in value or "final-year" in value for value in academic_values)
            explicit_other_year = any(any(word in value for word in ("first year", "second year", "third year", "1st year", "2nd year", "3rd year")) for value in academic_values)
            active_expected = any(
                isinstance(item.claim_value, int) and request.current_year <= item.claim_value <= request.current_year + 1
                for item in expected
            )
            past_graduation = any(isinstance(item.claim_value, int) and item.claim_value < request.current_year for item in graduated)
            if explicit_final or active_expected:
                final_status = FinalYearStatus.FINAL_YEAR_VERIFIED
                explanations.append("Final-year status is supported by an explicit final-year statement or an expected graduation year within the current/next calendar year.")
            elif explicit_other_year or past_graduation:
                final_status = FinalYearStatus.NON_FINAL_YEAR
                explanations.append("Available academic-year or completed-graduation evidence indicates the student is not currently in the final year.")
            elif year_sources or final_evidence:
                explanations.append("Academic information exists, but it does not establish current final-year status.")
            else:
                explanations.append("No reliable final-year or graduation evidence was extracted.")

        ai_evidence = [item for item in evidence if item.claim_type in {ClaimType.AI_ACTIVITY, ClaimType.AI_INTEREST}
                       and item.ai_category in _AI_DIRECT_CATEGORIES]
        direct_activity = any(item.claim_type == ClaimType.AI_ACTIVITY and item.strength in {EvidenceStrength.HIGH, EvidenceStrength.MEDIUM}
                              for item in ai_evidence)
        explicit_interest = any(item.claim_type == ClaimType.AI_INTEREST
                                and item.ai_category == AIEvidenceCategory.EXPLICIT_AI_INTEREST
                                and item.strength in {EvidenceStrength.HIGH, EvidenceStrength.MEDIUM}
                                for item in ai_evidence)
        weak_indicator = bool(ai_evidence) and not (direct_activity or explicit_interest)
        if direct_activity or explicit_interest:
            ai_status = AIInterestStatus.AI_INTEREST_SUPPORTED
            ai_items = [item for item in ai_evidence if item.claim_type == ClaimType.AI_ACTIVITY or item.ai_category == AIEvidenceCategory.EXPLICIT_AI_INTEREST]
            explanations.append("AI interest or experience is supported by source evidence of an AI-related activity or an explicit AI-interest statement.")
            validated.extend(ValidatedClaim(
                claim_type=item.claim_type, value=item.claim_value,
                status=ClaimValidationStatus.SUPPORTED, evidence_ids=[item.evidence_id],
                explanation="AI-related activity/interest is directly described in the cited source.",
            ) for item in ai_items if not any(c.evidence_ids == [item.evidence_id] for c in validated))
        elif weak_indicator or not request.research_complete:
            ai_status = AIInterestStatus.AI_INTEREST_UNVERIFIED
            explanations.append("AI-related wording is inconclusive, or source research is incomplete; this is not treated as supported or absent.")
        else:
            ai_status = AIInterestStatus.AI_INTEREST_NOT_FOUND
            explanations.append("Completed source research found no evidence of AI interest or experience.")

        profile = extracted.profile.model_copy(deep=True)
        profile.evidence = evidence
        profile.source_references = extracted.source_references
        profile.final_year_status = final_status
        profile.ai_interest_status = ai_status
        return StudentValidationResult(
            profile=profile, final_year_status=final_status,
            ai_interest_status=ai_status, validated_claims=validated,
            unsupported_claims=unsupported, conflicting_claims=conflicts,
            evidence_references=evidence, explanations=explanations,
        )
