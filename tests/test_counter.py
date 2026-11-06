"""The frequency counter's components (F #192): input conditioning, the gate and the timebase, the
four measurement modes and the statistics, on benches driven through SCPI.

The measurement physics is checked against closed forms: a 1 kHz sine reads 1 kHz (and 1 ms), a fast
timebase scales both by 1 + ε, totalize counts the gate's events, and a time interval between two
channels is the phase between them plus the trigger error of the two channels that do not share it.
"""

from __future__ import annotations

import itertools
import math
from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"


def awg(frequency: float = 1000.0, amplitude: float = 2.0, phase: float = 0.0, offset: float = 0.0) -> dict:
    return {
        "model": "sb-awg-2",
        "state": {
            "ch1.function": "SINusoid",
            "ch1.frequency": frequency,
            "ch1.amplitude": amplitude,
            "ch1.phase": phase,
            "ch1.offset": offset,
            "ch1.load": math.inf,
            "ch1.output": True,
        },
    }


def bench(
    instruments: dict, wires: list, clock: str = "command", hidden: dict | None = None
) -> BenchDefinition:
    if "cnt" in instruments and hidden:
        instruments["cnt"] = {**instruments["cnt"], "hidden": hidden}
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "counter",
            "instruments": instruments,
            "wires": wires,
            "run": {"seed": 5, "clock": clock, "idle": "off"},
        }
    )


def q(session, instrument: str, text: str) -> str:
    session.write(instrument, (text + "\n").encode())
    reply = session.read(instrument)
    assert reply is not None, text
    return reply.decode().rstrip("\n")


def num(session, instrument: str, text: str) -> float:
    return float(q(session, instrument, text))


def one_channel(clock: str = "command", hidden: dict | None = None):
    return bench(
        {"cnt": {"model": "sb-counter-1"}, "awg": awg()},
        [{"from": "awg.OUT1", "to": "cnt.CH1"}],
        clock=clock,
        hidden=hidden,
    )


def two_channels() -> BenchDefinition:
    return bench(
        {"cnt": {"model": "sb-counter-1"}, "a": awg(phase=0.0), "b": awg(phase=180.0)},
        [{"from": "a.OUT1", "to": "cnt.CH1"}, {"from": "b.OUT1", "to": "cnt.CH2"}],
        clock="stepped",
    )


# ---- frequency, period and totalize: the measurement modes on one channel ------------------------------


def test_the_frequency_period_and_totalize_flow():
    """Frequency and period are reciprocal and take the gate time; totalize counts the events
    inside the gate."""
    session = open_bench(one_channel(), definition_dirs=[DEFS])
    try:
        # -- frequency and period are reciprocal and take the gate time (was
        # test_frequency_and_period_are_reciprocal_and_take_the_gate_time)
        before = session.now_ns
        assert num(session, "cnt", ":MEASure:FREQuency?") == pytest.approx(1000.0, rel=1e-6)
        assert session.now_ns - before == 100_000_000  # the 0.1 s gate in virtual time
        assert num(session, "cnt", ":MEASure:PERiod?") == pytest.approx(1e-3, rel=1e-6)
        session.write("cnt", b":GATe:TIME 0.02\n")
        assert num(session, "cnt", ":MEASure:FREQuency?") == pytest.approx(1000.0, rel=1e-6)
        session.write("cnt", b":FUNCtion PERiod\n")
        assert num(session, "cnt", ":MEASure:PERiod?") == pytest.approx(1e-3, rel=1e-6)
        # -- totalize counts the events inside the gate (was
        # test_totalize_counts_the_events_inside_the_gate; :MEASure: names its function, so the
        # PERiod set above does not follow it into the totalize)
        session.write("cnt", b":GATe:TIME 0.1\n")
        assert num(session, "cnt", ":MEASure:TOTalize?") == pytest.approx(
            100.0
        )  # [0, 100 ms): the crossing at 100 ms is out
        session.write("cnt", b":GATe:TIME 0.01\n")
        assert num(session, "cnt", ":MEASure:TOTalize?") == pytest.approx(10.0)
    finally:
        session.close()


