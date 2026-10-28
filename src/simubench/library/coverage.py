"""Real models (D42, docs/real-models.md): coverage tables and manual cases.

A coverage table (``<id>.coverage.yaml`` next to the definition) lists every command of the manual,
by section, with how far SimuBench goes: implemented, accepted (parsed and stored, no effect on the
simulation) or not modeled (answers -113). Manual cases (``tests/models/<id>/cases.yaml``) are
command sequences with expected replies, each citing the manual section it checks.

:func:`check` keeps the three in step: every command of the definition is in the table, every
implemented or accepted command of the table is in the definition and has at least one case.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..engine.instrument import BUILTIN_HEADERS
from ..models.definition import InstrumentDefinition
from ..scpi.errors import ScpiError
from ..scpi.parse import Pattern, parse_unit, split_units
from .loader import COVERAGE_SUFFIX, _read_yaml, load_yaml

Level = Literal["implemented", "accepted", "not_modeled"]
LEVEL_TEXT = {"implemented": "实现", "accepted": "接受", "not_modeled": "未实现"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoverageEntry(_Model):
    header: str = Field(
        ..., description="Header as the definition writes it, e.g. '[:SENSe]:VOLTage[:DC]:NPLC'"
    )
    level: Level
    note: str | None = None


class CoverageSection(_Model):
    section: str = Field(..., description="Manual section, e.g. 'Command Reference > SENSe Subsystem'")
    commands: list[CoverageEntry]


class Coverage(_Model):
    schema_: Literal["simubench.coverage/1"] = Field("simubench.coverage/1", alias="schema")
    model: str = Field(..., description="Definition id, e.g. keysight-34465a")
    sections: list[CoverageSection]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def entries(self) -> list[tuple[str, CoverageEntry]]:
        return [(s.section, c) for s in self.sections for c in s.commands]

    def stats(self) -> dict[str, int]:
        counts = Counter(c.level for _, c in self.entries())
        return {level: counts.get(level, 0) for level in ("implemented", "accepted", "not_modeled")}


class Step(_Model):
    """One command of a case: ``send`` writes, ``query`` writes and reads the reply."""

    send: str | None = None
    query: str | None = None
    expect: str | None = Field(None, description="The exact reply")
    expect_match: str | None = Field(None, description="A regular expression the whole reply matches")
    expect_value: float | None = Field(None, description="The reply as a number")
    tolerance: float | None = Field(None, description="Absolute tolerance for expect_value")
    binary: bool = Field(
        False,
        description="The reply is binary (a block): expect / expect_match apply to its bytes read as Latin-1",
    )

    @model_validator(mode="after")
    def _check(self) -> Step:
        if (self.send is None) == (self.query is None):
            raise ValueError("a step has exactly one of send or query")
        if self.send is not None and (self.expect, self.expect_match, self.expect_value) != (
            None,
            None,
            None,
        ):
            raise ValueError(f"{self.send!r}: only a query has an expected reply")
        if self.query is not None and [self.expect, self.expect_match, self.expect_value].count(None) != 2:
            raise ValueError(f"{self.query!r}: give one of expect, expect_match or expect_value")
        if self.tolerance is not None and self.expect_value is None:
            raise ValueError("tolerance goes with expect_value")
        return self

    @property
    def command(self) -> str:
        return self.send if self.send is not None else self.query  # type: ignore[return-value]


class Case(_Model):
    name: str
    manual: str = Field(..., description="Manual section the case follows")
    steps: list[Step]


class Cases(_Model):
    model: str | None = Field(None, description="Definition id; defaults to the folder name")
    bench: str | None = Field(
        None, description="Bench file relative to the cases file; default: the instrument alone"
    )
    instrument: str = Field("dut", description="Instrument the steps talk to")
    cases: list[Case]


def load_cases(path: Path) -> Cases:
    data = load_yaml(path.read_text(encoding="utf-8"))
    if isinstance(data, list):  # the short form: just the list of cases
        data = {"cases": data}
    cases = Cases.model_validate(data)
    if cases.model is None:
        cases.model = path.parent.name
    return cases


def coverage_path(definition_path: Path) -> Path:
    return definition_path.with_name(definition_path.stem + COVERAGE_SUFFIX)


def load_coverage(path: Path) -> Coverage:
    return Coverage.model_validate(_read_yaml(path))


def _norm(header: str) -> str:
    """Headers compare as written, ignoring case, spaces, the root colon and suffix names (<n>, <ch>)."""
    return re.sub(r"<\w*>", "<>", re.sub(r"\s+", "", header)).lstrip(":").upper()


def _matches(header: str, command: str) -> bool:
    """Whether a program message (one or more units) uses the command ``header``."""
    try:
        units = [parse_unit(u) for u in split_units(command.encode())]
    except ScpiError:
        return False
    pattern = Pattern.compile(header)
    for unit in units:
        if pattern.common:
            if unit.common and unit.tokens[0].keyword == pattern.nodes[0].long:
                return True
        elif not unit.common and pattern.match(unit.tokens) is not None:
            return True
    return False


def check(definition: InstrumentDefinition, coverage: Coverage, cases: Cases | None) -> list[str]:
    """Problems that make the definition, its coverage table and its cases disagree (none: [])."""
    problems: list[str] = []
    if coverage.model != definition.id:
        problems.append(f"coverage table is for {coverage.model!r}, the definition is {definition.id!r}")
    defined = {_norm(c.header): c.header for c in definition.commands}
    builtin = {_norm(h) for h in (BUILTIN_HEADERS if definition.builtins is None else definition.builtins)}
    listed: dict[str, tuple[str, CoverageEntry]] = {}
    for section, entry in coverage.entries():
        key = _norm(entry.header)
        if key in listed:
            problems.append(
                f"{entry.header}: listed twice in the coverage table ({listed[key][0]}, {section})"
            )
        listed[key] = (section, entry)
    for key, header in defined.items():
        if key not in listed:
            problems.append(f"{header}: in the definition but not in the coverage table")
        elif listed[key][1].level == "not_modeled":
            problems.append(f"{header}: in the definition but marked not modeled in the coverage table")
    for key, (section, entry) in listed.items():
        if entry.level == "not_modeled":
            continue
        if key not in defined and key not in builtin:
            problems.append(f"{entry.header}: marked {entry.level} ({section}) but not in the definition")
        if cases is not None and not any(
            _matches(entry.header, step.command) for case in cases.cases for step in case.steps
        ):
            problems.append(f"{entry.header}: marked {entry.level} ({section}) but no manual case uses it")
    return problems
