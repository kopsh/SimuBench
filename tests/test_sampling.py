"""Equivalent-time sampling (#1094): clock recovery, pattern lock and the records of a sampling
scope (sb-dca-1), checked against the analytic waveform: levels, noise added in power and falling
by √N with averaging, timebase jitter through the waveform's slope, the time an acquisition takes,
and the refusals (not locked, eye mode, beyond the model)."""

import math
from pathlib import Path

import numpy as np
import pytest
import yaml
from refcalc.noise import sigma_interval

from simubench.api import open_bench
from simubench.engine.signal import Signal
from simubench.library.components.optical import modulation_levels
from simubench.library.components.sampling import repetition_rate, symbol_multiple
from simubench.library.components.sources import pam4_symbols, prbs_sequence

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "validation" / "benches" / "optical_dca.bench.yaml"
RATE = 2.65625e10
UI = 1 / RATE
P1, P0 = modulation_levels(1e-3, 6.0)


def _bench(tmp_path: Path, hidden: dict | None = None, otx: dict | None = None) -> Path:
    data = yaml.safe_load(BENCH.read_text("utf-8"))
    if hidden:
        data["instruments"]["dca"]["hidden"] = hidden
    if otx:
        data["instruments"]["otx"]["state"].update(otx)
    path = tmp_path / "b.bench.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    return path


def _record(s, ch: int = 1) -> np.ndarray:
    return np.array(
        [float(x) for x in s.query("dca", f":WAVeform:SOURce CHANnel{ch};:WAVeform:DATA?").split(b",")]
    )


def _clean(s, points: int, scale: float, position: float = 0.0) -> np.ndarray:
    """The noiseless channel-1 waveform at the record's instants (the signal at the sampler)."""
    acq = s.instrument("dca").components["acq"]
    t = position + 10 * scale / points * np.arange(points)
    return acq.input("in1").sample_rel(0, t)


def test_the_repetition_and_symbol_rate_come_from_the_pattern_spectrum():
    seq = prbs_sequence(7)
    n = seq.size
    f0 = RATE / n
    k = np.arange(1, 8 * n + 1)
    spec = np.fft.fft(seq) / n
    coef = spec[k % n] * np.sinc(k / n)
    keep = np.abs(coef) > 1e-15
    sig = Signal.lines(k[keep] * f0, 2 * coef[keep])
    assert repetition_rate(sig) == pytest.approx(f0, rel=1e-9)
    assert symbol_multiple(sig, f0) == 127
    assert repetition_rate(Signal.constant(1.0)) is None
    # PAM4 built from PRBS7 repeats every 127 symbols too, levels -1, -1/3, 1/3, 1
    symbols = pam4_symbols(seq)
    assert symbols.size == n
    assert sorted(set(np.round(symbols * 3).astype(int))) == [-3, -1, 1, 3]


def test_clock_recovery_locks_near_the_data_rate_only():
    with open_bench(BENCH) as s:
        assert s.query("dca", ":CRECovery:LOCKed?").strip() == b"1"
        assert float(s.query("dca", ":CRECovery:RRATe?")) == pytest.approx(RATE, rel=1e-9)
        assert s.query("dca", ":TRIGger:PLENgth?").strip() == b"127"
        # within the ±0.1 % lock range: still the data's own rate
        s.write("dca", b":CRECovery:RATE 26.58E9")
        assert float(s.query("dca", ":CRECovery:RRATe?")) == pytest.approx(RATE, rel=1e-9)
        # half the rate, or 1 % off: not locked; an acquisition is refused (-230)
        for nominal in (b"13.28125E9", b"26.83E9"):
            s.write("dca", b":CRECovery:RATE " + nominal)
            assert s.query("dca", ":CRECovery:LOCKed?").strip() == b"0"
        s.write("dca", b":SINGle")
        assert s.query("dca", ":SYSTem:ERRor?").startswith(b"-230")
        # no light: nothing to lock to
        s.write("dca", b":CRECovery:RATE 26.5625E9")
        s.write("otx", b":OUTPut 0")
        assert s.query("dca", ":CRECovery:LOCKed?").strip() == b"0"


