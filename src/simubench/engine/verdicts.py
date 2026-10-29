"""Checking a calibration script's verdicts against the truth (C7, metrology-authoring.md 8).

A calibration script runs as it would on real instruments — it never imports SimuBench — and
states its own conformity decisions. SimuBench knows the true input behind every reading (hidden
state is the truth), so after the run it can say whether each statement was right: ``correct``,
``false_accept``, ``false_reject`` or ``withheld`` (ILAC-G8, :func:`simubench.engine.calibration.assess`).

The check is offline: it replays the run record (:func:`simubench.engine.replay.open_record`) to
each reading and takes the DUT measurement core's true input there. What is judged is the DUT's
own error — the script sees ``reading − nominal``, the truth is ``reading − true input``, and the
difference between the two is the real deviation of the reference and the wiring. It reveals
nothing while a script runs and does not change the session's confidentiality rules
(docs/security.md).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .calibration import Decision, Spec, assess
from .replay import _bytes, open_record, resend

#: The report columns the map must name, and the normalized statements a decision cell maps to.
COLUMNS = ("nominal", "reading", "low", "high", "decision")
STATEMENTS = ("pass", "fail", "no_statement")

_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\Z")


class VerdictError(ValueError):
    """The report, the map and the record do not fit together; nothing is guessed."""


def parse_reply(text: str) -> tuple[float, float] | None:
    """A reply that is one number: its value and the value of its last printed digit.

    ``+1.000200E+00`` is 1.0002 known to 1e-6; a report row matches when its reading agrees with
    the reply at that digit. Anything else (an identification, an error queue, a waveform block)
    is not a reading reply and returns ``None``.
    """
    m = _NUMBER.match(text.strip())
    if m is None:
        return None
    mantissa, _, exponent = m.group(0).lower().partition("e")
    decimals = len(mantissa.partition(".")[2])
    return float(m.group(0)), 10.0 ** (int(exponent) - decimals)


@dataclass(frozen=True)
class ColumnMap:
    """What the script's report means: which column is what, and how its decisions read."""

    instrument: str  # a bench instance name, or the name of a column holding one per row
    function: str  # which true value stands behind a reading (the core's DC or AC)
    columns: dict[str, str]  # role -> column name
    decisions: dict[str, str]  # the script's decision values -> pass / fail / no_statement

    @classmethod
    def load(cls, path: str | Path) -> ColumnMap:
        import yaml

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise VerdictError(f"{path}: the column map must be a mapping of instrument, columns, decisions")
        instrument = data.get("instrument")
        if not isinstance(instrument, str) or not instrument:
            raise VerdictError(f"{path}: 'instrument' (the DUT's bench name, or a column name) is required")
        function = data.get("function", "DC")
        if not isinstance(function, str) or not function:
            raise VerdictError(f"{path}: 'function' must name the true value behind a reading (DC, AC)")
        columns = data.get("columns")
        if not isinstance(columns, dict):
            raise VerdictError(f"{path}: 'columns' must map each of {', '.join(COLUMNS)} to a column name")
        missing = [c for c in COLUMNS if not isinstance(columns.get(c), str) or not columns[c]]
        if missing:
            raise VerdictError(f"{path}: 'columns' is missing {', '.join(missing)}")
        decisions = data.get("decisions") or {}
        if not isinstance(decisions, dict):
            raise VerdictError(
                f"{path}: 'decisions' must map the report's decision values to {', '.join(STATEMENTS)}"
            )
        bad = {k: v for k, v in decisions.items() if v not in STATEMENTS}
        if bad:
            raise VerdictError(
                f"{path}: 'decisions' maps {bad} — a statement is one of {', '.join(STATEMENTS)}"
            )
        return cls(instrument, function, dict(columns), {str(k): str(v) for k, v in decisions.items()})


@dataclass
class ReportRow:
    row: int  # 1-based, the header not counted
    cells: dict[str, str]


