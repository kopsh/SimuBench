"""The spectrum analyzer's gold standard (#187, D1): messages, replies, virtual time and trace data
of a fixed command sequence on ``sb-sa-1``, recorded before the monolithic ``spectrum_analyzer``
component was split.

The sequence covers the receiver chain: setting queries and the auto couplings (RBW, VBW, sweep
time, attenuation), a marker peak walk, the detector and trace modes, max hold and averaging across
sweeps, a single frozen sweep and the reference clock switch. The split (input / mixer / IF /
detector / VBW / trace) must reproduce every row and every trace point.

``tests/data/specan_gold.json`` holds the recording: commands with their replies and consumed
virtual time, advances of the virtual clock, and the 1001-point traces the display shows.

The two real models (#948, signal-chain.md 5.5): ``keysight-n9320b`` and ``keysight-n9042b`` on
their example benches walk the example script's commands, then every detector the model offers ×
the trace modes, RBW and attenuation automatic and manual, the marker searches and the counter, a
single sweep, and an overloaded input. ``tests/data/specan_models_gold.json`` holds, per step, the
SHA-256 of the reply (numbers rounded, see ``_digest``) and the virtual time the command consumed, then the screen's
readings — recorded before the front end's blocks pass signals between them (#952), which must not
change one byte. Neither model has a reference-oscillator command; the reference switch is in the
``sb-sa-1`` recording above. Neither reports an overload over SCPI: the screen's flag is recorded.

Record the models again (only when a change to the replies is intended and reviewed):
``uv run python tests/test_specan_migration.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from helpers import run_gold_step, warm_up
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
BENCHES = ROOT / "examples" / "benches"
GOLD = json.loads((ROOT / "tests" / "data" / "specan_gold.json").read_text())
MODELS_FILE = ROOT / "tests" / "data" / "specan_models_gold.json"
MODELS_GOLD: dict[str, list] = json.loads(MODELS_FILE.read_text()) if MODELS_FILE.exists() else {}


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
            "run": {"seed": GOLD["seed"], "clock": "command", "idle": "off"},
        }
    )


def test_the_gold_standard_is_reproduced():
    session = open_bench(bench(), definition_dirs=[DEFS])
    try:
        # Warm up: the first command of a freshly loaded definition bills its lazy loading to the
        # wall clock (D47); that timing is not part of the analyzer's behaviour.
        assert warm_up(session, "sa")[0] is not None
        for i, step in enumerate(GOLD["steps"]):
            handled, _ = run_gold_step(session, i, step)
            if not handled and "trace" in step:
                session.write("sa", b":TRAC:DATA?\n")
                text = session.read("sa").decode().rstrip("\n")
                actual = np.array([float(x) for x in text.split(",")])
                expected = np.array(step["values"])
                assert actual.shape == expected.shape, f"step {i} ({step['trace']}): shape"
                np.testing.assert_allclose(
                    actual, expected, rtol=1e-9, atol=1e-12, err_msg=f"step {i} ({step['trace']})"
                )
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


def test_power_on_is_calibrated_and_the_screen_reads_the_coupled_rbw():
    """#279/#345: at power-on RBW, VBW and sweep time are coupled — the sweep is not short of what
    the RBW needs (no UNCAL), and the screen's RBW is the one the instrument reports."""
    session = open_bench(bench(), definition_dirs=[DEFS])
    try:

        def q(cmd: str) -> str:
            session.write("sa", cmd.encode() + b"\n")
            out = session.read("sa")
            return out.decode().strip() if out else ""

        assert q(":SWE:TIME:UNC?") == "0"
        values = session.instruments["sa"].components["sa"].readings()["values"]
        assert values["uncal"] is False
        assert values["rbw"] == pytest.approx(float(q(":BAND?")))
        assert values["rbw"] != pytest.approx(3e6)  # not the stored manual RBW
    finally:
        session.close()


# ---- the two real models (#948) ---------------------------------------------------------------------

_READ = [":CALC:MARK1:MAX", ":CALC:MARK1:X?", ":CALC:MARK1:Y?", ":TRAC:DATA? TRACE1"]


def _modes(detectors: list[str], modes: list[str], detector: str, mode: str) -> list[str]:
    """Every detector × every trace mode: two sweeps pass in each, so hold and average have
    something to combine."""
    out = []
    for d in detectors:
        out.append(detector.format(d))
        for m in modes:
            out += [mode.format(m), "advance> 0.25", ":TRAC:DATA? TRACE1"]
    return out


