"""Keysight DSO-X 2024A (issue 151): the guide's waveform reading, against the engine.

The manual cases (tests/models/keysight-dsox2024a/cases.yaml) check command by command; these
tests run the Programmer's Guide waveform procedure (examples/scripts/keysight_dsox_read_waveform.py)
and compare what it reconstructs with the signal the bench carries, then check the definition
pieces the cases cannot see: synchronisation, the sub-sample time origin, the data sheet's
individual deviations, offset clamping and the probe rescale.

Flow tests per D49/#704: one test walks one flow, each ``# --`` act preserves an original test's
body verbatim; acts that are provably state-compatible share one bench, with the bridging
commands noted.
"""

from pathlib import Path

import numpy as np
import pytest
import pyvisa
import yaml

from helpers import load_script
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
CASES_BENCH = ROOT / "tests" / "models" / "keysight-dsox2024a" / "bench.yaml"
EXAMPLE_BENCH = ROOT / "examples" / "benches" / "keysight-dsox2024a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_dsox_read_waveform.py"


reader = load_script(SCRIPT)


def _open(tmp_path, source=CASES_BENCH, awg_state=None, **instrument):
    data = yaml.safe_load(source.read_text("utf-8"))
    data["instruments"]["scope"].update(instrument)
    if awg_state:
        data["instruments"]["awg"]["state"].update(awg_state)
    bench = tmp_path / "bench.yaml"
    bench.write_text(yaml.safe_dump(data), "utf-8")
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    dso = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
    return rm, dso


@pytest.fixture
def scope(tmp_path):
    rm, dso = _open(tmp_path)
    dso.write(":TIMebase:SCALe 2E-4;:CHANnel1:PROBe 1;:CHANnel1:SCALe 0.5")
    yield dso, rm.visalib.bench
    rm.close()


def _record(session):
    return session.instruments["scope"].components["acq"].records[1]


def _truth(session, rec):
    net = session.port_net["scope.CH1"]
    return session.net_signal(net).sample_rel(rec.t_start_ns, np.arange(rec.volts.size) * rec.dt_s)


@pytest.mark.parametrize("fmt", ["BYTE", "WORD", "ASCii"])
def test_guide_procedure_reconstructs_the_bench_signal(scope, fmt):
    dso, session = scope
    times, volts = reader.read_waveform(dso, "CHANnel1", fmt)
    rec = _record(session)
    assert len(volts) == len(times) == 1000
    # 1000 points in NORMal mode spread over the record; each one is a record sample.
    step = rec.volts.size // 1000
    lsb = 0.5 / 32  # 8 bits, 32 codes per division at 0.5 V/div
    picked = rec.volts[::step][:1000]
    assert np.max(np.abs(np.asarray(volts) - picked)) <= (1e-5 if fmt == "ASCii" else 1e-9) + 1e-6
    truth = _truth(session, rec)[::step][:1000]
    # The record carries the range's input noise now (#745, specs.noise_per_division: the table's
    # 10 mV rms on 0.5 V/div, quantized to the 15.6 mV codes) — a 1000-point capture reaches some
    # 4 rms past the truth beside the code's lsb/2, instead of sitting on it.
    assert np.max(np.abs(np.asarray(volts) - truth)) <= lsb / 2 + 0.01 + 4 * 0.01
    # Time axis: the reference is the centre, so the trigger (a rising zero crossing) is at t = 0.
    assert times[0] == pytest.approx(-5 * 2e-4, abs=2 * rec.dt_s * step)
    assert times[1] - times[0] == pytest.approx(rec.dt_s * step)
    i0 = int(np.argmin(np.abs(times)))
    assert abs(volts[i0]) <= 0.05 and volts[i0 + 5] > volts[i0 - 5]


def test_example_bench_runs_the_script(tmp_path):
    rm, dso = _open(tmp_path, EXAMPLE_BENCH)
    try:
        times, volts = reader.read_waveform(dso, "CHANnel1")
    finally:
        rm.close()
    # 1 kHz through the 1.59 kHz RC low pass: |H| = 1 / sqrt(1 + (f/fc)^2) ≈ 0.846 of 1 V peak.
    gain = 1 / np.sqrt(1 + (1000 * 2 * np.pi * 1e3 * 1e-7) ** 2)
    assert np.max(volts) == pytest.approx(gain, abs=0.03)
    assert times[-1] - times[0] == pytest.approx(10 * 2e-4, rel=0.01)


# ---- the digitize and the sub-sample time origin: one flow ------------------------------------------


