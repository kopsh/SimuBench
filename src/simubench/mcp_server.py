"""Local MCP service: lets an AI agent build, deploy, drive and observe benches (D9, architecture.md 7.5).

The tools are thin wrappers over the same :class:`~simubench.server.manager.SessionManager` and
bench-file operations the Web Bench uses, so whatever an agent does shows up in the engineer's
browser, and agent edits land in readable bench files.

Rules the tools enforce:

* dangerous commands (outputs on, sweeps that could harm a DUT) need ``confirm=true``;
* simulation control (advancing the clock, reading true values) is off unless the service was
  started with ``--allow-control``; true values also need ``run.reveal_true_values`` in the bench;
* calls are rate limited, so a looping agent cannot flood a session.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import __version__
from .engine.commands import classify
from .engine.edit import EditError, EditOp
from .engine.faults import LinkDown
from .engine.jsonpatch import PatchError, PatchOp
from .engine.scriptscan import draft_bench, draft_definition
from .models.bench import Fault
from .server.app import to_event
from .server.benchfiles import Conflict, diagnostics
from .server.catalog import components as component_catalog
from .server.definitionfiles import DefinitionFiles
from .server.manager import BenchInvalid, ServedSession, SessionManager, _plain

INSTRUCTIONS = """\
SimuBench runs virtual instrument benches: instruments (AWG, oscilloscope, ...), DUTs and wires,
simulated with correct circuit physics. Typical flow:
1. list_catalog / get_definition to see what instruments and DUTs exist; list_components for the
   building blocks of instrument definitions (ports, parameters, operations);
2. read_bench or create_bench, then edit_bench with operations (always pass the sha256 you read);
   or draft_bench_from_script to start from an existing PyVISA test program;
3. validate_bench and fix every error the diagnostics point at (pointer + suggestion);
   run_cases checks instruments against a cases file of commands and expected replies;
   to build an instrument the library lacks: draft_definition_from_script or create_definition,
   then edit_definition (JSON Patch) / validate_definition until clean, then run_cases;
4. start_session deploys the bench: instruments answer SCPI at the returned addresses, and the
   engineer watches it in the Web Bench;
5. send_scpi / get_instrument_state / read_log to drive and observe it;
6. with --allow-control: inject_fault (timeouts, disconnects, error codes, drift…) to test how a
   script handles failures, advance_clock, read_true_values.
