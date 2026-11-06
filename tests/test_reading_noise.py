"""Reading noise (#746): the ``noise`` parameter of a meter component adds a seeded, per-reading
normal error after the calibration and before the quantization; the default is none."""

import math
import statistics
from pathlib import Path

import pytest

from helpers import session_query
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

SOURCE = Path(__file__).resolve().parent / "models" / "meter-core-test" / "meter-core-test.yaml"
ROOT = Path(__file__).resolve().parents[1]
KEYSIGHT_34465A = ROOT / "examples" / "benches" / "keysight-34465a.bench.yaml"
KEYSIGHT_34970A = ROOT / "examples" / "benches" / "keysight_34970a.bench.yaml"
LEVEL = 5.0
PPM = 20.0  # of the 10 V range at the reference time: rms 200 uV
NPLC_TIMES = "{0.02: 0.0004, 0.2: 0.004, 1: 0.02, 10: 0.2}"


def definition_dir(tmp_path: Path, noise: str | None) -> Path:
    text = SOURCE.read_text(encoding="utf-8")
    extra = f"      reading_time: {{from: $sense.nplc, table: {NPLC_TIMES}}}\n"
    if noise:
        extra += f"      noise: {noise}\n"
    text = text.replace("      digits: 9\n", "      digits: 9\n" + extra, 1)
    out = tmp_path / "defs"
    out.mkdir(exist_ok=True)
    (out / "meter-core-test.yaml").write_text(text, encoding="utf-8")
    return out


def open_noisy(
    tmp_path: Path, noise: str | None = f"{{ppm_of_range: {PPM}, ref_time_s: 0.02}}", seed: int = 1
):
    spec = {
        "name": "noise",
        "instruments": {
            "src": {"model": "sb-smu-1", "state": {"source.voltage.level": LEVEL, "output.enabled": True}},
            "dmm": {
                "model": "meter-core-test",
                "state": {"sense.nplc": 1, "sense.range": 10, "sense.range_auto": False},
            },
        },
        "wires": [{"from": "src.HI", "to": "dmm.IN"}],
        # idle off: a MEAS? costs no virtual time, so on the default idle='wall' the clock would
        # move by real gaps between commands (D47) — cold imports land in the first write and the
        # two sessions' now_ns stop matching when the file runs alone (#754).
        "run": {"clock": "command", "seed": seed, "idle": "off"},
    }
    return open_bench(BenchDefinition.model_validate(spec), definition_dirs=[definition_dir(tmp_path, noise)])


def q(s, cmd: str) -> str:
    s.write("dmm", (cmd + "\n").encode())
    out = s.read("dmm")
    return out.decode().strip() if out else ""


def reads(s, n: int, cmd: str = "MEAS:VOLT?") -> list[float]:
    return [float(q(s, cmd).lstrip("+")) for _ in range(n)]


def test_a_meter_without_noise_reads_the_same_every_time(tmp_path):
    with open_noisy(tmp_path, noise=None) as s:
        assert len(set(reads(s, 5))) == 1


def test_script_readings_differ_with_a_normal_error_around_the_value(tmp_path):
    with open_noisy(tmp_path) as s:
        values = reads(s, 400)
    sigma = PPM * 1e-6 * 10
    assert len(set(values)) > 20  # quantized to 30 uV steps
    assert statistics.mean(values) == pytest.approx(LEVEL, abs=4 * sigma / math.sqrt(400))
    assert statistics.stdev(values) == pytest.approx(sigma, rel=0.15)


def test_the_noise_falls_as_the_square_root_of_the_integration_time(tmp_path):
    spread = {}
    for nplc in (1, 10):
        with open_noisy(tmp_path) as s:
            q(s, f"SENS:VOLT:NPLC {nplc}")
            spread[nplc] = statistics.stdev(reads(s, 400))
    assert spread[10] / spread[1] == pytest.approx(math.sqrt(0.1), rel=0.2)


