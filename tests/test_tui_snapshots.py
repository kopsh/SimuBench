"""The overview's snapshot tests (tui-screens.md 14): zh/en at 100×32 and 80×24.

The session is deterministic: the demo bench (tests/data/tui/demo.bench.yaml, contents per
section 1.3) with a fixed seed, the command clock and idle off (D47), and the six commands of
the section 4 communication log executed before the TUI connects (they come with the backlog).
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from helpers import stop_manager_sessions, wait_for_free_ports
from simubench.server import drive
from simubench.server.app import create_app
from simubench.server.manager import SessionManager
from simubench.tui.app import SimuBenchApp

pytestmark = (
    pytest.mark.tui_boot
)  # boots a Textual app or a real server: runs on main, not in the fast checks

ROOT = Path(__file__).resolve().parents[1]

#: The ceiling of every wait for a screen to come up (s). The waits end on their readiness signals
#: (connected, a quiet data version, a stable render); this only bounds a hang, and a cold start
#: under load needs more than the 30 s it was (#857). Below the 120 s per-test timeout.
SETTLE_S = 90.0


@pytest.fixture(autouse=True)
def color_theme(monkeypatch):
    """Snapshots are the colour theme's: a developer's ``NO_COLOR`` must not change them."""
    monkeypatch.delenv("NO_COLOR", raising=False)


DEMO = ROOT / "tests/data/tui/demo.bench.yaml"
EXAMPLES_DIR = ROOT / "examples/benches"


@pytest.fixture(scope="module")
def demo_server():
    """A real uvicorn server with the demo bench session and the section 4 log already written.

    The SCPI and HiSLIP ports are fixed: the instrument screen shows the resource address
    (tui-screens.md 5.1), so the SVG is only reproducible when the ports are (5175–5178, 4891).
    """
    import socket

    import uvicorn

    port = 5175
    wait_for_free_ports([*range(port, port + 8), 4891])  # the module before may not have let go yet (#857)
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
    drive.panel(served, "scope", "stop", client="ui")
    yield f"http://127.0.0.1:{port}", served.id
    asyncio.run(stop_manager_sessions(manager))  # the sessions hold the HiSLIP port, not uvicorn (issue 686)
    server.should_exit = True
    thread.join(timeout=10)


@pytest.mark.parametrize(
    ("lang", "size", "name"),
    [
        ("en", (100, 32), "overview-en-100x32"),
        ("en", (80, 24), "overview-en-80x24"),
    ],
)
def test_overview_snapshot(snap_compare, demo_server, lang, size, name):
    url, sid = demo_server
    tui = SimuBenchApp(url, sid=sid)

    async def run_before(pilot) -> None:
        import asyncio

        # The capture pins the log to the fixture's own records; a maintenance pass would rebuild
        # the list from the whole store and undo the trim, so the log view is frozen (#850).
        original_maintenance = tui._maintenance

        async def frozen_maintenance() -> None:
            tui._dirty_lines = False  # the rebuild would un-pin the log
            await original_maintenance()

        end = asyncio.get_running_loop().time() + SETTLE_S
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
            # Everything the picture shows must have arrived: the dmm card's measured volts
            # (rendered at both sizes), the RC card's measured values where that card renders
            # (100x32 only), and the fixture's whole log (its last line is the panel stop).
            # Under load the app syncs these later, and a partial state once captured a
            # different picture (#850).
            if (
                "9.999000" in flat_text
                and ("100.000 nF" in flat_text or size == (80, 24))
                and tui.lines
                and tui.lines[-1].kind == "panel"
            ):
                # Pin the log to the fixture's own records (tui-screens.md 4): the demo service
                # may serve other tests' commands later. Trim after freezing the log view (above).
                tui._maintenance = frozen_maintenance
                del tui.lines[6:]
                tui.bump()
                await pilot.pause(0.5)
                return
        raise AssertionError("the overview never settled")

    assert snap_compare(tui, terminal_size=size, run_before=run_before)


async def _settle(pilot, tui, deadline_s: float = SETTLE_S) -> str:
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
        ("en", (100, 32), "instrument-smu-en-100x32"),
        ("en", (80, 24), "instrument-smu-en-80x24"),
    ],
)
def test_instrument_smu_snapshot(snap_compare, demo_server, lang, size, name):
    """Two keystrokes from the overview (14): the digit 3, then Enter (tui-screens.md 5)."""
    url, sid = demo_server
    tui = SimuBenchApp(url, sid=sid)

    async def run_before(pilot) -> None:
        import asyncio

        await _settle(pilot, tui)
        await pilot.press("3")
        await pilot.pause(0.2)
        await pilot.press("enter")
        end = asyncio.get_running_loop().time() + SETTLE_S
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
        ("en", (100, 32), "state-tree-en-100x32"),
        ("en", (80, 24), "state-tree-en-80x24"),
    ],
)
def test_state_tree_snapshot(snap_compare, demo_server, lang, size, name):
    """``t`` on the smu screen, then ``h`` (tui-screens.md 5.6): groups, fields, the hidden section."""
    url, sid = demo_server
    tui = SimuBenchApp(url, sid=sid)

    async def run_before(pilot) -> None:
        import asyncio

        await _settle(pilot, tui)
        await pilot.press("3")
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.pause(1.0)
        await pilot.press("t")
        await pilot.pause(0.5)
        await pilot.press("h")
        end = asyncio.get_running_loop().time() + SETTLE_S
        while asyncio.get_running_loop().time() < end:
            await pilot.pause(0.1)
            text = "\n".join(
                "".join(seg.text for seg in strip) for strip in tui.screen._compositor.render_strips()
            )
            if "State · smu" in text and "reveal_true_values" in text:
                return
        raise AssertionError("the state tree never settled")

    assert snap_compare(tui, terminal_size=size, run_before=run_before)


@pytest.mark.parametrize(
    ("lang", "size", "name"),
    [
        ("en", (100, 32), "scope-en-100x32"),
        ("en", (80, 24), "scope-en-80x24"),
    ],
)
def test_scope_snapshot(snap_compare, demo_server, lang, size, name):
    """Enter on the scope card (14): braille trace, settings column, log (tui-screens.md 6)."""
    url, sid = demo_server
    tui = SimuBenchApp(url, sid=sid)

    async def run_before(pilot) -> None:
        import asyncio

        await _settle(pilot, tui)
        await pilot.press("2")
        await pilot.pause(0.2)
        await pilot.press("enter")
        end = asyncio.get_running_loop().time() + SETTLE_S
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


@pytest.fixture(scope="module")
def scope_server():
    """The scope_rc bench's session for the nets screen: deterministic, command clock."""
    import asyncio
    import socket

    import uvicorn

    manager = SessionManager(EXAMPLES_DIR, scpi_base_port=5185, hislip_port=4895)
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
    served = asyncio.run(manager.start("siglent_scope_rc.bench.yaml", seed=7, clock="command"))
    served.session.exchange("awg", b"C1:OUTP ON", client="ui", reads=1)
    drive.panel(served, "scope", "stop", client="ui")  # a stopped scope shows its last acquisition
    yield f"http://127.0.0.1:{port}", served.id
    asyncio.run(stop_manager_sessions(manager))  # the sessions hold the HiSLIP port, not uvicorn (issue 686)
    server.should_exit = True
    thread.join(timeout=10)