def test_pattern_locked_record_shows_the_levels_and_the_receiver_edge():
    with open_bench(BENCH) as s:
        assert float(s.query("dca", ":CHANnel1:APOWer?")) == pytest.approx(1e-3, rel=1e-9)
        s.write("dca", b":TIMebase:SCALe 20E-12;:CHANnel1:SCALe 2E-4;:CHANnel1:OFFSet 1E-3;:SINGle")
        assert float(s.query("dca", ":MEASure:VTOP? CHANnel1")) == pytest.approx(P1, abs=2e-5)
        assert float(s.query("dca", ":MEASure:VBASe? CHANnel1")) == pytest.approx(P0, abs=2e-5)
        assert float(s.query("dca", ":MEASure:VAMPlitude? CHANnel1")) == pytest.approx(P1 - P0, abs=3e-5)
        # 10-90 % fall of a 4th-order Bessel at 45 GHz: about 0.34 / 45 GHz
        assert float(s.query("dca", ":MEASure:FALLtime? CHANnel1")) == pytest.approx(0.34 / 45e9, rel=0.08)
        # pattern lock: the same position in the pattern gives the same waveform, any acquisition
        first = _record(s)
        s.write("dca", b":SINGle")
        second = _record(s)
        assert first.size == 1024
        assert not np.array_equal(first, second)  # each acquisition draws its own noise
        assert np.abs(first - second).max() < 10 * 4.5e-6 * math.sqrt(2)
        # the whole pattern later, the same waveform (position + 127 UI)
        s.write("dca", f":TIMebase:POSition {127 * UI!r}".encode() + b";:SINGle")
        assert np.abs(_record(s) - first).max() < 10 * 4.5e-6 * math.sqrt(2)


def test_noise_adds_in_power_and_averaging_divides_it(tmp_path):
    """The receiver's 4 µW and the sampler's 1 % of a division (2 µW at 200 µW/div), no jitter."""
    scale = 2e-4
    sigma = math.hypot(4e-6, 0.01 * scale)
    with open_bench(_bench(tmp_path, {"acq": {"jitter_s": 0.0}})) as s:
        s.write("dca", f":TIMebase:SCALe 100E-12;:ACQuire:POINts 4096;:CHANnel1:SCALe {scale!r}".encode())
        s.write("dca", b":SINGle")
        residual = _record(s) - _clean(s, 4096, 100e-12)
        lo, hi = sigma_interval(sigma, residual.size)
        assert lo <= float(residual.std()) <= hi
        assert abs(float(residual.mean())) < 5 * sigma / math.sqrt(residual.size)
        s.write("dca", b":ACQuire:COUNt 16;:ACQuire:AVERage ON;:SINGle")
        residual = _record(s) - _clean(s, 4096, 100e-12)
        lo, hi = sigma_interval(sigma / 4, residual.size)
        assert lo <= float(residual.std()) <= hi


def test_jitter_moves_samples_along_the_slope(tmp_path):
    """Noise off: what is left is the 90 fs timebase jitter times the waveform's slope (small
    jitter: first order)."""
    hidden = {"acq": {"jitter_s": 9e-14}, "ch1.orx": {"noise_w": 0.0}, "ch1.front": {"noise_divs": 0.0}}
    with open_bench(_bench(tmp_path, hidden)) as s:
        s.write("dca", b":TIMebase:SCALe 100E-12;:ACQuire:POINts 8192;:SINGle")
        t = 1e-9 / 8192 * np.arange(8192)
        acq = s.instrument("dca").components["acq"]
        sig = acq.input("in1")
        h = 1e-14
        slope = (sig.sample_rel(0, t + h) - sig.sample_rel(0, t - h)) / (2 * h)
        residual = _record(s) - sig.sample_rel(0, t)
        expected = 9e-14 * float(np.sqrt(np.mean(slope**2)))
        lo, hi = sigma_interval(expected, residual.size, k=6.0)
        assert lo <= float(residual.std()) <= hi


