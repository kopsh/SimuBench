"""Tools for deployed sessions: start/stop, drive, observe, control (off by default)."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ..engine.commands import classify
from ..engine.events import project
from ..engine.record import jsonable
from ..models.bench import Fault
from ..server.manager import ClientsConnected
from ..server.services import clock, faults
from .context import McpContext, scpi_payload


def _fault(f: Any) -> dict[str, Any]:
    """The same fault shape the web API returns (``FaultInfo``): one DTO, two seams."""
    return f.model_dump(mode="json")


def register(mcp: MCPServer, ctx: McpContext) -> None:
    manager = ctx.manager
    files = ctx.files
    guard = ctx.guard
    fail = ctx.fail
    force_needed = ctx.force_needed
    served = ctx.served
    control = ctx.control
    session_summary = ctx.session_summary

    @mcp.tool()
    async def start_session(
        path: str,
        clock: Literal["command", "wall", "stepped", "scaled"] | None = None,
        seed: int | None = None,
        time_scale: float | None = Field(
            None, description="For clock=scaled: virtual seconds per real second (default 1)"
        ),
        force: bool = Field(
            False,
            description="Required while scripts are connected to the ports: starting replaces the running bench and drops them",
        ),
    ) -> dict[str, Any]:
        """Deploy a bench: start a session (replacing the running one) and return the resource
        addresses each instrument answers at, and how to point an existing script at them.
        Refused while scripts are connected to the ports, unless force=true."""
        guard()
        try:
            s = await manager.start(path, clock=clock, seed=seed, force=force, time_scale=time_scale)
        except ClientsConnected as exc:
            raise force_needed(exc) from None
        except Exception as exc:
            raise fail(exc) from None
        return session_summary(s)

    @mcp.tool()
    async def stop_session(
        session_id: str | None = None,
        force: bool = Field(
            False,
            description="Required while scripts are connected to the ports: stopping drops them",
        ),
    ) -> str:
        """Stop a session and close its SCPI sockets. Refused while scripts are connected to the
        ports, unless force=true."""
        guard()
        s = served(session_id)
        try:
            await manager.stop(s.id, force=force)
        except ClientsConnected as exc:
            raise force_needed(exc) from None
        return f"stopped {s.id}"

    @mcp.tool()
    def list_sessions() -> list[dict[str, Any]]:
        """Running sessions with their instruments and addresses."""
        guard()
        return [session_summary(s) for s in manager.sessions.values()]

    @mcp.tool()
    def describe_session(session_id: str | None = None) -> dict[str, Any]:
        """Topology of a running session: instruments, parts, which of them are DUTs, wires and the nets
        they form."""
        guard()
        s = served(session_id)
        info = manager.views.describe(s)
        return {
            **session_summary(s, info),
            "parts": [
                {"name": d.name, "type": d.type, "dut": d.dut, "ports": [p.name for p in d.ports]}
                for d in info.parts
            ],
            "duts": info.duts,
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
        from the error queue, as of virtual time ``now_ns`` (nanoseconds, the unit every
        SimuBench tool reports). Logged as client 'mcp'."""
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
            # One exchange (#121): a script on the port cannot take this command's response.
            return scpi_payload(s.session, instrument, command, client="mcp")

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
        snap["state"] = {k: jsonable(v) for k, v in snap["state"].items()}
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
        with s.session.view():
            history = list(s.session.history)
        events = []
        for ev in history:
            if ev.get("eid", 0) <= after:
                continue
            if instrument and ev.get("instrument") != instrument:
                continue
            if kinds and ev.get("kind") not in kinds:
                continue
            events.append(project(ev, ev.get("eid", 0)))
            if len(events) >= limit:
                break
        last = events[-1]["seq"] if events else after
        return {"events": events, "next": last, "latest": s.session.event_id}

    # ---- simulation control (off by default) ------------------------------------------------------

    @mcp.tool()
    def advance_clock(
        seconds: float = Field(..., gt=0, le=3600),
        session_id: str | None = None,
        force: bool = Field(
            False,
            description="Required while scripts are connected to the ports: their view of virtual time moves",
        ),
    ) -> dict[str, Any]:
        """Advance the session's virtual clock (simulation control; needs --allow-control).
        Returns ``now_ns``, the virtual time after the advance in nanoseconds — the unit every
        SimuBench tool reports. Refused while scripts are connected to the ports, unless
        force=true."""
        guard()
        control()
        try:
            return {"now_ns": clock.advance(served(session_id), seconds, force=force).now_ns}
        except ClientsConnected as exc:
            raise force_needed(exc) from None

    @mcp.tool()
    def inject_fault(fault: Fault, session_id: str | None = None) -> dict[str, Any]:
        """Inject a fault into a running session (simulation control; needs --allow-control).
        Link faults on an instrument: timeout, disconnect, garble, latency, busy; instrument faults:
        error (queue an error code), trip (outputs off with an error); component faults: drift
        (drift_per_s), hidden (set a parameter). at_s / duration_s / count limit it."""
        guard()
        control()
        try:
            return _fault(faults.inject(served(session_id), fault, source="mcp"))
        except (KeyError, ValueError) as exc:
            raise fail(exc) from None

    @mcp.tool()
    def list_faults(session_id: str | None = None) -> list[dict[str, Any]]:
        """Faults of a session: pending, active and ended."""
        guard()
        return [_fault(f) for f in faults.list_faults(served(session_id))]

    @mcp.tool()
    def clear_fault(fault_id: str, session_id: str | None = None) -> dict[str, Any]:
        """End a fault (simulation control; needs --allow-control)."""
        guard()
        control()
        try:
            return _fault(faults.clear(served(session_id), fault_id))
        except KeyError as exc:
            raise fail(exc) from None

    @mcp.tool()
    def read_asset_register(session_id: str | None = None) -> dict[str, Any]:
        """The bench's virtual calibration register (serials, status, due dates) and its calendar
        date, as a test script's pre-run check would see them."""
        from ..engine.calibration import register_view

        s = served(session_id).session
        now = s.calendar()
        return {"calendar": now.isoformat(), "assets": register_view(s.bench.assets, now.date())}

    @mcp.tool()
    def read_true_values(session_id: str | None = None) -> dict[str, Any]:
        """Hidden state the simulation knows exactly: part parameters including hidden tolerances,
        instrument hidden overrides, the individual deviations drawn from the bench seed, and the
        true signal on every net. Needs --allow-control and
        run.reveal_true_values in the bench (off in validation runs, so calibration cannot read
        the answer)."""
        guard()
        control()
        s = served(session_id)
        session = s.session
        if not session.reveal_true_values:
            raise ToolError("该测试台未开启 run.reveal_true_values；验证模式下不提供真实值")
        with session.view():
            parts = {
                n: {k: jsonable(v) for k, v in c.describe().items()} for n, c in session.components.items()
            }
            hidden = {
                name: {
                    cname: {k: jsonable(v) for k, v in comp.overrides.items()}
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
            deviations = {k: jsonable(v) for k, v in session.deviations().items()}
        return {"parts": parts, "instrument_hidden": hidden, "deviations": deviations, "nets": nets}

    @mcp.tool()
    def check_verdicts(
        record: str = Field(..., description="Run record (run-*.jsonl) in the workspace"),
        report: str = Field(
            ..., description="The calibration script's verdict report (CSV), in the workspace"
        ),
        map: str = Field(
            ...,
            description="Column map (YAML) in the workspace: which column is what, "
            "and how the script's decision values read (pass / fail / no_statement)",
        ),
    ) -> dict[str, Any]:
        """Check a calibration script's statements against the truth, offline after the run (C7).

        Replays the run record, matches the report's rows to the DUT's read replies in order, and
        judges each statement with the true input behind the reading: correct, false_accept,
        false_reject or withheld, per point and in summary. Needs --allow-control: the result
        contains the true errors. It reveals nothing while a script runs — the script is over, and
        the run-time confidentiality rules (run.reveal_true_values) are untouched."""
        guard()
        control()
        from ..engine.verdicts import VerdictError
        from ..engine.verdicts import check_verdicts as check

        try:
            return check(files.resolve(record), files.resolve(report), files.resolve(map)).as_dict()
        except VerdictError as exc:
            raise ToolError(str(exc)) from None
        except FileNotFoundError as exc:
            raise fail(exc) from None