NET_SIZES = [
    ("en", (100, 32), "nets-en-100x32"),
    ("en", (80, 24), "nets-en-80x24"),
]


@pytest.mark.parametrize(("lang", "size", "name"), NET_SIZES)
def test_nets_snapshot(snap_compare, scope_server, lang, size, name):
    """`n`: the nets list with levels and the selected net's probe (tui-screens.md 11.1)."""
    url, sid = scope_server
    tui = SimuBenchApp(url, sid=sid)

    async def run_before(pilot) -> None:
        import asyncio

        end = asyncio.get_running_loop().time() + SETTLE_S
        while asyncio.get_running_loop().time() < end:
            await pilot.pause(0.05)
            if tui.conn != "connected" or not tui.session_info:
                continue
            await pilot.press("n")
            await pilot.pause(0.1)
            from simubench.tui.screens.nets import NetsScreen

            if isinstance(tui.screen, NetsScreen):
                text = "\n".join(
                    "".join(seg.text for seg in strip) for strip in tui.screen._compositor.render_strips()
                )
                # The list renders the levels fetch and the probe pane draws the stopped scope's
                # last acquisition (braille trace, screen_data); under load either lands late and
                # the capture once happened before they did (#850): hold for the data, not the
                # layout.
                trace = any(0x2800 <= ord(c) <= 0x28FF for c in text)
                if (
                    (("net-0" in text and "直流" in text) or ("DC" in text and "net-0" in text))
                    and (tui.levels or {}).get("nets")
                    and trace
                ):
                    return
        raise AssertionError("the nets screen never settled")

    assert snap_compare(tui, terminal_size=size, run_before=run_before)


