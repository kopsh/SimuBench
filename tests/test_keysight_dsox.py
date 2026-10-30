"""Keysight DSO-X 2024A (issue 151): the guide's waveform reading, against the engine.

The manual cases (tests/models/keysight-dsox2024a/cases.yaml) check command by command; these
tests run the Programmer's Guide waveform procedure (examples/scripts/keysight_dsox_read_waveform.py)
and compare what it reconstructs with the signal the bench carries, then check the definition
pieces the cases cannot see: synchronisation, the sub-sample time origin, the data sheet's
individual deviations, offset clamping and the probe rescale.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import pyvisa
import yaml

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
CASES_BENCH = ROOT / "tests" / "models" / "keysight-dsox2024a" / "bench.yaml"
EXAMPLE_BENCH = ROOT / "examples" / "benches" / "keysight-dsox2024a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_dsox_read_waveform.py"


def _script():
    spec = importlib.util.spec_from_file_location("keysight_dsox_read_waveform", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


reader = _script()


def _open(tmp_path, source=CASES_BENCH, **instrument):
    data = yaml.safe_load(source.read_text("utf-8"))
    data["instruments"]["scope"].update(instrument)
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
    assert np.max(np.abs(np.asarray(volts) - truth)) <= lsb / 2 + 0.01
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


def test_digitize_completes_before_the_reply(scope):
    dso, _ = scope
    dso.write(":DIGitize CHANnel1")
    assert dso.query("*OPC?").strip() == "1"
    # The acquisition is done by the time *OPC? answers: the preamble describes it.
    assert dso.query(":WAVeform:PREamble?").split(",")[2] == "1000"


def test_time_origin_is_the_trigger_to_a_fraction_of_a_sample(scope):
    dso, session = scope
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


def test_offset_out_of_range_clamps_and_follows_the_scale(scope):
    dso, _ = scope
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


def test_range_is_scale_times_divisions(scope):
    dso, _ = scope
    dso.write(":CHANnel1:RANGe 8")
    assert float(dso.query(":CHANnel1:SCALe?")) == pytest.approx(1.0)
    dso.write(":TIMebase:RANGe 1E-3")
    assert float(dso.query(":TIMebase:SCALe?")) == pytest.approx(1e-4)
    assert dso.query(":TIMebase:RANGe?").strip() == "+1.00E-03"


def test_probe_ratio_rescales_scale_and_offset(scope):
    dso, _ = scope
    dso.write(":CHANnel1:SCALe 0.5;:CHANnel1:OFFSet 0.2")
    dso.write(":CHANnel1:PROBe 10")
    assert float(dso.query(":CHANnel1:SCALe?")) == pytest.approx(5.0)
    assert float(dso.query(":CHANnel1:OFFSet?")) == pytest.approx(2.0)
    dso.write(":CHANnel1:PROBe X1")
    assert float(dso.query(":CHANnel1:SCALe?")) == pytest.approx(0.5)
