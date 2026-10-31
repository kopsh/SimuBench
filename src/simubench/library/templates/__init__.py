"""Instrument templates (instrument-authoring.md 7.4, D3): an agent fills in a model's data sheet, a
template generates the definition.

A template is a generating function plus a JSON Schema of its inputs (pydantic models with Chinese
and English labels, units and descriptions); there is no expression language. Expanding gives an
ordinary definition that records ``template: {id, version, inputs}``; loading and running ignore
that record. Diagnostics on the inputs point under ``/template/inputs``; the expanded definition
then goes through the usual static checks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ...engine.validate import Diagnostic

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
            Diagnostic("error", p.code, pointer(*p.path), p.message, p.suggestion) for p in self.check(model)
        ]
        if problems:
            return Expansion(None, problems)
        data = self.build(model)
        data = {"schema": "simubench.instrument/1", "id": definition_id, **data}
        data["template"] = {"id": self.id, "version": self.version, "inputs": inputs}
        from ...engine.validate_definition import validate_definition_data

        _, report = validate_definition_data(data)
        return Expansion(data, list(report.diagnostics))


def _from_pydantic(error: Any) -> Diagnostic:
    loc = tuple(p for p in error.get("loc", ()) if not (isinstance(p, str) and p.startswith("function-")))
    kind = str(error.get("type", ""))
    code = "E-TPL-INPUT-MISSING" if kind == "missing" else "E-TPL-INPUT"
    return Diagnostic("error", code, pointer(*loc), str(error.get("msg", "invalid input")))


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
        raise KeyError(f"没有模板 {template_id!r}（已有：{known}）") from None


def _load() -> None:
    from . import oscilloscope, signal_source  # noqa: F401  (they register themselves)
