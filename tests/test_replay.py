"""Run records keep acquisitions as recipes, and replay reproduces a run reply for reply (D36)."""

import json
import platform
from pathlib import Path

import pytest

from simubench import __version__
from simubench.api import open_bench
from simubench.cli import main
from simubench.models.bench import Fault
from simubench.replay import replay

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "acceptance" / "benches" / "awg_rc_scope.bench.yaml"


def record_run(directory: Path, clock: str = "command", raw: bool = False) -> Path:
    s = open_bench(BENCH, record_dir=directory, clock=clock, record_raw=raw)
    s.write("awg", b":OUTPut1:STATe ON;:SOURce1:FREQuency 1591.55\n")
    s.write("scope", b":CHANnel1:SCALe 0.1;:TIMebase:SCALe 0.0005;:DIGitize\n")
    s.write("scope", b":WAVeform:DATA?\n")
    s.read("scope")
    s.write("scope", b":WAVeform:PREamble?\n")
    s.read("scope")
    if clock == "stepped":
        s.advance(0.00025)
    s.instrument("awg").set_state("ch1.frequency", 3000.0, source="ui")  # a change made in the UI
    s.faults.inject(Fault(kind="error", instrument="scope", code=-313))
    s.write("scope", b":DIGitize;:WAVeform:DATA?\n")
    s.read("scope")
    s.write("scope", b":SYSTem:ERRor?\n")
    s.read("scope")
    s.close()
    return next(directory.glob("run-*.jsonl"))


def lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("clock", ["command", "stepped"])
def test_replay_reproduces_every_reply(tmp_path, clock):
    rec = record_run(tmp_path, clock)
    report = replay(rec)
    assert report.ok, report.as_dict()
    assert report.replies == 4 and report.actions >= 6 and report.warnings == []


def test_acquisitions_are_recorded_as_recipes(tmp_path):
    records = lines(record_run(tmp_path))
    header = records[0]
    assert header["bench_data"]["parts"]["rc"]["params"]["r"] == 1000.0
    assert len(header["definitions"]["scope"]["sha256"]) == 64
    acquisitions = [r for r in records if r["kind"] == "acquisition"]
    assert len(acquisitions) == 2
    ch1 = acquisitions[0]["channels"]["1"]
    assert ch1["points"] == 1000 and ch1["dt_s"] == pytest.approx(5e-6) and acquisitions[0]["seed"] == 7
    blocks = [
        r["data"] for r in records if r["kind"] == "scpi" and r["dir"] == "read" and "sha256" in r["data"]
    ]
    assert len(blocks) == 2
    assert blocks[0]["recipe"] == acquisitions[0]["seq"] and blocks[0]["size"] == 2007
    assert "b64" not in blocks[0]  # not byte for byte
    texts = [r["data"] for r in records if r["kind"] == "scpi" and r["dir"] == "read" and "text" in r["data"]]
    assert any(t["text"].startswith("-313,") for t in texts)  # text replies are kept verbatim


def test_raw_recording_keeps_the_bytes(tmp_path):
    records = lines(record_run(tmp_path, raw=True))
    block = next(
        r["data"] for r in records if r["kind"] == "scpi" and r["dir"] == "read" and "sha256" in r["data"]
    )
    assert "b64" in block and "recipe" in block


def test_a_changed_bench_is_located(tmp_path):
    rec = record_run(tmp_path)
    records = lines(rec)
    records[0]["bench_data"]["parts"]["rc"]["params"]["r"] = 1100.0  # as if the bench had drifted
    rec.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    report = replay(rec)
    assert not report.ok
    first = report.mismatches[0]
    assert first.instrument == "scope" and first.command == ":WAVeform:DATA?"
    assert "sha256" in first.expected
    assert main(["replay", str(rec)]) == 1
    assert main(["replay", str(rec), "--json", "--first"]) == 1


def test_cli_replay(tmp_path, capsys):
    rec = record_run(tmp_path)
    assert main(["replay", str(rec)]) == 0
    assert "identical" in capsys.readouterr().out
    other = tmp_path / "x.jsonl"
    other.write_text('{"kind": "nothing"}\n', encoding="utf-8")
    assert main(["replay", str(other)]) == 2


def test_runs_api(tmp_path):
    from fastapi.testclient import TestClient

    from simubench.server.app import create_app
    from simubench.server.manager import SessionManager

    record_run(tmp_path / "runs")
    app = create_app(
        SessionManager(tmp_path, scpi_base_port=None, record_dir=tmp_path / "runs"),
        token="t",
        web_dist=Path("/nonexistent"),
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = "t"
        runs = c.get("/api/runs").json()
        assert len(runs) == 1 and runs[0]["bench"] == "acceptance-awg-rc-scope" and runs[0]["ended"]
        run_id = runs[0]["run_id"]
        detail = c.get(f"/api/runs/{run_id}").json()
        kinds = {e["kind"] for e in detail["events"]}
        assert {"scpi", "state", "acquisition", "fault"} <= kinds
        block = next(e for e in detail["events"] if e.get("sha256"))
        assert block["size"] == 2007 and block["recipe"] is not None
        writes = [e for e in detail["events"] if e.get("dir") == "write"]
        assert writes[0]["text"].startswith(":OUTPut1:STATe ON")
        result = c.post(f"/api/runs/{run_id}/replay").json()
        assert result["ok"] and result["replies"] == 4
        assert c.get("/api/runs/..%2Fx").status_code == 404
        assert c.get("/api/runs/nope").status_code == 404


def test_a_clients_own_wait_is_recorded_and_replays(tmp_path):
    """A read that timed out or an SRQ wait moves a command clock; the record keeps it and replay repeats it."""
    s = open_bench(BENCH, record_dir=tmp_path, clock="command")
    s.write("awg", b":OUTPut1:STATe ON;:SOURce1:FREQuency 1591.55\n")
    s.wait(0.25)  # a script's timeout
    s.write("scope", b":CHANnel1:SCALe 0.1;:DIGitize\n")
    s.write("scope", b":WAVeform:PREamble?\n")
    s.read("scope")
    s.close()
    path = next(tmp_path.glob("run-*.jsonl"))
    waits = [e for e in lines(path) if e["kind"] == "advance" and e.get("reason") == "wait"]
    assert [e["dt_ns"] for e in waits] == [250_000_000]
    report = replay(path)
    assert report.ok, report.as_dict()


def _rewritten_header(rec: Path, **changes) -> Path:
    records = lines(rec)
    records[0].update(changes)
    rec.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return rec


def test_replay_warns_when_the_engine_version_or_the_platform_differs(tmp_path):
    """#1512: a replay promises identical replies on the same version and platform (D20), so a
    record made elsewhere says so; the same machine says nothing."""
    rec = record_run(tmp_path)
    assert replay(rec).warnings == []  # step: same version, same platform
    _rewritten_header(rec, engine="2020.1.1.1")
    warnings = replay(rec).warnings
    assert len(warnings) == 1 and "engine 2020.1.1.1" in warnings[0]  # step: another engine version
    _rewritten_header(rec, platform="Windows-10-10.0.19045-SP0")
    warnings = replay(rec).warnings
    assert len(warnings) == 2 and "made on Windows" in warnings[1]  # step: another operating system
    # step: the same family with another kernel or libc is not a difference
    same_family = platform.platform().split("-", 1)[0] + "-1.2.3-other-with-glibc2.0"
    _rewritten_header(rec, engine=__version__, platform=same_family)
    assert replay(rec).warnings == []