def test_the_timebase_scales_the_reading():
    session = open_bench(one_channel(hidden={"clock": {"ppm": 2.0}}), definition_dirs=[DEFS])
    try:
        # A fast timebase makes the instrument's second short: it reads low by ε.
        assert num(session, "cnt", ":MEASure:FREQuency?") == pytest.approx(1000.0 / (1 + 2e-6), rel=1e-6)
        assert num(session, "cnt", ":MEASure:PERiod?") == pytest.approx(1e-3 * (1 + 2e-6), rel=1e-6)
    finally:
        session.close()


# ---- time interval -------------------------------------------------------------------------------


def test_the_time_interval_is_the_phase_between_the_channels():
    session = open_bench(two_channels(), definition_dirs=[DEFS])
    try:
        # B is 180° behind A: its first rising zero crossing is half a period on.
        assert num(session, "cnt", ":MEASure:TINTerval?") == pytest.approx(0.5e-3, abs=6e-9)
        # The same instant reads the same value (the trigger error is seeded by virtual time).
        first = num(session, "cnt", ":MEASure:TINTerval?")
        assert num(session, "cnt", ":MEASure:TINTerval?") == first
        session.advance(0.137)
        second = num(session, "cnt", ":MEASure:TINTerval?")
        assert abs(second - 0.5e-3) <= 6e-9
        session.write("b", b":SOURce1:PHASe 270\n")
        assert num(session, "cnt", ":MEASure:TINTerval?") == pytest.approx(0.25e-3, abs=6e-9)
    finally:
        session.close()


# ---- input conditioning --------------------------------------------------------------------------


def test_threshold_and_sensitivity_reject_small_signals():
    session = open_bench(one_channel(), definition_dirs=[DEFS])
    try:
        assert num(session, "cnt", ":MEASure:FREQuency?") == pytest.approx(1000.0, rel=1e-6)
        # A 2 Vpp signal (1 V peak) never reaches a 1.2 V threshold.
        session.write("cnt", b":INPut:A:LEVel 1.2\n")
        assert num(session, "cnt", ":MEASure:FREQuency?") == 0.0
        # Nor does a 2 Vpp sensitivity accept it.
        session.write("cnt", b":INPut:A:LEVel 0\n")
        session.write("cnt", b":INPut:A:SENSitivity 3\n")
        assert num(session, "cnt", ":MEASure:FREQuency?") == 0.0
        session.write("cnt", b":INPut:A:SENSitivity 0.5\n")
        assert num(session, "cnt", ":MEASure:FREQuency?") == pytest.approx(1000.0, rel=1e-6)
    finally:
        session.close()


def test_the_input_impedance_loads_the_source():
    session = open_bench(one_channel(clock="stepped"), definition_dirs=[DEFS])
    try:
        # 2 Vpp open circuit, 1 V peak into 1 MΩ: a 0.7 V threshold counts ...
        session.write("cnt", b":INPut:A:LEVel 0.7\n")
        assert num(session, "cnt", ":MEASure:FREQuency?") == pytest.approx(1000.0, rel=1e-6)
        # ... but the 50 Ω setting halves the voltage against the source's 50 Ω: no crossings.
        session.write("cnt", b":INPut:A:IMPedance 50\n")
        assert num(session, "cnt", ":MEASure:FREQuency?") == 0.0
        session.write("cnt", b":INPut:A:IMPedance 1M\n")
        assert num(session, "cnt", ":MEASure:FREQuency?") == pytest.approx(1000.0, rel=1e-6)
    finally:
        session.close()


def test_ac_coupling_removes_a_dc_offset():
    session = open_bench(
        bench(
            {"cnt": {"model": "sb-counter-1"}, "awg": awg(amplitude=1.0, offset=2.0)},
            [{"from": "awg.OUT1", "to": "cnt.CH1"}],
            clock="stepped",
        ),
        definition_dirs=[DEFS],
    )
    try:
        # 2 V ± 0.5 V never crosses zero with DC coupling.
        session.write("cnt", b":INPut:A:LEVel 0\n")
        assert num(session, "cnt", ":MEASure:FREQuency?") == 0.0
        session.write("cnt", b":INPut:A:COUPling AC\n")
        assert num(session, "cnt", ":MEASure:FREQuency?") == pytest.approx(1000.0, rel=1e-6)
    finally:
        session.close()


# ---- statistics and the trigger model -------------------------------------------------------------


