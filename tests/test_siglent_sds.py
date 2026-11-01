"""Siglent SDS2104X Plus (issue 11): the manual's waveform reading, against the engine.

The manual cases (tests/models/siglent-sds2104xplus/cases.yaml) check command by command; these
tests run the guide's waveform reading procedure (examples/scripts/siglent_sds_read_waveform.py)
and compare what it reconstructs with the signal the bench actually carries.
"""

import importlib.util
import math
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import pyvisa
import yaml

ROOT = Path(__file__).resolve().parents[1]
CASES_BENCH = ROOT / "tests" / "models" / "siglent-sds2104xplus" / "bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "siglent_sds_read_waveform.py"


def _script():
    spec = importlib.util.spec_from_file_location("siglent_sds_read_waveform", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


reader = _script()


@pytest.fixture
def scope(tmp_path):
    bench = tmp_path / "bench.yaml"
    shutil.copy(CASES_BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    sds = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)
    sds.write(":TIMebase:SCALe 2E-4;:CHANnel1:SCALe 0.5")
    yield sds, rm.visalib.bench
    rm.close()


def _truth(session, rec):
    """The net signal at the record's sample instants (the 100 MHz front end is flat at 1 kHz)."""
    net = session.port_net["scope.CH1"]
    return session.net_signal(net).sample_rel(rec.t_start_ns, np.arange(rec.volts.size) * rec.dt_s)


def _record(session):
    return session.instruments["scope"].components["acq"].records[1]


@pytest.mark.parametrize("resolution", ["8Bits", "10Bits"])
def test_guide_procedure_reconstructs_the_bench_signal(scope, resolution):
    sds, session = scope
    sds.write(f":ACQuire:RESolution {resolution}")
    sds.write(":TRIGger:MODE SINGle")
    times, volts = reader.read_waveform(sds, "C1")
    rec = _record(session)
    assert len(volts) == rec.volts.size == 10_000
    lsb = 0.5 / (30 if resolution == "8Bits" else 120)  # volts per code at 0.5 V/div
    # The transferred codes are the record (already on the ADC's grid); the record is the net seen
    # through the front end (a few microvolts at 1 kHz) plus the division's noise, quantized. At
    # 0.5 V/div that noise is the estimated 7.5 mV rms (noise_table, D46) - a real scope's record
    # wanders a few codes around the signal, which the bound takes into account.
    sigma = session.instruments["scope"].components["acq"].vertical_of(1).noise_rms()
    assert sigma == pytest.approx(7.5e-3)
    assert np.max(np.abs(np.array(volts) - rec.volts)) < 1e-9
    assert np.max(np.abs(rec.volts - _truth(session, rec))) <= lsb / 2 + 6 * sigma + 5e-5
    # Time axis: delay 0 puts the trigger (rising zero crossing of the sine) at t = 0.
    assert times[0] == pytest.approx(-5 * 2e-4)
    i0 = int(np.argmin(np.abs(np.array(times))))
    assert abs(volts[i0]) <= 6 * sigma + lsb and volts[i0 + 20] > volts[i0 - 20]


def test_byte_and_word_carry_the_same_codes(scope):
    sds, _ = scope
    sds.write(":TRIGger:MODE SINGle;:WAVeform:SOURce C1;:WAVeform:WIDTh BYTE")
    as_bytes = sds.query_binary_values(":WAVeform:DATA?", datatype="b", container=np.array)
    sds.read_bytes(1)
    sds.write(":WAVeform:WIDTh WORD")
    words = sds.query_binary_values(":WAVeform:DATA?", datatype="h", container=np.array)
    sds.read_bytes(1)
    assert np.array_equal(words >> 8, as_bytes)


def test_start_point_and_interval_select_the_transferred_points(scope):
    sds, _ = scope
    sds.write(":TRIGger:MODE SINGle;:WAV:SOUR C1;:WAV:WIDT BYTE")
    full = sds.query_binary_values(":WAVeform:DATA?", datatype="b", container=np.array)
    sds.read_bytes(1)
    sds.write(":WAVeform:STARt 100;:WAVeform:POINt 50;:WAVeform:INTerval 3")
    part = sds.query_binary_values(":WAVeform:DATA?", datatype="b", container=np.array)
    sds.read_bytes(1)
    assert np.array_equal(part, full[100 : 100 + 150 : 3])


def test_measurements_match_the_waveform(scope):
    sds, session = scope
    sds.write(":TIMebase:SCALe 1E-3;:TRIGger:MODE SINGle")
    _, volts = reader.read_waveform(sds, "C1")
    pkpk = float(sds.query(":MEASure:SIMPle:VALue? PKPK"))
    freq = float(sds.query(":MEASure:SIMPle:VALue? FREQ"))
    rms = float(sds.query(":MEASure:SIMPle:VALue? RMS"))
    v = np.array(volts)
    assert pkpk == pytest.approx(v.max() - v.min(), abs=0.02)  # 3 digits, codes of 16.7 mV
    # The extremes carry the division's noise (7.5 mV rms, D46): a real scope's PKPK of one
    # acquisition sits a few codes above the signal's own 2 V.
    sigma = session.instruments["scope"].components["acq"].vertical_of(1).noise_rms()
    assert pkpk == pytest.approx(2.0, abs=8 * sigma + 0.5 / 30)
    assert freq == pytest.approx(1000.0, rel=5e-3)
    assert rms == pytest.approx(math.sqrt((1.0 / np.sqrt(2)) ** 2 + sigma**2), rel=5e-3)


def test_amplitude_of_a_sine_reads_the_peaks(scope):
    """F2 (#131): AMPLitude is top - base, the levels the waveform rests on — not the halves'
    medians, which sit at 0.707 of the peak on a sine."""
    sds, session = scope
    sds.write(":TIMebase:SCALe 1E-3;:TRIGger:MODE SINGle")  # 10 ms: ten cycles
    pkpk = float(sds.query(":MEASure:SIMPle:VALue? PKPK"))
    ampl = float(sds.query(":MEASure:SIMPle:VALue? AMPL"))
    # PKPK rides the record's noise excursions, AMPL (top - base) does not: one acquisition's
    # two extremes differ by a few sigma (7.5 mV rms at 0.5 V/div, D46) plus a code. The error
    # this measurement exists to catch, the halves' medians, is 0.707 — far beyond either.
    sigma = session.instruments["scope"].components["acq"].vertical_of(1).noise_rms()
    assert ampl == pytest.approx(pkpk, abs=8 * sigma + 0.5 / 30)
    assert ampl == pytest.approx(2.0, abs=0.04)


def test_amplitude_of_a_square_is_the_level_it_rests_on(scope):
    sds, _ = scope
    sds.write(":SOURce1:FUNCtion SQUare;:TIMebase:SCALe 1E-3;:TRIGger:MODE SINGle")
    lsb = 0.5 / 30  # 0.5 V/div, 30 codes a division (8 Bits)
    ampl = float(sds.query(":MEASure:SIMPle:VALue? AMPL"))
    assert ampl == pytest.approx(2.0, abs=lsb)


def test_frequency_survives_noise_at_the_quantization_floor(scope):
    """F2 (#131): the counter runs on a hysteresis band around the middle, so noise there
    cannot fake crossings. 1 mVpp at 500 uV/div is about 20 dB of SNR over the input noise
    floor (80 uV at the input), 8-bit codes of 16.7 uV."""
    sds, _ = scope
    sds.write(":SOURce1:VOLTage:AMPLitude 1E-3;:CHANnel1:SCALe 5E-4")
    sds.write(":TIMebase:SCALe 1E-2;:TRIGger:MODE SINGle")  # 100 ms: a hundred cycles
    freq = float(sds.query(":MEASure:SIMPle:VALue? FREQ"))
    assert freq == pytest.approx(1000.0, abs=1.0)  # 0.1 %


def test_a_window_with_less_than_a_period_is_unmeasurable(scope):
    """F2 (#131): no complete cycle in the record — the declared invalid value, not a number."""
    sds, _ = scope
    sds.write(":TIMebase:SCALe 1E-5;:TRIGger:MODE SINGle")  # 100 us: a tenth of a cycle
    for kind in ("FREQ", "PER"):
        assert sds.query(f":MEASure:SIMPle:VALue? {kind}") == "9.9E37"


def test_settings_out_of_the_data_sheet_ranges_are_refused(scope):
    sds, session = scope
    sds.write(":CHANnel1:SCALe 20")  # 1 MOhm: at most 10 V/div at the input
    assert sds.query(":CHANnel1:SCALe?").strip() == "5.00E-01"
    sds.write(":CHANnel1:PROBe VALue,10;:CHANnel1:SCALe 20")  # 2 V/div at the input: fine
    assert sds.query(":CHANnel1:SCALe?").strip() == "2.00E+01"
    sds.write(":CHANnel1:PROBe DEFault;:CHANnel1:OFFSet 5")  # 2 V/div: offset up to +-20 V
    assert sds.query(":CHANnel1:OFFSet?").strip() == "5.00E+00"
    sds.write(":CHANnel1:SCALe 0.05")  # 50 mV/div allows only +-2 V of offset: refused
    assert sds.query(":CHANnel1:SCALe?").strip() == "2.00E+00"
    sds.write(":TRIGger:EDGE:LEVel 50")  # +-4.1 div around the offset
    assert sds.query(":TRIGger:EDGE:LEVel?").strip() == "0.00E+00"
    sds.write(":TIMebase:DELay 1")  # at most 5 div
    assert sds.query(":TIMebase:DELay?").strip() == "0.00E+00"
    refused = [e for e in session.history if e.get("kind") == "error" and e["code"] == -222]
    assert len(refused) == 3 + 1  # scale, scale, level, delay


def test_only_the_common_commands_of_the_manual_answer(scope):
    sds, session = scope
    sds.write("*CLS")
    sds.write(":SYSTem:ERRor?")
    errors = [e for e in session.history if e.get("kind") == "error"]
    # The model's builtins are *IDN, *OPC and *RST (D45): the rest is excluded, not a modeling gap.
    assert [e.get("reason") for e in errors[-2:]] == ["builtin_excluded", "builtin_excluded"]
    assert sds.query("*OPC?").strip() == "1"  # and nothing was queued for the script to read


def test_example_bench_and_script_run_as_on_the_real_bench(tmp_path, capsys, monkeypatch):
    """The guide's procedure on the example bench, addressed by the real bench's VISA address."""
    data = yaml.safe_load((ROOT / "examples" / "benches" / "siglent_scope_rc.bench.yaml").read_text("utf-8"))
    data["instruments"]["awg"]["state"]["ch1.output"] = True  # outputs are off at power-on
    bench = tmp_path / "siglent_scope_rc.bench.yaml"
    bench.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "TCPIP0::192.168.1.31::INSTR", "C1", "--bench", str(bench)])
    reader.main()
    out = capsys.readouterr().out
    # The SDG2042X's 2 Vpp is set for a HiZ load (its default): the RC passes 0.847 of it at 1 kHz,
    # and the 10x probe divides by 10 while the scope still assumes 1X.
    assert out.startswith("10000 points")
    lo, hi = (float(x.split()[0]) for x in out.split(" s, ")[1].split(" to "))
    assert hi - lo == pytest.approx(2 * 0.847 / 10, rel=0.03)