_N9320B = [
    # the example script (examples/scripts/keysight_n9320b_measure.py)
    "*RST",
    ":FREQ:CENT 10000000.0",
    ":FREQ:SPAN 2000000.0",
    ":BAND:RES:AUTO ON",
    ":BAND:VID:AUTO ON",
    ":SWE:TIME:AUTO ON",
    ":DISP:WIND:TRAC:Y:RLEV 0",
    ":CALC:MARK:PEAK:THR -30",
    ":CALC:MARK:PEAK:EXC 6",
    ":CALC:MARK:MAX",
    ":CALC:MARK:X?",
    ":CALC:MARK:Y?",
    ":CALC:MARK:FCO ON",
    ":CALC:MARK:FCO:X?",
    ":TRAC:DATA? TRACE1",
    ":SYST:ERR?",
    # the couplings as they stand
    ":BAND?",
    ":BAND:VID?",
    ":SWE:TIME?",
    ":POW:ATT?",
    *_modes(["POS", "SAMP", "AVER", "RMS"], ["WRIT", "AVER", "MAXH"], ":DET {}", ":TRAC1:MODE {}"),
    ":TRAC1:MODE WRIT",
    ":DET POS",
    # RBW and VBW manual, then automatic again
    ":BAND 10000",
    ":BAND:VID 1000",
    ":SWE:TIME?",
    *_READ,
    ":BAND:RES:AUTO ON",
    ":BAND:VID:AUTO ON",
    # attenuation manual, then automatic at another reference level
    ":POW:ATT 30",
    ":POW:ATT?",
    *_READ,
    ":POW:ATT:AUTO ON",
    ":DISP:WIND:TRAC:Y:RLEV 10",
    ":POW:ATT?",
    *_READ,
    # markers: the searches, a delta pair and the counter
    "awg> C1:BSWV WVTP,SQUARE",
    ":FREQ:STAR 5000000.0",
    ":FREQ:STOP 75000000.0",
    ":CALC:MARK1:MAX",
    ":CALC:MARK1:X?",
    ":CALC:MARK1:MAX:NEXT",
    ":CALC:MARK1:X?",
    ":CALC:MARK1:Y?",
    ":CALC:MARK1:MAX:RIGH",
    ":CALC:MARK1:X?",
    ":CALC:MARK1:MAX:LEFT",
    ":CALC:MARK1:X?",
    ":CALC:MARK1:FCO:X?",
    ":CALC:MARK1:MODE DELT",
    ":CALC:MARK1:MAX:NEXT",
    ":CALC:MARK1:X:DELT?",
    ":CALC:MARK1:Y?",
    ":CALC:MARK1:MODE POS",
    ":CALC:MARK1:MIN",
    ":CALC:MARK1:Y?",
    ":CALC:MARK:AOFF",
    ":CALC:MARK1:Y?",
    ":SYST:ERR?",
    # a single sweep, averaged
    ":TRAC1:MODE AVER",
    ":AVER:COUN 4",
    ":INIT:CONT OFF",
    ":INIT",
    ":TRAC:DATA? TRACE1",
    ":INIT:CONT ON",
    ":TRAC1:MODE WRIT",
    # overload: 20 Vpp into the mixer at 0 dB
    "awg> C1:BSWV AMP,20",
    ":POW:ATT 0",
    ":TRAC:DATA? TRACE1",
    ":SYST:ERR?",
    ":SYST:ERR?",
]

_N9042B = [
    # the example script (examples/scripts/keysight_n9042b_measure.py)
    "*RST",
    ":INIT:CONT OFF",
    ":FREQ:CENT 5800000000.0",
    ":FREQ:SPAN 1000000.0",
    ":BAND:AUTO ON;:BAND:VID:AUTO ON;:SWE:TIME:AUTO ON",
    ":DISP:WIND:TRAC:Y:RLEV 0 dBm",
    ":DET:TRAC1 POS",
    ":TRAC1:TYPE WRIT",
    ":FORM:DATA ASC",
    ":BAND?",
    ":SWE:TIME?",
    ":INIT:IMM",
    "*OPC?",
    ":CALC:MARK1:MODE POS",
    ":CALC:MARK1:MAX",
    ":CALC:MARK1:X?",
    ":CALC:MARK1:Y?",
    ":CALC:MARK1:FCO ON",
    ":CALC:MARK1:FCO:X?",
    ":TRAC:DATA? TRACE1",
    ":SYST:ERR?",
    ":BAND:VID?",
    ":POW:ATT?",
    ":INIT:CONT ON",
    *_modes(
        ["NORM", "POS", "SAMP", "NEG"],
        ["WRIT", "AVER", "MAXH", "MINH"],
        ":DET:TRAC1 {}",
        ":TRAC1:TYPE {}",
    ),
    ":TRAC1:TYPE WRIT",
    # the automatic detector follows the trace mode
    ":DET:TRAC1:AUTO ON",
    ":DET:TRAC1?",
    ":TRAC1:TYPE MAXH",
    ":DET:TRAC1?",
    ":TRAC1:TYPE MINH",
    ":DET:TRAC1?",
    ":TRAC:DATA? TRACE1",
    ":TRAC1:TYPE WRIT",
    ":DET:TRAC1 POS",
    # RBW and VBW manual (the 10 % steps), then automatic again
    ":BAND 4300",
    ":BAND?",
    ":BAND:VID 510",
    ":BAND:VID?",
    ":SWE:TIME?",
    *_READ,
    ":BAND:AUTO ON",
    ":BAND:VID:AUTO ON",
    # attenuation manual, then automatic at another reference level
    ":POW:ATT 30",
    ":POW:ATT?",
    *_READ,
    ":POW:ATT:AUTO ON",
    ":DISP:WIND:TRAC:Y:RLEV 10 dBm",
    ":POW:ATT?",
    *_READ,
    ":SWE:POIN 2001",
    ":TRAC:DATA? TRACE1",
    ":SWE:POIN 1001",
    # markers: the searches, a delta pair and the counter
    ":CALC:MARK1:MAX",
    ":CALC:MARK1:FCO:X?",
    ":CALC:MARK1:MODE DELT",
    ":CALC:MARK1:X 200000",
    ":CALC:MARK1:X?",
    ":CALC:MARK1:Y?",
    ":CALC:MARK1:MODE POS",
    ":CALC:MARK1:MIN",
    ":CALC:MARK1:Y?",
    ":CALC:MARK1:MAX:NEXT",
    ":CALC:MARK1:MAX:LEFT",
    ":CALC:MARK:AOFF",
    ":CALC:MARK1:Y?",
    ":SYST:ERR?",
    ":SYST:ERR?",
    ":SYST:ERR?",
    # single sweeps in max hold take the hold count
    ":TRAC1:TYPE MAXH",
    ":AVER:COUN 4",
    ":INIT:CONT OFF",
    ":INIT:IMM",
    ":TRAC:DATA? TRACE1",
    ":INIT:REST",
    ":TRAC:DATA? TRACE1",
    ":TRAC:CLE:ALL",
    ":INIT:CONT ON",
    ":TRAC1:TYPE WRIT",
    # overload: +18 dBm into the mixer at 0 dB
    "sg> :POW 18",
    ":POW:ATT 0",
    ":TRAC:DATA? TRACE1",
    ":SYST:ERR?",
]

