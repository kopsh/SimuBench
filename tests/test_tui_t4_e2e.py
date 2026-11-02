"""The `simubench tui --script` path end to end (6, D49's tui_boot tier): a real CLI process
starts the service, opens the session over its own HTTP API, drives the app headlessly, writes
the SVGs, frames and run record, and shuts everything down.

The in-process tests of ``test_tui_t4.py`` cover the steps engine and the screen; this one
covers the wiring the CLI adds (argument parsing, the served session, one record per run).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.tui_boot

ROOT = Path(__file__).resolve().parents[1]


def test_the_cli_runs_a_script_and_leaves_one_reviewable_run(tmp_path: Path):
    steps = tmp_path / "steps.yaml"
    steps.write_text(
        yaml.safe_dump(
            {
                "size": [100, 32],
                "steps": [
                    {"caption": "A demo in the terminal", "wait": 0.5},
                    {"screenshot": "cli"},
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "shots"
    runs = tmp_path / "runs"
    command = [
        sys.executable,
        "-c",
        "from simubench.cli import main; raise SystemExit(main())",
        "tui",
        "validation/benches/find-the-trip.bench.yaml",
        "--workspace",
        "validation",
        "--scpi-base-port",
        "0",
        "--hislip-port",
        "0",
        "--script",
        str(steps),
        "--out",
        str(out),
        "--record",
        "--record-dir",
        str(runs),
    ]
    env = {**os.environ, "SIMUBENCH_DEFINITIONS": str(ROOT / "validation/instruments")}
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env, timeout=180)
    assert result.returncode == 0, result.stderr
    assert "1 SVG(s)" in result.stdout

    svg = out / "cli.svg"
    assert svg.is_file() and "SimuBench" in svg.read_text(encoding="utf-8")
    frames = sorted(out.glob("frame-*.svg"))
    assert len(frames) == round(0.5 * 8)  # --record: one frame per 1/8 s of the hold
    cast = (out / "demo.cast").read_text(encoding="utf-8").splitlines()
    assert json.loads(cast[0])["version"] == 2 and len(cast) == 1 + len(frames)

    # One session and one run record: the CLI must not start another session behind its back.
    records = list(runs.glob("run-*.jsonl"))
    assert len(records) == 1
    header = json.loads(records[0].read_text(encoding="utf-8").splitlines()[0])
    assert header["kind"] == "header" and header["bench"] == "find-the-trip"
