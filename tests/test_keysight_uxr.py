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


def test_the_record_noise_matches_the_table(tmp_path):
    """#745's statistical acceptance on the UXR: a DC level captured repeatedly scatters with the
    definition's noise on the 0.1 V/div range (the table's 2.5 mV rms, quantized to the 0.78 mV
    codes), and the mean stays at the true level."""
    import math

    import yaml
    from refcalc.noise import quantized_sigma, sigma_interval

    data = yaml.safe_load(CASES_BENCH.read_text(encoding="utf-8"))
    data["instruments"]["awg"]["state"] = {"ch1.function": "DC", "ch1.offset": 0.1, "ch1.output": True}
    bench = tmp_path / "uxr.bench.yaml"
    bench.write_text(yaml.safe_dump(data), encoding="utf-8")
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
    try:
        uxr.write(":CHANnel1:SCALe 0.1;:ACQuire:POINts 2000")
        captures = []
        for _ in range(3):
            uxr.write(":DIGitize CHANnel1")
            _times, volts, _wfm = reader.read_waveform(uxr, "CHANnel1")
            captures.append(np.asarray(volts))
        assert not np.array_equal(captures[0], captures[1])  # each capture draws anew
        samples = np.concatenate(captures)
        # 0.1 V of DC from the generator's 50 ohm into the scope's DC 50 ohm
        assert abs(float(np.mean(samples)) - 0.1) < 5 * 0.0025 / math.sqrt(samples.size) + 0.1 / 128 / 2
        lo, hi = sigma_interval(quantized_sigma(0.0025, 0.1 / 128), samples.size)
        assert lo <= float(np.std(samples)) <= hi, (float(np.std(samples)), lo, hi)
        # the data-sheet jitter reached the acquisition: 25 fs is far below the record's ns
        # timestamps, so the wiring is what a test can pin (the data cannot show it)
        assert rm.visalib.bench.instruments["scope"].components["acq"].param("trigger_jitter_s") == 2.5e-14
    finally:
        rm.close()
