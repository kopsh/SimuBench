"""The T2 scenarios that need a real server (tui-screens.md 14): few on purpose, slower.

The unit tests in ``test_tui_t2.py`` cover the screens against mocks; these three exercise what
only the real stack can: the trip a fault fires being read away by a script over a socket (找到
跳闸), the remote state a port client creates, and the server-side ``/screen`` contract. The bed
is the same as tests/test_tui.py's (a real uvicorn server in a thread), per tui-screens.md 14.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from simubench.tui.app import SimuBenchApp
from test_tui import Served, flat, loop_time, wait_connected, wait_quiet

pytestmark = (
    pytest.mark.tui_boot
)  # boots a Textual app or a real server: runs on main, not in the fast checks

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"


@pytest.fixture(scope="module")
async def served_validation() -> Served:
    s = Served(VALIDATION)
    yield s  # type: ignore[misc]
    await s.stop()


async def open_instrument(pilot, tui: SimuBenchApp, name: str, timeout: float = 20.0) -> None:
    """Two keystrokes from the overview: the digit key, then Enter (tui-screens.md 2.3)."""
    from simubench.tui.screens.instrument import InstrumentScreen
    from simubench.tui.screens.overview import Card, OverviewScreen
    from simubench.tui.screens.scope import ScopeScreen

    session = tui.session_info or {}
    names = [i["name"] for i in session.get("instruments", [])]
    # The digit focuses the card; a rebuild can drop that focus under load, so press again
    # until it sticks (the same robustness the unit tests' focus_card has).
    digit = str(names.index(name) + 1)
    end = loop_time() + timeout
    while loop_time() < end:
        await pilot.press(digit)
        await pilot.pause(0.05)
        focused = tui.screen.focused if isinstance(tui.screen, OverviewScreen) else None
        if isinstance(focused, Card) and focused.card.name == name:
            break
    else:
        raise AssertionError(f"the digit never focused {name}'s card")
    await pilot.press("enter")
    end = loop_time() + timeout
    while loop_time() < end:
        await pilot.pause(0.05)
        screen = tui.screen
        if isinstance(screen, InstrumentScreen | ScopeScreen) and screen.instrument_name == name:
            return
    raise AssertionError(f"{name}'s screen never opened (on {type(tui.screen).__name__})")


async def state_of(tui: SimuBenchApp, name: str, path: str) -> object:
    info = await tui.client.instrument(tui.sid, name)
    return next(sv for sv in info["state"] if sv["path"] == path)["value"]


async def wait_for(pilot, predicate, timeout: float = 20.0, message: str = "condition") -> None:
    """Poll ``predicate`` (sync or async) until it is true."""
    import inspect

    end = loop_time() + timeout
    while loop_time() < end:
        await pilot.pause(0.1)
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
    raise AssertionError(f"{message} never became true")


async def wait_flat(pilot, tui, *needles: str, timeout: float = 20.0) -> str:
    """The screen text captured when all ``needles`` first show at once.

    The compositor's strips are read live: a render can be brief (a refresh recomposing) or
    partial (one box updated, the next not yet), so reading once after a fixed pause races the
    screen. Poll until one render shows every needle, and assert the rest against that render.
    """
    end = loop_time() + timeout
    while loop_time() < end:
        await pilot.pause(0.1)
        text = flat(tui)
        if all(needle in text for needle in needles):
            return text
    missing = [needle for needle in needles if needle not in flat(tui)]
    raise AssertionError(f"{missing} never appeared on {type(tui.screen).__name__}")


# ---- 复现“找到跳闸”（tui-screens.md 14, the reference video's scenario) -------------------------------


async def test_find_the_trip(served_validation):
    """A preset trip: two keystrokes to the screen; the TUI itself names fault, source and command;
    an external :SYSTem:ERRor? zeroes the count live while the history keeps the entry."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid)
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        # The fault is injected through the API exactly as a preset fault behaves (deterministic;
        # the fault is the cause). Its source is 终端 because the request carries the tui header.
        async with httpx.AsyncClient(
            base_url=served_validation.url, headers={"X-SimuBench-Client": "tui"}
        ) as http:
            r = await http.post(
                f"/api/sessions/{sid}/faults",
                json={"kind": "trip", "instrument": "smu", "code": -300, "message": "Overvoltage"},
            )
            assert r.status_code == 200
            fault_id = r.json()["id"]

        # Two keystrokes: the digit selects the card, Enter opens the instrument screen.
        await open_instrument(pilot, tui, "smu")
        # The TUI updates live as the fault's events arrive — the reason line comes with the
        # fault event, the queued error with the next instrument refetch; wait for both.
        await wait_for(
            pilot, lambda: f"Reason  fault {fault_id} tripped (Terminal)" in flat(tui), message="reason line"
        )
        # The identity box and the error box carry the queued error (at 100 cols the message is
        # truncated with … per 4.2; the full text is one Enter away, asserted below).
        text = await wait_flat(pilot, tui, "queue 1", "-300")
        # and the fault id shows in the identity box (8).
        assert f"Fault {fault_id} active" in text

        # ``?`` opens the causal chain without any test-provided text.
        await pilot.press("w")
        text = await wait_flat(pilot, tui, f"fault {fault_id} tripped (Terminal)", "Overvoltage")
        assert "←" in text and f"fault {fault_id} (Terminal)" in text
        await pilot.press("escape")
        await pilot.pause(0.3)

        # A script reads the error queue: the count drops to 0 live; the history keeps the
        # entry greyed with the fault as its origin.
        port = (await tui.client.instrument(sid, "smu"))["scpi_port"]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b":SYSTem:ERRor?\n")
        await writer.drain()
        reply = await asyncio.wait_for(reader.readline(), timeout=10)
        assert b"-300" in reply
        writer.close()
        await wait_for(pilot, lambda: "queue 0" in flat(tui), message="queue zeroed live")
        # The queue count refetches with the instrument, the log rows rebuild from the events —
        # two paths, so wait for the read command's line instead of asserting on a snapshot.
        text = await wait_flat(pilot, tui, "SYSTem:ERRor?")
        assert "read away" in text and "-300" in text


