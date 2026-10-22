"""Rule-based diagnosis: which failure modes explain this alarm?

Deterministic on purpose. The same alarm always gives the same answer, it runs
without an LLM, and an engineer can read exactly why a cause was suggested.
The LLM agent uses this as a tool rather than guessing on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain import Alarm, FailureMode
from .plant import Plant


@dataclass
class Diagnosis:
    failure_mode: FailureMode
    confidence: str  # high | medium | low
    reason: str


def diagnose(alarm: Alarm, plant: Plant) -> list[Diagnosis]:
    """Rank the asset's known failure modes against the alarm parameter.

    A failure mode whose *primary* indicator (first in its list) is the alarmed
    parameter scores highest; a secondary indicator scores lower. A critical
    alarm raises confidence by one step.
    """
    results: list[tuple[int, Diagnosis]] = []
    for fm in plant.failure_modes_for(alarm.asset_id):
        if alarm.parameter not in fm.indicators:
            continue
        primary = fm.indicators[0] == alarm.parameter
        score = (2 if primary else 1) + (1 if alarm.severity == "critical" else 0)
        confidence = {3: "high", 2: "medium"}.get(score, "low")
        role = "primary" if primary else "secondary"
        reason = f"{alarm.parameter} is a {role} indicator of {fm.name.lower()}"
        results.append((score, Diagnosis(fm, confidence, reason)))
    results.sort(key=lambda r: -r[0])
    return [d for _, d in results]