def test_noisy_acquisitions_replay_byte_for_byte(tmp_path):
    """Noise is a function of the seed and virtual time (issue 24), whatever the processing (D46):
    a run record with plain, averaged, peak-detect and high-resolution acquisitions replays
    exactly."""
    from simubench.api import open_bench
    from simubench.engine.replay import replay

    s = open_bench(CASES_BENCH, record_dir=tmp_path)
    s.write("scope", b":TIMebase:SCALe 2E-4;:CHANnel1:SCALe 5E-3;:TRIGger:MODE SINGle;:WAVeform:SOURce C1\n")
    s.write("scope", b":WAVeform:DATA?\n")
    s.read("scope")
    for kind in ("AVERage,16", "PEAK", "ERES,2.0"):
        s.write("scope", f":ACQuire:TYPE {kind};:TRIGger:MODE SINGle\n".encode())
        s.write("scope", b":WAVeform:DATA?\n")
        s.read("scope")
    s.close()
    report = replay(next(tmp_path.glob("run-*.jsonl")))
    assert report.ok, report.as_dict()
    assert report.replies == 4


MODELS = ROOT / "tests" / "models" / "siglent-sds2104xplus"
CONTRACT = MODELS / "calibration-cases.yaml"
#: The V/div detents of the data sheet (input connector, 1X probe): one attenuator step each.
SCALES = [5e-4, 1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2, 1e-1, 2e-1, 5e-1, 1.0, 2.0, 5.0, 10.0]


