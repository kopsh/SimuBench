"""The DMM measurement path's gold standard (#190, E1): replies and virtual time of fixed command
sequences on the Keysight 34465A, recorded before ``dmm_core`` was split into its real blocks.

The split (``dmm_input_terminal`` / ``dmm_conditioning`` / ``dmm_adc``) must leave every row
unchanged. ``DC`` is the bench of the manual cases: an SMU as the source. ``AC`` uses the
function generator so the AC path (rms, the bandwidth filter) is exercised too.
``tests/data/dmm_gold.json`` holds the recording in the one gold step shape (#1635); re-record it
with ``uv run python tests/record_gold.py dmm``. The reading replies carry the front end's noise
(specs.noise_ppm_of_range, #744) — the gold guards the command path, not the noise draws, so a
re-recording reproduces them from the bench seed. #286: ``:CONF?`` reports the resolution
CONFigure was given, and the resolution is a setting that persists across range changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import record_steps, require_gold, warm_up
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
GOLD_FILE = ROOT / "tests" / "data" / "dmm_gold.json"
GOLD = json.loads(GOLD_FILE.read_text()) if GOLD_FILE.exists() else {}

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


#: case name -> (the bench builder, the sequence to walk).
CASES: dict[str, tuple] = {"dc": (dc_bench, DC_SEQUENCE), "ac": (ac_bench, AC_SEQUENCE)}


def _open(bench: BenchDefinition):
    # idle: sleep is the documented deterministic tier for tests (D47): wall would bill the runner's
    # scheduling jitter into the virtual clock and the recorded 0 ns rows would flip on slow
    # machines (issue 994: 16 ms on Windows CI, as issue 621 for the load).
    session = open_bench(bench, definition_dirs=[DEFS], idle="sleep")
    # Warm up: the first command of a freshly loaded definition bills its lazy loading to the
    # wall clock, which would make the recorded times depend on the machine (D47).
    warm_up(session, "dut", "src", "awg")
    return session


@pytest.mark.parametrize(
    ("bench", "sequence", "name"), [(bench, sequence, name) for name, (bench, sequence) in CASES.items()]
)
def test_replies_and_virtual_time_are_unchanged(bench, sequence, name):
    session = _open(bench())
    try:
        got = record_steps(session, sequence)
    finally:
        session.close()
    require_gold(got, GOLD.get(name, {}).get("steps", []), f"{GOLD_FILE.name}[{name}]")


def record() -> None:
    """Re-record both benches into ``tests/data/dmm_gold.json`` (``tests/record_gold.py`` calls
    this): walk each sequence on its bench, the way the test above compares it."""
    recorded = {}
    for name, (bench, sequence) in CASES.items():
        session = _open(bench())
        try:
            recorded[name] = {"steps": record_steps(session, sequence)}
        finally:
            session.close()
    GOLD_FILE.write_text(json.dumps(recorded, indent=1) + "\n")
    print(f"wrote {GOLD_FILE.relative_to(ROOT)}")
