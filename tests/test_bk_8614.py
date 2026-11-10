"""B&K Precision 8614 (issue 337, capability #661): the 8600 manual's flow on the example bench.

The manual cases (tests/models/bk-8614/) check command by command; these tests run the
real-instrument-style script (examples/scripts/bk_8614_load_test.py) unchanged on the example
bench, and check in virtual time what the cases cannot: the OCP and OPP delays (the input stays on
for the delay, then the protection shuts it down and PS is set), an overload that ends before the
delay, and the CC slew at the manual's slow rate.
"""

import math
import statistics
from pathlib import Path

import pytest

from helpers import load_script, run_manual_script, session_query
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "bk-8614.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "bk_8614_load_test.py"
PS = 1 << 13

script = load_script(SCRIPT)


#: 4σ of the readback noise (specs.readback_noise_rms, #808)
V_TOL = 0.0018
I_TOL = 0.0048


def session():
    return open_bench(BENCH, idle="off")


def settle(s) -> None:
    """1 ms after the step the CC ramp is done and a reading lets the load see the operating
    point: the protection delay runs from when the simulator sees the over-limit (as the
    supply's OCP delay, #271)."""
    s.advance(1e-3)
    q(s, "FETC:CURR?")


def q(s, command: str) -> str | None:
    return session_query(s, "load", command)


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(script, ["load"], BENCH, tmp_path, monkeypatch, capsys)
    assert out[0] == "B&K PRECISION, 8614, 601400010000000001, 1.32-1.37"
    assert out[4].startswith(" 20.00 A set: ")  # the readings ride their noise (#808): 4σ below
    v, i, w = (float(x.strip(" VAW")) for x in out[4].split(": ")[1].split(","))
    assert v == pytest.approx(46.0, abs=V_TOL)
    assert i == pytest.approx(20.0, abs=I_TOL)
    assert w == pytest.approx(920.0, abs=4 * 2**0.5 * math.hypot(20 * V_TOL / 4, 46 * I_TOL / 4))
    assert out[5] == '0,"No Error"'
    assert out[6] == "source resistance: 100.0 mOhm"


def test_the_definition_noise_distributes_replays_and_leaves_an_open_input_at_zero():
    """The readback noise (specs.readback_noise_rms, #808): V and I are independent draws around
    the solved point (σ = 0.45 mV / 1.2 mA), the same seed replays bit for bit, the setpoint is
    never noised, and an input that is off sinks exactly 0 A."""
    sigma_v, sigma_i = V_TOL / 4, I_TOL / 4

    def run(n: int) -> tuple[list[float], list[float], list[float]]:
        s = session()
        try:
            assert q(s, "CURR 20") is None
            off = [float(q(s, "MEAS:CURR?")) for _ in range(20)]
            assert q(s, "INP ON") is None
            settle(s)
            volts, amps = [], []
            for _ in range(n):  # read in pairs, so a shorter run replays the start of a longer one
                volts.append(float(q(s, "MEAS:VOLT?")))
                amps.append(float(q(s, "MEAS:CURR?")))
            assert float(q(s, "CURR?")) == 20.0  # the setpoint reads back exactly
            return off, volts, amps
        finally:
            s.close()

    off, volts, amps = run(200)
    assert off == [0.0] * 20  # the input is off: no current, no noise
    assert len(set(volts)) > 100 and len(set(amps)) > 100
    assert statistics.mean(volts) == pytest.approx(46.0, abs=4 * sigma_v / math.sqrt(200))
    assert statistics.stdev(volts) == pytest.approx(sigma_v, rel=0.25)
    assert statistics.mean(amps) == pytest.approx(20.0, abs=4 * sigma_i / math.sqrt(200))
    assert statistics.stdev(amps) == pytest.approx(sigma_i, rel=0.25)
    _, volts2, amps2 = run(20)
    assert (volts2, amps2) == (volts[:20], amps[:20])  # the same seed replays bit for bit


