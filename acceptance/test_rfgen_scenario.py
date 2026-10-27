"""RF signal generator scenario (docs/validation-rfgen.md): a generator measured with the analyzer."""

from __future__ import annotations

import math

import pytest
import pyvisa
from refcalc.modulation import bessel_j, carson_omitted_power, dbc

from simubench.library.components.rfgen import carson_orders, omitted_power_fraction

from .conftest import BENCHES

FC = 1e9


@pytest.fixture
def rig():
    rm = pyvisa.ResourceManager(f"{BENCHES / 'rfgen.bench.yaml'}@simubench")
    sa = rm.open_resource("sa", read_termination="\n", write_termination="\n")
    rf = rm.open_resource("rf", read_termination="\n", write_termination="\n")
    yield sa, rf, rm.visalib.bench
    rm.close()


def num(inst, cmd: str) -> float:
    return float(inst.query(cmd))


def errors(inst) -> list[str]:
    out = []
    while not (e := inst.query(":SYST:ERR?")).startswith("0,"):
        out.append(e)
    return out


def walk(sa, count: int) -> list[tuple[float, float]]:
    found = []
    for i in range(count):
        sa.write(":CALC:MARK:MAX" if i == 0 else ":CALC:MARK:MAX:NEXT")
        found.append((num(sa, ":CALC:MARK:X?") - FC, num(sa, ":CALC:MARK:Y?")))
    return sorted(found)


# ---- 1. sidebands -------------------------------------------------------------------------------


def test_am_fm_pm_sidebands(rig):
    sa, rf, _ = rig
    sa.write(":SENS:ROSC:SOUR EXT")  # share the generator's timebase
    assert num(sa, ":SENS:BAND:RES?") == 30e3 and num(sa, ":SENS:POW:RF:ATT?") == 20
    sa.write(":CALC:MARK:PEAK:THR -60")

    rf.write(":SOUR:AM:DEPT 30; :SOUR:AM:INT:FREQ 1e6; :SOUR:AM:STAT ON")
    assert sa.query(":SENS:POW:RF:OVER?") == "0"
    for (x, y), (wx, wy) in zip(walk(sa, 3), [(-1e6, dbc(0.15)), (0.0, 0.0), (1e6, dbc(0.15))], strict=True):
        assert x == wx and y == pytest.approx(wy, abs=0.01)
    rf.write(":SOUR:AM:STAT OFF")

    want = [(n * 1e6, dbc(bessel_j(n, 1.0))) for n in range(-2, 3)]
    for setup in (
        ":SOUR:FM:DEV 1e6; :SOUR:FM:INT:FREQ 1e6; :SOUR:FM:STAT ON",
        ":SOUR:PM:DEV 1; :SOUR:PM:INT:FREQ 1e6; :SOUR:PM:STAT ON",
    ):
        rf.write(setup)
        for (x, y), (wx, wy) in zip(walk(sa, 5), want, strict=True):
            assert x == wx and y == pytest.approx(wy, abs=0.01)  # floor noise: ±0.005 dB at −19 dBm
        rf.write(":SOUR:FM:STAT OFF; :SOUR:PM:STAT OFF")
    assert errors(sa) == [] and errors(rf) == []


# ---- 2. reference level and overload ------------------------------------------------------------


def test_am_power_and_overload(rig):
    sa, rf, _ = rig
    sa.write(":DISP:WIND:TRAC:Y:SCAL:RLEV 0")  # auto attenuation 10 dB
    assert num(sa, ":SENS:POW:RF:ATT?") == 10
    assert sa.query(":SENS:POW:RF:OVER?") == "0"
    rf.write(":SOUR:AM:DEPT 30; :SOUR:AM:STAT ON")  # +0.19 dBm total: −9.81 dBm at the mixer
    assert sa.query(":SENS:POW:RF:OVER?") == "1"
    rf.write(":SOUR:AM:STAT OFF; :SOUR:FM:DEV 5e6; :SOUR:FM:INT:FREQ 1e6; :SOUR:FM:STAT ON")
    assert sa.query(":SENS:POW:RF:OVER?") == "0"  # constant envelope
    sa.write(":DISP:WIND:TRAC:Y:SCAL:RLEV 10")
    rf.write(":SOUR:FM:STAT OFF; :SOUR:AM:STAT ON")
    assert num(sa, ":SENS:POW:RF:ATT?") == 20 and sa.query(":SENS:POW:RF:OVER?") == "0"


