"""The overview's snapshot tests (tui-screens.md 14): zh/en at 100×32 and 80×24.

The session is deterministic: the demo bench (tests/data/tui/demo.bench.yaml, contents per
section 1.3) with a fixed seed, the command clock and idle off (D47), and the six commands of
the section 4 communication log executed before the TUI connects (they come with the backlog).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from simubench.server.app import create_app
from simubench.server.manager import SessionManager
from simubench.tui.app import SimuBenchApp

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "tests/data/tui/demo.bench.yaml"


@pytest.fixture(scope="module")
def demo_server():
    """A real uvicorn server with the demo bench session and the section 4 log already written.

    The SCPI and HiSLIP ports are fixed: the instrument screen shows the resource address
    (tui-screens.md 5.1), so the SVG is only reproducible when the ports are (5175–5178, 4891).
    """
    import socket

    import uvicorn

    for port in (5175, 5275, 5375):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            break
    else:
        raise RuntimeError("none of the fixed snapshot SCPI ports 5175/5275/5375 is free")
    manager = SessionManager(ROOT / "tests/data/tui", scpi_base_port=port, hislip_port=4891)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(
        create_app(manager, web_dist=Path("/nonexistent")),
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)

    async def open_session():
        return await manager.start(DEMO.name, seed=7, clock="command")

    import asyncio

    served = asyncio.run(open_session())
    session = served.session
    # The six commands of the mock in tui-screens.md 4, in order, from their clients.
    session.exchange("awg", b"C1:OUTP ON", client="ui", reads=1)
    session.exchange("scope", b":FOO", client="ui", reads=1)
    session.exchange("smu", b":SOUR:VOLT 15", client="pyvisa", reads=1)
    session.exchange("smu", b":SOUR:VOLT 999", client="pyvisa", reads=1)
    session.exchange("smu", b":MEAS:CURR?", client="pyvisa", reads=1)
    session.exchange("dmm", b":MEAS:VOLT:DC?", client="mcp", reads=1)
    # A running scope redraws its live frame with fresh noise on every poll (D32), so the SVGs
    # could never match; stopped, it shows its last acquisition, which is deterministic.
    manager.panel(served, "scope", "stop", client="ui")
    yield f"http://127.0.0.1:{port}", served.id
    server.should_exit = True
    thread.join(timeout=10)


@pytest.mark.parametrize(
    ("lang", "size", "name"),
    [
        ("zh", (100, 32), "overview-zh-100x32"),
        ("en", (100, 32), "overview-en-100x32"),
        ("zh", (80, 24), "overview-zh-80x24"),
        ("en", (80, 24), "overview-en-80x24"),
    ],
)
def test_overview_snapshot(snap_compare, demo_server, lang, size, name):
    url, sid = demo_server
    tui = SimuBenchApp(url, sid=sid, lang=lang)

    async def run_before(pilot) -> None:
        import asyncio

        end = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < end:
            await pilot.pause(0.05)
            if tui.conn != "connected" or not tui.session_info:
                continue
            version, stable = tui.data_version, 0
            while asyncio.get_running_loop().time() < end:
                await pilot.pause(0.05)
                if tui.data_version == version:
                    stable += 1
                    if stable >= 8:
                        break
                else:
                    stable, version = 0, tui.data_version
            strips = tui.screen._compositor.render_strips()
            flat_text = "\n".join("".join(seg.text for seg in strip) for strip in strips)
            if "measured" in flat_text or "9.9" in flat_text:  # the smu card's readings rendered
                return
        raise AssertionError("the overview never settled")

    assert snap_compare(tui, terminal_size=size, run_before=run_before)


async def _settle(pilot, tui, deadline_s: float = 30.0) -> str:
    """Wait for the connection, a quiet data version and a stable render; return the screen text."""
    import asyncio

    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_s
    while loop.time() < end:
        await pilot.pause(0.05)
        if tui.conn != "connected" or not tui.session_info:
            continue
        version, stable = tui.data_version, 0
        while loop.time() < end:
            await pilot.pause(0.05)
            if tui.data_version == version:
                stable += 1
                if stable >= 8:
                    break
            else:
                stable, version = 0, tui.data_version
        text = "\n".join(
            "".join(seg.text for seg in strip) for strip in tui.screen._compositor.render_strips()
        )
        if text.strip():
            return text
    raise AssertionError("the screen never settled")


@pytest.mark.parametrize(
    ("lang", "size", "name"),
    [
        ("zh", (100, 32), "instrument-smu-zh-100x32"),
        ("en", (100, 32), "instrument-smu-en-100x32"),
        ("zh", (80, 24), "instrument-smu-zh-80x24"),
        ("en", (80, 24), "instrument-smu-en-80x24"),
    ],
)
def test_instrument_smu_snapshot(snap_compare, demo_server, lang, size, name):
    """Two keystrokes from the overview (14): the digit 3, then Enter (tui-screens.md 5)."""
    url, sid = demo_server
    tui = SimuBenchApp(url, sid=sid, lang=lang)

    async def run_before(pilot) -> None:
        import asyncio

        await _settle(pilot, tui)
        await pilot.press("3")
        await pilot.pause(0.2)
        await pilot.press("enter")
        end = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < end:
            await pilot.pause(0.1)
            text = "\n".join(
                "".join(seg.text for seg in strip) for strip in tui.screen._compositor.render_strips()
            )
            # The identity box of the §5 mock (at 80 cols the narrow columns truncate CC).
            if "SB-SMU-1" in text and "-222" in text:
                return
        raise AssertionError("the smu screen never settled")

    assert snap_compare(tui, terminal_size=size, run_before=run_before)


@pytest.mark.parametrize(
    ("lang", "size", "name"),
    [
        ("zh", (100, 32), "scope-zh-100x32"),
        ("en", (100, 32), "scope-en-100x32"),
        ("zh", (80, 24), "scope-zh-80x24"),
        ("en", (80, 24), "scope-en-80x24"),
    ],
)
def test_scope_snapshot(snap_compare, demo_server, lang, size, name):
    """Enter on the scope card (14): braille trace, settings column, log (tui-screens.md 6)."""
    url, sid = demo_server
    tui = SimuBenchApp(url, sid=sid, lang=lang)

    async def run_before(pilot) -> None:
        import asyncio

        await _settle(pilot, tui)
        await pilot.press("2")
        await pilot.pause(0.2)
        await pilot.press("enter")
        end = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < end:
            await pilot.pause(0.1)
            text = "\n".join(
                "".join(seg.text for seg in strip) for strip in tui.screen._compositor.render_strips()
            )
            # The settings column (ch1's scale) of the §6 mock.
            if "50.0 mV" in text:
                return
        raise AssertionError("the scope screen never settled")

    assert snap_compare(tui, terminal_size=size, run_before=run_before)
