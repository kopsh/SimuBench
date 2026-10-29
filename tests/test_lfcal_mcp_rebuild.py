"""Issue 48: the low-frequency calibration bench rebuilt from an empty workspace through MCP alone,
then checked by the same tests as the example (tests/test_lowfreq_cal.py and the acceptance scenario).

The test plays the agent: it has the two definitions and the bench in mind (the example's content)
and writes them only with MCP tools; no file is copied into the workspace except the cases file, which
is the host program's contract the agent is given.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import Client

from simubench.library.loader import load_yaml
from simubench.mcp_server import RateLimiter, build_server
from simubench.server.benchfiles import fingerprint
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "lowfreq-cal"
DEFINITIONS = ["lfcal-reference-scope", "lfcal-scope-calibrator"]
BENCH = "lowfreq_cal.bench.yaml"


def result(r):
    if r.is_error:
        raise AssertionError(r.content[0].text)
    sc = r.structured_content
    return sc.get("result", sc) if set(sc) == {"result"} else sc


async def call(client, tool, /, **args):
    return result(await client.call_tool(tool, args))


def bench_ops(bench: dict) -> list[dict]:
    """The edit operations that build ``bench``: instruments, identities, initial state, wires."""
    ops: list[dict] = [{"op": "set_meta", "description": bench.get("description")}]
    for name, inst in bench["instruments"].items():
        xy = bench.get("layout", {}).get(name, {})
        ops.append(
            {"op": "add_instrument", "name": name, "model": inst["model"], "label": inst.get("label"), **xy}
        )
        if inst.get("idn"):
            ops.append({"op": "set_identity", "instrument": name, "idn": inst["idn"]})
        for path, value in inst.get("state", {}).items():
            ops.append({"op": "set_state", "instrument": name, "path": path, "value": value})
    for w in bench["wires"]:
        ops.append(
            {"op": "connect", "from": w["from"], "to": w["to"], "accessories": w.get("accessories", [])}
        )
    ops.append({"op": "set_run", **bench["run"]})
    return ops


@pytest.fixture
def mcp_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    shutil.copy(EXAMPLE / "cases.yaml", ws / "cases.yaml")  # the contract, given to the agent
    return ws


async def test_the_bench_is_rebuilt_through_mcp_alone(mcp_workspace):
    ws = mcp_workspace
    manager = SessionManager(ws, scpi_base_port=None)
    server = build_server(manager, limiter=RateLimiter(rate=1000, burst=1000))
    definitions = {
        d: load_yaml((EXAMPLE / "instruments" / f"{d}.yaml").read_text("utf-8")) for d in DEFINITIONS
    }
    example_bench = load_yaml((EXAMPLE / BENCH).read_text("utf-8"))
    async with Client(server) as c:
        # Every component the definitions use is a platform component the catalog describes.
        used = sorted(
            {
                comp["type"]
                for d in definitions.values()
                for comp in d.get("components", []) + d.get("channel_components", [])
            }
        )
        catalog = await call(c, "list_components", types=used)
        assert sorted(e["type"] for e in catalog) == used
        assert all(e["category"] == "internal" for e in catalog)

        for def_id, data in definitions.items():
            doc = await call(c, "create_definition", definition_id=def_id, data=data)
            assert doc["ok"], doc["diagnostics"]
            assert await call(c, "validate_definition", definition_id=def_id) == {
                "ok": True,
                "diagnostics": [],
            }

        bench = await call(c, "create_bench", path=BENCH, name=example_bench["name"])
        bench = await call(
            c, "edit_bench", path=BENCH, base_sha256=bench["sha256"], ops=bench_ops(example_bench)
        )
        assert bench["ok"], bench["diagnostics"]
        assert bench["fingerprint"] == fingerprint(example_bench)  # the same bench as the example

        cases = await call(c, "run_cases", path="cases.yaml")
        assert cases["failed"] == 0, [x["summary"] for x in cases["cases"] if not x["passed"]]
    assert sorted(p.name for p in (ws / "instruments").iterdir()) == [f"{d}.yaml" for d in DEFINITIONS]
    assert not any(p.suffix == ".py" for p in ws.rglob("*"))  # definitions only, no code

    # The example's tests, on the workspace the agent built.
    env = {**os.environ, "SIMUBENCH_LFCAL_WORKSPACE": str(ws)}
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_lowfreq_cal.py",
            "acceptance/test_lowfreq_cal_scenario.py",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