# ---- 3. Carson's rule ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "beta, orders, omitted",
    [
        (0.5, 1, 0.00189),
        (1.0, 2, 0.00078),
        (2.0, 3, 0.00241),
        (5.99, 6, 0.04035),
        (6.0, 7, 0.00739),
        (10.0, 11, 0.01004),
    ],
)
def test_carson_truncation(beta, orders, omitted):
    assert carson_orders(beta) == orders
    assert omitted_power_fraction(beta) == pytest.approx(omitted, abs=1e-5)
    assert omitted_power_fraction(beta) == pytest.approx(carson_omitted_power(beta), abs=1e-6)


def test_truncation_bound():
    worst = max(omitted_power_fraction(b / 100) for b in range(1, 3000))
    assert worst < 0.0410


def test_truncation_seen_on_the_analyzer(rig):
    sa, rf, _ = rig
    sa.write(":SENS:ROSC:SOUR EXT;:CALC:MARK:PEAK:THR -60")
    rf.write(":SOUR:FM:DEV 1e6; :SOUR:FM:INT:FREQ 1e6; :SOUR:FM:STAT ON")
    walk(sa, 5)
    sa.write(":CALC:MARK:MAX:NEXT")  # the exact J3(1) would be at −34.2 dBc; the set stops at order 2
    assert errors(sa) == ['-200,"Execution error"']
    assert dbc(bessel_j(3, 1.0)) == pytest.approx(-34.2, abs=0.05)
    rf.write(":SOUR:FM:DEV 2404826")  # J0(2.404826) = 0: the carrier nulls
    sa.write(":CALC:MARK:X 1e9")
    assert num(sa, ":CALC:MARK:Y?") < -80  # the analyzer's floor, about −85 dBm


def test_line_budget(rig):
    _, rf, _ = rig
    rf.write(":SOUR:FM:DEV 1e7; :SOUR:FM:INT:FREQ 0.1; :SOUR:FM:STAT ON")  # β = 1e8
    assert errors(rf) == ['-222,"Data out of range; modulation index 1e+08 needs more than 999 lines"']
    assert rf.query(":SOUR:FM:STAT?") == "0"


# ---- 4. list and step sweeps --------------------------------------------------------------------


def test_list_and_step_sweeps(rig):
    sa, rf, _ = rig
    sa.write(":SENS:FREQ:SPAN 7.5e9;:SENS:FREQ:CENT 3.75e9;:SENS:BAND:RES:AUTO ON")
    assert num(sa, ":SENS:BAND:RES?") == 3e6 and sa.query(":SENS:SWE:POIN?") == "1001"
    rf.write(":SOUR:LIST:FREQ 1e9,2.4e9,5.8e9")  # D13: the unquoted form real instruments take
    assert errors(rf) == [] and rf.query(":SOUR:LIST:FREQ:POIN?") == "3"
    rf.write(':SOUR:LIST:FREQ "1e9,2.4e9,5.8e9"')
    assert errors(rf) == [] and rf.query(":SOUR:LIST:FREQ:POIN?") == "3"
    rf.write(":SOUR:FREQ:MODE LIST;:INIT")
    for f in (1e9, 2.4e9, 5.8e9):  # the analyzer on its own timebase: the generator reads 2 ppm high
        sa.write(":CALC:MARK:MAX")
        assert num(sa, ":CALC:MARK:FCO:X?") == pytest.approx(f * (1 + 2e-6), abs=0.5)
        assert num(sa, ":CALC:MARK:Y?") == pytest.approx(0.0, abs=0.01)
        rf.write(":TRIG")
    rf.write(":TRIG")
    assert rf.query(":SOUR:LIST:MAN?") == "3"  # holds at the last point

    sa.write(":SENS:ROSC:SOUR EXT")
    rf.write(
        ":SOUR:FREQ:STAR 1e9; :SOUR:FREQ:STOP 2e9; :SOUR:SWE:POIN 5; :SOUR:SWE:SPAC LIN; :SOUR:FREQ:MODE SWE"
    )
    rf.write(":INIT")
    for f in (1e9, 1.25e9, 1.5e9, 1.75e9, 2e9):
        sa.write(":CALC:MARK:MAX")
        assert num(sa, ":CALC:MARK:FCO:X?") == pytest.approx(f, abs=1e-3)
        rf.write(":TRIG")
    assert errors(rf) == [] and errors(sa) == []


