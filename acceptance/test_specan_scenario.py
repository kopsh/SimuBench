"""Spectrum analyzer scenario (docs/validation-specan.md), through PyVISA.

Levels follow the physical amplitude convention of D13: the generator's 1 Vpp is defined into its
50 Ω load setting, so the analyzer's 50 Ω input sees 0.5 V peak, 6.02 dB above the reference
article. Expected values come from refcalc.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
import pyvisa
from refcalc.spectrum import (
    bucket_response_db,
    detector_offsets,
    gaussian_rbw_loss_db,
    line_dbm,
    noise_floor_dbm,
    prbs_line_peak,
    square_harmonic_peak,
)

from .conftest import BENCHES

A = 0.5  # square amplitude on the RF_IN net (V peak)
EPS = 20 / 10e6  # the external reference is 2 ppm fast


@pytest.fixture
def rig():
    rm = pyvisa.ResourceManager(f"{BENCHES / 'specan.bench.yaml'}@simubench")
    res = {
        n: rm.open_resource(n, read_termination="\n", write_termination="\n") for n in ("sa", "awg", "ref")
    }
    yield res["sa"], res["awg"], res["ref"], rm.visalib.bench
    rm.close()


def num(sa, cmd: str) -> float:
    return float(sa.query(cmd))


def trace(sa) -> tuple[np.ndarray, np.ndarray]:
    start, stop = num(sa, ":SENS:FREQ:STAR?"), num(sa, ":SENS:FREQ:STOP?")
    y = np.array([float(v) for v in sa.query(":TRAC:DATA? TRACE1").split(",")])
    return np.linspace(start, stop, y.size), y


def errors(inst) -> list[str]:
    out = []
    while not (e := inst.query(":SYST:ERR?")).startswith("0,"):
        out.append(e)
    return out


# ---- 1. connection ------------------------------------------------------------------------------


def test_identity_and_peak(rig):
    sa, awg, ref, _ = rig
    assert "SB-SA-1" in sa.query("*IDN?") and awg.query("*IDN?") == ref.query("*IDN?")
    sa.write(":CALC:MARK:MAX")
    assert num(sa, ":CALC:MARK:X?") == 100e3
    assert num(sa, ":CALC:MARK:Y?") == pytest.approx(line_dbm(square_harmonic_peak(A, 1)), abs=2e-3)


# ---- 2. couplings -------------------------------------------------------------------------------


def settings(sa) -> tuple:
    return (
        num(sa, ":SENS:FREQ:CENT?"),
        num(sa, ":SENS:BAND:RES?"),
        sa.query(":SENS:BAND:RES:AUTO?"),
        num(sa, ":SENS:BAND:VID?"),
        sa.query(":SENS:BAND:VID:AUTO?"),
        num(sa, ":SENS:SWE:TIME?"),
        sa.query(":SENS:SWE:TIME:UNC?"),
        num(sa, ":SENS:POW:RF:ATT?"),
        sa.query(":SENS:POW:RF:OVER?"),
    )


def test_setting_couplings(rig):
    sa, _, _, _ = rig
    assert settings(sa) == (500e3, 10e3, "1", 10e3, "1", pytest.approx(0.025), "0", 20, "0")
    sa.write(":SENS:FREQ:CENT 200e3")  # the start would be negative: the window slides
    assert num(sa, ":SENS:FREQ:CENT?") == 500e3 and errors(sa) == []
    sa.write(":SENS:FREQ:SPAN 10e6")
    sa.write(":SENS:FREQ:CENT 5e6")
    assert settings(sa) == (5e6, 100e3, "1", 100e3, "1", pytest.approx(2.5e-3), "0", 20, "0")
    sa.write(":SENS:BAND:RES 2000")  # snaps to 3 kHz; the sweep stretches
    assert settings(sa) == (5e6, 3e3, "0", 3e3, "1", pytest.approx(2.5e7 / 9e6), "0", 20, "0")
    sa.write(":SENS:SWE:TIME 0.05")
    assert settings(sa)[5:7] == (pytest.approx(0.05), "1")
    sa.write(":SENS:SWE:TIME:AUTO ON")
    sa.write(":SENS:BAND:RES:AUTO ON")
    sa.write(":DISP:WIND:TRAC:Y:SCAL:RLEV -20")
    assert settings(sa)[7:] == (0, "1")  # the square's total power overloads the mixer
    sa.write(":DISP:WIND:TRAC:Y:SCAL:RLEV 10")
    assert settings(sa)[7:] == (20, "0")
    assert errors(sa) == []


# ---- 3. trace and markers -----------------------------------------------------------------------


def test_trace_and_harmonic_walk(rig):
    sa, _, _, _ = rig
    f, y = trace(sa)
    assert y.size == 1001 and f[0] == 0 and f[-1] == 1e6
    assert f[y.argmax()] == 100e3
    sa.write(":CALC:MARK:PEAK:THR -30")
    for n in (1, 3, 5, 7, 9):
        sa.write(":CALC:MARK:MAX" if n == 1 else ":CALC:MARK:MAX:NEXT")
        assert num(sa, ":CALC:MARK:X?") == n * 100e3
        assert num(sa, ":CALC:MARK:FCO:X?") == pytest.approx(n * 100e3 / (1 + EPS), abs=1e-3)
        assert num(sa, ":CALC:MARK:Y?") == pytest.approx(line_dbm(square_harmonic_peak(A, n)), abs=2e-3)
    sa.write(":CALC:MARK:MAX:NEXT")
    assert errors(sa) == ['-200,"Execution error"']
    assert num(sa, ":CALC:MARK:X?") == 900e3  # the marker stays


# ---- 4. detectors and the noise floor -----------------------------------------------------------


def test_detectors_on_noise_and_on_a_line(rig):
    sa, _, _, bench = rig
    sa.write(":SENS:FREQ:SPAN 100e3")
    sa.write(":SENS:FREQ:CENT 200e3")
    sa.write(":SENS:SWE:POIN 101")
    sa.write(":SENS:BAND:RES 100")  # 1 kHz per point: ten resolution cells
    floor = noise_floor_dbm(100, 20)
    theory = detector_offsets(10)
    line = line_dbm(square_harmonic_peak(A, 3))
    for det in ("SAMPle", "POSitive", "AVERage", "RMS"):
        sa.write(f":SENS:DET:FUNC {det}")
        sa.write(":SENS:FREQ:CENT 200e3")
        sweeps = []
        for _ in range(12):
            bench.advance(num(sa, ":SENS:SWE:TIME?"))
            sweeps.append(trace(sa)[1])
        mean = float(np.mean(sweeps))
        assert mean - floor == pytest.approx(theory[det], abs=0.35), det
        sa.write(":SENS:FREQ:CENT 300e3")  # the third harmonic on the middle point
        # D13: a point's bucket is ten RBWs wide; average and rms detectors spread a CW line over
        # it and read it low, as real analyzers do. Sample and positive peak read it exactly.
        expected = line + bucket_response_db(0.0, 100, 1000, det)
        assert trace(sa)[1].max() == pytest.approx(expected, abs=5e-3), det


def test_floor_follows_attenuation(rig):
    sa, _, _, _ = rig
    sa.write(":SENS:FREQ:SPAN 100e3;:SENS:FREQ:CENT 200e3")
    sa.write(":SENS:SWE:POIN 1001;:SENS:DET:FUNC RMS;:SENS:BAND:RES 1000")
    for att in (0, 10, 20, 30):
        sa.write(f":SENS:POW:RF:ATT {att}")
        _, y = trace(sa)
        mean_power = 10 * math.log10(np.mean(10 ** (y / 10)))
        assert mean_power == pytest.approx(noise_floor_dbm(1000, att), abs=0.5)


def test_quasi_peak_is_refused(rig):
    sa, _, _, _ = rig
    sa.write(":SENS:DET:FUNC QPEak")
    assert errors(sa)[0].startswith("-224,")


# ---- 5. trace modes and virtual time ------------------------------------------------------------


def test_trace_modes_on_the_floor(rig):
    sa, _, _, bench = rig
    sa.write(":SENS:DET:FUNC SAMP;:SENS:FREQ:SPAN 100e3;:SENS:FREQ:CENT 200e3;:SENS:BAND:RES 1000")
    sa.write(":SENS:AVER:COUN 10")
    sweep = num(sa, ":SENS:SWE:TIME?")
    assert sweep == pytest.approx(0.25)
    assert np.array_equal(trace(sa)[1], trace(sa)[1])  # one instant, one trace
    stats = {}
    for mode in ("WRITe", "AVERage", "MAXHold"):
        sa.write(f":TRAC:TYPE {mode}")
        bench.advance(10.5 * sweep)
        y = trace(sa)[1]
        stats[mode] = (y.mean(), y.std())
    assert stats["AVERage"][1] == pytest.approx(stats["WRITe"][1] / math.sqrt(10), rel=0.25)
    assert abs(stats["AVERage"][0] - stats["WRITe"][0]) < 1.0
    assert stats["MAXHold"][0] > stats["WRITe"][0] + 4


def test_modes_remember_a_moving_source(rig):
    sa, awg, _, bench = rig
    sa.write(
        ":SENS:FREQ:SPAN 1e6;:SENS:FREQ:CENT 5e5;:SENS:BAND:RES 10e3;:SENS:DET:FUNC POS;:SENS:AVER:COUN 40"
    )
    sweep = num(sa, ":SENS:SWE:TIME?")
    line = line_dbm(square_harmonic_peak(A, 1))
    seen = {}
    for mode in ("WRITe", "AVERage", "MAXHold"):
        awg.write(":SOUR1:FREQ 100000")
        sa.write(f":TRAC:TYPE {mode}")
        bench.advance(20.5 * sweep)
        awg.write(":SOUR1:FREQ 150000")
        bench.advance(20 * sweep)
        f, y = trace(sa)
        seen[mode] = (y[np.argmin(np.abs(f - 100e3))], y[np.argmin(np.abs(f - 150e3))])
    assert seen["WRITe"][0] < -80 and seen["WRITe"][1] == pytest.approx(line, abs=0.01)
    assert seen["MAXHold"] == (pytest.approx(line, abs=0.01), pytest.approx(line, abs=0.01))
    assert all(-60 < v < -30 for v in seen["AVERage"])  # about half way down in dB
    sa.write(":TRAC:CLE")
    f, y = trace(sa)
    assert y[np.argmin(np.abs(f - 100e3))] < -80  # the old line is forgotten
    assert errors(sa) == []


def test_single_sweep_takes_its_time():
    """On the command-driven clock (D12) a single sweep advances virtual time by its sweep time."""
    from simubench.api import open_bench

    with open_bench(BENCHES / "specan.bench.yaml", clock="command") as bench:
        bench.scpi("sa", ":INIT:CONT OFF;:SENS:AVER:COUN 4;:TRAC:TYPE AVER")
        sweep = float(bench.scpi("sa", ":SENS:SWE:TIME?"))
        t0 = bench.now_ns
        bench.scpi("sa", ":INIT")
        assert (bench.now_ns - t0) / 1e9 == pytest.approx(4 * sweep, rel=1e-3)  # one sweep per average
        assert bench.scpi("sa", ":TRAC:DATA? TRACE1") == bench.scpi("sa", ":TRAC:DATA? TRACE1")


# ---- 6. a user-defined source: PRBS -------------------------------------------------------------


def test_prbs_lines_follow_the_closed_form():
    """D13: a PRBS's spectrum is exact lines (no FFT record), so it matches the closed form up to
    the Gaussian RBW's response at the marker's offset from the line."""
    rm = pyvisa.ResourceManager(f"{BENCHES / 'specan_prbs.bench.yaml'}@simubench")
    sa = rm.open_resource("sa", read_termination="\n", write_termination="\n")
    sa.write(":SENS:FREQ:SPAN 20e3;:SENS:FREQ:CENT 10e3;:SENS:BAND:RES 100")
    assert num(sa, ":SENS:SWE:TIME?") == pytest.approx(5.0)
    level = 0.25 / 2  # open-circuit level across the 50 Ω input
    for k in (1, 2, 5, 12, 20, 25):
        f_line = k * 100e3 / 127
        sa.write(f":CALC:MARK:X {f_line}")
        x = num(sa, ":CALC:MARK:X?")
        # The positive-peak detector catches the line anywhere in the marker point's 20 Hz bucket.
        expected = line_dbm(prbs_line_peak(level, 7, k)) + gaussian_rbw_loss_db(
            max(abs(x - f_line) - 10, 0), 100
        )
        assert num(sa, ":CALC:MARK:Y?") == pytest.approx(expected, abs=0.01), k
    assert errors(sa) == []
    rm.close()


# ---- 7. limits and errors -----------------------------------------------------------------------


def test_too_many_lines_is_reported_on_every_read(rig):
    sa, awg, _, _ = rig
    awg.write(":SOUR1:FREQ 100")
    sa.write(":SENS:FREQ:SPAN 20e6;:SENS:FREQ:CENT 10e6")
    for _ in range(2):
        trace(sa)
        errs = errors(sa)
        assert len(errs) == 1 and errs[0].startswith('-221,"Settings conflict; too many lines for one trace')


@pytest.mark.parametrize(
    "cmd, code",
    [
        (":SENS:FREQ:SPAN 1", "-222"),
        (":SENS:FREQ:CENT 8e9", "-222"),
        (":SENS:BAND:RES 0.5", "-222"),
        (":SENS:DET:FUNC QPEak", "-224"),
    ],
)
def test_refusals(rig, cmd, code):
    sa, _, _, _ = rig
    sa.write(cmd)
    assert errors(sa)[0].split(",")[0] == code


def test_peak_search_without_a_signal(rig):
    sa, awg, _, _ = rig
    sa.write(":CALC:MARK:PEAK:THR -30")
    awg.write(":OUTP1:STAT OFF")
    sa.write(":CALC:MARK:MAX")
    assert errors(sa) == ['-200,"Execution error"']


def test_external_reference_without_a_signal(rig):
    sa, _, ref, _ = rig
    ref.write(":OUTP1:STAT OFF")
    sa.write(":SENS:ROSC:SOUR INT")
    sa.write(":SENS:ROSC:SOUR EXT")
    assert sa.query(":SENS:ROSC:LOCK?") == "0" and sa.query(":SENS:ROSC:ACT?") == "INT"
    assert errors(sa)[0].startswith('-221,"Settings conflict; external reference unusable')
