"""The frequency counter's components (F #192): input conditioning, the gate and the timebase, the
four measurement modes and the statistics, on benches driven through SCPI.

The measurement physics is checked against closed forms: a 1 kHz sine reads 1 kHz (and 1 ms), a fast
timebase scales both by 1 + ε, totalize counts the gate's events, and a time interval between two
channels is the phase between them plus the trigger error of the two channels that do not share it.
"""

from __future__ import annotations

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


# ---- frequency, period and the timebase ------------------------------------------------------------


def test_frequency_and_period_are_reciprocal_and_take_the_gate_time():
    session = open_bench(one_channel(), definition_dirs=[DEFS])
    try:
        before = session.now_ns
        assert num(session, "cnt", ":MEASure:FREQuency?") == pytest.approx(1000.0, rel=1e-6)
        assert session.now_ns - before == 100_000_000  # the 0.1 s gate in virtual time
        assert num(session, "cnt", ":MEASure:PERiod?") == pytest.approx(1e-3, rel=1e-6)
        session.write("cnt", b":GATe:TIME 0.02\n")
        assert num(session, "cnt", ":MEASure:FREQuency?") == pytest.approx(1000.0, rel=1e-6)
        session.write("cnt", b":FUNCtion PERiod\n")
        assert num(session, "cnt", ":MEASure:PERiod?") == pytest.approx(1e-3, rel=1e-6)
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


# ---- totalize ------------------------------------------------------------------------------------


def test_totalize_counts_the_events_inside_the_gate():
    session = open_bench(one_channel(), definition_dirs=[DEFS])
    try:
        session.write("cnt", b":GATe:TIME 0.1\n")
        assert num(session, "cnt", ":MEASure:TOTalize?") == pytest.approx(101.0)  # 0, 1 ms, ..., 100 ms
        session.write("cnt", b":GATe:TIME 0.01\n")
        assert num(session, "cnt", ":MEASure:TOTalize?") == pytest.approx(11.0)
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
        assert 0.0 <= sdev <= 6e-9  # the 1 ns trigger error of the two channels
        session.write("cnt", b":CALCulate:STATistics:CLEar\n")
        assert num(session, "cnt", ":CALCulate:STATistics:COUNt?") == 0.0
    finally:
        session.close()


def test_a_triggered_run_takes_one_reading_per_gate():
    session = open_bench(one_channel(), definition_dirs=[DEFS])
    try:
        session.write("cnt", b":SAMPle:COUNt 3\n")
        values = [float(v) for v in q(session, "cnt", ":READ?").split(",")]
        assert len(values) == 3
        assert values == pytest.approx([1000.0, 1000.0, 1000.0], rel=1e-6)
        session.write("cnt", b":INITiate\n")
        fetched = [float(v) for v in q(session, "cnt", ":FETCh?").split(",")]
        assert fetched == pytest.approx(values, rel=1e-12)
    finally:
        session.close()


def test_frequency_resolution_follows_the_gate_time():
    """#255: the endpoint errors are fixed, so a 10x gate gives about a 10x smaller spread."""
    session = open_bench(one_channel(), definition_dirs=[DEFS])
    try:

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
    finally:
        session.close()


def test_statistics_sdev_is_nonzero_for_frequency():
    """#255: repeated frequency readings carry a resolution error, unlike the old exact model."""
    session = open_bench(one_channel(), definition_dirs=[DEFS])
    try:
        session.write("cnt", b":CALCulate:STATistics:CLEar\n")
        for _ in range(10):
            session.advance(0.11)
            num(session, "cnt", ":MEASure:FREQuency?")
        sdev = num(session, "cnt", ":CALCulate:STATistics:SDEViation?")
        assert 0.0 < sdev < 1.0  # a few 1e-8 of 1000 Hz at the 0.1 s gate
    finally:
        session.close()