def test_an_acquisition_takes_its_samples_time():
    with open_bench(BENCH, idle="off") as s:
        before = s.now_ns
        s.write("dca", b":ACQuire:POINts 2500;:SINGle")
        took = s.now_ns - before
        assert took == pytest.approx((2500 / 2.5e5 + 1e-3) * 1e9, rel=0.01)
        assert s.query("dca", ":ACQuire:COUNt:ACQuired?").strip() == b"1"
        s.write("dca", b":ACQuire:CLEar")
        assert s.query("dca", ":ACQuire:COUNt:ACQuired?").strip() == b"0"


def test_eye_mode_and_oversized_averages_are_refused():
    with open_bench(BENCH) as s:
        s.write("dca", b":TRIGger:PLOCk OFF;:SINGle")
        assert s.query("dca", ":SYSTem:ERRor?").startswith(b"-230")
        s.write(
            "dca", b":TRIGger:PLOCk ON;:ACQuire:POINts 16384;:ACQuire:COUNt 4096;:ACQuire:AVERage ON;:SINGle"
        )
        assert s.query("dca", ":SYSTem:ERRor?").startswith(b"-221")


def test_pam4_symbols_sit_on_four_levels(tmp_path):
    """PAM4 at 26.5625 GBd: sampled at every symbol's centre, each sample is one of the four levels
    Pavg ± OMA/2, ± OMA/6 (the 45 GHz receiver leaves a little inter-symbol interference)."""
    with open_bench(_bench(tmp_path, {"acq": {"jitter_s": 0.0}}, {"format": "PAM4"})) as s:
        assert s.query("dca", ":TRIGger:PLENgth?").strip() == b"127"
        scale = 127 * UI / 10
        s.write(
            "dca", f":TIMebase:SCALe {scale!r};:TIMebase:POSition {UI / 2!r};:ACQuire:POINts 127".encode()
        )
        s.write("dca", b":ACQuire:COUNt 64;:ACQuire:AVERage ON;:SINGle")
        v = _record(s)
        oma = P1 - P0
        levels = 1e-3 + oma / 2 * np.array([-1, -1 / 3, 1 / 3, 1])
        nearest = np.abs(v[:, None] - levels[None, :]).min(axis=1)
        assert nearest.max() < 0.08 * oma
        assert len({int(np.argmin(np.abs(levels - x))) for x in v}) == 4


