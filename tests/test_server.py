import asyncio
import hashlib
import os
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

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
    assert ok["ok"] and ok["value"] is True and ok["changed"] is True  # #1678
    same = client.post(
        f"/api/sessions/{sid}/instruments/awg/configure", json={"path": "ch1.output", "value": True}
    ).json()
    assert same["ok"] and same["value"] is True and same["changed"] is False  # an equal write
    bad = client.post(
        f"/api/sessions/{sid}/instruments/awg/configure", json={"path": "ch1.frequency", "value": 1e9}
    ).json()
    assert not bad["ok"] and bad["error"]["code"] == -222 and not bad["changed"]
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


def test_event_stream_since_sends_only_the_tail():
    """#1632: ``seq`` is the event's stable cursor in the session (the same event carries the
    same seq on every connect), and ``since`` asks the server for the events after the client's
    last one — so a reconnect sends the missed tail instead of the whole history again."""
    manager = SessionManager(ROOT / "validation", scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))

    def read_until(ws, command: str) -> list[dict]:
        """The stream's events up to the one carrying the command's reply, which is the last the
        command produces; more than a command's worth without it means the stream broke."""
        out = []
        for _ in range(10):
            ev = ws.receive_json()
            out.append(ev)
            if ev["kind"] == "scpi" and str(ev.get("text", "")).startswith(command):
                return out
        raise AssertionError(f"the {command} reply never arrived, got {out}")

    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]

        with c.websocket_connect(
            f"/api/sessions/{sid}/events?token={TOKEN}", headers={"host": "127.0.0.1"}
        ) as ws:
            c.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": ":FREQ 2000"})
            first = read_until(ws, ":FREQ 2000")
            seqs = [ev["seq"] for ev in first]
            assert seqs == sorted(set(seqs))  # the live stream, strictly increasing from the start
        last = first[-1]["seq"]
        c.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": ":FREQ 3000"})

        with c.websocket_connect(
            f"/api/sessions/{sid}/events?token={TOKEN}&since={last}", headers={"host": "127.0.0.1"}
        ) as ws:
            missed = read_until(ws, ":FREQ 3000")
            missed_seqs = [ev["seq"] for ev in missed]
            assert missed_seqs[0] == last + 1  # the tail only — the client holds everything before
            c.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": ":FREQ 4000"})
            live = read_until(ws, ":FREQ 4000")
            assert next(ev["seq"] for ev in live) == missed_seqs[-1] + 1  # the stream goes on unchanged

        n = len(manager.get(sid).session.events.history)
        with c.websocket_connect(
            f"/api/sessions/{sid}/events?token={TOKEN}", headers={"host": "127.0.0.1"}
        ) as ws:
            replay = [ws.receive_json() for _ in range(n)]
            assert [ev["seq"] for ev in replay] == list(range(1, n + 1))  # without since, all of it


