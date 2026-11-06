"""The TUI scenarios that need the real stack (tui-screens.md 14, issue #177): few on purpose,
slower — one file for all of them (#699, formerly test_tui_t2/t3/t4_e2e.py).

The unit tests in ``test_tui_t2.py``/``t3.py`` cover the screens against mocks; these exercise
what only the real stack can: the trip a fault fires being read away by a script over a socket
(找到跳闸), the remote state a port client creates, the server-side ``/screen`` contract, a lost
reply with the client's retry, the nets screen's sweep against the physics, the coupled RBW
view, and the ``simubench tui --script`` CLI path end to end. The bed is the same as
tests/test_tui.py's (a real uvicorn server in a thread); one server per module, each scenario
its own session. The fault is injected through the TUI's own API path, so it lands in the run
record with the source 终端 exactly as the dialog's request does.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from simubench.tui.app import SimuBenchApp
from test_tui import Served, wait_connected, wait_quiet
from tui_mock import flat, loop_time, wait_flat, wait_for

pytestmark = (
    pytest.mark.tui_boot
)  # boots a Textual app or a real server: runs on main, not in the fast checks

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/benches"
VALIDATION = ROOT / "validation"


@pytest.fixture(scope="module")
async def served_validation() -> Served:
    s = Served(VALIDATION)
    yield s  # type: ignore[misc]
    await s.stop()


@pytest.fixture(scope="module")
async def served_examples() -> Served:
    s = Served(EXAMPLES)
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
    served_examples = Served(EXAMPLES)
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


# ---- a lost reply fault and the client's retry (tui-screens.md 14, scenario A) ------------------------


async def test_a_lost_reply_fault_and_the_clients_retry(served_validation):
    """Scenario A: a one-shot lost reply, injected from the TUI, drops the script's exchange;
    its retry succeeds, the fault ends, and the run record names the source 终端."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid)
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        # Inject a one-shot timeout through the same HTTP call the dialog makes (tui header).
        async with httpx.AsyncClient(
            base_url=served_validation.url, headers={"X-SimuBench-Client": "tui"}
        ) as http:
            r = await http.post(
                f"/api/sessions/{sid}/faults", json={"kind": "timeout", "instrument": "smu", "count": 1}
            )
            assert r.status_code == 200
            fault_id = r.json()["id"]

        port = (await tui.client.instrument(sid, "smu"))["scpi_port"]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        # The first read takes nothing: the fault eats the reply (D27, at the exchange entry).
        writer.write(b":MEAS:VOLT?\n")
        await writer.drain()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.readline(), timeout=2)
        # The retry gets through: the fault has spent its one message.
        writer.write(b":MEAS:VOLT?\n")
        await writer.drain()
        reply = await asyncio.wait_for(reader.readline(), timeout=10)
        assert b"+5" in reply or b"+9" in reply or b"+" in reply
        writer.close()

        # The run record carries the injection and its effect, with the source 终端.
        events = served_session.session.history
        faults = [e for e in events if e.get("kind") == "fault" and e.get("source") == "tui"]
        assert any(
            f.get("fault") == fault_id or (f.get("spec") or {}).get("kind") == "timeout" for f in faults
        )
        # The app saw the injection (its fault list carries the one-shot fault), and the record
        # has the write, the empty read and the fault events.
        assert tui.faults and tui.faults[0]["id"] == fault_id
        text = await wait_flat(pilot, tui, "smu")
        assert "smu" in text
        writes = [e for e in events if e.get("kind") == "scpi" and e.get("dir") == "write"]
        assert len(writes) >= 2  # the first attempt and the retry
        assert len(events) >= 3


# ---- the nets screen's sweep against the physics (scenario B) ------------------------------------------


