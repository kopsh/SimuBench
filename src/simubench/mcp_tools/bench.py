"""Tools for the catalog, bench files, cases and trying a definition (build)."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ..engine.record import jsonable
from ..models import tools as T
from ..server.catalog import components as component_catalog
from ..server.errors import DOMAIN_ERRORS
from ..server.projections import definition_spec, run_cases_file, try_commands
from .context import McpContext, tool_input


def _catalog_tools(mcp: MCPServer, ctx: McpContext) -> None:
    files = ctx.files
    guard = ctx.guard

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


def _definition_and_bench_list_tools(mcp: MCPServer, ctx: McpContext) -> None:
    manager = ctx.manager
    files = ctx.files
    guard = ctx.guard
    fail = ctx.fail

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
            "state": [definition_spec(k, s) for k, s in d.state.items()],
            "channel_state": [definition_spec(f"ch<n>.{k}", s) for k, s in d.channel_state.items()],
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


def _bench_file_tools(mcp: MCPServer, ctx: McpContext) -> None:
    files = ctx.files
    guard = ctx.guard
    fail = ctx.fail

    @mcp.tool()
    def read_bench(path: str) -> dict[str, Any]:
        """A bench file's content, its sha256 (needed to edit it) and its diagnostics."""
        guard()
        try:
            return files.read(path).model_dump(mode="json")
        except DOMAIN_ERRORS as exc:
            raise fail(exc) from None

    @mcp.tool()
    @tool_input(T.CreateBench)
    def create_bench(req: T.CreateBench) -> dict[str, Any]:
        """Create an empty bench file; fill it with edit_bench."""
        guard()
        data: dict[str, Any] = {"schema": "simubench.bench/2", "name": req.name}
        if req.description:
            data["description"] = req.description
        try:
            return files.save(req.path, None, data, "agent").model_dump(mode="json")
        except DOMAIN_ERRORS as exc:
            raise fail(exc) from None

    @mcp.tool()
    @tool_input(T.McpBenchEdit)
    def edit_bench(req: T.McpBenchEdit) -> dict[str, Any]:
        """Edit a bench file: add instruments and parts, mark DUTs (set_dut), connect ports (with accessories such as
        probes), set initial state and part parameters, rename, remove, move. Returns the new
        content, sha256 and diagnostics."""
        guard()
        try:
            return files.edit(req.path, req.base_sha256, req.ops, "agent").model_dump(mode="json")
        except DOMAIN_ERRORS as exc:
            raise fail(exc) from None

    @mcp.tool()
    def list_bench_history(path: str) -> list[dict[str, Any]]:
        """Saved versions of a bench file, newest first: who wrote each (ui, agent, external,
        restore) and when. Read one with read_bench_version."""
        guard()
        try:
            return [v.model_dump(mode="json") for v in files.versions(path)]
        except DOMAIN_ERRORS as exc:
            raise fail(exc) from None

    @mcp.tool()
    def read_bench_version(path: str, seq: int) -> dict[str, Any]:
        """The content of one saved version of a bench file."""
        guard()
        try:
            return files.version(path, seq).model_dump(mode="json")
        except DOMAIN_ERRORS as exc:
            raise fail(exc) from None


def _validation_tools(mcp: MCPServer, ctx: McpContext) -> None:
    files = ctx.files
    guard = ctx.guard
    fail = ctx.fail

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
        except DOMAIN_ERRORS as exc:
            raise fail(exc) from None
        return {"ok": report.ok, "diagnostics": [d.model_dump() for d in report.diagnostics]}

    @mcp.tool()
    @tool_input(T.RunCases)
    def run_cases(req: T.RunCases) -> dict[str, Any]:
        """Run a cases file: each case on a freshly powered bench, every step's reply checked against
        its expect / expect_prefix / expect_value(s) / expect_fields / expect_format / expect_block
        (no regular expressions, D49). A failure names the step, the command, what was
        expected and the actual reply. The same files as `simubench cases` and the manual cases of
        real models."""
        guard()
        try:
            results = run_cases_file(files, req.path)
        except DOMAIN_ERRORS as exc:
            raise fail(exc) from None
        return {
            "path": req.path,
            "passed": sum(r.passed for r in results),
            "failed": sum(not r.passed for r in results),
            "cases": [r.as_dict() | {"summary": r.describe()} for r in results],
        }


def _try_tools(mcp: MCPServer, ctx: McpContext) -> None:
    manager = ctx.manager
    guard = ctx.guard
    fail = ctx.fail

    @mcp.tool()
    @tool_input(T.McpTryDefinition)
    async def try_definition(req: T.McpTryDefinition) -> dict[str, Any]:
        """Send commands to a definition's instrument alone, without a bench: the temporary session
        comes from the same scratch pool the acceptance page uses (no SCPI port is bound, no run
        record is written, the engineer's open bench is untouched) and stays open for reuse until
        it falls idle. Each result carries the reply, the errors the command added to the queue
        and the virtual time, as send_scpi returns them. Commands marked dangerous in the
        definition run without confirm: the simulation is the sandbox."""
        guard()
        try:
            results = await try_commands(manager, req.definition_id, req.commands, client="mcp")
        except DOMAIN_ERRORS as exc:
            raise fail(exc) from None
        return {"definition": req.definition_id, "instrument": req.definition_id, "results": results}


def register(mcp: MCPServer, ctx: McpContext) -> None:
    _catalog_tools(mcp, ctx)
    _definition_and_bench_list_tools(mcp, ctx)
    _bench_file_tools(mcp, ctx)
    _validation_tools(mcp, ctx)
    _try_tools(mcp, ctx)
