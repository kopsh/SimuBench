"""The lock-in amplifier's gold standard (#189, D3): messages, replies and virtual time of fixed
command sequences on ``sb-lia-1``, recorded before ``lockin_demodulator`` was split into its real
blocks and before the data buffer, AUX, offset and input configuration were added.

Two sequences: the RC bench (settling after a step, Bode points, noise at fixed virtual times, the
sensitivity ladder with its overload errors, auto gain and auto phase) and the chopper bench
(external reference and harmonics, reference loss and recovery, AC coupling against a DC
background, and the 329 of a truncated square with a wide filter). ``tests/data/lockin_gold.json``
holds the recording: commands with their replies and consumed virtual time, and clock advances —
the one gold step shape (#1635), re-recorded with ``uv run python tests/record_gold.py lockin``
(the committed steps drive the walk, their actual values replace the recorded ones).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from helpers import record_gold_step, run_gold_step, warm_up
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
BENCHES = ROOT / "acceptance" / "benches"
GOLD_FILE = ROOT / "tests" / "data" / "lockin_gold.json"
GOLD = json.loads(GOLD_FILE.read_text())


def sequence(name: str) -> dict:
    return next(s for s in GOLD["sequences"] if s["name"] == name)


@pytest.mark.parametrize("name", [s["name"] for s in GOLD["sequences"]])
def test_the_gold_standard_is_reproduced(name: str):
    bench = sequence(name)
    session = open_bench(BENCHES / bench["bench"], definition_dirs=[DEFS], clock="command", idle="off")
    try:
        # Warm up: the first command of a freshly loaded definition bills its lazy loading to the
        # wall clock (D47); that timing is not part of the amplifier's behaviour.
        assert warm_up(session, "lia")[0] is not None
        for i, step in enumerate(bench["steps"]):
            run_gold_step(session, i, step)
    finally:
        session.close()


def record() -> None:
    """Re-record both sequences into ``tests/data/lockin_gold.json`` (``tests/record_gold.py``
    calls this): the committed steps drive the walk on their bench, the way the test above judges
    them, and their actual values replace the recorded ones."""
    recorded = json.loads(GOLD_FILE.read_text())
    for seq in recorded["sequences"]:
        session = open_bench(BENCHES / seq["bench"], definition_dirs=[DEFS], clock="command", idle="off")
        try:
            warm_up(session, "lia")
            seq["steps"] = [record_gold_step(session, i, step) for i, step in enumerate(seq["steps"])]
        finally:
            session.close()
    GOLD_FILE.write_text(json.dumps(recorded, indent=1) + "\n")
    print(f"wrote {GOLD_FILE.relative_to(ROOT)}")


def test_the_gold_covers_the_whole_amplifier():
    """A guard on the recording itself: both benches, the filter, the noise, the errors and the
    auto functions, not just one steady state."""
    rc = sequence("rc")
    chopper = sequence("chopper")
    cmds = [s["cmd"] for s in rc["steps"] if "cmd" in s]
    other = [s["cmd"] for s in chopper["steps"] if "cmd" in s]
    assert {":FILTer:SLOPe 6", ":FILTer:SLOPe 12", ":FILTer:SLOPe 18", ":FILTer:SLOPe 24"} <= set(cmds)
    assert ":AUTO:GAIN" in cmds and ":AUTO:PHASe" in cmds
    assert any("Reference unlock" in s.get("reply", "") for s in chopper["steps"])
    assert any("Input overload" in s.get("reply", "") for s in rc["steps"])
    assert any("harmonic 51 x 10000 Hz" in s.get("reply", "") for s in chopper["steps"])
    assert any("Input beyond the model" in s.get("reply", "") for s in chopper["steps"])
    assert ":INPut:COUPling AC" in other and ":REFerence:HARMonic 3" in other
    # The noise at a fixed virtual time is recorded twice: the same instant reads the same value.
    xs = [s for s in rc["steps"] if s.get("cmd") == ":MEASure:X?"]
    assert len(xs) >= 3 and xs[0]["reply"] == xs[1]["reply"]
    assert all(s.get("ns", 0) == 0 for s in rc["steps"] + chopper["steps"] if "cmd" in s)


def test_the_recorded_r_values_match_the_circuit():
    """A reading of the recording itself: the RC bench's settled points are the hand-computed
    table of validation-lockin.md (D13's circuit), so the gold is anchored to physics."""
    snaps = [s for s in sequence("rc")["steps"] if s.get("cmd") == ":MEASure:SNAP? X,Y,R,THETa"]
    r_thetas = [(float(s["reply"].split(",")[2]), float(s["reply"].split(",")[3])) for s in snaps[:4]]
    table = [(498.811, -3.77), (417.295, -33.41), (353.512, -44.99), (37.786, -85.67)]
    for (r, theta), (r_mv, th) in zip(r_thetas, table, strict=True):
        assert r * 1e3 == pytest.approx(r_mv, abs=0.01)
        assert theta == pytest.approx(th, abs=0.01)
    assert math.isclose(GOLD["seed"], 5)
