"""Conformity decisions and the truth to check them against (calibration-notes.md 4, ILAC-G8).

A script decides from what it measured, under a decision rule it declares. SimuBench also knows the
true value (hidden state is the truth), so it can say whether the script's statement was right:
under simple acceptance a reading just inside a limit is accepted even when the truth is outside;
a guard band of the expanded uncertainty U trades such false accepts for "no statement".
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal

from ..models.bench import Asset

Rule = Literal["simple", "guard_band"]
Decision = Literal["pass", "fail", "no_statement"]


@dataclass(frozen=True)
class Spec:
    low: float
    high: float

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high


def decide(
    measured: float, spec: Spec, *, rule: Rule = "simple", u: float = 0.0, guard: float | None = None
) -> Decision:
    """The conformity statement a rule makes from a measured value.

    ``u`` is the expanded uncertainty U (k = 2); the guard band ``guard`` defaults to U. Under the
    guard-band rule the acceptance zone is [low + w, high − w] and the rejection zone lies beyond
    [low − w, high + w]; in between no statement of conformity is made.
    """
    if rule == "simple":
        return "pass" if spec.contains(measured) else "fail"
    w = u if guard is None else guard
    if spec.low + w <= measured <= spec.high - w:
        return "pass"
    if measured < spec.low - w or measured > spec.high + w:
        return "fail"
    return "no_statement"


def truth(true_value: float, spec: Spec) -> Literal["conforms", "does_not_conform"]:
    return "conforms" if spec.contains(true_value) else "does_not_conform"


def assess(
    statement: Decision, true_value: float, spec: Spec
) -> Literal["correct", "false_accept", "false_reject", "withheld"]:
    """Was a script's statement right, given the true value?"""
    t = truth(true_value, spec)
    if statement == "no_statement":
        return "withheld"
    if statement == "pass":
        return "correct" if t == "conforms" else "false_accept"
    return "correct" if t == "does_not_conform" else "false_reject"


def register_view(assets: list[Asset], today: dt.date) -> list[dict[str, Any]]:
    """The register as a script would read it, with days to the due date on the bench's calendar."""
    out = []
    for a in assets:
        row: dict[str, Any] = a.model_dump(mode="json")
        row["due_in_days"] = (a.cal_due - today).days if a.cal_due else None
        out.append(row)
    return out
