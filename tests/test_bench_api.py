import shutil
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t"
BENCH = "awg_rc_scope.bench.yaml"


@pytest.fixture
def ws(tmp_path):
    shutil.copy(ROOT / "validation" / "benches" / BENCH, tmp_path / BENCH)
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app(SessionManager(ws, scpi_base_port=None), token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        yield c


def test_read_reports_diagnostics(client):
    doc = client.get("/api/benches/file", params={"path": BENCH}).json()
    assert doc["ok"] and len(doc["sha256"]) == 64
    assert [d["code"] for d in doc["diagnostics"]] == ["W-IMPEDANCE-MISMATCH"]
    assert doc["data"]["duts"]["rc"]["type"] == "rc_lowpass"


def test_edit_writes_and_detects_conflicts(client, ws):
    doc = client.get("/api/benches/file", params={"path": BENCH}).json()
    ops = [{"op": "set_state", "instrument": "awg", "path": "ch1.load", "value": "INF"}]
    r = client.post("/api/benches/edit", json={"path": BENCH, "base_sha256": doc["sha256"], "ops": ops})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["diagnostics"] == [] and new["sha256"] != doc["sha256"]
    on_disk = yaml.safe_load((ws / BENCH).read_text(encoding="utf-8"))
    assert on_disk["instruments"]["awg"]["state"]["ch1.load"] == "INF"
    assert on_disk["description"].startswith("函数发生器")  # unicode kept, not escaped

    stale = client.post("/api/benches/edit", json={"path": BENCH, "base_sha256": doc["sha256"], "ops": ops})
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_sha256"] == new["sha256"]


def test_edit_error_is_422_and_leaves_file(client, ws):
    before = (ws / BENCH).read_bytes()
    doc = client.get("/api/benches/file", params={"path": BENCH}).json()
    r = client.post(
        "/api/benches/edit",
        json={"path": BENCH, "base_sha256": doc["sha256"], "ops": [{"op": "remove", "name": "ghost"}]},
    )
    assert r.status_code == 422 and "ghost" in r.json()["detail"]
    assert (ws / BENCH).read_bytes() == before


def test_create_new_file_and_undo_by_put(client, ws):
    data = {"schema": "simubench.bench/1", "name": "new", "instruments": {"s": {"model": "sb-dso-4"}}}
    r = client.put(
        "/api/benches/file", json={"path": "sub/new.bench.yaml", "base_sha256": None, "data": data}
    )
    assert r.status_code == 200, r.text
    created = r.json()
    again = client.put(
        "/api/benches/file", json={"path": "sub/new.bench.yaml", "base_sha256": None, "data": data}
    )
    assert again.status_code == 409
    # Undo is a save of an earlier document against the current hash.
    data2 = {**data, "name": "renamed"}
    r = client.put(
        "/api/benches/file",
        json={"path": "sub/new.bench.yaml", "base_sha256": created["sha256"], "data": data2},
    )
    assert r.json()["data"]["name"] == "renamed"
    assert "sub/new.bench.yaml" in [b["path"] for b in client.get("/api/benches").json()]


@pytest.mark.parametrize("path", ["../x.bench.yaml", "/etc/x.bench.yaml"])
def test_paths_outside_workspace_refused(client, path):
    r = client.put("/api/benches/file", json={"path": path, "base_sha256": None, "data": {}})
    assert r.status_code == 403


def test_wrong_suffix_refused(client):
    r = client.put("/api/benches/file", json={"path": "x.yaml", "base_sha256": None, "data": {}})
    assert r.status_code == 422


def test_invalid_bench_saved_as_given_and_start_refused(client):
    data = {"schema": "simubench.bench/1", "name": "bad", "instruments": {"a": {"model": "sb-awg-3"}}}
    r = client.put("/api/benches/file", json={"path": "bad.bench.yaml", "base_sha256": None, "data": data})
    assert r.status_code == 200 and not r.json()["ok"]
    r = client.post("/api/sessions", json={"bench_path": "bad.bench.yaml"})
    assert r.status_code == 422
    diags = r.json()["detail"]["diagnostics"]
    assert diags[0]["code"] == "E-MODEL-UNKNOWN" and "sb-awg-2" in diags[0]["suggestion"]


def test_validate_unsaved_data(client):
    r = client.post(
        "/api/benches/validate",
        json={"data": {"schema": "simubench.bench/1", "name": "x", "wires": [{"from": "a.B", "to": "c.D"}]}},
    )
    body = r.json()
    assert not body["ok"] and body["diagnostics"][0]["code"] == "E-WIRE-OWNER"


def test_catalog(client):
    cat = client.get("/api/catalog").json()
    by_id = {d["id"]: d for d in cat["instruments"]}
    assert by_id["sb-dso-4"]["ports"][:2] == ["CH1", "CH2"] and by_id["sb-awg-2"]["channels"] == 2
    types = {c["type"]: c for c in cat["components"]}
    assert types["rc_lowpass"]["category"] == "dut" and types["passive_probe"]["category"] == "accessory"
    assert "output_stage" not in types
    assert types["rc_lowpass"]["units"]["c"] == "F"


def test_preview_renders_valid_parts_and_keeps_wire_indices(client):
    data = {
        "schema": "simubench.bench/1",
        "name": "p",
        "instruments": {
            "awg": {"model": "sb-awg-2", "state": {"ch1.frequency": -5}},
            "ghost": {"model": "sb-nope"},
            "scope": {"model": "sb-dso-4"},
        },
        "duts": {"rc": {"type": "rc_lowpass"}},
        "wires": [
            {"from": "ghost.OUT1", "to": "rc.IN"},
            {"from": "awg.OUT9", "to": "rc.IN"},
            {"from": "rc.OUT", "to": "scope.CH1"},
        ],
    }
    client.put("/api/benches/file", json={"path": "p.bench.yaml", "base_sha256": None, "data": data})
    p = client.get("/api/benches/preview", params={"path": "p.bench.yaml"}).json()
    assert p["error"] is None
    assert set(p["omitted"]) >= {"ghost", "awg.ch1.frequency", "wire:0", "wire:1"}
    s = p["session"]
    assert [i["name"] for i in s["instruments"]] == ["awg", "scope"]
    assert [w["index"] for w in s["wires"]] == [2]
    assert s["bench_sha256"] == p["sha256"]


def test_session_reports_its_bench_hash(client):
    doc = client.get("/api/benches/file", params={"path": BENCH}).json()
    s = client.post("/api/sessions", json={"bench_path": BENCH}).json()
    assert s["bench_sha256"] == doc["sha256"]


def test_moving_nodes_keeps_the_fingerprint(client):
    doc = client.get("/api/benches/file", params={"path": BENCH}).json()
    moved = client.post(
        "/api/benches/edit",
        json={
            "path": BENCH,
            "base_sha256": doc["sha256"],
            "ops": [{"op": "move", "name": "rc", "x": 1, "y": 2}],
        },
    ).json()
    assert moved["sha256"] != doc["sha256"] and moved["fingerprint"] == doc["fingerprint"]
    changed = client.post(
        "/api/benches/edit",
        json={
            "path": BENCH,
            "base_sha256": moved["sha256"],
            "ops": [{"op": "set_params", "dut": "rc", "params": {"r": 2000}}],
        },
    ).json()
    assert changed["fingerprint"] != doc["fingerprint"]


def test_fault_api_and_link_errors_in_the_console(client):
    sid = client.post("/api/sessions", json={"bench_path": BENCH}).json()["id"]
    r = client.post(f"/api/sessions/{sid}/faults", json={"kind": "disconnect", "instrument": "awg"})
    assert r.status_code == 200, r.text
    fault = r.json()
    assert fault["status"] == "active" and fault["source"] == "ui"
    scpi = client.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": "*IDN?"}).json()
    assert "down" in scpi["link_error"]
    client.delete(f"/api/sessions/{sid}/faults/{fault['id']}")
    client.post(f"/api/sessions/{sid}/faults", json={"kind": "timeout", "instrument": "awg"})
    scpi = client.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": "*IDN?"}).json()
    assert scpi["timed_out"] and scpi["response"] is None
    faults = client.get(f"/api/sessions/{sid}/faults").json()
    assert [f["status"] for f in faults] == ["ended", "active"]
    bad = client.post(f"/api/sessions/{sid}/faults", json={"kind": "drift", "component": "rc", "param": "r"})
    assert bad.status_code == 422
    missing = client.post(f"/api/sessions/{sid}/faults", json={"kind": "timeout", "instrument": "nope"})
    assert missing.status_code == 404


def test_readings_are_passive(tmp_path):
    shutil.copy(ROOT / "acceptance" / "benches" / "lockin_rc.bench.yaml", tmp_path / "l.bench.yaml")
    app = create_app(
        SessionManager(tmp_path, scpi_base_port=None), token=TOKEN, web_dist=Path("/nonexistent")
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "l.bench.yaml"}).json()["id"]
        r = c.get(f"/api/sessions/{sid}/instruments/lia/readings").json()
        assert r["values"]["demod.R"] == pytest.approx(0.6896, abs=1e-3)  # 1 Vrms drive at 1591.55 Hz
        assert r["values"]["osc.locked"] is True
        assert len(r["traces"][0]["values"]) == 121
        events = c.get(f"/api/sessions/{sid}").json()
        assert events  # no SCPI was sent; the error queue is untouched
        scpi = c.post(f"/api/sessions/{sid}/instruments/lia/scpi", json={"command": ":SYSTem:ERRor?"}).json()
        assert scpi["response"].startswith("0,")
        dmm = c.get(f"/api/sessions/{sid}/instruments/dmm/readings").json()
        assert dmm["values"]["meter.AC"] == pytest.approx(r["values"]["demod.R"], rel=1e-6)


