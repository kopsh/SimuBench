"""The lock-in amplifier's gold standard (#189, D3): messages, replies and virtual time of fixed
command sequences on ``sb-lia-1``, recorded before ``lockin_demodulator`` was split into its real
blocks and before the data buffer, AUX, offset and input configuration were added.

Two sequences: the RC bench (settling after a step, Bode points, noise at fixed virtual times, the
sensitivity ladder with its overload errors, auto gain and auto phase) and the chopper bench
(external reference and harmonics, reference loss and recovery, AC coupling against a DC
background, and the 329 of a truncated square with a wide filter). ``tests/data/lockin_gold.json``
holds the recording: commands with their replies and consumed virtual time, and clock advances.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
BENCHES = ROOT / "acceptance" / "benches"
GOLD = json.loads((ROOT / "tests" / "data" / "lockin_gold.json").read_text())


def sequence(name: str) -> dict:
    return next(s for s in GOLD["sequences"] if s["name"] == name)


@pytest.mark.parametrize("name", [s["name"] for s in GOLD["sequences"]])
def test_the_gold_standard_is_reproduced(name: str):
    bench = sequence(name)
    session = open_bench(BENCHES / bench["bench"], definition_dirs=[DEFS], clock="command", idle="off")
    try:
        # Warm up: the first command of a freshly loaded definition bills its lazy loading to the
        # wall clock (D47); that timing is not part of the amplifier's behaviour.
        session.write("lia", b"*IDN?\n")
        assert session.read("lia") is not None
        for i, step in enumerate(bench["steps"]):
            if "advance_s" in step:
                session.advance(step["advance_s"])
                continue
            before = session.now_ns
            session.write(step["instrument"], (step["cmd"] + "\n").encode())
            reply = session.read(step["instrument"])
            text = reply.decode().rstrip("\n") if reply is not None else None
            assert text == step.get("reply"), f"step {i}: {step['cmd']} replied {text!r}"
            if step.get("floats"):
                actual = [float(v) for v in (text or "").split(",")]
                expected = [float(v) for v in step["reply"].split(",")]
                assert len(actual) == len(expected), f"step {i}: {step['cmd']} count"
                for a, e in zip(actual, expected, strict=True):
                    assert a == pytest.approx(e, rel=1e-9, abs=1e-15), f"step {i}: {step['cmd']}"
            assert session.now_ns - before == step["ns"], f"step {i}: {step['cmd']} took time"
    finally:
        session.close()


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