def _metering_bench(tmp_path: Path, *, awg: dict | None = None, hidden: dict | None = None, seed: int = 3):
    """A from_specs bench: one AWG into CH1, the scope draws its own deviations."""
    state = {
        "ch1.frequency": 1000.0,
        "ch1.amplitude": 2.0,
        "ch1.load": math.inf,
        "ch1.output": True,
        **(awg or {}),
    }
    scope: dict = {"model": "siglent-sds2104xplus", "deviation": "from_specs"}
    if hidden:
        scope["hidden"] = hidden
    data = {
        "schema": "simubench.bench/1",
        "name": "sds_metrology",
        "instruments": {"awg": {"model": "sb-awg-2", "state": state}, "scope": scope},
        "wires": [{"from": "awg.OUT1", "to": "scope.CH1"}],
        "run": {"seed": seed, "clock": "command"},
    }
    path = tmp_path / "bench.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _open(path: Path):
    rm = pyvisa.ResourceManager(f"{path}@simubench")
    return rm, rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=5000)


def test_drawn_deviations_stay_within_the_data_sheet_and_reproduce(tmp_path):
    """G3 (#135): a unit drawn with ``deviation: from_specs`` sits inside the data sheet's limits —
    ±3 % of DC gain per V/div (``specs.dc_gain_accuracy`` = 0.03 = 30000 ppm) and ±1 ppm of
    timebase (``specs.timebase_accuracy_ppm``) — and the same bench seed draws the same unit."""
    path = _metering_bench(tmp_path)
    rm, _ = _open(path)
    drawn = rm.visalib.bench.instruments["scope"].deviations
    for n in range(1, 5):
        gain = drawn[f"ch{n}.front.gain_error_ppm"]
        assert set(gain) == set(SCALES)
        assert all(abs(v) <= 30_000.0 for v in gain.values())
    assert abs(drawn["timebase.clock_ppm"]) <= 1.0
    shown = repr(drawn)
    rm.close()

    rm, _ = _open(path)  # same bench and seed: the same unit
    assert repr(rm.visalib.bench.instruments["scope"].deviations) == shown
    rm.close()

    other = _metering_bench(tmp_path, seed=4)  # another seed: another unit
    rm, _ = _open(other)
    assert repr(rm.visalib.bench.instruments["scope"].deviations) != shown
    rm.close()