def test_statistics_summarize_the_measurements():
    session = open_bench(two_channels(), definition_dirs=[DEFS])
    try:
        session.write("cnt", b":CALCulate:STATistics:CLEar\n")
        for _ in range(20):
            session.advance(0.137)
            num(session, "cnt", ":MEASure:TINTerval?")
        assert num(session, "cnt", ":CALCulate:STATistics:COUNt?") == 20.0
        mean = num(session, "cnt", ":CALCulate:STATistics:MEAN?")
        low = num(session, "cnt", ":CALCulate:STATistics:MINimum?")
        high = num(session, "cnt", ":CALCulate:STATistics:MAXimum?")
        sdev = num(session, "cnt", ":CALCulate:STATistics:SDEViation?")
        assert mean == pytest.approx(0.5e-3, abs=6e-9)
        assert low <= mean <= high
        assert 1e-9 < sdev < 4e-9  # two independent channels: √2 ns each, √2 ns on the difference
        session.write("cnt", b":CALCulate:STATistics:CLEar\n")
        assert num(session, "cnt", ":CALCulate:STATistics:COUNt?") == 0.0
    finally:
        session.close()


def test_the_statistics_and_triggered_run_flow():
    """A triggered run takes one reading per gate; the readings' resolution error follows the gate
    time and is nonzero at any gate."""
    session = open_bench(one_channel(), definition_dirs=[DEFS])
    try:
        # -- a triggered run takes one reading per gate (was
        # test_a_triggered_run_takes_one_reading_per_gate)
        session.write("cnt", b":SAMPle:COUNt 3\n")
        values = [float(v) for v in q(session, "cnt", ":READ?").split(",")]
        assert len(values) == 3
        assert values == pytest.approx([1000.0, 1000.0, 1000.0], rel=1e-6)
        session.write("cnt", b":INITiate\n")
        fetched = [float(v) for v in q(session, "cnt", ":FETCh?").split(",")]
        assert fetched == pytest.approx(values, rel=1e-12)

        # -- frequency resolution follows the gate time (was
        # test_frequency_resolution_follows_the_gate_time) #255: the endpoint errors are fixed, so
        # a 10x gate gives about a 10x smaller spread. (:MEASure: takes one reading whatever
        # SAMPle:COUNt says, so the act above's count of 3 does not follow it here.)
        def spread(gate: float, n: int = 40) -> float:
            session.write("cnt", f":GATe:TIME {gate:g}\n".encode())
            session.write("cnt", b":CALCulate:STATistics:CLEar\n")
            for _ in range(n):
                session.advance(0.003)
                num(session, "cnt", ":MEASure:FREQuency?")
            return num(session, "cnt", ":CALCulate:STATistics:SDEViation?")

        slow = spread(0.1)
        fast = spread(0.01)
        assert slow > 0.0 and fast > 0.0
        ratio = fast / slow
        assert 5.0 < ratio < 20.0  # 10x the gate, within the spread of 40 samples
        # -- the sdev is nonzero for frequency (was test_statistics_sdev_is_nonzero_for_frequency)
        # #255: repeated frequency readings carry a resolution error, unlike the old exact model.
        session.write("cnt", b":GATe:TIME 0.1\n")  # bridge: the 0.1 s gate the act's bound names
        session.write("cnt", b":CALCulate:STATistics:CLEar\n")
        for _ in range(10):
            session.advance(0.11)
            num(session, "cnt", ":MEASure:FREQuency?")
        sdev = num(session, "cnt", ":CALCulate:STATistics:SDEViation?")
        assert 0.0 < sdev < 1.0  # a few 1e-8 of 1000 Hz at the 0.1 s gate
    finally:
        session.close()


# ---- the timebase: timestamps and gap-free gates (#328) ----------------------------------------------


def _off_grid(hidden: dict | None = None) -> BenchDefinition:
    """1234.567 Hz into CH1: the 10 ms gates hold 12 or 13 edges, never a whole number of periods."""
    return bench(
        {"cnt": {"model": "sb-counter-1"}, "awg": awg(frequency=1234.567)},
        [{"from": "awg.OUT1", "to": "cnt.CH1"}],
        hidden=hidden,
    )


GATES = 8