def test_history_records_sources_external_changes_and_restores(client, ws):
    def edit(sha, ops, source="ui"):
        r = client.post(
            "/api/benches/edit", json={"path": BENCH, "base_sha256": sha, "ops": ops, "source": source}
        )
        assert r.status_code == 200, r.text
        return r.json()

    def history():
        return client.get("/api/benches/history", params={"path": BENCH}).json()

    original = client.get("/api/benches/file", params={"path": BENCH}).json()
    a = edit(
        original["sha256"], [{"op": "set_state", "instrument": "awg", "path": "ch1.load", "value": "INF"}]
    )
    b = edit(a["sha256"], [{"op": "set_params", "dut": "rc", "params": {"r": 2000}}], source="agent")
    # The file as first seen counts as external (it was not written by SimuBench).
    assert [(v["seq"], v["source"]) for v in history()] == [(3, "agent"), (2, "ui"), (1, "external")]
    assert history()[2]["sha256"] == original["sha256"]

    # Node moves right after each other merge into one version.
    c = edit(b["sha256"], [{"op": "move", "name": "awg", "x": 10, "y": 20}])
    d = edit(c["sha256"], [{"op": "move", "name": "awg", "x": 30, "y": 40}])
    h = history()
    assert len(h) == 4 and h[0]["layout_only"] and h[0]["sha256"] == d["sha256"]

    # A text editor changes the file: seen as an external version at the next save.
    text = (ws / BENCH).read_text(encoding="utf-8").replace("r: 2000", "r: 3000")
    (ws / BENCH).write_text(text, encoding="utf-8")
    current = client.get("/api/benches/file", params={"path": BENCH}).json()
    edit(current["sha256"], [{"op": "set_params", "dut": "rc", "params": {"c": 2e-7}}])
    assert [v["source"] for v in history()[:2]] == ["ui", "external"]

    old = client.get("/api/benches/version", params={"path": BENCH, "seq": 2}).json()
    assert old["data"]["duts"]["rc"]["params"]["r"] == 1000 and old["version"]["source"] == "ui"
    head = client.get("/api/benches/file", params={"path": BENCH}).json()
    r = client.post("/api/benches/restore", json={"path": BENCH, "seq": 2, "base_sha256": head["sha256"]})
    assert r.status_code == 200 and r.json()["fingerprint"] == a["fingerprint"]
    assert history()[0]["source"] == "restore" and len(history()) == 7  # nothing was dropped
    assert client.get("/api/benches/version", params={"path": BENCH, "seq": 99}).status_code == 404
    assert client.get("/api/benches/history", params={"path": "../x.bench.yaml"}).status_code == 403