FAULT_SIZES = [(lang, size, name.replace("nets", "fault")) for lang, size, name in NET_SIZES]


@pytest.mark.parametrize(("lang", "size", "name"), FAULT_SIZES)
def test_fault_dialog_snapshot(snap_compare, scope_server, lang, size, name):
    """`f`: the injection dialog with its kind groups (tui-screens.md 8)."""
    url, sid = scope_server
    tui = SimuBenchApp(url, sid=sid)

    async def run_before(pilot) -> None:
        import asyncio

        end = asyncio.get_running_loop().time() + SETTLE_S
        while asyncio.get_running_loop().time() < end:
            await pilot.pause(0.05)
            if tui.conn != "connected" or not tui.session_info:
                continue
            await pilot.press("1")  # focus the first card: `f` acts on the focused instrument (8)
            await pilot.pause(0.05)
            await pilot.press("f")
            await pilot.pause(0.1)
            from simubench.tui.screens.faultdialog import FaultDialog

            if isinstance(tui.screen, FaultDialog):
                text = "\n".join(
                    "".join(seg.text for seg in strip) for strip in tui.screen._compositor.render_strips()
                )
                if "Timeout" in text or "Timeout" in text:
                    return
        raise AssertionError("the fault dialog never settled")

    assert snap_compare(tui, terminal_size=size, run_before=run_before)


# ---- the runs screen (tui-screens.md 11.3) ----------------------------------------------------------


@pytest.fixture
def fixed_times(monkeypatch):
    """Run records show wall-clock start times: pin them to UTC so the snapshot is the same
    in every time zone (Windows has no ``time.tzset``)."""
    import time as time_module

    monkeypatch.setattr("simubench.tui.screens.runs.time.localtime", time_module.gmtime)


def _runs_app(lang: str):
    """The overview's mock session plus two recorded runs (one still running) and a mismatch."""
    import copy

    from tui_mock import load, unit_app

    fixture = copy.deepcopy(load("unit_smu.json"))
    fixture["runs"] = [
        {
            "run_id": "run-abc",
            "bench": "示例测试台",
            "started": 1793577600.0,
            "ended": None,
            "clock": "command",
            "seed": 5,
            "events": 1284,
            "duration_ns": 4_210_000_000,
            "instruments": ["smu"],
            "size": 217_088,
        },
        {
            "run_id": "run-def",
            "bench": "siglent_scope_rc",
            "started": 1793491200.0,
            "ended": 1793491230.0,
            "clock": "stepped",
            "seed": 7,
            "events": 56,
            "duration_ns": 31_000_000,
            "instruments": ["scope"],
            "size": 1_500_000,
        },
    ]
    fixture["run_detail"] = {"summary": fixture["runs"][0]}
    fixture["replay"] = {
        "run_id": "run-abc",
        "ok": False,
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
        "warnings": [],
    }
    return unit_app(fixture, lang=lang)[0]


RUN_SIZES = [
    ("en", (100, 32), "runs-en-100x32"),
    ("en", (80, 24), "runs-en-80x24"),
]


