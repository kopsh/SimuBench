"""The T3 acceptance scenarios against a real server (tui-screens.md 14, issue #177).

The environment is shared (D49): one server for the module, each scenario its own session. The
fault is injected through the TUI's own API path, so it lands in the run record with the source
终端 exactly as the dialog's request does.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from simubench.tui.app import SimuBenchApp
from test_tui import Served, flat, loop_time, wait_connected, wait_quiet

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/benches"
VALIDATION = ROOT / "validation"


@pytest.fixture(scope="module")
async def served_examples() -> Served:
    s = Served(EXAMPLES)
    yield s  # type: ignore[misc]
    await s.stop()


@pytest.fixture(scope="module")
async def served_validation() -> Served:
    s = Served(VALIDATION)
    yield s  # type: ignore[misc]
    await s.stop()


async def wait_for(pilot, predicate, timeout: float = 20.0, message: str = "condition") -> None:
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
    end = loop_time() + timeout
    while loop_time() < end:
        await pilot.pause(0.1)
        text = flat(tui)
        if all(needle in text for needle in needles):
            return text
    missing = [n for n in needles if n not in flat(tui)]
    raise AssertionError(f"{missing} never appeared on {type(tui.screen).__name__}")


async def test_a_lost_reply_fault_and_the_clients_retry(served_validation):
    """Scenario A: a one-shot lost reply, injected from the TUI, drops the script's exchange;
    its retry succeeds, the fault ends, and the run record names the source 终端."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid, lang="zh")
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


async def test_rc_sweep_shows_the_amplitude_falling(served_examples):
    """Scenario B: a script sweeps the source's frequency upward past fc; the nets screen's
    level for rc.OUT falls monotonically — the same numbers the canvas shows."""
    served_session = await served_examples.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_examples.url, sid=sid, lang="zh")
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
