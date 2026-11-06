"""The T4 tests that boot the app (D49's tui_boot tier): the scripted screenshots produce
byte-identical SVGs across runs (6, D47), and the runs screen lists a recorded run and replays
it, reporting the first difference (11.3).
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
import yaml

from helpers import stop_manager_sessions
from simubench.server.app import create_app
from simubench.server.manager import SessionManager
from simubench.tui.app import SimuBenchApp
from tui_mock import flat, fresh, load, settle, unit_app, wait_for

pytestmark = pytest.mark.tui_boot  # boots a Textual app or a real server: runs on main, not the fast checks

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs/tui"
SCOPE_BENCH = load("unit_scope_rc.json")


@pytest.fixture(autouse=True)
def color_theme(monkeypatch):
    """The committed demo screenshots are the colour theme's, like CI's."""
    monkeypatch.delenv("NO_COLOR", raising=False)


async def test_scripted_screenshots_are_byte_identical(tmp_path: Path):
    """The same bench and the same steps produce byte-identical SVGs (6, D47)."""
    steps_yaml = {
        "size": [100, 32],
        "steps": [
            {"caption": "The example bench's overview", "wait": 0.2},
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
            await settle(pilot, tui)
            written = await run_steps(tui, pilot, steps, out)
        assert {p.name for p in written} == {"instrument.svg", "back.svg"}
        hashes.append({p.name: p.read_bytes() for p in written})
    assert hashes[0] == hashes[1]  # byte for byte


async def test_runs_screen_lists_and_replays(tmp_path: Path):
    """`R`: the recorded run is listed; `p` replays it; a mismatch is named (11.3).

    The record is written by a scripted exchange against the mock's session — the real
    record/replay round trip is covered by tests/test_replay.py; here the screen shows it.
    """
    fixture = fresh(load("unit_smu.json"))
    fixture["runs"] = [
        {
            "run_id": "run-abc",
            "bench": "smu",
            "started": 1793577600.0,
            "ended": 1793577630.0,
            "clock": "command",
            "seed": 5,
            "events": 9,
            "duration_ns": 25_000_000,
            "instruments": ["smu"],
            "size": 3072,
        }
    ]
    fixture["run_detail"] = {"summary": fixture["runs"][0]}
    fixture["replay"] = {
        "run_id": "run-abc",
        "ok": False,
        "actions": 9,
        "replies": 9,
        "mismatches": [
            {"seq": 3, "instrument": "smu", "command": ":MEAS:VOLT?", "expected": "+5", "actual": "+6"}
        ],
        "warnings": [],
    }
    tui, _fake = unit_app(fixture)

    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await pilot.press("R")
        from simubench.tui.screens.runs import RunsScreen as R

        await pilot.pause(0.5)

        assert isinstance(tui.screen, R)
        text = flat(tui)
        assert "run-abc" not in text  # the list shows the row, not the id — assert on the bench
        assert "smu" in text and "live" not in text
        tui.screen.index = 0
        await pilot.press("p")
        await wait_for(pilot, lambda: "first mismatch" in flat(tui) or "#3" in flat(tui))


# ---- --record (tui-screens.md 6) -------------------------------------------------------------------


async def test_record_writes_frames_and_a_playable_cast(tmp_path: Path):
    """`--record` turns each hold into frames plus an asciinema v2 cast, and it really moves."""
    from simubench.tui.script import Steps, run_steps

    path = tmp_path / "steps.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "size": [100, 32],
                "steps": [
                    {"caption": "Overview", "wait": 0.5},
                    {"screenshot": "one"},
                    {"key": "l", "wait": 0.25},
                    {"screenshot": "two"},
                ],
            }
        ),
        encoding="utf-8",
    )
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    out = tmp_path / "recorded"
    steps = Steps.load(path)
    async with tui.run_test(size=steps.size) as pilot:
        await settle(pilot, tui)
        written = await run_steps(tui, pilot, steps, out, record=True)

    assert [p.name for p in written] == ["one.svg", "two.svg"]
    frames = sorted(out.glob("frame-*.svg"))
    assert len(frames) == round(0.5 * 8) + round(0.25 * 8)  # one frame per 1/8 s of each hold
    assert frames[0].read_bytes() == (out / "one.svg").read_bytes()  # a frame is a screenshot
    assert frames[0].read_bytes() != frames[-1].read_bytes()  # the key moved the focus

    lines = (out / "demo.cast").read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert header["version"] == 2 and (header["width"], header["height"]) == (100, 32)
    events = [json.loads(line) for line in lines[1:]]
    assert len(events) == len(frames)
    assert all(event[1] == "o" for event in events)
    assert [event[0] for event in events] == [i / 8 for i in range(len(frames))]
    assert "\x1b[2J" in events[0][2] and "Overview" in events[0][2]  # a terminal frame, not a file name


# ---- the demo and the docs screenshots (6, issue 178's acceptance) ---------------------------------


@pytest.fixture(scope="module")
def demo_server(tmp_path_factory):
    """The demo bench on fixed ports: its screenshots show the SCPI address, so it must be fixed."""
    records = tmp_path_factory.mktemp("runs")
    manager = SessionManager(ROOT / "validation", scpi_base_port=5195, hislip_port=4899, record_dir=records)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(manager, web_dist=Path("/nonexistent")),
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}", manager, records
    asyncio.run(stop_manager_sessions(manager))  # the sessions hold the HiSLIP port, not uvicorn (issue 686)
    server.should_exit = True
    thread.join(timeout=10)


async def test_the_demo_regenerates_the_docs_screenshots(demo_server, tmp_path: Path):
    """`docs/tui/find-the-trip.steps.yaml` → byte-identical SVGs, and the run record reviews it."""
    from simubench.tui.script import Steps, run_steps, validate

    url, manager, records = demo_server
    steps = Steps.load(DOCS / "find-the-trip.steps.yaml")
    assert validate(steps) == []
    served = await manager.start("benches/find-the-trip.bench.yaml", seed=5, clock="command")
    try:
        tui = SimuBenchApp(url, sid=served.id)
        async with tui.run_test(size=steps.size) as pilot:
            await settle(pilot, tui)
            written = await run_steps(tui, pilot, steps, tmp_path)
        assert [p.name for p in written] == ["find-the-trip.svg", "find-the-trip-error.svg"]
        for path in written:
            assert path.read_bytes() == (DOCS / path.name).read_bytes()

        # The same demo left a run record: the external client's command and the preset trip's
        # error are in it, so the screenshots' state can be reviewed afterwards (issue 178).
        record_files = list(records.glob("run-*.jsonl"))
        assert len(record_files) == 1  # the demo wrote the run record it can be reviewed from
        runs = manager.runs.list()
        assert [run.bench for run in runs] == ["find-the-trip"]
        detail = manager.runs.detail(runs[0].run_id)
        reads = [
            e
            for e in detail.events
            if e.kind == "scpi" and e.dir == "write" and (e.text or "").strip() == ":SYSTem:ERRor?"
        ]
        assert reads and reads[0].client and reads[0].client.startswith("socket:")
        assert reads[0].client.endswith(":5401")  # the pinned source port, from the steps file
        errors = [e for e in detail.events if e.kind == "error"]
        assert [e.code for e in errors] == [-300]
        assert any("Overvoltage" in (e.message or "") for e in errors)
        assert any(e.kind == "fault" and e.source == "bench" for e in detail.events)
    finally:
        await manager.stop(served.id)


async def test_the_modules_sessions_stopping_releases_its_hislip_port(demo_server):
    """The teardown stops the sessions, not just uvicorn (issue 686): the fixed HiSLIP port
    binds again, as the next module's fixed port needs when two files run in one process."""
    manager, _records = demo_server[1], demo_server[2]
    _served = await manager.start("benches/find-the-trip.bench.yaml", seed=5, clock="command")
    port = manager.hislip_port
    with socket.socket() as probe, pytest.raises(OSError):
        probe.bind(("127.0.0.1", port))  # the session holds it
    await stop_manager_sessions(manager)  # what the module fixtures' teardown runs
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))  # released: the next module's bind will succeed
