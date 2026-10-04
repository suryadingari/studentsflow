"""Structured requirements and explainable candidate-match results."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.canonical import CanonicalConflict, CanonicalStudent
from app.schemas.student import Evidence, EvidenceStrength


class CriterionType(StrEnum):
    FINAL_YEAR = "final_year"
    AI_INTEREST = "ai_interest"
    LOCATION = "location"
    UNIVERSITY = "university"
    DEGREE = "degree"
    BRANCH = "branch"
    SKILL = "skill"
    AI_AREA = "ai_area"
    PROJECT = "project"
    RESEARCH = "research"
    EXPERIENCE = "experience"
    OTHER = "other"


class UserCriterion(BaseModel):
    criterion_type: CriterionType
    value: str | None = None
    required: bool = True
    # Alternatives are explicit requirement-level equivalences only.
    accepted_alternatives: list[str] = Field(default_factory=list)
    description: str | None = None

    @model_validator(mode="after")
    def non_status_criteria_need_a_value(self) -> "UserCriterion":
        if self.criterion_type not in {CriterionType.FINAL_YEAR, CriterionType.AI_INTEREST} and not (self.value and self.value.strip()):
            raise ValueError("this criterion type requires a non-empty value")
        if any(not value.strip() for value in self.accepted_alternatives):
            raise ValueError("accepted alternatives must not be blank")
        return self


class UserRequirement(BaseModel):
    requirement_id: str | None = None
    raw_text: str | None = None
    criteria: list[UserCriterion] = Field(min_length=1)


class MatchStatus(StrEnum):
    MATCH = "match"
    PARTIAL_MATCH = "partial_match"
    NO_MATCH = "no_match"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CriterionStatus(StrEnum):
    MATCHED = "matched"
    MISSING = "missing"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CriterionAssessment(BaseModel):
    criterion_type: CriterionType
    value: str | None = None
    required: bool
    status: CriterionStatus
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


class CandidateMatch(BaseModel):
    student_reference: str | None = None
    canonical_student_id: str
    candidate_name: str | None = None
    status: MatchStatus
    match_score: int = Field(default=0, ge=0, le=100)
    score_explanation: str = "Deterministic weighted evidence coverage; not a probability."
    assessments: list[CriterionAssessment] = Field(default_factory=list)
    matched_required_criteria: list[CriterionAssessment] = Field(default_factory=list)
    missing_required_criteria: list[CriterionAssessment] = Field(default_factory=list)
    missing_preferred_criteria: list[CriterionAssessment] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    conflicts: list[CanonicalConflict] = Field(default_factory=list)
    explanation: str


class MatchingRequest(BaseModel):
    requirement: UserRequirement
    candidates: list[CanonicalStudent]


class MatchingResult(BaseModel):
    requirement: UserRequirement
    candidate_matches: list[CandidateMatch] = Field(default_factory=list)