def test_event_cursor_survives_a_reset():
    """#1642: a reset keeps the session id and starts a new event bus; the new session counts on
    from the old one's last event, so a client reconnecting with the ``since`` it held before the
    reset gets every event of the new session, and the cursor never goes back."""
    manager = SessionManager(ROOT / "validation", scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
        for freq in (1000, 2000, 3000):
            c.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": f":FREQ {freq}"})
        before = manager.get(sid).session.events.event_id
        assert before >= 3
        assert c.post(f"/api/sessions/{sid}/reset", json={}).status_code == 200
        c.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": ":FREQ 4000"})
        history = list(manager.get(sid).session.events.history)
        assert history and min(ev["eid"] for ev in history) == before + 1  # counts on, no restart at 1

        with c.websocket_connect(
            f"/api/sessions/{sid}/events?token={TOKEN}&since={before}", headers={"host": "127.0.0.1"}
        ) as ws:
            got = [ws.receive_json() for _ in range(len(history))]
        assert [ev["seq"] for ev in got] == [ev["eid"] for ev in history]  # nothing dropped
        assert any(ev["kind"] == "scpi" and str(ev.get("text", "")).startswith(":FREQ 4000") for ev in got)


def test_event_stream_rejects_a_bad_since(client):
    """A since that is not a number is a client protocol error: the socket is closed."""
    sid = client.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(
            f"/api/sessions/{sid}/events?token={TOKEN}&since=abc", headers={"host": "127.0.0.1"}
        ) as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 1008


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


def test_scpi_socket_long_and_block_messages(tmp_path):
    """#1448: a program message is not limited to 64 KiB, a definite-length block may hold LF,
    and an over-long message is -223 without dropping the connection."""
    from simubench.access.scpi_server import ScpiServer
    from simubench.api import open_bench
    from simubench.models.bench import BenchDefinition

    bench = {"schema": "simubench.bench/2", "name": "t", "instruments": {"awg": {"model": "keysight-33622a"}}}
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    with open_bench(BenchDefinition.model_validate(bench), files_dir=tmp_path) as session:
        server = ScpiServer(session, base_port=0)
        port = asyncio.run_coroutine_threadsafe(server.start(), loop).result(5)[0].port

        def block(b: bytes) -> bytes:
            n = str(len(b)).encode()
            return b"#" + str(len(n)).encode() + n + b

        def line(sock, data: bytes = b"") -> bytes:
            sock.sendall(data)
            out = b""
            while not out.endswith(b"\n"):
                chunk = sock.recv(1 << 20)
                assert chunk, "connection closed"
                out += chunk
            return out

        try:
            with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
                sock.sendall(b'MMEM:DOWN:FNAM "INT:\\t1.txt"\n')
                payload = b"AB\nCD\r\nEF"
                sock.sendall(b"MMEM:DOWN:DATA " + block(payload) + b"\n")
                assert line(sock, b"SYST:ERR?\n").startswith(b"+0,")  # block LF is data
                assert line(sock, b'MMEM:UPL? "INT:\\t1.txt"\n').rstrip(b"\n") == block(payload)
                big = b"DATA:ARB myarb," + b",".join([b"0.5"] * 300000) + b"\n"  # about 1 MB
                assert len(big) > 1_000_000
                assert line(sock, big + b"SYST:ERR?\n").startswith(b"+0,")
                # '#' in a quoted string is not a block; the connection is still served
                assert line(sock, b"*IDN?\n").startswith(b"Keysight")
        finally:
            asyncio.run_coroutine_threadsafe(server.stop(), loop).result(5)
            loop.call_soon_threadsafe(loop.stop)


def test_scpi_message_reader_splits_and_limits():
    from simubench.access.scpi_server import MessageReader

    async def run(chunks: list[bytes], limit: int = 1 << 20):
        r = asyncio.StreamReader()
        for c in chunks:
            r.feed_data(c)
        r.feed_eof()
        m = MessageReader(r, limit)
        out = []
        while True:
            msg, over = await m.read()
            if not msg and not over:
                return out
            out.append((msg, over))

    got = asyncio.run(run([b"A\n#14AB\nC\n", b"X '#9' Y\nZ\n"]))
    assert got == [(b"A\n", False), (b"#14AB\nC\n", False), (b"X '#9' Y\n", False), (b"Z\n", False)]
    got = asyncio.run(run([b"#1", b"3A\nB\nQ\n"]))
    assert got == [(b"#13A\nB\n", False), (b"Q\n", False)]  # a header split across reads
    got = asyncio.run(run([b"x" * 100, b"y" * 100 + b"\nOK\n"], limit=150))
    assert got == [(b"", True), (b"OK\n", False)]


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

    # Polling asks for values only (#1299): the same values by path, no definitions, the same build.
    full = client.get(f"/api/sessions/{sid}").json()
    light = client.get(f"/api/sessions/{sid}", params={"state": "values"}).json()
    assert light["build"] == full["build"] != ""
    for f_inst, l_inst in zip(full["instruments"], light["instruments"], strict=True):
        assert l_inst["state"] == [] and f_inst["values"] is None
        assert l_inst["values"] == {v["path"]: v["value"] for v in f_inst["state"]}
    assert light["instruments"][1]["values"]["ch1.scale"] == 0.5

    after = client.post(f"/api/sessions/{sid}/reset", json={}).json()
    assert after["id"] == sid  # the same session, scripts keep their entry point
    assert after["build"] != full["build"]  # another build: a polling client fetches the definitions again
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


def test_a_combination_that_moves_limits_reaches_a_values_poll():
    """The E36312A's series pair doubles CH2's limits within one build (#259): a client polling
    values only learns it from ``spec_rev`` and fetches the full session again (#1302)."""
    app = create_app(
        SessionManager(ROOT / "examples", scpi_base_port=None), token=TOKEN, web_dist=Path("/nonexistent")
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "benches/keysight_e36312a.bench.yaml"}).json()["id"]

        def psu(**params):
            info = c.get(f"/api/sessions/{sid}", params=params).json()
            return info["build"], next(i for i in info["instruments"] if i["name"] == "psu")

        build, before = psu()
        single = next(v["max"] for v in before["state"] if v["path"] == "ch2.voltage")
        _, light = psu(state="values")
        assert light["spec_rev"] == before["spec_rev"]  # nothing moved: the values are enough
        c.post(
            f"/api/sessions/{sid}/instruments/psu/scpi", json={"command": "OUTP OFF,(@1:3);:OUTP:PAIR SER"}
        )
        assert (
            c.post(f"/api/sessions/{sid}/instruments/psu/scpi", json={"command": "OUTP:PAIR?"}).json()[
                "response"
            ]
            == "SER"
        )
        same_build, light = psu(state="values")
        assert same_build == build and light["spec_rev"] != before["spec_rev"]  # the client refetches
        _, after = psu()
        assert after["spec_rev"] == light["spec_rev"]
        assert next(v["max"] for v in after["state"] if v["path"] == "ch2.voltage") == 2 * single


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


