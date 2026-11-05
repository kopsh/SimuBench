"""The TUI's tests that boot (issue 175): the server-side client header, and the app driven by
Textual's Pilot against a REAL uvicorn server in a background thread (tui-screens.md 14).

The pure-logic tier moved to ``test_tui_units.py`` and the real-stack scenarios to
``test_tui_e2e.py`` (#699); what stays here needs the server bed or the running app. The
interface-level helpers this file owns (``Served``, ``wait_connected``, ``wait_quiet``) are
imported by the e2e file; the screen-reading helpers live in ``tui_mock``.

Windows-safe: sockets and threads only, no signals or fork.
"""

from __future__ import annotations

import asyncio
import re
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from simubench.server.app import create_app
from simubench.server.manager import SessionManager
from simubench.tui.app import SimuBenchApp
from tui_mock import flat, loop_time

pytestmark = (
    pytest.mark.tui_boot
)  # boots a Textual app or a real server: runs on main, not in the fast checks

TEXT = "The terminal console needs the optional dependency: pip install 'simubench[tui]'"

ROOT = Path(__file__).resolve().parents[1]


# ---- server side: the X-SimuBench-Client header (tui-screens.md 1.6) -------------------------------


@pytest.fixture
def served_api() -> Iterator[tuple[TestClient, dict, SessionManager]]:
    manager = SessionManager(ROOT / "validation", scpi_base_port=0, hislip_port=0)
    app = create_app(manager, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        info = c.post("/api/sessions", json={"bench_path": "benches/smu.bench.yaml"}).json()
        yield c, info, manager
        c.delete(f"/api/sessions/{info['id']}", params={"force": True})


def test_client_header_defaults_to_ui(served_api):
    """Without the header the source is ui, as before; the tui header marks 终端 (D18)."""
    c, info, manager = served_api
    sid = info["id"]
    r = c.post(f"/api/sessions/{sid}/instruments/smu/scpi", json={"command": ":SYST:ERR?"})
    assert r.status_code == 200
    r = c.post(
        f"/api/sessions/{sid}/instruments/smu/scpi",
        json={"command": "*IDN?"},
        headers={"X-SimuBench-Client": "tui"},
    )
    assert r.status_code == 200
    clients = [e.get("client") for e in manager.get(sid).session.history if e["kind"] == "scpi"]
    assert "ui" in clients and "tui" in clients


def test_client_header_rejects_unknown_values(served_api):
    c, info, _ = served_api
    sid = info["id"]
    r = c.post(
        f"/api/sessions/{sid}/instruments/smu/scpi",
        json={"command": "*IDN?"},
        headers={"X-SimuBench-Client": "agent"},
    )
    assert r.status_code == 400
    assert "X-SimuBench-Client" in r.text


def test_configure_panel_and_faults_carry_the_client(served_api):
    c, info, _ = served_api
    sid = info["id"]
    r = c.post(
        f"/api/sessions/{sid}/instruments/smu/configure",
        json={"path": "source.voltage.level", "value": 3.0},
        headers={"X-SimuBench-Client": "tui"},
    )
    assert r.status_code == 200 and r.json()["ok"]
    r = c.post(
        f"/api/sessions/{sid}/instruments/smu/panel",
        json={"action": "stop"},
        headers={"X-SimuBench-Client": "tui"},
    )
    assert r.status_code == 200
    # Fault injection records its source as the calling client, not a hardcoded ui (app.py).
    r = c.post(
        f"/api/sessions/{sid}/faults",
        json={"kind": "trip", "instrument": "smu", "code": -300, "message": "Overvoltage"},
        headers={"X-SimuBench-Client": "tui"},
    )
    assert r.status_code == 200 and r.json()["source"] == "tui"


# ---- the app against a real server -------------------------------------------------------------------


class Served:
    """A real uvicorn server in a background thread, plus bench sessions (issue 175's test bed)."""

    def __init__(self, workspace: Path) -> None:
        import uvicorn

        self.manager = SessionManager(workspace, scpi_base_port=0, hislip_port=0)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.app = create_app(self.manager, web_dist=Path("/nonexistent"))
        self.url = f"http://127.0.0.1:{self.port}"
        self.config = uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_config=None, access_log=False
        )
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)

    async def start(self, bench: str, **kw):
        return await self.manager.start(bench, **kw)

    async def two_sessions(self) -> None:
        """Two sessions listed at once: start a second, then put the first back into the dict.

        (One bench owns the SCPI ports; the TUI's picker only reads GET /api/sessions.)
        """
        first = next(iter(self.manager.sessions))
        saved = {first: self.manager.sessions.pop(first)}
        await self.manager.start("siglent_scope_rc.bench.yaml", seed=8, clock="wall", sid="second-session")
        self.manager.sessions.update(saved)

    async def restart(self) -> None:
        """Stop and start uvicorn again on the same port (for the disconnect test)."""
        await self.stop()
        import uvicorn

        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)

    async def stop(self) -> None:
        await stop_uvicorn(self.server, self.thread)

    def http(self) -> httpx.Client:
        return httpx.Client(base_url=self.url, headers={"X-SimuBench-Client": "tui"}, timeout=10)


