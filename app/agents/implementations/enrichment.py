"""Evidence-only additive enrichment for canonical student profiles."""

from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.models import AgentInput
from app.schemas.canonical import (
    CanonicalConflict, CanonicalStudent, EnrichmentRequest, EnrichmentResult,
)
from app.schemas.student import SourcedFact


_SCALAR_FIELDS = (
    "name", "university", "degree", "branch", "expected_graduation_year",
    "graduation_year", "current_academic_year", "location", "github", "portfolio", "public_contact",
)
_LIST_FIELDS = ("skills", "projects", "research_interests")


def _key(value: Any) -> str:
    return " ".join(str(value).lower().split())


class EnrichmentAgent(BaseAgent):
    agent_name = "enrichment"
    agent_version = "2.0.0"
    description = "Adds evidence-backed profile information while preserving original claims and conflicts."
    # Enrichment consumes source-backed extraction results; it does not crawl/search by itself.
    allowed_tools = frozenset()

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> EnrichmentResult:
        request = agent_input.payload
        if not isinstance(request, EnrichmentRequest):
            request = EnrichmentRequest.model_validate(request)
        canonical = request.canonical_student.model_copy(deep=True)
        profile = canonical.profile
        added_ids: list[str] = []
        added_facts: list[str] = []
        new_conflicts: list[CanonicalConflict] = []
        notes: list[str] = []

        evidence_by_id = {item.evidence_id: item for item in profile.evidence}
        sources_by_url = {str(item.source_url): item for item in profile.source_references}
        for addition in request.additions:
            if addition.access not in {"public", "authorized", "public_or_authorized"}:
                notes.append(f"Skipped source {addition.source_url}: source is not marked public or authorized.")
                continue
            addition_source_urls = {str(item.source_url) for item in addition.extracted_profile.source_references}
            if addition.source_url not in addition_source_urls:
                notes.append(f"Skipped source {addition.source_url}: it is not represented in the supplied provenance.")
                continue
            matching_source = next(item for item in addition.extracted_profile.source_references
                                   if str(item.source_url) == addition.source_url)
            if matching_source.access not in {"public", "authorized", "public_or_authorized"}:
                notes.append(f"Skipped source {addition.source_url}: provenance does not permit its use.")
                continue
            incoming_evidence = addition.extracted_profile.evidence
            if not incoming_evidence:
                notes.append(f"No evidence-backed information was available from {addition.source_url}.")
                continue
            # Only retain evidence whose source is explicitly listed on this addition.
            permitted_urls = addition_source_urls
            source_evidence = [item for item in incoming_evidence if str(item.source_url) in permitted_urls]
            if len(source_evidence) != len(incoming_evidence):
                notes.append(f"Some evidence from {addition.source_url} was skipped because its source provenance did not match.")
            for item in source_evidence:
                evidence_by_id.setdefault(item.evidence_id, item)
                if item.evidence_id not in added_ids:
                    added_ids.append(item.evidence_id)

            for field in _SCALAR_FIELDS:
                incoming: SourcedFact | None = getattr(addition.extracted_profile, field)
                if incoming is None or not set(incoming.evidence_ids).issubset({e.evidence_id for e in source_evidence} | set(evidence_by_id)):
                    continue
                current: SourcedFact | None = getattr(profile, field)
                if current is None:
                    setattr(profile, field, incoming.model_copy(deep=True))
                    added_facts.append(field)
                elif _key(current.value) == _key(incoming.value):
                    current.evidence_ids = list(dict.fromkeys([*current.evidence_ids, *incoming.evidence_ids]))
                else:
                    conflict = CanonicalConflict(
                        field=field, values=[current.value, incoming.value],
                        evidence_ids=list(dict.fromkeys([*current.evidence_ids, *incoming.evidence_ids])),
                        explanation="Enrichment source disagrees with the canonical claim; original and new evidence are retained without overwriting.",
                    )
                    if not any(c.field == field and set(map(_key, c.values)) == set(map(_key, conflict.values)) for c in [*canonical.conflicts, *new_conflicts]):
                        new_conflicts.append(conflict)

            for field in _LIST_FIELDS:
                existing: list[SourcedFact] = getattr(profile, field)
                known = {_key(fact.value): fact for fact in existing}
                for incoming in getattr(addition.extracted_profile, field):
                    if not set(incoming.evidence_ids).issubset({e.evidence_id for e in source_evidence} | set(evidence_by_id)):
                        continue
                    key = _key(incoming.value)
                    if key in known:
                        known[key].evidence_ids = list(dict.fromkeys([*known[key].evidence_ids, *incoming.evidence_ids]))
                    else:
                        copied = incoming.model_copy(deep=True)
                        existing.append(copied)
                        known[key] = copied
                        added_facts.append(field)

            if (profile.expected_graduation_year is not None and profile.graduation_year is not None
                    and _key(profile.expected_graduation_year.value) != _key(profile.graduation_year.value)):
                year_facts = [profile.expected_graduation_year, profile.graduation_year]
                conflict = CanonicalConflict(
                    field="graduation_year", values=[item.value for item in year_facts],
                    evidence_ids=list(dict.fromkeys(eid for item in year_facts for eid in item.evidence_ids)),
                    explanation="Expected and attained graduation-year claims disagree; both source-backed values are retained.",
                )
                if not any(c.field == conflict.field and set(map(_key, c.values)) == set(map(_key, conflict.values)) for c in [*canonical.conflicts, *new_conflicts]):
                    new_conflicts.append(conflict)

            for source in addition.extracted_profile.source_references:
                sources_by_url.setdefault(str(source.source_url), source)
            if not any(addition.source_url in {str(source.source_url) for source in original.source_references}
                       for original in canonical.source_profiles):
                canonical.source_profiles.append(addition.extracted_profile.model_copy(deep=True))
            if addition.source_url not in canonical.source_profile_references:
                canonical.source_profile_references.append(addition.source_url)

        profile.evidence = list(evidence_by_id.values())
        profile.source_references = list(sources_by_url.values())
        canonical.conflicts.extend(new_conflicts)
        return EnrichmentResult(
            canonical_student=canonical,
            added_evidence_ids=added_ids,
            added_facts=list(dict.fromkeys(added_facts)),
            conflicts=new_conflicts,
            requires_validation=bool(added_ids or new_conflicts),
            notes=notes or (["No additional evidence-backed information was found."] if not added_ids else []),
        )
