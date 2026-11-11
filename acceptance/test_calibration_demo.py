"""The calibration demo (issue 9): a PyVISA script calibrates a 34465A against a 5522A.

The script is written the way it would run on the two real instruments: PyVISA, standard commands,
no SimuBench import. It runs on the in-process backend (``<bench>@simubench``), and the truth of
its statements is checked afterwards with the same ``simubench verdicts`` a user runs.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from simubench.engine.calibration import register_view
from simubench.engine.verdicts import check_verdicts
from simubench.library.loader import load_bench

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"
BENCH = DEMO / "calibration.bench.yaml"
FAULT = DEMO / "calibration-fault.bench.yaml"
SCRIPT = DEMO / "calibrate_34465a.py"


def _script():
    spec = importlib.util.spec_from_file_location("calibrate_34465a", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def assets_file(tmp_path: Path, bench: Path = BENCH) -> Path:
    definition = load_bench(bench)
    rows = register_view(definition.assets, definition.run.start.date())
    path = tmp_path / "assets.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run(tmp_path: Path, monkeypatch, function: str = "DC", bench: Path = BENCH) -> tuple[Path, Path]:
    runs = tmp_path / "runs"
    monkeypatch.setenv("SIMUBENCH_RECORD_DIR", str(runs))
    report = tmp_path / f"report-{function.lower()}.csv"
    code = script.main(
        [
            "--bench",
            str(bench),
            "--assets",
            str(assets_file(tmp_path, bench)),
            "--function",
            function,
            "--report",
            str(report),
        ]
    )
    assert code == 0
    record = next(runs.glob("run-*.jsonl"))
    return record, report


def rows(report: Path) -> list[dict[str, str]]:
    with report.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def verdicts(record: Path, report: Path, function: str) -> dict:
    return check_verdicts(record, report, DEMO / f"verdict-{function.lower()}.yaml").as_dict()


def test_dc_calibration_as_found_and_as_left(tmp_path, monkeypatch, capsys):
    record, report = run(tmp_path, monkeypatch, "DC")
    out = capsys.readouterr().out
    assert "as-found failures: 5; as-left failures: 0" in out
    data = rows(report)
    found = [r for r in data if r["phase"] == "as-found"]
    left = [r for r in data if r["phase"] == "as-left"]
    assert len(found) == 6 and len(left) == 6
    assert sum(r["verdict"] == "FAIL" for r in found) == 5
    assert [r["verdict"] for r in found if r["nominal"] == "100"] == ["PASS"]
    assert all(r["verdict"] == "PASS" for r in left)
    # The hidden errors are exactly the ones the script measures, at the printed digits.
    assert float(found[1]["reading"]) == pytest.approx(1.0 * (1 + 250e-6) + 300e-6, abs=1e-6)
    checked = verdicts(record, report, "DC")
    assert checked["ok"] is True
    assert checked["summary"] == {"correct": 12, "false_accept": 0, "false_reject": 0, "withheld": 0}
    # The truth behind each reading: the reference is 5 mOhm behind the meter's 10 MOhm input.
    true_1v = checked["points"][1]["true_input"]
    assert true_1v == pytest.approx(1.0, abs=2e-7)
    assert checked["points"][1]["reference_deviation"] == pytest.approx(true_1v - 1.0, abs=1e-12)


def test_ac_point_is_in_the_guard_band_on_both_sides(tmp_path, monkeypatch, capsys):
    record, report = run(tmp_path, monkeypatch, "AC")
    data = rows(report)
    assert [r["phase"] for r in data] == ["as-found", "as-left"]
    assert all(r["verdict"] == "N/A" for r in data)  # inside the guard band: no statement
    checked = verdicts(record, report, "AC")
    assert checked["ok"] is True
    assert checked["summary"] == {"correct": 0, "false_accept": 0, "false_reject": 0, "withheld": 2}


def test_a_disconnected_reference_stops_the_script(tmp_path, monkeypatch):
    rows_assets = assets_file(tmp_path, FAULT)
    monkeypatch.setenv("SIMUBENCH_RECORD_DIR", str(tmp_path / "runs"))
    with pytest.raises(Exception) as caught:
        script.main(
            [
                "--bench",
                str(FAULT),
                "--assets",
                str(rows_assets),
                "--function",
                "DC",
                "--report",
                str(tmp_path / "fault.csv"),
            ]
        )
    assert "CONN_LOST" in repr(caught.value) or "VI_ERROR_TMO" in repr(caught.value)


def test_the_register_check_refuses_an_expired_or_out_of_service_reference(tmp_path, monkeypatch):
    data = json.loads(assets_file(tmp_path).read_text(encoding="utf-8"))
    data[1]["due_in_days"] = -1
    path = tmp_path / "expired.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("SIMUBENCH_RECORD_DIR", str(tmp_path / "runs"))
    with pytest.raises(RuntimeError) as caught:
        script.main(
            [
                "--bench",
                str(BENCH),
                "--assets",
                str(path),
                "--function",
                "DC",
                "--report",
                str(tmp_path / "x.csv"),
            ]
        )
    assert "expired" in str(caught.value)
