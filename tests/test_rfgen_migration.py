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
virtual time, advances of the virtual clock, and the lines the net solver finds on the two outputs.
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
GOLD = json.loads((ROOT / "tests" / "data" / "rfgen_gold.json").read_text())


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
        session.write("rf", b"*IDN?\n")
        assert session.read("rf") is not None
        for i, step in enumerate(GOLD["steps"]):
            if "advance_s" in step:
                session.advance(step["advance_s"])
                continue
            if "signal" in step:
                merged = session.net_signal(session.port_net[step["signal"]]).merged()
                order = np.argsort(merged.freq, kind="stable")
                actual = np.array(
                    [
                        [float(f), float(a.real), float(a.imag)]
                        for f, a in zip(merged.freq[order], merged.amp[order], strict=True)
                    ]
                ).reshape(-1, 3)
                expected = np.array(step["lines"], dtype=float).reshape(-1, 3)
                assert actual.shape == expected.shape, f"step {i} ({step['signal']}): shape"
                np.testing.assert_allclose(
                    actual[:, 0],
                    expected[:, 0],
                    rtol=1e-9,
                    atol=1e-6,
                    err_msg=f"step {i} ({step['signal']}): freq",
                )
                np.testing.assert_allclose(
                    actual[:, 1:],
                    expected[:, 1:],
                    rtol=1e-9,
                    atol=1e-12,
                    err_msg=f"step {i} ({step['signal']}): amp",
                )
                continue
            before = session.now_ns
            session.write(step["instrument"], (step["cmd"] + "\n").encode())
            reply = session.read(step["instrument"])
            text = reply.decode().rstrip("\n") if reply is not None else None
            assert text == step.get("reply"), f"step {i}: {step['cmd']} replied {text!r}"
            assert session.now_ns - before == step["ns"], f"step {i}: {step['cmd']} took time"
    finally:
        session.close()


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
