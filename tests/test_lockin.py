"""The lock-in's new capabilities (D3 #189): the data buffer over the generic trigger model and
reading buffer, the circuit-level AUX ports, offset/expansion, the line notches and the reserve.

The demodulator itself is covered by the acceptance scenario (docs/validation-lockin.md) and the
gold standard (tests/test_lockin_migration.py); these tests exercise what the split added.
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
BENCHES = ROOT / "acceptance" / "benches"


def bench(instruments: dict, wires: list) -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "lockin-caps",
            "instruments": instruments,
            "wires": wires,
            "run": {"seed": 5, "clock": "stepped"},
        }
    )


def scpi(session, instrument: str, text: str) -> str | None:
    session.write(instrument, (text + "\n").encode())
    reply = session.read(instrument)
    return reply.decode().rstrip("\n") if reply is not None else None


def drain(session, instrument: str) -> list[str]:
    out = []
    while not (e := scpi(session, instrument, ":SYSTem:ERRor?")).startswith("0,"):
        out.append(e)
    return out


def open_rc():
    return open_bench(BENCHES / "lockin_rc.bench.yaml", definition_dirs=[DEFS], clock="stepped")


# ---- AUX ----------------------------------------------------------------------------------------


def test_aux_inputs_and_outputs_are_circuit_ports():
    """AUX_IN reads the solved voltage on its net and AUX_OUT drives its pin from the DAC setting."""
    b = bench(
        {
            "lia": {
                "model": "sb-lia-1",
                "state": {"sine_out.enabled": False},
            },
            "awg": {
                "model": "sb-awg-2",
                "state": {
                    "ch1.function": "DC",
                    "ch1.offset": 1.0,
                    "ch1.load": math.inf,
                    "ch1.output": True,
                },
            },
            "dmm": {"model": "sb-dmm-1"},
        },
        [
            {"from": "awg.OUT1", "to": "lia.AUX_IN1"},
            {"from": "lia.AUX_OUT2", "to": "dmm.IN"},
        ],
    )
    session = open_bench(b, definition_dirs=[DEFS])
    try:
        # The 50 Ω source into the AUX input's 1 MΩ.
        assert float(scpi(session, "lia", ":AUX:INPut? 1")) == pytest.approx(1.0 * 1e6 / (1e6 + 50), rel=1e-6)
        assert float(scpi(session, "lia", ":AUX:INPut? 3")) == pytest.approx(0.0, abs=1e-12)

        assert scpi(session, "lia", ":AUX:OUTPut 2,1.5") is None
        assert float(scpi(session, "lia", ":AUX:INPut? 2")) == pytest.approx(0.0, abs=1e-12)
        # The DAC's 1 kΩ into the DMM's 10 MΩ.
        assert float(scpi(session, "dmm", ":MEASure:VOLTage:DC?")) == pytest.approx(
            1.5 * 10e6 / (10e6 + 1e3), rel=1e-6
        )
        session.write("lia", b":AUX:OUTPut 9,1\n")
        assert drain(session, "lia") == ['-222,"Data out of range; an AUX output is 1 to 4"']
    finally:
        session.close()


# ---- buffer -------------------------------------------------------------------------------------


def test_the_data_buffer_samples_the_filter_at_its_own_instants():
    """SRAT/TRCA through the generic trigger_model + reading_buffer: ten readings 10 ms apart,
    taken from the closed-form filter state at those instants."""
    session = open_rc()
    try:
        scpi(session, "lia", ":OUTPut:STATe OFF")
        scpi(session, "lia", ":SOURce:VOLTage 0.5")
        session.advance(2.0)
        scpi(session, "lia", ":OUTPut:STATe ON")  # the step starts now
        scpi(session, "lia", ":BUFFer:RATE 100")
        scpi(session, "lia", ":BUFFer:POINts 10")
        scpi(session, "lia", ":BUFFer:STARt")
        session.advance(0.1)  # the last reading's instant
        assert scpi(session, "lia", ":BUFFer:STOReD?") == "10"
        values = [float(v) for v in scpi(session, "lia", ":BUFFer:DATA? 1,10,R").split(",")]
        spacing = [float(v) for v in scpi(session, "lia", ":BUFFer:DATA? 1,10,REL").split(",")]
        assert spacing == pytest.approx([0.01 * i for i in range(10)], rel=1e-6)
        assert all(b >= a - 1e-6 for a, b in itertools.pairwise(values))
        assert values[0] == pytest.approx(0.0, abs=1e-3)
        # The last buffered instant is now: the panel reading and the sample agree.
        assert values[-1] == pytest.approx(float(scpi(session, "lia", ":MEASure:R?")), rel=1e-6)
        assert drain(session, "lia") == []
    finally:
        session.close()


# ---- offset and expansion -----------------------------------------------------------------------


def test_offset_and_expansion_are_display_level():
    session = open_rc()
    try:
        scpi(session, "lia", ":SOURce:VOLTage 0.5")
        scpi(session, "lia", ":REFerence:FREQuency 1515.76")
        session.advance(1.0)
        raw_x = float(scpi(session, "lia", ":MEASure:X?"))
        raw_r = float(scpi(session, "lia", ":MEASure:R?"))
        raw_theta = float(scpi(session, "lia", ":MEASure:THETa?"))
        assert raw_x != 0.0
        # 10 % of full scale (1 V) is subtracted, then x10: (X - 0.1 V) * 10.
        scpi(session, "lia", ":DISPlay:OFFSet:X 10")
        scpi(session, "lia", ":DISPlay:EXPand 10")
        shown_x = float(scpi(session, "lia", ":MEASure:X?"))
        assert shown_x == pytest.approx((raw_x - 0.1) * 10, abs=1e-9)
        # R and theta always show the raw outputs; the panel's X matches the command's.
        assert float(scpi(session, "lia", ":MEASure:R?")) == pytest.approx(raw_r, rel=1e-12)
        assert float(scpi(session, "lia", ":MEASure:THETa?")) == pytest.approx(raw_theta, rel=1e-12)
        scpi(session, "lia", ":DISPlay:OFFSet:X 0")
        scpi(session, "lia", ":DISPlay:EXPand 1")
    finally:
        session.close()


# ---- notches and reserve ------------------------------------------------------------------------


def sine_bench(frequency: float) -> BenchDefinition:
    return bench(
        {
            "lia": {
                "model": "sb-lia-1",
                "state": {
                    "sine_out.enabled": False,
                    "reference.frequency": frequency,
                    "filter.time_constant": 0.03,
                    "filter.slope": "24",
                },
            },
            "awg": {
                "model": "sb-awg-2",
                "state": {
                    "ch1.function": "SINusoid",
                    "ch1.frequency": frequency,
                    "ch1.amplitude": 1.0,
                    "ch1.load": math.inf,
                    "ch1.output": True,
                },
            },
        },
        [{"from": "awg.OUT1", "to": "lia.SIG_IN"}],
    )


def test_the_line_notch_removes_the_line_frequency():
    session = open_bench(sine_bench(50.0), definition_dirs=[DEFS])
    try:
        session.advance(1.0)
        without = float(scpi(session, "lia", ":MEASure:R?"))
        assert without > 0.1
        scpi(session, "lia", ":INPut:LINefrequency 50")
        scpi(session, "lia", ":INPut:NOTCh ON")
        session.advance(1.0)
        assert float(scpi(session, "lia", ":MEASure:R?")) < without * 1e-3
        scpi(session, "lia", ":INPut:NOTCh OFF")
        scpi(session, "lia", ":INPut:NOTCh:TWOx ON")
        session.advance(1.0)
        # A 100 Hz notch is 50 Hz away from the 50 Hz signal: its transfer there is 0.9978.
        assert float(scpi(session, "lia", ":MEASure:R?")) == pytest.approx(without * 0.9978, rel=1e-3)
    finally:
        session.close()


def test_the_reserve_widens_the_input_limit():
    session = open_bench(sine_bench(1000.0), definition_dirs=[DEFS])
    try:
        scpi(session, "lia", ":INPut:SENSitivity 0.002")
        scpi(session, "lia", ":INPut:REServe 10")
        session.advance(1.0)
        assert scpi(session, "lia", ":STATus:OVERload:INPut?") == "1"
        assert drain(session, "lia") == ['325,"Input overload"', '326,"Output overload"']
        # 1000 x 2 mV is past even the 1.4 V absolute input limit: the reserve has capped out.
        scpi(session, "lia", ":INPut:REServe 1000")
        assert scpi(session, "lia", ":STATus:OVERload:INPut?") == "0"
        # 100 x 2 mV = 0.283 V is below the 0.5 V peak again: the input bit comes back.
        scpi(session, "lia", ":INPut:REServe 100")
        assert scpi(session, "lia", ":STATus:OVERload:INPut?") == "1"
        assert drain(session, "lia") == ['325,"Input overload"']
    finally:
        session.close()
