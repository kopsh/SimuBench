"""SRS SR865A (issue 333): the real-instrument-style RC sweep script against the engine.

The manual cases (tests/models/srs-sr865a/) check command by command; these tests run
examples/scripts/srs_sr865a_rc_sweep.py on the example bench and check the readings against the
circuit's closed form (50 Ohm source, 1 kOhm / 100 nF, the 10 MOhm / 25 pF SIG_IN terminal and the
0.16 Hz AC coupling), then what a transcript cannot see: the input range overloads on the signal's
peak, auto phase settles theta to zero, and the current input reads amperes.
"""

from __future__ import annotations

import cmath
import math
from pathlib import Path

import pytest
import yaml

from helpers import load_script, run_manual_script
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "srs-sr865a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "srs_sr865a_rc_sweep.py"

R, C = 1000.0, 100e-9
RS, LOAD, C_IN = 50.0, 10e6, 25e-12  # SINE_OUT, SIG_IN input, SIG_IN capacitance
AC_CORNER = 0.16  # Hz, ICPL AC
AMPLITUDE = 0.5  # V rms

script = load_script(SCRIPT)


def expected(frequency: float) -> complex:
    omega = 2 * math.pi * frequency
    z_c = 1 / (1j * omega * C)
    z_node = 1 / (1 / z_c + 1 / LOAD + 1j * omega * C_IN)
    h = z_node / (RS + R + z_node)
    ac = 1j * frequency / AC_CORNER / (1 + 1j * frequency / AC_CORNER)
    return AMPLITUDE * h * ac


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(
        script, ["TCPIP0::192.168.1.50::INSTR"], BENCH, tmp_path, monkeypatch, capsys, rm_wrap=False
    )
    assert out[0] == "Stanford_Research_Systems,SR865A,003210,v1.55"
    points = [[float(x) for x in line.split(",")] for line in out[1:-2]]
    assert len(points) == len(script.FREQUENCIES)
    for (frequency, magnitude, theta), want in zip(points, map(expected, script.FREQUENCIES), strict=True):
        assert magnitude == pytest.approx(abs(want), rel=2e-3), frequency
        assert theta == pytest.approx(math.degrees(cmath.phase(want)), abs=0.1), frequency
    corner = points[script.FREQUENCIES.index(1515.76)]
    assert corner[1] == pytest.approx(AMPLITUDE / math.sqrt(2), rel=2e-3)
    assert corner[2] == pytest.approx(-45.0, abs=0.1)
    _, phase, theta = out[-2].split(",")
    assert float(phase) == pytest.approx(points[-1][2], abs=0.05)  # APHS took theta at 10 kHz
    assert float(theta) == pytest.approx(0.0, abs=0.01)
    assert out[-1] == "aux,1.5,1.4985015"


def _session(**state):
    data = yaml.safe_load(BENCH.read_text("utf-8"))
    data["instruments"]["lia"]["state"].update(state)
    return open_bench(BenchDefinition.model_validate(data))


def _q(session, text: str) -> str:
    session.write("lia", (text + "\n").encode())
    reply = session.read("lia")
    assert reply is not None, text
    return reply.decode().strip()


def test_the_input_range_overloads_on_the_signal_peak():
    session = _session()  # 0.5 V rms: about 0.59 V peak after the RC at 1 kHz
    try:
        assert _q(session, "CUROVLDSTAT?") == "0"
        session.write("lia", b"IRNG 300MVOLT\n")  # 0.3 V range: the peak overloads it
        assert int(_q(session, "CUROVLDSTAT?")) & 16
        session.write("lia", b"IRNG 0\n")
        assert _q(session, "CUROVLDSTAT?") == "0"
    finally:
        session.close()


def test_the_current_input_reads_amperes(tmp_path):
    """IVMD CURR: SIG_IN is 100 Ohm to virtual ground on the 1 uA range; 1 V through the RC's
    1.05 kOhm (50 Ohm source + 1 kOhm) drives about 0.94 mA into it at DC-ish frequencies."""
    session = _session(**{"reference.frequency": 10.0})
    try:
        # 1 s and 24 dB/oct: the 2f ripple at 10 Hz is far below the reading
        session.write("lia", b"IVMD CURR;ICUR 1MEG;OFLT 12;OFSL 3\n")
        session.advance(20.0)
        r = float(_q(session, "OUTP? R"))
        # the capacitor is nearly open at 10 Hz: 0.5 V / (1050 + 100) Ohm
        assert r == pytest.approx(AMPLITUDE / (RS + R + 100.0), rel=5e-3)
    finally:
        session.close()


def test_the_input_noise_is_the_manual_value():
    """#744: the SR865A's front end answers its defined input noise, the manual's 2.5 nV/√Hz
    (typical at 1 kHz, 10 mV range) — the distribution itself is the lock-in component's, covered
    on sb-lia-1 and on the SR830 through its buffer."""
    session = open_bench(BENCH)
    try:
        assert float(_q(session, "OUTP? XNOise")) == pytest.approx(2.5e-9)
        assert float(_q(session, "OUTP? YNOise")) == pytest.approx(2.5e-9)
    finally:
        session.close()