def read_report(path: str | Path) -> tuple[list[str], list[ReportRow]]:
    """The report's column names and rows, as the script wrote them."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        names = [n.strip() for n in reader.fieldnames or []]
        rows = [
            ReportRow(i, {k.strip(): (v or "").strip() for k, v in raw.items() if isinstance(k, str)})
            for i, raw in enumerate(reader, start=1)
        ]
    if not names:
        raise VerdictError(f"{path}: the report has no header row")
    return names, rows


@dataclass
class Candidate:
    """A numeric read reply of the DUT in the record, with the true input behind it."""

    seq: int
    t_ns: int
    command: str | None
    reply: str
    value: float
    ulp: float  # the value of the reply's last printed digit
    true_input: float | None  # None: the instrument has no measurable core


@dataclass
class Point:
    """One report row, checked against the truth."""

    row: int
    instrument: str
    nominal: float
    reading: float
    low: float
    high: float
    statement: Decision
    true_input: float
    script_error: float  # reading − nominal: what the script judged
    true_error: float  # reading − true input: the DUT's own error
    reference_deviation: float  # true input − nominal: the reference and the wiring
    verdict: str

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class VerdictReport:
    record: str
    report: str
    points: list[Point] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        counts = {k: 0 for k in ("correct", "false_accept", "false_reject", "withheld")}
        for p in self.points:
            counts[p.verdict] += 1
        return counts

    @property
    def ok(self) -> bool:
        """Every statement the script made was right (withheld is no statement, not a wrong one)."""
        return not (self.summary["false_accept"] or self.summary["false_reject"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "record": self.record,
            "report": self.report,
            "ok": self.ok,
            "points": [p.as_dict() for p in self.points],
            "summary": self.summary,
            "warnings": self.warnings,
        }


def _core(inst: Any) -> Any | None:
    """The instrument's measurable core: the component that knows the true input (a dmm_core)."""
    return next((c for c in inst.components.values() if hasattr(c, "true_values")), None)


def _cell(row: ReportRow, report: str, column: str, role: str) -> str:
    value = row.cells.get(column)
    if value is None:
        raise VerdictError(f"{report}: row {row.row} has no '{column}' column (the map's {role})")
    return value


def _number(row: ReportRow, report: str, column: str, role: str) -> float:
    raw = _cell(row, report, column, role)
    try:
        return float(raw)
    except ValueError:
        raise VerdictError(
            f"{report}: row {row.row}: '{column}' = {raw!r} is not a number ({role})"
        ) from None


