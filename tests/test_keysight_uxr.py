"""Keysight Infiniium UXR0334B (issue 338): the guide's procedures, against the engine.

The manual cases (tests/models/keysight-uxr0334b/cases.yaml) check command by command; these tests
run the Programmer's Guide waveform reading (examples/scripts/keysight_uxr_read_waveform.py) and its
polling synchronization (:SINGle, then :ADER? until 1), and compare what they read with the signal
the bench carries.
"""

import math
from pathlib import Path

import numpy as np
import pytest
import pyvisa
import yaml

from helpers import load_script
from simubench.library.loader import DefinitionLibrary

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


@pytest.mark.parametrize("def_id", ["keysight-uxr0334b", "keysight-uxr1104b"])
def test_the_bandwidth_filter_ceiling_is_the_spec_bandwidth(def_id):
    """#1098: :ACQuire:BANDwidth's MAX (and the setting's range ceiling) is the model's data-sheet
    typical bandwidth (specs.bandwidth_hz), with the warranted bandwidth inside it — so the cases'
    MAX readback and the out-of-range rejection stay tied to the bandwidth the model simulates."""
    definition = DefinitionLibrary().get(def_id)
    bandwidth = definition.state["acquire.bandwidth"]
    assert bandwidth.special["MAX"] == bandwidth.max == definition.specs["bandwidth_hz"]
    assert definition.specs["warranted_bandwidth_hz"] <= bandwidth.max


def test_the_records_time_origin_scatters_by_the_jitter_spec(tmp_path):
    """#774: the data sheet's 25 fs intrinsic jitter (specs.trigger_jitter_s_rms) lands in every
    record's time origin — over repeated acquisitions of the same repeating trigger the origins
    scatter by exactly it (the trigger's interpolated phase repeats; the jitter draws anew). The
    trigger path's own noise (#1263) moves the trigger instant by its noise over the edge's slope
    besides; it is switched off here (a hidden parameter) to see the intrinsic term alone."""
    from refcalc.noise import sigma_interval

    data = yaml.safe_load(CASES_BENCH.read_text("utf-8"))
    data["instruments"]["scope"]["hidden"] = {"trigger": {"noise_table": None}}
    bench = tmp_path / "bench.yaml"
    bench.write_text(yaml.safe_dump(data), "utf-8")
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
    uxr.write(":CHANnel1:SCALe 0.1;:ACQuire:POINts 2000")
    session = rm.visalib.bench
    origins = []
    for _ in range(24):
        uxr.write(":DIGitize CHANnel1")
        uxr.query(":WAVeform:DATA?")
        origins.append(session.instruments["scope"].components["acq"].records[1].origin)
    rm.close()
    lo, hi = sigma_interval(2.5e-14, len(origins))
    assert lo <= float(np.std(origins)) <= hi, (float(np.std(origins)), lo, hi)


