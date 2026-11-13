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
# The 110 GHz UXR1104B (#1092): the same command set, its own ranges and noise table.
EXAMPLE_BENCH_1104B = ROOT / "examples" / "benches" / "keysight-uxr1104b.bench.yaml"
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
    # the 10 MHz, 400 mVpp sine the generator drives into 50 ohm: its peaks and its period.
    # The range's noise (the data sheet's 2.91 mV rms, #786) rides the ~270 samples that dwell
    # within one sigma of each peak: their expected worst excursion is ~2.9 sigma = 8.4 mV.
    assert volts.max() == pytest.approx(0.2, abs=lsb + 0.01)
    assert volts.min() == pytest.approx(-0.2, abs=lsb + 0.01)
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


@pytest.mark.parametrize("example", [EXAMPLE_BENCH, EXAMPLE_BENCH_1104B], ids=["uxr0334b", "uxr1104b"])
def test_example_bench_runs_the_script(example):
    rm = pyvisa.ResourceManager(f"{example}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
        uxr.write(":ACQuire:POINts 2000")
        _, volts, wfm = reader.read_waveform(uxr, "CHANnel2")
        assert wfm["xinc"] == pytest.approx(5e-10)  # 2000 points over 10 x 100 ns/div: 2 GSa/s
    finally:
        rm.close()
    assert volts.max() == pytest.approx(0.2, abs=0.05)
    assert volts.min() == pytest.approx(-0.2, abs=0.05)


@pytest.mark.parametrize(
    ("example", "scale", "level", "sigma"),
    [
        (EXAMPLE_BENCH, 0.1, 0.2, 2.91e-3),
        (EXAMPLE_BENCH, 0.01, 0.02, 3.88e-4),
        (EXAMPLE_BENCH_1104B, 0.1, 0.2, 6.1e-3),
        (EXAMPLE_BENCH_1104B, 0.01, 0.02, 8.6e-4),
    ],
    ids=["uxr0334b-100mV", "uxr0334b-10mV", "uxr1104b-100mV", "uxr1104b-10mV"],
)
def test_the_captures_scatter_by_the_table_noise_around_the_true_level(
    tmp_path, example, scale, level, sigma
):
    """#745/#774/#786's statistical acceptance: the same DC captured repeatedly through the
    guide's waveform procedure — the samples scatter with the definition's noise on the range
    (the Infiniium UXR-B data sheet's measured RMS Noise Floor table: UXR0334B column 2.91 mV
    rms at 100 mV/div and 388 µV rms at 10 mV/div; UXR1104B column (#1092) 6.1 mV at 100 mV/div
    and, below 12.5 mV/div, the 60 mV full-scale range's 860 µV; quantized to the 10-bit codes),
    the mean stays at the true level, and every capture draws anew."""
    import math

    from refcalc.noise import quantized_sigma, sigma_interval

    data = yaml.safe_load(example.read_text("utf-8"))
    data["instruments"]["awg"]["state"].update({"ch1.function": "DC", "ch1.offset": level})
    bench = tmp_path / "bench.yaml"
    bench.write_text(yaml.safe_dump(data), "utf-8")
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
        uxr.write(f":ACQuire:POINts 2000;:CHANnel1:SCALe {scale}")
        captures = []
        for _ in range(3):
            uxr.write(":DIGitize CHANnel1")
            _times, volts, _wfm = reader.read_waveform(uxr, "CHANnel1")
            captures.append(np.asarray(volts))
        assert not np.array_equal(captures[0], captures[1])  # each capture draws anew
        samples = np.concatenate(captures)
        # the generator's DC into its matched 50 ohm load setting, the scope's 50 ohm input
        # the mean sits at the true level: 5 rms of the sample mean plus half a code of bias
        assert abs(float(np.mean(samples)) - level) < 5 * sigma / math.sqrt(samples.size) + scale / 128 / 2
        lo, hi = sigma_interval(quantized_sigma(sigma, scale / 128), samples.size)
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


def test_uxr1104b_reference_input_and_memory_option(tmp_path):
    """UXR1104B (#1092): :TIMebase:REFClock ON locks the sampling clock to a 10 MHz on REF IN within
    the guide's +-20 ppm, and a unit with option 02G reports it in *OPT? and its memory capability."""
    bench = tmp_path / "ref.bench.yaml"
    bench.write_text(
        yaml.safe_dump(
            {
                "schema": "simubench.bench/2",
                "name": "uxr_ref",
                "instruments": {
                    "awg": {"model": "keysight-33622a",
                            "state": {"ch1.frequency": 10e6 * (1 + 15e-6), "ch1.amplitude": 0.4, "ch1.output": True}},
                    "scope": {"model": "keysight-uxr1104b", "options": ["02G"]},
                },
                "wires": [{"from": "awg.CH1", "to": "scope.REF IN"}],
                "run": {"seed": 1, "clock": "command", "idle": "off"},
            }
        )
    )  # fmt: skip
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
        session = rm.visalib.bench
        timebase = session.instruments["scope"].components["timebase"]
        internal = timebase.clock_error()
        uxr.write(":TIMebase:REFClock ON")
        # locked: the clock carries the reference's +15 ppm
        assert timebase.clock_error() == pytest.approx(15e-6, abs=1e-9)
        session.configure("awg", "ch1.frequency", 10e6 * (1 + 30e-6))  # outside +-20 ppm
        assert timebase.clock_error() == pytest.approx(internal)
        uxr.write(":TIMebase:REFClock OFF")
        assert timebase.clock_error() == pytest.approx(internal)
        assert uxr.query("*OPT?").strip() == "02G"
        assert uxr.query(":SYSTem:CAPability:ACQuire? MEMory").strip() == '"2000000000"'
        assert uxr.query(":SYSTem:ERRor?").strip() == "0"
    finally:
        rm.close()
