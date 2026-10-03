"""Conservative profile comparison and canonical grouping."""

import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.models import AgentInput
from app.schemas.canonical import (
    CanonicalConflict, CanonicalStudent, DeduplicationDecision,
    DeduplicationDecisionType, DeduplicationRequest, DeduplicationResult,
    DeduplicationSignal,
)
from app.schemas.student import EvidenceStrength, SourcedFact, StudentProfile


def _value(fact: SourcedFact | None) -> Any | None:
    return fact.value if fact is not None else None


def _norm(value: Any | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip() or None


def _handle(value: Any | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    host = urlparse(text).hostname or ""
    if "github.com" in host:
        parts = [part for part in urlparse(text).path.split("/") if part]
        return parts[0].lower() if parts else None
    return _norm(text)


def _name_key(value: Any | None) -> str | None:
    normalized = _norm(value)
    if not normalized:
        return None
    parts = normalized.split()
    return f"{parts[0][0]} {' '.join(parts[1:])}" if len(parts) >= 2 else normalized


def _fact_evidence(*facts: SourcedFact | None) -> list[str]:
    return list(dict.fromkeys(eid for fact in facts if fact for eid in fact.evidence_ids))


class DeduplicationAgent(BaseAgent):
    agent_name = "deduplication"
    agent_version = "2.0.0"
    description = "Compares evidence-backed profiles conservatively and builds provenance-preserving canonical profiles."
    allowed_tools = frozenset()

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> DeduplicationResult:
        request = agent_input.payload
        if not isinstance(request, DeduplicationRequest):
            request = DeduplicationRequest.model_validate(request)
        profiles = request.profiles
        decisions: list[DeduplicationDecision] = []
        pair_results: dict[tuple[int, int], DeduplicationDecisionType] = {}

        for i, left in enumerate(profiles):
            for j in range(i + 1, len(profiles)):
                right = profiles[j]
                signals: list[DeduplicationSignal] = []

                def compare(field: str, lfact: SourcedFact | None, rfact: SourcedFact | None,
                            strength: EvidenceStrength = EvidenceStrength.MEDIUM,
                            normalize=_norm) -> None:
                    lv, rv = _value(lfact), _value(rfact)
                    if lv is None or rv is None:
                        return
                    match = normalize(lv) == normalize(rv)
                    signals.append(DeduplicationSignal(
                        field=field, left_value=lv, right_value=rv, match=match,
                        strength=strength,
                        explanation=(f"{field.replace('_', ' ')} values match." if match
                                     else f"{field.replace('_', ' ')} values differ."),
                        evidence_ids=_fact_evidence(lfact, rfact),
                    ))

                compare("name", left.name, right.name, EvidenceStrength.HIGH, _name_key)
                compare("university", left.university, right.university, EvidenceStrength.HIGH)
                compare("degree", left.degree, right.degree)
                compare("branch", left.branch, right.branch)
                compare("graduation_year", left.expected_graduation_year or left.graduation_year,
                        right.expected_graduation_year or right.graduation_year, EvidenceStrength.HIGH)
                compare("github", left.github, right.github, EvidenceStrength.HIGH, _handle)
                compare("portfolio", left.portfolio, right.portfolio, EvidenceStrength.HIGH, _handle)
                compare("public_contact", left.public_contact, right.public_contact, EvidenceStrength.HIGH, _norm)

                left_projects = {_norm(f.value) for f in left.projects}
                right_projects = {_norm(f.value) for f in right.projects}
                overlap = sorted(left_projects & right_projects - {None})
                if overlap:
                    signals.append(DeduplicationSignal(
                        field="project_overlap", left_value=overlap, right_value=overlap,
                        match=True, strength=EvidenceStrength.MEDIUM,
                        explanation="One or more project descriptions match exactly after normalization.",
                        evidence_ids=_fact_evidence(*left.projects, *right.projects),
                    ))

                matches = {s.field for s in signals if s.match}
                mismatches = {s.field for s in signals if s.match is False}
                ids = {"github", "portfolio", "public_contact"}
                same_name = "name" in matches
                same_university = "university" in matches
                same_grad = "graduation_year" in matches
                same_id = bool(matches & ids)
                differing_id = bool(mismatches & ids)
                different_university = "university" in mismatches

                if different_university and same_name:
                    result, confidence = DeduplicationDecisionType.DIFFERENT_PERSON, EvidenceStrength.HIGH
                    reasoning = ["The same name is associated with different universities; profiles are kept separate."]
                elif differing_id:
                    result, confidence = DeduplicationDecisionType.POSSIBLE_DUPLICATE, EvidenceStrength.MEDIUM
                    reasoning = ["Profiles share some identity/academic signals but have different public identifiers; automatic merging is unsafe."]
                elif same_id and (same_name or same_university):
                    result, confidence = DeduplicationDecisionType.SAME_PERSON, EvidenceStrength.HIGH
                    reasoning = ["A public identifier matches and is corroborated by a matching name or university."]
                elif same_name and same_university and same_grad:
                    result, confidence = DeduplicationDecisionType.SAME_PERSON, EvidenceStrength.HIGH
                    reasoning = ["Name, university, and graduation year all match, with no conflicting strong identifier."]
                elif same_name and (same_university or same_grad):
                    result, confidence = DeduplicationDecisionType.POSSIBLE_DUPLICATE, EvidenceStrength.MEDIUM
                    reasoning = ["Name plus one academic signal matches, but evidence is not sufficient for a safe merge."]
                elif signals:
                    result, confidence = DeduplicationDecisionType.INSUFFICIENT_EVIDENCE, EvidenceStrength.LOW
                    reasoning = ["Available signals do not establish identity or a reliable distinction."]
                else:
                    result, confidence = DeduplicationDecisionType.INSUFFICIENT_EVIDENCE, EvidenceStrength.UNKNOWN
                    reasoning = ["No comparable identity signals are available."]

                decision = DeduplicationDecision(
                    left_profile_index=i, right_profile_index=j, decision=result,
                    confidence=confidence, reasoning=reasoning, signals=signals,
                    evidence_references=[e for e in [*left.evidence, *right.evidence]
                                         if any(e.evidence_id in s.evidence_ids for s in signals)],
                )
                decisions.append(decision)
                pair_results[i, j] = result

        # Complete-link grouping: a new record joins a group only if it has a
        # SAME_PERSON decision against every existing member of that group.
        groups: list[list[int]] = []
        for index in range(len(profiles)):
            target = next((group for group in groups if all(
                pair_results.get((min(index, member), max(index, member))) == DeduplicationDecisionType.SAME_PERSON
                for member in group
            )), None)
            if target is None:
                groups.append([index])
            else:
                target.append(index)

        canonical: list[CanonicalStudent] = []
        for group in groups:
            sources = [profiles[index] for index in group]
            merged, conflicts = _merge_profiles(sources)
            refs = list(dict.fromkeys(str(source.source_url) for profile in sources for source in profile.source_references))
            material = "|".join(sorted(refs) or [str(group[0])])
            canonical.append(CanonicalStudent(
                canonical_id="student-" + hashlib.sha256(material.encode()).hexdigest()[:16],
                profile=merged, source_profiles=sources, source_profile_references=refs,
                conflicts=conflicts,
            ))
        return DeduplicationResult(decisions=decisions, canonical_students=canonical)


def _merge_profiles(profiles: list[StudentProfile]) -> tuple[StudentProfile, list[CanonicalConflict]]:
    merged = profiles[0].model_copy(deep=True)
    all_evidence = list({item.evidence_id: item for p in profiles for item in p.evidence}.values())
    all_sources = list({str(item.source_url): item for p in profiles for item in p.source_references}.values())
    conflicts: list[CanonicalConflict] = []
    scalar_fields = ("name", "university", "degree", "branch", "expected_graduation_year",
                     "graduation_year", "current_academic_year", "location", "github", "portfolio", "public_contact")
    for field in scalar_fields:
        facts = [getattr(p, field) for p in profiles if getattr(p, field) is not None]
        values: dict[str, list[SourcedFact]] = {}
        for fact in facts:
            values.setdefault(_norm(fact.value) or str(fact.value), []).append(fact)
        if len(values) == 1:
            chosen = facts[0]
            setattr(merged, field, SourcedFact(value=chosen.value, evidence_ids=_fact_evidence(*facts)))
        elif len(values) > 1:
            ids = _fact_evidence(*facts)
            conflicts.append(CanonicalConflict(
                field=field, values=[facts_[0].value for facts_ in values.values()], evidence_ids=ids,
                explanation="Source profiles disagree; the canonical profile preserves all source values without selecting one.",
            ))
            setattr(merged, field, None)
    for field in ("skills", "projects", "research_interests"):
        facts = [fact for profile in profiles for fact in getattr(profile, field)]
        unique: dict[str, SourcedFact] = {}
        for fact in facts:
            key = _norm(fact.value) or str(fact.value)
            if key in unique:
                unique[key].evidence_ids = list(dict.fromkeys([*unique[key].evidence_ids, *fact.evidence_ids]))
            else:
                unique[key] = fact.model_copy(deep=True)
        setattr(merged, field, list(unique.values()))
    if (merged.expected_graduation_year is not None and merged.graduation_year is not None
            and _norm(merged.expected_graduation_year.value) != _norm(merged.graduation_year.value)):
        year_facts = [merged.expected_graduation_year, merged.graduation_year]
        conflicts.append(CanonicalConflict(
            field="graduation_year",
            values=[fact.value for fact in year_facts],
            evidence_ids=_fact_evidence(*year_facts),
            explanation="Expected and attained graduation-year claims disagree; both source-backed values are retained.",
        ))
    # Statuses are conservative across records: enrichment/merging never upgrades
    # or downgrades a status based only on absence in another source.
    merged.evidence = all_evidence
    merged.source_references = all_sources
    return merged, conflicts