@pytest.mark.parametrize(
    ("value", "code"),
    [("abc", -104), (None, -104), ([], -104), ({}, -104), pytest.param(10**400, -222, id="10**400")],
)
def test_configure_wrong_type_is_a_clean_scpi_error_not_500(client, value, code):
    """#1456: a configure value of the wrong type is a bad parameter (-104), not a server bug; an
    integer too large for a float is out of range (-222) like infinity (#1500). The session
    configure and the try-session configure both answer it cleanly, never a 500."""
    sid = client.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
    r = client.post(
        f"/api/sessions/{sid}/instruments/awg/configure",
        json={"path": "ch1.frequency", "value": value},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body["ok"] and body["error"]["code"] == code
    t = client.post("/api/try/sb-dso-4").json()
    r2 = client.post(f"/api/try/{t['session_id']}/configure", json={"path": "ch1.scale", "value": value})
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert not body2["ok"] and body2["error"]["code"] == code
    client.delete(f"/api/sessions/{sid}")
    client.delete(f"/api/try/{t['session_id']}")


def test_unknown_instrument_404_detail_keeps_its_closing_quote(client):
    """#1456: the 404 for an unknown instrument must read ``no instrument named 'nope'`` — the
    closing quote used to be eaten by stripping every quote character off ``str(KeyError(...))``."""
    sid = client.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
    r = client.post(f"/api/sessions/{sid}/instruments/nope/scpi", json={"command": "*IDN?"})
    assert r.status_code == 404
    assert "'nope'" in r.json()["detail"]
    client.delete(f"/api/sessions/{sid}")


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
            {"path": "cases/dmm.cases.yaml", "cases": 2, "builtin": False, "from_model": None}
        ]
        assert c.get("/api/definitions/sb-awg-2/cases").json() == [
            {"path": "cases/other.cases.yaml", "cases": 0, "builtin": False, "from_model": None}
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


def test_builtin_models_manual_cases_are_listed_and_run(tmp_path):
    """A built-in model's acceptance page shows the manual cases shipped with it, and runs them (#1352)."""
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        listed = c.get("/api/definitions/keysight-34465a/cases").json()
        # listed: both of the model's manual cases files, built-in, with their case counts
        assert {f["path"] for f in listed} == {
            "builtin:keysight-34465a/cases.yaml",
            "builtin:keysight-34465a-impedance/cases.yaml",
        }
        assert all(f["builtin"] and f["cases"] > 0 for f in listed)
        # run: the small one passes case by case, on the built-in definition
        run = c.post(
            "/api/definitions/keysight-34465a/cases/run",
            json={"path": "builtin:keysight-34465a-impedance/cases.yaml"},
        ).json()
        assert run["passed"] == 2 and run["failed"] == 0
        # refused: another model's file, and a name that climbs out of the folder
        for name in ("builtin:rs-sma100b/cases.yaml", "builtin:../models/x/cases.yaml"):
            assert (
                c.post("/api/definitions/keysight-34465a/cases/run", json={"path": name}).status_code == 422
            )


def test_a_copy_runs_the_cases_and_shows_the_coverage_of_the_model_it_is_based_on(tmp_path):
    """A workspace copy (extends) lists its parent's shipped cases as inherited and runs them on
    itself — so a command the copy removed fails them — and shows the parent's coverage table with
    the commands it runs differently (P1-4)."""
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        copied = c.post("/api/definitions/keysight-34465a/copy").json()["data"]
        my_id, name = copied["id"], "builtin:keysight-34465a-impedance/cases.yaml"
        listed = c.get(f"/api/definitions/{my_id}/cases").json()
        assert {f["path"] for f in listed} == {"builtin:keysight-34465a/cases.yaml", name}
        assert all(f["builtin"] and f["from_model"] == "keysight-34465a" for f in listed)
        run = c.post(f"/api/definitions/{my_id}/cases/run", json={"path": name}).json()
        assert (run["passed"], run["failed"]) == (2, 0)
        coverage = c.get(f"/api/definitions/{my_id}/coverage").json()
        assert coverage["from_model"] == "keysight-34465a" and coverage["changed"] == []

        header = "[:SENSe]:VOLTage[:DC]:IMPedance:AUTO"
        doc = c.get(f"/api/definitions/{my_id}/document").json()
        (tmp_path / "instruments" / f"{my_id}.yaml").write_text(
            yaml.safe_dump({**doc["data"], "command_overrides": {header: None}}), encoding="utf-8"
        )
        run = c.post(f"/api/definitions/{my_id}/cases/run", json={"path": name}).json()
        assert (run["passed"], run["failed"]) == (0, 2)  # the copy ran, without the command
        assert c.get(f"/api/definitions/{my_id}/coverage").json()["changed"] == [header]
        # the built-in model itself does not inherit anything
        assert all(f["from_model"] is None for f in c.get("/api/definitions/keysight-53230a/cases").json())


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


def test_a_definition_that_does_not_parse_is_422_with_its_position(tmp_path):
    """#1406: a broken definition file is the caller's to fix, not a 500."""
    (tmp_path / "instruments").mkdir()
    (tmp_path / "instruments" / "bad.yaml").write_text("name: [\n", encoding="utf-8")
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        r = c.get("/api/definitions/bad/document")
        assert r.status_code == 422, r.text
        assert "bad" in str(r.json()["detail"]) and "line" in str(r.json()["detail"])


def test_a_json_bench_with_a_key_written_twice_is_422_pointing_at_the_second_writing(tmp_path):
    """#1596: the 422 of a JSON bench that names a key twice carries the line of the second writing
    (and the first), not "line 1 column 1" at the top of the file."""
    text = (
        '{\n  "schema": "simubench.bench/2",\n  "name": "dup",\n  "instruments": {\n'
        '    "a": {"model": "keysight-34465a"},\n    "a": {"model": "keysight-34465a"}\n  }\n}\n'
    )
    (tmp_path / "dup.bench.json").write_text(text, encoding="utf-8")
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        r = c.get("/api/benches/file", params={"path": "dup.bench.json"})
        detail = str(r.json()["detail"])
        assert r.status_code == 422, r.text
        assert "duplicate key 'a' (first written on line 5)" in detail and "line 6 column 5" in detail


def test_the_document_route_names_the_sessions_to_restart(tmp_path):
    """The HTTP definition document fills sessions_to_restart like the MCP read_definition does
    (#1382): a running session keeps the definition it started with until restarted (D25)."""
    import yaml

    shutil.copy(
        ROOT / "validation" / "benches" / "awg_rc_scope.bench.yaml", tmp_path / "awg_rc_scope.bench.yaml"
    )
    (tmp_path / "instruments").mkdir()  # the workspace copy shadows the built-in sb-awg-2
    shutil.copy(
        ROOT / "validation" / "instruments" / "sb-awg-2.yaml", tmp_path / "instruments" / "sb-awg-2.yaml"
    )
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "awg_rc_scope.bench.yaml"}).json()["id"]
        assert c.get("/api/definitions/sb-awg-2/document").json()["sessions_to_restart"] == []
        path = tmp_path / "instruments" / "sb-awg-2.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["description"] = "changed after the session started"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        assert c.get("/api/definitions/sb-awg-2/document").json()["sessions_to_restart"] == [sid]
        c.delete(f"/api/sessions/{sid}")


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
        monkeypatch.setattr("simubench.server.services.faults.fault_info", fault_info)

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


