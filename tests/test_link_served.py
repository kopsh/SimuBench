"""The bench link through the service: the address in the session info, a connected script as a remote client (D63)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import pyvisa
from fastapi.testclient import TestClient
from pyvisa import constants

from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
BENCH = "benches/awg_rc_scope.bench.yaml"
TOKEN = "test-token"


@pytest.fixture(scope="module")
def served():
    manager = SessionManager(ROOT / "validation", scpi_base_port=0, hislip_port=0, link_port=0)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        info = c.post("/api/sessions", json={"bench_path": BENCH}).json()
        yield c, info, manager
        c.delete(f"/api/sessions/{info['id']}", params={"force": True})


def test_the_session_info_says_where_a_script_connects(served):
    _, info, manager = served
    assert info["link_address"] == manager.sessions[info["id"]].link.address()
    assert info["link_address"].startswith("127.0.0.1:")


def test_a_connected_script_makes_the_instrument_remote_and_locks_show(served):
    c, info, _ = served
    sid = info["id"]
    rm = pyvisa.ResourceManager(f"{info['link_address']}@simubench")
    try:
        scope = rm.open_resource("scope", read_termination="\n", write_termination="\n")
        assert scope.query("*IDN?").startswith("SIMUBENCH,SB-DSO-4")
        inst = c.get(f"/api/sessions/{sid}/instruments/scope").json()
        assert inst["remote"] and inst["remote"][0].startswith("bench:127.0.0.1:")
        r = c.post(
            f"/api/sessions/{sid}/instruments/scope/configure", json={"path": "ch1.scale", "value": 0.5}
        )
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "remote"  # the UI keeps out (#121)
        clients = c.get(f"/api/sessions/{sid}").json()["clients"]
        assert [x["instrument"] for x in clients] == ["scope"] and not clients[0]["locked"]
        scope.lock_excl()
        assert c.get(f"/api/sessions/{sid}").json()["clients"][0]["locked"]
        scope.unlock()
    finally:
        rm.close()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not c.get(f"/api/sessions/{sid}/instruments/scope").json()["remote"]:
            break
        time.sleep(0.02)
    assert not c.get(f"/api/sessions/{sid}/instruments/scope").json()["remote"]  # gone with the connection


def test_the_events_name_the_connected_script(served):
    _, info, manager = served
    rm = pyvisa.ResourceManager(f"{info['link_address']}@simubench")
    try:
        awg = rm.open_resource("awg", read_termination="\n", write_termination="\n")
        awg.query("*OPC?")
    finally:
        rm.close()
    session = manager.sessions[info["id"]].session
    clients = {e.get("client") for e in session.history if e["kind"] == "scpi"}
    assert any(label and label.startswith("bench:") for label in clients)


def test_a_reset_serves_the_link_again_at_the_same_address(served):
    c, info, _ = served
    r = c.post(f"/api/sessions/{info['id']}/reset", json={"force": True})
    assert r.status_code == 200, r.text
    rm = pyvisa.ResourceManager(f"{r.json()['link_address']}@simubench")
    try:
        awg = rm.open_resource("awg", read_termination="\n", write_termination="\n")
        assert awg.query("*IDN?").startswith("SIMUBENCH,SB-AWG-2")
        assert (
            awg.get_visa_attribute(constants.ResourceAttribute.interface_type)
            == constants.InterfaceType.tcpip
        )
    finally:
        rm.close()


@pytest.mark.unit
def test_bench_is_a_remote_client_prefix():
    from simubench.server.manager import is_remote

    assert is_remote("bench:127.0.0.1:50000#0001") and not is_remote("pyvisa") and not is_remote(None)
