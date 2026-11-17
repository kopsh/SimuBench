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
from simubench.library.components.scope_record import AdcView

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
    offset mismatch of -65 dBFS a spur at 16 GHz (±1 dB). Every 16th sample comes from one
    sub-converter: a record decimated by 16 carries no spur, only that converter's own gain and
    offset (sub-converter 0 at time zero: the pattern's peak)."""
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
    decimated = _frame(view, count=N // 16, dt=16 / FS)
    clean = _frame(dataclasses.replace(view, ilv=0), count=N // 16, dt=16 / FS)
    gain, offset = 1 + view.ilv_gain, view.ilv_offset * view.half
    assert np.allclose(decimated, gain * clean + offset, atol=1e-12)


def test_the_aperture_jitter_adds_noise_of_two_pi_f_sigma_on_a_50_ghz_sine():
    """15 fs rms on a 50 GHz sine: the sampling error is 2πf·σ of the signal, rms (±10 %)."""
    f, sigma, amp = 5e10, 15e-15, 0.3
    sig = Signal.lines([f], [amp + 0j])
    jittered = _frame(_view(signal=sig, aperture=sigma))
    ideal = _frame(_view(signal=sig))
    ratio = float(np.std(jittered - ideal)) / (amp / math.sqrt(2))
    assert ratio == pytest.approx(2 * math.pi * f * sigma, rel=0.10)


@pytest.mark.parametrize(
    ("scale", "spec"),
    [(0.05, 5.0), (0.0075, 4.2)],
    ids=["400mVfs", "60mVfs"],
)
def test_the_uxr1104b_enob_matches_the_data_sheet(scale, spec):
    """specs.enob_typical (≥ 400 mVfs: 5.0, 60 mVfs: 4.2) from a 0 dBFS 1 GHz sine through the
    UXR1104B's converter as its definition sets it: the range's noise, the distortion, the
    interleaving spurs, the aperture jitter and the 10-bit codes. The model leaves 0.2 to 0.35 bit
    more than the data sheet (its ENOB is measured over the full 113 GHz, where the frequency
    response adds error the model does not have): within 0.4 bit."""
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
    assert enob == pytest.approx(spec, abs=0.4)
    assert enob >= spec - 0.05  # the model is never worse than the data sheet