def test_the_clock_has_an_upper_limit_and_stays_usable_after_a_refused_advance(client):
    """#1458: an advance is at most the clock's whole range (422 above it, inf and nan too), and
    one that would take the clock past the range is a 409 that changes nothing: the instruments
    still answer and the screen still renders."""
    sid = client.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]

    def advance(seconds):
        return client.post(f"/api/sessions/{sid}/clock/advance", json={"seconds": seconds})

    for bad in (1e308, 1e10, "inf", "nan", 0):
        assert advance(bad).status_code == 422, bad
    assert advance(9e6).status_code == 200  # 9e15 ns, inside 2**53 ns
    now = client.get(f"/api/sessions/{sid}/clock").json()["now_ns"]
    refused = advance(9e6)  # 1.8e16 ns: past the limit from here
    assert refused.status_code == 409 and "limit" in refused.json()["detail"]
    assert client.get(f"/api/sessions/{sid}/clock").json()["now_ns"] == now
    reply = client.post(f"/api/sessions/{sid}/instruments/scope/scpi", json={"command": "*IDN?"}).json()
    assert reply["errors"] == [] and reply["response"]
    assert client.get(f"/api/sessions/{sid}/instruments/scope/screen").status_code == 200


def test_the_running_clock_is_switched_live_and_back_over_the_api(client):
    """#1660: Live / Pause switches the running clock; the session keeps the mode it opened on,
    and a reset opens on it again (the bench file is not changed)."""
    info = client.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()
    sid = info["id"]
    assert (info["clock_mode"], info["start_clock_mode"]) == ("command", "command")
    client.post(f"/api/sessions/{sid}/clock/advance", json={"seconds": 1})
    live = client.post(f"/api/sessions/{sid}/clock/mode", json={"mode": "scaled", "scale": 50})
    assert live.status_code == 200
    assert (live.json()["mode"], live.json()["scale"]) == ("scaled", 50.0)
    assert live.json()["now_ns"] >= 1_000_000_000  # no rewind
    session = client.get(f"/api/sessions/{sid}").json()
    assert (session["clock_mode"], session["start_clock_mode"]) == ("scaled", "command")
    assert client.post(f"/api/sessions/{sid}/clock/advance", json={"seconds": 1}).status_code == 409
    assert client.post(f"/api/sessions/{sid}/clock/mode", json={"mode": "fast"}).status_code == 422
    paused = client.post(f"/api/sessions/{sid}/clock/mode", json={"mode": "command"}).json()
    assert paused["mode"] == "command" and paused["scale"] == 1.0
    assert client.post(f"/api/sessions/{sid}/clock/advance", json={"seconds": 1}).status_code == 200
    client.post(f"/api/sessions/{sid}/clock/mode", json={"mode": "wall"})
    reset = client.post(f"/api/sessions/{sid}/reset", json={}).json()
    assert reset["clock_mode"] == "command"  # a reset opens on the bench's clock again