def test_wall_clock_readings_follow_real_time(tmp_path):
    data = yaml.safe_load(
        (ROOT / "acceptance" / "benches" / "lockin_rc.bench.yaml").read_text(encoding="utf-8")
    )
    data["run"] = {"clock": "wall", "seed": 1}
    (tmp_path / "w.bench.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    app = create_app(
        SessionManager(tmp_path, scpi_base_port=None), token=TOKEN, web_dist=Path("/nonexistent")
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "w.bench.yaml"}).json()["id"]
        first = c.get(f"/api/sessions/{sid}/instruments/lia/readings").json()["now_ns"]
        time.sleep(0.05)
        second = c.get(f"/api/sessions/{sid}/instruments/lia/readings").json()["now_ns"]
        assert second - first >= 40_000_000  # nothing was sent; time moved with the wall


def test_history_versions_are_not_listed_as_benches(client, ws):
    # Saving keeps versions in .simubench/history/<bench path>/, a folder named like a bench file.
    doc = client.get("/api/benches/file", params={"path": BENCH}).json()
    data = {**doc["data"], "description": "edited"}
    r = client.put("/api/benches/file", json={"path": BENCH, "base_sha256": doc["sha256"], "data": data})
    assert r.status_code == 200, r.text
    assert (ws / ".simubench" / "history" / BENCH).is_dir()
    (ws / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "node_modules" / "pkg" / "x.bench.yaml").write_text("name: not mine\n", encoding="utf-8")

    benches = client.get("/api/benches").json()
    assert [(b["path"], b.get("error")) for b in benches] == [(BENCH, None)]

    from simubench.ops import doctor

    check = next(c for c in doctor(ws) if c.name == "bench files")
    assert (check.status, check.detail) == ("ok", "1 valid")
