"""B&K Precision 8614 (issue 337, capability #661): the 8600 manual's flow on the example bench.

The manual cases (tests/models/bk-8614/) check command by command; these tests run the
real-instrument-style script (examples/scripts/bk_8614_load_test.py) unchanged on the example
bench, and check in virtual time what the cases cannot: the OCP and OPP delays (the input stays on
for the delay, then the protection shuts it down and PS is set), an overload that ends before the
delay, and the CC slew at the manual's slow rate.
"""

from pathlib import Path

import pytest

from helpers import load_script, run_manual_script, session_query
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "bk-8614.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "bk_8614_load_test.py"
PS = 1 << 13

script = load_script(SCRIPT)


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
    assert out[4] == " 20.00 A set:  46.0000 V,  20.0000 A,   920.000 W"
    assert out[5] == '0,"No Error"'
    assert out[6] == "source resistance: 100.0 mOhm"


def test_opp_trips_only_after_its_delay():
    s = session()
    try:
        for c in ("POW:PROT 500", "POW:PROT:DEL 2", "CURR 20", "INP ON"):
            assert q(s, c) is None
        settle(s)
        s.advance(1.0)
        assert float(q(s, "FETC:CURR?")) == pytest.approx(20.0)  # 920 W, still within the delay
        assert int(q(s, "STAT:QUES:COND?")) & PS == 0
        s.advance(1.5)
        assert float(q(s, "FETC:CURR?")) == 0.0  # 2 s over the level: the input is shut down
        assert int(q(s, "STAT:QUES:COND?")) & PS
        assert q(s, "POW:PROT 1500;:PROT:CLE") is None
        assert float(q(s, "FETC:CURR?")) == pytest.approx(20.0)
    finally:
        s.close()


def test_an_overcurrent_that_ends_within_the_delay_does_not_trip():
    s = session()
    try:
        for c in ("CURR:PROT:STAT ON", "CURR:PROT 15", "CURR:PROT:DEL 3", "CURR 20", "INP ON"):
            assert q(s, c) is None
        s.advance(2.0)
        assert float(q(s, "FETC:CURR?")) == pytest.approx(20.0)
        assert int(q(s, "STAT:QUES:COND?")) & 0b10  # OC: over the level, not yet shut down
        assert q(s, "CURR 10") is None
        s.advance(5.0)
        assert float(q(s, "FETC:CURR?")) == pytest.approx(10.0)
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


def test_cc_slew_at_the_slowest_rate():
    s = session()
    try:
        for c in ("CURR:SLEW:POS 0.001", "CURR 20", "INP ON"):
            assert q(s, c) is None
        s.advance(10e-6)  # 10 µs at 0.001 A/µs
        assert float(q(s, "FETC:CURR?")) == pytest.approx(0.01, abs=1e-9)
        s.advance(0.1)
        assert float(q(s, "FETC:CURR?")) == pytest.approx(20.0)
    finally:
        s.close()