def test_a_stepped_clock_takes_no_new_scale_over_the_api(client):
    sid = client.post(
        "/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml", "clock": "stepped"}
    ).json()["id"]
    assert client.post(f"/api/sessions/{sid}/clock/scale", json={"scale": 2}).status_code == 409


def test_watcher_announces_external_changes_once(tmp_path):
    """A change the server's write points did not make (an editor, a second service on the same
    workspace) is announced by the scan; content a write point published is not repeated (#1484)."""
    from simubench.server.workspace_events import WorkspaceWatcher

    published: list[dict] = []
    watcher = WorkspaceWatcher(tmp_path, lambda kind, **fields: published.append(fields))
    (tmp_path / "a.bench.yaml").write_text("schema: simubench.bench/2\nname: a\n", encoding="utf-8")
    watcher._pass()  # the first pass baselines the workspace
    assert published == []
    (tmp_path / "a.bench.yaml").write_text("schema: simubench.bench/2\nname: b\n", encoding="utf-8")
    # Same size, and Windows' coarse mtime can leave the stat unmoved: push the mtime forward so the
    # test pins the scan, not the file system's clock granularity.
    st = (tmp_path / "a.bench.yaml").stat()
    os.utime(tmp_path / "a.bench.yaml", ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    watcher._pass()
    assert [p["path"] for p in published] == ["a.bench.yaml"]
    # The server writes the same file itself: note() marks the content, the scan stays silent.
    raw = b"schema: simubench.bench/2\nname: c\n"
    (tmp_path / "a.bench.yaml").write_bytes(raw)
    watcher.note("a.bench.yaml", hashlib.sha256(raw).hexdigest())
    watcher._pass()
    assert [p["path"] for p in published] == ["a.bench.yaml"]
    # A file that disappears (or a folder removed whole) is announced too.
    (tmp_path / "a.bench.yaml").unlink()
    watcher._pass()
    assert [p["path"] for p in published] == ["a.bench.yaml", "a.bench.yaml"]


def test_watcher_reads_again_when_a_same_size_write_lands_in_one_timestamp_tick(tmp_path):
    """#1502: two same-size writes inside one timestamp tick leave mtime and size unmoved, so a
    stat-only scan never sees the second one; the pass after a change reads the file once more."""
    from simubench.server.workspace_events import WorkspaceWatcher

    published: list[dict] = []
    watcher = WorkspaceWatcher(tmp_path, lambda kind, **fields: published.append(fields))
    path = tmp_path / "a.bench.yaml"
    path.write_bytes(b"schema: simubench.bench/2\nname: a\n")
    before = path.stat()
    watcher._pass()  # baseline
    path.write_bytes(b"schema: simubench.bench/2\nname: b\n")  # same size
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))  # force the same tick
    watcher._pass()
    assert [p["path"] for p in published] == ["a.bench.yaml"]