async def stop_uvicorn(server, thread: threading.Thread) -> None:
    """Shut uvicorn down without blocking the caller's loop.

    The graceful shutdown completes through the websocket clients this very loop serves: a
    blocking ``thread.join`` would freeze the loop, the close handshake could not be answered
    and every stop would burn its whole 10 s timeout. Yield instead; force-exit as a fallback.
    """
    server.should_exit = True
    deadline = time.monotonic() + 10
    while thread.is_alive() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    if thread.is_alive():
        server.force_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="module")
async def served() -> AsyncIterator[Served]:
    s = Served(ROOT / "examples/benches")
    yield s
    await s.stop()


async def wait_connected(pilot, tui: SimuBenchApp, deadline: float = 20.0) -> None:
    end = loop_time() + deadline
    while loop_time() < end:
        await pilot.pause(0.05)
        if tui.conn == "connected" and tui.session_info:
            return
    raise AssertionError(f"never connected (conn={tui.conn})")


async def wait_quiet(pilot, tui: SimuBenchApp, deadline: float = 8.0) -> None:
    """Wait until the data version stops changing (fetches done, screens rendered)."""
    version, stable = tui.data_version, 0
    end = loop_time() + deadline
    while loop_time() < end:
        await pilot.pause(0.05)
        if tui.data_version == version:
            stable += 1
            if stable >= 10:
                return
        else:
            stable, version = 0, tui.data_version
    raise AssertionError("the app never settled")


async def test_overview_renders_cards_from_the_real_session(served):
    served_session = await served.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    tui = SimuBenchApp(served.url, sid=served_session.id)
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        from simubench.tui.screens.overview import Card

        cards = list(tui.screen.query(Card))
        assert [c.card.name for c in cards] == ["awg", "scope", "rc"]
        assert cards[0].card.number == 1 and cards[2].card.number == 3
        text = flat(tui)
        assert "1 ○ awg · SDG2042X" in text and "3 rc · RC 低通 · DUT" in text  # ○: idle (4.2)
        assert "SDG2042X" in text and "SDS2104X Plus" in text
        # The top bar carries the D44 activity, the clock mode and the virtual time.
        assert "● waiting for commands · Command-driven clock" in text
        # The bottom bar shows the keys and the remote status.
        assert "1-9 select" in text and "remote: None" in text
        # Digit keys move the focus.
        await pilot.press("2")
        await pilot.pause()
        assert tui.focused is not None and tui.focused.card.name == "scope"


async def test_socket_client_updates_cards_and_log_with_script_source(served):
    """A script on the raw socket drives SCPI while the TUI runs: the log shows `脚本 :<port>`."""
    served_session = await served.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served.url, sid=sid)
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)

        port = served.http().get(f"/api/sessions/{sid}/instruments/scope").json()["scpi_port"]
        # An async socket client: the SCPI servers share this loop, so a blocking recv would starve them.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b":CHAN1:SCAL 0.2;:CHAN1:SCAL?\n")
        await writer.drain()
        buf = await asyncio.wait_for(reader.readline(), timeout=10)
        writer.close()
        assert b"2.00E-01" in buf

        end = loop_time() + 8
        while loop_time() < end:
            await pilot.pause(0.1)
            if any(":CHAN1:SCAL" in line.command for line in tui.lines):
                break
        else:
            raise AssertionError("the script's command never appeared in the log")
        line = next(ln for ln in tui.lines if ":CHAN1:SCAL" in ln.command)
        assert re.fullmatch(r"Script :\d+", line.source), line.source
        assert line.result_text().startswith("→")
        # The card reflects the new scale (the startup setting was 50.0 mV/div, now 200 mV/div).
        from simubench.tui.screens.overview import Card

        end = loop_time() + 8
        body = ""
        while loop_time() < end:
            await pilot.pause(0.1)
            scope_card = next(c for c in tui.screen.query(Card) if c.card.name == "scope")
            body = "".join(v for ln in scope_card.card.lines for v, _ in ln)
            if "200.0 mV/div" in body:
                break
        assert "200.0 mV/div" in body, body


