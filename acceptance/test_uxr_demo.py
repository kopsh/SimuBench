"""The UXR1104B demonstration (docs/demo-uxr.md): examples/demo/uxr_demo.py on its bench.

The script is written the way it runs on a real UXR: PyVISA and the Programmer's Guide commands,
no SimuBench import. One run, then its results are compared with the signals the two 33622A
generators drive (examples/demo/uxr.bench.yaml): a 10 MHz sine and a 1 MHz square of 400 mVpp,
a 500 kHz triangle of 300 mVpp, a 2 MHz pulse from 0 V to 200 mV at 20 % duty, all into 50 ohm.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "demo" / "uxr.bench.yaml"
SCRIPT = ROOT / "examples" / "demo" / "uxr_demo.py"


def _script():
    spec = importlib.util.spec_from_file_location("uxr_demo", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """The script's main(), as ``python uxr_demo.py scope --bench … --csv …`` runs it."""
    out = tmp_path_factory.mktemp("uxr") / "ch4.csv"
    argv = sys.argv
    sys.argv = [SCRIPT.name, "scope", "--bench", str(BENCH), "--csv", str(out)]
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed):
            result = _script().main()
    finally:
        sys.argv = argv
    return result, printed.getvalue(), out


def test_the_script_runs_without_errors(demo):
    result, printed, out = demo
    assert result["idn"].startswith("Keysight Technologies,UXR1104B,")
    assert result["errors"] == []
    assert "7. :SYSTem:ERRor? 0, no errors" in printed
    assert out.read_text("utf-8").splitlines()[0] == "time_s,volts"


def test_autoscale_finds_the_four_signals(demo):
    auto = demo[0]["autoscale"]
    assert all(ch["display"] for ch in auto["channels"].values())
    # each peak-to-peak over at most six divisions (the next 1-2-5 step), centred
    assert [auto["channels"][n]["scale"] for n in (1, 2, 3, 4)] == [0.1, 0.1, 0.1, 0.05]
    assert auto["channels"][4]["offset"] == pytest.approx(0.1, abs=0.005)
    assert auto["trigger"] == "CHAN4"  # the trigger search starts at channel 4
    assert auto["timebase"] == pytest.approx(5e-8)  # three periods of CH1's 10 MHz


def test_the_measurements_match_the_generators(demo):
    rows = demo[0]["measurements"]
    for n, freq in zip((1, 2, 3, 4), (1e7, 1e6, 5e5, 2e6), strict=True):
        assert rows[n]["freq_hz"] == pytest.approx(freq, rel=5e-3)
    # AC RMS over whole cycles: sine A/(2*sqrt 2), square A/2, triangle A/(2*sqrt 3), pulse
    # A*sqrt(d(1-d)); the range noise (6.1 mV rms at 100 mV/div) adds in quadrature
    truth = {1: 0.4 / (2 * math.sqrt(2)), 2: 0.2, 3: 0.3 / (2 * math.sqrt(3)), 4: 0.2 * math.sqrt(0.2 * 0.8)}
    for n, rms in truth.items():
        assert rows[n]["vrms_ac_v"] == pytest.approx(rms, rel=0.03)
    assert rows[2]["vamp_v"] == pytest.approx(0.4, abs=0.01)
    assert rows[4]["vamp_v"] == pytest.approx(0.2, abs=0.01)
    assert rows[2]["duty_pct"] == pytest.approx(50.0, abs=1.0)
    assert rows[4]["duty_pct"] == pytest.approx(20.0, abs=1.0)
    # a sine's 10-90 % rise is 2 asin(0.8) / (2 pi f); the pulse's edge is the generator's 10 ns
    assert rows[1]["rise_s"] == pytest.approx(2 * math.asin(0.8) / (2 * math.pi * 1e7), rel=0.1)
    assert rows[4]["rise_s"] == pytest.approx(1e-8, rel=0.15)
    # the 33622A's square: rolled off at its 120 MHz bandwidth, the data sheet's 2.9 ns edge
    assert rows[2]["rise_s"] == pytest.approx(2.9e-9, rel=0.1)


def test_the_fft_shows_the_square_odd_harmonics(demo):
    peaks = demo[0]["fft"]
    assert len(peaks) == 3
    for (freq, _), harmonic in zip(peaks, (1e6, 3e6, 5e6), strict=True):
        # the FFT takes 16384 of the 20 000 points (a power of two): 122 kHz bins at 2 GSa/s
        assert freq == pytest.approx(harmonic, abs=6.2e4)
    fundamental = peaks[0][1]
    # 4/pi * 0.2 V peak into 50 ohm: -1.9 dBm; the harmonics at 1/3 and 1/5 of it
    assert fundamental == pytest.approx(10 * math.log10((0.8 / math.pi) ** 2 / 2 / 50 / 1e-3), abs=0.5)
    assert peaks[1][1] - fundamental == pytest.approx(20 * math.log10(1 / 3), abs=1.5)
    assert peaks[2][1] - fundamental == pytest.approx(20 * math.log10(1 / 5), abs=1.5)


def test_the_waveform_readback_is_the_pulse(demo):
    times, volts = demo[0]["waveform"]
    assert volts.size == 20000
    assert times[-1] - times[0] == pytest.approx(1e-5, rel=1e-3)
    high = volts > 0.1
    assert high.mean() == pytest.approx(0.2, abs=0.01)
    # the levels: medians, past the 10 ns edges and the overshoot after them
    assert np.median(volts[high]) == pytest.approx(0.2, abs=0.005)
    assert np.median(volts[~high]) == pytest.approx(0.0, abs=0.005)
