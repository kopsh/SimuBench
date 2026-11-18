"""Instrument templates (instrument-authoring.md 7.4, D3): an agent fills in a model's data sheet, a
template generates the definition.

A template is a generating function plus a JSON Schema of its inputs (pydantic models with Chinese
and English labels, units and descriptions); there is no expression language. Expanding gives an
ordinary definition that records ``template: {id, version, inputs}``; loading and running ignore
that record. Diagnostics on the inputs point under ``/template/inputs``; the expanded definition
then goes through the usual static checks.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from ...engine.validate import Diagnostic
from ...errors import Unknown

INPUTS = "/template/inputs"


def pointer(*path: str | int) -> str:
    """The JSON pointer of an input field, e.g. ``pointer("vertical", "table", 2, "v_div")``."""
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in path]
    return "/".join([INPUTS, *parts]) if parts else INPUTS


@dataclass
class InputProblem:
    """A problem with the inputs that their schema cannot express (order, coverage, references)."""

    code: str
    path: tuple[str | int, ...]
    message: str
    suggestion: str | None = None
    #: "warning": legal inputs that are probably a slip (they expand, with the warning); "error"
    #: stops the expansion.
    severity: Literal["error", "warning"] = "error"


@dataclass
class Filled:
    """Where a pre-filled input came from: a line of the script, or a default to confirm."""

    pointer: str  # under /template/inputs
    value: Any
    source: str  # "script" | "default"
    line: int | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {"pointer": self.pointer, "value": self.value, "source": self.source}
        if self.line is not None:
            out["line"] = self.line
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class Expansion:
    """What expanding a template gave: the definition (None when the inputs have errors) and the
    diagnostics, those on the inputs first."""

    definition: dict[str, Any] | None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.definition is not None and not any(d.severity == "error" for d in self.diagnostics)


@dataclass(frozen=True)
class Mnemonic:
    """A spelling many models share for one of the template's settings or operations, for drafts
    from a script whose commands no library model knows (D81: it binds one command, it does not
    pick a vendor). ``pattern`` is matched (in full) against the header as the draft normalises it:
    upper case, a channel number as ``#``, no leading colon or trailing ``?``. ``bind`` is the
    command's binding, or a function of the match that returns it (None: not this one)."""

    pattern: str
    bind: dict[str, Any] | Callable[[re.Match[str]], dict[str, Any] | None]


@dataclass(frozen=True)
class Drafting:
    """A template's part in drafting a definition from a script (#145, D81). The draft learns each
    command's binding from a library model that accepts the same header, else from a mnemonic, else
    keeps the command as a setting that only stores its value (to confirm); these hooks fill in the
    data-sheet inputs around that."""

    #: generic inputs a data sheet would replace, without commands
    base: Callable[[], dict[str, Any]]
    #: (inputs, the script's commands, hints) -> where the identity and channel count came from
    basics: Callable[[dict[str, Any], list[Any], dict[str, Any]], list[Filled]]
    mnemonics: list[Mnemonic]
    #: (inputs, a setting path or "comp.op" a command binds, the library model it was learned from)
    #: -> the inputs it switched on or changed so the definition has it as that model does
    enable: Callable[[dict[str, Any], str, Any], list[Filled]]
    #: (inputs, bound setting -> [(arguments, line)]) -> data-sheet values read from the arguments
    refine: Callable[[dict[str, Any], dict[str, list[tuple[list[str], int]]]], list[Filled]]
    #: (inputs, the library model the script talks most like) -> data-sheet placeholders taken from
    #: it instead of generic ones (still to confirm)
    resemble: Callable[[dict[str, Any], Any], list[Filled]] = lambda _inputs, _model: []


@dataclass(frozen=True)
class Template:
    id: str
    version: int
    category: str  # the instrument class it generates, e.g. oscilloscope
    title: str
    title_en: str
    description: str
    inputs: type[BaseModel]
    example: Callable[[], dict[str, Any]]
    #: the definition (without id, template record) from validated inputs
    build: Callable[[Any], dict[str, Any]]
    #: problems the schema cannot express
    check: Callable[[Any], list[InputProblem]] = lambda _inputs: []
    #: how a draft from a script fills this template in (#145, D81); None: no drafting
    drafting: Drafting | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "category": self.category,
            "title": self.title,
            "title_en": self.title_en,
            "description": self.description,
        }

    def schema(self) -> dict[str, Any]:
        schema = self.inputs.model_json_schema()
        schema["title"] = self.title
        schema["title_en"] = self.title_en
        return schema

    def expand(self, inputs: dict[str, Any], definition_id: str) -> Expansion:
        """Expand ``inputs`` into a definition with id ``definition_id`` and check it."""
        try:
            model = self.inputs.model_validate(inputs)
        except ValidationError as exc:
            return Expansion(None, [_from_pydantic(e) for e in exc.errors()])
        problems = [
            Diagnostic(
                severity=p.severity,
                code=p.code,
                pointer=pointer(*p.path),
                message=p.message,
                suggestion=p.suggestion,
            )
            for p in self.check(model)
        ]
        if any(p.severity == "error" for p in problems):
            return Expansion(None, problems)
        data = self.build(model)
        data = {"schema": "simubench.instrument/2", "id": definition_id, **data}
        data["template"] = {"id": self.id, "version": self.version, "inputs": inputs}
        from ...engine.validate_definition import validate_definition_data

        _, report = validate_definition_data(data)
        return Expansion(data, problems + list(report.diagnostics))


def _from_pydantic(error: Any) -> Diagnostic:
    loc = tuple(p for p in error.get("loc", ()) if not (isinstance(p, str) and p.startswith("function-")))
    kind = str(error.get("type", ""))
    code = "E-TPL-INPUT-MISSING" if kind == "missing" else "E-TPL-INPUT"
    return Diagnostic(
        severity="error", code=code, pointer=pointer(*loc), message=str(error.get("msg", "invalid input"))
    )


_TEMPLATES: dict[str, Template] = {}


def register(template: Template) -> Template:
    _TEMPLATES[template.id] = template
    return template


def templates() -> list[Template]:
    _load()
    return sorted(_TEMPLATES.values(), key=lambda t: t.id)


def get_template(template_id: str) -> Template:
    _load()
    try:
        return _TEMPLATES[template_id]
    except KeyError:
        known = ", ".join(sorted(_TEMPLATES))
        raise Unknown(f"没有模板 {template_id!r}（已有：{known}）") from None


def _load() -> None:
    from . import oscilloscope, signal_source  # noqa: F401  (they register themselves)
