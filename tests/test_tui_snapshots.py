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
    """A real uvicorn server with the demo bench session and the section 4 log already written."""
    import socket

    import uvicorn

    manager = SessionManager(ROOT / "tests/data/tui", scpi_base_port=0, hislip_port=0)
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
