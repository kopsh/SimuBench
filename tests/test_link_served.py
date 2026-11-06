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


def current_address(c, info) -> str:
    """The link address now: a reset starts the service's ports again, and with port 0 they move."""
    return c.get(f"/api/sessions/{info['id']}").json()["link_address"]


# ---- waits of a connected script on a served bench (the clock guard of #121 must not stop them) ------------------


def test_a_read_timeout_on_a_served_bench_is_a_timeout_and_costs_virtual_time(served):
    from pyvisa import errors

    c, info, manager = served
    session = manager.sessions[info["id"]].session
    start = session.now_ns
    rm = pyvisa.ResourceManager(f"{current_address(c, info)}@simubench")
    try:
        awg = rm.open_resource("awg", read_termination="\n")
        awg.timeout = 300
        with pytest.raises(errors.VisaIOError) as err:
            awg.read()  # nothing was asked: the instrument has nothing to say
        assert err.value.error_code == constants.VI_ERROR_TMO  # not a system error from the clock guard
    finally:
        rm.close()
    assert (session.now_ns - start) / 1e9 >= 0.3  # the script's own wait moved the shared clock


def test_wait_for_srq_on_a_served_bench_returns(served):
    c, info, _ = served
    rm = pyvisa.ResourceManager(f"{current_address(c, info)}@simubench")
    try:
        awg = rm.open_resource("awg", read_termination="\n", write_termination="\n")
        for command in ("*CLS", "*ESE 1", "*SRE 32", "*OPC"):
            awg.write(command)
        awg.enable_event(constants.EventType.service_request, constants.EventMechanism.queue)
        awg.wait_on_event(
            constants.EventType.service_request, 1000
        )  # the request is there: the wait ends at once
        assert awg.read_stb() & 0x20
    finally:
        rm.close()


def test_the_uis_explicit_advance_is_still_refused_while_a_script_is_connected(served):
    c, info, _ = served
    rm = pyvisa.ResourceManager(f"{current_address(c, info)}@simubench")
    try:
        awg = rm.open_resource("awg")  # kept: a resource that is collected is closed
        assert awg is not None
        r = c.post(f"/api/sessions/{info['id']}/clock/advance", json={"seconds": 0.01})
        assert r.status_code == 409  # a script is connected: moving its time needs a confirmation
    finally:
        rm.close()


# ---- the addresses the service itself serves ------------------------------------------------------------------------


def test_the_addresses_the_service_prints_open_through_the_link(served):
    """The ports are the service's choice, so what `serve` prints has to be found in connected mode too."""
    c, info, _ = served
    now = c.get(f"/api/sessions/{info['id']}").json()
    awg = next(i for i in now["instruments"] if i["name"] == "awg")
    assert awg["address_virtual"] and awg["address_hislip"]
    rm = pyvisa.ResourceManager(f"{now['link_address']}@simubench")
    try:
        for address in (awg["address_virtual"], awg["address_hislip"]):
            inst = rm.open_resource(address, read_termination="\n", write_termination="\n")
            assert inst.query("*IDN?").startswith("SIMUBENCH,SB-AWG-2"), address
        assert awg["address_virtual"] in rm.list_resources("?*")
    finally:
        rm.close()
