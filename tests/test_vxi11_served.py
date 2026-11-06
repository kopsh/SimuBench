"""VXI-11 through the service: addresses in the session info, remote control by a VXI-11 script (D63)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import pyvisa
from fastapi.testclient import TestClient

from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
BENCH = "benches/awg_rc_scope.bench.yaml"
TOKEN = "test-token"


@pytest.fixture(scope="module")
def served():
    manager = SessionManager(ROOT / "validation", scpi_base_port=0, hislip_port=0, vxi11_port=0)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        info = c.post("/api/sessions", json={"bench_path": BENCH}).json()
        yield c, info, manager
        c.delete(f"/api/sessions/{info['id']}", params={"force": True})


def test_session_info_lists_the_vxi11_address_of_every_instrument(served):
    _, info, manager = served
    port = manager.sessions[info["id"]].vxi11.port
    by_name = {i["name"]: i["address_vxi11"] for i in info["instruments"]}
    assert (
        by_name["awg"] == f"TCPIP0::127.0.0.1,{port}::inst0::INSTR"
    )  # no port mapper: the port is spelled out
    assert by_name["scope"] == f"TCPIP0::127.0.0.1,{port}::inst1::INSTR"


def test_a_vxi11_script_makes_the_instrument_remote(served):
    c, info, _ = served
    sid = info["id"]
    rm = pyvisa.ResourceManager("@py")
    try:
        scope = rm.open_resource(
            info["instruments"][1]["address_vxi11"], read_termination="\n", write_termination="\n"
        )
        assert scope.query("*IDN?").startswith("SIMUBENCH,SB-DSO-4")
        deadline = time.monotonic() + 5
        inst = {}
        while time.monotonic() < deadline:
            inst = c.get(f"/api/sessions/{sid}/instruments/scope").json()
            if inst["remote"]:
                break
            time.sleep(0.02)
        assert inst["remote"][0].startswith("vxi11:127.0.0.1:")
        r = c.post(
            f"/api/sessions/{sid}/instruments/scope/configure", json={"path": "ch1.scale", "value": 0.5}
        )
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "remote"
        clients = c.get(f"/api/sessions/{sid}").json()["clients"]
        assert [x["instrument"] for x in clients] == ["scope"] and not clients[0]["locked"]
        scope.lock_excl()  # PyVISA's lock call over VXI-11 is device_lock
        assert c.get(f"/api/sessions/{sid}").json()["clients"][0]["locked"]
        scope.unlock()
        scope.close()
    finally:
        rm.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("vxi11_port", "portmapper_port", "no_scpi", "expected"),
    [
        (None, None, False, None),  # off by default: a random port would make launcher output unstable
        (0, None, False, 0),  # asked for, the OS chooses
        (4999, None, False, 4999),
        (None, 111, False, 0),  # a port mapper needs VXI-11 behind it
        (0, 111, True, None),  # --no-scpi turns every network port off
    ],
)
def test_the_command_line_turns_vxi11_on_only_when_asked(vxi11_port, portmapper_port, no_scpi, expected):
    from types import SimpleNamespace

    from simubench.server.manager import vxi11_port_of

    args = SimpleNamespace(vxi11_port=vxi11_port, portmapper_port=portmapper_port, no_scpi=no_scpi)
    assert vxi11_port_of(args) == expected


def test_a_reset_serves_vxi11_again_on_the_new_session(served):
    c, info, _ = served
    sid = info["id"]
    r = c.post(f"/api/sessions/{sid}/reset", json={"force": True})
    assert r.status_code == 200, r.text
    fresh = r.json()
    rm = pyvisa.ResourceManager("@py")
    try:
        awg = rm.open_resource(
            fresh["instruments"][0]["address_vxi11"], read_termination="\n", write_termination="\n"
        )
        assert awg.query("*IDN?").startswith("SIMUBENCH,SB-AWG-2")
        awg.close()
    finally:
        rm.close()
