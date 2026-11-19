"""Static checks of instrument definition files (instrument-authoring.md 5.1, issue 40).

A definition that loads (the JSON Schema accepts it) can still name a component type that does
not exist, an operation the component does not have or a state variable that is not declared; the
instrument would only fail when a session starts, or when the command is first sent. These checks
find such mistakes in the file, with the same diagnostics as bench validation (code, JSON pointer,
message, suggestion), so that people and agents correct a definition before running it (D9).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ...models.definition import InstrumentDefinition
from ...models.macros import expand_macros
from ...models.migrate import FormatError, upgrade
from ...models.same_as import SameAsError, resolve_same_as
from ..component import component_types
from ..validate import Report, _ptr
from .cmds import _check_command, _check_offset_convention, _check_overlaps, _check_syntax
from .lookups import _protocol_gaps as _protocol_gaps  # re-exported: a test reads it
from .lookups import operations as operations  # re-exported: the definition's operations (D50)
from .panel import _check_panel
from .parts import _check_component_refs, _check_components, _check_deviations, _check_options
from .states import _check_constraints, _check_initial_values, _check_words
from .wires import _check_wiring


def validate_definition_data(data: Any) -> tuple[InstrumentDefinition | None, Report]:
    """Validate raw definition data (parsed YAML/JSON); return the model when the schema accepts it."""
    report = Report()
    try:
        # An explicit upgrade (issue 641): a broken schema must map to E-FORMAT here, before any
        # model exists; the macros below expand the upgraded dict.
        data, _ = upgrade(data, "instrument")
    except FormatError as exc:
        report.add("error", "E-FORMAT", "/schema", str(exc), None)
        return None, report
    try:
        data = resolve_same_as(data) if isinstance(data, dict) else data  # one value in one place (D82)
    except SameAsError as exc:
        report.add("error", exc.code, exc.pointer, str(exc), None)
        return None, report
    try:
        data = expand_macros(data)
    except ValueError as exc:
        report.add("error", "E-DEF-MACRO", "/components", str(exc), "把模块逐个写出")
        return None, report
    try:
        definition = InstrumentDefinition.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = [p for p in err["loc"] if not (isinstance(p, str) and p.startswith("function-"))]
            report.add("error", "E-SCHEMA", _ptr(*loc), err["msg"], None)
        return None, report
    validate_definition(definition, report)
    return definition, report


def validate_definition(definition: InstrumentDefinition, report: Report | None = None) -> Report:
    report = report or Report()
    d = definition
    known = component_types()
    classes = _check_components(d, known, report)
    _check_component_refs(d, known, report)
    _check_offset_convention(d, known, report)
    _check_deviations(d, known, report)
    _check_options(d, known, report)
    _check_initial_values(d, report)
    _check_constraints(d, report)
    _check_words(d, known, report)
    _check_wiring(d, classes, report)
    compiled = [_check_command(d, i, cmd, classes, report) for i, cmd in enumerate(d.commands)]
    _check_overlaps(d, compiled, report)
    _check_syntax(d, report)
    _check_panel(d, report)
    return report