# The UXR1104B's CAL OUT follows the guide's levels (ZERO -150 mV, the square -150 to +150 mV on CAL OUT,
# -440 to -125 mV on AUX OUT, guide 11.80 p. 473); its manual cases loop both outputs back into CH3 and CH4.
@pytest.mark.parametrize("def_id", ["keysight-uxr0334b"])
def test_the_front_panel_outputs_drive_what_a_second_scope_measures(tmp_path, def_id):
    """#1170: one UXR's CAL OUT and AUX OUT carry the :CALibrate:OUTPut signal into another's
    50 ohm input — DC levels (CAL's commanded, AUX's fixed ZERO of -440 mV, guide p. 473), the
    enables, and the 750 Hz probe-compensation square."""
    bench = {
        "schema": "simubench.bench/2",
        "name": "uxr_cal_out",
        "instruments": {"src": {"model": def_id}, "scope": {"model": def_id}},
        "wires": [{"from": "src.CAL_OUT", "to": "scope.CH1"}],
        "run": {"seed": 1, "clock": "command", "idle": "off"},
    }
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(bench), "utf-8")
    rm = pyvisa.ResourceManager(f"{path}@simubench")
    try:
        src = rm.open_resource("src", read_termination="\n", write_termination="\n", timeout=5000)
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
        uxr.write(":ACQuire:POINts 2000;:CHANnel1:SCALe 0.2;:TIMebase:SCALe 2e-4")

        def read() -> np.ndarray:
            uxr.write(":DIGitize CHANnel1")
            return np.asarray(reader.read_waveform(uxr, "CHANnel1")[1])

        tol = 6e-3  # five sigma of the 200 mV/div noise on 2000 samples, and the code step
        assert abs(read().mean()) < tol  # the output is off at power-up
        src.write(":CALibrate:OUTPut DC,0.5;:CALibrate:OUTPut:CAL ON")
        assert abs(read().mean() - 0.5) < tol  # the commanded level, into 50 ohm
        src.write(":CALibrate:OUTPut ZERO")
        assert abs(read().mean() + 0.44) < tol  # ZERO: -440 mV
        src.write(":CALibrate:OUTPut:CAL OFF")
        assert abs(read().mean()) < tol  # disabled again
        src.write(":CALibrate:OUTPut PROBecomp;:CALibrate:OUTPut:CAL ON")
        volts = read()
        # a 0 to 0.5 V square: the 750 Hz spends half the time at each level
        assert abs(volts.mean() - 0.25) < 0.02
        assert np.percentile(volts, 10) < 0.05 and np.percentile(volts, 90) > 0.45
        src.write(":CALibrate:OUTPut DC,2")
        assert src.query(":SYSTem:ERRor?").startswith("-222")  # beyond ±1.2 V
        src.write(":CALibrate:OUTPut NOPE")
        assert src.query(":SYSTem:ERRor?").startswith("-224")
    finally:
        rm.close()


@pytest.mark.parametrize("def_id", ["keysight-uxr0334b", "keysight-uxr1104b"])
def test_the_aux_output_has_its_own_enable_and_a_fixed_dc_level(tmp_path, def_id):
    bench = {
        "schema": "simubench.bench/2",
        "name": "uxr_aux_out",
        "instruments": {"src": {"model": def_id}, "scope": {"model": def_id}},
        "wires": [{"from": "src.AUX_OUT", "to": "scope.CH1"}],
        "run": {"seed": 1, "clock": "command", "idle": "off"},
    }
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(bench), "utf-8")
    rm = pyvisa.ResourceManager(f"{path}@simubench")
    try:
        src = rm.open_resource("src", read_termination="\n", write_termination="\n", timeout=5000)
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
        uxr.write(":ACQuire:POINts 2000;:CHANnel1:SCALe 0.2")

        def read() -> float:
            uxr.write(":DIGitize CHANnel1")
            return float(np.mean(reader.read_waveform(uxr, "CHANnel1")[1]))

        src.write(":CALibrate:OUTPut DC,0.5;:CALibrate:OUTPut:CAL ON;:CALibrate:OUTPut:AUX OFF")
        assert abs(read()) < 4e-3  # CAL is on, but the cable is on AUX, which is off
        src.write(":CALibrate:OUTPut:AUX ON")
        assert abs(read() + 0.44) < 4e-3  # AUX's DC is the fixed ZERO level whatever the level asked
    finally:
        rm.close()


