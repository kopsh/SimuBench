"""While a script is connected, the UI does not disturb the instruments behind its back (#121).

An instrument with a client on its SCPI port is remote: the UI's settings, console and panel
buttons are refused until LOCAL, which lasts until the script's next command. Operations that drop
or disturb the connected scripts (start, stop, reset, advance) need ``force``.
"""

import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simubench.api import open_bench
from simubench.engine.session import Session
from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
BENCH = "benches/awg_rc_scope.bench.yaml"
TOKEN = "test-token"


@pytest.fixture
def served():
    manager = SessionManager(ROOT / "validation", scpi_base_port=0, hislip_port=0)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        info = c.post("/api/sessions", json={"bench_path": BENCH}).json()
        yield c, info, manager
        c.delete(f"/api/sessions/{info['id']}", params={"force": True})


def port_of(info: dict, name: str) -> int:
    return next(i["scpi_port"] for i in info["instruments"] if i["name"] == name)


def connect(c: TestClient, info: dict, name: str) -> socket.socket:
    """A script on ``name``'s raw socket port, once the session lists it."""
    sock = socket.create_connection(("127.0.0.1", port_of(info, name)), timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        inst = c.get(f"/api/sessions/{info['id']}/instruments/{name}").json()
        if inst["remote"]:
            return sock
        time.sleep(0.02)
    sock.close()
    raise AssertionError("the script never showed up as a client")


def query(sock: socket.socket, command: bytes) -> bytes:
    sock.sendall(command + b"\n")
    buf = b""
    while not buf.endswith(b"\n"):
        buf += sock.recv(4096)
    return buf


def test_no_client_no_change(served):
    """Without connected scripts every UI operation works as before."""
    c, info, _ = served
    sid = info["id"]
    assert c.get(f"/api/sessions/{sid}/instruments/scope").json()["remote"] == []
    r = c.post(f"/api/sessions/{sid}/instruments/scope/configure", json={"path": "ch1.scale", "value": 0.5})
    assert r.status_code == 200 and r.json()["ok"]
    assert c.post(f"/api/sessions/{sid}/instruments/scope/scpi", json={"command": "*IDN?"}).status_code == 200
    assert c.post(f"/api/sessions/{sid}/instruments/scope/panel", json={"action": "stop"}).status_code == 200
    assert c.post(f"/api/sessions/{sid}/clock/advance", json={"seconds": 0.01}).status_code == 200
    assert c.post(f"/api/sessions/{sid}/reset", json={}).status_code == 200


def test_remote_instrument_refuses_ui_operations(served):
    c, info, _ = served
    sid = info["id"]
    with connect(c, info, "scope") as sock:
        assert float(query(sock, b":CHAN1:SCAL 0.2;:CHAN1:SCAL?")) == 0.2
        inst = c.get(f"/api/sessions/{sid}/instruments/scope").json()
        assert inst["remote"][0].startswith("socket:127.0.0.1:") and not inst["local"]
        session = c.get(f"/api/sessions/{sid}").json()
        assert [x["instrument"] for x in session["clients"]] == ["scope"]

        for path, body in [
            ("configure", {"path": "ch1.scale", "value": 0.5}),
            ("scpi", {"command": ":CHAN1:SCAL 0.5"}),
            ("panel", {"action": "stop"}),
        ]:
            r = c.post(f"/api/sessions/{sid}/instruments/scope/{path}", json=body)
            assert r.status_code == 409, path
            detail = r.json()["detail"]
            assert detail["reason"] == "remote" and detail["instrument"] == "scope"
            assert detail["clients"] == inst["remote"]
        assert float(query(sock, b":CHAN1:SCAL?")) == 0.2  # the script's setting stands

        # The other instrument has no client: still free.
        r = c.post(
            f"/api/sessions/{sid}/instruments/awg/configure", json={"path": "ch1.output", "value": True}
        )
        assert r.status_code == 200 and r.json()["ok"]


def test_local_until_the_scripts_next_command(served):
    c, info, manager = served
    sid = info["id"]
    with connect(c, info, "scope") as sock:
        inst = c.post(f"/api/sessions/{sid}/instruments/scope/local").json()
        assert inst["local"] and inst["remote"]
        r = c.post(
            f"/api/sessions/{sid}/instruments/scope/configure", json={"path": "ch1.scale", "value": 0.5}
        )
        assert r.status_code == 200 and r.json()["ok"]
        r = c.post(f"/api/sessions/{sid}/instruments/scope/scpi", json={"command": ":CHAN1:SCAL?"})
        assert r.status_code == 200 and float(r.json()["response"]) == 0.5

        query(sock, b"*IDN?")  # the script's next command returns the instrument to remote
        assert not c.get(f"/api/sessions/{sid}/instruments/scope").json()["local"]
        r = c.post(f"/api/sessions/{sid}/instruments/scope/configure", json={"path": "ch1.scale", "value": 1})
        assert r.status_code == 409

        kinds = [
            (e["action"], e["source"]) for e in manager.get(sid).session.history if e["kind"] == "remote"
        ]
        assert kinds[0] == ("local", "ui") and kinds[1][0] == "remote" and kinds[1][1].startswith("socket:")


def test_operations_on_the_whole_bench_need_force(served):
    c, info, _ = served
    sid = info["id"]
    with connect(c, info, "scope"):
        for method, url, kwargs in [
            ("post", f"/api/sessions/{sid}/clock/advance", {"json": {"seconds": 0.01}}),
            ("post", f"/api/sessions/{sid}/reset", {"json": {}}),
            ("post", "/api/sessions", {"json": {"bench_path": BENCH}}),
            ("delete", f"/api/sessions/{sid}", {}),
        ]:
            r = getattr(c, method)(url, **kwargs)
            assert r.status_code == 409, url
            detail = r.json()["detail"]
            assert detail["reason"] == "clients_connected" and detail["clients"][0].startswith("socket:")
        assert c.get(f"/api/sessions/{sid}").status_code == 200  # nothing happened

        r = c.post(f"/api/sessions/{sid}/clock/advance", json={"seconds": 0.01, "force": True})
        assert r.status_code == 200
        r = c.post(f"/api/sessions/{sid}/reset", json={"force": True})
        assert r.status_code == 200 and r.json()["id"] == sid  # the script was dropped
        assert r.json()["clients"] == []


def test_exchange_is_one_action(monkeypatch):
    """A script's write and the read of its response are one action: a UI console query sent in
    between waits, and each side gets its own response (before, the console could take it)."""
    session = open_bench(ROOT / "validation" / BENCH)
    ui: list[list[bytes]] = []
    write = Session.write

    def write_then_ui_query(self, name, data, client="sdk"):
        write(self, name, data, client)
        if client == "script":  # the UI console fires right after the script's write
            t = threading.Thread(
                target=lambda: ui.append(self.exchange(name, b"*IDN?", client="ui", reads=1))
            )
            t.start()
            t.join(0.2)  # it cannot run while the script's exchange holds the session

    monkeypatch.setattr(Session, "write", write_then_ui_query)
    try:
        got = session.exchange("scope", b":CHAN1:SCAL?", client="script")
        deadline = time.monotonic() + 5
        while not ui and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(got) == 1 and float(got[0]) > 0
        assert ui and ui[0][0].startswith(b"SIMUBENCH")
    finally:
        session.close()
