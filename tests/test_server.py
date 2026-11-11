import asyncio
import hashlib
import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simubench.server.app import _host_ok, create_app
from simubench.server.manager import SessionManager
from simubench.server.views import fault_info as app_fault_info

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token"


@pytest.fixture
def client():
    manager = SessionManager(ROOT / "validation", scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        yield c


def test_host_check():
    """The pure function behind the host guard; the HTTP behaviour is test_security.py's
    (every route's token, foreign host/origin, escaping bench paths — #700 deduplicated them)."""
    assert _host_ok("127.0.0.1:8765") and _host_ok("localhost") and _host_ok("http://localhost:5173")
    assert not _host_ok("evil.example:8765") and not _host_ok("http://evil.example")


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
    assert screen["source"] == "live" and screen["running"] and len(screen["traces"][0]["volts"]) == 1000
    wf = client.get(
        f"/api/sessions/{sid}/nets/net-0/waveform", params={"window_s": 2e-3, "points": 100}
    ).json()
    assert len(wf["volts"]) == 100
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_stop_unknown_session_is_404(client):
    """manager.stop returns silently for an unknown id, but the route answers 404 as before
    (moved here from test_remote_state.py, #700: a general endpoint behaviour, not remote-state)."""
    assert client.delete("/api/sessions/unknown").status_code == 404


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

    session = open_bench(ROOT / "validation/benches/awg_rc_scope.bench.yaml")
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


def test_rf_generator_panel_readings_come_from_its_blocks(client):
    """#188 D2: the panel reads ``<component>.<key>``; after the split the frequency is the
    synthesizer's, the line count and β the modulator's, the lock state the reference's."""
    info = client.post("/api/sessions", json={"bench_path": "benches/rfgen.bench.yaml"}).json()
    sid = info["id"]
    try:
        values = client.get(f"/api/sessions/{sid}/instruments/rf/readings").json()["values"]
        assert values["synth.frequency"] == 1e9 and values["synth.points"] == 0
        assert values["mod.lines"] == 1 and values["mod.beta"] == 0
        assert values["reference.locked"] is True
        client.post(f"/api/sessions/{sid}/instruments/rf/scpi", json={"command": ":SOUR:FM:DEV 1e6"})
        client.post(f"/api/sessions/{sid}/instruments/rf/scpi", json={"command": ":SOUR:FM:INT:FREQ 1e6"})
        client.post(f"/api/sessions/{sid}/instruments/rf/scpi", json={"command": ":SOUR:FM:STAT ON"})
        values = client.get(f"/api/sessions/{sid}/instruments/rf/readings").json()["values"]
        assert values["mod.beta"] == 1 and values["mod.lines"] == 5
        assert values["mod.omitted_power"] == pytest.approx(0.00078, abs=1e-5)
    finally:
        client.delete(f"/api/sessions/{sid}")


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


def test_reset_power_cycles_the_bench(client):
    """Reset: same session id, instruments back at power-on defaults (issue 31)."""
    sid = client.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
    changed = client.post(
        f"/api/sessions/{sid}/instruments/scope/configure", json={"path": "ch1.scale", "value": 0.5}
    ).json()
    assert changed["value"] == 0.5

    after = client.post(f"/api/sessions/{sid}/reset", json={}).json()
    assert after["id"] == sid  # the same session, scripts keep their entry point
    scale = next(
        v
        for i in after["instruments"]
        if i["name"] == "scope"
        for v in i["state"]
        if v["path"] == "ch1.scale"
    )
    assert scale["value"] != 0.5  # back to power-on defaults
    assert after["record_path"] is None  # nothing has happened on the fresh session yet
    client.delete(f"/api/sessions/{sid}")


def test_opening_without_actions_writes_no_record(tmp_path):
    """A bench that is only opened leaves no record; the first command starts one (issue 31)."""
    app = create_app(
        SessionManager(ROOT / "validation", scpi_base_port=None, record_dir=tmp_path),
        token=TOKEN,
        web_dist=Path("/nonexistent"),
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
        assert c.get(f"/api/sessions/{sid}").json()["record_path"] is None
        assert list(tmp_path.iterdir()) == []  # only looking, nothing recorded
        c.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": "*IDN?"})
        assert len(list(tmp_path.iterdir())) == 1  # the first command starts the record
        c.delete(f"/api/sessions/{sid}")


def test_connected_scripts_are_listed_for_the_switch_confirm():
    """The session description names the scripts holding the SCPI ports (issue 31)."""
    manager = SessionManager(ROOT / "validation", scpi_base_port=0, hislip_port=0)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
        port = c.get(f"/api/sessions/{sid}").json()["instruments"][1]["scpi_port"]
        assert c.get(f"/api/sessions/{sid}").json()["clients"] == []
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:  # a script connects
            sock.sendall(b"*IDN?\n")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                clients = c.get(f"/api/sessions/{sid}").json()["clients"]
                if clients:
                    break
                time.sleep(0.05)
            assert clients and clients[0]["client"].startswith("socket:127.0.0.1:")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and c.get(f"/api/sessions/{sid}").json()["clients"]:
            time.sleep(0.05)  # gone once the script disconnects
        assert c.get(f"/api/sessions/{sid}").json()["clients"] == []
        c.delete(f"/api/sessions/{sid}")


def test_try_session_runs_alone_and_leaves_the_bench_alone(client):
    """A try session serves one instrument without a bench and without touching the open bench (T2)."""
    sid = client.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
    t = client.post("/api/try/sb-dso-4").json()
    assert t["instrument"] == "sb-dso-4" and t["session_id"] not in ("", None)
    tried = client.post(f"/api/try/{t['session_id']}/scpi", json={"command": "*IDN?"}).json()
    assert tried["response"].startswith("SIMUBENCH,SB-DSO-4")
    same = client.post(f"/api/sessions/{sid}/instruments/scope/scpi", json={"command": "*IDN?"}).json()
    assert same["response"] == tried["response"]  # the same model answers the same in both worlds
    bad = client.post(f"/api/try/{t['session_id']}/scpi", json={"command": ":CHAN9:SCAL 1"}).json()
    assert bad["errors"][0]["code"] == -114
    panel = client.post(f"/api/try/{t['session_id']}/panel", json={"action": "stop"}).json()
    assert panel["ok"]
    ok = client.post(
        f"/api/try/{t['session_id']}/configure", json={"path": "ch1.scale", "value": 0.05}
    ).json()
    assert ok["ok"] and ok["value"] == 0.05
    # the try session is not a bench session: /api/sessions still lists only the bench
    assert [s["id"] for s in client.get("/api/sessions").json()] == [sid]
    assert [x["definition_id"] for x in client.get("/api/try").json()] == ["sb-dso-4"]
    client.delete(f"/api/try/{t['session_id']}")
    assert client.get("/api/try").json() == []
    assert client.post(f"/api/try/{t['session_id']}/scpi", json={"command": "*IDN?"}).status_code == 404
    assert client.post("/api/try/no-such-definition").status_code == 404


def test_try_session_binds_no_ports_and_writes_no_record(tmp_path):
    manager = SessionManager(ROOT / "validation", scpi_base_port=0, record_dir=tmp_path)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        t = c.post("/api/try/sb-dmm-1").json()
        c.post(f"/api/try/{t['session_id']}/scpi", json={"command": "*IDN?"})
        assert manager.tries.all()[0].served.scpi is None
        assert manager.tries.all()[0].served.hislip is None
        assert list(tmp_path.iterdir()) == []  # commands ran, nothing was recorded
        c.delete(f"/api/try/{t['session_id']}")


def test_try_session_is_reaped_after_the_idle_timeout():
    manager = SessionManager(ROOT / "validation", scpi_base_port=None)
    manager.tries.idle_s = 0.0
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        t = c.post("/api/try/sb-dmm-1").json()
        r = c.post(f"/api/try/{t['session_id']}/scpi", json={"command": "*IDN?"})
        assert r.status_code == 404  # unused past the timeout: closed at the next look
        assert manager.tries.all() == []


def test_definition_cases_are_listed_and_run_with_steps(tmp_path):
    import yaml

    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "dmm.cases.yaml").write_text(
        yaml.safe_dump(
            {
                "model": "sb-dmm-1",
                "cases": [
                    {"name": "idn", "steps": [{"query": "*IDN?", "expect_prefix": "SIMUBENCH,SB-DMM-1"}]},
                    {
                        "name": "bad",
                        "manual": "Common Commands > *IDN?",
                        "steps": [{"send": "*RST"}, {"query": "*IDN?", "expect": "ACME,DM2"}],
                    },
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (cases_dir / "other.cases.yaml").write_text(
        yaml.safe_dump({"model": "sb-awg-2", "cases": []}), encoding="utf-8"
    )
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        assert c.get("/api/definitions/sb-dmm-1/cases").json() == [
            {"path": "cases/dmm.cases.yaml", "cases": 2}
        ]
        assert c.get("/api/definitions/sb-awg-2/cases").json() == [
            {"path": "cases/other.cases.yaml", "cases": 0}
        ]
        run = c.post("/api/definitions/sb-dmm-1/cases/run", json={"path": "cases/dmm.cases.yaml"}).json()
        assert run["path"] == "cases/dmm.cases.yaml" and run["passed"] == 1 and run["failed"] == 1
        idn, bad = run["cases"]
        assert idn["passed"] and [(s["index"], s["ok"]) for s in idn["steps"]] == [(1, True)]
        assert not bad["passed"] and bad["step"] == 2 and bad["command"] == "*IDN?"
        assert bad["expected"] == "'ACME,DM2'" and bad["actual"].startswith("SIMUBENCH,SB-DMM-1,")
        assert [(s["index"], s["action"], s["ok"]) for s in bad["steps"]] == [
            (1, "send", True),
            (2, "query", False),
        ]
        assert c.get("/api/definitions/no-such-def/cases").status_code == 404
        assert (
            c.post("/api/definitions/sb-dmm-1/cases/run", json={"path": "cases/none.cases.yaml"}).status_code
            == 404
        )
        assert c.post("/api/definitions/sb-dmm-1/cases/run", json={"path": "../x.yaml"}).status_code == 403


def test_definition_document_and_coverage_are_served():
    """The acceptance page reloads on the file hash and shows a real model's coverage (T4)."""
    manager = SessionManager(ROOT / "validation", scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        doc = c.get("/api/definitions/sb-dmm-1/document").json()
        assert doc["editable"] and doc["path"] == "instruments/sb-dmm-1.yaml" and doc["ok"]
        assert (
            doc["sha256"]
            == hashlib.sha256(
                (ROOT / "validation" / "instruments" / "sb-dmm-1.yaml").read_bytes()
            ).hexdigest()
        )
        # a built-in definition is read only: the page can show it, the agent must copy it to edit
        builtin = c.get("/api/definitions/keysight-34465a/document").json()
        assert builtin["editable"] is False and builtin["path"] is None and builtin["ok"]
        cov = c.get("/api/definitions/keysight-34465a/coverage").json()
        assert cov["model"] == "keysight-34465a"
        assert sum(cov["stats"].values()) == len(cov["entries"])
        assert cov["stats"]["implemented"] > 0 and cov["stats"]["not_modeled"] > 0
        assert all(e["section"] and e["header"] for e in cov["entries"])
        assert c.get("/api/definitions/sb-dmm-1/coverage").json() is None  # not a real model
        assert c.get("/api/definitions/no-such-def/document").status_code == 404
        assert c.get("/api/definitions/no-such-def/coverage").status_code == 404


def test_try_session_instrument_and_internals(client):
    """A try session is inspectable like a bench instrument, without a bench (T4)."""
    sid = client.post("/api/try/sb-dmm-1").json()["session_id"]
    inst = client.get(f"/api/try/{sid}/instrument").json()
    assert inst["name"] == "sb-dmm-1" and inst["definition_id"] == "sb-dmm-1"
    assert inst["idn"].startswith("SIMUBENCH,SB-DMM-1")
    assert {p["name"] for p in inst["ports"]} == {"IN"}
    assert inst["state"] and inst["deviation"] == "nominal"
    view = client.get(f"/api/try/{sid}/internals").json()
    assert view["definition_id"] == "sb-dmm-1" and view["components"]
    client.delete(f"/api/try/{sid}")
    assert client.get(f"/api/try/{sid}/instrument").status_code == 404
    assert client.get(f"/api/try/{sid}/internals").status_code == 404


def test_an_instrument_with_pattern_ports_is_described():
    """The 34970A's relay terminals come from the card's port_pattern: describing the instrument
    gives them as electrical ports (#355: this raised KeyError and the bench would not open)."""
    manager = SessionManager(ROOT / "examples", scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        r = c.post("/api/sessions", json={"bench_path": "benches/keysight_34970a.bench.yaml", "force": True})
        assert r.status_code == 200, r.text
        daq = next(i for i in r.json()["instruments"] if i["name"] == "daq")
        h101 = next(p for p in daq["ports"] if p["name"] == "H101")
        assert (h101["direction"], h101["medium"]) == ("inout", "electrical") and h101["net"]


def test_fault_endpoints_convert_inside_the_view(monkeypatch):
    """#489: every fault endpoint assembles its DTO inside the session's passive view — through
    the real HTTP handler, not a replica of its shape. The Tracked lock flags the lock depth
    reaching zero between the snapshot and the last field read."""
    manager = SessionManager(ROOT / "validation", scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
        r = c.post(
            f"/api/sessions/{sid}/faults",
            json={"kind": "latency", "instrument": "awg", "latency_s": 0.1},
        )
        assert r.status_code == 200, r.text
        s = manager.sessions[sid].session

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
            seen["snapshot"] = True  # the read begins: the lock must hold until the DTO is done
            return real_snapshot()

        real_fault_info = app_fault_info  # the module-level import, before the patch

        def fault_info(f):
            info = real_fault_info(f)
            seen["read"] = True  # the last field read has happened
            return info

        monkeypatch.setattr(s.faults, "snapshot", snapshot)
        monkeypatch.setattr(s, "_lock", Tracked())
        monkeypatch.setattr("simubench.server.routes.sessions.fault_info", fault_info)

        r = c.get(f"/api/sessions/{sid}/faults")
        assert r.status_code == 200 and len(r.json()) == 1
        assert violations == []


def test_scaled_clock_is_started_read_and_rescaled_over_the_api(client):
    info = client.post(
        "/api/sessions",
        json={"bench_path": "benches/awg_rc_scope.bench.yaml", "clock": "scaled", "time_scale": 5},
    ).json()
    sid = info["id"]
    assert (info["clock_mode"], info["time_scale"]) == ("scaled", 5.0)
    assert client.get(f"/api/sessions/{sid}/clock").json()["scale"] == 5.0
    before = client.get(f"/api/sessions/{sid}/clock").json()["now_ns"]
    r = client.post(f"/api/sessions/{sid}/clock/scale", json={"scale": 20})
    assert r.status_code == 200 and r.json()["scale"] == 20.0 and r.json()["now_ns"] >= before  # no rewind
    assert client.post(f"/api/sessions/{sid}/clock/scale", json={"scale": 0}).status_code == 422
    # A scaled clock walks on its own, like the wall clock: it cannot be advanced by hand.
    assert client.post(f"/api/sessions/{sid}/clock/advance", json={"seconds": 1}).status_code == 409


def test_a_stepped_clock_takes_no_new_scale_over_the_api(client):
    sid = client.post(
        "/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml", "clock": "stepped"}
    ).json()["id"]
    assert client.post(f"/api/sessions/{sid}/clock/scale", json={"scale": 2}).status_code == 409
