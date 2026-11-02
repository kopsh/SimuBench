"""SRS SR830 (issue 195): the real-instrument-style RC sweep script against the engine.

The manual cases (tests/models/srs-sr830/) check command by command; these tests run
examples/scripts/srs_sr830_rc_sweep.py on the example bench, check the readings against the
circuit's closed form (50 Ohm source, 1 kOhm / 100 nF, the 10 MOhm / 25 pF SIG_IN terminal and the
0.16 Hz AC coupling), and check that a real SR830 resource string — the default GPIB address 8 —
opens the simulated instrument. The SR830 has no LAN: a bench declares the GPIB (or RS-232) address
and the in-process PyVISA backend maps it.
"""

from __future__ import annotations

import cmath
import importlib.util
import math
import shutil
import sys
from pathlib import Path

import pytest
import pyvisa

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "srs_sr830.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "srs_sr830_rc_sweep.py"

R, C = 1000.0, 100e-9
RS, LOAD, C_IN = 50.0, 10e6, 25e-12  # SINE_OUT, SIG_IN input, SIG_IN capacitance
AC_CORNER = 0.16  # Hz, ICPl = AC
AMPLITUDE = 0.5  # V rms


def _script():
    spec = importlib.util.spec_from_file_location("srs_sr830_rc_sweep", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def expected(frequency: float) -> complex:
    """The settled phasor at SIG_IN for the bench: SINE_OUT 0.5 Vrms, RC, 10 MOhm || 25 pF, AC."""
    omega = 2 * math.pi * frequency
    z_c = 1 / (1j * omega * C)
    z_node = 1 / (1 / z_c + 1 / LOAD + 1j * omega * C_IN)
    h = z_node / (RS + R + z_node)
    ac = 1j * frequency / AC_CORNER / (1 + 1j * frequency / AC_CORNER)
    return AMPLITUDE * h * ac


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(sys, "argv", ["srs_sr830_rc_sweep.py", "GPIB0::8::INSTR", "--bench", str(bench)])
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Stanford_Research_Systems,SR830,s/n00111,ver1.000"
    points = [[float(x) for x in line.split(",")] for line in out[1:-1]]
    assert len(points) == len(script.FREQUENCIES)
    for (frequency, magnitude, theta), want in zip(points, map(expected, script.FREQUENCIES), strict=True):
        # The reading is a settled measurement of the circuit; 0.2 % covers the virtual-time
        # settling and the residual 2f ripple of the shortest time constant.
        assert magnitude == pytest.approx(abs(want), rel=2e-3), frequency
        assert theta == pytest.approx(math.degrees(cmath.phase(want)), abs=0.1), frequency
    # The RC corner: 1/sqrt(2) of the low-frequency value, -45 degrees.
    corner = points[script.FREQUENCIES.index(1515.76)]
    assert corner[1] == pytest.approx(AMPLITUDE / math.sqrt(2), rel=2e-3)
    assert corner[2] == pytest.approx(-45.0, abs=0.1)
    # The AUX DAC output behind 1 kOhm into the 1 MOhm ADC.
    assert out[-1] == "aux,1.5,1.4985015"


def test_the_gpib_address_8_of_a_real_sr830_opens_the_simulated_instrument():
    rm = pyvisa.ResourceManager(f"{BENCH}@simubench")
    try:
        lia = rm.open_resource("GPIB0::8::INSTR", read_termination="\n", write_termination="\n")
        try:
            assert lia.query("*IDN?") == "Stanford_Research_Systems,SR830,s/n00111,ver1.000"
            # A real program starts with OUTX: answers go to the GPIB interface it is talking on.
            lia.write("OUTX 1")
            assert lia.query("OUTX?") == "1"
            assert float(lia.query("OUTP? 3")) == pytest.approx(2 * abs(expected(1000.0)), rel=2e-3)
        finally:
            lia.close()
    finally:
        rm.close()


def test_the_readings_are_one_coherent_phasor():
    """X, Y, R and theta are consistent, and the inputs feed the same solver as the rest of the bench."""
    rm = pyvisa.ResourceManager(f"{BENCH}@simubench")
    try:
        lia = rm.open_resource("GPIB0::8::INSTR", read_termination="\n", write_termination="\n")
        try:
            x = float(lia.query("OUTP? 1"))
            y = float(lia.query("OUTP? 2"))
            r = float(lia.query("OUTP? 3"))
            theta = float(lia.query("OUTP? 4"))
        finally:
            lia.close()
    finally:
        rm.close()
    assert r == pytest.approx(math.hypot(x, y), abs=1e-9)
    assert theta == pytest.approx(math.degrees(math.atan2(y, x)), abs=1e-6)
    assert r == pytest.approx(2 * abs(expected(1000.0)), rel=2e-3)
