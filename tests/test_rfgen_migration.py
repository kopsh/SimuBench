"""The RF signal generator's gold standard (#188, D2): messages, replies, virtual time and the
spectral lines on RF_OUT and REF_OUT of a fixed command sequence on ``sb-sg-1``, recorded before
the monolithic ``rf_generator_core`` component was split.

The sequence covers the carrier and level, AM, FM and PM to Carson's rule, the line budget (the
-222 refusal rolls the causing setting back), pulse modulation and out-of-range refusals, the list
and stepped sweeps, the reference output switch and the hidden timebase error (the oscillator is
2 ppm fast: ``:FREQ?`` reads the set value while every line on the wire carries the error). The
split (reference / synthesizer / modulation source / modulator / level / attenuator / output) must
reproduce every row, every time and every line.

``tests/data/rfgen_gold.json`` holds the recording: commands with their replies and consumed
virtual time, advances of the virtual clock, and the lines the net solver finds on the two outputs
— the one gold step shape (#1635), re-recorded with ``uv run python tests/record_gold.py rfgen``
(the committed steps drive the walk, their actual values replace the recorded ones).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import record_gold_step, run_gold_step, warm_up
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
GOLD_FILE = ROOT / "tests" / "data" / "rfgen_gold.json"
GOLD = json.loads(GOLD_FILE.read_text())


def bench() -> BenchDefinition:
    """The validation bench (validation-rfgen.md): the generator into a spectrum analyzer with
    REF_OUT to REF_IN, its timebase 2 ppm fast (hidden), on the command clock with idle time off
    so commands and virtual time are reproducible (D47)."""
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "rfgen-gold",
            "instruments": {
                "rf": {
                    "model": "sb-sg-1",
                    "state": {
                        "freq.cw": 1e9,
                        "power.level": 0.0,
                        "output.enabled": True,
                        "am.depth": 30.0,
                        "am.rate": 1e6,
                    },
                    "hidden": {"reference": {"ppm": 2.0}},
                },
                "sa": {
                    "model": "sb-sa-1",
                    "state": {
                        "freq.center": 1e9,
                        "freq.span": 5e6,
                        "display.ref_level": 10.0,
                        "marker.threshold": -60.0,
                    },
                },
            },
            "wires": [
                {"from": "rf.RF_OUT", "to": "sa.RF_IN"},
                {"from": "rf.REF_OUT", "to": "sa.REF_IN"},
            ],
            "run": {"seed": GOLD["seed"], "clock": "command", "idle": "off"},
        }
    )


def test_the_gold_standard_is_reproduced():
    session = open_bench(bench(), definition_dirs=[DEFS])
    try:
        # Warm up: the first command of a freshly loaded definition bills its lazy loading to the
        # wall clock (D47); that timing is not part of the generator's behaviour.
        assert warm_up(session, "rf")[0] is not None
        for i, step in enumerate(GOLD["steps"]):
            run_gold_step(session, i, step)
    finally:
        session.close()


def record() -> None:
    """Re-record the sequence into ``tests/data/rfgen_gold.json`` (``tests/record_gold.py`` calls
    this): the committed steps drive the walk, the way the test above judges them, and their
    actual values replace the recorded ones."""
    recorded = json.loads(GOLD_FILE.read_text())
    session = open_bench(bench(), definition_dirs=[DEFS])
    try:
        warm_up(session, "rf")
        recorded["steps"] = [record_gold_step(session, i, step) for i, step in enumerate(recorded["steps"])]
    finally:
        session.close()
    GOLD_FILE.write_text(json.dumps(recorded, indent=1) + "\n")
    print(f"wrote {GOLD_FILE.relative_to(ROOT)}")


def test_the_gold_covers_the_whole_generator():
    """A guard on the recording itself: it must exercise the modulation paths, the refusals, the
    sweeps, the reference and the hidden timebase, not just one carrier setting."""
    commands = [s["cmd"] for s in GOLD["steps"] if "cmd" in s]
    signals = [s["signal"] for s in GOLD["steps"] if "signal" in s]
    assert {":SOUR:AM:STAT ON", ":SOUR:FM:STAT ON", ":SOUR:PM:STAT ON"} <= set(commands)
    assert any("LIST:FREQ" in c for c in commands) and any("SWE:POIN" in c for c in commands)
    assert any("ROSC:OUTP" in c for c in commands) and any("ROSC:SOUR" in c for c in commands)
    assert any("modulation index 1e+08" in s.get("reply", "") for s in GOLD["steps"])
    assert any("pulse modulation is not simulated" in s.get("reply", "") for s in GOLD["steps"])
    assert "rf.RF_OUT" in signals and "rf.REF_OUT" in signals
    # The hidden timebase: the recorded line on RF_OUT is 2 ppm above the set 1 GHz.
    cw = next(s for s in GOLD["steps"] if s.get("signal") == "rf.RF_OUT")
    assert cw["lines"][0][0] == pytest.approx(1_000_002_000.0)
    ref = next(s for s in GOLD["steps"] if s.get("signal") == "rf.REF_OUT")
    assert ref["lines"][0][0] == pytest.approx(10_000_020.0)
    assert all(s.get("ns", 0) == 0 for s in GOLD["steps"] if "cmd" in s)
