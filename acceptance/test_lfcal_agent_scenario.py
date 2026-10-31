"""Issue 146: only a script is given; the agent builds the low-frequency calibration bench through
the MCP tools alone (T7's protocol-level flow) and the script runs to the end on the session.

The workspace starts empty but for the host script (``lowfreq_cal_host.py``): the agent drafts the
bench from it, creates both definitions from the suggested templates — the source lenient, the
scope strict, per the contract — learns the one command no template speaks into the scope's
definition, confirms the inferred cable, identity strings and deterministic run, validates, and
deploys. The script then runs against the addresses ``start_session`` returned: contract
(distinguishable ``*IDN?``, chunked readback, every query answered, unknown commands per policy,
same seed bit-identical) and physics (50 Ω / high-Z, the frequency response, dBm / Vrms / Vpp).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from itertools import pairwise
from pathlib import Path

import pytest
import pyvisa

pytest.importorskip("mcp")

SCRIPT = Path(__file__).parent / "lowfreq_cal_host.py"

from mcp import Client  # noqa: E402

from simubench.mcp_server import build_server  # noqa: E402
from simubench.server.manager import SessionManager  # noqa: E402


def _module(ws: Path, name: str):
    """The host script as a module: it drives the bench the way it drives hardware."""
    spec = importlib.util.spec_from_file_location(name, ws / "host.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


async def _call(c: Client, tool: str, **args):
    r = await c.call_tool(tool, args)
    assert not r.is_error, (tool, r.content[0].text)
    sc = r.structured_content
    return (
        sc.get("result", sc)
        if sc is not None and set(sc) == {"result"}
        else (sc or json.loads(r.content[0].text))
    )


async def _build(c: Client) -> dict:
    """The agent's build, from an empty workspace: draft, two templates, the missing command,
    cable and identity, run mode, validate. Returns the draft report and the session."""
    draft = await _call(c, "draft_bench_from_script", script_path="host.py", save_as="lfcal.bench.yaml")
    entries = {e["receiver"]: e for e in draft["report"]}
    scope_t, cal_t = entries["scope"]["template"], entries["cal"]["template"]
    assert (scope_t["template"], scope_t["variant"]) == ("oscilloscope", "lecroy-scope")
    assert (cal_t["template"], cal_t["variant"]) == ("signal_source", "channel-base")
    # One command no template speaks, reported with its line: the host's PERSist 5. The source's
    # beeper probe stays unsupported by choice — the contract says unknown commands are ignored.
    lines = SCRIPT.read_text("utf-8").splitlines()
    persist_line = next(i for i, line in enumerate(lines, 1) if 'scope.write("PERSist 5")' in line)
    beeper_line = next(
        i for i, line in enumerate(lines, 1) if 'cal.write(":SYSTem:BEEPer:STATe OFF")' in line
    )
    assert [(u["line"], u["command"]) for u in entries["scope"]["unsupported"]] == [
        (persist_line, "PERSist 5")
    ]
    assert [(u["line"], u["command"]) for u in entries["cal"]["unsupported"]] == [
        (beeper_line, ":SYSTem:BEEPer:STATe OFF")
    ]
    filled = {f["pointer"]: f for f in scope_t["filled"]}
    model = filled["/template/inputs/identity/model"]
    assert model["value"] == "LFCAL-REFSCOPE" and model["source"] == "script" and model["line"] > 0
    assert draft["bench"]["wires"] == [{"from": "cal.CH1", "to": "scope.CH1"}]
    assert draft["inferred_wires"][0]["why"].startswith("推断")
    assert draft["bench"]["run"] == {"seed": 1, "clock": "command", "idle": "off"}

    cal_inputs = json.loads(json.dumps(cal_t["inputs"]))
    cal_inputs["dialect"]["unknown_commands"] = "ignore"  # the contract: a lenient source
    scope_doc = await _call(
        c, "create_definition_from_template", template_id="oscilloscope",
        definition_id=scope_t["definition_id"], inputs=scope_t["inputs"],
    )  # fmt: skip
    cal_doc = await _call(
        c, "create_definition_from_template", template_id="signal_source",
        definition_id=cal_t["definition_id"], inputs=cal_inputs,
    )  # fmt: skip
    assert scope_doc["saved"] and scope_doc["ok"], scope_doc["diagnostics"]
    assert cal_doc["saved"] and cal_doc["ok"], cal_doc["diagnostics"]

    # The agent teaches the scope the command the script needs, drafted on the template expansion
    # and saved over it in one call (issue 169).
    merged = await _call(
        c, "draft_definition_from_script", script_path="host.py", definition_id="lfcal-refscope",
        receiver="scope", base_definition="lfcal-refscope", save=True, base_sha256=scope_doc["sha256"],
    )  # fmt: skip
    assert [d["header"] for d in merged["drafted"]] == [":PERSIST"]
    assert merged["saved"]["ok"], merged["saved"]["diagnostics"]

    bench = await _call(
        c, "edit_bench", path="lfcal.bench.yaml", base_sha256=draft["saved"]["sha256"], ops=[
            {"op": "set_accessories", "index": 0, "accessories": [
                {"type": "coax_cable", "params": {"length_m": 1.0, "r": 0.05, "r_skin_1mhz": 0.15}},
            ]},
            {"op": "set_identity", "instrument": "scope", "idn": "LECROY,LFCAL-REFSCOPE,{serial},{firmware}"},
            {"op": "set_identity", "instrument": "cal", "idn": "SIMUBENCH,LFCAL-CALIBRATOR,{serial},{firmware}"},
            {"op": "set_run", "seed": 1, "clock": "command"},
        ],
    )  # fmt: skip
    assert bench["ok"], bench["diagnostics"]
    checked = await _call(c, "validate_bench", path="lfcal.bench.yaml")
    assert checked["ok"], checked["diagnostics"]
    return {
        "draft": draft,
        "entries": entries,
        "session": await _call(c, "start_session", path="lfcal.bench.yaml"),
    }


def _connect(addrs: dict, host):
    """The script opens the addresses the session returned, as it would open real instruments."""
    rm = pyvisa.ResourceManager("@py")
    scope = host.open_instrument(rm, addrs["scope"]["scpi_socket"], "LFCAL-REFSCOPE")
    cal = host.open_instrument(rm, addrs["cal"]["scpi_socket"], "LFCAL-CALIBRATOR")
    return rm, scope, cal


def _run_script(addrs: dict, host) -> tuple[list, list]:
    rm, scope, cal = _connect(addrs, host)
    try:
        return host.run(scope, cal, amp_vpp=0.05, points=30000)
    finally:
        rm.close()


def _pairing_problems(events: list[dict]) -> list[str]:
    """Every query answered, no answer without a query, per instrument (the ports' contract)."""
    pending: dict[str, int] = {}
    problems: list[str] = []
    for e in events:
        if e.get("kind") != "scpi":
            continue
        inst = e["instrument"]
        if e.get("dir") == "write":
            pending[inst] = pending.get(inst, 0) + e.get("text", "").count("?")
        elif e.get("dir") == "read":
            if pending.get(inst, 0) <= 0:
                problems.append(f"answer without a query: {e}")
            else:
                pending[inst] -= 1
    problems += [f"{inst}: {n} unanswered" for inst, n in pending.items() if n > 0]
    return problems


async def test_the_agent_builds_the_bench_and_the_script_runs_through(tmp_path):
    """The whole flow: MCP alone to a deployed bench, then the script to the end on the addresses."""
    ws = tmp_path
    (ws / "host.py").write_bytes(SCRIPT.read_bytes())  # only the script is given
    host = _module(ws, "lfcal_host_build")
    manager = SessionManager(ws, scpi_base_port=0, hislip_port=None)
    server = build_server(manager, ui_url="http://127.0.0.1:1/?token=x")
    async with Client(server) as c:
        built = await _build(c)
        session = built["session"]
        instruments = {i["name"]: i for i in session["instruments"]}
        # Two instruments, distinguishable by the *IDN? the bench's idn strings define.
        assert instruments["scope"]["idn"] == "LECROY,LFCAL-REFSCOPE,SB000001,1.0.0"
        assert instruments["cal"]["idn"] == "SIMUBENCH,LFCAL-CALIBRATOR,SB000001,1.0.0"
        assert instruments["scope"]["idn"] != instruments["cal"]["idn"]
        assert instruments["scope"]["scpi_socket"] and instruments["cal"]["scpi_socket"]

        # The definition now covers the script: re-drafting reports nothing unsupported for the scope.
        redraft = await _call(c, "draft_bench_from_script", script_path="host.py")
        red = {e["receiver"]: e for e in redraft["report"]}
        assert red["scope"]["unsupported"] == []
        assert [u["command"] for u in red["cal"]["unsupported"]] == [":SYSTem:BEEPer:STATe OFF"]

        resp, table = await asyncio.to_thread(_run_script, instruments, host)

    # Flow 1: the pulse amplitude about the 50 mVpp set at every frequency, falling with frequency.
    freqs = [f for f, _ in resp]
    assert freqs == [1e4, 2e4, 5e4, 1e5, 2e5, 5e5, 1e6, 2e6, 5.5e6]
    rel = [a / 0.05 for _, a in resp]
    assert all(0.95 < r < 1.02 for r in rel), rel
    assert all(b <= a + 0.005 for a, b in pairwise(rel)), rel
    assert rel[0] - rel[-1] > 0.005  # the response really falls
    # Flow 2: DC levels read back as themselves; the AC levels about the setting.
    dc = [r for r in table if r["mode"] == "DC"]
    assert [r["set_v"] for r in dc] == [-0.05, -0.025, 0.0, 0.025, 0.05]
    assert all(abs(r["measured_v"] - r["set_v"]) <= 1e-3 + 0.01 * abs(r["set_v"]) for r in dc)
    ac = [r for r in table if r["mode"] == "AC"]
    assert [(r["unit"], r["set"]) for r in ac] == [
        ("VPP", 0.02),
        ("VPP", 0.05),
        ("VPP", 0.1),
        ("VRMS", 0.25),
        ("DBM", pytest.approx(0.9691, abs=1e-3)),
        ("DBM", pytest.approx(-19.0309, abs=1e-3)),  # negative dBm, the range being Vpp (D48)
    ]
    for r in ac[:3]:
        assert r["measured_vpp"] == pytest.approx(r["set"], rel=0.01)
    # The same 0.5 Vpp square set as VRMS and as dBm into 50 Ω lands as the same wave as in VPP.
    assert ac[3]["measured_vpp"] == ac[4]["measured_vpp"]
    assert ac[3]["measured_vpp"] == pytest.approx(0.5, rel=0.01)
    assert ac[5]["measured_vpp"] == pytest.approx(0.05, rel=0.01)
    await manager.stop(next(iter(manager.sessions)))


async def test_the_log_shows_every_query_answered_and_unknown_commands_per_policy(tmp_path):
    """Queries have answers, settings none; the source's ignored probe is on record; a strict
    unknown command queues -113."""
    ws = tmp_path
    (ws / "host.py").write_bytes(SCRIPT.read_bytes())
    host = _module(ws, "lfcal_host_log")
    manager = SessionManager(ws, scpi_base_port=0, hislip_port=None)
    server = build_server(manager, ui_url="http://127.0.0.1:1/?token=x")
    async with Client(server) as c:
        instruments = {i["name"]: i for i in (await _build(c))["session"]["instruments"]}
        await asyncio.to_thread(_run_script, instruments, host)
        log = await _call(c, "read_log", kinds=["scpi", "ignored"], limit=1000)
        assert _pairing_problems(log["events"]) == []
        # The beeper probe: swallowed without an error, but never silently (D16).
        ignored = [e for e in log["events"] if e["kind"] == "ignored"]
        assert [(e["instrument"], e["reason"]) for e in ignored] == [("cal", "unknown_command")]

        # The scope is strict: a command outside its definition queues -113, on record as an event.
        def strict():
            rm, scope, _cal = _connect(instruments, host)
            try:
                scope.write("NONSENSE 1")
                return scope.query("SYST:ERR?").strip()
            finally:
                rm.close()

        assert (await asyncio.to_thread(strict)).startswith("-113")
        errors = (await _call(c, "read_log", kinds=["error"], limit=100))["events"]
        assert [(e["instrument"], e["code"]) for e in errors] == [("scope", -113)]
    await manager.stop(next(iter(manager.sessions)))


async def test_same_seed_and_same_commands_give_the_same_digits(tmp_path):
    """Two deployments of the bench, same seed: the response and the table match digit for digit."""
    ws = tmp_path
    (ws / "host.py").write_bytes(SCRIPT.read_bytes())
    host = _module(ws, "lfcal_host_seed")
    manager = SessionManager(ws, scpi_base_port=0, hislip_port=None)
    server = build_server(manager, ui_url="http://127.0.0.1:1/?token=x")
    async with Client(server) as c:
        await _build(c)
        first = await asyncio.to_thread(
            _run_script, {i["name"]: i for i in (await _call(c, "list_sessions"))[0]["instruments"]}, host
        )
        await _call(c, "start_session", path="lfcal.bench.yaml")  # the same bench again, same seed
        second = await asyncio.to_thread(
            _run_script, {i["name"]: i for i in (await _call(c, "list_sessions"))[0]["instruments"]}, host
        )
        assert first == second
    await manager.stop(next(iter(manager.sessions)))


async def test_chunked_readback_joins_into_the_whole_record(tmp_path):
    """12 345, 30 000 and 100 000 points read in pieces: the pieces are the record, continuously."""
    ws = tmp_path
    (ws / "host.py").write_bytes(SCRIPT.read_bytes())
    host = _module(ws, "lfcal_host_chunks")
    manager = SessionManager(ws, scpi_base_port=0, hislip_port=None)
    server = build_server(manager, ui_url="http://127.0.0.1:1/?token=x")
    async with Client(server) as c:
        instruments = {i["name"]: i for i in (await _build(c))["session"]["instruments"]}

        def chunks():
            rm, scope, cal = _connect(instruments, host)
            out = {}
            try:
                for point, tdiv in [(12345, "5E-4"), (30000, "2E-4"), (100000, "2E-4")]:
                    host.setup(scope, cal, point)
                    cal.write(":CHANnel1:BASE:WAVe SQUare")
                    cal.write(":CHANnel1:BASE:AMPLitude 0.05")
                    cal.write(":CHANnel1:BASE:FREQuency 1000")
                    cal.write(":CHANnel1:OUTPut 1")
                    cal.query("*OPC?")
                    scope.write("C1:VDIV 10 mV")
                    scope.write(f"TDIV {tdiv}")
                    scope.write("TRMD AUTO")
                    data = host.read_waveform(scope, 1, point)
                    out[point] = (data, point // 2 if point != 12345 else 2469)
            finally:
                rm.close()
            return out

        read = await asyncio.to_thread(chunks)
    for point, (data, period) in read.items():
        assert len(data) == point
        # An integer number of samples per period: the pieces carry one continuous record.
        assert data[: point - period] == pytest.approx(data[period:], abs=2e-3)
        assert max(data) > 0.02 and min(data) < -0.02  # the square is on the record, both levels
    await manager.stop(next(iter(manager.sessions)))


async def test_the_load_setting_scales_what_the_scope_sees(tmp_path):
    """50 Ω vs high-Z on the source (D13), and the scope's own 50 Ω / 1 MΩ input (D50 / D1M)."""
    ws = tmp_path
    (ws / "host.py").write_bytes(SCRIPT.read_bytes())
    host = _module(ws, "lfcal_host_load")
    manager = SessionManager(ws, scpi_base_port=0, hislip_port=None)
    server = build_server(manager, ui_url="http://127.0.0.1:1/?token=x")
    async with Client(server) as c:
        instruments = {i["name"]: i for i in (await _build(c))["session"]["instruments"]}

        def amplitude(load, coupling):
            rm, scope, cal = _connect(instruments, host)
            try:
                host.setup(scope, cal, 30000)
                scope.write("C1:CPL " + coupling)
                cal.write(f":CHANnel1:LOAD {load}")
                return host.pulse_measure(scope, cal, 1e4, 0.05, 0.002)
            finally:
                rm.close()

        into_50 = await asyncio.to_thread(amplitude, 50, "D50")
        high_z = await asyncio.to_thread(amplitude, 10000, "D50")
        into_1m = await asyncio.to_thread(amplitude, 50, "D1M")
    # The amplitude is what the load setting defines: high-Z doubles it into a 1 MΩ input and
    # halves it into the scope's 50 Ω input; the source's 50 Ω into the scope's 1 MΩ doubles it.
    assert high_z / into_50 == pytest.approx(0.5, abs=0.05)
    assert into_1m / into_50 == pytest.approx(2.0, rel=0.05)
    assert into_50 == pytest.approx(0.05, rel=0.05)
    await manager.stop(next(iter(manager.sessions)))
