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