Edits change the bench file; a running session picks up topology changes only when restarted.
Bench files are the engineer's: explain what you change.
"""


def _fault(f: Any) -> dict[str, Any]:
    return {
        "id": f.id,
        "status": f.status,
        "description": f.describe(),
        "source": f.source,
        "started_s": f.started_ns / 1e9 if f.started_ns is not None else None,
        "ends_s": f.ends_ns / 1e9 if f.ends_ns is not None else None,
        "spec": f.spec.model_dump(exclude_none=True),
    }


class RateLimiter:
    """Token bucket shared by all tools."""

    def __init__(self, rate: float = 20.0, burst: int = 60) -> None:
        self.rate, self.burst = rate, burst
        self.tokens = float(burst)
        self.t = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
            self.t = now
            if self.tokens < 1:
                wait = (1 - self.tokens) / self.rate
                raise ToolError(f"调用过于频繁，请 {wait:.2f} 秒后重试 (rate limited)")
            self.tokens -= 1


def build_server(
    manager: SessionManager,
    *,
    allow_control: bool = False,
    ui_url: str | None = None,
    limiter: RateLimiter | None = None,
) -> MCPServer:
    mcp = MCPServer(name="simubench", version=__version__, instructions=INSTRUCTIONS)
    files = manager.files
    definitions = DefinitionFiles(files)
    limiter = limiter or RateLimiter()

    def guard() -> None:
        limiter.take()

    def fail(exc: Exception) -> ToolError:
        if isinstance(exc, Conflict):
            again = "read_definition" if "instruments/" in str(exc) else "read_bench"
            return ToolError(f"{exc}；当前 sha256 = {exc.current_sha256}，请先 {again} 再编辑")
        if isinstance(exc, BenchInvalid):
            lines = [f"{d.code} {d.pointer}: {d.message}" for d in exc.diagnostics if d.severity == "error"]
            return ToolError("测试台未通过校验，无法启动：\n" + "\n".join(lines))
        if isinstance(exc, FileNotFoundError):
            return ToolError("文件不存在")
        if isinstance(exc, KeyError):
            return ToolError(str(exc).strip("'\""))
        return ToolError(str(exc).splitlines()[0] if str(exc) else type(exc).__name__)

    def served(session_id: str | None) -> ServedSession:
        if session_id:
            try:
                return manager.get(session_id)
            except KeyError:
                raise ToolError(f"没有会话 {session_id!r}；用 list_sessions 查看") from None
        if len(manager.sessions) == 1:
            return next(iter(manager.sessions.values()))
        if not manager.sessions:
            raise ToolError("没有运行中的会话；先用 start_session 部署测试台")
        raise ToolError("有多个会话，请指定 session_id")

    def control() -> None:
        if not allow_control:
            raise ToolError("仿真控制默认关闭；工程师需以 `simubench mcp --allow-control` 启动服务")

    # ---- build ------------------------------------------------------------------------------------

    @mcp.tool()
    def list_catalog() -> dict[str, Any]:
        """Instrument definitions (id, class, channels, ports) and DUT / accessory component types
        (ports, default parameters, units) that can be placed on a bench."""
        guard()
        return files.catalog().model_dump(mode="json")

    @mcp.tool()
    def list_components(
        types: list[str] | None = Field(None, description="Component types to describe in full"),
        category: Literal["dut", "accessory", "internal"] | None = None,
    ) -> list[dict[str, Any]]:
        """Component types for instrument definitions, internal ones included: ports, circuit role,
        parameters (default, unit, description; a value may be bound to a setting with '$path') and
        operations (what a command's op: <component>.<name> calls, with its parameters).

        Without ``types`` each entry is a summary (first line of the description, parameter and
        operation names); name the types you use to get every field."""
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
                    out[key] = _plain(v)
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
            "components": [_plain(c.model_dump()) for c in d.components],
            "channel_components": [_plain(c.model_dump()) for c in d.channel_components],
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
        data: dict[str, Any] = {"schema": "simubench.bench/1", "name": name}
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
        """Edit a bench file: add instruments and DUTs, connect ports (with accessories such as
        probes), set initial state and DUT parameters, rename, remove, move. Returns the new
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
        return {"ok": report.ok, "diagnostics": [d.model_dump() for d in diagnostics(report)]}

    @mcp.tool()
    def run_cases(
        path: str = Field(..., description="Workspace-relative cases file (commands and expected replies)"),
    ) -> dict[str, Any]:
        """Run a cases file: each case on a freshly powered bench, every step's reply checked against
        its expect / expect_match / expect_value. A failure names the step, the command, what was
        expected and the actual reply. The same files as `simubench cases` and the manual cases of
        real models."""
        guard()
        from .library.coverage import run_cases as run

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

    # ---- instrument definitions (instrument-authoring.md 5.4) --------------------------------------

    def definition_out(doc: Any) -> dict[str, Any]:
        doc.data = _plain(doc.data)  # JSON has no infinity: "INF", as bench files spell it
        doc.sessions_to_restart = [
            sid for sid, sv in manager.sessions.items() if doc.id in manager.stale_definitions(sv)
        ]
        return doc.model_dump(mode="json")

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
        building blocks are list_components). The file is kept even when it does not pass the
        checks yet; fix what the diagnostics point at with edit_definition."""
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
        "/channel_state/scale/max", "value": 10}. Every save enters the version history. Sessions that
        use the definition keep the old one until restarted (sessions_to_restart)."""
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
        return {"ok": report.ok, "diagnostics": [d.as_dict() for d in report.diagnostics]}

    @mcp.tool()
    def draft_definition_from_script(
        script_path: str = Field(..., description="Workspace-relative path of a Python + PyVISA script"),
        definition_id: str = Field(..., description="Id of the drafted definition"),
        receiver: str | None = Field(None, description="Which instrument of the script, e.g. 'scope'"),
        base_definition: str | None = Field(
            None, description="Start from this definition and draft only what it does not support"
        ),
        save: bool = Field(False, description="Also create instruments/<definition_id>.yaml"),
    ) -> dict[str, Any]:
        """Read a test program without running it and draft an instrument definition for one of its
        instruments: a setting and a command for every header the base does not support, typed from
        the values the script writes and marked 草稿（待确认）. Check them, then edit_definition."""
        guard()
        try:
            script = files.resolve(script_path)
            source = script.read_text(encoding="utf-8")
            lib = files.library(script)
            base = lib.get(base_definition) if base_definition else None
            out = draft_definition(source, definition_id, receiver=receiver, base=base)
            if save:
                out["saved"] = definition_out(definitions.create(definition_id, out["definition"], "agent"))
        except Exception as exc:
            raise fail(exc) from None
        return out

    @mcp.tool()
    def draft_bench_from_script(
        script_path: str = Field(..., description="Workspace-relative path of a Python + PyVISA script"),
        save_as: str | None = Field(None, description="Also create this bench file from the draft"),
    ) -> dict[str, Any]:
        """Read a test program without running it: find the resources it opens and the SCPI it
        sends, choose the instrument definition that supports most of each instrument's commands,
        and list the commands no model supports (with line numbers). Wiring cannot be read from a
        script; add DUTs and wires with edit_bench."""
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
                except Exception:
                    continue
            return out

        name = Path(save_as or script.stem).name.split(".")[0]
        result = draft_bench(source, defs, name=name)
        if save_as:
            try:
                result["saved"] = files.save(save_as, None, result["bench"], "agent").model_dump(mode="json")
            except Exception as exc:
                raise fail(exc) from None
        return result

    # ---- deploy -----------------------------------------------------------------------------------

    def session_summary(s: ServedSession) -> dict[str, Any]:
        info = manager.describe(s)
        rel = info.bench_path
        out: dict[str, Any] = {
            "session_id": info.id,
            "bench": info.bench_name,
            "bench_path": rel,
            "clock": info.clock_mode,
            "now_s": info.now_ns / 1e9,
            "instruments": [
                {
                    "name": i.name,
                    "model": i.model,
                    "idn": i.idn,
                    "resource_virtual": i.address_virtual,
                    "resource_real": i.address_real,
                    "resource_hislip": i.address_hislip,
                    "scpi_socket": f"TCPIP0::127.0.0.1::{i.scpi_port}::SOCKET" if i.scpi_port else None,
                }
                for i in info.instruments
            ],
            "switch_script": (
                "In-process (CI): rm = pyvisa.ResourceManager('<bench file>@simubench'); the script's "
                "real addresses and the instance names both open the virtual instruments. "
                "Or keep the default backend and use the scpi_socket addresses of this session."
            ),
        }
        if ui_url:
            out["web_bench"] = ui_url
        return out

    @mcp.tool()
    async def start_session(
        path: str,
        clock: Literal["command", "wall", "stepped"] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Deploy a bench: start a session (replacing the running one) and return the resource
        addresses each instrument answers at, and how to point an existing script at them."""
        guard()
        try:
            s = await manager.start(path, clock=clock, seed=seed)
        except Exception as exc:
            raise fail(exc) from None
        return session_summary(s)

    @mcp.tool()
    async def stop_session(session_id: str | None = None) -> str:
        """Stop a session and close its SCPI sockets."""
        guard()
        s = served(session_id)
        await manager.stop(s.id)
        return f"stopped {s.id}"

    @mcp.tool()
    def list_sessions() -> list[dict[str, Any]]:
        """Running sessions with their instruments and addresses."""
        guard()
        return [session_summary(s) for s in manager.sessions.values()]

    @mcp.tool()
    def describe_session(session_id: str | None = None) -> dict[str, Any]:
        """Topology of a running session: instruments, DUTs, wires and the nets they form."""
        guard()
        info = manager.describe(served(session_id))
        return {
            **session_summary(served(session_id)),
            "duts": [{"name": d.name, "type": d.type, "ports": [p.name for p in d.ports]} for d in info.duts],
            "wires": [
                {
                    "index": w.index,
                    "from": w.from_,
                    "to": w.to,
                    "accessories": [a.type for a in w.accessories],
                    "net": w.net,
                }
                for w in info.wires
            ],
            "nets": [n.model_dump() for n in info.nets],
        }

    # ---- instruments ------------------------------------------------------------------------------

    @mcp.tool()
    async def send_scpi(
        instrument: str,
        command: str = Field(..., description="Program message; ';' joins units, '?' makes a query"),
        session_id: str | None = None,
        confirm: bool = Field(False, description="Required for commands marked dangerous"),
    ) -> dict[str, Any]:
        """Send SCPI to an instrument of the session and return its response and any new errors
        from the error queue. Logged as client 'mcp'."""
        guard()
        s = served(session_id)
        try:
            inst = s.session.instrument(instrument)
        except KeyError as exc:
            raise fail(exc) from None
        risky = [m.text for m in classify(inst.definition, command) if m.dangerous]
        if risky and not confirm:
            raise ToolError(f"危险命令需要确认：{'; '.join(risky)}。向工程师说明后，以 confirm=true 重新调用")

        def run() -> dict[str, Any]:
            session = s.session
            before = len(inst.errors)
            try:
                session.write(instrument, command.encode(), client="mcp")
                out = session.read(instrument, client="mcp")
            except LinkDown as exc:
                return {"response": None, "link_error": str(exc), "now_s": session.now_ns / 1e9}
            errors = inst.errors.peek_all()[before:]
            result: dict[str, Any] = {
                "response": out.decode(errors="replace").rstrip("\n") if out is not None else None,
                "errors": [{"code": e.code, "message": e.message} for e in errors],
                "now_s": session.now_ns / 1e9,
            }
            if out is None and "?" in command:
                result["timed_out"] = True
            return result

        return await asyncio.to_thread(run)

    @mcp.tool()
    def get_instrument_state(instrument: str, session_id: str | None = None) -> dict[str, Any]:
        """Current state variables and error queue of an instrument (what its front panel shows)."""
        guard()
        s = served(session_id)
        try:
            snap = s.session.instrument(instrument).snapshot()
        except KeyError as exc:
            raise fail(exc) from None
        snap["state"] = {k: _plain(v) for k, v in snap["state"].items()}
        return snap

    # ---- observe ----------------------------------------------------------------------------------

    @mcp.tool()
    def read_log(
        session_id: str | None = None,
        after: int = Field(0, ge=0, description="Return events after this seq (cursor)"),
        limit: int = Field(100, ge=1, le=1000),
        instrument: str | None = None,
        kinds: list[str] | None = Field(None, description="e.g. ['scpi', 'error', 'state']"),
    ) -> dict[str, Any]:
        """Communication log and events of a session (SCPI writes and reads with their clients,
        state changes, errors, clock advances). Page with the returned next cursor."""
        guard()
        s = served(session_id)
        with s.session._lock:
            history = list(s.session.history)
        events = []
        for ev in history:
            if ev.get("eid", 0) <= after:
                continue
            if instrument and ev.get("instrument") != instrument:
                continue
            if kinds and ev.get("kind") not in kinds:
                continue
            events.append(to_event(ev, ev.get("eid", 0)))
            if len(events) >= limit:
                break
        last = events[-1]["seq"] if events else after
        return {"events": events, "next": last, "latest": s.session.event_id}

    # ---- simulation control (off by default) ------------------------------------------------------

    @mcp.tool()
    def advance_clock(
        seconds: float = Field(..., gt=0, le=3600), session_id: str | None = None
    ) -> dict[str, Any]:
        """Advance the session's virtual clock (simulation control; needs --allow-control)."""
        guard()
        control()
        s = served(session_id)
        s.session.advance(seconds)
        return {"now_s": s.session.now_ns / 1e9}

    @mcp.tool()
    def inject_fault(fault: Fault, session_id: str | None = None) -> dict[str, Any]:
        """Inject a fault into a running session (simulation control; needs --allow-control).
        Link faults on an instrument: timeout, disconnect, garble, latency, busy; instrument faults:
        error (queue an error code), trip (outputs off with an error); component faults: drift
        (drift_per_s), hidden (set a parameter). at_s / duration_s / count limit it."""
        guard()
        control()
        s = served(session_id)
        try:
            with s.session._lock:
                state = s.session.faults.inject(fault, source="mcp")
        except (KeyError, ValueError) as exc:
            raise fail(exc) from None
        return _fault(state)

    @mcp.tool()
    def list_faults(session_id: str | None = None) -> list[dict[str, Any]]:
        """Faults of a session: pending, active and ended."""
        guard()
        s = served(session_id)
        with s.session._lock:
            return [_fault(f) for f in s.session.faults.snapshot()]

    @mcp.tool()
    def clear_fault(fault_id: str, session_id: str | None = None) -> dict[str, Any]:
        """End a fault (simulation control; needs --allow-control)."""
        guard()
        control()
        s = served(session_id)
        try:
            with s.session._lock:
                return _fault(s.session.faults.clear(fault_id))
        except KeyError as exc:
            raise fail(exc) from None

    @mcp.tool()
    def read_asset_register(session_id: str | None = None) -> dict[str, Any]:
        """The bench's virtual calibration register (serials, status, due dates) and its calendar
        date, as a test script's pre-run check would see them."""
        from .engine.calibration import register_view

        s = served(session_id).session
        now = s.calendar()
        return {"calendar": now.isoformat(), "assets": register_view(s.bench.assets, now.date())}

    @mcp.tool()
    def read_true_values(session_id: str | None = None) -> dict[str, Any]:
        """Hidden state the simulation knows exactly: DUT parameters including hidden tolerances,
        instrument hidden overrides, and the true signal on every net. Needs --allow-control and
        run.reveal_true_values in the bench (off in validation runs, so calibration cannot read
        the answer)."""
        guard()
        control()
        s = served(session_id)
        session = s.session
        if not session.reveal_true_values:
            raise ToolError("该测试台未开启 run.reveal_true_values；验证模式下不提供真实值")
        with session._lock:
            duts = {n: {k: _plain(v) for k, v in c.describe().items()} for n, c in session.components.items()}
            hidden = {
                name: {
                    cname: {k: _plain(v) for k, v in comp.overrides.items()}
                    for cname, comp in inst.components.items()
                    if comp.overrides
                }
                for name, inst in session.instruments.items()
            }
            nets = {}
            for net_id in session.nets:
                sig = session.net_signal(net_id).merged()
                nets[net_id] = {
                    "rms_v": float(sig.rms()),
                    "lines": [
                        {"freq_hz": float(f), "amp_peak_v": float(abs(a))}
                        for f, a in zip(sig.freq, sig.amp, strict=False)
                        if abs(a) > 1e-12
                    ][:16],
                    "dc_v": float(sig.dc),
                }
        return {"duts": duts, "instrument_hidden": hidden, "nets": nets}

    return mcp
