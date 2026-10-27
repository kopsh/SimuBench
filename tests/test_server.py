import asyncio
import socket
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simubench.server.app import _host_ok, create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token"


@pytest.fixture
def client():
    manager = SessionManager(ROOT / "examples", scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        yield c


def test_host_check():
    assert _host_ok("127.0.0.1:8765") and _host_ok("localhost") and _host_ok("http://localhost:5173")
    assert not _host_ok("evil.example:8765") and not _host_ok("http://evil.example")


def test_token_required(client):
    assert client.get("/api/health").status_code == 200
    r = client.get("/api/benches", headers={"x-simubench-token": "wrong"})
    assert r.status_code == 401


def test_rebinding_host_rejected(client):
    r = client.get("/api/health", headers={"host": "attacker.example"})
    assert r.status_code == 403


def test_session_lifecycle_and_scpi(client):
    benches = client.get("/api/benches").json()
    assert any(b["path"] == "benches/awg_rc_scope.bench.yaml" for b in benches)
    info = client.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()
    sid = info["id"]
    assert {i["name"] for i in info["instruments"]} == {"awg", "scope"}
    assert info["nets"] and info["wires"][1]["accessories"][0]["type"] == "passive_probe"
    r = client.post(f"/api/sessions/{sid}/instruments/scope/scpi", json={"command": "*IDN?"}).json()
    assert r["response"].startswith("SIMUBENCH,SB-DSO-4")
    r = client.post(f"/api/sessions/{sid}/instruments/scope/scpi", json={"command": ":CHAN9:SCAL 1"}).json()
    assert r["errors"][0]["code"] == -114
    ok = client.post(
        f"/api/sessions/{sid}/instruments/awg/configure", json={"path": "ch1.output", "value": True}
    ).json()
    assert ok["ok"] and ok["value"] is True
    bad = client.post(
        f"/api/sessions/{sid}/instruments/awg/configure", json={"path": "ch1.frequency", "value": 1e9}
    ).json()
    assert not bad["ok"] and bad["error"]["code"] == -222
    screen = client.get(f"/api/sessions/{sid}/instruments/scope/screen").json()
    assert screen["source"] == "preview" and len(screen["traces"][0]["volts"]) == 1000
    wf = client.get(
        f"/api/sessions/{sid}/nets/net-0/waveform", params={"window_s": 2e-3, "points": 100}
    ).json()
    assert len(wf["volts"]) == 100
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_bench_path_cannot_escape_workspace(client):
    r = client.post("/api/sessions", json={"bench_path": "../pyproject.toml"})
    assert r.status_code == 403


def test_event_stream(client):
    sid = client.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
    with client.websocket_connect(
        f"/api/sessions/{sid}/events?token={TOKEN}", headers={"host": "127.0.0.1"}
    ) as ws:
        client.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": ":FREQ 2000"})
        kinds = []
        for _ in range(3):
            ev = ws.receive_json()
            kinds.append(ev["kind"])
            if ev["kind"] == "scpi":
                assert ev["text"].startswith(":FREQ 2000")
                break
        assert "scpi" in kinds


def test_scpi_socket_roundtrip():
    from simubench.access.scpi_server import ScpiServer
    from simubench.api import open_bench

    session = open_bench(ROOT / "examples/benches/awg_rc_scope.bench.yaml")
    loop = asyncio.new_event_loop()
    server = ScpiServer(session, base_port=0)
    listeners = loop.run_until_complete(server.start())
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        port = next(lst.port for lst in listeners if lst.instrument == "scope")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(b"*IDN?\n")
            data = sock.recv(4096)
        assert data.startswith(b"SIMUBENCH,SB-DSO-4")
    finally:
        asyncio.run_coroutine_threadsafe(server.stop(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)


def test_reference_ports_are_reported_as_reference(client):
    info = client.post("/api/sessions", json={"bench_path": "benches/rfgen.bench.yaml"}).json()
    media = {(i["name"], p["name"]): p["medium"] for i in info["instruments"] for p in i["ports"]}
    assert media[("rf", "REF_OUT")] == "reference" and media[("sa", "REF_IN")] == "reference"
    assert media[("rf", "RF_OUT")] == "electrical"
    client.delete(f"/api/sessions/{info['id']}")


def test_virtual_probe_explains_the_value(client):
    info = client.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()
    sid = info["id"]
    client.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": ":OUTPut1:STATe ON"})
    out = next(n["id"] for n in info["nets"] if "rc.OUT" in n["members"])
    p = client.get(f"/api/sessions/{sid}/nets/{out}/probe").json()
    src = p["sources"][0]
    assert src["source"] == "awg/ch1.out" and src["instrument"] == "awg"
    line = src["lines"][0]
    assert line["freq_hz"] == 1000.0
    # The net's value is the source's EMF times the circuit's transfer: nothing else.
    assert line["contribution"] == pytest.approx(line["emf"] * line["gain"], rel=1e-12)
    assert p["lines"][0]["amplitude"] == pytest.approx(line["contribution"], rel=1e-9)
    assert p["rms"] == pytest.approx(line["contribution"] / 2**0.5, rel=1e-9)
    assert -90 < line["phase_deg"] < 0  # a low-pass lags
    assert len(p["waveform"]["volts"]) == 400 and p["waveform"]["exact"]
    assert client.get(f"/api/sessions/{sid}/nets/nope/probe").status_code == 404
    client.delete(f"/api/sessions/{sid}")