@pytest.mark.parametrize(("lang", "size", "name"), RUN_SIZES)
def test_runs_snapshot(snap_compare, fixed_times, lang, size, name):
    """`R`: the list, the live session line, and `p`'s first difference (11.3)."""
    tui = _runs_app(lang)

    async def run_before(pilot) -> None:
        import asyncio

        from simubench.tui.screens.runs import RunsScreen

        end = asyncio.get_running_loop().time() + SETTLE_S
        while asyncio.get_running_loop().time() < end:
            await pilot.pause(0.05)
            if tui.conn != "connected" or not tui.session_info:
                continue
            screen = tui.screen
            if not isinstance(screen, RunsScreen):
                await pilot.press("R")
                await pilot.pause(0.1)
                continue
            if not screen.runs:  # the list's fetch has not landed yet
                await pilot.pause(0.1)
                continue
            await pilot.press("p")  # the replay of the selected run reports its first difference
            end = asyncio.get_running_loop().time() + SETTLE_S
            while asyncio.get_running_loop().time() < end:
                await pilot.pause(0.05)
                if screen.replay is not None:
                    return
            break
        raise AssertionError("the runs screen never settled")

    assert snap_compare(tui, terminal_size=size, run_before=run_before)


# ---- the new categories' instrument screens (#282) ----------------------------------------------------


def _serve(workspace: Path, scpi_base_port: int, hislip_port: int):
    """A server over a workspace's benches with fixed SCPI/HiSLIP ports (the identity box shows the
    addresses); the test starts one session per category bench."""
    import socket

    import uvicorn

    wait_for_free_ports([*range(scpi_base_port, scpi_base_port + 8), hislip_port])  # #857
    manager = SessionManager(workspace, scpi_base_port=scpi_base_port, hislip_port=hislip_port)
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
    return f"http://127.0.0.1:{port}", manager, server, thread


@pytest.fixture(scope="module")
def category_server():
    """The validation benches and the example benches (real models: the load, the power supply and
    the DAQ have no validation bench), each behind its own server."""
    servers = {
        # HiSLIP 4799, not 4899: test_tui_t4's demo pins 4899 and runs on another xdist worker.
        "validation": _serve(ROOT / "validation", 5585, 4799),
        "examples": _serve(ROOT / "examples", 5685, 4999),
    }
    yield {key: (url, manager) for key, (url, manager, _, _) in servers.items()}
    for _, manager, server, thread in servers.values():
        asyncio.run(
            stop_manager_sessions(manager)
        )  # the sessions hold the HiSLIP port, not uvicorn (issue 686)
        server.should_exit = True
        thread.join(timeout=10)


#: (workspace, bench, instrument, text the screen shows once its readings arrived, kind).
CATEGORY_SCREENS = [
    ("validation", "benches/counter.bench.yaml", "cnt", "1.000000000 kHz", "counter"),
    ("validation", "benches/specan.bench.yaml", "sa", "Marker", "specan"),
    ("validation", "benches/lockin_rc.bench.yaml", "lia", "θ", "lockin"),
    # #355: the RF source, the load, the power supply and the DAQ.
    ("validation", "benches/rfgen.bench.yaml", "rf", "Frequency", "rfgen"),
    ("examples", "benches/rigol_dl3021.bench.yaml", "dut", "INPUT", "eload"),
    ("examples", "benches/keysight_e36312a.bench.yaml", "psu", "CH1", "psu"),
    ("examples", "benches/keysight_34970a.bench.yaml", "daq", "Scan", "daq"),
]


async def _open_instrument(pilot, tui, instrument: str, needle: str, kind: str) -> str:
    import asyncio

    await _settle(pilot, tui)
    tui.show("instrument", instrument)
    end = asyncio.get_running_loop().time() + SETTLE_S
    text = ""
    while asyncio.get_running_loop().time() < end:
        await pilot.pause(0.1)
        text = "\n".join(
            "".join(seg.text for seg in strip) for strip in tui.screen._compositor.render_strips()
        )
        if needle in text:
            return text
    raise AssertionError(f"the {kind} screen never showed {needle!r}:\n{text}")


