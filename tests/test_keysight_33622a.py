"""Keysight 33622A (issue 334): the Trueform guide's APPLy, arbitrary-waveform and AM flow.

The manual cases (tests/models/keysight-33622a/cases.yaml) check command by command; these tests run
the real-instrument-style script examples/scripts/keysight_33622a_waves.py against the example bench
(a DSOX2024A on both channels) and check what a transcript cannot see: the waveform each setting
produces at the output — the load setting's effect on the level, the arbitrary table's period at the
sample rate, the high/low levels and the one-modulation-at-a-time switches.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight-33622a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_33622a_waves.py"


def _script():
    spec = importlib.util.spec_from_file_location("keysight_33622a_waves", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


@pytest.fixture
def awg():
    s = open_bench(BENCH, idle="off")
    yield s
    s.close()


def w(s, text: str) -> None:
    s.write("awg", (text + "\n").encode())


def wave(s, ch: int = 1):
    """The channel's EMF behind its 50 Ω output (what the level module hands the output stage)."""
    return s.instruments["awg"].components[f"ch{ch}.level"].output("out")


def peak_to_peak(sig, period: float) -> float:
    t = np.linspace(0, 4 * period, 4000, endpoint=False) * 1e9
    v = sig.sample(t)
    return float(v.max() - v.min())


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    monkeypatch.setattr(sys, "argv", ["keysight_33622a_waves.py", "awg", "scope", "--bench", str(bench)])
    script.main()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Keysight Technologies,33622A,MY12345678,0.179-1.19-8.88-52-00"
    assert out[-1] == '+0,"No error"'


def test_the_load_setting_defines_the_amplitude(awg):
    w(awg, "OUTP:LOAD 50;:APPL:SIN 1e3,2,0")  # 2 Vpp into 50 Ω: a 4 Vpp EMF behind 50 Ω
    assert peak_to_peak(wave(awg), 1e-3) == pytest.approx(4.0, rel=1e-3)
    w(awg, "OUTP:LOAD INF")  # high-Z: the setting is the open-circuit voltage
    assert peak_to_peak(wave(awg), 1e-3) == pytest.approx(2.0, rel=1e-3)


def test_high_and_low_levels_set_the_wave_s_extremes(awg):
    w(awg, "OUTP:LOAD INF;:APPL:SQU 1e3,1,0")
    w(awg, "VOLT:HIGH 3;:VOLT:LOW 1")
    t = np.linspace(0, 1e-3, 2000, endpoint=False) * 1e9
    v = wave(awg).sample(t)
    assert np.median(v[v > 2]) == pytest.approx(3.0, abs=0.02)
    assert np.median(v[v < 2]) == pytest.approx(1.0, abs=0.02)


def test_an_arbitrary_table_repeats_at_points_over_sample_rate(awg):
    w(awg, "OUTP:LOAD INF")
    w(awg, "DATA:ARB tri8," + ",".join(f"{v:g}" for v in script.TRIANGLE8))
    w(awg, "FUNC:ARB tri8;:APPL:ARB 4e4,2,0")  # 8 points at 40 kSa/s: 5 kHz
    sig = wave(awg)
    assert float(sig.freq[int(np.argmax(np.abs(sig.amp)))]) == pytest.approx(5e3)
    assert peak_to_peak(sig, 2e-4) == pytest.approx(2.0, rel=0.02)


def test_one_modulation_at_a_time_and_apply_turns_it_off(awg):
    w(awg, "APPL:SIN 1e4,1,0;:AM:INT:FREQ 500;:AM:STAT ON")
    lines = sorted(np.round(wave(awg).freq, 3))
    assert lines == [9500.0, 10000.0, 10500.0]  # carrier and the two AM sidebands
    w(awg, "FM:STAT ON")
    mod = awg.instruments["awg"].components["ch1.mod"]
    assert (mod.param("type"), mod.param("enabled")) == ("FM", True)
    assert awg.instruments["awg"].get_state("ch1.am_state") is False
    w(awg, "APPL:SIN 1e4,1,0")
    assert mod.param("enabled") is False


def test_polarity_inverts_the_output(awg):
    w(awg, "OUTP:LOAD INF;:APPL:DC DEF,DEF,1.5")
    out = awg.instruments["awg"].components["ch1.out"]
    w(awg, "OUTP:POL INV")
    assert out.param("invert") is True
