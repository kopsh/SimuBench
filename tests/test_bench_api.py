import shutil
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
    shutil.copy(ROOT / "examples" / "benches" / BENCH, tmp_path / BENCH)
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
