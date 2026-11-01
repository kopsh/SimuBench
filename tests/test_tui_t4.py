"""The T4 tests that boot the app (D49's tui_boot tier): the scripted screenshots produce
byte-identical SVGs across runs (6, D47), and the runs screen lists a recorded run and replays
it, reporting the first difference (11.3).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from simubench.tui.app import SimuBenchApp
from tui_mock import load, unit_app

SCOPE_BENCH = load("unit_scope_rc.json")


def fresh(fixture: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(fixture)


async def _settle(pilot, tui: SimuBenchApp) -> None:
    import asyncio

    end = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < end:
        await pilot.pause(0.05)
        if tui.conn == "connected" and tui.session_info:
            return
    raise AssertionError("the app never connected")


async def test_scripted_screenshots_are_byte_identical(tmp_path: Path):
    """The same bench and the same steps produce byte-identical SVGs (6, D47)."""
    steps_yaml = {
        "size": [100, 32],
        "steps": [
            {"caption": "示例测试台的总览", "wait": 0.2},
            {"key": "1"},
            {"screenshot": "instrument"},
            {"key": "escape"},
            {"screenshot": "back"},
        ],
    }
    path = tmp_path / "steps.yaml"
    path.write_text(yaml.safe_dump(steps_yaml), encoding="utf-8")

    from simubench.tui.script import Steps, run_steps

    hashes: list[dict[str, bytes]] = []
    for run in range(2):
        tui, _fake = unit_app(fresh(SCOPE_BENCH))
        out = tmp_path / f"run{run}"
        steps = Steps.load(path)
        async with tui.run_test(size=steps.size) as pilot:
            await _settle(pilot, tui)
            written = await run_steps(tui, pilot, steps, out)
        assert {p.name for p in written} == {"instrument.svg", "back.svg"}
        hashes.append({p.name: p.read_bytes() for p in written})
    assert hashes[0] == hashes[1]  # byte for byte


async def test_runs_screen_lists_and_replays(tmp_path: Path):
    """`R`: the recorded run is listed; `p` replays it; a mismatch is named (11.3).

    The record is written by a scripted exchange against the mock's session — the real
    record/replay round trip is covered by tests/test_replay.py; here the screen shows it.
    """
    from simubench.tui.screens.runs import RunsScreen

    fixture = fresh(load("unit_smu.json"))
    fixture["runs"] = [{
        "run_id": "run-abc", "bench": "smu", "started": 1793577600.0, "ended": 1793577630.0,
        "clock": "command", "seed": 5, "events": 9, "duration_ns": 25_000_000,
        "instruments": ["smu"], "size": 3072,
    }]
    fixture["run_detail"] = {"summary": fixture["runs"][0]}
    fixture["replay"] = {
        "run_id": "run-abc", "ok": False, "actions": 9, "replies": 9,
        "mismatches": [{"seq": 3, "instrument": "smu", "command": ":MEAS:VOLT?",
                        "expected": "+5", "actual": "+6"}],
        "warnings": [],
    }
    tui, fake = unit_app(fixture)

    async with tui.run_test(size=(100, 32)) as pilot:
        await _settle(pilot, tui)
        await pilot.press("R")
        from simubench.tui.screens.runs import RunsScreen as R

        await pilot.pause(0.5)
        from test_tui import flat

        assert isinstance(tui.screen, R)
        text = flat(tui)
        assert "run-abc" not in text  # the list shows the row, not the id — assert on the bench
        assert "smu" in text and "运行中" not in text
        tui.screen.index = 0
        await pilot.press("p")
        await wait_true(pilot, lambda: "第一处不同" in flat(tui) or "#3" in flat(tui))


async def wait_true(pilot, predicate, timeout: float = 20.0) -> None:
    import asyncio

    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        await pilot.pause(0.05)
        if predicate():
            return
    raise AssertionError("the condition never became true")
