"""Domain models for conservative identity resolution and enrichment."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.student import Evidence, EvidenceStrength, StudentProfile


class DeduplicationDecisionType(StrEnum):
    SAME_PERSON = "same_person"
    DIFFERENT_PERSON = "different_person"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class DeduplicationSignal(BaseModel):
    field: str
    left_value: Any | None = None
    right_value: Any | None = None
    match: bool | None = None
    strength: EvidenceStrength = EvidenceStrength.UNKNOWN
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)


class DeduplicationDecision(BaseModel):
    left_profile_index: int
    right_profile_index: int
    decision: DeduplicationDecisionType
    confidence: EvidenceStrength
    reasoning: list[str]
    signals: list[DeduplicationSignal] = Field(default_factory=list)
    evidence_references: list[Evidence] = Field(default_factory=list)


class CanonicalConflict(BaseModel):
    field: str
    values: list[Any]
    evidence_ids: list[str]
    explanation: str


class CanonicalStudent(BaseModel):
    canonical_id: str
    profile: StudentProfile
    source_profiles: list[StudentProfile] = Field(min_length=1)
    source_profile_references: list[str] = Field(default_factory=list)
    conflicts: list[CanonicalConflict] = Field(default_factory=list)


class DeduplicationRequest(BaseModel):
    profiles: list[StudentProfile]


class DeduplicationResult(BaseModel):
    decisions: list[DeduplicationDecision] = Field(default_factory=list)
    canonical_students: list[CanonicalStudent] = Field(default_factory=list)


class EnrichmentRequest(BaseModel):
    canonical_student: CanonicalStudent
    additions: list["EnrichmentSource"] = Field(default_factory=list)


class EnrichmentSource(BaseModel):
    extracted_profile: StudentProfile
    source_url: str
    access: str = "public_or_authorized"


class EnrichmentResult(BaseModel):
    canonical_student: CanonicalStudent
    added_evidence_ids: list[str] = Field(default_factory=list)
    added_facts: list[str] = Field(default_factory=list)
    conflicts: list[CanonicalConflict] = Field(default_factory=list)
    requires_validation: bool = False
    notes: list[str] = Field(default_factory=list)