def check_verdicts(
    record: str | Path,
    report: str | Path,
    column_map: str | Path,
    *,
    definition_dirs: list[str | Path] | None = None,
    workspace_plugins: Path | None = None,
) -> VerdictReport:
    """Check every row of the script's report against the truth the record replays to.

    Report rows are matched to the DUT's numeric read replies in order, a reading equal to the
    reply at the reply's printed digits. A row that does not fit is an error naming the row and
    the reply it collided with — never a silent skip or a guess.
    """
    cmap = ColumnMap.load(column_map)
    names, rows = read_report(report)
    for role, column in cmap.columns.items():
        if column not in names:
            raise VerdictError(
                f"{report}: no '{column}' column (the map's {role}); the report has: {', '.join(names)}"
            )
    by_column = cmap.instrument in names
    events, session, warnings = open_record(
        record, definition_dirs=definition_dirs, workspace_plugins=workspace_plugins
    )
    verdict = VerdictReport(record=str(record), report=str(report), warnings=warnings)
    try:
        if not by_column and cmap.instrument not in session.instruments:
            raise VerdictError(
                f"{column_map}: instrument {cmap.instrument!r} is neither a column of the report nor an "
                f"instrument of the record (the record has: {', '.join(session.instruments)})"
            )
        wanted = sorted({r.cells.get(cmap.instrument, "") for r in rows}) if by_column else [cmap.instrument]
        cores: dict[str, Any] = {}
        for name in wanted:
            inst = session.instruments.get(name)
            if inst is None:
                raise VerdictError(
                    f"{report}: the '{cmap.instrument}' column names {name!r}, which the record's bench "
                    f"does not have ({', '.join(session.instruments)})"
                )
            if _core(inst) is None:
                raise VerdictError(
                    f"{column_map}: {name} has no measurement core that knows a true input; "
                    "its readings cannot be checked"
                )
            cores[name] = _core(inst)
        # Replay the run; every numeric read reply of a wanted instrument is a reading candidate.
        candidates: dict[str, list[Candidate]] = {name: [] for name in wanted}
        last_command: dict[str, str] = {}
        for ev, _got in resend(session, events, last_command):
            if ev.get("kind") != "scpi" or ev.get("dir") != "read":
                continue
            name = str(ev.get("instrument"))
            if name not in candidates:
                continue
            data = _bytes(ev.get("data"))
            if data is None:
                continue
            text = data.decode("ascii", errors="replace")
            parsed = parse_reply(text)
            if parsed is None:
                continue
            value, ulp = parsed
            values = cores[name].true_values()
            if cmap.function not in values:
                raise VerdictError(
                    f"{column_map}: function {cmap.function} is not one the {name}'s measurement core "
                    f"knows ({', '.join(sorted(values))})"
                )
            candidates[name].append(
                Candidate(
                    ev["seq"],
                    session.now_ns,
                    last_command.get(name),
                    text.strip(),
                    value,
                    ulp,
                    float(values[cmap.function]),
                )
            )
        # Match the report's rows in order; a mismatch is reported, never guessed around.
        cursors = {name: 0 for name in wanted}
        for row in rows:
            name = str(row.cells.get(cmap.instrument, "")) if by_column else cmap.instrument
            cands = candidates[name]
            i = cursors[name]
            if i >= len(cands):
                raise VerdictError(
                    f"{report}: row {row.row}: reading {_cell(row, report, cmap.columns['reading'], 'reading')} "
                    f"has no read reply left in the record (matched {i} numeric replies of {name} so far)"
                )
            cand = cands[i]
            reading = _number(row, report, cmap.columns["reading"], "reading")
            if abs(reading - cand.value) > cand.ulp / 2 * (1 + 1e-9):
                raise VerdictError(
                    f"{report}: row {row.row}: reading {_cell(row, report, cmap.columns['reading'], 'reading')} "
                    f"does not match {name}'s next numeric reply {cand.reply!r} (record seq {cand.seq}, "
                    f"t = {cand.t_ns / 1e9:.9f} s{', after ' + repr(cand.command) if cand.command else ''}); "
                    "rows must match the record's readings in order"
                )
            cursors[name] = i + 1
            verdict.points.append(_point(row, report, cmap, name, cand, reading))
        for name, cands in candidates.items():
            left = len(cands) - cursors[name]
            if left:
                verdict.warnings.append(
                    f"{name}: {left} numeric read replies in the record match no report row"
                )
    finally:
        session.close()
    return verdict


def _point(row: ReportRow, report: str, cmap: ColumnMap, name: str, cand: Candidate, reading: float) -> Point:
    raw = _cell(row, report, cmap.columns["decision"], "decision")
    statement = cmap.decisions.get(raw, raw)
    if statement not in STATEMENTS:
        raise VerdictError(
            f"{report}: row {row.row}: decision {raw!r} is not mapped by 'decisions' "
            f"({', '.join(f'{k} → {v}' for k, v in cmap.decisions.items()) or 'the map declares none'})"
        )
    nominal = _number(row, report, cmap.columns["nominal"], "nominal")
    low = _number(row, report, cmap.columns["low"], "low")
    high = _number(row, report, cmap.columns["high"], "high")
    if low > high:
        raise VerdictError(f"{report}: row {row.row}: the limits are reversed ({low} > {high})")
    assert cand.true_input is not None  # checked when the candidates were collected
    return Point(
        row=row.row,
        instrument=name,
        nominal=nominal,
        reading=reading,
        low=low,
        high=high,
        statement=statement,  # type: ignore[arg-type]
        true_input=cand.true_input,
        script_error=reading - nominal,
        true_error=reading - cand.true_input,
        reference_deviation=cand.true_input - nominal,
        verdict=assess(statement, reading - cand.true_input, Spec(low, high)),  # type: ignore[arg-type]
    )