def test_the_drawn_gain_error_reaches_the_record_at_every_scale(tmp_path):
    """G3 (#135): each V/div's drawn gain error is the one the record shows; the AWG's DC level is
    set to 2.5 divisions (5 V at the top scales), averages reduce the input noise, and one ADC
    code is the allowed quantization remainder."""
    rm, sds = _open(_metering_bench(tmp_path))
    scope = rm.visalib.bench.instruments["scope"]
    acq = scope.components["acq"]
    awg = rm.open_resource("awg", read_termination="\n", write_termination="\n")
    gains = scope.deviations["ch1.front.gain_error_ppm"]
    sds.write(":TIMebase:SCALe 1E-3;:ACQuire:TYPE AVERage,256")
    for scale in (s for s in SCALES if s <= 2.0):  # the AWG's +-5 V reaches 2.5 div up to 2 V/div
        level = 2.5 * scale
        awg.write(f":FUNCtion DC;:VOLTage:OFFSet {level:.6g}")
        sds.write(f":CHANnel1:SCALe {scale:g}")
        rec, _ = acq.measure_record(1)
        lsb = scale / 30  # 8 bits, 30 codes a division
        assert float(np.mean(rec.volts)) == pytest.approx(level * (1 + gains[scale] * 1e-6), abs=0.75 * lsb)
    rm.close()


