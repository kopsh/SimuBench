"""Tools for instrument definitions and templates (instrument-authoring.md 5.4)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ..engine.jsonpatch import PatchError, PatchOp
from ..engine.scriptscan import draft_bench, draft_definition
from ..library.templates import get_template as template_by_id
from ..library.templates import templates as instrument_templates
from ..library.templates.draft import suggester as template_suggester
from ..server.definitionfiles import DefinitionFiles
from .context import McpContext

log = logging.getLogger(__name__)


def _pointer_diff(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """The entries where two definitions differ, as JSON pointers under their top-level member
    (issue 169): `/commands/12`, `/channel_state/amplitude_unit` — entries, not deep paths."""
    out: list[str] = []
    for member in sorted(set(expected) | set(actual)):
        e, a = expected.get(member), actual.get(member)
        if e == a:
            continue
        if isinstance(e, list) and isinstance(a, list):
            for i in range(max(len(e), len(a))):
                ei = e[i] if i < len(e) else None
                ai = a[i] if i < len(a) else None
                if ei != ai:
                    out.append(f"/{member}/{i}")
        elif isinstance(e, dict) and isinstance(a, dict):
            out += [f"/{member}/{k}" for k in sorted(set(e) | set(a)) if e.get(k) != a.get(k)]
        else:
            out.append(f"/{member}")
    return out


def _refuse_lost_edits(definitions: DefinitionFiles, t: Any, definition_id: str, discard: bool) -> None:
    """Re-expanding a template must not silently drop what was drafted or edited into the file
    afterwards (issue 169): expand the recorded inputs again and compare with the file."""
    if discard:
        return
    doc = definitions.read(definition_id)
    recorded = doc.data.get("template")
    if (
        not isinstance(recorded, dict)
        or recorded.get("id") != t.id
        or not isinstance(recorded.get("inputs"), dict)
    ):
        return  # no template record, or a different template: re-expanding replaces it as before
    again = t.expand(recorded["inputs"], definition_id)
    if again.definition is None:
        return  # the recorded inputs no longer expand (the template moved on); the new inputs decide
    current = {k: v for k, v in doc.data.items() if k != "template"}
    diffs = _pointer_diff({k: v for k, v in again.definition.items() if k != "template"}, current)
    if diffs:
        shown = ", ".join(diffs[:8]) + (" …" if len(diffs) > 8 else "")
        raise ToolError(
            f"{definition_id} 在模板之外被改过（{shown}）：重新展开会丢弃这些改动。"
            "把改动并入模板输入，或者确认丢弃（discard_edits）。"
        )


def register(mcp: MCPServer, ctx: McpContext) -> None:
    files = ctx.files
    definitions = ctx.definitions
    guard = ctx.guard
    fail = ctx.fail
    definition_out = ctx.definition_out

    @mcp.tool()
    def read_definition(definition_id: str) -> dict[str, Any]:
        """An instrument definition file as stored: its data, sha256 (pass it to edit_definition) and
        static-check diagnostics. Only definitions in the workspace's instruments/ are editable."""
        guard()
        try:
            return definition_out(definitions.read(definition_id))
        except Exception as exc:
            raise fail(exc) from None

    @mcp.tool()
    def create_definition(
        definition_id: str = Field(..., description="The new id; the file is instruments/<id>.yaml"),
        data: dict[str, Any] = Field(..., description="The definition (simubench.instrument/1)"),
    ) -> dict[str, Any]:
        """Write a new instrument definition into the workspace's instruments/ (never code: the
        building blocks are list_components). The result reports what was created (changes, in the
        instrument's language). The file is kept even when it does not pass the checks yet; fix what
        the diagnostics point at with edit_definition."""
        guard()
        try:
            return definition_out(definitions.create(definition_id, data, "agent"))
        except Exception as exc:
            raise fail(exc) from None

    @mcp.tool()
    def edit_definition(
        definition_id: str,
        base_sha256: str = Field(..., description="sha256 from read_definition or the last edit"),
        patch: list[PatchOp] = Field(..., description="JSON Patch (RFC 6902) operations, applied in order"),
    ) -> dict[str, Any]:
        """Edit a workspace definition with JSON Patch, e.g. {"op": "replace", "path":
        "/channel_state/scale/max", "value": 10}. The result reports what changed (changes, in the
        instrument's language — report that to the engineer). Every save enters the version history
        (the Web acceptance page shows it with these descriptions). Sessions that use the definition
        keep the old one until restarted (sessions_to_restart)."""
        guard()
        try:
            return definition_out(definitions.edit(definition_id, base_sha256, patch, "agent"))
        except PatchError as exc:
            raise ToolError(f"补丁没有应用：{exc}") from None
        except Exception as exc:
            raise fail(exc) from None

    @mcp.tool()
    def validate_definition(
        definition_id: str | None = Field(None, description="A definition by id"),
        data: dict[str, Any] | None = Field(None, description="Or a definition's content, not saved"),
    ) -> dict[str, Any]:
        """Static checks of an instrument definition: schema, component types and parameters, setting
        bindings, operations, wiring and ports, headers (codes E-DEF-*, pointer, suggestion)."""
        guard()
        if (definition_id is None) == (data is None):
            raise ToolError("给出 definition_id 或 data 之一")
        try:
            content = data if data is not None else definitions.read(definition_id).data  # type: ignore[arg-type]
        except Exception as exc:
            raise fail(exc) from None
        report = definitions.validate(content)
        return {"ok": report.ok, "diagnostics": [d.model_dump() for d in report.diagnostics]}

    @mcp.tool()
    def draft_definition_from_script(
        script_path: str = Field(..., description="Workspace-relative path of a Python + PyVISA script"),
        definition_id: str = Field(..., description="Id of the drafted definition"),
        receiver: str | None = Field(None, description="Which instrument of the script, e.g. 'scope'"),
        base_definition: str | None = Field(
            None, description="Start from this definition and draft only what it does not support"
        ),
        save: bool = Field(False, description="Also create instruments/<definition_id>.yaml"),
        base_sha256: str | None = Field(
            None,
            description="With save: extend the definition in place (base_definition = definition_id)"
            " — the sha256 you read, so the draft is saved over the file instead of created",
        ),
    ) -> dict[str, Any]:
        """Read a test program without running it and draft an instrument definition for one of its
        instruments: a setting and a command for every header the base does not support, typed from
        the values the script writes and marked 草稿（待确认）. Check them, then edit_definition — or
        extend a workspace definition in place by passing base_definition = definition_id together
        with the sha256 you read as base_sha256."""
        guard()
        try:
            script = files.resolve(script_path)
            source = script.read_text(encoding="utf-8")
            lib = files.library(script)
            base = lib.get(base_definition) if base_definition else None
            out = draft_definition(source, definition_id, receiver=receiver, base=base)
            if save and base_sha256 is None:
                out["saved"] = definition_out(definitions.create(definition_id, out["definition"], "agent"))
            elif save:
                out["saved"] = definition_out(
                    definitions.save(definition_id, base_sha256, out["definition"], "agent")
                )
        except Exception as exc:
            raise fail(exc) from None
        return out

    @mcp.tool()
    def list_templates() -> list[dict[str, Any]]:
        """Instrument templates: fill in a model's data sheet and the template generates the definition
        (components, settings, commands), so an instrument need not be built from components."""
        guard()
        return [t.summary() for t in instrument_templates()]

    @mcp.tool()
    def get_template(template_id: str) -> dict[str, Any]:
        """A template's inputs as a JSON Schema (each field with its Chinese and English label, unit and
        description) and an example input that expands clean."""
        guard()
        try:
            t = template_by_id(template_id)
        except KeyError as exc:
            raise ToolError(str(exc.args[0])) from None
        return {**t.summary(), "schema": t.schema(), "example": t.example()}

    @mcp.tool()
    def create_definition_from_template(
        template_id: str,
        definition_id: str = Field(..., description="The new id; the file is instruments/<id>.yaml"),
        inputs: dict[str, Any] = Field(
            ..., description="The template's inputs (get_template has the schema)"
        ),
        base_sha256: str | None = Field(
            None, description="Expand again over an existing workspace definition: the sha256 you read"
        ),
        discard_edits: bool = Field(
            False,
            description="With base_sha256: overwrite what was drafted or edited into the file after"
            " the template created it. False (the default) refuses the expansion and lists those edits",
        ),
    ) -> dict[str, Any]:
        """Expand a template into a definition and save it in the workspace's instruments/ (the version
        history records the agent). The file records template: {id, version, inputs}; later changes are
        either new inputs (expand again with base_sha256) or edit_definition on the result. Diagnostics
        on the inputs point under /template/inputs and nothing is saved until they are fixed. Expanding
        again over a definition that was changed outside the template since (a drafted command, an
        edit_definition) is refused: merge those changes into the inputs, or pass discard_edits."""
        guard()
        try:
            t = template_by_id(template_id)
        except KeyError as exc:
            raise ToolError(str(exc.args[0])) from None
        out = t.expand(inputs, definition_id)
        if out.definition is None:
            return {"saved": False, "diagnostics": [d.model_dump() for d in out.diagnostics]}
        if base_sha256 is not None:
            _refuse_lost_edits(definitions, t, definition_id, discard_edits)
        try:
            if base_sha256 is None:
                doc = definitions.create(definition_id, out.definition, "agent")
            else:
                doc = definitions.save(definition_id, base_sha256, out.definition, "agent")
        except Exception as exc:
            raise fail(exc) from None
        return {"saved": True, **definition_out(doc)}

    @mcp.tool()
    def draft_bench_from_script(
        script_path: str = Field(..., description="Workspace-relative path of a Python + PyVISA script"),
        save_as: str | None = Field(None, description="Also create this bench file from the draft"),
    ) -> dict[str, Any]:
        """Read a test program without running it: find the resources it opens and the SCPI it
        sends, choose the instrument definition that supports most of each instrument's commands,
        and list the commands no model supports (with line numbers). Wiring cannot be read from a
        script; add parts and wires with edit_bench."""
        guard()
        try:
            script = files.resolve(script_path)
            source = script.read_text(encoding="utf-8")
        except Exception as exc:
            raise fail(exc) from None
        lib = files.library(script)

        def defs() -> list[Any]:
            out = []
            for i in lib.ids():
                try:
                    out.append(lib.get(i))
                except Exception as exc:  # a definition that does not load supports no command
                    log.debug("draft_bench_from_script: definition %r skipped: %s", i, exc)
                    continue
            return out

        name = Path(save_as or script.stem).name.split(".")[0]
        result = draft_bench(source, defs, name=name, suggest=template_suggester(source))
        if save_as:
            try:
                result["saved"] = files.save(save_as, None, result["bench"], "agent").model_dump(mode="json")
            except Exception as exc:
                raise fail(exc) from None
        return result