def test_the_scope_screen_shows_the_sampling_records():
    """The Web and TUI scope screen (``/screen``) draws a sampling scope as it draws a real-time
    one: the record's trace in watts, and the measurement table on it."""
    from fastapi.testclient import TestClient

    from simubench.server.app import create_app
    from simubench.server.manager import SessionManager

    manager = SessionManager(ROOT, scpi_base_port=None)
    app = create_app(manager, token="t", web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = "t"
        sid = c.post(
            "/api/sessions", json={"bench_path": "validation/benches/optical_dca.bench.yaml", "force": True}
        ).json()["id"]
        r = c.post(f"/api/sessions/{sid}/instruments/dca/scpi", json={"command": ":STOP;:SINGle"}).json()
        assert r["errors"] == []
        screen = c.get(f"/api/sessions/{sid}/instruments/dca/screen", params={"points": 100}).json()
        assert screen["source"] == "record"
        assert [t["channel"] for t in screen["traces"]] == [1]
        trace = screen["traces"][0]
        assert len(trace["minmax"]) == 200
        assert max(trace["minmax"]) == pytest.approx(P1, abs=5e-5)
        assert screen["measurements"][0]["vmax"] == pytest.approx(P1, abs=5e-5)


def _analytic(seq: np.ndarray, bandwidth: float, t: np.ndarray) -> np.ndarray:
    """Independent of the engine: the PRBS pattern as ideal NRZ symbols (levels P0/P1, one UI each,
    repeating), through a 4th-order Bessel (scipy, -3 dB at ``bandwidth``, delay deskewed), by FFT
    over a fine grid of one pattern, read at the instants ``t`` (periodic, linear interpolation)."""
    from scipy import signal as sps

    per = 64
    dt = UI / per
    wave = np.repeat(np.where(seq > 0, P1, P0), per)
    b, a = sps.bessel(4, 2 * np.pi * bandwidth, btype="low", analog=True, norm="mag")
    f = np.fft.fftfreq(wave.size, dt)
    w = 2 * np.pi * f
    _, h = sps.freqs(b, a, worN=w)
    # remove the low-frequency group delay (a scope deskews it): the slope of the phase at DC
    eps = 2 * np.pi * bandwidth * 1e-4
    delay = -np.angle(sps.freqs(b, a, worN=[eps])[1][0]) / eps
    h = h * np.exp(1j * w * delay)
    y = np.fft.ifft(np.fft.fft(wave) * h).real
    x = (np.asarray(t) / dt) % y.size
    i = np.floor(x).astype(int)
    return y[i] * (1 - (x - i)) + y[(i + 1) % y.size] * (x - i)


def test_record_matches_an_independent_analytic_waveform(tmp_path):
    """The record equals the known pattern through the known filter, point by point: within the
    noise (6 σ) plus 5 % of the OMA, which is what the source's truncated line spectrum leaves
    against the ideal-symbol analytic (a wrong bandwidth is off by more, checked below); and the
    10-90 % fall time follows 0.34 / bandwidth at two bandwidths."""
    seq = prbs_sequence(7)
    scale, points = 127 * UI / 10, 1016
    sigma = math.hypot(4e-6, 0.01 * 2e-4)
    tol = 6 * sigma + 0.05 * (P1 - P0)
    wrong = {}
    for bw in (4.5e10, 2.25e10):
        hidden = {"acq": {"jitter_s": 0.0}, "ch1.orx": {"bandwidth": bw}}
        with open_bench(_bench(tmp_path, hidden)) as s:
            s.write(
                "dca",
                f":TIMebase:SCALe {scale!r};:ACQuire:POINts {points};:CHANnel1:SCALe 2E-4;:CHANnel1:OFFSet 1E-3;:SINGle".encode(),
            )
            t = 10 * scale / points * np.arange(points)
            rec = _record(s)
            assert np.abs(rec - _analytic(seq, bw, t)).max() < tol
            wrong[bw] = np.abs(rec - _analytic(seq, 4.5e10 if bw < 3e10 else 2.25e10, t)).max()
            s.write("dca", b":TIMebase:SCALe 20E-12;:SINGle")
            fall = float(s.query("dca", ":MEASure:FALLtime? CHANnel1"))
            assert fall == pytest.approx(0.34 / bw, rel=0.08)
    assert min(wrong.values()) > tol  # the check can tell one bandwidth from the other


def test_acquisition_time_is_proportional_to_the_points():
    with open_bench(BENCH, idle="off") as s:
        took = {}
        for points in (1000, 2000, 4000):
            before = s.now_ns
            s.write("dca", f":ACQuire:POINts {points};:SINGle".encode())
            took[points] = s.now_ns - before
        per_point = (took[4000] - took[1000]) / 3000
        assert per_point == pytest.approx(1e9 / 2.5e5, rel=0.01)
        assert took[2000] - took[1000] == pytest.approx(1000 * per_point, rel=0.01)
        assert took[4000] - took[2000] == pytest.approx(2000 * per_point, rel=0.01)


def test_the_same_seed_gives_the_same_waveform_value_for_value():
    records = []
    for _ in range(2):
        with open_bench(BENCH) as s:
            s.write("dca", b":ACQuire:POINts 512;:SINGle")
            records.append(_record(s))
    assert np.array_equal(records[0], records[1])


def test_clock_recovery_refuses_rates_outside_its_range(tmp_path):
    """The data's rate (26.5625 GBd, nominal rate matching) lies above a 20 GBd ``max_rate`` and
    below a 30 GBd ``min_rate``: not locked, an acquisition is refused with -230."""
    for limits in ({"max_rate": 2e10}, {"min_rate": 3e10}):
        with open_bench(_bench(tmp_path, {"cdr": limits})) as s:
            assert s.query("dca", ":CRECovery:LOCKed?").strip() == b"0"
            s.write("dca", b":SINGle")
            assert s.query("dca", ":SYSTem:ERRor?").startswith(b"-230")
    # inside the range it locks, the control
    with open_bench(_bench(tmp_path, {"cdr": {"min_rate": 1e9, "max_rate": 1e11}})) as s:
        assert s.query("dca", ":CRECovery:LOCKed?").strip() == b"1"
