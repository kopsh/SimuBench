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
    shutil.copy(ROOT / "examples" / "benches" / BENCH, tmp_path / BENCH)
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
    assert "dmm" not in d["bench"]["instruments"]
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
    assert tv["duts"]["rc"]["r"] == 1000.0
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
