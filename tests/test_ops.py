"""Operations: structured logs, manifest, health checks, diagnostics bundle (M4)."""

import io
import json
import logging
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from simubench.cli import main
from simubench.ops import RING, JsonFormatter, doctor, manifest, setup_logging
from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "secret-token-xyz"


def test_json_log_lines_carry_extra_fields():
    rec = logging.LogRecord("simubench.x", logging.WARNING, __file__, 1, "link %s down", ("awg",), None)
    rec.session = "s1"
    out = json.loads(JsonFormatter().format(rec))
    assert out["level"] == "warning" and out["msg"] == "link awg down" and out["session"] == "s1"
    assert out["ts"].endswith("+00:00")


def test_ring_keeps_recent_records():
    setup_logging("warning", "json")
    logging.getLogger("simubench.test").info("kept in the ring, not printed")
    assert any("kept in the ring" in r for r in RING.records)


def test_manifest_lists_definitions_with_hashes():
    m = manifest(ROOT / "examples")
    ids = {d["id"] for d in m["definitions"]}
    assert {"sb-dso-4", "sb-smu-1", "sb-sg-1"} <= ids
    assert all(len(d["sha256"]) == 64 for d in m["definitions"])
    assert m["dependencies"]["pydantic"] and "smu_core" in m["component_types"]


def test_doctor_passes_here_and_reports_broken_benches(tmp_path):
    assert all(c.status != "fail" for c in doctor(ROOT / "examples"))
    (tmp_path / "bad.bench.yaml").write_text(
        "schema: simubench.bench/1\nname: bad\ninstruments: {x: {model: nope}}\n"
    )
    checks = {c.name: c for c in doctor(tmp_path)}
    assert checks["bench files"].status == "warn" and "bad.bench.yaml" in checks["bench files"].detail
    assert {c.name: c for c in doctor(tmp_path / "missing")}["workspace"].status == "fail"
    assert main(["doctor", "--workspace", str(ROOT / "examples"), "--json"]) == 0


def test_diagnostics_bundle_over_the_api(tmp_path):
    app = create_app(
        SessionManager(ROOT / "validation", scpi_base_port=None, record_dir=tmp_path / "runs"),
        token=TOKEN,
        web_dist=Path("/nonexistent"),
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
        c.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": ":BOGus"})
        r = c.get("/api/diagnostics")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(z.namelist())
        assert {"manifest.json", "doctor.json", "logs.jsonl", "sessions.json"} <= names
        assert any(n.startswith("benches/") for n in names) and any(n.startswith("runs/run-") for n in names)
        sessions = json.loads(z.read("sessions.json"))
        assert sessions[0]["recent_errors_and_faults"][0]["code"] == -113
        for n in names:  # the access token never leaves
            assert TOKEN.encode() not in z.read(n)
        assert c.get("/api/manifest").json()["simubench"]
        assert any(x["name"] == "python" for x in c.get("/api/doctor").json())
        c.delete(f"/api/sessions/{sid}")


def test_cli_diagnose(tmp_path):
    out = tmp_path / "d.zip"
    assert main(["diagnose", "--out", str(out), "--workspace", str(ROOT / "examples")]) == 0
    assert "manifest.json" in zipfile.ZipFile(out).namelist()


def test_metrics_and_net_fidelity_notes():
    app = create_app(
        SessionManager(ROOT / "acceptance", scpi_base_port=None), token=TOKEN, web_dist=Path("/nonexistent")
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        info = c.post("/api/sessions", json={"bench_path": "benches/lockin_chopper.bench.yaml"}).json()
        noted = [n for n in info["nets"] if n["notes"]]
        assert noted and "harmonics" in noted[0]["notes"][0]  # a square wave is truncated: said so
        c.post(f"/api/sessions/{info['id']}/instruments/lia/scpi", json={"command": "*IDN?"})
        text = c.get("/api/metrics").text
        assert "simubench_sessions 1" in text
        assert (
            f'simubench_events_total{{session="{info["id"]}",bench="acceptance-lockin-chopper",kind="scpi"}} 2'
            in text
        )
        assert "simubench_solves_total{" in text and "# TYPE simubench_solve_seconds_total counter" in text
        c.delete(f"/api/sessions/{info['id']}")
