"""Structured diagnosis output.

The agent is required to emit a :class:`Diagnosis`, not free-form
prose. Every :class:`Finding` must cite one or more correlation IDs
from tool calls in the same run — this is what makes
"evidence vs. assumption" enforceable (architecture doc §5.5).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Confidence = Literal["low", "medium", "high"]


class Finding(BaseModel):
    model_config = {"extra": "forbid"}

    finding: str = Field(..., min_length=1, max_length=500)
    evidence: list[str] = Field(
        default_factory=list,
        description="Correlation IDs of tool calls that support this finding.",
    )
    confidence: Confidence = "medium"
    # Empty allowed: sometimes a finding is "everything is fine" and no
    # action is needed. UI substitutes a placeholder for empties.
    recommended_action: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _require_evidence(self) -> "Finding":
        if not self.evidence:
            raise ValueError("Every finding must cite at least one tool-call ID.")
        return self


class Diagnosis(BaseModel):
    model_config = {"extra": "forbid"}

    summary: str = Field(..., min_length=1, max_length=1000)
    findings: list[Finding] = Field(default_factory=list)
    all_tool_calls: list[str] = Field(
        default_factory=list,
        description="Correlation IDs of every tool call made this run.",
    )

    @model_validator(mode="after")
    def _evidence_must_reference_real_calls(self) -> "Diagnosis":
        known = set(self.all_tool_calls)
        for f in self.findings:
            unknown = [e for e in f.evidence if e not in known]
            if unknown:
                raise ValueError(
                    f"Finding cites unknown tool-call IDs: {unknown}"
                )
        return self
