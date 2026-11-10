"""Teledyne LeCroy WavePro 804HD (issue 883): the MAUI manual's waveform transfer, against the engine.

The manual cases (tests/models/lecroy-wavepro804hd/cases.yaml) check command by command; this test
runs the manual's waveform reading (examples/scripts/lecroy_wavepro_read_waveform.py):
``C1:INSPECT? "WAVEDESC"`` for the descriptor, ``C1:INSPECT? "DATA_ARRAY_1",FLOAT`` for the text
volts, and HORIZONTAL_INTERVAL/HORIZ_OFFSET for the time axis, and compares what it reads with the
signal the bench carries.
"""

from pathlib import Path

import numpy as np
import pytest
import pyvisa

from helpers import load_script, run_manual_script

ROOT = Path(__file__).resolve().parents[1]
CASES_BENCH = ROOT / "tests" / "models" / "lecroy-wavepro804hd" / "bench.yaml"
EXAMPLE_BENCH = ROOT / "examples" / "benches" / "lecroy-wavepro804hd.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "lecroy_wavepro_read_waveform.py"

reader = load_script(SCRIPT)


@pytest.fixture
def scope():
    rm = pyvisa.ResourceManager(f"{CASES_BENCH}@simubench")
    wps = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
    wps.write("C1:VDIV 500MV;TDIV 500US")
    yield wps, rm.visalib.bench
    rm.close()


def test_the_manual_procedure_reconstructs_the_bench_signal(scope):
    wps, session = scope
    times, volts, desc = reader.read_waveform(wps, "C1")
    rec = session.instruments["scope"].components["acq"].records[1]
    assert desc["WAVE_ARRAY_COUNT"] == len(volts) == rec.volts.size == 10_000
    # text volts carry six fractional digits; every sample is the record's to that rounding
    assert np.max(np.abs(volts - rec.volts)) < 2e-5
    assert desc["HORIZ_INTERVAL"] == pytest.approx(rec.dt_s)
    assert times[0] == pytest.approx(rec.origin)
    assert times[1] - times[0] == pytest.approx(desc["HORIZ_INTERVAL"])
    # the 1 kHz, 2 Vpp sine the generator drives into 1 MΩ: its peaks and its frequency
    assert volts.max() == pytest.approx(1.0, abs=0.05)
    assert volts.min() == pytest.approx(-1.0, abs=0.05)
    freqs = np.fft.rfftfreq(volts.size, desc["HORIZ_INTERVAL"])
    assert freqs[np.argmax(np.abs(np.fft.rfft(volts)))] == pytest.approx(1e3, rel=1e-2)


def test_the_example_bench_runs_the_script(tmp_path, monkeypatch, capsys):
    out = run_manual_script(reader, ["scope", "C1"], EXAMPLE_BENCH, tmp_path, monkeypatch, capsys)
    assert out[0] == "LECROY,WAVEPRO804HD,WP804HD0001,10.3.0.0"
    assert "10000 points" in out[1] and "2e+06 Sa/s" in out[1]
