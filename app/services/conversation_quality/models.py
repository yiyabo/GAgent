"""Validated data contracts for conversation quality evaluation."""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SatisfactionLevel = Literal["satisfied", "acceptable", "negative", "angry"]
EvaluationStatus = Literal["pending", "retry", "evaluating", "provisional", "final", "failed"]


class QualityEvidence(BaseModel):
    source: Literal["user_follow_up", "run_fact", "observation"]
    quote: str = Field(min_length=1, max_length=1000)
    explanation: str = Field(min_length=1, max_length=1000)


class ConversationQualityResult(BaseModel):
    satisfaction_level: SatisfactionLevel
    confidence: float = Field(ge=0.0, le=1.0)
    feedback_relation: Literal[
        "explicit_feedback",
        "unresolved_follow_up",
        "satisfied_confirmation",
        "new_request",
        "observation_timeout",
        "other",
    ]
    signals: List[str] = Field(default_factory=list, max_length=12)
    evidence: List[QualityEvidence] = Field(default_factory=list, min_length=1, max_length=6)
    failure_modes: List[str] = Field(default_factory=list, max_length=8)
    responsible_stages: List[str] = Field(default_factory=list, max_length=6)
    recommended_investigation: List[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def cap_observation_timeout_confidence(self) -> "ConversationQualityResult":
        if self.feedback_relation == "observation_timeout":
            self.confidence = min(self.confidence, 0.45)
        return self

    @field_validator("signals", "failure_modes", "responsible_stages", "recommended_investigation")
    @classmethod
    def normalize_strings(cls, values: List[str]) -> List[str]:
        normalized: List[str] = []
        for value in values:
            candidate = str(value).strip().lower().replace(" ", "_")[:80]
            if candidate and candidate not in normalized:
                normalized.append(candidate)
        return normalized
