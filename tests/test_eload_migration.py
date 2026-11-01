"""The electronic load's gold standard (#186, C1): replies and virtual time of fixed command
sequences on ``sb-el-1``, recorded before the monolithic ``electronic_load`` component was split.

The split (input / power stage / mode loop / meter / protection) must leave every row unchanged.
The first bench has a 5 V source (CC and CR operate normally); the second a source below the
constant-current knee (``r_min`` limits the sink).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"

NORMAL_SEQUENCE: list[tuple[str, str]] = [
    ("el", "*IDN?"),
    ("el", "*RST"),
    ("el", ":SOUR:FUNC?"),
    ("el", ":SOUR:CURR?"),
    ("el", ":SOUR:RES?"),
    ("el", ":INP?"),
    ("el", ":MEAS:VOLT?"),
    ("el", ":MEAS:CURR?"),
    ("el", ":INP ON"),
    ("el", ":SOUR:CURR 0.002"),
    ("el", ":MEAS:VOLT?"),
    ("el", ":MEAS:CURR?"),
    ("el", ":MEAS:POW?"),
    ("el", ":SOUR:CURR 0.003"),
    ("el", ":MEAS:CURR?"),
    ("el", ":SOUR:FUNC CR"),
    ("el", ":SOUR:RES 1000"),
    ("el", ":MEAS:CURR?"),
    ("el", ":MEAS:POW?"),
    ("el", ":SOUR:FUNC CC"),
    ("el", ":SOUR:CURR 0.01"),
    ("el", ":MEAS:CURR?"),
    ("el", ":INP OFF"),
    ("el", ":MEAS:VOLT?"),
    ("el", ":MEAS:CURR?"),
    ("el", ":SYST:ERR?"),
]

KNEE_SEQUENCE: list[tuple[str, str]] = [
    ("el", ":INP ON"),
    ("el", ":SOUR:CURR 0.002"),
    ("el", ":MEAS:VOLT?"),
    ("el", ":MEAS:CURR?"),
    ("el", ":SYST:ERR?"),
]

#: (instrument, command, reply, ns consumed) — recorded on the electronic_load implementation.
EXPECTED_NORMAL: list[tuple[str, str, str | None, int]] = [
    ("el", "*IDN?", "SIMUBENCH,SB-EL-1,SBEL0000001,1.0.0", 0),
    ("el", "*RST", None, 0),
    ("el", ":SOUR:FUNC?", "CC", 0),
    ("el", ":SOUR:CURR?", "+0.000000E+00", 0),
    ("el", ":SOUR:RES?", "+1.000000E+03", 0),
    ("el", ":INP?", "0", 0),
    ("el", ":MEAS:VOLT?", "+5.0000000E+00", 0),
    ("el", ":MEAS:CURR?", "+0.0000000E+00", 0),
    ("el", ":INP ON", None, 0),
    ("el", ":SOUR:CURR 0.002", None, 0),
    ("el", ":MEAS:VOLT?", "+5.0000000E+00", 0),
    ("el", ":MEAS:CURR?", "+2.0000000E-03", 0),
    ("el", ":MEAS:POW?", "+1.0000000E-02", 0),
    ("el", ":SOUR:CURR 0.003", None, 0),
    ("el", ":MEAS:CURR?", "+3.0000000E-03", 0),
    ("el", ":SOUR:FUNC CR", None, 0),
    ("el", ":SOUR:RES 1000", None, 0),
    ("el", ":MEAS:CURR?", "+5.0000000E-03", 0),
    ("el", ":MEAS:POW?", "+2.5000000E-02", 0),
    ("el", ":SOUR:FUNC CC", None, 0),
    ("el", ":SOUR:CURR 0.01", None, 0),
    ("el", ":MEAS:CURR?", "+1.0000000E-02", 0),
    ("el", ":INP OFF", None, 0),
    ("el", ":MEAS:VOLT?", "+5.0000000E+00", 0),
    ("el", ":MEAS:CURR?", "+0.0000000E+00", 0),
    ("el", ":SYST:ERR?", '0,"No error"', 0),
]
EXPECTED_KNEE: list[tuple[str, str, str | None, int]] = [
    ("el", ":INP ON", None, 0),
    ("el", ":SOUR:CURR 0.002", None, 0),
    ("el", ":MEAS:VOLT?", "+4.9998638E-05", 0),
    ("el", ":MEAS:CURR?", "+1.3623607E-03", 0),
    ("el", ":SYST:ERR?", '0,"No error"', 0),
]


def bench(source_v: float, source_r: float) -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "eload-gold",
            "instruments": {"el": {"model": "sb-el-1"}},
            "parts": {
                "src": {"type": "dc_supply", "params": {"v": source_v, "r": source_r}},
            },
            "wires": [{"from": "src.OUT", "to": "el.IN"}],
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


@pytest.mark.parametrize(
    ("source_v", "source_r", "sequence", "expected", "name"),
    [
        (5.0, 1e-6, NORMAL_SEQUENCE, EXPECTED_NORMAL, "normal"),
        (5.0e-5, 1e-6, KNEE_SEQUENCE, EXPECTED_KNEE, "knee"),
    ],
)
def test_replies_and_virtual_time_are_unchanged(source_v, source_r, sequence, expected, name):
    session = open_bench(bench(source_v, source_r), definition_dirs=[DEFS])
    try:
        # Warm up the lazily loaded definition before the recorded rows (D47: the first command
        # of a fresh definition would bill its loading to the wall clock).
        session.write("el", b"*IDN?\n")
        session.read("el")
        got = run_sequence(session, sequence)
    finally:
        session.close()
    if not expected:
        for row in got:
            print(repr(row))
        pytest.fail(f"EXPECTED_{name.upper()} is empty: paste the output above")
    assert got == expected
