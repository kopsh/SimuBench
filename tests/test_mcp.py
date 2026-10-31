import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import Client

from simubench.mcp_server import RateLimiter, build_server
from simubench.server.manager import SessionManager

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


def result(r):
    if r.is_error:
        return {"error": r.content[0].text}
    if r.structured_content is not None:
        sc = r.structured_content
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(r.content[0].text)


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
    # The DMM's :CONF:VOLT:DC is supported by the real model now: it joins the draft.
    assert d["bench"]["instruments"]["dmm"]["model"] == "keysight-34465a"
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
        assert (await call(c, "advance_clock", seconds=0.5))["now_s"] >= 0.5
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
        "function_generator_core",
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
        assert [t["id"] for t in await call(c, "list_templates")] == ["oscilloscope"]
        tpl = await call(c, "get_template", template_id="oscilloscope")
        assert tpl["schema"]["properties"]["vertical"]["title_en"] == "Vertical"
        missing = await call(c, "get_template", template_id="nothing")
        assert "oscilloscope" in missing["error"]

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
