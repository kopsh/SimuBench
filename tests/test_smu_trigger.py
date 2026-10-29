"""The SMU core on the trigger model and reading memory: counts × samples, named buffers, elements
and the measure/source ranges (#94, metrology-authoring.md 3.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

DEFS = Path(__file__).parent / "definitions"
# One reading at 1 PLC with autozero on: 50 µs + 3 phases + firmware, the rule smu_core shares.
NPLC_S = 50e-6 + 3 * (1.0 / 60.0 + 185e-6) + 1.8e-3
OVERLOAD = 9.9e37


def bench(r: float = 1000.0, **state: object) -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "name": "smu-trigger",
            "instruments": {
                "smu": {
                    "model": "test-smu-trigger",
                    "state": {"source.voltage.level": 1.0, "output.enabled": True, **state},
                }
            },
            "parts": {"r1": {"type": "load_resistor", "params": {"r": r}}},
            "wires": [{"from": "smu.HI", "to": "r1.A"}],
            "run": {"clock": "command", "idle": "off"},
        }
    )


@pytest.fixture
def s():
    session = open_bench(bench(), definition_dirs=[DEFS])
    yield session
    session.close()


def q(s, cmd: str) -> str | None:
    s.write("smu", (cmd + "\n").encode())
    out = s.read("smu")
    return out.decode().strip() if out else None


def err(s) -> str:
    return q(s, ":SYST:ERR?") or ""


def values(reply: str | None) -> list[float]:
    assert reply, reply
    return [float(x) for x in reply.split(",")]


def test_counts_times_and_values(s):
    q(s, ":TRIG:COUN 3;:SAMP:COUN 2;:TRIG:DEL 0.01")
    t0 = s.now_ns
    q(s, ":INIT")
    assert s.now_ns == t0  # INITiate returns at once
    readings = values(q(s, ":FETC?"))
    assert len(readings) == 6
    assert readings == pytest.approx([1.0] * 6, abs=1e-9)  # 1 V across 1 kΩ
    step = round(NPLC_S * 1e9)
    assert s.now_ns - t0 == 3 * (round(0.01 * 1e9) + 2 * step)  # delay per trigger, readings back to back
    assert values(q(s, ":FETC?")) == readings  # fetching again gives the same run
    assert q(s, ":TRIG:STAT?") == "IDLE"


def test_a_sweep_is_one_run_of_its_points(s):
    q(s, ":TRIG:COUN 2;:SAMP:COUN 1")  # the sweep decides the run's size, not the counts
    q(s, ":SOUR:SWE:VOLT:LIN 0,0.9,3,0")
    t0 = s.now_ns
    rows = values(q(s, ":READ?"))
    assert rows == pytest.approx([0.0, 0.45, 0.9], abs=1e-9)
    assert s.now_ns - t0 == 3 * round(NPLC_S * 1e9)
    assert q(s, ":DATA:POIN?") == "3"


def test_named_buffers_and_elements(s):
    q(s, ":SAMP:COUN 3")
    q(s, ":INIT;*WAI")
    rel = values(q(s, ':TRAC:DATA? 1,3,"defbuffer1",READ,REL'))
    assert rel[0::2] == pytest.approx([1.0] * 3, abs=1e-9)
    assert rel[1::2] == pytest.approx([0.0, NPLC_S, 2 * NPLC_S], rel=1e-9)  # seconds since the first
    fields = values(q(s, ':TRAC:DATA? 1,3,"defbuffer1",TIME,VOLT,CURR,RES,STAT'))
    assert fields[0::5] == pytest.approx([NPLC_S, 2 * NPLC_S, 3 * NPLC_S], rel=1e-9)  # the reading times
    assert fields[1::5] == pytest.approx([1.0] * 3, abs=1e-9) and fields[2::5] == pytest.approx(
        [1.0e-3] * 3, abs=1e-9
    )
    assert fields[3::5] == pytest.approx([1000.0] * 3, rel=1e-9) and fields[4::5] == [0.0, 0.0, 0.0]
    q(s, ':TRAC:MAKE "mybuf",10')
    q(s, ':TRAC:DATA? 1,3,"mybuf",READ')
    assert err(s).startswith("-230,")  # the trigger model fills the default buffer only
    q(s, ":TRAC:CLE")
    assert q(s, ":DATA:POIN?") == "0"
    assert values(q(s, ":FETC?")) == rel[0::2]  # FETCh? answers from the run, the memory was emptied


def test_manual_measure_ranges_and_overload(s):
    q(s, ":SENS:RANG:AUTO OFF;:SENS:VOLT:RANG 0.1")
    assert q(s, ":SENS:VOLT:RANG:ACT?") == "+1.0000000E-01"
    row = values(q(s, ":MEAS?"))  # 1 V beyond 1.2 × 0.1: the voltage reads the overload
    assert row[0] == OVERLOAD and row[1] == pytest.approx(1e-3, rel=1e-9)
    assert row[2] == pytest.approx(1000.0, rel=1e-9) and row[4] == 0.0  # RES and STAT are the raw ones
    q(s, ":SENS:VOLT:RANG 0.5")  # the setting need not be a range: the smallest fitting one is used
    assert q(s, ":SENS:VOLT:RANG:ACT?") == "+1.0000000E+00"
    assert values(q(s, ":MEAS?"))[0] == pytest.approx(1.0, abs=1e-9)  # 1 V inside 1.2 × 1: no overload
    q(s, ":SENS:CURR:RANG 1.0e-4")  # the current reading overloads on its own range
    row = values(q(s, ":MEAS?"))
    assert row[0] == pytest.approx(1.0, abs=1e-9) and row[1] == OVERLOAD
    assert row[2] == pytest.approx(1000.0, rel=1e-9)


def test_measure_autorange_follows_the_reading(s):
    q(s, ":SOUR:VOLT 0.05;:MEAS?")  # the decision happens in a reading
    assert q(s, ":SENS:VOLT:RANG:ACT?") == "+1.0000000E-01"
    q(s, ":SOUR:VOLT 0.11;:MEAS?")  # inside the band (up = 1.2 × 0.1): keep the range
    assert q(s, ":SENS:VOLT:RANG:ACT?") == "+1.0000000E-01"
    q(s, ":SOUR:VOLT 0.5;:MEAS?")  # outside it: the smallest fitting range
    assert q(s, ":SENS:VOLT:RANG:ACT?") == "+1.0000000E+00"
    q(s, ":SOUR:VOLT 0.05;:MEAS?")  # below down = 0.1 × 1: drop
    assert q(s, ":SENS:VOLT:RANG:ACT?") == "+1.0000000E-01"


def test_the_source_envelope_comes_from_the_range_table():
    # source_ranges [1, 10] with range_limits {1: 5 mA, 10: 50 mA}; 5 V into 80 Ω wants 62.5 mA:
    # on the auto range (10 V) the 50 mA cap holds the terminal.
    session = open_bench(bench(r=80.0, **{"source.voltage.level": 5.0}), definition_dirs=[DEFS])
    try:
        row = [float(x) for x in q(session, ":MEAS?").split(",")]
        assert row[0] == pytest.approx(4.0, abs=1e-9) and row[1] == pytest.approx(0.05, rel=1e-9)
        assert row[4] == 8.0  # the limit, not the setpoint, holds the terminal
    finally:
        session.close()


def test_the_manual_source_range_governs_the_envelope():
    # The same bench on the manual 1 V source range: its cap is 5 mA, so the terminal lands lower.
    session = open_bench(bench(r=80.0, **{"source.voltage.level": 5.0}), definition_dirs=[DEFS])
    try:
        q(session, ":SOUR:RANG:AUTO OFF;:SOUR:VOLT:RANG 1")
        row = [float(x) for x in q(session, ":MEAS?").split(",")]
        assert row[0] == pytest.approx(0.4, abs=1e-9) and row[1] == pytest.approx(0.005, rel=1e-9)
        assert row[4] == 8.0
        q(session, ":SOUR:RANG:AUTO ON")  # auto again: the 10 V range and its 50 mA cap
        row = [float(x) for x in q(session, ":MEAS?").split(",")]
        assert row[0] == pytest.approx(4.0, abs=1e-9) and row[1] == pytest.approx(0.05, rel=1e-9)
    finally:
        session.close()