def test_watcher_stays_silent_when_a_write_point_notes_the_content_mid_pass(tmp_path, monkeypatch):
    """#1526: a write point that notes its content while a scan is reading the file (after the
    scan's stat snapshot) is not announced a second time."""
    from simubench.server import workspace_events
    from simubench.server.workspace_events import WorkspaceWatcher

    published: list[dict] = []
    watcher = WorkspaceWatcher(tmp_path, lambda kind, **fields: published.append(fields))
    path = tmp_path / "a.bench.yaml"
    path.write_bytes(b"schema: simubench.bench/2\nname: a\n")
    watcher._pass()  # baseline
    raw = b"schema: simubench.bench/2\nname: b\n"
    path.write_bytes(raw)
    real_sha256 = hashlib.sha256

    def noting_sha256(data=b""):
        watcher.note("a.bench.yaml", real_sha256(raw).hexdigest())  # lands mid-pass
        return real_sha256(data)

    monkeypatch.setattr(workspace_events.hashlib, "sha256", noting_sha256)
    watcher._pass()
    assert published == []


def test_watcher_stays_silent_when_a_pass_lands_between_the_write_and_its_note(tmp_path):
    """#1526: a write point replaces the file, then publishes and notes it; a pass in between
    reads content it was never told about. The write point names the content before the file
    changes (expect), so that pass stays silent and only the write point announces it."""
    from simubench.server.workspace_events import WorkspaceWatcher

    published: list[dict] = []
    watcher = WorkspaceWatcher(tmp_path, lambda kind, **fields: published.append(fields))
    path = tmp_path / "a.bench.yaml"
    path.write_bytes(b"schema: simubench.bench/2\nname: a\n")
    watcher._pass()  # baseline
    raw = b"schema: simubench.bench/2\nname: b\n"
    sha = hashlib.sha256(raw).hexdigest()
    watcher.expect("a.bench.yaml", sha)
    watcher._pass()  # before the replace: the old content, unchanged
    path.write_bytes(raw)
    watcher._pass()  # after the replace, before the note
    watcher.note("a.bench.yaml", sha)
    watcher._pass()
    assert published == []
    path.write_bytes(b"schema: simubench.bench/2\nname: c\n")  # an editor's later save still counts
    watcher._pass()
    assert published == [{"path": "a.bench.yaml"}]


