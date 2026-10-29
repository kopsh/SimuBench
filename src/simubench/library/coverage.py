"""Real models (D42, docs/real-models.md): coverage tables and manual cases.

A coverage table (``<id>.coverage.yaml`` next to the definition) lists every command of the manual,
by section, with how far SimuBench goes: implemented, accepted (parsed and stored, no effect on the
simulation) or not modeled (answers -113). Cases (``cases.yaml``) are command sequences with
expected replies: manual cases of a real model (``tests/models/<id>/cases.yaml``) cite the manual
section they check, contract cases the interface contract of an existing test program. Both run the
same way: each case on a freshly powered bench (:func:`run_cases`, ``simubench cases``).

:func:`check` keeps the three in step: every command of the definition is in the table, every
implemented or accepted command of the table is in the definition and has at least one case.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from xml.sax.saxutils import escape, quoteattr

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..engine.instrument import BUILTIN_HEADERS
from ..models.definition import InstrumentDefinition
from ..scpi.errors import ScpiError
from ..scpi.parse import Pattern, parse_unit, split_units
from .loader import COVERAGE_SUFFIX, _read_yaml, load_yaml

if TYPE_CHECKING:
    from ..engine.session import Session

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
    instrument: str | None = Field(None, description="Instrument of this step; default: the file's")
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
    manual: str | None = Field(
        None, description="Manual section the case follows (or the contract clause it checks)"
    )
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
    for case in cases.cases if cases is not None else []:
        if not case.manual:
            problems.append(f"{case.name}: a manual case names the manual section it follows (manual)")
    return problems


DRAFT_SECTION = "Commands"
DRAFT_NOTE = "草稿：定义中已有，确认程度与说明"


def read_command_list(text: str) -> list[tuple[str, list[str]]]:
    """A command list: one header per line; ``# name`` starts a section, blank lines are ignored.

    Headers before the first ``#`` line go in a section called "Commands". Only command names come
    from the manual (real-models.md 7), so the list is what an author copies from its index."""
    sections: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            sections.append((line.lstrip("#").strip() or DRAFT_SECTION, []))
            continue
        if not sections:
            sections.append((DRAFT_SECTION, []))
        sections[-1][1].append(line)
    return [(name, headers) for name, headers in sections if headers]


def draft_coverage(
    model: str,
    commands: list[tuple[str, list[str]]],
    existing: Coverage | None = None,
    definition: InstrumentDefinition | None = None,
) -> tuple[Coverage, int]:
    """A coverage table with every listed command, and how many entries were added.

    New commands are not modeled, except those the definition already has: implemented, with a
    note to confirm. An existing table keeps its entries, levels and notes as they are; listed
    commands it lacks join the section of the same name, or a new one at the end."""
    cov = existing.model_copy(deep=True) if existing else Coverage(model=model, sections=[])
    known = {_norm(c.header) for _, c in cov.entries()}
    defined = {_norm(c.header) for c in definition.commands} if definition else set()
    added = 0
    for name, headers in commands:
        section = next((s for s in cov.sections if s.section == name), None)
        for header in headers:
            key = _norm(header)
            if key in known:
                continue
            known.add(key)
            if section is None:
                section = CoverageSection(section=name, commands=[])
                cov.sections.append(section)
            entry = (
                CoverageEntry(header=header, level="implemented", note=DRAFT_NOTE)
                if key in defined
                else CoverageEntry(header=header, level="not_modeled")
            )
            section.commands.append(entry)
            added += 1
    return cov, added


def dump_coverage(cov: Coverage, leading_comments: str = "") -> str:
    """YAML text of a coverage table, after the comment lines a hand-written file starts with."""
    import yaml

    data = cov.model_dump(mode="json", by_alias=True, exclude_none=True)
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    return leading_comments + body


def leading_comments(text: str) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        if not line.startswith("#"):
            break
        lines.append(line)
    return "".join(lines)


# ---- running cases --------------------------------------------------------------------------------


@dataclass
class CaseResult:
    """The outcome of one case; a failure names its step, the command, and what was expected and got."""

    name: str
    manual: str | None
    passed: bool
    step: int | None = None
    command: str | None = None
    expected: str | None = None
    actual: str | None = None
    error: str | None = None
    seconds: float = 0.0

    def describe(self) -> str:
        if self.passed:
            return self.name
        where = f"{self.name}: step {self.step} ({self.command!r})" if self.step else self.name
        if self.error:
            return f"{where}: {self.error}"
        return f"{where}: expected {self.expected}, got {self.actual!r}"

    def as_dict(self) -> dict[str, object]:
        return {k: v for k, v in vars(self).items() if v is not None}


def open_case_bench(cases_file: Path, cases: Cases, definition_dirs: list[Path] | None = None) -> Session:
    """A freshly powered bench for one case: the cases' bench file, or the instrument alone.

    Definitions are looked up next to the cases file (a model under development), in
    ``definition_dirs`` and in the library."""
    from ..api import open_bench
    from ..models.bench import BenchDefinition

    dirs = [cases_file.parent, *(definition_dirs or [])]
    if cases.bench:
        return open_bench(cases_file.parent / cases.bench, definition_dirs=dirs)
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "case",
            "instruments": {cases.instrument: {"model": cases.model}},
        }
    )
    return open_bench(bench, definition_dirs=dirs)


def _reply(session: Session, instrument: str, step: Step) -> str | None:
    session.write(instrument, step.command.encode() + b"\n")
    if step.send is not None:
        return None
    out = session.read(instrument)
    if out is None:
        return None
    text = out.decode("latin-1")
    return text if step.binary else text.strip()


def _check(step: Step, reply: str | None) -> tuple[str, bool]:
    """(what was expected, whether the reply meets it)."""
    if step.expect is not None:
        return repr(step.expect), reply == step.expect
    if step.expect_match is not None:
        return f"a match of {step.expect_match!r}", reply is not None and bool(
            re.fullmatch(step.expect_match, reply, re.DOTALL)
        )
    tol = step.tolerance or 0.0
    expected = f"{step.expect_value!r} ± {tol!r}"
    try:
        return expected, reply is not None and abs(float(reply) - float(step.expect_value or 0.0)) <= tol
    except ValueError:
        return expected, False


def run_case(
    cases_file: Path, cases: Cases, case: Case, definition_dirs: list[Path] | None = None
) -> CaseResult:
    """Run one case on a freshly powered bench."""
    t0 = time.perf_counter()
    result = CaseResult(case.name, case.manual, passed=True)
    try:
        session = open_case_bench(cases_file, cases, definition_dirs)
    except Exception as exc:  # the bench or a definition does not load
        result.passed, result.error = False, f"the bench does not open: {exc}"
        result.seconds = time.perf_counter() - t0
        return result
    try:
        for i, step in enumerate(case.steps, 1):
            instrument = step.instrument or cases.instrument
            try:
                reply = _reply(session, instrument, step)
            except Exception as exc:
                result.passed, result.step, result.command = False, i, step.command
                result.error = f"{type(exc).__name__}: {exc}"
                break
            if step.query is None:
                continue
            expected, ok = _check(step, reply)
            if not ok:
                result.passed, result.step, result.command = False, i, step.command
                result.expected = expected
                result.actual = "(no reply)" if reply is None else reply[:200]
                break
    finally:
        session.close()
    result.seconds = time.perf_counter() - t0
    return result


def run_cases(cases_file: Path, definition_dirs: list[Path] | None = None) -> list[CaseResult]:
    """Run every case of a cases file, each on a freshly powered bench."""
    cases = load_cases(cases_file)
    return [run_case(cases_file, cases, case, definition_dirs) for case in cases.cases]


def junit_xml(suites: list[tuple[str, list[CaseResult]]]) -> str:
    """JUnit XML for CI: one test suite per cases file, one test case per case."""
    total = sum(len(r) for _, r in suites)
    failed = sum(not c.passed for _, r in suites for c in r)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<testsuites tests="{total}" failures="{failed}">']
    for name, results in suites:
        bad = sum(not c.passed for c in results)
        seconds = sum(c.seconds for c in results)
        lines.append(
            f'  <testsuite name={quoteattr(name)} tests="{len(results)}" failures="{bad}" time="{seconds:.3f}">'
        )
        for c in results:
            label = f"{c.name} [{c.manual}]" if c.manual else c.name
            head = f'    <testcase classname={quoteattr(name)} name={quoteattr(label)} time="{c.seconds:.3f}"'
            if c.passed:
                lines.append(head + "/>")
            else:
                lines.append(head + ">")
                lines.append(
                    f"      <failure message={quoteattr(c.describe())}>{escape(c.describe())}</failure>"
                )
                lines.append("    </testcase>")
        lines.append("  </testsuite>")
    lines.append("</testsuites>")
    return "\n".join(lines) + "\n"