#: model -> (its example bench, the command sequence: for "sa", or for the instrument named before
#: "> "; "advance> <seconds>" moves the virtual clock).
MODELS: dict[str, tuple[str, list[str]]] = {
    "keysight-n9320b": ("keysight_n9320b.bench.yaml", _N9320B),
    "keysight-n9042b": ("keysight-n9042b.bench.yaml", _N9042B),
}


def _digest(data: bytes | None) -> str | None:
    """A reply's SHA-256. A reply of numbers is hashed as its length and its values rounded to
    5 decimals: the last digits of a 17-digit trace differ between machines (numpy / libm builds),
    which a refactor cannot be blamed for (the earlier ``sb-sa-1`` table compares with rtol 1e-9)."""
    if data is None:
        return None
    try:
        values = [round(float(x), 5) for x in data.decode().split(",")]
    except ValueError:
        return hashlib.sha256(data).hexdigest()
    return hashlib.sha256(f"{len(data)}:{values}".encode()).hexdigest()


def walk(model: str, show: bool = False) -> list[list]:
    """[command, reply digest, ns consumed] for every step, then the screen's readings."""
    name, steps = MODELS[model]
    bench_def = BenchDefinition.model_validate(yaml.safe_load((BENCHES / name).read_text(encoding="utf-8")))
    s = open_bench(bench_def, clock="command", seed=7, idle="off")
    try:
        assert all(warm_up(s, *s.instruments))
        rows = []
        for command in steps:
            before = s.now_ns
            instrument, _, text = command.rpartition("> ")
            reply = None
            if instrument == "advance":
                s.advance(float(text))
            else:
                s.write(instrument or "sa", (text + "\n").encode())
                reply = s.read(instrument or "sa")
            rows.append([command, _digest(reply), s.now_ns - before])
            if show:
                print(f"  {command:40} {len(reply) if reply is not None else '-':>6} {(reply or b'')[:60]!r}")
        with s.view():
            screen = s.instruments["sa"].components["sa"].readings()
        values = json.dumps(screen["values"], sort_keys=True)
        trace = ",".join(repr(v) for v in screen["traces"][0]["values"]).encode()
        rows.append(["<screen values>", values, 0])
        rows.append(["<screen trace>", _digest(trace), 0])
        if show:
            print(f"  screen: {values}")
        return rows
    finally:
        s.close()


@pytest.mark.parametrize("model", list(MODELS))
def test_the_real_models_gold_standard_is_reproduced(model):
    got = walk(model)
    expected = MODELS_GOLD.get(model)
    assert expected, f"no gold standard for {model}: record it (see the module docstring)"
    assert [row[0] for row in got] == [row[0] for row in expected]  # the same sequence was recorded
    for step, (row, want) in enumerate(zip(got, expected, strict=True)):
        assert row == want, f"{model} step {step}: {row[0]}"


if __name__ == "__main__":
    recorded = {}
    for case in MODELS:
        print(case)
        recorded[case] = walk(case, show=True)
    MODELS_FILE.write_text(json.dumps(recorded, indent=1) + "\n")