def _gates(session, gap_free: bool) -> tuple[list[float], list[float], float, list[tuple[int, float, float]]]:
    """Eight 10 ms gates read sample by sample: (readings, SPAN fields, the boundary span on the
    timebase — from the edge before the first gate to the last edge of the last — and each gate's
    (edges, first edge, last edge))."""
    session.write("cnt", b":GATe:TIME 0.01\n")
    session.write("cnt", f":FREQuency:MODE:GAPFree {'ON' if gap_free else 'OFF'}\n".encode())
    core = session.instruments["cnt"].components["core"]
    tb = session.instruments["cnt"].components["tb"]
    core.run_begin()
    start = session.now_ns
    gate = core._gate().duration_ns()
    readings, spans = [], []
    for i in range(GATES):
        readings.append(core.sample(i))
        spans.append(core.sample_fields(i)["SPAN"])
    edges = [core._input("a").events(start + i * gate, start + (i + 1) * gate) for i in range(GATES)]
    boundary = tb.timestamp(edges[-1][2]) - tb.timestamp(core._last_edge_before(start))
    return readings, spans, boundary, edges


def test_the_gap_free_gates_flow():
    """Gap-free and gated runs on the off-grid source (#328/#351): adjacent gap-free readings share
    their boundary edge, so their spans add up to the whole boundary span — the property that tells
    gap-free from gated (Σ reading × SPAN = Σ edges holds for any span by construction, so it alone
    proves nothing); with GAPFree OFF each gate loses the time before its first edge; the SCPI path
    reads gap-free samples; the timestamps follow the edges on the timebase."""
    session = open_bench(_off_grid(), definition_dirs=[DEFS])
    try:
        # -- the gap-free spans join end to end (was test_gap_free_spans_join_end_to_end)
        readings, spans, boundary, edges = _gates(session, gap_free=True)
        assert sum(spans) == pytest.approx(boundary, rel=1e-12)
        assert sum(f * s for f, s in zip(readings, spans, strict=True)) == pytest.approx(
            sum(count for count, _, _ in edges), rel=1e-9
        )
        assert all(r == pytest.approx(1234.567, rel=1e-6) for r in readings)
        # -- without gap-free each gate loses the time before its first edge (was
        # test_without_gap_free_each_gate_loses_the_time_before_its_first_edge); the control: each
        # reading spans its own first-to-last edge, so the spans fall short of the boundary span by
        # one input period per gate (the dead time). run_begin re-anchors the run at the act's start.
        readings, _, boundary, edges = _gates(session, gap_free=False)
        own = [(count - 1) / f for (count, _, _), f in zip(edges, readings, strict=True)]
        # one period (0.81 ms) per gate; the readings' trigger error moves it by nanoseconds
        assert boundary - sum(own) == pytest.approx(GATES / 1234.567, abs=1e-7)
        # -- gap-free readings through SCPI (was test_gap_free_readings_through_scpi): :READ? with
        # GAPFree ON and eight samples, the gap-free path end to end
        for text in (":GATe:TIME 0.01", ":FREQuency:MODE:GAPFree ON", ":SAMPle:COUNt 8"):
            session.write("cnt", (text + "\n").encode())
        values = [float(v) for v in q(session, "cnt", ":READ?").split(",")]
        assert len(values) == 8
        assert all(v == pytest.approx(1234.567, rel=1e-6) for v in values)
        assert q(session, "cnt", ":SYST:ERR?").startswith("0,")
        # -- the timestamps follow the edges on the timebase (was
        # test_timestamps_follow_the_edges_on_the_timebase)
        stamps = [float(x) for x in q(session, "cnt", ":MEASure:TSTamp? 4").split(",")]
        steps = [b - a for a, b in itertools.pairwise(stamps)]
        # one period apart, within the 20 ps single-shot resolution
        assert all(st == pytest.approx(1 / 1234.567, abs=1e-10) for st in steps)
    finally:
        session.close()


def test_gap_free_readings_on_a_timebase_with_an_error():
    """A timebase 5 ppm fast reads every span 5 ppm long, so each gap-free reading is low by the
    same factor, and the spans still join end to end on that timebase."""
    session = open_bench(_off_grid(hidden={"clock": {"ppm": 5.0}}), definition_dirs=[DEFS])
    try:
        readings, spans, boundary, _ = _gates(session, gap_free=True)
        assert sum(spans) == pytest.approx(boundary, rel=1e-12)
        assert all(r == pytest.approx(1234.567 / (1 + 5e-6), rel=2e-7) for r in readings)
    finally:
        session.close()
