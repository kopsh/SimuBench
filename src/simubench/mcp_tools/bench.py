"""Tools for the catalog, bench files, cases and trying a definition (build)."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ..engine.edit import EditError, EditOp
from ..engine.record import jsonable
from ..server.benchfiles import Conflict
from ..server.catalog import components as component_catalog
from .context import McpContext, scpi_payload


def register(mcp: MCPServer, ctx: McpContext) -> None:
    manager = ctx.manager
    files = ctx.files
    guard = ctx.guard
    fail = ctx.fail

    @mcp.tool()
    def list_catalog() -> dict[str, Any]:
        """Instrument definitions (id, class, channels, ports) and part / accessory component types
        (ports, default parameters, units) that can be placed on a bench."""
        guard()
        return files.catalog().model_dump(mode="json")

    @mcp.tool()
    def list_components(
        types: list[str] | None = Field(None, description="Component types to describe in full"),
        category: Literal["part", "accessory", "internal"] | None = None,
    ) -> list[dict[str, Any]]:
        """Component types for instrument definitions, internal ones included: ports, circuit role,
        parameters (default, unit, description; a value may be bound to a setting with '$path') and
        operations (what a command's op: <component>.<name> calls, with its parameters).

        Without ``types`` each entry is a summary (first line of the description, parameter and
        operation names); name the types you use to get every field. ``generic: false`` marks a
        component written for one model: a new definition uses generic components only."""
        guard()
        out = []
        for c in component_catalog():
            if category and c.category != category:
                continue
            if types is not None:
                if c.type in types:
                    out.append(c.model_dump(mode="json"))
                continue
            out.append(
                {
                    "type": c.type,
                    "category": c.category,
                    "role": c.role,
                    "generic": c.generic,
                    "doc": (c.doc or "").split("\n", 1)[0] or None,
                    "ports": [p.name for p in c.ports] + ([f"/{c.port_pattern}/"] if c.port_pattern else []),
                    "params": [p.name for p in c.params],
                    "operations": [o.name for o in c.operations],
                }
            )
        if types is not None:
            unknown = sorted(set(types) - {c["type"] for c in out})
            if unknown:
                raise ToolError(
                    f"没有组件类型 {', '.join(unknown)}；不带 types 调用 list_components 查看全部"
                )
        return out

    @mcp.tool()
    def get_definition(
        definition_id: str,
        offset: int = Field(0, ge=0, description="First command to list"),
        limit: int = Field(40, ge=1, le=200, description="Commands per page"),
    ) -> dict[str, Any]:
        """One instrument definition: state variables (with units, ranges, options), its components
        (list_components describes their types), internal wiring and exposed ports, and its SCPI
        commands, paged. Header patterns use SCPI notation: upper case is the short form,
        [optional] nodes, <ch> a channel suffix."""
        guard()
        try:
            d = files.library(None).get(definition_id)
        except KeyError as exc:
            raise fail(exc) from None

        def spec(path: str, s: Any) -> dict[str, Any]:
            out = {"path": path, "type": s.type}
            for key in ("unit", "min", "max", "options", "default", "label", "label_en", "readonly"):
                v = getattr(s, key)
                if v not in (None, False):
                    out[key] = jsonable(v)
            return out

        commands = [
            {
                k: v
                for k, v in {
                    "header": c.header,
                    "set": c.set,
                    "query": c.query,
                    "state": c.state,
                    "op": c.op,
                    "params": [p.model_dump(exclude_defaults=True) for p in c.params] or None,
                    "dangerous": c.dangerous or None,
                    "doc": c.doc,
                }.items()
                if v is not None
            }
            for c in d.commands
        ]
        return {
            "id": d.id,
            "manufacturer": d.manufacturer,
            "model": d.model,
            "class": d.instrument_class,
            "description": d.description,
            "channels": d.channels.count,
            "channel_ports": list(d.ports.channel),
            "instrument_ports": list(d.ports.instrument),
            "state": [spec(k, s) for k, s in d.state.items()],
            "channel_state": [spec(f"ch<n>.{k}", s) for k, s in d.channel_state.items()],
            "components": [jsonable(c.model_dump()) for c in d.components],
            "channel_components": [jsonable(c.model_dump()) for c in d.channel_components],
            "wiring": d.wiring.model_dump(),
            "ports": {"channel": dict(d.ports.channel), "instrument": dict(d.ports.instrument)},
            "commands": commands[offset : offset + limit],
            "commands_total": len(commands),
            "next_offset": offset + limit if offset + limit < len(commands) else None,
        }

    @mcp.tool()
    def list_benches() -> list[dict[str, Any]]:
        """Bench files in the workspace."""
        guard()
        return [b.model_dump(mode="json") for b in manager.bench_files()]

    @mcp.tool()
    def read_bench(path: str) -> dict[str, Any]:
        """A bench file's content, its sha256 (needed to edit it) and its diagnostics."""
        guard()
        try:
            return files.read(path).model_dump(mode="json")
        except Exception as exc:
            raise fail(exc) from None

    @mcp.tool()
    def create_bench(
        path: str = Field(..., description="Workspace-relative, ending in .bench.yaml"),
        name: str = Field(..., description="Bench name"),
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create an empty bench file; fill it with edit_bench."""
        guard()
        data: dict[str, Any] = {"schema": "simubench.bench/2", "name": name}
        if description:
            data["description"] = description
        try:
            return files.save(path, None, data, "agent").model_dump(mode="json")
        except Exception as exc:
            raise fail(exc) from None

    @mcp.tool()
    def edit_bench(
        path: str,
        base_sha256: str = Field(..., description="sha256 from the last read_bench / edit_bench"),
        ops: list[EditOp] = Field(..., description="Applied in order, all or nothing"),
    ) -> dict[str, Any]:
        """Edit a bench file: add instruments and parts, mark DUTs (set_dut), connect ports (with accessories such as
        probes), set initial state and part parameters, rename, remove, move. Returns the new
        content, sha256 and diagnostics."""
        guard()
        try:
            return files.edit(path, base_sha256, ops, "agent").model_dump(mode="json")
        except (Conflict, EditError, PermissionError, FileNotFoundError, ValueError) as exc:
            raise fail(exc) from None

    @mcp.tool()
    def list_bench_history(path: str) -> list[dict[str, Any]]:
        """Saved versions of a bench file, newest first: who wrote each (ui, agent, external,
        restore) and when. Read one with read_bench_version."""
        try:
            return [v.model_dump(mode="json") for v in files.versions(path)]
        except Exception as exc:
            raise fail(exc) from None

    @mcp.tool()
    def read_bench_version(path: str, seq: int) -> dict[str, Any]:
        """The content of one saved version of a bench file."""
        try:
            return files.version(path, seq).model_dump(mode="json")
        except Exception as exc:
            raise fail(exc) from None

    @mcp.tool()
    def validate_bench(
        path: str | None = Field(None, description="Bench file to check"),
        data: dict[str, Any] | None = Field(None, description="Or unsaved bench content"),
    ) -> dict[str, Any]:
        """Check a bench and return diagnostics: code, JSON pointer, message and suggestion."""
        guard()
        try:
            if data is None:
                if not path:
                    raise ToolError("给出 path 或 data")
                doc = files.read(path)
                return {"ok": doc.ok, "diagnostics": [d.model_dump() for d in doc.diagnostics]}
            report = files.validate(data, path)
        except ToolError:
            raise
        except Exception as exc:
            raise fail(exc) from None
        return {"ok": report.ok, "diagnostics": [d.model_dump() for d in report.diagnostics]}

    @mcp.tool()
    def run_cases(
        path: str = Field(..., description="Workspace-relative cases file (commands and expected replies)"),
    ) -> dict[str, Any]:
        """Run a cases file: each case on a freshly powered bench, every step's reply checked against
        its expect / expect_match / expect_value. A failure names the step, the command, what was
        expected and the actual reply. The same files as `simubench cases` and the manual cases of
        real models."""
        guard()
        from ..library.coverage import run_cases as run

        try:
            cases_file = files.resolve(path)
            results = run(cases_file, [files.workspace / "instruments"])
        except Exception as exc:
            raise fail(exc) from None
        return {
            "path": path,
            "passed": sum(r.passed for r in results),
            "failed": sum(not r.passed for r in results),
            "cases": [r.as_dict() | {"summary": r.describe()} for r in results],
        }

    @mcp.tool()
    async def try_definition(
        definition_id: str = Field(..., description="The definition to try (list_catalog has the ids)"),
        commands: list[str] = Field(
            ..., description="Program messages, sent in order to the instrument alone", max_length=200
        ),
    ) -> dict[str, Any]:
        """Send commands to a definition's instrument alone, without a bench: the temporary session
        comes from the same scratch pool the acceptance page uses (no SCPI port is bound, no run
        record is written, the engineer's open bench is untouched) and stays open for reuse until
        it falls idle. Each result carries the reply, the errors the command added to the queue
        and the virtual time, as send_scpi returns them. Commands marked dangerous in the
        definition run without confirm: the simulation is the sandbox."""
        guard()

        def run() -> list[dict[str, Any]]:
            # The pool owns the try semantics (no ports, no record, scratch bench) and the reuse:
            # a second batch for the same definition runs on the same session instead of cycling
            # a bench per call (issue 636).
            # unknown id: KeyError before anything builds
            with manager.tries.using(definition_id) as entry:
                return [
                    {
                        "command": command,
                        **scpi_payload(entry.served.session, definition_id, command, client="mcp"),
                    }
                    for command in commands
                ]

        try:
            results = await asyncio.to_thread(run)
        except Exception as exc:
            raise fail(exc) from None
        return {"definition": definition_id, "instrument": definition_id, "results": results}
