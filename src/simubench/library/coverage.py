"""Real models (D42, docs/real-models.md): coverage tables and manual cases.

A coverage table (``<id>.coverage.yaml`` next to the definition) lists every command of the manual,
by section, with how far SimuBench goes: implemented, accepted (parsed and stored, no effect on the
simulation) or not implemented in the definition (answers -113). Cases (``cases.yaml``) are command sequences with
expected replies: manual cases of a real model (``tests/models/<id>/cases.yaml``) cite the manual
section they check, contract cases the interface contract of an existing test program. Both run the
same way: each case on a freshly powered bench (:func:`simubench.cases.run_cases`,
``simubench cases``).

:func:`check` keeps the three in step: every command of the definition is in the table, every
implemented or accepted command of the table is in the definition and has at least one case.
"""

from __future__ import annotations

import itertools
import logging
import os
import re
import threading
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..engine.commands import answered_builtins
from ..errors import Invalid
from ..models.definition import InstrumentDefinition
from ..scpi.errors import ScpiError
from ..scpi.parse import Pattern, parse_unit, split_units
from .expectations import Block, Field_, Fields, as_fields
from .loader import COVERAGE_SUFFIX, _read_yaml, load_yaml, parse_error_text

if TYPE_CHECKING:
    pass

Level = Literal["implemented", "accepted", "not_modeled"]
LEVEL_TEXT = {"implemented": "实现", "accepted": "接受", "not_modeled": "未实现"}
#: Why a command is not modeled (#1037): never simulated, waiting for a platform capability, or
#: doable with what the platform has. Absent on an entry nobody has triaged yet.
Gap = Literal["out_of_scope", "blocked", "model_work"]
GAP_TEXT = {"out_of_scope": "不做", "blocked": "等能力", "model_work": "可做", "untriaged": "未分拣"}

log = logging.getLogger(__name__)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoverageEntry(_Model):
    header: str = Field(
        ..., description="Header as the definition writes it, e.g. '[:SENSe]:VOLTage[:DC]:NPLC'"
    )
    level: Level
    affects_data: bool | None = Field(
        None,
        description="accepted commands must say false: parsing, storing and returning the value "
        "changes no acquisition, measurement, transfer or reply. A command that does change one is "
        "implemented or not modeled instead (real-models.md 2)",
    )
    note: str | None = None
    gap: Gap | None = Field(
        None,
        description="not_modeled only: why — out_of_scope (never simulated), blocked (waits for the "
        "capability in blocked_by) or model_work (the platform can already do it)",
    )
    blocked_by: str | None = Field(
        None,
        description="gap: blocked only: the capability it waits for — a product-plan.md 4.2 id "
        "(e.g. C5) or a short name of a capability the plan does not list yet",
    )

    @model_validator(mode="after")
    def _gap_rules(self) -> CoverageEntry:
        if self.level != "not_modeled" and (self.gap is not None or self.blocked_by is not None):
            raise ValueError(f"{self.header}: gap and blocked_by are for not_modeled commands only")
        if self.gap == "blocked" and not (self.blocked_by or "").strip():
            raise ValueError(f"{self.header}: gap: blocked needs blocked_by (the capability it waits for)")
        if self.gap != "blocked" and self.blocked_by is not None:
            raise ValueError(f"{self.header}: blocked_by needs gap: blocked")
        return self


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

    def gap_stats(self) -> dict[str, int]:
        """The not_modeled commands by gap; ``untriaged`` counts those without one (#1037)."""
        counts = Counter(c.gap or "untriaged" for _, c in self.entries() if c.level == "not_modeled")
        return {gap: counts.get(gap, 0) for gap in ("out_of_scope", "blocked", "model_work", "untriaged")}


#: The expectation keys of a query step: exactly one is given (D49, #926: none is a regex).
EXPECT_KEYS = (
    "expect",
    "expect_prefix",
    "expect_format",
    "expect_value",
    "expect_values",
    "expect_fields",
    "expect_block",
)