def test_user_calibration_writes_every_scale_and_corrects_the_reading(tmp_path):
    """G3 (#135): the check-adjust-recheck loop on one scale — the as-found reading carries the
    drawn gain error, ``*CAL?`` writes the per-V/div constants into the store, and the as-left
    reading is right again (one ADC code's quantization remains)."""
    rm, sds = _open(_metering_bench(tmp_path, awg={"ch1.function": "DC", "ch1.offset": 1.0}))
    scope = rm.visalib.bench.instruments["scope"]
    acq, cal = scope.components["acq"], scope.components["ch1.cal"]
    drawn = scope.deviations["ch1.front.gain_error_ppm"][0.5]
    sds.write(":TIMebase:SCALe 1E-3;:CHANnel1:SCALe 0.5")
    lsb = 0.5 / 30
    as_found = float(np.mean(acq.measure_record(1)[0].volts))
    assert as_found == pytest.approx(1.0 * (1 + drawn * 1e-6), abs=0.75 * lsb)

    assert sds.query("*CAL?") == "*CAL 0"
    as_left = float(np.mean(acq.measure_record(1)[0].volts))
    assert as_left == pytest.approx(1.0, abs=0.75 * lsb)
    state = cal.describe()
    assert state["count"] == 1 and len(state["keys"]) == len(SCALES)
    gain, offset = cal.coefficients()
    assert gain == pytest.approx(1 / (1 + drawn * 1e-6), rel=1e-12) and offset == 0.0
    rm.close()


def test_skew_delays_the_channel(tmp_path):
    """G3 (#135): ``:CHANnel1:SKEW`` moves the channel's record in time — 100 ns is a tenth of a
    1 MHz cycle, so the trigger sample shows the sine from 36° earlier."""
    rm, sds = _open(_metering_bench(tmp_path, awg={"ch1.frequency": 1e6}))
    scope = rm.visalib.bench.instruments["scope"]
    acq = scope.components["acq"]
    sds.write(":TIMebase:SCALe 5E-7;:CHANnel1:SCALe 0.5")
    lsb = 0.5 / 30

    def single():
        # A single acquisition finishes in virtual time: *OPC? waits for it, the next command
        # settles the completed record into the store.
        sds.write(":TRIGger:MODE SINGle")
        sds.query("*OPC?")
        sds.query(":MEASure:SIMPle:VALue? PKPK")
        return acq.records[1]

    rec = single()
    assert abs(float(rec.volts[rec.trigger_index])) <= 2 * lsb  # the unskewed rising zero crossing
    sds.write(":CHANnel1:SKEW 1E-7")
    rec = single()
    assert float(rec.volts[rec.trigger_index]) == pytest.approx(-math.sin(0.2 * math.pi), abs=4 * lsb)
    rm.close()


def test_the_clock_error_shows_in_a_frequency_measurement(tmp_path):
    """G3 (#135): the sampling clock's ppm reaches the time measurements. A fast clock samples
    closer together than the nominal dt the scope reports, so the counter reads low; 20000 ppm
    is above the reply's three significant digits (200 ppm would round away)."""
    rm, sds = _open(_metering_bench(tmp_path, hidden={"timebase": {"clock_ppm": 20_000.0}}))
    sds.write(":TIMebase:SCALe 1E-3")  # 10 ms: ten cycles of the 1 kHz sine
    freq = float(sds.query(":MEASure:SIMPle:VALue? FREQ"))
    assert freq == pytest.approx(1000.0 / 1.02, abs=1.0)
    rm.close()


def test_the_calibration_contract_cases_pass():
    """G3 (#135), measure 5: the 5522A + SDS2104X Plus contract case, check → adjust → recheck,
    runs through the same runner as ``simubench cases`` (tests/models/.../calibration-cases.yaml)."""
    from simubench.library.coverage import load_cases, run_case

    cases = load_cases(CONTRACT)
    results = [run_case(CONTRACT, cases, case) for case in cases.cases]
    assert all(r.passed for r in results), [r.describe() for r in results if not r.passed]


def test_without_deviations_the_unit_is_nominal(scope):
    """A definition without ``deviations`` (or a bench without ``deviation: from_specs``) draws
    nothing: the scope stays the ideal unit the existing tests pin."""
    _, session = scope
    scope = session.instruments["scope"]
    assert scope.deviations == {}
    assert scope.components["timebase"].clock_error() == 0.0
    assert scope.components["ch1.front"]._error("gain_error_ppm") == 0.0
    assert scope.components["ch1.probe"].delay() == 0.0