async def test_disconnect_readonly_and_backfill_without_dupes(served):
    """Server down: the top bar shows the red read-only state; back up: backfill, no duplicates."""
    served_session = await served.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served.url, sid=sid)
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        before = len(tui.store.events)

        await served.stop()  # the engine lives on in this process; only the service drops
        end = loop_time() + 15
        while loop_time() < end and tui.conn != "disconnected":
            await pilot.pause(0.1)
        assert tui.conn == "disconnected"
        top = "".join(v for v, _ in tui.status_segments())
        assert "disconnected" in top and "read-only" in top and "reconnecting" in top

        # Events emitted while the service is down land in the history (the engine is in-process).
        served.manager.get(sid).session.exchange("awg", b"*IDN?", client="pyvisa", reads=1)

        await served.restart()
        end = loop_time() + 20
        while loop_time() < end:
            await pilot.pause(0.1)
            if tui.conn == "connected" and any(ln.command == "*IDN?" for ln in tui.lines):
                break
        assert tui.conn == "connected"
        seqs = [e["seq"] for e in tui.store.events]
        assert len(seqs) == len(set(seqs)), "the backfill duplicated events"
        assert len(tui.store.events) > before


async def test_fullscreen_log_filter_and_errors_only(served):
    served_session = await served.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    session = served_session.session
    session.exchange("scope", b":FOO", client="pyvisa", reads=1)  # an error
    session.exchange("awg", b"*IDN?", client="pyvisa", reads=1)
    tui = SimuBenchApp(served.url, sid=served_session.id)
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await pilot.press("l")
        await pilot.pause(0.2)
        assert tui.screen.screen_key == "log"
        text = flat(tui)
        assert "Virtual time" in text and "Reply / result" in text
        assert "✗ -113" in text and "→ Siglent" in text

        await pilot.press("e")  # errors only
        await pilot.pause(0.2)
        text = flat(tui)
        assert "✗ -113" in text and "→ Siglent" not in text

        await pilot.press("e")
        await pilot.press("slash")  # / opens the filter input
        await pilot.pause(0.2)
        await pilot.press("a", "w", "g", "enter")
        await pilot.pause(0.2)
        text = flat(tui)
        assert "→ Siglent" in text and "✗ -113" not in text  # the scope's line filtered out

        await pilot.press("escape")  # Esc clears the filter first
        await pilot.pause(0.2)
        assert "✗ -113" in flat(tui)
        await pilot.press("escape")  # then returns to the overview
        await pilot.pause(0.2)
        assert tui.screen.screen_key == "overview"


