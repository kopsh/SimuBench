import asyncio
import json
import shutil
import socket
import time
from pathlib import Path

import pytest
import yaml

pytest.importorskip("mcp")

from mcp import Client

from helpers import mcp_result
from simubench.mcp_server import RateLimiter, build_server
from simubench.models import api as M
from simubench.server.manager import ClientsConnected, SessionManager

ROOT = Path(__file__).resolve().parents[1]
BENCH = "awg_rc_scope.bench.yaml"


@pytest.fixture
def ws(tmp_path):
    shutil.copy(ROOT / "validation" / "benches" / BENCH, tmp_path / BENCH)
    shutil.copy(ROOT / "tests" / "data" / "cal_script.py", tmp_path / "cal_script.py")
    return tmp_path


def make(ws, **kw):
    manager = SessionManager(ws, scpi_base_port=None)
    return manager, build_server(manager, ui_url="http://127.0.0.1:1/?token=x", **kw)


def make_ports(ws, **kw):
    """Like make, with the SCPI ports on: the force guard is about the scripts on them (issue 624)."""
    manager = SessionManager(ws, scpi_base_port=0, hislip_port=0)
    return manager, build_server(manager, ui_url="http://127.0.0.1:1/?token=x", **kw)


async def connect_script(manager: SessionManager, sid: str, instrument: str) -> socket.socket:
    """A script on the instrument's raw socket port, once the session lists it as a client."""
    served = manager.get(sid)
    lst = next(lt for lt in served.scpi.listeners if lt.instrument == instrument)
    sock = socket.create_connection(("127.0.0.1", lst.port), timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if served.remote_clients(instrument):
            return sock
        await asyncio.sleep(0.02)  # the accept handler registers the label on the loop
    sock.close()
    raise AssertionError("the script never showed up as a client")


def result(r):
    return mcp_result(r, error_ok=True)


async def call(client, tool, /, **args):
    return result(await client.call_tool(tool, args))


async def test_tools_listed_with_schemas(ws):
    _, server = make(ws)
    async with Client(server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert {"edit_bench", "start_session", "send_scpi", "read_log", "draft_bench_from_script"} <= set(tools)
    # Edit operations are typed in the tool schema, so agents see every operation and its fields.
    schema = json.dumps(tools["edit_bench"].input_schema)
    assert "add_instrument" in schema and "set_accessories" in schema
    # Whole-bench operations carry the force confirmation (issue 624).
    for tool in ("start_session", "stop_session", "advance_clock"):
        assert "force" in json.dumps(tools[tool].input_schema), tool


async def test_build_deploy_drive_observe(ws):
    manager, server = make(ws)
    async with Client(server) as c:
        doc = await call(c, "create_bench", path="agent.bench.yaml", name="agent")
        ops = [
            {"op": "add_instrument", "name": "gen", "model": "sb-awg-2"},
            {"op": "add_instrument", "name": "osc", "model": "sb-dso-4"},
            {"op": "add_dut", "name": "r", "type": "rc_lowpass", "params": {"r": 1000, "c": 1e-7}},
            {"op": "connect", "from": "gen.OUT1", "to": "r.IN"},
            {"op": "connect", "from": "r.OUT", "to": "osc.CH1", "accessories": [{"type": "passive_probe"}]},
        ]
        doc = await call(c, "edit_bench", path="agent.bench.yaml", base_sha256=doc["sha256"], ops=ops)
        assert doc["ok"], doc
        assert [d["code"] for d in doc["diagnostics"]] == ["W-IMPEDANCE-MISMATCH"]

        stale = await call(c, "edit_bench", path="agent.bench.yaml", base_sha256="0" * 64, ops=ops[:1])
        assert "read_bench" in stale["error"]
        versions = await call(c, "list_bench_history", path="agent.bench.yaml")
        assert [v["source"] for v in versions] == ["agent", "agent"]  # D35: who wrote each version
        first = await call(c, "read_bench_version", path="agent.bench.yaml", seq=1)
        assert first["data"]["name"] == "agent" and "instruments" not in first["data"]

        s = await call(c, "start_session", path="agent.bench.yaml")
        assert {i["name"] for i in s["instruments"]} == {"gen", "osc"}
        assert s["web_bench"].startswith("http://127.0.0.1")

        refused = await call(c, "send_scpi", instrument="gen", command=":SOUR1:FREQ 1000;:OUTP1 ON")
        assert "confirm=true" in refused["error"]
        ok = await call(c, "send_scpi", instrument="gen", command=":OUTP1 ON", confirm=True)
        assert ok["errors"] == []
        idn = await call(c, "send_scpi", instrument="osc", command="*IDN?")
        assert "SB-DSO-4" in idn["response"]
        bad = await call(c, "send_scpi", instrument="osc", command=":NOPE 1")
        assert bad["errors"][0]["code"] == -113

        state = await call(c, "get_instrument_state", instrument="gen")
        assert state["state"]["ch1.output"] in (True, 1, "ON")

        log = await call(c, "read_log", kinds=["scpi"], limit=2)
        assert [e["client"] for e in log["events"]] == ["mcp", "mcp"]
        more = await call(c, "read_log", kinds=["scpi"], after=log["next"])
        assert more["events"] and more["events"][0]["seq"] > log["next"]

        await call(c, "stop_session")
    assert manager.sessions == {}


async def test_start_refuses_invalid_bench(ws):
    _, server = make(ws)
    async with Client(server) as c:
        doc = await call(c, "create_bench", path="bad.bench.yaml", name="bad")
        doc = await call(
            c,
            "edit_bench",
            path="bad.bench.yaml",
            base_sha256=doc["sha256"],
            ops=[{"op": "add_instrument", "name": "x", "model": "sb-dso-5"}],
        )
        r = await call(c, "start_session", path="bad.bench.yaml")
        assert "E-MODEL-UNKNOWN" in r["error"]


async def test_advance_clock_needs_force_while_scripts_are_connected(ws):
    """A script on a port stands in the way of the clock, as on the HTTP side (issue 624): the
    refusal is a tool error naming the client and the force=true retry, and the guard holds for
    any caller of Session.advance."""
    manager, server = make_ports(ws, allow_control=True)
    async with Client(server) as c:
        s = await call(c, "start_session", path=BENCH)
        sid = s["session_id"]
        sock = await connect_script(manager, sid, "scope")
        try:
            r = await call(c, "advance_clock", seconds=0.5)
            assert "force=true" in r["error"] and "socket:" in r["error"]
            assert manager.get(sid).session.now_ns == 0  # nothing moved behind the script's back
            with pytest.raises(ClientsConnected):
                manager.get(sid).session.advance(0.1)  # the session layer itself refuses
            ok = await call(c, "advance_clock", seconds=0.5, force=True)
            assert ok["now_ns"] >= 500_000_000
        finally:
            sock.close()


async def test_stop_session_needs_force_while_scripts_are_connected(ws):
    manager, server = make_ports(ws)
    async with Client(server) as c:
        s = await call(c, "start_session", path=BENCH)
        sid = s["session_id"]
        sock = await connect_script(manager, sid, "awg")
        try:
            r = await call(c, "stop_session")
            assert "force=true" in r["error"] and "socket:" in r["error"]
            assert sid in manager.sessions  # the bench stands; the script keeps its ports
            assert await call(c, "stop_session", force=True) == f"stopped {sid}"
        finally:
            sock.close()
        assert manager.sessions == {}


async def test_start_session_needs_force_while_scripts_are_connected(ws):
    """Starting a bench replaces the running one and drops its scripts: refused without force."""
    manager, server = make_ports(ws)
    async with Client(server) as c:
        s = await call(c, "start_session", path=BENCH)
        sid = s["session_id"]
        sock = await connect_script(manager, sid, "awg")
        try:
            r = await call(c, "start_session", path=BENCH)
            assert "force=true" in r["error"] and "socket:" in r["error"]
            assert manager.sessions and next(iter(manager.sessions)) == sid
            s2 = await call(c, "start_session", path=BENCH, force=True)
        finally:
            sock.close()
        assert sid not in manager.sessions and s2["session_id"] in manager.sessions


async def test_read_log_returns_why_a_command_was_not_executed(ws):
    """The error classification, its origin file and the closest command flow through to MCP (D18)."""
    manager, server = make(ws)
    async with Client(server) as c:
        doc = await call(c, "create_bench", path="class.bench.yaml", name="class")
        doc = await call(
            c,
            "edit_bench",
            path="class.bench.yaml",
            base_sha256=doc["sha256"],
            ops=[{"op": "add_instrument", "name": "gen", "model": "sb-awg-2"}],
        )
        await call(c, "start_session", path="class.bench.yaml")
        bad = await call(c, "send_scpi", instrument="gen", command=":SOUR1:FREQuencyy 1000")
        assert bad["errors"][0]["code"] == -113
        log = await call(c, "read_log", kinds=["error"])
        e = next(e for e in log["events"] if e["kind"] == "error")
        assert e["reason"] == "not_in_definition"
        assert e["origin"].endswith("sb-awg-2.yaml")
        assert e["closest"] == "[:SOURce<ch>]:FREQuency[:CW]"
        await call(c, "stop_session")
    assert manager.sessions == {}


async def test_draft_from_script(ws):
    _, server = make(ws)
    async with Client(server) as c:
        d = await call(c, "draft_bench_from_script", script_path="cal_script.py", save_as="cal.bench.yaml")
    assert d["bench"]["instruments"]["awg"]["model"] == "sb-awg-2"
    assert d["bench"]["instruments"]["scope"]["address"] == "TCPIP0::192.168.1.21::5025::SOCKET"
    awg = next(r for r in d["report"] if r["instance"] == "awg")
    assert awg["unsupported"] == [
        {"line": 18, "command": ":SOUR1:SWEEP:STAT ON", "reason": "仪表模型中没有该命令"}
    ]
    # The DMM's :CONF:VOLT:DC is supported by the real multimeters now: one of them joins the draft
    # (34465A and 8588A both speak it, a tie the library order settles).
    assert d["bench"]["instruments"]["dmm"]["model"] in ("fluke-8588a", "keysight-34465a")
    dmm = next(r for r in d["report"] if r["instance"] == "dmm")
    assert dmm["supported"] == 1 and dmm["unsupported"] == []
    assert d["saved"]["ok"]
    assert (ws / "cal.bench.yaml").is_file()


async def test_control_tools_off_by_default(ws):
    _, server = make(ws)
    async with Client(server) as c:
        await call(c, "start_session", path=BENCH)
        r = await call(c, "advance_clock", seconds=1.0)
        assert "--allow-control" in r["error"]


async def test_true_values_need_bench_switch(ws):
    _, server = make(ws, allow_control=True)
    async with Client(server) as c:
        await call(c, "start_session", path=BENCH)
        assert (await call(c, "advance_clock", seconds=0.5))["now_ns"] >= 500_000_000
        r = await call(c, "read_true_values")
        assert "reveal_true_values" in r["error"]
        doc = await call(c, "read_bench", path=BENCH)
        await call(
            c,
            "edit_bench",
            path=BENCH,
            base_sha256=doc["sha256"],
            ops=[{"op": "set_run", "reveal_true_values": True}],
        )
        await call(c, "start_session", path=BENCH)
        await call(c, "send_scpi", instrument="awg", command=":OUTP1 ON", confirm=True)
        tv = await call(c, "read_true_values")
    assert tv["parts"]["rc"]["r"] == 1000.0
    assert any(n["lines"] for n in tv["nets"].values())


SWEEP_BENCH = {
    "schema": "simubench.bench/2",
    "name": "true_values_wall",
    "description": "Timed sweep on the wall clock: read_true_values must read the present (#480).",
    "instruments": {
        "sg": {
            "model": "sb-sg-1",
            "state": {
                "freq.mode": "SWEep",
                "sweep.start": 1.0e9,
                "sweep.stop": 2.0e9,
                "output.enabled": True,
            },
            # sb-sg-1 leaves the synthesizer's timed step unwired: the hidden state sets it
            # directly (10 ms of virtual time per sweep point).
            "hidden": {"synth": {"step_by": "TIME", "dwell": 0.01}},
        }
    },
    "parts": {"rc": {"type": "rc_lowpass", "dut": True, "params": {"r": 50.0, "c": 1.0e-12}}},
    "wires": [{"from": "sg.RF_OUT", "to": "rc.IN"}],
    "run": {"seed": 5, "clock": "wall", "reveal_true_values": True},
}


async def test_true_values_follow_the_wall_clock(ws):
    """#480 regression: read_true_values once read at a stale instant on the wall clock (it
    missed follow_wall), and a timed sweep still sat on its first point. The view follows the
    wall, so by the time the read solves, the sweep has moved on."""
    (ws / "sweep.bench.yaml").write_text(yaml.safe_dump(SWEEP_BENCH), encoding="utf-8")
    _, server = make(ws, allow_control=True)
    async with Client(server) as c:
        await call(c, "start_session", path="sweep.bench.yaml")
        await call(c, "send_scpi", instrument="sg", command=":INIT")  # the timed sweep starts here
        time.sleep(0.03)  # three dwell periods of wall time pass before the read
        tv = await call(c, "read_true_values")
    lines = [line for net in tv["nets"].values() for line in net["lines"]]
    assert lines and max(line["freq_hz"] for line in lines) > 1.05e9  # past the first point (1 GHz)


async def test_rate_limit(ws):
    _, server = make(ws, limiter=RateLimiter(rate=0.001, burst=2))
    async with Client(server) as c:
        await call(c, "list_benches")
        await call(c, "list_benches")
        r = await call(c, "list_benches")
    assert "rate limited" in r["error"]


async def test_fault_tools_are_control(ws):
    _, server = make(ws)
    async with Client(server) as c:
        await call(c, "start_session", path=BENCH)
        r = await call(c, "inject_fault", fault={"kind": "timeout", "instrument": "awg"})
        assert "--allow-control" in r["error"]
        assert await call(c, "list_faults") == []

    _, server = make(ws, allow_control=True)
    async with Client(server) as c:
        await call(c, "start_session", path=BENCH)
        f = await call(c, "inject_fault", fault={"kind": "timeout", "instrument": "awg"})
        assert f["status"] == "active"
        r = await call(c, "send_scpi", instrument="awg", command="*IDN?")
        assert r["timed_out"] is True
        await call(c, "clear_fault", fault_id=f["id"])
        r = await call(c, "send_scpi", instrument="awg", command="*IDN?")
        assert "SB-AWG-2" in r["response"]


async def test_components_are_listed_for_definition_authors(ws):
    _, server = make(ws)
    async with Client(server) as c:
        summary = await call(c, "list_components")
        internal = await call(c, "list_components", category="internal")
        detail = await call(c, "list_components", types=["function_generator_core", "output_stage"])
        unknown = await call(c, "list_components", types=["output_stag"])
        definition = await call(c, "get_definition", definition_id="sb-awg-2", limit=1)
    by_type = {e["type"]: e for e in summary}
    assert "scope_acquisition" in by_type and "run_mode" in by_type["scope_acquisition"]["operations"]
    assert all(e["category"] == "internal" for e in internal) and "waveform_transfer" in {
        e["type"] for e in internal
    }
    assert "output_stag" in unknown["error"]
    assert [w for w in definition["wiring"]["channel"]] and definition["ports"]["channel"]
    assert {c["type"] for c in definition["channel_components"]} >= {
        "waveform_synth",
        "amplitude_level",
        "output_stage",
    }

    # From the catalog alone, an agent writes a minimal definition that passes the static checks.
    gen, out = sorted(detail, key=lambda e: e["type"] != "function_generator_core")

    def port(entry, direction, medium):
        return next(
            p["name"] for p in entry["ports"] if p["direction"] == direction and p["medium"] == medium
        )

    params = {p["name"]: p for p in gen["params"]}
    draft = {
        "schema": "simubench.instrument/1",
        "id": "agent-gen",
        "manufacturer": "Agent",
        "model": "GEN-1",
        "class": "awg",
        "channels": {"count": 1},
        "channel_state": {
            "frequency": {
                "type": "float",
                "unit": "HZ",
                "min": 1.0,
                "max": 1e6,
                "default": params["frequency"]["default"],
            },
            "output": {"type": "bool", "default": False},
        },
        "channel_components": [
            {"name": "gen", "type": gen["type"], "params": {"frequency": "$ch.frequency"}},
            {"name": "out", "type": out["type"], "params": {"enabled": "$ch.output"}},
        ],
        "wiring": {"channel": [[f"gen.{port(gen, 'out', 'signal')}", f"out.{port(out, 'in', 'signal')}"]]},
        "ports": {"channel": {"OUT{n}": f"out.{port(out, 'out', 'electrical')}"}},
        "commands": [
            {"header": ":SOURce<ch>:FREQuency", "state": "ch.frequency"},
            {"header": ":OUTPut<ch>", "state": "ch.output"},
        ],
    }
    from simubench.engine.validate_definition import validate_definition_data

    _, report = validate_definition_data(draft)
    assert [d.as_dict() for d in report.diagnostics] == []


async def test_run_cases(ws):
    (ws / "awg.cases.yaml").write_text(
        f"bench: {BENCH}\ninstrument: awg\ncases:\n"
        "  - {name: identity, steps: [{query: '*IDN?', expect_match: '.*SB-AWG-2.*'}]}\n"
        "  - {name: wrong, steps: [{query: 'FREQ?', expect_value: 5.0}]}\n",
        encoding="utf-8",
    )
    _, server = make(ws)
    async with Client(server) as c:
        out = await call(c, "run_cases", path="awg.cases.yaml")
        outside = await call(c, "run_cases", path="../x.yaml")
    assert (out["passed"], out["failed"]) == (1, 1)
    wrong = out["cases"][1]
    assert (wrong["passed"], wrong["step"], wrong["command"]) == (False, 1, "FREQ?")
    assert wrong["actual"] == "+1.000000E+03" and "expected 5.0" in wrong["summary"]
    assert "error" in outside


CONTRACT_SCRIPT = """
import pyvisa

rm = pyvisa.ResourceManager()
dmm = rm.open_resource("TCPIP0::10.0.0.5::5025::SOCKET")
dmm.write("*RST")
dmm.write("MODE DCV")
assert dmm.query("MODE?").strip() == "DCV"
dmm.write("RANGE 10 V")
for ch in (1, 2):
    dmm.write(f"C{ch}:LEVEL 0.5")
    dmm.query(f"C{ch}:LEVEL?")
dmm.write("OUT ON")
print(dmm.query("SYST:ERR?"))
"""


async def test_an_agent_builds_a_definition_from_a_script(ws):
    """Issue 46: draft_definition_from_script -> edit_definition -> validate_definition -> run_cases."""
    (ws / "contract.py").write_text(CONTRACT_SCRIPT, encoding="utf-8")
    (ws / "dmm.cases.yaml").write_text(
        "model: agent-dmm\ninstrument: dmm\ncases:\n"
        "  - name: mode\n    steps:\n      - {send: 'MODE ACV'}\n      - {query: 'MODE?', expect: ACV}\n"
        "  - name: level per channel\n    steps:\n      - {send: 'C2:LEVEL 0.25'}\n"
        "      - {query: 'C2:LEVEL?', expect_value: 0.25}\n      - {query: 'C1:LEVEL?', expect_value: 0.5}\n"
        "  - name: range in volts\n    steps:\n      - {send: 'RANGE 100 mV'}\n"
        "      - {query: 'RANGE?', expect_value: 0.1, tolerance: 1.0e-12}\n"
        "  - name: identity\n    steps:\n      - {query: '*IDN?', expect_match: 'AGENT LABS,DMM-1,.*'}\n",
        encoding="utf-8",
    )
    _, server = make(ws)
    async with Client(server) as c:
        draft = await call(
            c, "draft_definition_from_script", script_path="contract.py", definition_id="agent-dmm", save=True
        )
        assert [d["header"] for d in draft["drafted"]] == [":MODE", ":RANGE", ":C<ch>:LEVEL", ":OUT"]
        # The loop's channel reads as 1 (an f-string placeholder): the agent sets the count itself.
        assert draft["definition"]["channels"]["count"] == 1
        saved = draft["saved"]
        assert saved["path"] == "instruments/agent-dmm.yaml" and saved["ok"], saved["diagnostics"]

        failing = await call(c, "run_cases", path="dmm.cases.yaml")
        assert failing["failed"] == 3  # no ACV option, one channel, a placeholder identity

        doc = await call(
            c,
            "edit_definition",
            definition_id="agent-dmm",
            base_sha256=saved["sha256"],
            patch=[
                {"op": "add", "path": "/state/mode/options/-", "value": "ACV"},
                {"op": "replace", "path": "/manufacturer", "value": "Agent Labs"},
                {"op": "replace", "path": "/model", "value": "DMM-1"},
                {"op": "replace", "path": "/channels/count", "value": 2},
                {"op": "test", "path": "/channel_state/c_level/type", "value": "float"},
            ],
        )
        assert doc["ok"] and doc["data"]["state"]["mode"]["options"] == ["DCV", "ACV"]
        assert (await call(c, "validate_definition", definition_id="agent-dmm")) == {
            "ok": True,
            "diagnostics": [],
        }
        passing = await call(c, "run_cases", path="dmm.cases.yaml")
        assert (passing["passed"], passing["failed"]) == (4, 0), passing

        stale = await call(
            c, "edit_definition", definition_id="agent-dmm", base_sha256=saved["sha256"], patch=[]
        )
        assert "read_definition" in stale["error"]
        bad = await call(
            c, "edit_definition", definition_id="agent-dmm", base_sha256=doc["sha256"],
            patch=[{"op": "remove", "path": "/state/nothing"}],
        )  # fmt: skip
        assert "补丁没有应用" in bad["error"]
    history = ws / ".simubench" / "history" / "instruments" / "agent-dmm.yaml" / "index.jsonl"
    assert [json.loads(line)["source"] for line in history.read_text(encoding="utf-8").splitlines()] == [
        "agent",
        "agent",
    ]


async def test_definitions_outside_the_workspace_are_read_only(ws):
    _, server = make(ws)
    async with Client(server) as c:
        doc = await call(c, "read_definition", definition_id="sb-awg-2")
        assert doc["editable"] is False and doc["path"] is None and doc["ok"]
        r = await call(c, "edit_definition", definition_id="sb-awg-2", base_sha256=doc["sha256"], patch=[])
        assert "create a copy" in r["error"]
        bad = await call(c, "validate_definition", data={**doc["data"], "channels": {"count": "two"}})
        assert bad["ok"] is False and bad["diagnostics"][0]["code"] == "E-SCHEMA"
        exists = await call(c, "create_definition", definition_id="sb-awg-2", data=doc["data"])
        assert exists["ok"]  # a workspace copy under the same id shadows the library's
        again = await call(c, "create_definition", definition_id="sb-awg-2", data=doc["data"])
        assert "already exists" in again["error"]
        wrong = await call(c, "create_definition", definition_id="other", data=doc["data"])
        assert "holds the definition" in wrong["error"]


async def test_a_session_names_the_definitions_to_restart_for(ws):
    manager, server = make(ws)
    async with Client(server) as c:
        doc = await call(c, "read_definition", definition_id="sb-awg-2")
        copy = await call(c, "create_definition", definition_id="sb-awg-2", data=doc["data"])
        await call(c, "start_session", path=BENCH)
        assert manager.describe(next(iter(manager.sessions.values()))).stale_definitions == []
        edited = await call(
            c, "edit_definition", definition_id="sb-awg-2", base_sha256=copy["sha256"],
            patch=[{"op": "replace", "path": "/description", "value": "changed"}],
        )  # fmt: skip
        [sid] = manager.sessions
        assert edited["sessions_to_restart"] == [sid]
        assert manager.describe(manager.sessions[sid]).stale_definitions == ["sb-awg-2"]


async def test_an_agent_builds_a_scope_from_a_template(ws):
    """Issue 125: list_templates -> get_template -> create_definition_from_template, saved by the agent."""
    _, server = make(ws)
    async with Client(server) as c:
        assert [t["id"] for t in await call(c, "list_templates")] == ["oscilloscope", "signal_source"]
        tpl = await call(c, "get_template", template_id="oscilloscope")
        assert tpl["schema"]["properties"]["vertical"]["title_en"] == "Vertical"
        missing = await call(c, "get_template", template_id="nothing")
        assert "oscilloscope" in missing["error"] and "signal_source" in missing["error"]

        inputs = tpl["example"]
        wrong = json.loads(json.dumps(inputs))
        wrong["vertical"]["table"][1]["v_div"] = 0.001  # the same as the row before
        refused = await call(
            c,
            "create_definition_from_template",
            template_id="oscilloscope",
            definition_id="agent-dso",
            inputs=wrong,
        )
        assert refused["saved"] is False
        assert [(d["code"], d["pointer"]) for d in refused["diagnostics"]] == [
            ("E-TPL-ORDER", "/template/inputs/vertical/table/1/v_div")
        ]
        assert not (ws / "instruments" / "agent-dso.yaml").exists()

        doc = await call(
            c,
            "create_definition_from_template",
            template_id="oscilloscope",
            definition_id="agent-dso",
            inputs=inputs,
        )
        assert doc["saved"] and doc["ok"] and doc["diagnostics"] == [], doc["diagnostics"]
        assert doc["data"]["template"] == {"id": "oscilloscope", "version": 1, "inputs": inputs}

        # New inputs, expanded again over the saved file.
        inputs["channels"]["count"] = 2
        inputs["adc"]["rate_pairs"] = [[1, 2]]
        again = await call(
            c, "create_definition_from_template", template_id="oscilloscope", definition_id="agent-dso",
            inputs=inputs, base_sha256=doc["sha256"],
        )  # fmt: skip
        assert again["saved"] and again["data"]["channels"]["count"] == 2
        stale = await call(
            c, "create_definition_from_template", template_id="oscilloscope", definition_id="agent-dso",
            inputs=inputs, base_sha256=doc["sha256"],
        )  # fmt: skip
        assert "error" in stale
    history = ws / ".simubench" / "history" / "instruments" / "agent-dso.yaml" / "index.jsonl"
    assert [json.loads(line)["source"] for line in history.read_text(encoding="utf-8").splitlines()] == [
        "agent",
        "agent",
    ]


async def test_try_definition_agrees_with_a_bench_session(ws):
    """try_definition's replies are what the same commands get on a deployed bench (T2 acceptance)."""
    _, server = make(ws)
    async with Client(server) as c:
        doc = await call(c, "create_bench", path="try.bench.yaml", name="try")
        ops = [
            {"op": "add_instrument", "name": "dmm", "model": "sb-dmm-1"},
            {"op": "add_instrument", "name": "dmm2", "model": "keysight-34465a"},
        ]
        doc = await call(c, "edit_bench", path="try.bench.yaml", base_sha256=doc["sha256"], ops=ops)
        assert doc["ok"], doc
        await call(c, "start_session", path="try.bench.yaml")
        commands = {
            "sb-dmm-1": ("dmm", ["*IDN?", "*RST", ":MEAS:VOLT:DC?", ":MEAS:VOLT:AC?", ":CALibration:COUNt?"]),
            "keysight-34465a": ("dmm2", ["*IDN?", ":CONF:VOLT:DC 10,0.001", ":MEAS:VOLT:DC?", ":READ?"]),
        }
        for model, (name, cmds) in commands.items():
            on_bench = [await call(c, "send_scpi", instrument=name, command=cmd) for cmd in cmds]
            tried = await call(c, "try_definition", definition_id=model, commands=cmds)
            assert tried["definition"] == model and tried["instrument"] == model
            assert [r["response"] for r in tried["results"]] == [r["response"] for r in on_bench]
            assert tried["results"][0]["response"]  # the *IDN? reply came through in both worlds


async def test_try_definition_reports_errors_and_captures_state(ws):
    _, server = make(ws)
    async with Client(server) as c:
        tried = await call(
            c, "try_definition", definition_id="sb-dmm-1", commands=["*IDN?", ":NOPE?", ":MEAS:VOLT:DC?"]
        )
        [idn, nope, meas] = tried["results"]
        assert idn["response"].startswith("SIMUBENCH,SB-DMM-1,") and idn["errors"] == []
        assert nope["errors"] and nope["errors"][0]["code"] == -113
        assert meas["response"] is not None and meas["errors"] == []
        assert c is not None
        # the open bench (none here) is untouched: try sessions are not bench sessions
        assert await call(c, "list_sessions") == []
        unknown = await call(c, "try_definition", definition_id="no-such-model", commands=["*IDN?"])
        assert "no-such-model" in unknown["error"]


async def test_scpi_exchanges_have_one_shape(ws):
    """One SCPI-exchange shape (issue 627): send_scpi returns exactly what the web API's
    SessionManager.scpi shapes (M.ScpiResponse), and a try_definition item adds only its command.
    The MCP tools used to carry their own dicts, with seconds instead of ns."""
    manager, server = make(ws)
    fields = set(M.ScpiResponse(response=None, now_ns=0).model_dump(mode="json"))
    async with Client(server) as c:
        s = await call(c, "start_session", path=BENCH)
        mcp = await call(c, "send_scpi", instrument="scope", command="*IDN?")
        http = SessionManager.scpi(manager.get(s["session_id"]), "scope", "*IDN?", client="mcp").model_dump(
            mode="json"
        )
        assert set(mcp) == fields
        mcp.pop("now_ns")
        http.pop("now_ns")  # the two exchanges move virtual time separately
        assert mcp == http
        tried = await call(c, "try_definition", definition_id="sb-dmm-1", commands=["*IDN?", ":NOPE 1"])
        for item in tried["results"]:
            assert set(item) == {"command"} | fields
        assert tried["results"][0]["response"] and tried["results"][0]["timed_out"] is False
        assert tried["results"][1]["errors"][0]["code"] == -113


async def test_try_definition_runs_dangerous_commands_without_confirm(ws):
    """The sandbox is the simulation: the command send_scpi refuses without confirm (see
    test_build_deploy_drive_observe) runs in a try session, and its effect is visible."""
    _, server = make(ws)
    async with Client(server) as c:
        dangerous = ":SOUR1:FREQ 1000;:OUTP1 ON"  # outputs on: send_scpi asks for confirm=true
        tried = await call(
            c, "try_definition", definition_id="sb-awg-2", commands=[dangerous, ":SOUR1:FREQ?"]
        )
        assert tried["results"][1]["response"] == "+1.000000E+03"


async def test_edit_definition_reports_what_changed(ws):
    """create/edit_definition return the change description (T3): the agent can report it verbatim."""
    _, server = make(ws)
    async with Client(server) as c:
        data = json.loads(
            json.dumps(
                yaml.safe_load((ROOT / "validation/instruments/sb-dmm-1.yaml").read_text(encoding="utf-8"))
            )
        )
        doc = await call(c, "create_definition", definition_id="sb-dmm-1", data=data)
        assert doc["changes"] and doc["changes"][0]["action"] == "added"  # the creation, described
        edited = await call(
            c,
            "edit_definition",
            definition_id="sb-dmm-1",
            base_sha256=doc["sha256"],
            patch=[{"op": "replace", "path": "/components/0/params/z_in", "value": 1.0e9}],
        )
        assert edited["changes"] == [
            {
                "kind": "component",
                "action": "changed",
                "target": "input.z_in",
                "detail": None,
                "old": 10.0e6,
                "new": 1.0e9,
            }
        ]
        again = await call(c, "read_definition", definition_id="sb-dmm-1")
        assert again["changes"] == []  # a read reports nothing


async def test_mcp_fault_list_converts_inside_the_view(ws, monkeypatch):
    """#489: the MCP list_faults tool assembles its DTO inside the session's view — through the
    real tool, with a Tracked session lock flagging the depth reaching zero between the snapshot
    and the last field read."""
    import threading

    import simubench.mcp_server

    manager, server = make(ws, allow_control=True)
    async with Client(server) as c:
        await call(c, "start_session", path=BENCH)
        f = await call(c, "inject_fault", fault={"kind": "timeout", "instrument": "awg"})
        assert f["status"] in ("pending", "active")
        s = next(iter(manager.sessions.values())).session

        seen = {"snapshot": False, "read": False}
        violations: list[int] = []
        real = threading.RLock()
        depth = {"n": 0}

        class Tracked:
            def __enter__(self):
                real.acquire()
                depth["n"] += 1
                return self

            def __exit__(self, *exc):
                depth["n"] -= 1
                real.release()
                if seen["snapshot"] and not seen["read"] and depth["n"] == 0:
                    violations.append(depth["n"])
                return False

        real_snapshot = s.faults.snapshot

        def snapshot():
            seen["snapshot"] = True
            return real_snapshot()

        real_fault = simubench.mcp_server._fault

        def _fault(x):
            out = real_fault(x)
            seen["read"] = True
            return out

        monkeypatch.setattr(s.faults, "snapshot", snapshot)
        monkeypatch.setattr(s, "_lock", Tracked())
        monkeypatch.setattr(simubench.mcp_server, "_fault", _fault)

        out = await call(c, "list_faults")
        assert len(out) == 1
        assert violations == []


async def test_try_definition_sessions_are_pooled_and_reused(ws):
    """A second batch for the same definition runs on the same scratch session instead of cycling
    a bench per call; the pool owns the semantics, so MCP adds none of its own (issue 636)."""
    manager, server = make(ws)
    async with Client(server) as c:
        first = await call(c, "try_definition", definition_id="sb-dmm-1", commands=["*IDN?"])
        assert [e.definition for e in manager.tries.all()] == ["sb-dmm-1"]
        id1 = [e.served.id for e in manager.tries.all()]
        second = await call(c, "try_definition", definition_id="sb-dmm-1", commands=["*IDN?"])
        assert [e.served.id for e in manager.tries.all()] == id1  # the same session, not a new one
        assert [r["response"] for r in first["results"]] == [r["response"] for r in second["results"]]
        # an unknown definition still fails before anything builds
        unknown = await call(c, "try_definition", definition_id="no-such-model", commands=["*IDN?"])
        assert "error" in unknown
        assert manager.tries.all() == [] or all(e.definition != "no-such-model" for e in manager.tries.all())


async def test_the_time_fields_of_every_tool_agree_on_name_and_unit(ws):
    """One time field across the MCP tools (issue 653): ``now_ns``, nanoseconds — send_scpi,
    advance_clock and the session summary read the same instant as the same field."""
    manager, server = make(ws, allow_control=True)
    async with Client(server) as c:
        s = await call(c, "start_session", path=BENCH)
        sid = s["session_id"]
        assert set(s) >= {"now_ns"} and "now_s" not in s  # the session summary too
        sent = await call(c, "send_scpi", instrument="scope", command="*IDN?")
        advanced = await call(c, "advance_clock", seconds=1.0)
        assert "now_s" not in advanced
        assert advanced["now_ns"] - sent["now_ns"] >= 1_000_000_000  # nanoseconds, not seconds
        assert manager.get(sid).session.now_ns == advanced["now_ns"]


async def test_try_definition_state_carries_over_between_batches(ws):
    """What one batch sets is there in the next batch: the same session, not a fresh one (issue 671)."""
    manager, server = make(ws)
    async with Client(server) as c:
        await call(c, "try_definition", definition_id="sb-dmm-1", commands=["*ESE 32"])
        second = await call(c, "try_definition", definition_id="sb-dmm-1", commands=["*ESE?"])
        assert second["results"][0]["response"].strip() == "32"
        assert len(manager.tries.all()) == 1


async def test_describe_session_describes_once(ws, monkeypatch):
    """The topology tool re-projects the session once (issue 641 → issue 691): the summary takes
    the described info instead of describing again."""
    manager, server = make(ws)
    async with Client(server) as c:
        s = await call(c, "start_session", path=BENCH)
        sid = s["session_id"]
        calls = 0
        original = manager.describe

        def counted(session):
            nonlocal calls
            calls += 1
            return original(session)

        manager.describe = counted  # type: ignore[method-assign]
        await call(c, "describe_session", session_id=sid)
        assert calls == 1