@pytest.mark.parametrize(
    ("workspace", "bench", "instrument", "needle", "kind"),
    CATEGORY_SCREENS,
    ids=[c[4] for c in CATEGORY_SCREENS],
)
@pytest.mark.parametrize("size", [(100, 32), (80, 24)], ids=["100x32", "80x24"])
def test_category_instrument_snapshot(
    snap_compare, category_server, workspace, bench, instrument, needle, kind, size
):
    """Each category's screen: the counter's reading, the analyzer's trace and marker, the lock-in's
    X/Y/R/θ (#282); the RF source, the load, the power supply and the DAQ (#355)."""
    import asyncio

    url, manager = category_server[workspace]
    served = asyncio.run(manager.start(bench, seed=5, clock="command"))
    tui = SimuBenchApp(url, sid=served.id)

    async def run_before(pilot) -> None:
        await _open_instrument(pilot, tui, instrument, needle, kind)

    assert snap_compare(tui, terminal_size=size, run_before=run_before)


@pytest.mark.parametrize(
    ("workspace", "bench", "instrument", "shows", "kind"),
    [
        # Frequency and level, the RF/modulation flags and integer line counts — no internal
        # quantities of the synthesizer.
        (
            "validation",
            "benches/rfgen.bench.yaml",
            "rf",
            ["Frequency", "Level", "RF ON", "Lines 1", "Carrier"],
            "rfgen",
        ),
        # Measured V, I, P as values with their units (the bench's source gives 5 V, the input
        # is off), the input state and the mode (#374: labels alone prove nothing). The voltage
        # rides the readback noise (#808): any draw within ±4σ (0.6 mV) of 5 V; the off input sinks
        # exactly 0 A, so I and P stay exact.
        (
            "examples",
            "benches/rigol_dl3021.bench.yaml",
            "dut",
            [
                "INPUT",
                r"re:V\s+(?:4\.999[4-9][0-9]|5\.000[0-6][0-9]) V",
                r"re:I\s+0\.00000 A",
                r"re:P\s+0\.000 W",
            ],
            "eload",
        ),
        # Per-channel voltage and current readings with units, all outputs off (zeros) (#374).
        (
            "examples",
            "benches/keysight_e36312a.bench.yaml",
            "psu",
            # The panel reading rides the readback noise (#744): any draw within ±4σ of 0 V.
            # The panel reading rides the readback noise (#744) and the psu screen renders it in
            # engineering units: any draw within ~4σ of 0 V shows as µV (below ~1 mV) or mV.
            [
                "CH1 voltage",
                "CH3 current",
                r"re:CH1 voltage\s+[+-]?(?:[0-9]{1,3}\.[0-9]{6} µV|1\.[0-9]{6} mV|0\.000000 V)",
            ],
            "psu",
        ),
        # The scan's reading with its unit; nothing scanned yet, so zero volts (#374).
        (
            "examples",
            "benches/keysight_34970a.bench.yaml",
            "daq",
            ["Scan list", r"re:Reading\s+0\.000000 V"],
            "daq",
        ),
    ],
    ids=["rfgen", "eload", "psu", "daq"],
)
def test_the_category_screens_show_meaningful_readings(
    category_server, workspace, bench, instrument, shows, kind
):
    import asyncio
    import re

    url, manager = category_server[workspace]
    served = asyncio.run(manager.start(bench, seed=5, clock="command"))
    tui = SimuBenchApp(url, sid=served.id)

    async def run() -> str:
        async with tui.run_test(size=(100, 32)) as pilot:
            return await _open_instrument(pilot, tui, instrument, shows[0], kind)

    text = asyncio.run(run())
    for needle in shows:
        if needle.startswith("re:"):
            assert re.search(needle[3:], text), needle
        else:
            assert needle in text, needle
    for internal in (
        "synth.point",
        "synth.points",
        "mod.lines",
        "mod.beta",
        "mod.omitted_power",
        "load.regulating",
    ):
        assert internal not in text
    assert not re.search(r"[\u2e80-\u9fff]", text), "the TUI is English only (D23)"