@pytest.mark.parametrize(
    ("def_id", "one_level", "output_after_reset", "aux_after_reset", "cal_after_reset"),
    [
        ("keysight-uxr0334b", 0.44, '"ZERO"', "0", "0"),
        ("keysight-uxr1104b", -0.125, "PROB", "1", "1"),
    ],
    ids=["uxr0334b", "uxr1104b"],
)
def test_the_front_panel_signals_follow_the_guide(
    tmp_path, def_id, one_level, output_after_reset, aux_after_reset, cal_after_reset
):
    """#1173: the AUX OUT clocks, ONE level and FAST/SLOW edges of both UXR models, and *RST.

    The clocks are read back through the scope's own measurements: MHZ97 and MHZ7_5 at their
    frequencies, ONE at its level (the guide's p. 473 levels differ per model), and the AUX OUT
    rise time much shorter on FAST than on SLOW (the guide's edge-speed setting).
    """
    bench = {
        "schema": "simubench.bench/2",
        "name": "uxr_front_panel",
        "instruments": {"src": {"model": def_id}, "scope": {"model": def_id}},
        "wires": [{"from": "src.AUX_OUT", "to": "scope.CH1"}],
        "run": {"seed": 1, "clock": "command", "idle": "off"},
    }
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(bench), "utf-8")
    rm = pyvisa.ResourceManager(f"{path}@simubench")
    try:
        src = rm.open_resource("src", read_termination="\n", write_termination="\n", timeout=5000)
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
        uxr.write(":ACQuire:POINts 2000;:CHANnel1:SCALe 0.2;:TIMebase:SCALe 2e-7")
        src.write(":CALibrate:OUTPut MHZ97;:CALibrate:OUTPut:AUX ON")

        def measure(what: str) -> float:
            return float(uxr.query(f":MEASure:{what}? CHANnel1"))

        assert measure("FREQuency") == pytest.approx(97e6, rel=1e-2)
        src.write(":CALibrate:OUTPut MHZ7_5")
        assert measure("FREQuency") == pytest.approx(7.5e6, rel=1e-2)

        # The AUX OUT edges: FAST is much shorter than SLOW, on the 97 MHz clock (a periodic
        # selection, where the guide makes the setting visible).
        # The full 256 GSa/s (12800 points over 50 ns): the guide times an edge only with four or
        # more samples on it (#1332), and the 1104B's FAST edge is shorter than two 2000-point steps.
        src.write(":CALibrate:OUTPut MHZ97;:CALibrate:OUTPut:AUX:RTIMe FAST")
        uxr.write(":TIMebase:SCALe 5e-9;:ACQuire:POINts 12800")
        fast = measure("RISetime")
        src.write(":CALibrate:OUTPut:AUX:RTIMe SLOW")
        slow = measure("RISetime")
        assert fast > 0 and slow > 3 * fast, (fast, slow)

        # ONE is a constant at the model's own level (the guide gives -440 to -125 mV on AUX OUT).
        src.write(":CALibrate:OUTPut ONE")
        uxr.write(":TIMebase:SCALe 2e-6")
        assert float(uxr.query(":MEASure:VAVerage? DISPlay,CHANnel1")) == pytest.approx(one_level, abs=0.02)

        # *RST puts the selection and both enables back to the model's power-on values.
        src.write("*RST")
        assert src.query(":CALibrate:OUTPut?").strip() == output_after_reset
        assert src.query(":CALibrate:OUTPut:AUX?").strip() == aux_after_reset
        assert src.query(":CALibrate:OUTPut:CAL?").strip() == cal_after_reset
    finally:
        rm.close()


@pytest.mark.parametrize("def_id", ["keysight-uxr0334b", "keysight-uxr1104b"])
def test_the_probe_compensation_square_keeps_its_phase_and_seed(tmp_path, def_id):
    """#1173: PROBecomp is the guide's ~750 Hz square, its half-period edges spaced by the same
    interval in every record (aligned to virtual time), and two benches with the same seed read
    exactly the same values."""
    bench = {
        "schema": "simubench.bench/2",
        "name": "uxr_probe_comp",
        "instruments": {"src": {"model": def_id}, "scope": {"model": def_id}},
        "wires": [{"from": "src.CAL_OUT", "to": "scope.CH1"}],
        "run": {"seed": 1, "clock": "command", "idle": "off"},
    }

    def capture() -> tuple[np.ndarray, np.ndarray]:
        path = tmp_path / "bench.yaml"
        path.write_text(yaml.safe_dump(bench), "utf-8")
        rm = pyvisa.ResourceManager(f"{path}@simubench")
        try:
            src = rm.open_resource("src", read_termination="\n", write_termination="\n", timeout=5000)
            uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
            uxr.write(":ACQuire:POINts 2000;:CHANnel1:SCALe 0.2;:TIMebase:SCALe 2e-4")
            src.write(":CALibrate:OUTPut PROB;:CALibrate:OUTPut:CAL ON")
            times, volts, _ = reader.read_waveform(uxr, "CHANnel1")
            return np.asarray(times), np.asarray(volts)
        finally:
            rm.close()

    times, volts = capture()
    _same_times, same_seed = capture()
    assert np.array_equal(volts, same_seed)
    mid = (float(volts.min()) + float(volts.max())) / 2.0
    crossings = np.nonzero(np.diff(np.sign(volts - mid)) != 0)[0]
    assert len(crossings) >= 2
    half_periods = np.diff(times[crossings])
    assert np.allclose(half_periods, 1.0 / 1500.0, rtol=1e-2), half_periods


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
                "wires": [{"from": "awg.CH1", "to": "scope.REF_IN"}],
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


