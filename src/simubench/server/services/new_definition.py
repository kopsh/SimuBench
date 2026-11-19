"""A new workspace definition from a template's inputs or blank (P2-5): the one path the Web's
"new instrument" and the agent's ``create_definition_from_template`` share. The template's own
schema and ``check`` judge the inputs (D3 layer 2); nothing is written while they have errors, and
the warnings ride along with the saved definition's diagnostics."""

from __future__ import annotations

from typing import Any

from ...contracts import api as M
from ...errors import Invalid
from ...library.templates import get_template
from ..definitionfiles import DefinitionFiles
from ..errors import NotFound
from ..history import Source


def templates() -> list[M.InstrumentTemplateInfo]:
    from ...library.templates import templates as all_templates

    return [
        M.InstrumentTemplateInfo(**t.summary(), schema=t.schema(), example=t.example())  # type: ignore[arg-type]
        for t in all_templates()
    ]


def from_template(
    definitions: DefinitionFiles,
    template_id: str,
    definition_id: str,
    inputs: dict[str, Any],
    source: Source,
    base_sha256: str | None = None,
) -> M.NewDefinitionResult:
    """Expand and save (create, or with ``base_sha256`` overwrite); the inputs' diagnostics first."""
    try:
        template = get_template(template_id)
    except KeyError as exc:
        raise NotFound(str(exc.args[0])) from None
    out = template.expand(inputs, definition_id)
    if out.definition is None:
        return M.NewDefinitionResult(saved=False, diagnostics=out.diagnostics)
    if base_sha256 is None:
        doc = definitions.create(definition_id, out.definition, source)
    else:
        doc = definitions.save(definition_id, base_sha256, out.definition, source)
    warned = [d for d in out.diagnostics if d.pointer.startswith("/template/inputs")]
    return M.NewDefinitionResult(saved=True, document=doc, diagnostics=warned + list(doc.diagnostics))


def create(
    definitions: DefinitionFiles, req: M.NewDefinitionRequest, source: Source
) -> M.NewDefinitionResult:
    if req.template_id:
        return from_template(definitions, req.template_id, req.definition_id, req.inputs or {}, source)
    if not (req.instrument_class and req.manufacturer and req.model):
        raise Invalid("a blank instrument needs its class, manufacturer and model (or give a template)")
    data = {"manufacturer": req.manufacturer, "model": req.model, "class": req.instrument_class}
    doc = definitions.create(req.definition_id, data, source)
    return M.NewDefinitionResult(saved=True, document=doc, diagnostics=list(doc.diagnostics))