def test_watcher_keeps_a_note_that_lands_while_it_reads_the_older_content(tmp_path, monkeypatch):
    """#1547: a pass reads the file just before a write point replaces it and notes the new
    content. The pass must not record its older read over the note: the next pass would see the
    new content as unannounced and announce the server's own write a second time."""
    from simubench.server import workspace_events
    from simubench.server.workspace_events import WorkspaceWatcher

    published: list[dict] = []
    watcher = WorkspaceWatcher(tmp_path, lambda kind, **fields: published.append(fields))
    path = tmp_path / "a.bench.yaml"
    path.write_bytes(b"schema: simubench.bench/2\nname: a\n")
    raw = b"schema: simubench.bench/2\nname: b\n"
    real_sha256 = hashlib.sha256

    def writing_mid_read(data=b""):  # the pass has read the old bytes; the write point lands now
        path.write_bytes(raw)
        watcher.note("a.bench.yaml", real_sha256(raw).hexdigest())
        return real_sha256(data)

    monkeypatch.setattr(workspace_events.hashlib, "sha256", writing_mid_read)
    watcher._pass()  # the first pass, baselining, with the write landing inside its read
    monkeypatch.setattr(workspace_events.hashlib, "sha256", real_sha256)
    watcher._pass()
    watcher._pass()
    assert published == []


def test_watcher_does_not_take_a_file_created_after_its_listing_for_a_deleted_one(tmp_path, monkeypatch):
    """#1547: a write point creates a file (a definition copy) and notes it after a pass listed the
    folder. The pass knows the file but did not see it; it must not announce it as deleted, which
    would repeat the server's own write."""
    from simubench.server.workspace_events import WorkspaceWatcher

    published: list[dict] = []
    watcher = WorkspaceWatcher(tmp_path, lambda kind, **fields: published.append(fields))
    (tmp_path / "instruments").mkdir()
    watcher._pass()  # baseline: an empty workspace
    listing = watcher._files
    created = tmp_path / "instruments" / "my-dso.yaml"
    raw = b"schema: simubench.instrument/2\nid: my-dso\n"

    def listing_then_a_write():  # the pass has listed the folder; the write point lands now
        files = listing()
        created.write_bytes(raw)
        watcher.note("instruments/my-dso.yaml", hashlib.sha256(raw).hexdigest())
        return files

    monkeypatch.setattr(watcher, "_files", listing_then_a_write)
    watcher._pass()
    monkeypatch.setattr(watcher, "_files", listing)
    watcher._pass()
    assert published == []