def test_opp_trips_only_after_its_delay():
    s = session()
    try:
        for c in ("POW:PROT 500", "POW:PROT:DEL 2", "CURR 20", "INP ON"):
            assert q(s, c) is None
        settle(s)
        s.advance(1.0)
        assert float(q(s, "FETC:CURR?")) == pytest.approx(20.0, abs=I_TOL)  # 920 W, still within the delay
        assert int(q(s, "STAT:QUES:COND?")) & PS == 0
        s.advance(1.5)
        assert float(q(s, "FETC:CURR?")) == 0.0  # 2 s over the level: the input is shut down
        assert int(q(s, "STAT:QUES:COND?")) & PS
        assert q(s, "POW:PROT 1500;:PROT:CLE") is None
        assert float(q(s, "FETC:CURR?")) == pytest.approx(20.0, abs=I_TOL)
    finally:
        s.close()


def test_an_overcurrent_that_ends_within_the_delay_does_not_trip():
    s = session()
    try:
        for c in ("CURR:PROT:STAT ON", "CURR:PROT 15", "CURR:PROT:DEL 3", "CURR 20", "INP ON"):
            assert q(s, c) is None
        s.advance(2.0)
        assert float(q(s, "FETC:CURR?")) == pytest.approx(20.0, abs=I_TOL)
        assert int(q(s, "STAT:QUES:COND?")) & 0b10  # OC: over the level, not yet shut down
        assert q(s, "CURR 10") is None
        s.advance(5.0)
        assert float(q(s, "FETC:CURR?")) == pytest.approx(10.0, abs=I_TOL)
        assert int(q(s, "STAT:QUES:COND?")) & (PS | 0b10) == 0
    finally:
        s.close()


def test_ocp_trips_when_the_overcurrent_outlasts_the_delay():
    s = session()
    try:
        for c in ("CURR:PROT:STAT ON", "CURR:PROT 15", "CURR:PROT:DEL 3", "CURR 20", "INP ON"):
            assert q(s, c) is None
        settle(s)
        s.advance(3.5)
        assert float(q(s, "FETC:CURR?")) == 0.0
        assert int(q(s, "STAT:QUES:COND?")) & (PS | 0b10) == PS | 0b10
    finally:
        s.close()


def test_a_rate_above_the_new_range_is_clamped_when_the_range_switches():
    """A slew set on the high range follows the range down (issue 721, the model's decision — the
    manual is silent): the readback clamps to 0.25 A/µs and the ramp runs at the clamped rate."""
    s = session()
    try:
        for c in ("CURR:SLEW:POS 0.5", "CURR:RANG 24", "CURR 10", "INP ON"):
            assert q(s, c) is None
        assert float(q(s, "CURR:SLEW:POS?")) == pytest.approx(0.25)
        s.advance(10e-6)  # 10 µs at 0.25 A/µs: a quarter of the way to 10 A
        assert float(q(s, "FETC:CURR?")) == pytest.approx(2.5, abs=I_TOL)
        s.advance(0.1)
        assert float(q(s, "FETC:CURR?")) == pytest.approx(10.0, abs=I_TOL)
        # back up: the clamped rate is legal on the high range and stays
        assert q(s, "CURR:RANG 240") is None
        assert float(q(s, "CURR:SLEW:POS?")) == pytest.approx(0.25)
    finally:
        s.close()


def test_slew_def_follows_the_selected_range_like_min_and_max():
    s = session()
    try:
        assert q(s, "CURR:RANG 24") is None
        assert q(s, "CURR:SLEW:POS DEF") is None  # was -222 before issue 721
        assert float(q(s, "CURR:SLEW:POS?")) == pytest.approx(0.25)
        assert q(s, "SYST:ERR?") == '0,"No Error"'
        assert q(s, "CURR:RANG 240") is None
        assert q(s, "CURR:SLEW:POS DEF") is None
        assert float(q(s, "CURR:SLEW:POS?")) == pytest.approx(2.5)
    finally:
        s.close()


def test_cc_slew_at_the_slowest_rate():
    s = session()
    try:
        # 0.001 A/µs is the low range's slowest (issue 663): the range first, then the slew.
        for c in ("CURR:RANG 24", "CURR:SLEW:POS 0.001", "CURR 20", "INP ON"):
            assert q(s, c) is None
        s.advance(10e-6)  # 10 µs at 0.001 A/µs
        assert float(q(s, "FETC:CURR?")) == pytest.approx(0.01, abs=I_TOL)
        s.advance(0.1)
        assert float(q(s, "FETC:CURR?")) == pytest.approx(20.0, abs=I_TOL)
    finally:
        s.close()
