"""Diagnosis scoring.

Given a scenario's ``expected_findings`` (keywords) and an actual
:class:`agent.schemas.Diagnosis`, produce a numeric score in [0, 1].

Scoring is deliberately simple — bag-of-keywords over concatenated
finding text. Interview-defensible: it's transparent, reproducible,
and captures the thing that matters (did the agent name the right
root cause?) without the noise of exact-string matching.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.schemas import Diagnosis


@dataclass
class Score:
    scenario: str
    matched: list[str]
    missing: list[str]
    score: float

    @property
    def passed(self) -> bool:
        return self.score >= 0.5


def score_diagnosis(
    scenario_key: str,
    expected_findings: tuple[str, ...],
    diagnosis: Diagnosis | None,
) -> Score:
    if diagnosis is None:
        return Score(scenario_key, [], list(expected_findings), 0.0)

    haystack = (
        diagnosis.summary
        + " "
        + " ".join(f.finding + " " + f.recommended_action for f in diagnosis.findings)
    ).lower()

    matched: list[str] = []
    missing: list[str] = []
    for kw in expected_findings:
        (matched if kw.lower() in haystack else missing).append(kw)

    denom = max(len(expected_findings), 1)
    return Score(
        scenario=scenario_key,
        matched=matched,
        missing=missing,
        # any-match earns partial credit; all-match is 1.0
        score=len(matched) / denom,
    )