class Step(_Model):
    """One command of a case: ``send`` writes, ``query`` writes and reads the reply.

    A query says what its reply must be with exactly one ``expect*`` key (library/expectations.py):
    the exact text, its start, its written format, a number or a list of numbers within a tolerance,
    fields judged one by one, or an IEEE 488.2 block. None of them is a regular expression."""

    send: str | None = None
    query: str | None = None
    instrument: str | None = Field(None, description="Instrument of this step; default: the file's")
    expect: str | None = Field(None, description="The exact reply")
    expect_prefix: str | None = Field(
        None,
        description="The reply starts with this text (an error and its message: '-222,\"Data out of range')",
    )
    expect_format: str | None = Field(
        None,
        description="How the whole reply is written: integer, nr1, nr2, nr3, number, or a picture "
        "(d a digit, ± a sign, ~ an optional sign, anything else itself: dddd,dd,dd)",
    )
    expect_value: float | None = Field(None, description="The reply as a number")
    expect_values: list[float] | None = Field(
        None, description="The comma-separated reply as numbers, compared elementwise (#836)"
    )
    expect_fields: list[Field_] | Fields | None = Field(
        None,
        description="The reply cut into fields (by ',' unless separator says otherwise), each judged by "
        "its spec: exact text, a number within tolerance, min/max, a format, one_of, …",
    )
    expect_block: Block | None = Field(None, description="The reply is an IEEE 488.2 block")
    tolerance: float | list[float] | None = Field(
        None,
        description="Absolute tolerance for expect_value / expect_values (one per value, or one for all) "
        "and the bare numbers of expect_fields",
    )
    format: str | None = Field(
        None, description="How each number of expect_value / expect_values is written (see expect_format)"
    )
    binary: bool = Field(
        False,
        description="The reply is binary (a block): the expectation applies to its bytes read as Latin-1, unstripped",
    )

    @model_validator(mode="before")
    @classmethod
    def _no_regex(cls, data: Any) -> Any:
        if isinstance(data, dict) and "expect_match" in data:
            raise ValueError(
                "expect_match (a regular expression) is no longer accepted (D49, #926): judge the reply "
                "by meaning — expect_prefix for an error and its message, expect_value(s) with tolerance "
                "for readings, expect_fields for a list of fields, expect_format for how it is written, "
                "expect_block for a block"
            )
        return data

    @model_validator(mode="after")
    def _check(self) -> Step:
        if (self.send is None) == (self.query is None):
            raise ValueError("a step has exactly one of send or query")
        given = [k for k in EXPECT_KEYS if getattr(self, k) is not None]
        if self.send is not None and given:
            raise ValueError(f"{self.send!r}: only a query has an expected reply")
        if self.query is not None and len(given) != 1:
            raise ValueError(f"{self.query!r}: give one of {', '.join(EXPECT_KEYS)}")
        numeric = ("expect_value", "expect_values", "expect_fields", "expect_block")
        if self.tolerance is not None and (not given or given[0] not in numeric):
            raise ValueError("tolerance goes with expect_value, expect_values, expect_fields or expect_block")
        if isinstance(self.tolerance, list) and (
            self.expect_values is None or len(self.tolerance) != len(self.expect_values)
        ):
            raise ValueError("a list of tolerances goes with expect_values, one per value")
        if self.format is not None and self.expect_value is None and self.expect_values is None:
            raise ValueError("format goes with expect_value or expect_values")
        if self.expect_values is not None and not self.expect_values:
            raise ValueError("expect_values is empty")
        if self.expect_fields is not None:
            as_fields(self.expect_fields)  # an empty list fails here, with the rule
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
    try:
        data = load_yaml(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        # A cases file that does not parse is a domain error (#1406), like a bench or definition.
        raise Invalid(parse_error_text(exc, path.name)) from None
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


def _forms(header: str) -> set[str]:
    """The spellings of a header with each optional node present or absent, compared like _norm.

    Two entries that share a spelling name the same concrete command, so their levels must agree
    (one manual documents ``[:TRACe]`` optional, another spells it out; #293). Literal suffix
    digits stay in the spelling, so SOURce1 and SOURce2 are different commands."""
    pieces = re.split(r"(\[[^\]]*\])", _norm(header))
    choices = [((p[1:-1], "") if p.startswith("[") else (p,)) for p in pieces if p]
    return {"".join(combo).lstrip(":") for combo in itertools.product(*choices)}


def _units(command: str) -> list[Any]:
    """The parsed units of a program message (none when it does not parse)."""
    try:
        return [parse_unit(u) for u in split_units(command.encode())]
    except ScpiError:
        return []


def _uses(pattern: Pattern, units: list[Any]) -> bool:
    """Whether a parsed program message uses the command the pattern compiles."""
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
    builtin = {_norm(h) for h in answered_builtins(definition)}
    listed: dict[str, tuple[str, CoverageEntry]] = {}
    forms: dict[str, tuple[str, CoverageEntry]] = {}
    for section, entry in coverage.entries():
        key = _norm(entry.header)
        if key in listed:
            problems.append(
                f"{entry.header}: listed twice in the coverage table ({listed[key][0]}, {section})"
            )
        for form in _forms(entry.header):
            first = forms.setdefault(form, (section, entry))
            if first[1] is not entry and first[1].level != entry.level:
                problems.append(
                    f"{entry.header}: {entry.level} ({section}) contradicts {first[1].header}: "
                    f"{first[1].level} ({first[0]}); the two spellings name the same command"
                )
                break
        if entry.level == "accepted" and entry.affects_data is not False:
            problems.append(
                f"{entry.header}: accepted but does not declare affects_data: false ({section}); a "
                "command that changes data is implemented or not modeled"
            )
        listed[key] = (section, entry)
    for key, header in defined.items():
        if key not in listed:
            problems.append(f"{header}: in the definition but not in the coverage table")
        elif listed[key][1].level == "not_modeled":
            problems.append(f"{header}: in the definition but marked not modeled in the coverage table")
    # every case step parsed once, not once per entry (#1185: a table of 1 147 rows and 100 cases)
    parsed = (
        [_units(step.command) for case in cases.cases for step in case.steps] if cases is not None else []
    )
    for key, (section, entry) in listed.items():
        if entry.level == "not_modeled":
            continue
        if key not in defined and key not in builtin:
            problems.append(f"{entry.header}: marked {entry.level} ({section}) but not in the definition")
        pattern = Pattern.compile(entry.header) if cases is not None else None
        if pattern is not None and not any(_uses(pattern, u) for u in parsed):
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


# ---- finding a model's cases ------------------------------------------------------------------------


def find_cases_files(workspace: Path) -> list[Path]:
    """The case files in a workspace: named ``cases.yaml`` or ``<something>.cases.yaml``.

    Folders that never hold the user's files (SimuBench's own state, tool folders) are skipped, as
    :func:`simubench.server.benchfiles.scan` does for bench files."""
    from .loader import SKIP_DIRS

    found = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found.extend(Path(root) / f for f in files if f.endswith("cases.yaml"))
    return sorted(found, key=lambda p: p.relative_to(workspace).as_posix())


def cases_for_model(workspace: Path, def_id: str) -> list[tuple[str, int]]:
    """Case files in the workspace whose model is ``def_id``, workspace-relative, with case counts.

    A YAML file that merely ends in ``cases.yaml`` but is not one is skipped."""
    out: list[tuple[str, int]] = []
    for path in find_cases_files(workspace):
        try:
            cases = load_cases(path)
        except Exception as exc:  # not a cases file, or a broken one: the case runner reports it
            log.debug("cases for %r: %s skipped: %s", def_id, path, exc)
            continue
        if cases.model == def_id:
            out.append((path.relative_to(workspace).as_posix(), len(cases.cases)))
    return out


# ---- the built-in models' manual cases (#1352) -------------------------------------------------------

#: How a built-in cases file is named in the API: ``builtin:<folder>/cases.yaml``.
BUILTIN_PREFIX = "builtin:"


def builtin_cases_root(library: Path | None = None) -> Path | None:
    """Where the built-in models' manual cases live (#1352): packaged in the wheel as
    ``simubench/library/model_cases`` (hatch_build.py), else the checkout's ``tests/models``.
    ``library`` stands for this package's ``library`` folder (a test points it elsewhere, #1377)."""
    library = library if library is not None else Path(__file__).parent
    packaged = library / "model_cases"
    if packaged.is_dir():
        return packaged
    checkout = library.resolve().parents[2] / "tests" / "models"
    return checkout if checkout.is_dir() else None


def _builtin_index(root: Path) -> list[tuple[str, str, int]]:
    """(folder, model, case count) of every manual cases file under ``root``; cached per file state."""
    files = sorted(root.glob("*/cases.yaml"))
    key = tuple((p.as_posix(), p.stat().st_mtime_ns) for p in files)
    with _INDEX_LOCK:  # concurrent requests build the index once (#1377)
        return _index_for(files, key)


def _index_for(files: list[Path], key: tuple) -> list[tuple[str, str, int]]:
    if _INDEX_CACHE.get("key") != key:
        index: list[tuple[str, str, int]] = []
        for path in files:
            try:
                cases = load_cases(path)
            except Exception as exc:  # a broken file: the case runner reports it when run
                log.debug("built-in cases %s skipped: %s", path, exc)
                continue
            index.append((path.parent.name, cases.model or path.parent.name, len(cases.cases)))
        _INDEX_CACHE.update(key=key, index=index)
    return _INDEX_CACHE["index"]


_INDEX_CACHE: dict[str, Any] = {}
_INDEX_LOCK = threading.Lock()


def builtin_cases_for_model(def_id: str, root: Path | None = None) -> list[tuple[str, int]]:
    """The manual cases shipped with SimuBench for ``def_id``, as ``builtin:<folder>/cases.yaml``
    with their case counts (#1352)."""
    root = root if root is not None else builtin_cases_root()
    if root is None:
        return []
    return [
        (f"{BUILTIN_PREFIX}{folder}/cases.yaml", n)
        for folder, model, n in _builtin_index(root)
        if model == def_id
    ]


def builtin_cases_file(name: str, def_id: str | None = None, root: Path | None = None) -> Path:
    """The file a ``builtin:<folder>/cases.yaml`` name stands for: ``def_id``'s own, or any model's
    when ``def_id`` is None (an agent seam runs a built-in name without a definition in context,
    #1485).

    Anything else — another model's file, a path out of the folder — is an :class:`Invalid`: the
    caller typed the name, so it is a user-input error, not a broken file (#1394)."""
    root = root if root is not None else builtin_cases_root()
    folder = name.removeprefix(BUILTIN_PREFIX).removesuffix("/cases.yaml")
    if (
        root is None
        or not name.startswith(BUILTIN_PREFIX)
        or not folder
        or "/" in folder
        or "\\" in folder
        or folder in {".", ".."}
    ):
        raise Invalid(f"not a built-in cases file: {name}")
    owners = {(f, m) for f, m, _ in _builtin_index(root)}
    if def_id is None:
        if folder not in {f for f, _ in owners}:
            raise Invalid(f"not a built-in cases file: {name}")
    elif (folder, def_id) not in owners:
        raise Invalid(f"{name} is not a cases file of {def_id}")
    return root / folder / "cases.yaml"