def test_the_same_seed_replays_bit_for_bit_and_another_seed_differs(tmp_path):
    with open_noisy(tmp_path, seed=7) as s:
        first = reads(s, 20)
    with open_noisy(tmp_path, seed=7) as s:
        again = reads(s, 20)
    with open_noisy(tmp_path, seed=8) as s:
        other = reads(s, 20)
    assert first == again
    assert first != other


def test_the_noise_is_added_before_quantization(tmp_path):
    # a 10 V range at 3e-6 of range is a 30 uV step; 200 uV rms noise flickers across ~13 steps
    with open_noisy(tmp_path) as s:
        values = reads(s, 200)
    steps = {round((v - LEVEL) / 30e-6) for v in values}
    assert len(steps) > 6
    assert all(abs(v / 30e-6 - round(v / 30e-6)) < 1e-6 for v in values)


def test_the_panel_readings_move_each_frame_without_changing_what_a_script_sees(tmp_path):
    with open_noisy(tmp_path) as alone:
        expected = [(reads(alone, 1)[0], alone.now_ns) for _ in range(5)]
    with open_noisy(tmp_path) as watched:
        meter = watched.instruments["dmm"].components["meter"]
        seen, frames = [], []
        for _ in range(5):
            frames += [meter.readings()["values"]["DC"] for _ in range(3)]  # the screen redraws
            seen.append((reads(watched, 1)[0], watched.now_ns))
    assert seen == expected
    assert len(set(frames)) > 8  # 15 frames on a 30 uV grid: not frozen


def test_an_smu_meter_reads_with_noise_per_reading_and_per_frame():
    spec = {
        "name": "smu-noise",
        "instruments": {
            "smu": {
                "model": "keysight-b2901a",
                "state": {"source.voltage.level": 1.0, "output.enabled": True},
            },
        },
        "wires": [],
        "run": {"clock": "command", "seed": 3},
    }
    with open_bench(BenchDefinition.model_validate(spec)) as s:
        meter = s.instruments["smu"].components["core"]
        scpi = lambda: float(  # noqa: E731
            (s.write("smu", b":MEAS:VOLT?\n"), s.read("smu"))[1].decode().strip().lstrip("+")
        )
        assert len({scpi() for _ in range(5)}) == 1  # default: noiseless
        meter.overrides["noise"] = {"ppm_of_range": 100.0, "ref_time_s": 0.02}
        script = [scpi() for _ in range(40)]
        assert len(set(script)) > 20
        frames = [meter.readings()["values"]["voltage"] for _ in range(40)]
        assert len(set(frames)) > 20 and frames != script
        assert scpi() != scpi()


def test_a_sample_count_burst_takes_distinct_samples_and_replays_bit_for_bit():
    """``SAMPle:COUNt 5`` on the 34465A (#749): one trigger's 5 samples are 5 independent draws, a
    second burst draws anew (the meter's script-read counter keeps counting past the trigger
    model's per-initiate sample index), and the same seed with the same commands replays bit for
    bit (D32)."""
    NOISE = {"ppm_of_range": 100.0, "ref_time_s": 0.02}

    def burst():
        with open_bench(KEYSIGHT_34465A, idle="off") as s:
            s.instruments["dut"].components["meter"].overrides["noise"] = dict(NOISE)
            session_query(s, "dut", "SAMP:COUN 5")
            return [float(v) for v in session_query(s, "dut", "READ?").split(",")]

    first = burst()
    assert len(first) == 5 and len(set(first)) == 5
    assert burst() == first
    with open_bench(KEYSIGHT_34465A, idle="off") as s:
        s.instruments["dut"].components["meter"].overrides["noise"] = dict(NOISE)
        session_query(s, "dut", "SAMP:COUN 5")
        second = [float(v) for v in session_query(s, "dut", "READ?").split(",")]
        third = [float(v) for v in session_query(s, "dut", "READ?").split(",")]
    assert len(set(second)) == 5 and set(second).isdisjoint(third)