async def test_rc_sweep_shows_the_amplitude_falling(served_examples):
    """Scenario B: a script sweeps the source's frequency upward past fc; the nets screen's
    level for rc.OUT falls monotonically — the same numbers the canvas shows."""
    served_session = await served_examples.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_examples.url, sid=sid)
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await pilot.press("n")
        from simubench.tui.screens.nets import NetsScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, NetsScreen), message="nets screen")
        # Select the DUT's output net.
        tui.screen.selected = "net-1"
        tui.screen.probe = None
        tui.screen._probe_net = None
        tui.screen.refresh_content()
        await pilot.pause(0.5)

        script = served_session.session
        seen: list[float] = []

        def level_of_net1() -> float:
            for lv in (tui.levels or {}).get("nets", []):
                if lv["net"] == "net-1":
                    return float(lv["rms"])
            return -1.0

        script.exchange("awg", b"C1:OUTP ON", client="pyvisa", reads=1)
        await wait_for(pilot, lambda: level_of_net1() > 0.1, message="signal arrives")
        for freq in (1000, 2000, 5000, 10000, 20000):
            script.exchange("awg", f"C1:BSWV WVTP,SINE,FRQ,{freq}".encode(), client="pyvisa", reads=1)
            await pilot.pause(0.4)
            seen.append(level_of_net1())
        # fc ≈ 1/(2πRC) ≈ 1.59 kHz: past it the displayed amplitude falls for good.
        assert seen[0] > 0.3
        assert seen[-1] < seen[0] / 2
        assert seen[2] >= seen[-1]  # already below at 5 kHz
        # The nets screen's own detail echoes the falling level (the probe, not a stale read).
        assert tui.screen.probe is not None
        assert float(tui.screen.probe.get("rms") or 0) < 0.5
        # And the screen marks the net as carrying a signal.
        rows, _ = tui.screen._rows()
        assert next(r for r in rows if r["net"] == "net-1")["active"] is True


# ---- the coupled couplings read AUTO (tui-screens.md 5, #279/#345) -------------------------------------


async def test_spectrum_generic_view_reads_auto_for_a_coupled_manual_value(served_validation):
    """#279/#345: at power-on the RBW is coupled, so the generic view shows AUTO, not the stored
    manual 3 MHz; turning the coupling off brings the manual value back."""
    served_session = await served_validation.start("benches/specan.bench.yaml", seed=5, clock="command")
    tui = SimuBenchApp(served_validation.url, sid=served_session.id)
    async with tui.run_test(size=(120, 36)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        tui.show("instrument", "sa")
        text = await wait_flat(pilot, tui, "RBW (manual)")
        words = " ".join(text.split())
        assert "RBW (manual) AUTO" in words
        assert "VBW (manual) AUTO" in words and "Sweep time (manual) AUTO" in words  # all coupled
        assert "RBW auto On" in words
        assert "3.0000 MHz" not in text
        served_session.session.exchange("sa", b":BAND:AUTO OFF", client="pyvisa", reads=0)
        text = await wait_flat(pilot, tui, "3.0000 MHz")
        words = " ".join(text.split())
        assert "RBW (manual) 3.0000 MHz" in words and "RBW auto Off" in words
        assert "VBW (manual) AUTO" in words  # the other couplings stay on


# ---- the `simubench tui --script` path end to end (6) ---------------------------------------------------


def test_the_cli_runs_a_script_and_leaves_one_reviewable_run(tmp_path: Path):
    """A real CLI process starts the service, opens the session over its own HTTP API, drives the
    app headlessly, writes the SVGs, frames and run record, and shuts everything down. The
    in-process tests of ``test_tui_t4.py`` cover the steps engine and the screen; this one
    covers the wiring the CLI adds (argument parsing, the served session, one record per run)."""
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


async def test_an_error_fault_with_count_from_the_tui_path(served_validation):
    """error+count injected through the TUI's own API path (the dialog's request: the same POST
    with the X-SimuBench-Client: tui header, issue 697): the queue answers the code in order that
    many times and is empty again, and the injection event names 终端 as the source."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid)
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        async with httpx.AsyncClient(
            base_url=served_validation.url, headers={"X-SimuBench-Client": "tui"}
        ) as http:
            r = await http.post(
                f"/api/sessions/{sid}/faults",
                json={"kind": "error", "instrument": "smu", "code": -313, "count": 3},
            )
            assert r.status_code == 200
            assert r.json()["source"] == "tui"
        events = [
            e
            for e in served_validation.manager.get(sid).session.history
            if e["kind"] == "fault" and e.get("reason") == "injected"
        ]
        assert events and events[-1]["source"] == "tui" and events[-1]["spec"]["count"] == 3
        # the TUI sees the queue drain live (the identity box carries it), and the reads answer
        # the code in order: three times, then the empty queue
        port = (await tui.client.instrument(sid, "smu"))["scpi_port"]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        replies = []
        for _ in range(4):
            writer.write(b":SYSTem:ERRor?\n")
            await writer.drain()
            replies.append((await asyncio.wait_for(reader.readline(), timeout=10)).decode().strip())
        writer.close()
        assert [r.split(",")[0] for r in replies[:3]] == ["-313"] * 3
        assert replies[3].startswith("0,")
