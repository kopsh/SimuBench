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


def q(s, text: str) -> str:
    s.write("awg", (text + "\n").encode())
    reply = s.read("awg")
    return reply.decode().strip() if reply else ""


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


def test_changing_the_load_setting_keeps_the_output_and_rescales_the_readback(awg):
    """#436, 33500-90901 Output Termination: changing the load setting "does not affect the actual
    output voltage; it only changes the values displayed and queried" — 2 Vpp at 50 Ω reads 4 Vpp
    at high-Z, the offset doubles too, and the wave on the scope stays the same."""
    w(awg, "OUTP:LOAD 50;:APPL:SIN 1e3,2,0.5")
    before = peak_to_peak(wave(awg), 1e-3)
    w(awg, "OUTP:LOAD INF")
    assert float(q(awg, "VOLT?")) == pytest.approx(4.0)
    assert float(q(awg, "VOLT:OFFS?")) == pytest.approx(1.0)
    assert peak_to_peak(wave(awg), 1e-3) == pytest.approx(before, rel=1e-6)
    w(awg, "OUTP:LOAD 50")
    assert float(q(awg, "VOLT?")) == pytest.approx(2.0)
    assert q(awg, "SYST:ERR?") == '+0,"No error"'


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


@pytest.mark.parametrize(
    ("command", "error"),
    [
        # 33500-90901 DATA:ARBitrary: <arb_name> is an unquoted string of up to 12 characters;
        # the -224 is the model's guess — the guide names no error for an overlong name (#468)
        ("DATA:ARB abcdefghijklm,1,1,1,1,-1,-1,-1,-1", '-224,"Illegal parameter value"'),
        # a name already in volatile memory: +786 (clearing the memory is the only way to delete);
        # the guide does not say whether names are case-sensitive — the model matches them
        # case-insensitively (unverified, #468)
        ("DATA:ARB SQ8,1,1,1,1,-1,-1,-1,-1", '+786,"Specified arb waveform already exists"'),
    ],
    ids=["name-over-12", "name-exists"],
)
def test_arb_names_follow_the_guide(awg, command, error):
    """#430: a bad name is rejected and the memory is unchanged; +786 is the guide's error, the
    -224 for an overlong name is the model's choice (the guide names no error, #468)."""
    w(awg, "DATA:ARB sq8,1,1,1,1,-1,-1,-1,-1")
    w(awg, command)
    assert q(awg, "SYST:ERR?") == error
    assert q(awg, "DATA:VOL:CAT?") == '"sq8"'


def test_reset_and_clear_return_to_the_default_arb(awg):
    r"""#430: *RST plays the default waveform again but keeps volatile memory (Factory Reset State's
    Arb); DATA:VOL:CLE of the waveform playing is no error and reloads the default. The model's
    default waveform is unnamed so FUNC:ARB? answers ""; a real instrument's default Arb is the
    built-in Exponential Rise (INT:\BuiltIn\EXP_RISE.arb), #468."""
    w(awg, "DATA:ARB sq8,1,1,1,1,-1,-1,-1,-1;:FUNC:ARB sq8")
    w(awg, "*RST")
    assert q(awg, "FUNC:ARB?") == '""'
    assert q(awg, "DATA:VOL:CAT?") == '"sq8"'
    w(awg, "FUNC:ARB sq8")
    w(awg, "DATA:VOL:CLE")
    assert q(awg, "SYST:ERR?") == '+0,"No error"'
    assert q(awg, "FUNC:ARB?") == '""'
    assert q(awg, "DATA:VOL:CAT?") == '""'


def test_the_pulse_edge_times_shape_the_output():
    """#437: FUNC:PULS:TRAN:LEAD/TRA set the 10-90 % times of the pulse's edges, and FUNC:PULS:WIDT
    sets the width between the 50 % points (the duty follows: width x frequency)."""
    s = open_bench(BENCH, idle="off")
    try:
        w(s, ":FUNC PULS;:FREQ 1e5;:VOLT 2;:OUTP:LOAD INF")
        w(s, ":FUNC:PULS:WIDT 4e-6;:FUNC:PULS:TRAN:LEAD 1e-7;:FUNC:PULS:TRAN:TRA 2e-7")
        assert float(q(s, ":FUNC:PULS:DCYC?")) == pytest.approx(40.0)
        sig = wave(s)
        levels = sig.sample(np.array([2e-6, 7e-6]) * 1e9)  # the flat top and the flat bottom
        hi, lo = float(levels[0]), float(levels[1])
        span = hi - lo

        def window(start, stop):
            t = np.linspace(start, stop, 801)  # 1 ns steps
            return t, sig.sample(t * 1e9)

        def crossing(level, rising, tv):
            t, v = tv
            above = v >= level
            idx = np.nonzero(above[1:] & ~above[:-1] if rising else ~above[1:] & above[:-1])[0]
            return t[idx[0]]

        up, down = window(-4e-7, 4e-7), window(3.6e-6, 4.4e-6)  # around the two edges
        rise = crossing(lo + 0.9 * span, True, up) - crossing(lo + 0.1 * span, True, up)
        fall = crossing(lo + 0.1 * span, False, down) - crossing(lo + 0.9 * span, False, down)
        width = crossing(lo + 0.5 * span, False, down) - crossing(lo + 0.5 * span, True, up)
        assert rise == pytest.approx(1e-7, rel=0.1)
        assert fall == pytest.approx(2e-7, rel=0.1)
        assert width == pytest.approx(4e-6, rel=0.01)
    finally:
        s.close()
