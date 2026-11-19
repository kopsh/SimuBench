"""The electronic load's gold standard (#186, C1): replies and virtual time of fixed command
sequences on ``sb-el-1``, recorded before the monolithic ``electronic_load`` component was split.

The split (input / power stage / mode loop / meter / protection) must leave every row unchanged.
The first bench has a 5 V source (CC and CR operate normally); the second a source below the
constant-current knee (``r_min`` limits the sink). ``tests/data/eload_gold.json`` holds the
recording in the one gold step shape (#1635); re-record it with
``uv run python tests/record_gold.py eload``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import record_steps, require_gold
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
GOLD_FILE = ROOT / "tests" / "data" / "eload_gold.json"
GOLD = json.loads(GOLD_FILE.read_text()) if GOLD_FILE.exists() else {}

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

#: case name -> (the source the bench faces, its resistance, the sequence to walk).
CASES: dict[str, tuple[float, float, list[tuple[str, str]]]] = {
    "normal": (5.0, 1e-6, NORMAL_SEQUENCE),
    "knee": (5.0e-5, 1e-6, KNEE_SEQUENCE),
}


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
            # idle: sleep is the documented deterministic tier for tests (D47): wall would bill the
            # runner's scheduling jitter into the virtual clock and the recorded 0 ns rows would
            # flip on slow machines (issue 621, 15-16 ms on Windows CI).
            "run": {"clock": "command", "seed": 1, "idle": "sleep"},
        }
    )


@pytest.mark.parametrize(
    ("source_v", "source_r", "sequence", "name"),
    [(source_v, source_r, sequence, name) for name, (source_v, source_r, sequence) in CASES.items()],
)
def test_replies_and_virtual_time_are_unchanged(source_v, source_r, sequence, name):
    session = open_bench(bench(source_v, source_r), definition_dirs=[DEFS])
    try:
        # Under sleep there is no wall-clock idle to bill, so no warm-up is needed before the
        # recorded rows: the first command loads the definition without moving the clock (D47).
        got = record_steps(session, sequence)
    finally:
        session.close()
    require_gold(got, GOLD.get(name, {}).get("steps", []), f"{GOLD_FILE.name}[{name}]")


def record() -> None:
    """Re-record both benches into ``tests/data/eload_gold.json`` (``tests/record_gold.py`` calls
    this): walk each sequence on its bench, the way the test above compares it."""
    recorded = {}
    for name, (source_v, source_r, sequence) in CASES.items():
        session = open_bench(bench(source_v, source_r), definition_dirs=[DEFS])
        try:
            recorded[name] = {"steps": record_steps(session, sequence)}
        finally:
            session.close()
    GOLD_FILE.write_text(json.dumps(recorded, indent=1) + "\n")
    print(f"wrote {GOLD_FILE.relative_to(ROOT)}")
