"""Operations: structured logs, manifest, health checks, diagnostics bundle (M4)."""

import io
import json
import logging
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from simubench.api import open_bench
from simubench.cli import main
from simubench.scpi.errors import ScpiError
from simubench.server.app import create_app
from simubench.server.manager import SessionManager
from simubench.server.ops import RING, JsonFormatter, doctor, manifest, setup_logging

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


def test_configure_and_panel_reach_the_ring(tmp_path):
    """#1694: the postmortem reads the diagnostics ring (``logs.jsonl``), not a caplog capture --
    an out-of-band write and a panel press land in ``RING.records`` even when the stream prints
    only warnings, and a refused write leaves its line too."""
    setup_logging("warning", "json")  # the stream prints warnings; the ring keeps INFO
    path = tmp_path / "ring.bench.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema": "simubench.bench/1",
                "name": "ring",
                "instruments": {"scope": {"model": "sb-dso-4"}},
                "run": {"clock": "command", "seed": 3, "idle": "off"},
            }
        ),
        encoding="utf-8",
    )
    with open_bench(path) as s:
        s.configure("scope", "ch1.scale", "0.05", client="agent")
        s.panel("scope", "stop", client="ui")
        with pytest.raises(ScpiError):
            s.panel("scope", "launch", client="ui")
    assert any("configure scope ch1.scale = '0.05' (client=agent)" in r for r in RING.records)
    assert any("panel scope stop (client=ui)" in r for r in RING.records)
    assert any("panel scope launch (client=ui) failed: ScpiError" in r for r in RING.records)


def test_manifest_lists_definitions_with_hashes():
    m = manifest(ROOT / "examples")
    ids = {d["id"] for d in m["definitions"]}
    assert {"sb-dso-4", "sb-smu-1", "sb-sg-1"} <= ids
    assert all(len(d["sha256"]) == 64 for d in m["definitions"])
    assert m["dependencies"]["pydantic"] and "smu_meter" in m["component_types"]


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


def test_serve_names_a_bench_file_it_cannot_read(tmp_path, capsys):
    """A mistyped bench path stops serve with one line, before uvicorn's lifespan traceback (#1672)."""
    assert main(["serve", str(tmp_path / "missing.bench.yaml")]) == 1
    not_a_bench = tmp_path / "README.md"
    not_a_bench.write_text("a: [unclosed\n", encoding="utf-8")
    assert main(["serve", str(not_a_bench)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert str(tmp_path / "missing.bench.yaml") in err and str(not_a_bench) in err


def test_the_serving_entries_share_one_set_of_service_options(monkeypatch, tmp_path):
    """serve / mcp / tui parse the same options into the same manager (issue 637, issue 673):
    a flag one entry lacks would be a setting its users cannot make."""
    from simubench import cli

    seen: dict[str, object] = {}

    def record(name):
        def fake(args):
            seen[name] = args

        return fake

    for name in ("_serve", "_mcp", "_tui"):
        monkeypatch.setattr(cli, name, record(name))
    common = [
        "--scpi-base-port",
        "6000",
        "--hislip-port",
        "4900",
        "--record-dir",
        str(tmp_path / "runs"),
        "--record-raw",
        "--allow-plugins",
    ]
    main(["serve", *common])
    main(["mcp", *common])
    main(["tui", *common])
    assert set(seen) == {"_serve", "_mcp", "_tui"}
    for name, args in seen.items():
        manager = cli.build_manager(args, tmp_path)
        assert manager.scpi_base_port == 6000, name
        assert manager.hislip_port == 4900, name
        assert manager.record_dir == (tmp_path / "runs").resolve(), name
        assert manager.record_raw is True, name
        assert manager.allow_plugins is True, name


@pytest.mark.parametrize(
    "entry, option",
    [
        ("serve", "--port"),
        ("serve", "--scpi-base-port"),
        ("serve", "--hislip-port"),
        ("serve", "--vxi11-port"),
        ("serve", "--portmapper-port"),
        ("serve", "--link-port"),
        ("launch", "--port"),
        ("mcp", "--port"),
        ("tui", "--hislip-port"),
        ("doctor", "--port"),
        ("doctor", "--scpi-base-port"),
    ],
)
def test_a_port_out_of_range_stops_before_the_entry_starts(entry, option, monkeypatch, capsys):
    """#1673: 99999 wrapped modulo 65536 and the banner named a port nothing listened on."""
    from simubench import cli

    started = []
    for name in ("_serve", "_launch", "_mcp", "_tui", "_doctor"):
        monkeypatch.setattr(cli, name, started.append)
    with pytest.raises(SystemExit) as stopped:
        main([entry, option, "99999"])
    assert stopped.value.code == 2
    assert started == []
    err = capsys.readouterr().err
    assert option in err and "99999" in err and "0–65535" in err


def test_the_web_bench_port_refuses_zero(monkeypatch, capsys):
    """#1673: serve printed ``:0`` in the banner; 0 has no printable address, so it is refused."""
    from simubench import cli

    started = []
    monkeypatch.setattr(cli, "_serve", started.append)
    with pytest.raises(SystemExit) as stopped:
        main(["serve", "--port", "0"])
    assert stopped.value.code == 2 and started == []
    err = capsys.readouterr().err
    assert "--port" in err and "界面端口不能是 0" in err


def test_port_zero_still_lets_the_os_choose_the_scpi_family(monkeypatch):
    """--hislip-port 0 / --scpi-base-port 0 / --vxi11-port 0 stay valid (D41, D63)."""
    from simubench import cli

    seen: dict[str, object] = {}
    monkeypatch.setattr(cli, "_serve", lambda args: seen.update(vars(args)))
    main(["serve", "--scpi-base-port", "0", "--hislip-port", "0", "--vxi11-port", "0", "--link-port", "0"])
    assert seen["scpi_base_port"] == 0
    assert seen["hislip_port"] == 0
    assert seen["vxi11_port"] == 0
    assert seen["link_port"] == 0


def test_no_scpi_turns_both_listener_families_off(monkeypatch, tmp_path):
    """--no-scpi closes the sockets and the HiSLIP port with them (D41: they start together)."""
    from simubench import cli

    seen: dict[str, object] = {}

    def fake(args):
        seen["args"] = args

    monkeypatch.setattr(cli, "_serve", fake)
    main(["serve", "--no-scpi"])
    manager = cli.build_manager(seen["args"], tmp_path)
    assert manager.scpi_base_port is None and manager.hislip_port is None


def test_the_address_table_line_format():
    """One format from serve and the TUI launch (issue 637): kind left-aligned in 6, name in 12."""
    from simubench.service import address_line

    assert (
        address_line("SCPI", "calibrator", "TCPIP0::127.0.0.1::5025::SOCKET")
        == "  SCPI   calibrator   TCPIP0::127.0.0.1::5025::SOCKET"
    )
    assert (
        address_line("HiSLIP", "scope", "TCPIP0::127.0.0.1::hislip1,4880::INSTR")
        == "  HiSLIP scope        TCPIP0::127.0.0.1::hislip1,4880::INSTR"
    )


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
