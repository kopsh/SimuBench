"""Interface-level tests for T4 (D49's unit tier): the steps file's parsing and validation, the
runs screen's rows and replay wording — no Textual, no server, milliseconds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simubench.tui.script import Steps, split_key, validate
from simubench.tui.screens.runs import first_mismatch_text, run_rows

pytestmark = pytest.mark.unit  # the D49 budget guard applies (<=100 ms per test)


def _steps(tmp_path: Path, data: dict) -> Steps:
    path = tmp_path / "steps.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Steps.load(path)


# ---- the steps file (tui-screens.md 6) -------------------------------------------------------------------


def test_steps_load_size_and_items(tmp_path: Path):
    steps = _steps(
        tmp_path,
        {"size": [120, 36], "steps": [{"caption": "开始"}, {"key": "enter"}, {"screenshot": "overview"}]},
    )
    assert steps.size == (120, 36)
    assert [list(s) for s in steps.items] == [["caption"], ["key"], ["screenshot"]]


def test_steps_default_size(tmp_path: Path):
    assert Steps.load(_write(tmp_path, {"steps": []})).size == (100, 32)


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_validate_accepts_a_good_file(tmp_path: Path):
    steps = Steps.load(_write(
        tmp_path,
        {"steps": [
            {"caption": "x", "wait": 1.5},
            {"key": "1"},
            {"scpi": {"instrument": "smu", "command": ":SYSTem:ERRor?"}},
            {"advance": 0.01},
            {"highlight": ["source.voltage.level"]},
            {"screenshot": "end"},
        ]},
    ))
    assert validate(steps) == []


def test_validate_rejects_missing_or_double_actions(tmp_path: Path):
    steps = Steps.load(_write(tmp_path, {"steps": [{"caption": "x", "key": "1"}, {"wait": 1}]}))
    problems = validate(steps)
    assert len(problems) == 2  # two actions in step 0, none in step 1


def test_validate_rejects_bad_field_types(tmp_path: Path):
    steps = Steps.load(_write(tmp_path, {"steps": [{"key": 5}, {"scpi": {"instrument": "smu"}}, {"highlight": "x"}]}))
    assert len(validate(steps)) == 3


def test_split_key_named_vs_typed():
    assert split_key("enter") == ["enter"]
    assert split_key("question_mark") == ["question_mark"]
    assert split_key("4") == ["4"]
    assert split_key("abc") == ["a", "b", "c"]


# ---- the runs screen's interface (tui-screens.md 11.3) ----------------------------------------------------


def test_run_rows_newest_first_with_live_marker():
    runs = [
        {"run_id": "r2", "bench": "smu", "started": 1793577600, "ended": None, "clock": "command", "seed": 5,
         "events": 56, "duration_ns": 31_000_000},
        {"run_id": "r1", "bench": "smu", "started": 1793491200, "ended": 1793491230, "clock": "command",
         "seed": 5, "events": 9, "duration_ns": 25_000_000},
    ]
    rows = run_rows(runs, None, "zh")
    assert "运行中" in rows[0] and rows[0].startswith("20")
    assert "运行中" not in rows[1]


def test_replay_wording_ok_and_first_mismatch():
    ok = first_mismatch_text({"actions": 56, "replies": 56, "mismatches": []}, "zh")
    assert "56" in ok and "一致" in ok
    bad = first_mismatch_text(
        {"actions": 56, "replies": 56, "mismatches": [
            {"seq": 31, "instrument": "scope", "command": ":WAV:DATA?", "expected": "0x7F", "actual": "0x80"}
        ]},
        "zh",
    )
    assert "#31" in bad and "scope" in bad and "0x7F" in bad and "0x80" in bad
