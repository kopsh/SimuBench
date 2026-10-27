"""Run records keep acquisitions as recipes, and replay reproduces a run reply for reply (D36)."""

import json
from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.cli import main
from simubench.engine.replay import replay
from simubench.models.bench import Fault

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
    assert header["bench_data"]["duts"]["rc"]["params"]["r"] == 1000.0
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
    records[0]["bench_data"]["duts"]["rc"]["params"]["r"] = 1100.0  # as if the bench had drifted
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