def test_the_digitize_and_time_origin_flow(scope):
    """*OPC? answers with the acquisition done, and the time origin is the trigger to a fraction
    of a sample."""
    dso, session = scope
    # -- DIGitize completes before the reply (was test_digitize_completes_before_the_reply)
    dso.write(":DIGitize CHANnel1")
    assert dso.query("*OPC?").strip() == "1"
    # The acquisition is done by the time *OPC? answers: the preamble describes it.
    assert dso.query(":WAVeform:PREamble?").split(",")[2] == "1000"
    # -- the time origin is the trigger to a fraction of a sample (was
    # test_time_origin_is_the_trigger_to_a_fraction_of_a_sample; the act sets its own timebase
    # scale and points mode)
    dso.write(":TIMebase:SCALe 5E-4;:WAVeform:POINts:MODE MAXimum;:WAVeform:POINts 100000")
    for _ in range(3):
        dso.write(":DIGitize CHANnel1")
        pre = dso.query(":WAVeform:PREamble?").split(",")
        rec = _record(session)
        xinc, xorg = float(pre[4]), float(pre[5])
        # x origin is sample 0 relative to the trigger, not snapped to the sample grid.
        assert xorg == pytest.approx(rec.origin, abs=abs(rec.origin) * 1e-10)
        assert xinc == pytest.approx(rec.dt_s)
        # The trigger falls between samples: the origin is not a whole number of samples.
        assert 0.001 < xorg / xinc % 1 < 0.999


def test_the_records_time_origin_scatters_by_the_jitter_spec(scope):
    """#859: the trigger jitter (specs.trigger_jitter_s_rms, 50 ps — an estimate, 0.1 × the 2 GSa/s
    sample interval, #745) lands in every record's time origin: over repeated acquisitions of the
    same repeating trigger the origins scatter by exactly it (the trigger's interpolated phase
    repeats; the jitter draws anew)."""
    from refcalc.noise import sigma_interval

    dso, session = scope
    origins = []
    for _ in range(24):
        dso.write(":DIGitize CHANnel1")
        assert dso.query("*OPC?").strip() == "1"
        origins.append(_record(session).origin)
    lo, hi = sigma_interval(5.0e-11, len(origins))
    assert lo <= float(np.std(origins)) <= hi, (float(np.std(origins)), lo, hi)


def _bench(seed, deviation="from_specs"):
    data = yaml.safe_load(CASES_BENCH.read_text("utf-8"))
    data["instruments"]["scope"]["deviation"] = deviation
    data["run"]["seed"] = seed
    return BenchDefinition.model_validate(data)


def _deviations(seed):
    s = open_bench(_bench(seed))
    try:
        return {k: v for k, v in s.deviations().items() if k.startswith("scope/")}
    finally:
        s.close()


def test_individual_deviations_stay_within_the_data_sheet(tmp_path):
    first, again, other = _deviations(3), _deviations(3), _deviations(4)
    assert first == again and first != other
    for seed in range(5):
        d = _deviations(seed)
        for ch in range(1, 5):
            for scale, ppm in d[f"scope/ch{ch}.front.gain_error_ppm"].items():
                assert abs(ppm) <= (40000 if float(scale) < 0.01 else 30000)
            for scale, volts in d[f"scope/ch{ch}.front.offset_error"].items():
                assert abs(volts) <= 0.1 * float(scale) + 0.002 + 1e-12
            assert abs(d[f"scope/ch{ch}.front.dac_error_ppm"]) <= 10000
        assert abs(d["scope/timebase.clock_ppm"]) <= 25


# ---- the channel settings: offset clamp, range, probe rescale: one flow -----------------------------


def test_the_channel_settings_flow(scope):
    """The offset clamps to the scale's range and follows it, range is scale times divisions,
    and the probe ratio rescales scale and offset."""
    dso, _ = scope
    # -- an out-of-range offset clamps and follows the scale (was
    # test_offset_out_of_range_clamps_and_follows_the_scale)
    dso.write(":CHANnel1:SCALe 0.1;:CHANnel1:OFFSet 10")
    assert float(dso.query(":CHANnel1:OFFSet?")) == pytest.approx(2.0)
    dso.write(":CHANnel1:SCALe 1;:CHANnel1:OFFSet 10")
    assert float(dso.query(":CHANnel1:OFFSet?")) == pytest.approx(10.0)
    # Back to a sensitive scale: the offset is pulled into its narrower range.
    dso.write(":CHANnel1:SCALe 0.1")
    assert float(dso.query(":CHANnel1:OFFSet?")) == pytest.approx(2.0)
    dso.write(":CHANnel1:SCALe 100")
    assert float(dso.query(":CHANnel1:SCALe?")) == pytest.approx(5.0)
    assert dso.query(":SYSTem:ERRor?").startswith("+0,")
    # -- range is scale times divisions (was test_range_is_scale_times_divisions)
    dso.write(":CHANnel1:RANGe 8")
    assert float(dso.query(":CHANnel1:SCALe?")) == pytest.approx(1.0)
    dso.write(":TIMebase:RANGe 1E-3")
    assert float(dso.query(":TIMebase:SCALe?")) == pytest.approx(1e-4)
    assert dso.query(":TIMebase:RANGe?").strip() == "+1.00E-03"
    # -- the probe ratio rescales scale and offset (was test_probe_ratio_rescales_scale_and_offset)
    dso.write(":CHANnel1:SCALe 0.5;:CHANnel1:OFFSet 0.2")
    dso.write(":CHANnel1:PROBe 10")
    assert float(dso.query(":CHANnel1:SCALe?")) == pytest.approx(5.0)
    assert float(dso.query(":CHANnel1:OFFSet?")) == pytest.approx(2.0)
    dso.write(":CHANnel1:PROBe X1")
    assert float(dso.query(":CHANnel1:SCALe?")) == pytest.approx(0.5)