# ---- remote state and local takeover over a real port (tui-screens.md 12) -----------------------------


async def test_remote_makes_controls_readonly_and_l_takes_over(served_validation):
    """A socket client makes the screen read-only; L (capital) takes over until its next command."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid)
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        # A script occupies the instrument over its socket port.
        port = (await tui.client.instrument(sid, "smu"))["scpi_port"]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"*IDN?\n")
        await writer.drain()
        await asyncio.wait_for(reader.readline(), timeout=10)
        await open_instrument(pilot, tui, "smu")
        from simubench.tui.screens.instrument import ControlButton

        out = next(b for b in tui.screen.query(ControlButton) if b.control.path == "output.enabled")
        assert out.has_class("-readonly")
        out.focus()
        await pilot.pause(0.2)
        await pilot.press("space")
        await pilot.pause(0.5)
        assert "is remote-controlled by" in flat(tui)
        assert await state_of(tui, "smu", "output.enabled") is True

        # L (capital, 5.5): confirm, then the instrument goes local.
        await pilot.press("L")
        await pilot.pause(0.4)
        from simubench.tui.screens.states import ConfirmScreen

        assert isinstance(tui.screen, ConfirmScreen)
        await pilot.press("y")

        async def is_local() -> bool:
            return (await tui.client.instrument(sid, "smu")).get("local") is True

        await wait_for(pilot, is_local, message="local state")
        await pilot.pause(0.3)
        assert not out.has_class("-readonly")  # local again: the controls are writable
        writer.close()


# ---- the server-side /screen contract (tui-screens.md 6.1) --------------------------------------------


async def test_screen_points_contract(served_validation):
    """`points` returns per-bin min/max and no samples; without it the response is unchanged."""
    await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    # The validation smu bench has no scope; the examples workspace has one.
    served_examples = Served(ROOT / "examples/benches")
    try:
        served_session = await served_examples.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
        sid = served_session.id
        with served_examples.http() as http:
            plain = http.get(f"/api/sessions/{sid}/instruments/scope/screen").json()
            binned = http.get(f"/api/sessions/{sid}/instruments/scope/screen?points=140").json()
            rejected = http.get(f"/api/sessions/{sid}/instruments/scope/screen?points=1")
        assert plain["traces"][0]["volts"] and plain["traces"][0]["minmax"] is None
        assert binned["traces"][0]["volts"] == []
        minmax = binned["traces"][0]["minmax"]
        assert len(minmax) == 280 and all(minmax[i] <= minmax[i + 1] for i in range(0, 280, 2))
        assert minmax[0] <= minmax[1] and binned["traces"][0]["dt_s"] > 0
        assert rejected.status_code == 422
    finally:
        await served_examples.stop()
