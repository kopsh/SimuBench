"""Reading noise (#746): the ``noise`` parameter of a meter component adds a seeded, per-reading
normal error after the calibration and before the quantization; the default is none."""

import math
import statistics
from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

SOURCE = Path(__file__).resolve().parent / "models" / "meter-core-test" / "meter-core-test.yaml"
LEVEL = 5.0
PPM = 20.0  # of the 10 V range at the reference time: rms 200 uV
NPLC_TIMES = "{0.02: 0.0004, 0.2: 0.004, 1: 0.02, 10: 0.2}"


def definition_dir(tmp_path: Path, noise: str | None) -> Path:
    text = SOURCE.read_text()
    extra = f"      reading_time: {{from: $sense.nplc, table: {NPLC_TIMES}}}\n"
    if noise:
        extra += f"      noise: {noise}\n"
    text = text.replace("      digits: 9\n", "      digits: 9\n" + extra, 1)
    out = tmp_path / "defs"
    out.mkdir(exist_ok=True)
    (out / "meter-core-test.yaml").write_text(text)
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
        "run": {"clock": "command", "seed": seed},
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