def test_the_noise_falls_with_the_bandwidth_filters_after_the_adc():
    """#1262: :ACQuire:BANDwidth and InfiniiSim are DSP after the ADC. The noise added at the
    converter (the data sheet's 860 µV at full bandwidth, 10 mV/div) falls with √bandwidth through
    them; the internal probe sees the quantized codes at the ADC and the filtered stream after it."""
    from simubench.server.manager import ServedSession
    from simubench.server.services import internal_probe

    rm = pyvisa.ResourceManager(f"{EXAMPLE_BENCH_1104B}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
        uxr.write(":CHANnel3:DISPlay ON;:CHANnel3:SCALe 0.01;:CHANnel3:OFFSet 0")

        def noise() -> float:
            uxr.write(":DIGitize CHANnel3")
            _t, volts, _w = reader.read_waveform(uxr, "CHANnel3")
            return float(np.std(volts))

        # -- full bandwidth: the table's value, the filter passes the samples unchanged
        full = noise()
        assert full == pytest.approx(8.6e-4, rel=0.05)
        # -- each limit: within 10% of √(B / 113 GHz) of the full-bandwidth noise
        for bw in (5e10, 1e10, 1e9):
            uxr.write(f":ACQuire:BANDwidth {bw:g}")
            assert noise() == pytest.approx(8.6e-4 * math.sqrt(bw / 1.13e11), rel=0.10), bw
        # -- InfiniiSim's channel limit is a filter after the ADC as well
        uxr.write(":ACQuire:BANDwidth MAX;:CHANnel3:ISIM:BANDwidth 1E9;:CHANnel3:ISIM:BWLimit ON")
        assert noise() == pytest.approx(8.6e-4 * math.sqrt(1e9 / 1.13e11), rel=0.10)
        # -- the internal probe: codes at the ADC, the filtered stream after the InfiniiSim block
        served = ServedSession("t", rm.visalib.bench, None)
        codes = internal_probe.probe(served, "scope", "adc.out3")
        filtered = internal_probe.probe(served, "scope", "isim.out3")
        assert codes.format is not None and codes.format.bits == 10
        step = 0.01 / 128
        assert np.allclose(
            np.round(np.array(codes.waveform.volts) / step) * step, codes.waveform.volts, atol=1e-12
        )
        assert filtered.format is not None and filtered.format.bits == 0
        assert filtered.rms < codes.rms / 5
        assert reader.errors(uxr) == []
    finally:
        rm.close()


def test_an_input_beyond_its_limits_overloads_and_warns(tmp_path):
    """#1264, on the Hello World bench (the SDG2042X's CH1 into the UXR1104B's 50 ohm CH1): a 10 Vpp
    square at 10 mV/div is clipped, and beyond the maximum input range (offset ± 8 divisions, the
    data sheet) it sets CH1's bit of :OVLRegister? (read clears it); a 4 Vpp square on 0.5 V/div is
    within range. Its 5 V peak is below the absolute ±6.5 V, which no source of the bench reaches
    into 50 ohm: with the limit lowered to 4 V (a hidden parameter) the instrument warns of possible
    damage, as an event and in the session's problems; the error queue stays empty (the guide names
    no error for it)."""
    hello = ROOT / "src" / "simubench" / "library" / "examples" / "hello.bench.yaml"

    def run(bench: Path) -> tuple[list[str], dict[str, str], list[dict]]:
        rm = pyvisa.ResourceManager(f"{bench}@simubench")
        try:
            awg = rm.open_resource("awg", read_termination="\n", write_termination="\n", timeout=20000)
            uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
            awg.write("C1:BSWV WVTP,SQUARE,AMP,10,OFST,0")
            uxr.write(":CHAN1:SCAL 0.01;:ACQuire:POINts 2000;:DIG CHAN1")
            replies = [
                uxr.query(q).strip()
                for q in (":MEAS:VPP? CHAN1", ":CHANnel1:CLIPped?", ":OVLRegister?", ":OVLRegister?")
            ]
            awg.write("C1:BSWV AMP,4")
            uxr.write(":CHAN1:SCAL 0.5;:DIG CHAN1")
            replies += [uxr.query(":OVLRegister?").strip(), uxr.query(":SYSTem:ERRor? STR").strip()]
            session = rm.visalib.bench
            return (
                replies,
                dict(session.instruments["scope"].warnings),
                [e for e in session.history if e.get("kind") == "warning"],
            )
        finally:
            rm.close()

    replies, warnings, events = run(hello)
    # -- clipped at the converter's ±4 divisions: the measurement is the most accurate the scaling allows
    assert float(replies[0]) == pytest.approx(0.08, abs=0.002) and replies[1] == "1"
    # -- beyond the maximum input range: the channel's overload bit, cleared by the read; within it, none
    assert replies[2:5] == ["1", "0", "0"]
    assert replies[5] == '0,"No error"'
    assert warnings == {} and events == []
    # -- beyond the absolute maximum (lowered to 4 V): possible damage, kept and emitted once
    data = yaml.safe_load(hello.read_text("utf-8"))
    data["instruments"]["scope"]["hidden"] = {"ch1.front": {"max_input_v": 4.0}}
    lowered = tmp_path / "hello.bench.yaml"
    lowered.write_text(yaml.safe_dump(data), "utf-8")
    replies, warnings, events = run(lowered)
    assert list(warnings) == ["CH1-OVERVOLTAGE"] and "±4 V" in warnings["CH1-OVERVOLTAGE"]
    assert [e["code"] for e in events] == ["CH1-OVERVOLTAGE"]
    assert replies[5] == '0,"No error"'


def test_a_screen_frame_latches_no_overload_and_each_segment_does():
    """#1304: the overload check runs on acquisitions only. A running scope's screen frame
    (``compute`` with a frame number, which changes no state) latches nothing; a segmented
    acquisition — whose segments are acquisitions, not frames — is checked and sets the bit."""
    hello = ROOT / "src" / "simubench" / "library" / "examples" / "hello.bench.yaml"
    rm = pyvisa.ResourceManager(f"{hello}@simubench")
    try:
        awg = rm.open_resource("awg", read_termination="\n", write_termination="\n", timeout=20000)
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
        awg.write("C1:BSWV WVTP,SQUARE,AMP,10,OFST,0")
        uxr.write(":CHAN1:SCAL 0.01;:ACQuire:POINts 2000")
        acq = rm.visalib.bench.instruments["scope"].components["acq"]
        acq.compute([1], frame=3)
        assert acq.overloaded == set()
        assert uxr.query(":OVLRegister?").strip() == "0"
        uxr.write(":ACQuire:MODE SEGMented;:ACQuire:SEGMented:COUNt 2;:DIG CHAN1")
        assert uxr.query(":OVLRegister?").strip() == "1"
    finally:
        rm.close()


CASES_BENCH_1104B = ROOT / "tests" / "models" / "keysight-uxr1104b" / "bench.yaml"


def _bench_with(
    tmp_path: Path, name: str, hidden: dict | None = None, cases: Path = CASES_BENCH_1104B
) -> Path:
    """A UXR's cases bench (a 10 MHz, 400 mVpp sine on CH1), with ``hidden`` parameters."""
    data = yaml.safe_load(cases.read_text("utf-8"))
    if hidden:
        data["instruments"]["scope"]["hidden"] = hidden
    bench = tmp_path / name
    bench.write_text(yaml.safe_dump(data), "utf-8")
    return bench


def _triggers(bench: Path, setup: str, band: str = "MAX") -> tuple[str, list[float]]:
    """Twelve acquisitions of CH1 after ``setup``: the :TERegister? bits and the record origins."""
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
        uxr.write(f":ACQuire:POINts 2000;{setup};:ACQuire:BANDwidth {band}")
        hits, origins = "", []
        for _ in range(12):
            uxr.write(":DIGitize CHANnel1")
            hits += uxr.query(":TERegister?").strip()
            origins.append(rm.visalib.bench.instruments["scope"].components["acq"].records[1].origin)
        return hits, origins
    finally:
        rm.close()


@pytest.mark.parametrize(
    ("cases", "scale", "nreject"),
    [(CASES_BENCH_1104B, 0.5, True), (CASES_BENCH, 0.42, False)],
    ids=["uxr1104b", "uxr0334b"],
)
def test_the_trigger_taps_the_analog_front_end_with_its_own_noise(tmp_path, cases, scale, nreject):
    """#1263, #1298: the trigger compares the signal at the ADC's input — before :ACQuire:BANDwidth's
    and InfiniiSim's digital filters — through its own noise (the channel's per-division table, an
    estimate the data sheet's 71 fs display jitter at 55 GHz bears out). The 400 mVpp sine stays
    inside the normal hysteresis band (half a division either side: ±0.25 V at 0.5 V/div on the
    UXR1104B, ±0.21 V at 0.42 V/div on the UXR0334B) but for the noise: with it the noise carries
    the peaks past the band and every acquisition triggers; without the trigger's noise none does.
    The UXR1104B's NREJect band is twice as wide and never triggers. Same seed, same sequence."""
    setup = f":CHANnel1:SCALe {scale}"
    bench = _bench_with(tmp_path, "b.yaml", cases=cases)
    normal, origins = _triggers(bench, f"{setup};:TRIGger:HYSTeresis NORMal")
    # -- the noise makes the sub-band sine trigger, every time, at its rising crossing
    assert normal == "1" * 12
    if nreject:  # -- NREJect: the band is twice as wide; noise does not reach it
        assert _triggers(bench, f"{setup};:TRIGger:HYSTeresis NREJect")[0] == "0" * 12
    # -- the same seed gives the same acquisitions
    assert _triggers(bench, f"{setup};:TRIGger:HYSTeresis NORMal") == (normal, origins)
    # -- the noise is what triggers: without it the normal band is never crossed
    quiet = _bench_with(tmp_path, "q.yaml", {"trigger": {"noise_table": None}}, cases)
    assert _triggers(quiet, f"{setup};:TRIGger:HYSTeresis NORMal")[0] == "0" * 12
    # -- :ACQuire:BANDwidth is DSP after the ADC: a 1 GHz limit leaves every trigger where it was
    assert _triggers(bench, f"{setup};:TRIGger:HYSTeresis NORMal", "1E9") == (normal, origins)


@pytest.mark.parametrize("cases", [CASES_BENCH_1104B, CASES_BENCH], ids=["uxr1104b", "uxr0334b"])
def test_a_signal_beyond_the_trigger_bandwidth_does_not_trigger(tmp_path, cases):
    """#1298: ``scope_trigger`` ``bandwidth`` is a first-order low-pass on the trigger path only.
    The 10 MHz, 400 mVpp sine at 50 mV/div (a ±25 mV band) triggers every time; through a 100 kHz
    trigger bandwidth it is 100 times smaller, inside the band, and never triggers, while the
    acquisition still records the full 400 mVpp. (Both UXRs set no bandwidth: the data sheet's edge
    trigger bandwidth equals the acquisition's analog bandwidth, which the front end already sets.)"""
    quiet = {"noise_table": None}
    wide = _bench_with(tmp_path, "w.yaml", {"trigger": quiet}, cases)
    narrow = _bench_with(tmp_path, "n.yaml", {"trigger": {**quiet, "bandwidth": 1.0e5}}, cases)
    assert _triggers(wide, ":CHANnel1:SCALe 0.05")[0] == "1" * 12
    assert _triggers(narrow, ":CHANnel1:SCALe 0.05")[0] == "0" * 12
    rm = pyvisa.ResourceManager(f"{narrow}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
        uxr.write(":ACQuire:POINts 2000;:CHANnel1:SCALe 0.05;:DIGitize CHANnel1")
        assert float(uxr.query(":MEASure:VPP? CHANnel1")) == pytest.approx(0.4, abs=0.01)
    finally:
        rm.close()


@pytest.mark.parametrize("model", ["keysight-uxr1104b", "keysight-uxr0334b"])
def test_the_trigger_noise_is_the_channel_s(model):
    """#1298: the trigger's noise table is an estimate equal to the channel's (the same front end):
    the two copies in the definition stay the same."""
    definition = yaml.safe_load(
        (ROOT / "src" / "simubench" / "library" / "instruments" / f"{model}.yaml").read_text("utf-8")
    )
    trigger = next(c for c in definition["components"] if c["name"] == "trigger")
    front = next(c for c in definition["channel_components"] if c["name"] == "front")
    assert trigger["params"]["noise_table"] == front["params"]["noise_table"]


@pytest.mark.parametrize(
    ("example", "floor_v"),
    [(EXAMPLE_BENCH_1104B, 0.06), (EXAMPLE_BENCH, 0.032)],
    ids=["uxr1104b", "uxr0334b"],
)
def test_below_the_hardware_range_the_code_step_stays_the_range_s(example, floor_v):
    """#1265: below the smallest hardware range (60 mV full scale on the UXR1104B, 32 mV on the
    UXR0334B) the display is a magnification: the code step stays full scale / 1024, not the
    displayed division's. At or above that division nothing changes. Averaging still resolves
    finer than a code: the noise dithers the quantization."""
    lsb = floor_v / 1024
    rm = pyvisa.ResourceManager(f"{example}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
        uxr.write(":CHANnel3:DISPlay ON;:CHANnel3:OFFSet 0;:ACQuire:POINts 2000")

        def capture(scale: float) -> np.ndarray:
            uxr.write(f":CHANnel3:SCALe {scale:g};:DIGitize CHANnel3")
            return np.asarray(reader.read_waveform(uxr, "CHANnel3")[1])

        # -- 1 mV/div: every sample on the range's code grid, the smallest step one range code
        volts = capture(1e-3)
        codes = volts / lsb
        assert np.max(np.abs(codes - np.round(codes))) < 0.01
        assert float(np.min(np.diff(np.unique(np.round(codes))))) == 1.0
        # -- at the range's own division and above: a step of the division's code, as before
        for scale in (floor_v / 8, 0.02):
            codes = capture(scale) / (scale / 128)
            assert np.max(np.abs(codes - np.round(codes))) < 0.01, scale
        # -- averaging 1024 acquisitions at 1 mV/div: the noise falls by ~32 and the mean leaves the grid
        single = float(np.std(capture(1e-3)))
        uxr.write(":ACQuire:COUNt 1024;:ACQuire:AVERage ON")
        averaged = capture(1e-3)
        assert float(np.std(averaged)) == pytest.approx(single / 32, rel=0.3)
        # the record keeps the mean (the transfer's words carry the converter's 10-bit codes)
        record = rm.visalib.bench.instruments["scope"].components["acq"].records[3].volts
        off_grid = np.abs(record / lsb - np.round(record / lsb))
        assert float(np.mean(off_grid > 0.05)) > 0.5
        assert reader.errors(uxr) == []
    finally:
        rm.close()


def test_with_an_external_gain_the_rails_sit_on_the_record_s_code_grid():
    """#1294: with an external probe gain (display volts per input volt 1/gain) below the smallest
    hardware range, the converter's codes, its clip rails and the record's code step all use the
    same floor, in display volts: 60 mV x 10 at a gain of 0.1."""
    rm = pyvisa.ResourceManager(f"{EXAMPLE_BENCH_1104B}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
        uxr.write(":CHANnel3:DISPlay ON;:CHANnel3:PROBe:EXTernal ON;:CHANnel3:PROBe:EXTernal:GAIN 0.1")
        uxr.write(":CHANnel3:OFFSet 0;:CHANnel3:SCALe 0.01;:ACQuire:POINts 2000;:DIGitize CHANnel3")
        lsb = 0.6 / 1024
        codes = np.asarray(reader.read_waveform(uxr, "CHANnel3")[1]) / lsb
        assert np.max(np.abs(codes - np.round(codes))) < 0.01
        scope = rm.visalib.bench.instruments["scope"]
        acq, adc = scope.components["acq"], scope.components["adc"]
        rec = acq.records[3]
        assert rec.code_scale == pytest.approx(0.6 / 8)
        top, bottom = adc.rails(rec)
        assert (top / lsb, bottom / lsb) == (
            pytest.approx(round(top / lsb)),
            pytest.approx(round(bottom / lsb)),
        )
        assert top - bottom == pytest.approx(1023 * lsb)
        # the converter's own quantizer, given the probe's ratio, reaches the same rails
        assert list(adc.quantize(np.array([1e30, -1e30]), 0.01, 0.0, 10.0)) == [top, bottom]
        assert reader.errors(uxr) == []
    finally:
        rm.close()


HELLO_BENCH = ROOT / "src" / "simubench" / "library" / "examples" / "hello.bench.yaml"


def test_the_hello_square_triggers_on_its_edge_at_every_timebase():
    """#1331: Hello World's 1 MHz square into the UXR1104B, CH1 rising at 0 V. Zoomed in to a few
    ns/div, a record is a fraction of the period: the auto mode still finds the edge within its
    auto timeout, and an edge found on the coarse grid is bisected onto the signal (the trigger
    path has noise), so in both sweep modes the record's t=0 sits on a rising edge."""
    rm = pyvisa.ResourceManager(f"{HELLO_BENCH}@simubench")
    try:
        uxr = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=20000)
        uxr.write(":WAVeform:SOURce CHANnel1;:WAVeform:FORMat ASCii")
        for sweep in ("AUTO", "TRIGgered"):
            for scale in (1e-9, 5e-9, 1e-8, 2e-8):
                uxr.write(f":TRIGger:SWEep {sweep};:TIMebase:SCALe {scale};:DIGitize CHANnel1")
                x0 = float(uxr.query(":WAVeform:XORigin?"))
                dx = float(uxr.query(":WAVeform:XINCrement?"))
                v = np.asarray(uxr.query_ascii_values(":WAVeform:DATA?"))
                at = round(-x0 / dx)  # the sample at t=0
                ns = round(1e-9 / dx)
                step = f"{sweep} {scale:g} s/div"
                # 1 Vpp into 50 ohm, triggered rising at 0 V: at t=0 the trace crosses 0 V (within
                # the noise and the edge's slope over a few samples) and climbs through it; a
                # record not on the edge rests at -0.5 V or +0.5 V there
                assert abs(v[at - 2 : at + 3].mean()) < 0.1, step
                assert v[at : at + ns].mean() > v[at - ns : at].mean() + 0.02, step
                assert uxr.query(":TER?").strip() == "1", step

        # #1332: the guide times an edge only with four or more samples on it. At 500 ns/div
        # (31 ps a sample) the square's ~9 ns edge (the data sheet's, #1271) is timed; at 1 ms/div
        # (64 ns a sample) it is not; CH2, unwired, carries only noise and no edge to time either.
        def measure(what: str, ch: str = "CHANnel1") -> float:
            return float(uxr.query(f":MEASure:{what}? {ch}"))

        uxr.write(":TRIGger:SWEep AUTO;:TIMebase:SCALe 5e-7;:DIGitize CHANnel1")
        assert 8e-9 < measure("RISetime") < 10e-9
        assert 8e-9 < measure("FALLtime") < 10e-9
        uxr.write(":TIMebase:SCALe 1e-3;:DIGitize CHANnel1")
        assert measure("RISetime") == 9.99999e37
        assert measure("FALLtime") == 9.99999e37
        uxr.write(":TIMebase:SCALe 5e-7;:CHANnel2:DISPlay ON;:DIGitize CHANnel2")
        assert measure("RISetime", "CHANnel2") == 9.99999e37
        assert measure("FALLtime", "CHANnel2") == 9.99999e37
        assert reader.errors(uxr) == []
    finally:
        rm.close()
