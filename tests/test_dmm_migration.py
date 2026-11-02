"""The DMM measurement path's gold standard (#190, E1): replies and virtual time of fixed command
sequences on the Keysight 34465A, recorded before ``dmm_core`` was split into its real blocks.

The split (``dmm_input_terminal`` / ``dmm_conditioning`` / ``dmm_adc``) must leave every row
unchanged. ``DC`` is the bench of the manual cases: an SMU as the source. ``AC`` uses the
function generator so the AC path (rms, the bandwidth filter) is exercised too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"

DC_SEQUENCE: list[tuple[str, str]] = [
    ("dut", "*IDN?"),
    ("dut", "*RST"),
    ("dut", ":CONF:VOLT:DC 10,0.001"),
    ("dut", ":CONF?"),
    ("dut", ":SENS:VOLT:DC:NPLCycles 1"),
    ("src", ":SOUR:VOLT 5"),
    ("dut", ":MEAS:VOLT:DC?"),
    ("dut", ":SENS:VOLT:DC:NPLCycles 10"),
    ("dut", ":MEAS:VOLT:DC?"),
    ("dut", ":SENS:VOLT:DC:RANGe 1"),
    ("dut", ":CONF?"),
    ("dut", ":SENS:VOLT:DC:RANGe:AUTO ON"),
    ("src", ":SOUR:VOLT 0.2"),
    ("dut", ":MEAS:VOLT:DC?"),
    ("dut", ":SENS:VOLT:DC:RANGe 0.1"),
    ("dut", ":SENS:VOLT:DC:RANGe:AUTO OFF"),
    ("src", ":SOUR:VOLT 0.12"),
    ("dut", ":MEAS:VOLT:DC?"),
    ("src", ":SOUR:VOLT 0.13"),
    ("dut", ":MEAS:VOLT:DC?"),
    ("src", ":SOUR:VOLT 2"),
    ("dut", ":SENS:VOLT:DC:RANGe 10"),
    ("dut", ":SENS:VOLT:DC:IMPedance:AUTO ON"),
    ("dut", ":MEAS:VOLT:DC?"),
    ("dut", ":SENS:VOLT:DC:IMPedance:AUTO?"),
    ("dut", ":SENS:VOLT:DC:IMPedance:AUTO OFF"),
    ("dut", ':FUNC "VOLT:AC"'),
    ("dut", ":MEAS:VOLT:AC?"),
    ("dut", ':FUNC "VOLT"'),
    ("dut", ":TRIG:SOUR IMM;:TRIG:COUN 2;:SAMP:COUN 3;:INIT"),
    ("dut", "*OPC?"),
    ("dut", ":FETC?"),
    ("dut", ":DATA:POIN?"),
    ("dut", ":SYST:ERR?"),
]

AC_SEQUENCE: list[tuple[str, str]] = [
    ("dut", "*RST"),
    ("awg", ":SOUR1:FREQ 50"),
    ("dut", ':FUNC "VOLT:AC"'),
    ("dut", ":SENS:VOLT:AC:BANDwidth 20"),
    ("dut", ":MEAS:VOLT:AC?"),
    ("dut", ":SENS:VOLT:AC:BANDwidth 200"),
    ("dut", ":MEAS:VOLT:AC?"),
    ("dut", ":SENS:VOLT:AC:BANDwidth 3"),
    ("dut", ":MEAS:VOLT:AC?"),
    ("dut", ":SENS:VOLT:AC:RANGe 0.1"),
    ("dut", ":SENS:VOLT:AC:RANGe:AUTO OFF"),
    ("dut", ":MEAS:VOLT:AC?"),
    ("dut", ":SENS:VOLT:AC:RANGe 10"),
    ("dut", ":MEAS:VOLT:AC?"),
    ("dut", ":SYST:ERR?"),
]

#: (instrument, command, reply, ns consumed) — recorded on the dmm_core implementation.
EXPECTED_DC: list[tuple[str, str, str | None, int]] = [
    ("dut", "*IDN?", "Keysight Technologies,34465A,MY57440001,2.6.1-2.0.0-0.0.0-0.0-0-0", 0),
    ("dut", "*RST", None, 0),
    ("dut", ":CONF:VOLT:DC 10,0.001", None, 0),
    # #286: the resolution CONFigure was given is now reflected (was +1.00000000E-06, the NPLC step).
    ("dut", ":CONF?", '"VOLT +1.00000000E+01,+1.00000000E-03"', 0),
    ("dut", ":SENS:VOLT:DC:NPLCycles 1", None, 0),
    ("src", ":SOUR:VOLT 5", None, 0),
    ("dut", ":MEAS:VOLT:DC?", "+5.00000100E+00", 0),
    ("dut", ":SENS:VOLT:DC:NPLCycles 10", None, 0),
    ("dut", ":MEAS:VOLT:DC?", "+5.00000000E+00", 0),
    ("dut", ":SENS:VOLT:DC:RANGe 1", None, 0),
    # The resolution CONFigure gave is a setting: it persists across range changes (#286), so
    # CONF? still reports it (was +1.00000000E-07, range × NPLC step).
    ("dut", ":CONF?", '"VOLT +1.00000000E+00,+1.00000000E-03"', 0),
    ("dut", ":SENS:VOLT:DC:RANGe:AUTO ON", None, 0),
    ("src", ":SOUR:VOLT 0.2", None, 0),
    ("dut", ":MEAS:VOLT:DC?", "+2.00000000E-01", 0),
    ("dut", ":SENS:VOLT:DC:RANGe 0.1", None, 0),
    ("dut", ":SENS:VOLT:DC:RANGe:AUTO OFF", None, 0),
    ("src", ":SOUR:VOLT 0.12", None, 0),
    ("dut", ":MEAS:VOLT:DC?", "+9.90000000E+37", 0),
    ("src", ":SOUR:VOLT 0.13", None, 0),
    ("dut", ":MEAS:VOLT:DC?", "+9.90000000E+37", 0),
    ("src", ":SOUR:VOLT 2", None, 0),
    ("dut", ":SENS:VOLT:DC:RANGe 10", None, 0),
    ("dut", ":SENS:VOLT:DC:IMPedance:AUTO ON", None, 0),
    ("dut", ":MEAS:VOLT:DC?", "+2.00000000E+00", 0),
    ("dut", ":SENS:VOLT:DC:IMPedance:AUTO?", "1", 0),
    ("dut", ":SENS:VOLT:DC:IMPedance:AUTO OFF", None, 0),
    ("dut", ':FUNC "VOLT:AC"', None, 0),
    ("dut", ":MEAS:VOLT:AC?", "+0.00000000E+00", 0),
    ("dut", ':FUNC "VOLT"', None, 0),
    ("dut", ":TRIG:SOUR IMM;:TRIG:COUN 2;:SAMP:COUN 3;:INIT", None, 0),
    ("dut", "*OPC?", "1", 2400960000),
    (
        "dut",
        ":FETC?",
        "+2.00000000E+00,+2.00000000E+00,+2.00000000E+00,+2.00000000E+00,+2.00000000E+00,+2.00000000E+00",
        0,
    ),
    ("dut", ":DATA:POIN?", "6", 0),
    ("dut", ":SYST:ERR?", '+0,"No error"', 0),
]
EXPECTED_AC: list[tuple[str, str, str | None, int]] = [
    ("dut", "*RST", None, 0),
    ("awg", ":SOUR1:FREQ 50", None, 0),
    ("dut", ':FUNC "VOLT:AC"', None, 0),
    ("dut", ":SENS:VOLT:AC:BANDwidth 20", None, 0),
    ("dut", ":MEAS:VOLT:AC?", "+7.07103200E-02", 0),
    ("dut", ":SENS:VOLT:AC:BANDwidth 200", None, 0),
    ("dut", ":MEAS:VOLT:AC?", "+0.00000000E+00", 0),
    ("dut", ":SENS:VOLT:AC:BANDwidth 3", None, 0),
    ("dut", ":MEAS:VOLT:AC?", "+7.07103200E-02", 0),
    ("dut", ":SENS:VOLT:AC:RANGe 0.1", None, 0),
    ("dut", ":SENS:VOLT:AC:RANGe:AUTO OFF", None, 0),
    ("dut", ":MEAS:VOLT:AC?", "+7.07103200E-02", 0),
    ("dut", ":SENS:VOLT:AC:RANGe 10", None, 0),
    ("dut", ":MEAS:VOLT:AC?", "+7.07100000E-02", 0),
    ("dut", ":SYST:ERR?", '+0,"No error"', 0),
]


def dc_bench() -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "dmm-dc-gold",
            "instruments": {
                "src": {
                    "model": "sb-smu-1",
                    "state": {"source.voltage.level": 0.0, "output.enabled": True},
                },
                "dut": {"model": "keysight-34465a"},
            },
            "wires": [{"from": "src.HI", "to": "dut.IN"}],
            "run": {"clock": "command", "seed": 1},
        }
    )


def ac_bench() -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "dmm-ac-gold",
            "instruments": {
                "awg": {
                    "model": "sb-awg-2",
                    "state": {
                        "ch1.function": "SINusoid",
                        "ch1.frequency": 1000.0,
                        "ch1.amplitude": 0.1,
                        "ch1.output": True,
                    },
                },
                "dut": {"model": "keysight-34465a"},
            },
            "wires": [{"from": "awg.OUT1", "to": "dut.IN"}],
            "run": {"clock": "command", "seed": 1},
        }
    )


def run_sequence(session, sequence: list[tuple[str, str]]) -> list[tuple[str, str, str | None, int]]:
    out = []
    for instrument, cmd in sequence:
        before = session.now_ns
        session.write(instrument, (cmd + "\n").encode())
        reply = session.read(instrument)
        text = reply.decode().rstrip("\n") if reply is not None else None
        out.append((instrument, cmd, text, session.now_ns - before))
    return out


def _open(bench: BenchDefinition):
    session = open_bench(bench, definition_dirs=[DEFS])
    # Warm up: the first command of a freshly loaded definition bills its lazy loading to the
    # wall clock, which would make the recorded times depend on the machine (D47).
    for instrument in ("dut", "src", "awg"):
        if instrument in session.instruments:
            session.write(instrument, b"*IDN?\n")
            session.read(instrument)
    return session


@pytest.mark.parametrize(
    ("bench", "sequence", "expected", "name"),
    [(dc_bench, DC_SEQUENCE, EXPECTED_DC, "dc"), (ac_bench, AC_SEQUENCE, EXPECTED_AC, "ac")],
)
def test_replies_and_virtual_time_are_unchanged(bench, sequence, expected, name):
    session = _open(bench())
    try:
        got = run_sequence(session, sequence)
    finally:
        session.close()
    if not expected:
        for row in got:
            print(repr(row))
        pytest.fail(f"EXPECTED_{name.upper()} is empty: paste the output above")
    assert got == expected
