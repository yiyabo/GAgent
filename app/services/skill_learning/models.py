"""Model-authored procedures are proposals; lifecycle facts belong to the server."""
from __future__ import annotations
import hashlib
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProcedureStep(BaseModel):
    model_config = ConfigDict(extra='forbid')
    instruction: str = Field(min_length=1, max_length=900)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class SkillDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=3, max_length=64)
    description: str = Field(min_length=8, max_length=180)
    domain: Literal['routine', 'research', 'writing', 'other']
    when_to_use: str = Field(min_length=8, max_length=1000)
    inputs: list[str] = Field(min_length=1, max_length=12)
    steps: list[ProcedureStep] = Field(min_length=1, max_length=15)
    verification: list[str] = Field(min_length=1, max_length=12)
    limitations: list[str] = Field(min_length=1, max_length=12)
    pitfalls: list[str] = Field(default_factory=list, max_length=12)
    keywords: list[str] = Field(min_length=1, max_length=12)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,63}', value):
            raise ValueError('name must be a lowercase skill slug')
        return value

    @field_validator('inputs', 'verification', 'limitations', 'pitfalls', 'keywords')
    @classmethod
    def bounded_items(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 650 for value in values):
            raise ValueError('skill list items must be nonempty and bounded')
        return list(dict.fromkeys(value.strip() for value in values))

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def markdown(self, public_name: str) -> str:
        lines = ['---', f'name: {self.name}', 'description: '+json.dumps(self.description, ensure_ascii=False), '---', '', '# '+self.name,
                 '\n## When to Use', self.when_to_use, '\n## Inputs']
        lines.extend('- '+item for item in self.inputs)
        lines.append('\n## Procedure')
        lines.extend(f'{i}. {step.instruction}' for i, step in enumerate(self.steps, 1))
        for title, values in [('Verification', self.verification), ('Limitations', self.limitations), ('Pitfalls', self.pitfalls)]:
            if values:
                lines.append('\n## '+title)
                lines.extend('- '+item for item in values)
        return '\n'.join(lines)


class DistillationResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reusable: bool
    reason: str = Field(min_length=1, max_length=600)
    draft: SkillDraft | None = None