def test_the_noise_is_not_scaled_by_the_calibration_gain(tmp_path):
    sigma = PPM * 1e-6 * 10
    with open_noisy(tmp_path) as s:
        plain = reads(s, 200)
    with open_noisy(tmp_path) as s:
        s.instruments["dmm"].components["meter"].overrides["cal_gain"] = 2.0
        doubled = reads(s, 200)
    # The gain corrects the signal (the mean doubles); the noise is added after the calibration
    # (#746), so the spread stays the meter's own rms instead of doubling with the gain.
    assert statistics.mean(doubled) == pytest.approx(2 * LEVEL, abs=4 * sigma / math.sqrt(200))
    assert statistics.stdev(doubled) == pytest.approx(sigma, rel=0.15)
    assert statistics.stdev(doubled) == pytest.approx(statistics.stdev(plain), rel=0.3)


def test_one_frame_draws_the_dc_and_ac_noise_independently(tmp_path):
    """The function is part of the noise key (#746): the panel's DC and AC readings of one frame
    are different draws — with the function missing from the key every frame would flick
    identically on both."""
    with open_noisy(tmp_path) as s:
        meter = s.instruments["dmm"].components["meter"]
        frames = [meter.readings()["values"] for _ in range(20)]
    step = 3.0e-6 * 10  # the NPLC-1 resolution fraction of the 10 V range: a 30 uV grid
    dc = [round((f["DC"] - LEVEL) / step) for f in frames]
    ac = [round(f["AC"] / step) for f in frames]  # a DC source's AC reading is the noise alone
    assert len(set(dc)) > 8 and len(set(ac)) > 8  # both functions move frame by frame
    assert ac != dc and ac != [-d for d in dc]  # ... but not with the same draws


def test_scan_channels_draw_independently_around_their_own_values():
    """A 34970A scan (#749): every scanned point is its own draw around that channel's own value,
    with the rms of the range the scan took (1 V and 10 V, autoranged), and the same seed replays
    the whole scan bit for bit."""
    PPM_100 = {"ppm_of_range": 100.0, "ref_time_s": 0.02}

    def scan():
        with open_bench(KEYSIGHT_34970A, idle="off") as s:
            s.instruments["daq"].components["meter"].overrides["noise"] = dict(PPM_100)
            session_query(s, "daq", "ROUT:SCAN (@101,103)")
            session_query(s, "daq", "TRIG:COUN 100")
            return [float(v) for v in session_query(s, "daq", "READ?").split(",")]

    values = scan()
    assert len(values) == 200
    ch101, ch103 = values[0::2], values[1::2]
    sigma101, sigma103 = 100e-6 * 1.0, 100e-6 * 10.0  # 1 V and 10 V: the ranges autorange picks
    n = math.sqrt(100)
    assert statistics.mean(ch101) == pytest.approx(10 / 11, abs=4 * sigma101 / n)
    assert statistics.mean(ch103) == pytest.approx(40 / 11, abs=4 * sigma103 / n)
    assert statistics.stdev(ch101) == pytest.approx(sigma101, rel=0.35)
    assert statistics.stdev(ch103) == pytest.approx(sigma103, rel=0.35)
    assert scan() == values


@pytest.mark.parametrize(
    ("noise", "fragment"),
    [
        ("{ppm_of_rang: 20}", "ppm_of_rang"),
        ("{ppm_of_range: -5}", "-5"),
        ("{ppm_of_range: abc}", "abc"),
        ("{ppm_of_range: 1, table: {abc: 1}}", "abc"),
        ("{ppm_of_range: 1, table: {10: -1}}", "-1"),
        ("{ppm_of_range: 1, ref_time_s: 0}", "ref_time_s"),
        ("{ppm_of_range: 1, ref_time_s: -1}", "ref_time_s"),
        ("5", "noise"),
    ],
)
def test_a_misspelled_noise_parameter_fails_when_the_definition_loads(tmp_path, noise, fragment):
    with pytest.raises(Exception) as err:
        open_noisy(tmp_path, noise=noise)
    text = str(err.value)
    assert "instrument 'dmm'" in text  # the instrument, then the component and the parameter
    assert "meter.noise" in text
    assert fragment in text


def test_an_empty_noise_parameter_is_still_noiseless(tmp_path):
    with open_noisy(tmp_path, noise="null") as s:
        a = reads(s, 2)
    assert a[0] == a[1]