# ---- 5. timebase and reference ------------------------------------------------------------------


def test_timebase_and_reference(rig):
    sa, rf, bench = rig
    rf_net = bench.port_net["rf.RF_OUT"]
    ref_net = bench.port_net["rf.REF_OUT"]
    rf_sig, ref_sig = bench.net_signal(rf_net).merged(), bench.net_signal(ref_net).merged()
    assert float(rf_sig.freq[0]) == pytest.approx(1_000_002_000.0)
    assert abs(rf_sig.amp[0]) == pytest.approx(0.3162, abs=1e-4)  # 0 dBm into the 50 Ω input
    assert float(ref_sig.freq[0]) == pytest.approx(10_000_020.0)
    assert abs(ref_sig.amp[0]) == pytest.approx(0.5012, abs=1e-4)  # +4 dBm

    def carrier() -> float:
        sa.write(":CALC:MARK:MAX")
        return num(sa, ":CALC:MARK:FCO:X?")

    assert carrier() == pytest.approx(1_000_002_000.0, abs=0.5) and sa.query(":SENS:ROSC:LOCK?") == "0"
    sa.write(":SENS:ROSC:SOUR EXT")
    assert carrier() == pytest.approx(1e9, abs=0.5)
    assert sa.query(":SENS:ROSC:LOCK?") == "1" and sa.query(":SENS:ROSC:ACT?") == "EXT"
    rf.write(":SOUR:ROSC:OUTP OFF")
    assert carrier() == pytest.approx(1_000_002_000.0, abs=0.5)
    assert sa.query(":SENS:ROSC:LOCK?") == "0" and sa.query(":SENS:ROSC:ACT?") == "INT"


# ---- 6. refusals --------------------------------------------------------------------------------


def test_refusals(rig):
    _, rf, _ = rig

    def states() -> tuple[str, str, str]:
        return rf.query(":SOUR:PULM:STAT?"), rf.query(":SOUR:FM:STAT?"), rf.query(":SOUR:PM:STAT?")

    rf.write(":SOUR:PULM:STAT ON")
    assert errors(rf) == ['-221,"Settings conflict; pulse modulation is not simulated"'] and states() == (
        "0",
        "0",
        "0",
    )
    rf.write(":SOUR:FM:STAT ON; :SOUR:PM:STAT ON")
    assert errors(rf) == ['-221,"Settings conflict"'] and states() == ("0", "0", "1")  # the later one wins
    for cmd in (":POW 25", ":POW -131", ":FREQ 7e9", ":FREQ 8e3"):
        rf.write(cmd)
        assert errors(rf)[0].startswith("-222,"), cmd
    assert num(rf, ":POW?") == 0 and num(rf, ":FREQ?") == 1e9  # unchanged
    assert math.isclose(num(rf, ":SOUR:PM:DEV?"), 1.0)