# ---- issue 160: run control, trigger force, preset and the acquisition types ------------------------


# ---- run control: the bus trigger, the force trigger and preset: one flow (issue 160) ----------------


def test_the_run_control_flow(tmp_path):
    """*TRG digitizes like :DIGitize without parameters, :TRIGger:FORCe captures a frame no
    trigger would, and :SYSTem:PRESet restores the default setup and runs again."""
    rm, dso = _open(tmp_path)
    try:
        # -- the bus trigger is digitize without parameters (was
        # test_bus_trigger_is_digitize_without_parameters)
        # The guide: *TRG has the same effect as :DIGitize with no parameters (Root/Common commands).
        dso.write(":STOP;*TRG")
        assert dso.query("*OPC?").strip() == "1"
        assert dso.query(":WAVeform:PREamble?").split(",")[2] == "1000"
        assert _record(rm.visalib.bench).volts.size == 100_000
        assert dso.query(":SYSTem:ERRor?").startswith("+0,")
        # -- the force trigger takes a frame without a trigger (was
        # test_force_trigger_takes_a_frame_without_a_trigger; the error above was read out, the
        # sweep and level are the act's own settings)
        # A trigger level above the signal leaves :DIGitize waiting; :TRIGger:FORCe still captures.
        dso.write(":TRIGger:SWEep NORMal;:TRIGger:LEVel 20;:TRIGger:FORCe")
        assert dso.query("*OPC?").strip() == "1"
        # The forced frame completes in virtual time; the next command settles it.
        assert dso.query(":WAVeform:PREamble?").split(",")[2] == "1000"
        rec = _record(rm.visalib.bench)
        assert rec.triggered is False  # the frame was forced, not triggered
        assert rec.volts.size == 100_000
        assert dso.query(":SYSTem:ERRor?").startswith("+0,")
        # -- preset restores the default setup and runs again (was
        # test_preset_restores_the_default_setup_and_runs_again; the :STOP below is the act's own)
        dso.write(":CHANnel1:SCALe 0.2;:TIMebase:SCALe 1E-3;:TRIGger:SOURce CHANnel2;:STOP")
        acq = rm.visalib.bench.instruments["scope"].components["acq"]
        assert acq.running is False
        dso.write(":SYSTem:PRESet")
        assert acq.running is True  # the guide's Reset conditions: "Scope is running"
        assert float(dso.query(":CHANnel1:SCALe?")) == pytest.approx(5.0)
        assert dso.query(":TIMebase:SCALe?").strip() == "+100E-06"
        assert dso.query(":TRIGger:SOURce?").strip() == "CHAN1"
        assert dso.query(":ACQuire:TYPE?").strip() == "NORM"
    finally:
        rm.close()


def test_peak_detect_keeps_the_excursion_the_record_rate_steps_over(tmp_path):
    """A 40 MHz sine into 5 us record samples aliases to one phase; PEAK examines 64 native samples
    in each interval and keeps the extreme, so the 2 Vpp sine shows at full swing (D46, F4)."""
    rm, dso = _open(tmp_path, awg_state={"ch1.frequency": 40.0e6, "ch1.function": "SINE"})
    try:
        session = rm.visalib.bench
        dso.write(":TIMebase:SCALe 50E-3;:CHANnel1:PROBe 1;:CHANnel1:SCALe 0.5")
        dso.write(":ACQuire:TYPE NORMal;:DIGitize CHANnel1")
        assert dso.query("*OPC?").strip() == "1"
        normal = _record(session).volts
        dso.write(":ACQuire:TYPE PEAK;:DIGitize CHANnel1")
        assert dso.query("*OPC?").strip() == "1"
        peak = _record(session).volts
        assert np.max(np.abs(normal)) < 0.5
        assert np.max(np.abs(peak)) > 0.9
    finally:
        rm.close()


