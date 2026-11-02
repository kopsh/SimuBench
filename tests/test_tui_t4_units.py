"""Interface-level tests for T4 (D49's unit tier): the steps file's parsing and validation, the
runs screen's rows and replay wording — no Textual, no server, milliseconds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simubench.tui.fmt import cell_len
from simubench.tui.screens.runs import (
    first_mismatch_text,
    fmt_run_duration,
    fmt_run_size,
    run_header,
    run_rows,
    selection_text,
)
from simubench.tui.script import Steps, duration_seconds, split_key, validate

pytestmark = pytest.mark.unit  # the D49 budget guard applies (<=100 ms per test)


def _steps(tmp_path: Path, data: dict) -> Steps:
    path = tmp_path / "steps.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Steps.load(path)


# ---- the steps file (tui-screens.md 6) -------------------------------------------------------------------


def test_steps_load_size_and_items(tmp_path: Path):
    steps = _steps(
        tmp_path,
        {"size": [120, 36], "steps": [{"caption": "started"}, {"key": "enter"}, {"screenshot": "overview"}]},
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
    steps = Steps.load(
        _write(
            tmp_path,
            {
                "steps": [
                    {"caption": "x", "wait": 1.5},
                    {"key": "1"},
                    {"scpi": {"instrument": "smu", "command": ":SYSTem:ERRor?"}},
                    {"advance": 0.01},
                    {"highlight": ["source.voltage.level"]},
                    {"screenshot": "end"},
                ]
            },
        )
    )
    assert validate(steps) == []


def test_validate_accepts_the_design_example(tmp_path: Path):
    """`key` and `caption` on one step, with `wait` and `highlight` too (tui-design.md 6)."""
    steps = Steps.load(
        _write(
            tmp_path,
            {
                "steps": [
                    {"key": "enter", "caption": "Open it", "wait": "1s", "highlight": ["output.enabled"]},
                ]
            },
        )
    )
    assert validate(steps) == []


def test_validate_rejects_unknown_or_empty_steps(tmp_path: Path):
    steps = Steps.load(_write(tmp_path, {"steps": [{"keyp": "1"}, {}, {"wait": 1}]}))
    problems = validate(steps)
    assert len(problems) == 3
    assert "unknown field" in problems[0] and "nothing to do" in problems[1]


def test_validate_rejects_bad_field_types(tmp_path: Path):
    steps = Steps.load(
        _write(tmp_path, {"steps": [{"key": 5}, {"scpi": {"instrument": "smu"}}, {"highlight": "x"}]})
    )
    assert len(validate(steps)) == 3


def test_wait_and_advance_accept_seconds_and_suffixes(tmp_path: Path):
    steps = Steps.load(
        _write(
            tmp_path,
            {
                "steps": [
                    {"caption": "x", "wait": "1.5s"},
                    {"key": "1", "wait": "200ms"},
                    {"advance": "10 ms"},
                    {"screenshot": "end", "wait": 0.25},
                ]
            },
        )
    )
    assert validate(steps) == []  # the design's `wait: 1.5s` form is accepted (6)
    assert duration_seconds("1.5s") == 1.5
    assert duration_seconds("200ms") == 0.2
    assert duration_seconds("10 ms") == 0.01
    assert duration_seconds(2) == 2.0
    assert duration_seconds("soon") is None and duration_seconds(-1) is None


def test_split_key_named_vs_typed():
    assert split_key("enter") == ["enter"]
    assert split_key("question_mark") == ["question_mark"]
    assert split_key("4") == ["4"]
    assert split_key("abc") == ["a", "b", "c"]


# ---- the runs screen's interface (tui-screens.md 11.3) ----------------------------------------------------


def test_run_rows_keep_the_columns_and_name_the_live_session():
    runs = [
        {
            "run_id": "r2",
            "bench": "smu",
            "started": 1793577600,
            "ended": None,
            "clock": "command",
            "seed": 5,
            "events": 56,
            "duration_ns": 31_000_000,
            "size": 1_500_000,
        },
        {
            "run_id": "r1",
            "bench": "siglent_scope_rc",
            "started": 1793491200,
            "ended": 1793491230,
            "clock": "command",
            "seed": 5,
            "events": 9,
            "duration_ns": 25_000_000,
            "size": 3_072,
        },
    ]
    rows = run_rows(runs, None, "en")
    header = run_header("en")
    assert rows[0].startswith("20") and "live" not in "\n".join(rows)  # the selection line marks it
    assert all(cell_len(row) == cell_len(header) for row in rows)  # one width: nothing wraps
    assert "1.4 MB" in rows[0] and "3 KB" in rows[1] and "0.025 s" in rows[1]
    assert "live (this session)" in selection_text(runs[0], "en")
    assert "live" not in selection_text(runs[1], "en")


def test_duration_and_size_wording():
    assert fmt_run_duration(4_210_000_000) == "4.210 s"
    assert fmt_run_duration(0) == "0.000 s"
    assert fmt_run_size(3_072) == "3 KB"
    assert fmt_run_size(217_088) == "212 KB"
    assert fmt_run_size(1_500_000) == "1.4 MB"
    assert fmt_run_size(512) == "512 B"


def test_replay_wording_ok_and_first_mismatch():
    ok = first_mismatch_text({"actions": 56, "replies": 56, "mismatches": []}, "en")
    assert "56" in ok and "all equal" in ok
    bad = first_mismatch_text(
        {
            "actions": 56,
            "replies": 56,
            "mismatches": [
                {
                    "seq": 31,
                    "instrument": "scope",
                    "command": ":WAV:DATA?",
                    "expected": "0x7F",
                    "actual": "0x80",
                }
            ],
        },
        "en",
    )
    assert "#31" in bad and "scope" in bad and "0x7F" in bad and "0x80" in bad
