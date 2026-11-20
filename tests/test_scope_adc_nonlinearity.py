"""A scope ADC's non-idealities (#1266): the front end's harmonic distortion, the interleaved
sub-converters' spurs and the sampling clock's aperture jitter, each against its definition value;
and the ENOB they leave the UXR1104B with, against the data sheet's.

The frames are computed through ``AdcView`` (what ``scope_adc.frames`` computes through) on an
exact sine, sampled coherently, so a spectral line is one FFT bin and its level reads directly.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest

from simubench.api import open_bench
from simubench.engine.signal import Signal
from simubench.engine.stream import FrameRequest
from simubench.library.components.scope_record import (
    AdcView,
    aperture_jitter,
    converter_half,
    distortion,
    interleave,
    sub_converters,
)

ROOT = Path(__file__).resolve().parents[1]
FS = 2.56e11
N = 1 << 16
SCALE = 0.0875  # V/div: half the converter's 8 divisions is 0.35 V


def _view(**kw: object) -> AdcView:
    base = AdcView(
        signal=Signal.zero(), skew=0.0, sigma=0.0, seed=1, scale=SCALE, offset=0.0, bits=0, per_div=128.0,
        add=False, half=4 * SCALE,
    )  # fmt: skip
    return dataclasses.replace(base, **kw)


def _line_dbc(frame: np.ndarray, bin_: int, ref: float) -> float:
    """The level of FFT bin ``bin_`` of a coherently sampled frame, dB relative to amplitude ``ref``."""
    amp = 2 * abs(np.fft.rfft(frame)[bin_]) / frame.size
    return 20 * math.log10(amp / ref)


def _frame(view: AdcView, count: int = N, dt: float = 1 / FS, t0_ns: int = 0) -> np.ndarray:
    return np.asarray(view.frame(FrameRequest(t0_ns, count, dt, ("t",))).data)


@pytest.mark.parametrize("fraction", [1.0, 0.5])
def test_the_harmonics_follow_the_table_at_full_scale_and_fall_with_the_amplitude(fraction):
    """HD2 -54 dBc and HD3 -48 dBc at 0 dBFS (a2 = 2·10^(HD2/20), a3 = 4·10^(HD3/20)); half the
    amplitude: HD2 6 dB lower, HD3 12 dB lower (±1 dB)."""
    k = 1001
    amp = 4 * SCALE * fraction
    a2, a3 = 2 * 10 ** (-54 / 20), 4 * 10 ** (-48 / 20)
    view = _view(signal=Signal.lines([FS * k / N], [amp + 0j]), hd=(a2, a3))
    frame = _frame(view)
    assert _line_dbc(frame, 2 * k, amp) == pytest.approx(-54 + 20 * math.log10(fraction), abs=1.0)
    assert _line_dbc(frame, 3 * k, amp) == pytest.approx(-48 + 40 * math.log10(fraction), abs=1.0)


def test_the_interleaved_converters_make_images_and_a_spur_at_their_rate():
    """16 sub-converters at 256 GSa/s: a gain mismatch of -52 dBc puts an image at 16 GHz ± f, an
    offset mismatch of -65 dBFS a spur at 16 GHz (±1 dB). A slower record takes every M-th of the
    full-rate samples, each keeping its sub-converter (#1422): at 128 GSa/s the 16 GHz image and
    spur are still inside the band; at 16 GSa/s every sample is the same sub-converter's, so the
    pattern folds into that converter's gain and a DC offset — exactly its share of the cosine."""
    k = 1001
    amp = 4 * SCALE
    view = _view(
        signal=Signal.lines([FS * k / N], [amp + 0j]),
        ilv=16, ilv_gain=2 * 10 ** (-52 / 20), ilv_offset=10 ** (-65 / 20), native_dt=1 / FS,
    )  # fmt: skip
    frame = _frame(view)
    spur = N // 16
    assert _line_dbc(frame, spur - k, amp) == pytest.approx(-52, abs=1.0)
    assert _line_dbc(frame, spur + k, amp) == pytest.approx(-52, abs=1.0)
    assert _line_dbc(frame, spur, amp) == pytest.approx(-65, abs=1.0)
    # 128 GSa/s: N/2 samples, the same frequency resolution, the same bins
    half_rate = _frame(view, count=N // 2, dt=2 / FS)
    assert _line_dbc(half_rate, spur - k, amp) == pytest.approx(-52, abs=1.0)
    assert _line_dbc(half_rate, spur, amp) == pytest.approx(-65, abs=1.0)
    # 16 GSa/s from t = 0: sub-converter 0 throughout, cos(0) = 1: gain 1 + g, offset of half
    decimated = _frame(view, count=N // 16, dt=16 / FS)
    clean = _frame(dataclasses.replace(view, ilv=0), count=N // 16, dt=16 / FS)
    g, offset = 2 * 10 ** (-52 / 20), 10 ** (-65 / 20) * 4 * SCALE
    assert np.allclose(decimated, clean * (1 + g) + offset, rtol=0, atol=1e-12)


def test_each_sub_converter_s_own_mismatch_makes_the_images_of_its_fourier_series():
    """#1422: per-sub-converter gain errors g_m give, at k·fs/N ± f, images of |Σ g_m e^(-2πikm/N)| / N
    of the signal (the lists' discrete Fourier series); a cosine of timing skews of amplitude δ gives
    images of 2πf·δ / 2 (to first order, the slope times the skew)."""
    k, amp = 1001, 4 * SCALE
    f = FS * k / N
    gains = (3e-3, -1e-3, 0.0, 2e-3, -4e-3, 1e-3, 0.0, -1e-3)  # 8 sub-converters, made-up errors
    view = _view(signal=Signal.lines([f], [amp + 0j]), ilv=8, ilv_gains=gains, native_dt=1 / FS)
    frame = _frame(view)
    series = np.fft.fft(gains) / 8
    for j in (1, 2, 3):
        expected = 20 * math.log10(abs(series[j]))
        assert _line_dbc(frame, j * N // 8 + k, amp) == pytest.approx(expected, abs=0.2), j
    delta, f_hi = 5e-15, FS * 6001 / N  # 23.4 GHz
    skews = tuple(delta * math.cos(2 * math.pi * m / 16) for m in range(16))
    skewed = _frame(_view(signal=Signal.lines([f_hi], [amp + 0j]), ilv=16, ilv_skews=skews, native_dt=1 / FS))
    expected = 20 * math.log10(2 * math.pi * f_hi * delta / 2)
    assert _line_dbc(skewed, N // 16 + 6001, amp) == pytest.approx(expected, abs=0.5)


def _uxr1104b_view(n: int = 3) -> AdcView:
    """Channel n's converter view of the UXR1104B example bench, as its definition sets it."""
    session = open_bench(ROOT / "examples" / "benches" / "keysight-uxr1104b.bench.yaml", seed=3)
    try:
        return session.instruments["scope"].components["adc"].view(n)
    finally:
        session.close()


def _window_max_dbc(frame: np.ndarray, lo_hz: float, hi_hz: float, skip: set[int], ref: float) -> float:
    """The strongest line of a coherently sampled frame between ``lo_hz`` and ``hi_hz`` (DC and the
    ``skip`` bins left out), dB relative to amplitude ``ref``."""
    spectrum = 2 * np.abs(np.fft.rfft(frame)) / frame.size
    bins = [b for b in range(max(1, int(lo_hz * N / FS)), int(hi_hz * N / FS) + 1) if b not in skip]
    return 20 * math.log10(float(spectrum[bins].max()) / ref)


def test_the_uxr1104b_interleave_meets_the_data_sheet_in_its_measurement_windows():
    """#1579: the UXR1104B's lane and path mismatch (16 ADC lanes behind 4 SiGe samplers,
    3123-1371 p.11) against the data sheet 3123-1313 p.10, each figure where the data sheet measures
    it. Noise, distortion and jitter off: the interleave alone, on a -0.9 dBFS sine.
    - SFDR excluding harmonics, 5 GHz carrier in a 10 GHz span (0-10 GHz): <= -65 dBc;
    - SFDR, 50 GHz carrier in a 20 GHz span (40-60 GHz): <= -61 dBc; the one image there is the
      lanes' 6th harmonic, 96 - 50 = 46 GHz, at the -62.1 dBc the 5 fs skews give (±0.5 dB);
    - signal-related images, any 16 GHz interval: <= -52 dBc, also with a 105 GHz carrier."""
    view = _uxr1104b_view()
    quiet = dataclasses.replace(view, sigma=0.0, hd=(0.0, 0.0), aperture=0.0, clock=0.0, bits=0)
    amp = 0.9 * view.half
    for bin_, lo, hi, spec in ((1280, 0.0, 1e10, -65.0), (12800, 4e10, 6e10, -61.0)):  # 5 and 50 GHz
        frame = _frame(dataclasses.replace(quiet, signal=Signal.lines([FS * bin_ / N], [amp + 0j])))
        assert _window_max_dbc(frame, lo, hi, {bin_}, amp) <= spec, (bin_, spec)
        if bin_ == 12800:
            assert _line_dbc(frame, 6 * N // 16 - bin_, amp) == pytest.approx(-62.1, abs=0.5)
    bin_ = 26880  # 105 GHz
    frame = _frame(dataclasses.replace(quiet, signal=Signal.lines([FS * bin_ / N], [amp + 0j])))
    assert _window_max_dbc(frame, 0.0, FS / 2, {bin_}, amp) <= -52.0


def test_the_uxr1104b_residual_spurs_meet_the_data_sheet():
    """#1579, data sheet 3123-1313 p.10: residual responses -65 dBFS (the lanes' offsets, at
    16 GHz) and the -65 dBm clock spur at 64 GHz on the 0 dBm range (the 4 SiGe samplers' offsets,
    -65 dBFS), each ±0.5 dB; no other residual above -65 dBFS. No input, noise off."""
    view = _uxr1104b_view()
    quiet = dataclasses.replace(view, sigma=0.0, hd=(0.0, 0.0), aperture=0.0, clock=0.0, bits=0)
    frame = _frame(quiet)
    frame = frame - frame.mean()
    assert _line_dbc(frame, N // 16, view.half) == pytest.approx(-65.0, abs=0.5)  # 16 GHz
    assert _line_dbc(frame, N // 4, view.half) == pytest.approx(-65.0, abs=0.5)  # 64 GHz
    assert _window_max_dbc(frame, 0.0, FS / 2, set(), view.half) <= -65.0 + 0.5


def test_the_uxr1104b_jitter_is_mostly_the_shared_clock():
    """#1579, data sheet 3123-1313 p.11: intrinsic jitter 15 fs rms (< 1 µs acquisition) on one
    channel, < 10 fs rms between channels. Each sample's timing error, read off a 20 GHz sine
    where its slope is steep (the jitter moves a sample by slope × shift, scope_record.aperture_jitter),
    on two channels of one acquisition: each channel 15 fs (the shared clock 14.14 fs and its own
    5 fs), their difference √2 × 5 = 7.1 fs (±10 %, 16k samples). Skews off: the lanes' fixed
    pattern is the same on every channel and not jitter."""
    f = FS * 5120 / N  # 20 GHz
    sig = Signal.lines([f], [0.1 + 0j])
    shifts = []
    for n in (1, 2):
        view = _uxr1104b_view(n)
        quiet = dataclasses.replace(
            view, signal=sig, sigma=0.0, hd=(0.0, 0.0), bits=0, ilv_skews=(), ilv_offsets=()
        )
        frame = np.asarray(quiet.frame(FrameRequest(0, N, 1 / FS, ("acq", "noise", n, 1))).data)
        t = np.arange(N) / FS
        slope = (sig.scale(2j * math.pi * f)).sample_rel(0, t)
        steep = np.abs(slope) > 0.5 * float(np.abs(slope).max())
        shifts.append(((frame - sig.sample_rel(0, t)) / np.where(steep, slope, 1.0))[steep])
    assert float(np.std(shifts[0])) == pytest.approx(15e-15, rel=0.1)
    assert float(np.std(shifts[0] - shifts[1])) == pytest.approx(math.sqrt(2) * 5e-15, rel=0.1)
    assert float(np.std(shifts[0] - shifts[1])) < 10e-15


FS0 = 1.28e11  # the UXR0334B's 128 GSa/s (#1693)


def _uxr0334b_view(n: int = 3) -> AdcView:
    """Channel n's converter view of the UXR0334B example bench, as its definition sets it."""
    session = open_bench(ROOT / "examples" / "benches" / "keysight-uxr0334b.bench.yaml", seed=3)
    try:
        return session.instruments["scope"].components["adc"].view(n)
    finally:
        session.close()


def _line0_dbc(frame: np.ndarray, bin_: int, ref: float) -> float:
    """As ``_line_dbc`` at 128 GSa/s, but the Nyquist bin (N/2, where the 64 GHz clock spur sits) is a
    single real line of amplitude |X|/N, not the 2|X|/N of a bin with a negative-frequency pair."""
    spec = np.fft.rfft(frame)
    amp = abs(spec[bin_]) / frame.size if bin_ in (0, frame.size // 2) else 2 * abs(spec[bin_]) / frame.size
    return 20 * math.log10(max(amp, 1e-300) / ref)


def _window0_max_dbc(frame: np.ndarray, lo_hz: float, hi_hz: float, skip: set[int], ref: float) -> float:
    """As ``_window_max_dbc`` at 128 GSa/s, with the Nyquist bin's amplitude |X|/N (see ``_line0_dbc``)."""
    spectrum = np.abs(np.fft.rfft(frame))
    bins = [
        b for b in range(max(1, int(lo_hz * N / FS0)), min(int(hi_hz * N / FS0), N // 2) + 1) if b not in skip
    ]
    amp = np.array([spectrum[b] / frame.size if b == N // 2 else 2 * spectrum[b] / frame.size for b in bins])
    return 20 * math.log10(float(amp.max()) / ref)


def test_the_uxr0334b_interleave_meets_the_data_sheet_in_its_measurement_windows():
    """#1693: the UXR0334B's lane and path mismatch (8 ADC lanes behind 2 SiGe samplers,
    3123-1371 p.12) against the data sheet 3123-1313 p.10. The table is measured at 256 GSa/s (the 1 mm
    models); the 3.5 mm models have no table of their own, so the same figures are the target (#1422).
    Noise, distortion and jitter off: the interleave alone, on a -0.9 dBFS sine.
    - SFDR excluding harmonics, 5 GHz carrier in a 10 GHz span (0-10 GHz): <= -65 dBc;
    - SFDR, 50 GHz carrier in a 20 GHz span (40-60 GHz): <= -61 dBc; the images there are the lanes'
      1st and 2nd harmonics (16 + 50 = 66 -> 62 GHz, 32 + 50 = 82 -> 46 GHz), each at the -62.1 dBc the
      5 fs skews give (±0.5 dB);
    - signal-related images, any 16 GHz interval: <= -52 dBc, also with a 60 GHz carrier (near the
      64 GHz Nyquist; the 2 samplers' image at 64 - 60 = 4 GHz is the strongest, -55.7 dBc)."""
    view = _uxr0334b_view()
    quiet = dataclasses.replace(view, sigma=0.0, hd=(0.0, 0.0), aperture=0.0, clock=0.0, bits=0)
    amp = 0.9 * view.half
    for bin_, lo, hi, spec in ((2560, 0.0, 1e10, -65.0), (25600, 4e10, 6e10, -61.0)):  # 5 and 50 GHz
        frame = _frame(
            dataclasses.replace(quiet, signal=Signal.lines([FS0 * bin_ / N], [amp + 0j])), dt=1 / FS0
        )
        assert _window0_max_dbc(frame, lo, hi, {bin_}, amp) <= spec, (bin_, spec)
        if bin_ == 25600:
            assert _line0_dbc(frame, int(46e9 * N / FS0), amp) == pytest.approx(-62.1, abs=0.5)
    bin_ = 30720  # 60 GHz
    frame = _frame(dataclasses.replace(quiet, signal=Signal.lines([FS0 * bin_ / N], [amp + 0j])), dt=1 / FS0)
    assert _window0_max_dbc(frame, 0.0, FS0 / 2, {bin_}, amp) <= -52.0


def test_the_uxr0334b_residual_spurs_meet_the_data_sheet():
    """#1693, data sheet 3123-1313 p.10: residual responses -65 dBFS (the 8 lanes' offsets, at 16 GHz)
    and the -65 dBm clock spur at 64 GHz on the 0 dBm range (the 2 SiGe samplers' offsets, -65 dBFS),
    each ±0.5 dB; no other residual above -65 dBFS. No input, noise off. 64 GHz is the Nyquist bin
    (128 GSa/s / 2), read with |X|/N (``_line0_dbc``)."""
    view = _uxr0334b_view()
    quiet = dataclasses.replace(view, sigma=0.0, hd=(0.0, 0.0), aperture=0.0, clock=0.0, bits=0)
    frame = _frame(quiet, dt=1 / FS0)
    frame = frame - frame.mean()
    assert _line0_dbc(frame, N // 8, view.half) == pytest.approx(-65.0, abs=0.5)  # 16 GHz
    assert _line0_dbc(frame, N // 2, view.half) == pytest.approx(-65.0, abs=0.5)  # 64 GHz (Nyquist)
    assert _window0_max_dbc(frame, 0.0, FS0 / 2, {N // 8, N // 2}, view.half) <= -65.0 + 0.5


def test_the_uxr0334b_jitter_is_mostly_the_shared_clock():
    """#1693, data sheet 3123-1313 p.11: intrinsic jitter 15 fs rms (< 1 µs acquisition) on one
    channel, < 10 fs rms between channels. Each sample's timing error, read off a 20 GHz sine where its
    slope is steep, on two channels of one acquisition: each channel 15 fs (the shared clock 14.14 fs
    and its own 5 fs), their difference √2 × 5 = 7.1 fs (±10 %, 16k samples). The two channels share one
    acquisition board (3123-1371 p.12) but each has its own 2 SiGe samplers; the clock is shared
    instrument-wide (p.13). Skews off: the lanes' fixed pattern is the same on every channel, not jitter."""
    f = FS0 * 10240 / N  # 20 GHz
    sig = Signal.lines([f], [0.1 + 0j])
    shifts = []
    for n in (1, 2):
        view = _uxr0334b_view(n)
        quiet = dataclasses.replace(
            view, signal=sig, sigma=0.0, hd=(0.0, 0.0), bits=0, ilv_skews=(), ilv_offsets=()
        )
        frame = np.asarray(quiet.frame(FrameRequest(0, N, 1 / FS0, ("acq", "noise", n, 1))).data)
        t = np.arange(N) / FS0
        slope = (sig.scale(2j * math.pi * f)).sample_rel(0, t)
        steep = np.abs(slope) > 0.5 * float(np.abs(slope).max())
        shifts.append(((frame - sig.sample_rel(0, t)) / np.where(steep, slope, 1.0))[steep])
    assert float(np.std(shifts[0])) == pytest.approx(15e-15, rel=0.1)
    assert float(np.std(shifts[0] - shifts[1])) == pytest.approx(math.sqrt(2) * 5e-15, rel=0.1)
    assert float(np.std(shifts[0] - shifts[1])) < 10e-15


def test_the_aperture_jitter_adds_noise_of_two_pi_f_sigma_on_a_50_ghz_sine():
    """15 fs rms on a 50 GHz sine: the sampling error is 2πf·σ of the signal, rms (±10 %)."""
    f, sigma, amp = 5e10, 15e-15, 0.3
    sig = Signal.lines([f], [amp + 0j])
    jittered = _frame(_view(signal=sig, aperture=sigma))
    ideal = _frame(_view(signal=sig))
    ratio = float(np.std(jittered - ideal)) / (amp / math.sqrt(2))
    assert ratio == pytest.approx(2 * math.pi * f * sigma, rel=0.10)


@pytest.mark.parametrize(
    ("scale", "spec", "model"),
    [(0.05, 5.0, 5.35), (0.0075, 4.2, 4.41)],
    ids=["400mVfs", "60mVfs"],
)
def test_the_uxr1104b_enob_matches_the_data_sheet(scale, spec, model):
    """specs.enob_typical (≥ 400 mVfs: 5.0, 60 mVfs: 4.2) from a 0 dBFS 1 GHz sine through the
    UXR1104B's converter as its definition sets it: the range's noise, the distortion, the
    interleaving spurs, the aperture jitter and the 10-bit codes. The model gives 5.35 and 4.41
    (±0.1, so a change of any stage shows): 0.35 and 0.21 bit above the data sheet, whose ENOB is
    measured over the full 113 GHz, where the frequency response's error (not modeled) adds to
    the noise; the gap is larger on 400 mVfs, where the noise is a smaller part of the error. The
    model is never worse than the data sheet."""
    session = open_bench(ROOT / "examples" / "benches" / "keysight-uxr1104b.bench.yaml", seed=3)
    try:
        inst = session.instruments["scope"]
        session.instruments["scope"].set_state("ch3.scale", scale)
        view = inst.components["adc"].view(3)
    finally:
        session.close()
    k = 1001
    f = FS * k / N
    amp = 4 * scale * 0.999  # just inside full scale: no code clips
    frame = _frame(dataclasses.replace(view, signal=Signal.lines([f], [amp + 0j])))
    t = np.arange(N) / FS
    basis = np.stack([np.sin(2 * np.pi * f * t), np.cos(2 * np.pi * f * t), np.ones(N)], axis=1)
    fit, *_ = np.linalg.lstsq(basis, frame, rcond=None)
    residual = frame - basis @ fit
    sinad = 20 * math.log10((amp / math.sqrt(2)) / float(np.std(residual)))
    enob = (sinad - 1.76) / 6.02
    assert enob == pytest.approx(model, abs=0.1)
    assert enob >= spec - 0.05  # the model is never worse than the data sheet


def _sinad_enob(frame: np.ndarray, f: float, amp: float, fs: float = FS) -> float:
    """The effective bits of a frame holding one sine of amplitude ``amp`` at ``f``: a three-term
    sine fit, SINAD from the residual (IEEE 1241)."""
    t = np.arange(frame.size) / fs
    basis = np.stack([np.sin(2 * np.pi * f * t), np.cos(2 * np.pi * f * t), np.ones(frame.size)], axis=1)
    fit, *_ = np.linalg.lstsq(basis, frame, rcond=None)
    sinad = 20 * math.log10((amp / math.sqrt(2)) / float(np.std(frame - basis @ fit)))
    return (sinad - 1.76) / 6.02


def test_the_uxr1104b_harmonics_follow_the_data_sheet_over_frequency():
    """#1421: the UXR1104B's HD2 and HD3 at the input frequency, from the data sheet's 1, 16.5, 25
    and 50 GHz rows (87.5 mV/div, referred to a full-scale sine): read at the table's points,
    linearly between them, the nearest outside (50 GHz's HD3, not in the data sheet, repeats 25 GHz's). Then a
    25 GHz sine through the converter carries the 25 GHz row's harmonics (±1 dB)."""
    session = open_bench(ROOT / "examples" / "benches" / "keysight-uxr1104b.bench.yaml", seed=3)
    try:
        adc = session.instruments["scope"].components["adc"]
        db = lambda f: tuple(  # noqa: E731
            20 * math.log10(a / k) for a, k in zip(adc._distortion(0.0875, f), (2, 4), strict=True)
        )
        assert db(1e9) == pytest.approx((-54.1, -48.2)) and db(1e8) == pytest.approx((-54.1, -48.2))
        assert db(2.5e10) == pytest.approx((-50.1, -44.2))
        assert db(3.75e10) == pytest.approx((-46.6, -44.2))  # HD2 halfway; HD3 holds 25 GHz's
        assert db(6e10) == pytest.approx((-43.1, -44.2))
        assert db(None) == pytest.approx((-54.1, -48.2))  # no line: the lowest frequency's row
        assert db(2.5e10) != tuple(
            20 * math.log10(a / k) for a, k in zip(adc._distortion(0.0075, 2.5e10), (2, 4), strict=True)
        )
        hd = adc._distortion(0.0875, 2.5e10)
    finally:
        session.close()
    k = 100  # 100 cycles of 25 GHz in 1 024 samples at 256 GSa/s; 2f and 3f (50, 75 GHz) stay below FS/2
    f = 2.5e10
    n = round(k * FS / f)
    amp = 4 * SCALE
    frame = _frame(_view(signal=Signal.lines([f], [amp + 0j]), hd=hd), count=n)
    assert _line_dbc(frame, 2 * k, amp) == pytest.approx(-50.1, abs=1.0)
    assert _line_dbc(frame, 3 * k, amp) == pytest.approx(-44.2, abs=1.0)


def test_a_declared_enob_curve_sets_the_records_effective_bits():
    """#1421 (F1-1): SB-DSO-ENOB declares 9 bits at 1 MHz and 6 bits at 50 MHz. A full-scale sine
    from the generator, recorded at each frequency, has those effective bits (±0.15: 2 000-point
    records); halfway, 25.5 MHz, the linear interpolation's 7.5. With no line (the generator off)
    the converter still adds the lowest frequency's noise (#1442): the noise floor does not depend
    on whether a sine is applied."""
    from simubench.models.bench import BenchDefinition

    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2", "name": "enob",
            "instruments": {"awg": {"model": "sb-awg-2"}, "scope": {"model": "sb-dso-enob"}},
            "wires": [{"from": "awg.OUT1", "to": "scope.CH1"}], "run": {"seed": 5, "clock": "stepped"},
        }
    )  # fmt: skip
    session = open_bench(bench, definition_dirs=[ROOT / "validation" / "instruments"])
    try:
        scope = session.instruments["scope"]
        adc = scope.components["adc"]
        quiet = adc.view(1)
        assert quiet.sigma > 2 * scope.components["ch1.front"].input_noise_rms()  # no line: the floor stays
        session.instruments["awg"].set_state("ch1.output", True)
        session.instruments["awg"].set_state("ch1.frequency", 1e6)
        sine = adc.view(1)
        assert quiet.sigma == pytest.approx(sine.sigma, rel=1e-3)  # the lowest row's: 1 MHz
        for f, enob in ((1e6, 9.0), (2.55e7, 7.5), (5e7, 6.0)):
            session.instruments["awg"].set_state("ch1.frequency", f)
            view = adc.view(1)
            half = view.half
            amp = half * 0.999
            fs = 200 * f  # 200 samples per cycle, 10 cycles
            line = Signal.lines([f], [amp + 0j])
            frame = np.asarray(
                dataclasses.replace(view, signal=line).frame(FrameRequest(0, 2000, 1 / fs, ("e", f))).data
            )
            assert _sinad_enob(frame, f, amp, fs) == pytest.approx(enob, abs=0.15), f
    finally:
        session.close()


def test_a_per_sub_converter_list_must_match_the_interleave_count():
    """#1422: one value per sub-converter. A definition that changes ``interleave`` and keeps a
    parent's list is refused when it is validated, not when it first acquires."""
    from simubench.library.components.scope import ScopeAdc

    assert ScopeAdc.literal_problems({"interleave": 16, "interleave_skews_s": [0.0] * 16}) == []
    ((name, message),) = ScopeAdc.literal_problems({"interleave": 32, "interleave_skews_s": [0.0] * 16})
    assert name == "interleave_skews_s" and "16" in message and "32" in message


# ---- each stage on its own (#1305) ------------------------------------------------------------


def test_the_distortion_stage_leaves_dc_and_a_quiet_signal_alone():
    """x² and x³ act on the lines, not the DC: a pure DC level passes unchanged; on a sine the
    mean stays the DC (x²'s mean, the lines' power, is taken out)."""
    hd = (0.1, 0.05)
    dc = dataclasses.replace(Signal.zero(), dc=0.2)
    v = np.full(8, 0.2)
    assert np.array_equal(distortion(v, dc, hd, 0.35), v)
    f = FS * 7 / N  # coherent: 7 whole periods
    sig = dataclasses.replace(Signal.lines([f], [0.3 + 0j]), dc=0.1)
    t = np.arange(N) / FS
    clean = sig.sample_rel(0, t)
    bent = distortion(clean, sig, hd, 0.35)
    assert float(np.mean(bent)) == pytest.approx(0.1, abs=1e-9)
    assert float(np.max(np.abs(bent - clean))) > 1e-3
    assert np.array_equal(distortion(clean, sig, (0.0, 0.0), 0.35), clean)


def test_the_interleave_stage_repeats_every_n_samples_from_time_zero():
    """The pattern belongs to the converter's own grid counted from time zero: a sample keeps its
    sub-converter whichever frame it is in, and n samples on the pattern repeats."""
    dt = 1 / FS
    flat = np.zeros(64)
    gains, offsets = sub_converters(16, 0.0, 1e-3)
    whole = interleave(flat, 0, np.arange(64) * dt, dt, gains, offsets, 0.0)
    assert np.allclose(whole[:16], whole[16:32]) and np.allclose(whole[:16], whole[48:])
    later = interleave(np.zeros(16), 0, (np.arange(16) + 5) * dt, dt, gains, offsets, 0.0)
    assert np.allclose(later, whole[5:21])
    one = sub_converters(1, 0.1, 1e-3)
    assert np.array_equal(interleave(flat, 0, np.arange(64) * dt, dt, *one, 0.0), flat)
    # a record every 4th sample keeps each sample's sub-converter: 4 of the 16, repeating
    coarse = interleave(np.zeros(16), 0, np.arange(16) * 4 * dt, dt, gains, offsets, 0.0)
    assert np.allclose(coarse, whole[::4][:16])


def test_the_aperture_stage_moves_each_sample_by_the_slope_times_its_shift():
    """First order: v(t + δ) ≈ v(t) + v'(t)·δ, here against the exact sine (error ~ (ωδ)²)."""
    f, amp = 5e10, 0.3
    sig = Signal.lines([f], [amp + 0j])
    t = np.arange(256) / FS
    shift = np.full(256, 1e-15)
    moved = aperture_jitter(sig.sample_rel(0, t), sig, 0, t, shift)
    exact = sig.sample_rel(0, t + 1e-15)
    assert float(np.max(np.abs(moved - exact))) < amp * (2 * math.pi * f * 1e-15) ** 2
    dc_only = dataclasses.replace(Signal.zero(), dc=0.2)
    v = np.full(4, 0.2)
    assert np.array_equal(aperture_jitter(v, dc_only, 0, t[:4], shift[:4]), v)


def test_the_converter_half_is_its_codes_span():
    """10 bits at 128 codes per division: 8 divisions, half is 4 × V/div; no codes: the screen's."""
    assert converter_half(0.0875, 10, 128.0, 10.0) == pytest.approx(0.35)
    assert converter_half(0.1, 0, 128.0, 10.0) == pytest.approx(0.5)


def _noise_response_db(shape: str):
    """The power response of the noise-shaping FIR at 256 GSa/s with a 113 GHz cutoff, in dB
    relative to its mean below 0.8 of the cutoff, as (a lookup of the dB at a frequency, a mean over a band)."""
    from simubench.library.components.scope_record import noise_taps

    taps = noise_taps(shape, 1.13e11 / FS)
    assert taps is not None
    power = np.abs(np.fft.rfft(taps, 8192)) ** 2
    f = np.fft.rfftfreq(8192, 1 / FS)
    ref = power[f < 0.8 * 1.13e11].mean()
    return (
        lambda hz: 10 * np.log10(power[np.argmin(np.abs(f - hz))] / ref),
        lambda lo, hi: 10 * np.log10(power[(f >= lo) & (f <= hi)].mean() / ref),
    )


def test_the_brickwall_noise_filter_has_the_transition_band_the_docs_state():
    """#1462: the 49-tap filter is not a brick wall. Flat to 0.8 of the cutoff, -6 dB at it, down 20 dB
    from about 5 GHz above it (0.02 of the sample rate), -15 dB on average between the cutoff and the
    Nyquist frequency: signal-chain.md states these, the band 113-118 GHz is where it rolls off."""
    at, mean = _noise_response_db("brickwall")
    assert at(0.5e11) == pytest.approx(0.0, abs=0.05)
    assert at(1.06e11) > -3.0  # still near the passband
    assert at(1.13e11) == pytest.approx(-6.0, abs=0.5)  # the half-power point
    assert at(1.185e11) < -20.0  # 20 dB down within about 5 GHz past the cutoff
    assert mean(1.13e11, 1.18e11) == pytest.approx(-10.3, abs=1.0)  # the transition itself
    assert mean(1.13e11, 1.28e11) == pytest.approx(-15.0, abs=1.0)  # not -20 dB: part of it is the roll-off
    assert mean(1.185e11, 1.28e11) < -25.0


def test_the_gaussian_noise_shape_falls_only_about_3_db_past_the_cutoff():
    """#1462: a Gaussian response is -3 dB at its bandwidth and keeps falling slowly: between the
    cutoff and the Nyquist frequency the noise is about 2.8 dB down, as the analog response says."""
    _at, mean = _noise_response_db("gaussian")
    assert mean(1.13e11, 1.28e11) == pytest.approx(-2.8, abs=0.5)


def test_shaped_noise_keeps_its_rms_and_depends_on_the_index_only():
    """#1446: noise through the record's response keeps sigma (unit-power taps, ±3 %), loses what lies
    above the cutoff, and a sample's value depends on its stream index only: a tile computed alone
    equals that part of the whole record, scattered indexes give the same values as consecutive ones.
    A record whose Nyquist frequency is not above the cutoff keeps white noise."""
    from simubench.library.components.scope_record import noise_taps, shaped_gaussian

    view = _view(sigma=1e-3, noise_shape="brickwall", noise_fc=1.13e11)
    whole = _frame(view)
    assert float(np.std(whole)) == pytest.approx(1e-3, rel=0.03)
    power = np.abs(np.fft.rfft(whole)) ** 2
    f = np.fft.rfftfreq(whole.size, 1 / FS)
    assert power[f > 1.2e11].mean() < power[(f > 1e10) & (f < 1e11)].mean() / 100
    tile = np.asarray(view.frame(FrameRequest(0, 1000, 1 / FS, ("t",), first=4000, index0=4000)).data)
    assert np.array_equal(tile, whole[4000:5000])
    taps = noise_taps("brickwall", 1.13e11 / FS)
    assert taps is not None
    picked = np.array([7, 3, 900, 901, 42])
    assert np.allclose(shaped_gaussian(9, picked, taps), shaped_gaussian(9, np.arange(1000), taps)[picked])
    assert noise_taps("brickwall", 0.5) is None  # Nyquist at the cutoff: white
    slow = _view(sigma=1e-3)
    assert np.array_equal(
        _frame(dataclasses.replace(view, noise_fc=2e11), count=4096), _frame(slow, count=4096)
    )


@pytest.mark.parametrize(("span", "jitter"), [(1e-6, 15e-15), (1e-5, 25e-15), (1e-4, 40e-15), (1e-3, 50e-15)])
def test_the_uxr1104b_intrinsic_jitter_grows_with_the_acquisition_span(span, jitter):
    """#1698, data sheet 3123-1313 p.11: intrinsic jitter 15 / 25 / 40 / 50 fs rms for a < 1 µs, 10 µs,
    100 µs and 1 ms acquisition. The timing error of a 16k-sample record of that span, read off a
    20 GHz sine as in the test above (rms about the record's mean), averaged in variance over eight
    acquisitions (each draws its own phases), is the table's ±15 %; a segment of the record computed
    alone has the whole's values."""
    f = FS * 5120 / N
    sig = Signal.lines([f], [0.1 + 0j])
    view = dataclasses.replace(
        _uxr1104b_view(), signal=sig, sigma=0.0, hd=(0.0, 0.0), bits=0, ilv_skews=(), ilv_offsets=()
    )
    assert view.clock_tie
    dt = span / N
    t = np.arange(N) * dt
    slope = sig.scale(2j * math.pi * f).sample_rel(0, t)
    steep = np.abs(slope) > 0.5 * float(np.abs(slope).max())
    variances = []
    for acq in range(8):
        request = FrameRequest(0, N, dt, ("acq", "noise", 1, acq))
        frame = np.asarray(view.frame(request).data)
        shift = ((frame - sig.sample_rel(0, t)) / np.where(steep, slope, 1.0))[steep]
        variances.append(float(np.var(shift)))
    assert math.sqrt(float(np.mean(variances))) == pytest.approx(jitter, rel=0.15)
    # a segment alone equals the same samples of the whole record
    request = FrameRequest(0, N, dt, ("acq", "noise", 1, 0))
    whole = np.asarray(view.frame(request).data)
    part = np.asarray(view.frame(dataclasses.replace(request, count=1000, index0=5000, first=5000)).data)
    assert np.array_equal(part, whole[5000:6000])