def test_workspace_hub_drops_events_without_a_subscriber():
    """The hub is a fan-out: publishes from any thread reach the queues, unknown kinds refuse,
    and with nobody listening nothing is kept (the stream is live, not a log)."""
    from simubench.server.workspace_events import WorkspaceEvents, put_bounded

    # The bounded put both streams' sockets share: a full queue loses its oldest, never blocks.
    bounded: asyncio.Queue[int] = asyncio.Queue(maxsize=2)
    for i in range(2):
        bounded.put_nowait(i)
    put_bounded(bounded, 2)
    assert [bounded.get_nowait(), bounded.get_nowait()] == [1, 2]  # 0 went, the oldest

    hub = WorkspaceEvents()

    async def main() -> None:
        loop = asyncio.get_running_loop()
        hub.publish("clients_changed", sid="s1")  # no subscriber yet: dropped
        queue = hub.subscribe(loop)
        hub.publish("clients_changed", sid="s1")
        assert await asyncio.wait_for(queue.get(), 5) == {"kind": "clients_changed", "sid": "s1"}
        hub.unsubscribe(queue)
        hub.publish("file_changed", path="a.bench.yaml")  # the last listener left: dropped

    asyncio.run(main())
    with pytest.raises(ValueError):
        hub.publish("session_event")


def test_workspace_event_stream_carries_file_and_client_changes(tmp_path):
    """The /api/events socket: a write through the API and a script's connect and disconnect
    arrive as file_changed and clients_changed, addressed by path and sid (#1484)."""
    shutil.copy(
        ROOT / "validation" / "benches" / "awg_rc_scope.bench.yaml", tmp_path / "awg_rc_scope.bench.yaml"
    )
    manager = SessionManager(tmp_path, scpi_base_port=0, hislip_port=0)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "awg_rc_scope.bench.yaml"}).json()["id"]
        port = c.get(f"/api/sessions/{sid}").json()["instruments"][1]["scpi_port"]
        with c.websocket_connect(f"/api/events?token={TOKEN}", headers={"host": "127.0.0.1"}) as ws:
            doc = c.get("/api/benches/file", params={"path": "awg_rc_scope.bench.yaml"}).json()
            data = dict(doc["data"])
            data["description"] = "changed by another client"
            saved = c.put(
                "/api/benches/file",
                json={"path": "awg_rc_scope.bench.yaml", "base_sha256": doc["sha256"], "data": data},
            )
            assert saved.status_code == 200
            ev = ws.receive_json()
            assert ev["kind"] == "file_changed"
            assert ev["path"] == "awg_rc_scope.bench.yaml"
            # A definition written through its own save path is announced with its file path.
            copied = c.post("/api/definitions/sb-dso-4/copy").json()
            ev = ws.receive_json()
            assert ev["kind"] == "file_changed"
            assert ev["path"] == copied["path"]
            # A script connects and leaves: one clients_changed each, named by session.
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            ev = ws.receive_json()
            assert ev["kind"] == "clients_changed"
            assert ev["sid"] == sid
            sock.close()
            ev = ws.receive_json()
            assert ev["kind"] == "clients_changed"
            assert ev["sid"] == sid
            # A session stops under a connected page: sessions_changed names it (#1484), so the
            # Web's session queries refetch into the 404 and the stopped-bench flow sees it.
            c.delete(f"/api/sessions/{sid}")
            ev = ws.receive_json()
            assert ev["kind"] == "sessions_changed"
            assert ev["sid"] == sid
            # A session starts again: the same announcement, so the stopped-bench page follows.
            new_sid = c.post("/api/sessions", json={"bench_path": "awg_rc_scope.bench.yaml"}).json()["id"]
            ev = ws.receive_json()
            assert ev["kind"] == "sessions_changed"
            assert ev["sid"] == new_sid
            c.delete(f"/api/sessions/{new_sid}")
