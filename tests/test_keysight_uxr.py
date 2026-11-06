"""Keysight Infiniium UXR0334B (issue 338): the guide's procedures, against the engine.

The manual cases (tests/models/keysight-uxr0334b/cases.yaml) check command by command; these tests
run the Programmer's Guide waveform reading (examples/scripts/keysight_uxr_read_waveform.py) and its
polling synchronization (:SINGle, then :ADER? until 1), and compare what they read with the signal
the bench carries.
"""

from pathlib import Path

import numpy as np
import pytest
import pyvisa
import yaml

from helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
CASES_BENCH = ROOT / "tests" / "models" / "keysight-uxr0334b" / "bench.yaml"
EXAMPLE_BENCH = ROOT / "examples" / "benches" / "keysight-uxr0334b.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_uxr_read_waveform.py"

reader = load_script(SCRIPT)


@pytest.fixture
def scope():
    rm = pyvisa.ResourceManager(f"{CASES_BENCH}@simubench")
    uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
    uxr.write(":CHANnel1:SCALe 0.1;:ACQuire:POINts 2000")
    yield uxr, rm.visalib.bench
    rm.close()


def test_guide_procedure_reconstructs_the_bench_signal(scope):
    uxr, session = scope
    times, volts, wfm = reader.read_waveform(uxr, "CHANnel1")
    rec = session.instruments["scope"].components["acq"].records[1]
    assert wfm["points"] == len(volts) == rec.volts.size == 2000
    assert wfm["format"] == 2 and wfm["coupling"] == 2  # WORD, DC 50 ohm
    # every word is a record sample: 10-bit codes, 128 per division, shifted to 16 bits (the
    # preamble's Y increment has seven digits, so the reconstruction is exact to far below an LSB)
    lsb = 0.1 / 128
    assert np.max(np.abs(volts - rec.volts)) < lsb * 1e-3
    # the 10 MHz, 400 mVpp sine the generator drives into 50 ohm: its peaks and its period
    assert volts.max() == pytest.approx(0.2, abs=lsb + 0.005)
    assert volts.min() == pytest.approx(-0.2, abs=lsb + 0.005)
    rising = times[1:][(volts[:-1] < 0) & (volts[1:] >= 0)]
    assert np.diff(rising).mean() == pytest.approx(1e-7, rel=1e-2)
    assert times[1] - times[0] == pytest.approx(wfm["xinc"])
    assert reader.errors(uxr) == []


def test_polling_synchronization_reports_the_acquisition_done(scope):
    """The guide's "Polling Synchronization With Timeout": :STOP;*OPC?, clear :ADER?, :SINGle, poll."""
    uxr, _ = scope
    assert uxr.query(":STOP;*OPC?").strip() == "1"
    uxr.query(":ADER?")
    uxr.write(":SINGle")
    for _ in range(50):
        if uxr.query(":ADER?").strip() == "1":
            break
    else:
        pytest.fail(":ADER? never reported the acquisition done")
    assert float(uxr.query(":MEASure:FREQuency? CHANnel1")) == pytest.approx(1.0e7, rel=1e-3)
    assert uxr.query(":ADER?").strip() == "0"


def test_example_bench_runs_the_script():
    rm = pyvisa.ResourceManager(f"{EXAMPLE_BENCH}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
        uxr.write(":ACQuire:POINts 2000")
        _, volts, wfm = reader.read_waveform(uxr, "CHANnel2")
        assert wfm["xinc"] == pytest.approx(5e-10)  # 2000 points over 10 x 100 ns/div: 2 GSa/s
    finally:
        rm.close()
    assert volts.max() == pytest.approx(0.2, abs=0.05)
    assert volts.min() == pytest.approx(-0.2, abs=0.05)


def test_the_captures_scatter_by_the_table_noise_around_the_true_level(tmp_path):
    """#745/#774's statistical acceptance: the same DC captured repeatedly through the guide's
    waveform procedure — the samples scatter with the definition's noise on the 0.1 V/div range
    (the table's 1.48 mV rms — the published 50 mV/div floor, #774 — quantized to the 10-bit
    781 µV codes), the mean stays at the true level, and every capture draws anew."""
    import math

    from refcalc.noise import quantized_sigma, sigma_interval

    data = yaml.safe_load(EXAMPLE_BENCH.read_text("utf-8"))
    data["instruments"]["awg"]["state"].update({"ch1.function": "DC", "ch1.offset": 0.2})
    bench = tmp_path / "bench.yaml"
    bench.write_text(yaml.safe_dump(data), "utf-8")
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
        uxr.write(":ACQuire:POINts 2000")
        captures = []
        for _ in range(3):
            uxr.write(":DIGitize CHANnel1")
            _times, volts, _wfm = reader.read_waveform(uxr, "CHANnel1")
            captures.append(np.asarray(volts))
        assert not np.array_equal(captures[0], captures[1])  # each capture draws anew
        samples = np.concatenate(captures)
        # DC 0.2 V at the generator, its 50 ohm load setting matched by the scope's 50 ohm input
        truth = 0.2
        # the mean sits at the true level: 5 rms of the sample mean plus half a code of bias
        assert abs(float(np.mean(samples)) - truth) < 5 * 1.48e-3 / math.sqrt(samples.size) + 0.1 / 128 / 2
        lo, hi = sigma_interval(quantized_sigma(1.48e-3, 0.1 / 128), samples.size)
        assert lo <= float(np.std(samples)) <= hi, (float(np.std(samples)), lo, hi)
    finally:
        rm.close()


def test_the_records_time_origin_scatters_by_the_jitter_spec(scope):
    """#774: the data sheet's 25 fs intrinsic jitter (specs.trigger_jitter_s_rms) lands in every
    record's time origin — over repeated acquisitions of the same repeating trigger the origins
    scatter by exactly it (the trigger's interpolated phase repeats; the jitter draws anew)."""
    from refcalc.noise import sigma_interval

    uxr, session = scope
    origins = []
    for _ in range(24):
        uxr.write(":DIGitize CHANnel1")
        uxr.query(":WAVeform:DATA?")
        origins.append(session.instruments["scope"].components["acq"].records[1].origin)
    lo, hi = sigma_interval(2.5e-14, len(origins))
    assert lo <= float(np.std(origins)) <= hi, (float(np.std(origins)), lo, hi)