def test_a_running_normal_scope_waits_while_its_level_finds_no_edge(tmp_path):
    """WAIT (bit 5) follows the trigger a continuous run finds now, not the last acquisition's (#352)."""
    rm, dso = _open(tmp_path)
    try:
        dso.write("*RST;:CHANnel1:PROBe 1;:DIGitize CHANnel1")  # triggers on the 1 V sine
        dso.write(":TRIGger:SWEep NORMal;:TRIGger:LEVel 1.2")  # beyond the swing: never an edge
        dso.write(":RUN")
        assert dso.query(":OPERegister:CONDition?").strip() == "40"  # RUN | WAIT
        dso.write(":TRIGger:LEVel 0")
        assert dso.query(":OPERegister:CONDition?").strip() == "8"  # RUN, triggering again
        dso.write(":STOP")
        assert dso.query(":OPERegister:CONDition?").strip() == "0"
    finally:
        rm.close()


def test_the_captures_scatter_by_the_table_noise_around_the_true_level(tmp_path):
    """#745's statistical acceptance: the same DC captured repeatedly, read back through the
    guide's waveform procedure — the samples scatter with the definition's noise on the
    0.5 V/div range (the table's 10 mV rms, quantized to the 15.6 mV codes), the mean stays at
    the true level, and every capture draws anew."""
    import math

    from refcalc.noise import quantized_sigma, sigma_interval

    rm, dso = _open(
        tmp_path,
        EXAMPLE_BENCH,
        awg_state={"ch1.function": "DC", "ch1.offset": 0.8, "ch1.load": "INF", "ch1.output": True},
    )
    try:
        dso.write(":TIMebase:SCALe 2E-4;:CHANnel1:SCALe 0.5;:CHANnel1:OFFSet 0;:WAVeform:FORMat BYTE")
        captures = []
        for _ in range(3):
            dso.write(":DIGitize CHANnel1")
            _times, volts = reader.read_waveform(dso, "CHANnel1", "BYTE")
            captures.append(np.asarray(volts))
        assert not np.array_equal(captures[0], captures[1])  # each capture draws anew
        samples = np.concatenate(captures)
        # DC 0.8 V at the generator, the 1 kOhm series into the meter's 1 MOhm: 0.7992 V at the plate
        truth = 0.8 * 1.0e6 / (1.0e3 + 1.0e6)
        # the mean sits at the true level: 5 rms of the sample mean plus half a code of bias
        assert abs(float(np.mean(samples)) - truth) < 5 * 0.01 / math.sqrt(samples.size) + 0.5 / 32 / 2
        lo, hi = sigma_interval(quantized_sigma(0.01, 0.5 / 32), samples.size)
        assert lo <= float(np.std(samples)) <= hi, (float(np.std(samples)), lo, hi)
    finally:
        rm.close()


def test_the_band_floor_scales_with_the_range_and_the_signal_above_it_is_untouched(scope):
    """#773's per-division crossing floor through the model's SCPI path (#781): a channel without
    a signal carries only its range's own noise, which the 0.16-division floor outruns at 5 V/div,
    so FREQuency, PERiod and BRATe answer the model's infinity; at 1 mV/div the table's flat
    0.2 mV sensitive-range noise outruns the scaled-down floor and counts crossings; the floor at
    0 restores the old behaviour on the same noise; and the 4-division sine measures the same
    with the floor on and off."""
    dso, session = scope
    dso.write(":MEASure:SOURce CHANnel2")  # *RST: 5 V/div, no signal — the range's noise alone
    # -- the noise alone measures as nothing: the 0.1 V rms is 0.02 div, the floor 0.16 div
    for what in ("FREQuency", "PERiod", "BRATe"):
        assert float(dso.query(f":MEASure:{what}? CHANnel2")) == 9.9e37
    # -- the floor is a fraction of a division: at 1 mV/div the table's flat 0.2 mV noise is
    #    0.2 div rms and outruns the floor's 0.16 mV, so the crossings count (and back)
    dso.write(":CHANnel2:SCALe 1E-3")
    assert float(dso.query(":MEASure:FREQuency? CHANnel2")) < 9.9e37
    dso.write(":CHANnel2:SCALe 5")
    assert float(dso.query(":MEASure:FREQuency? CHANnel2")) == 9.9e37
    # -- the floor at 0 is the old counter: the same noise counts its crossings again
    session.set_parameter("scope", "meas", "hysteresis_floor_divs", 0.0)
    assert float(dso.query(":MEASure:FREQuency? CHANnel2")) < 9.9e37
    # -- a signal above the floor is unaffected either way: the sine spans 4 divisions, ten
    #    times the 0.4 V floor at 0.5 V/div
    dso.write(":MEASure:SOURce CHANnel1")
    assert float(dso.query(":MEASure:FREQuency? CHANnel1")) == pytest.approx(1000.0, abs=10.0)
    session.set_parameter("scope", "meas", "hysteresis_floor_divs", 0.16)
    assert float(dso.query(":MEASure:FREQuency? CHANnel1")) == pytest.approx(1000.0, abs=10.0)
