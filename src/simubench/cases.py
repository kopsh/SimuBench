"""Running cases (D42, docs/real-models.md): each case of a cases file on a freshly powered bench.

The cases themselves — their file format, the coverage tables they keep in step with, finding a
model's cases — are data in :mod:`simubench.library.coverage`; running them opens a bench through
the in-process API (:func:`simubench.api.open_bench`), a layer above the library, so the runner
lives here (``simubench cases``, the HTTP and MCP case runs).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape, quoteattr

from .api import open_bench
from .engine.session import Session
from .library.coverage import Case, Cases, Step, load_cases
from .library.expectations import as_fields, matches_format
from .models.bench import BenchDefinition


@dataclass
class StepResult:
    """One step of a case as it ran: the command, and (a query) what was expected and got."""

    index: int
    action: Literal["send", "query"]
    instrument: str
    command: str
    ok: bool
    expected: str | None = None
    actual: str | None = None
    error: str | None = None


@dataclass
class CaseResult:
    """The outcome of one case; a failure names its step, the command, and what was expected and got.

    With ``collect`` the outcome also carries every step in order (``steps``), for an interface that
    shows what ran; without it ``steps`` stays ``None`` and the runner reports a failure only.
    """

    name: str
    manual: str | None
    passed: bool
    step: int | None = None
    command: str | None = None
    expected: str | None = None
    actual: str | None = None
    error: str | None = None
    seconds: float = 0.0
    steps: list[StepResult] | None = None

    def describe(self) -> str:
        if self.passed:
            return self.name
        where = f"{self.name}: step {self.step} ({self.command!r})" if self.step else self.name
        if self.error:
            return f"{where}: {self.error}"
        return f"{where}: expected {self.expected}, got {self.actual!r}"

    def as_dict(self) -> dict[str, object]:
        return {k: v for k, v in vars(self).items() if v is not None}


def open_case_bench(
    cases_file: Path, cases: Cases, definition_dirs: list[Path] | None = None, model: str | None = None
) -> Session:
    """A freshly powered bench for one case: the cases' bench file, or the instrument alone.

    Idle time does not move virtual time (``idle: off``, D47): a case's result depends on its commands
    alone, not on how fast the machine running it is. Definitions are looked up next to the cases
    file (a model under development), in ``definition_dirs`` and in the library. ``model`` runs the
    cases on another definition in place of theirs — a copy (``extends``) checked against the
    cases of the model it is based on."""
    dirs = [cases_file.parent, *(definition_dirs or [])]
    swap = {cases.model: model} if model and cases.model else None
    if cases.bench:
        return open_bench(cases_file.parent / cases.bench, idle="off", definition_dirs=dirs, models=swap)
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "case",
            "instruments": {cases.instrument: {"model": model or cases.model}},
        }
    )
    return open_bench(bench, idle="off", definition_dirs=dirs)


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
    if step.expect_prefix is not None:
        return f"a reply starting {step.expect_prefix!r}", reply is not None and reply.startswith(
            step.expect_prefix
        )
    if step.expect_format is not None:
        return f"a reply written {step.expect_format}", reply is not None and matches_format(
            reply, step.expect_format
        )
    single = step.tolerance if isinstance(step.tolerance, int | float) else 0.0
    if step.expect_fields is not None:
        fields = as_fields(step.expect_fields)
        return fields.describe(), reply is not None and fields.judge(reply, single)
    if step.expect_block is not None:
        return step.expect_block.describe(), reply is not None and step.expect_block.judge(reply, single)
    written = f", written {step.format}" if step.format else ""
    if step.expect_values is not None:
        # A reading list rides noise (#744): compare elementwise in engineering units instead of a
        # hand-written per-digit envelope.
        tols = step.tolerance if isinstance(step.tolerance, list) else [single] * len(step.expect_values)
        if isinstance(step.tolerance, list):
            expected = ", ".join(f"{v!r} ± {t!r}" for v, t in zip(step.expect_values, tols, strict=True))
        else:
            expected = f"{', '.join(repr(v) for v in step.expect_values)} ± {single!r} (each)"
        expected += written
        if reply is None:
            return expected, False
        parts = reply.split(",")
        if len(parts) != len(step.expect_values):
            return expected, False
        if step.format and not all(matches_format(p, step.format) for p in parts):
            return expected, False
        try:
            got = [float(p) for p in parts]
        except ValueError:
            return expected, False
        return expected, all(
            abs(g - float(e)) <= t for g, e, t in zip(got, step.expect_values, tols, strict=True)
        )
    expected = f"{step.expect_value!r} ± {single!r}{written}"
    if reply is None or (step.format and not matches_format(reply, step.format)):
        return expected, False
    try:
        return expected, abs(float(reply) - float(step.expect_value or 0.0)) <= single
    except ValueError:
        return expected, False


def run_case(
    cases_file: Path,
    cases: Cases,
    case: Case,
    definition_dirs: list[Path] | None = None,
    *,
    collect: bool = False,
    model: str | None = None,
) -> CaseResult:
    """Run one case on a freshly powered bench; with ``collect``, every step's outcome is kept;
    with ``model``, on that definition in place of the cases' own (:func:`open_case_bench`)."""
    t0 = time.perf_counter()
    result = CaseResult(case.name, case.manual, passed=True, steps=[] if collect else None)
    try:
        session = open_case_bench(cases_file, cases, definition_dirs, model)
    except Exception as exc:  # the bench or a definition does not load
        result.passed, result.error = False, f"the bench does not open: {exc}"
        result.seconds = time.perf_counter() - t0
        return result
    try:
        for i, step in enumerate(case.steps, 1):
            instrument = step.instrument or cases.instrument
            entry: StepResult | None = (
                StepResult(
                    index=i,
                    action="query" if step.query is not None else "send",
                    instrument=instrument,
                    command=step.command,
                    ok=True,
                )
                if collect
                else None
            )
            try:
                reply = _reply(session, instrument, step)
            except Exception as exc:
                result.passed, result.step, result.command = False, i, step.command
                result.error = f"{type(exc).__name__}: {exc}"
                if entry is not None:
                    entry.ok, entry.error = False, result.error
                    if result.steps is not None:  # entry exists only when collect made the list
                        result.steps.append(entry)
                break
            if entry is not None and result.steps is not None:
                result.steps.append(entry)
            if step.query is None:
                continue
            expected, ok = _check(step, reply)
            if entry is not None:
                entry.expected = expected
                entry.actual = "(no reply)" if reply is None else reply[:200]
                entry.ok = ok
            if not ok:
                result.passed, result.step, result.command = False, i, step.command
                result.expected = expected
                result.actual = "(no reply)" if reply is None else reply[:200]
                break
    finally:
        session.close()
    result.seconds = time.perf_counter() - t0
    return result


def run_cases(
    cases_file: Path,
    definition_dirs: list[Path] | None = None,
    *,
    collect: bool = False,
    model: str | None = None,
) -> list[CaseResult]:
    """Run every case of a cases file, each on a freshly powered bench (``model``: on that
    definition in place of the cases' own)."""
    cases = load_cases(cases_file)
    return [
        run_case(cases_file, cases, case, definition_dirs, collect=collect, model=model)
        for case in cases.cases
    ]


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
