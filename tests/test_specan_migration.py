"""The spectrum analyzer's gold standard (#187, D1): messages, replies, virtual time and trace data
of a fixed command sequence on ``sb-sa-1``, recorded before the monolithic ``spectrum_analyzer``
component was split.

The sequence covers the receiver chain: setting queries and the auto couplings (RBW, VBW, sweep
time, attenuation), a marker peak walk, the detector and trace modes, max hold and averaging across
sweeps, a single frozen sweep and the reference clock switch. The split (input / mixer / IF /
detector / VBW / trace) must reproduce every row and every trace point.

``tests/data/specan_gold.json`` holds the recording: commands with their replies and consumed
virtual time, advances of the virtual clock, and the 1001-point traces the display shows.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
GOLD = json.loads((ROOT / "tests" / "data" / "specan_gold.json").read_text())


def bench() -> BenchDefinition:
    """The validation bench (validation-specan.md): a 100 kHz square and a 2 ppm-fast 10 MHz
    reference, on the command clock and a fixed seed so noise is reproducible."""
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "specan-gold",
            "instruments": {
                "awg": {
                    "model": "sb-awg-2",
                    "state": {
                        "ch1.function": "SQUare",
                        "ch1.frequency": 100000.0,
                        "ch1.amplitude": 1.0,
                        "ch1.output": True,
                    },
                },
                "ref": {
                    "model": "sb-awg-2",
                    "state": {
                        "ch1.frequency": 10000020.0,
                        "ch1.amplitude": 1.0,
                        "ch1.output": True,
                    },
                },
                "sa": {
                    "model": "sb-sa-1",
                    "state": {
                        "freq.center": 500000.0,
                        "freq.span": 1000000.0,
                        "display.ref_level": 10.0,
                        "rosc.source": "EXTernal",
                    },
                },
            },
            "wires": [
                {"from": "awg.OUT1", "to": "sa.RF_IN"},
                {"from": "ref.OUT1", "to": "sa.REF_IN"},
            ],
            "run": {"seed": GOLD["seed"], "clock": "command"},
        }
    )


def test_the_gold_standard_is_reproduced():
    session = open_bench(bench(), definition_dirs=[DEFS])
    try:
        # Warm up: the first command of a freshly loaded definition bills its lazy loading to the
        # wall clock (D47); that timing is not part of the analyzer's behaviour.
        session.write("sa", b"*IDN?\n")
        assert session.read("sa") is not None
        for i, step in enumerate(GOLD["steps"]):
            if "advance_s" in step:
                session.advance(step["advance_s"])
                continue
            if "trace" in step:
                session.write("sa", b":TRAC:DATA?\n")
                text = session.read("sa").decode().rstrip("\n")
                actual = np.array([float(x) for x in text.split(",")])
                expected = np.array(step["values"])
                assert actual.shape == expected.shape, f"step {i} ({step['trace']}): shape"
                np.testing.assert_allclose(
                    actual, expected, rtol=1e-9, atol=1e-12, err_msg=f"step {i} ({step['trace']})"
                )
                continue
            before = session.now_ns
            session.write(step["instrument"], (step["cmd"] + "\n").encode())
            reply = session.read(step["instrument"])
            text = reply.decode().rstrip("\n") if reply is not None else None
            assert text == step["reply"], f"step {i}: {step['cmd']} replied {text!r}"
            assert session.now_ns - before == step["ns"], f"step {i}: {step['cmd']} took time"
    finally:
        session.close()


def test_the_gold_covers_the_whole_receiver_chain():
    """A guard on the recording itself: it must exercise the couplings, all trace modes and the
    reference switch, not just one sweep."""
    commands = [s["cmd"] for s in GOLD["steps"] if "cmd" in s]
    traces = {s["trace"] for s in GOLD["steps"] if "trace" in s}
    assert any("BAND:RES" in c for c in commands)
    assert any("POW:RF:ATT" in c for c in commands)
    assert any("ROSC:SOUR" in c for c in commands)
    assert {":TRAC:TYPE MAXH", ":TRAC:TYPE AVER"} <= set(commands)
    assert {"max hold after two sweeps", "average", "frozen single", "after trace clear"} <= traces
    assert GOLD["steps"][-2] == {"instrument": "sa", "cmd": ":SYST:ERR?", "reply": '0,"No error"', "ns": 0}
    assert pytest.approx(10e9) == next(s["ns"] for s in GOLD["steps"] if s.get("cmd") == ":INIT")