async def test_attach_token_invalid_screen(served):
    """401: the token message shows, no retry (tui-screens.md 3)."""
    import uvicorn

    manager = served.manager
    token_app = create_app(manager, token="secret", web_dist=Path("/nonexistent"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(token_app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    try:
        tui = SimuBenchApp(f"http://127.0.0.1:{port}", token="wrong")
        async with tui.run_test(size=(100, 32)) as pilot:
            from simubench.tui.screens.states import MessageScreen

            end = loop_time() + 10
            while loop_time() < end and not isinstance(tui.screen, MessageScreen):
                await pilot.pause(0.1)
            assert isinstance(tui.screen, MessageScreen)
            assert "Invalid access token" in flat(tui)
    finally:
        await stop_uvicorn(server, thread)


async def test_attach_session_picker(served):
    """Two sessions: the picker lists them with the mock's columns; Enter connects."""
    await served.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    await served.two_sessions()
    tui = SimuBenchApp(served.url)
    async with tui.run_test(size=(100, 32)) as pilot:
        end = loop_time() + 10
        while loop_time() < end:
            await pilot.pause(0.1)
            from simubench.tui.screens.states import PickerScreen

            if isinstance(tui.screen, PickerScreen):
                break
        from simubench.tui.screens.states import PickerScreen

        assert isinstance(tui.screen, PickerScreen)
        end = loop_time() + 8
        while loop_time() < end and not tui.screen._rows:
            await pilot.pause(0.1)
        text = flat(tui)
        for column in ("Session", "Bench", "Clock", "Virtual time", "Clients"):
            assert column in text, column
        assert "siglent_scope_rc" in text
        await pilot.press("enter")
        await wait_connected(pilot, tui)
        assert tui.sid == "second-session"


async def test_min_size_guard(served):
    """Below 80×24 the whole screen shows the warning line (tui-screens.md 1.1)."""
    served_session = await served.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    tui = SimuBenchApp(served.url, sid=served_session.id)
    async with tui.run_test(size=(70, 20)) as pilot:
        end = loop_time() + 10
        while loop_time() < end and "Window too small" not in flat(tui):
            await pilot.pause(0.1)
        text = flat(tui)
        assert "Window too small: at least 80 × 24, now 70 × 20" in text
        assert "awg" not in text
    # At exactly the minimum the content shows.
    tui2 = SimuBenchApp(served.url, sid=served_session.id)
    async with tui2.run_test(size=(80, 24)) as pilot:
        await wait_connected(pilot, tui2)
        end = loop_time() + 10
        while loop_time() < end and "Window too small" in flat(tui2):
            await pilot.pause(0.1)
        end = loop_time() + 10
        while loop_time() < end and "awg" not in flat(tui2):
            await pilot.pause(0.1)
        assert "Window too small" not in flat(tui2)
        assert "awg" in flat(tui2)


async def test_file_changed_shows_hint_and_r_rebuilds(served, tmp_path):
    """D25: the launched bench file is hashed; a change lights the hint, R rebuilds after confirm."""
    bench = tmp_path / "demo.bench.yaml"
    bench.write_text((ROOT / "tests/data/tui/demo.bench.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    manager = SessionManager(tmp_path, scpi_base_port=0, hislip_port=0)
    import uvicorn

    from simubench.server.app import create_app

    with socket.socket() as sck:
        sck.bind(("127.0.0.1", 0))
        port = sck.getsockname()[1]
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
    while not server.started:
        time.sleep(0.02)
    try:
        served_session = await manager.start(bench.name, seed=7, clock="command")
        tui = SimuBenchApp(
            f"http://127.0.0.1:{port}",
            sid=served_session.id,
            launched=True,
            bench_path=bench,
        )
        async with tui.run_test(size=(100, 32)) as pilot:
            await wait_connected(pilot, tui)
            bench.write_text(
                bench.read_text(encoding="utf-8").replace("seed: 7", "seed: 9"), encoding="utf-8"
            )
            end = loop_time() + 8
            while loop_time() < end and not tui.file_changed:
                await pilot.pause(0.1)
            assert tui.file_changed
            end = loop_time() + 8
            while loop_time() < end and "file changed · R rebuild" not in flat(tui):
                await pilot.pause(0.1)
            assert "file changed · R rebuild" in flat(tui)
            sha_before = tui.session_info["bench_sha256"]
            await pilot.press("R")
            await pilot.pause(0.3)
            from simubench.tui.screens.states import ConfirmScreen

            assert isinstance(tui.screen, ConfirmScreen)
            await pilot.press("y")  # confirm the rebuild
            end = loop_time() + 10
            while loop_time() < end and tui.file_changed:
                await pilot.pause(0.1)
            assert not tui.file_changed
            # The bench was rebuilt from the new file: its hash changed.
            assert tui.session_info["bench_sha256"] not in (None, sha_before)
    finally:
        await stop_uvicorn(server, thread)


async def test_quit_with_clients_connected_confirms(served):
    """q with a script connected (launched service): the confirm dialog; cancel keeps running."""
    served_session = await served.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    sid = served_session.id
    port = served.http().get(f"/api/sessions/{sid}/instruments/scope").json()["scpi_port"]
    _reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await asyncio.sleep(0.3)  # let the session list the client
    tui = SimuBenchApp(served.url, sid=sid, launched=True)
    try:
        async with tui.run_test(size=(100, 32)) as pilot:
            await wait_connected(pilot, tui)
            await pilot.press("q")
            await pilot.pause(0.3)
            from simubench.tui.screens.states import ConfirmScreen

            assert isinstance(tui.screen, ConfirmScreen)
            assert "Quit" in flat(tui)
            await pilot.press("n")  # cancel: still running
            await pilot.pause(0.3)
            assert not tui._exit
    finally:
        writer.close()


async def test_no_color_boots_its_stylesheet(monkeypatch):
    """The mono theme's tokens are terminal-default colours, not empty strings (which fail
    Textual's stylesheet parse and take the whole TUI with them)."""
    monkeypatch.setenv("NO_COLOR", "1")
    tui = SimuBenchApp("http://127.0.0.1:1", sid="x")
    async with tui.run_test(size=(100, 32)) as pilot:
        await pilot.pause(0.3)
        assert tui.theme == "simubench-mono"
        assert type(tui.screen).__name__ == "OverviewScreen"


async def test_api_client_sends_the_tui_header(served):
    """Every TUI request carries X-SimuBench-Client: tui (the server records 终端 as the source)."""
    served_session = await served.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served.url, sid=sid)
    async with tui.run_test(size=(80, 24)):
        await tui.client.open()
        assert tui.client.http.headers["x-simubench-client"] == "tui"
        await tui.client.scpi(sid, "awg", "*IDN?")
        clients = [e.get("client") for e in served.manager.get(sid).session.history if e["kind"] == "scpi"]
        assert "tui" in clients
        await tui.client.close()


def test_tui_without_textual_exits_2(monkeypatch, capsys):
    """Without Textual installed, `simubench tui` names the install command and exits 2 (section 3)."""
    import importlib.util

    from simubench import cli

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "textual" else real(name))
    assert cli.main(["tui", "whatever.bench.yaml"]) == 2
    assert TEXT in capsys.readouterr().err
