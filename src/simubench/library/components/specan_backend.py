"""The spectrum analyzer's digital back end (signal-chain.md 5.3, D67): what follows the IF's ADC.

``specan_ddc`` multiplies the ADC's samples by a numerically controlled oscillator, so the
intermediate frequency lands on 0 Hz, and brings the rate down through a decimation filter;
``specan_rbw`` is the resolution filter — a windowed FFT whose window's frequency response *is*
the RBW filter. The same block answers both ways of drawing a trace (signal-chain.md 5.4): the
analytic evaluation draws every spectral line through :func:`line_response`, the window's
frequency response in closed form; the sampled one transforms frames of samples with the window's
coefficients (:func:`gaussian_window`, :func:`windowed_fft`). One set of parameters, one filter.

The algorithms are module-level pure functions (:func:`decimation_taps`, :func:`ddc`,
:func:`gaussian_window`, :func:`windowed_fft`): the blocks wrap them and keep nothing between
frames, so each can be tested without a session and compared with a reference (signal-chain.md 8).
"""

from __future__ import annotations

import math
from dataclasses import replace
from functools import lru_cache
from typing import Any

import numpy as np
from scipy.special import erf

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.stream import BINS, IQ, FrameRequest, SampleFrame
from ...scpi.errors import ScpiError

RBW_LADDER = [m * 10.0**k for k in range(0, 7) for m in (1.0, 3.0)]  # 1 Hz ... 3 MHz
RBW_LADDER = [r for r in RBW_LADDER if r <= 3e6]
# The "10%" filter steps of analyzers with a digital IF (Keysight X-Series, #616): the E24 values
# from 1 Hz to 3 MHz, then 4, 5, 6 and 8 MHz.
_E24 = (
    1.0,
    1.1,
    1.2,
    1.3,
    1.5,
    1.6,
    1.8,
    2.0,
    2.2,
    2.4,
    2.7,
    3.0,
    3.3,
    3.6,
    3.9,
    4.3,
    4.7,
    5.1,
    5.6,
    6.2,
    6.8,
    7.5,
    8.2,
    9.1,
)
TEN_PCT_LADDER = [round(m * 10.0**k, 6) for k in range(0, 7) for m in _E24 if m * 10.0**k <= 3e6 + 1] + [
    4e6,
    5e6,
    6e6,
    8e6,
]
STEPS = ("1-3-10", "10%")
NBW_FACTOR = math.sqrt(math.pi / (4 * math.log(2)))  # noise bandwidth of the Gaussian filter: 1.0645 RBW
KT0_DBM_HZ = -174.0


def line_response(d: np.ndarray, rbw: float, bucket: float, detector: str) -> np.ndarray:
    """Power a trace point shows of a unit line ``d`` Hz from the point, per detector.

    The Gaussian RBW filter swept across the point's bucket: the sample detector sees the filter
    at the point; the positive-peak detector the highest response within the bucket; average and
    rms detectors the mean over the bucket. When the bucket is narrower than the RBW all agree.
    """
    k = 4 * math.log(2) / rbw**2
    if detector == "SAMPle" or bucket <= rbw / 50:
        return np.exp(-k * d * d)
    if detector in ("POSitive", "NORMal"):
        e = np.maximum(np.abs(d) - bucket / 2, 0.0)
        return np.exp(-k * e * e)
    if detector == "NEGative":
        e = np.abs(d) + bucket / 2
        return np.exp(-k * e * e)
    rk = math.sqrt(k)
    return (math.sqrt(math.pi) / (2 * rk * bucket)) * (
        erf(rk * (d + bucket / 2)) - erf(rk * (d - bucket / 2))
    )


def snap_rbw(value: float) -> float:
    """The nearest 1-3-10 RBW setting."""
    return min(RBW_LADDER, key=lambda r: abs(math.log10(r) - math.log10(max(value, 1e-12))))


def filter_ladder(steps: str, lo: float, hi: float) -> list[float]:
    """The bandwidths a filter offers between ``lo`` and ``hi``: the 1-3-10 ladder, or the "10%"
    steps (E24 to 3 MHz, then 4/5/6/8 MHz). A ``hi`` above the ladder's top is one more setting,
    the wide-open position of a video filter (the X-Series 50 MHz VBW, #616)."""
    base = TEN_PCT_LADDER if steps == "10%" else RBW_LADDER
    out = [b for b in base if lo - 1e-9 <= b <= hi + 1e-9]
    if hi > base[-1] * (1 + 1e-9):
        out.append(hi)
    return out


def snap_filter(value: float, steps: str, lo: float, hi: float) -> float:
    """The setting a bandwidth entry selects: the nearest on a log scale for the 1-3-10 ladder;
    for the "10%" steps the nearest on a linear scale, a tie rounding up (the X-Series rule)."""
    ladder = filter_ladder(steps, lo, hi)
    if steps != "10%":
        return min(ladder, key=lambda r: abs(math.log10(r) - math.log10(max(value, 1e-12))))
    return min(ladder, key=lambda r: (abs(r - value), -r))


# ---- the digital down-converter ------------------------------------------------------------------

#: Stopband attenuation of the decimation filter the blocks ask for by default, dB.
STOPBAND_DB = 80.0


#: What the decimation filter is designed beyond its stated stopband attenuation, dB.
DESIGN_MARGIN_DB = 3.0


def taps_per_phase(stopband_db: float) -> int:
    """How many output intervals the decimation filter spans: Kaiser's length estimate for a
    transition from 0.25 to 0.75 of the output rate, (A - 7.95) / 7.18, rounded up."""
    return max(2, math.ceil((stopband_db + DESIGN_MARGIN_DB - 7.95) / 7.18))


@lru_cache(maxsize=2)
def decimation_taps(decimation: int, stopband_db: float = STOPBAND_DB) -> np.ndarray:
    """The low-pass a decimation by ``decimation`` needs, as FIR coefficients (unity gain at DC).

    A Kaiser-windowed sinc, ``taps_per_phase(stopband_db) * decimation`` taps long, its cutoff at
    half the *output* rate. Relative to the output rate f_out: the passband runs to 0.25 f_out
    (ripple below 0.01 dB), the stopband starts at 0.75 f_out (``stopband_db`` down) — whatever
    folds onto the passband when every ``decimation``-th sample is kept is that far down. The band
    a reader may use is therefore the middle half of the output rate. ``decimation`` 1 is no
    filter at all: one tap of 1."""
    if decimation < 1:
        raise ValueError(f"a decimation of {decimation}: at least 1")
    if decimation == 1:
        taps = np.ones(1)
    else:
        count = taps_per_phase(stopband_db) * decimation
        # Kaiser's estimate lands a decibel or so short at small decimations: design for more
        design_db = stopband_db + DESIGN_MARGIN_DB
        beta = 0.1102 * (design_db - 8.7) if design_db > 50 else 0.0
        k = np.arange(count) - (count - 1) / 2
        taps = np.sinc(k / decimation) * np.kaiser(count, beta)
        taps /= taps.sum()
    taps.flags.writeable = False
    return taps


def ddc(samples: np.ndarray, cycles_per_sample: float, decimation: int, taps: np.ndarray) -> np.ndarray:
    """Digital down-conversion: real ``samples`` times a complex oscillator of
    ``cycles_per_sample`` (its frequency over the sample rate), through the FIR ``taps``, every
    ``decimation``-th result kept.

    Output m is the filter over input samples m·decimation ... m·decimation + len(taps) - 1: a
    causal filter whose history is the first ``len(taps) - 1`` samples, so ``n`` inputs give
    ``(n - len(taps)) // decimation + 1`` outputs. The oscillator's phase is 0 at input sample 0.
    The result is scaled by 2: a real tone of amplitude A at the oscillator's frequency comes out
    as the constant A (its negative-frequency half is what the filter removes)."""
    x = np.asarray(samples, dtype=np.float64)
    length = int(taps.size)
    if length % decimation:
        raise ValueError(f"{length} taps do not divide into phases of {decimation}")
    phases = length // decimation
    count = (x.size - length) // decimation + 1
    if count < 1:
        raise ValueError(f"{x.size} samples are fewer than the filter's {length} taps")
    rows = count + phases - 1
    by_phase = np.ascontiguousarray(taps.reshape(phases, decimation).T)  # (decimation, phases)
    partial = np.empty((rows, phases), dtype=np.complex128)
    step = max(1, (1 << 20) // decimation)  # rows mixed and filtered at a time: bounded memory
    for start in range(0, rows, step):
        stop = min(rows, start + step)
        k = np.arange(start * decimation, stop * decimation, dtype=np.float64)
        phase = cycles_per_sample * k
        mixed = x[start * decimation : stop * decimation] * np.exp(-2j * np.pi * (phase - np.floor(phase)))
        partial[start:stop] = mixed.reshape(stop - start, decimation) @ by_phase
    out = np.zeros(count, dtype=np.complex128)
    for j in range(phases):
        out += partial[j : j + count, j]
    return 2.0 * out


# ---- the resolution filter as a window -----------------------------------------------------------

#: Where the Gaussian window is cut off, in standard deviations each side of its middle: the
#: response follows the Gaussian down to about -70 dB of its peak, below that the cut shows.
WINDOW_SIGMAS = 4.0
#: Frequency points a transform gives per resolution bandwidth at least: a line between two points
#: reads less than 0.005 dB low.
BINS_PER_RBW = 16


def window_sigma_s(rbw: float) -> float:
    """Standard deviation, s, of the Gaussian window whose frequency response is the Gaussian
    filter of ``rbw`` Hz at -3 dB: sqrt(ln 2) / (pi * rbw)."""
    return math.sqrt(math.log(2.0)) / (math.pi * rbw)


def window_length(rbw: float, dt_s: float) -> int:
    """Samples ``dt_s`` apart the window of ``rbw`` spans: ``WINDOW_SIGMAS`` each side."""
    return max(8, math.ceil(2 * WINDOW_SIGMAS * window_sigma_s(rbw) / dt_s))


def gaussian_window(count: int, rbw: float, dt_s: float) -> np.ndarray:
    """The window's coefficients: a Gaussian centered on the frame, exp(-t² / 2σ²)."""
    t = (np.arange(count) - (count - 1) / 2) * dt_s
    return np.exp(-0.5 * (t / window_sigma_s(rbw)) ** 2)


def noise_bandwidth(window: np.ndarray, dt_s: float) -> float:
    """Equivalent noise bandwidth of a window, Hz: Σw² / (Σw)² over the sample interval."""
    return float(np.sum(window * window) / np.sum(window) ** 2 / dt_s)


def windowed_fft(samples: np.ndarray, window: np.ndarray, points: int) -> np.ndarray:
    """The complex amplitude at ``points`` frequencies of the windowed ``samples``.

    Point k stands for (k - points/2) / (points · dt): the spectrum is centered, 0 Hz in the
    middle. Scaled by the window's sum, so a steady complex tone of amplitude A reads |A| at its
    frequency whatever the window. ``points`` beyond the window's length is zero padding: more
    points on the same response."""
    if samples.size != window.size:
        raise ValueError(f"{samples.size} samples under a window of {window.size}")
    if points < window.size:
        raise ValueError(f"{points} frequency points are fewer than the window's {window.size} samples")
    spectrum = np.fft.fft(samples * window, points) / float(np.sum(window))
    return np.fft.fftshift(spectrum)


# ---- the blocks ----------------------------------------------------------------------------------


def _upstream(block: Component, port: str) -> tuple[Component, str]:
    src = None if block.owner is None else block.owner.wiring.source(block.name, port)
    if src is None or block.owner is None:
        raise ScpiError(-300, f"{block.uid}: samples input {port!r} is not wired")
    return block.owner.components[src[0]], src[1]


@component("specan_ddc")
class SpecanDdc(Component):
    """The digital down-converter (signal-chain.md 5.3): the ADC's samples times a numerically
    controlled oscillator at ``nco_hz`` — the intermediate frequency comes to 0 Hz — then the
    decimation filter and the lower rate. ``out`` carries complex baseband samples (``iq``) whose
    0 Hz stands for the frequency the receiver is tuned to (the frame's ``center_hz``); a real tone
    of amplitude A on the IF is the complex amplitude A.

    ``decimation`` 0 is automatic: the block runs at the rate asked of it, a whole number of ADC
    samples per output sample (the resolution filter behind it asks for a rate that follows the
    RBW). The filter is a Kaiser-windowed sinc with ``stopband`` dB of attenuation
    (:func:`decimation_taps`): flat over the middle half of the output rate, which is the band a
    reader may use; its length is ``taps_per_phase(stopband)`` output intervals, and that history —
    one tap less than its length, in ADC samples — is the warm-up it asks the ADC for ahead of
    every frame. A decimation of 1 has no filter and no warm-up.

    The oscillator is a number of cycles per ADC sample, so it runs off the ADC's clock: an error
    of the reference moves it with the sample rate, and the frequency scale of everything behind
    it carries that error once, as the swept analyzer's does."""

    ports = {"in": PortSpec("in", "samples"), "out": PortSpec("out", "samples")}
    defaults = {
        "nco_hz": 21.4e6,  # Hz, the oscillator's frequency: the intermediate frequency it brings to 0 Hz
        "decimation": 0,  # ADC samples per output sample (0: automatic, the rate asked of it)
        "stopband": STOPBAND_DB,  # dB, the decimation filter's stopband attenuation
    }
    units = {"nco_hz": "Hz", "stopband": "dB"}

    def _adc_interval(self) -> float:
        block, port = _upstream(self, "in")
        return float(block.sample_interval(port))

    def decimation_for(self, dt_s: float) -> int:
        """ADC samples per output sample when the output runs ``dt_s`` apart."""
        fixed = int(self.param("decimation"))
        if fixed > 0:
            return fixed
        return max(1, round(dt_s / self._adc_interval()))

    def interval_near(self, wanted_s: float) -> float:
        """The output interval this block can run at that is nearest below ``wanted_s`` (a whole
        number of ADC samples; the fixed ``decimation`` when one is set)."""
        adc = self._adc_interval()
        fixed = int(self.param("decimation"))
        return adc * (fixed if fixed > 0 else max(1, math.floor(wanted_s / adc + 1e-9)))

    def taps(self, decimation: int) -> np.ndarray:
        return decimation_taps(decimation, float(self.param("stopband")))

    def filter_length(self, decimation: int) -> int:
        """Taps of the decimation filter (1: no filter), without designing it."""
        return 1 if decimation == 1 else taps_per_phase(float(self.param("stopband"))) * decimation

    def upstream_request(self, port: str, request: FrameRequest) -> FrameRequest:
        """What a request for ``out`` asks the ADC for: ``decimation`` samples per output sample
        and the filter's history ahead of the first."""
        d = self.decimation_for(request.dt_s)
        ratio = request.dt_s / (d * self._adc_interval())
        if abs(ratio - 1.0) > 1e-3:
            raise ScpiError(
                -221,
                f"{self.uid}: asked for {1.0 / request.dt_s:.6g} Sa/s, which is not the ADC's rate over {d}",
            )
        return request.upstream(d, self.filter_length(d) - 1)

    def frames(self, port: str, request: FrameRequest) -> SampleFrame:
        d = self.decimation_for(request.dt_s)
        up = self.input_frames("in", self.upstream_request(port, request))
        # cycles per ADC sample: the oscillator counts the ADC's own clock, not seconds
        iq = ddc(up.data, float(self.param("nco_hz")) * self._adc_interval(), d, self.taps(d))
        return SampleFrame(
            iq,
            request.t0_ns,
            request.dt_s,
            IQ,
            center_hz=0.0 if request.tune_hz is None else request.tune_hz,
            clipped=up.clipped,
            notes=up.notes,
        )

    def sample_interval(self, port: str) -> float:
        fixed = int(self.param("decimation"))
        if fixed <= 0:
            raise ScpiError(-221, f"{self.uid}: the decimation is automatic; it runs at the rate asked of it")
        return fixed * self._adc_interval()

    def input_interval(self, port: str) -> float:
        return self._adc_interval()


@component("specan_rbw")
class SpecanRbw(Component):
    """The resolution filter (signal-chain.md 5.3): the resolution bandwidth — auto: span /
    ``auto_ratio``, snapped to the model's filter steps and held at or below ``auto_max``; manual
    otherwise — and the Gaussian filter of that bandwidth at -3 dB, as a windowed FFT.

    ``in`` takes complex baseband samples, ``out`` gives the complex amplitude at each frequency
    point of one transform (``bins``: the frame's ``center_hz`` is the tuned frequency its middle
    point stands for, ``df_hz`` the spacing). A request for ``count`` points ``dt_s`` apart in
    time reads the :func:`window_length` samples the window spans and pads the transform to
    ``count``; a steady tone reads its amplitude. The window is cut ``WINDOW_SIGMAS`` standard
    deviations each side, so its response is the Gaussian down to about -70 dB.

    The window gives both evaluations their filter (signal-chain.md 5.4): :meth:`response` is its
    frequency response per detector, in closed form, for the analytic trace; :meth:`frames` uses
    its coefficients. Its noise bandwidth is 1.0645 × RBW either way.

    The rate it asks of the down-converter follows the RBW: one transform is to cover
    ``fft_width`` resolution bandwidths (the usable middle half of twice that rate). The steps
    and limits are the model's (#616): the 1-3-10 ladder to 3 MHz by default, the X-Series "10%"
    steps to 8 MHz."""

    ports = {"in": PortSpec("in", "samples"), "out": PortSpec("out", "samples")}
    defaults = {
        "rbw": 3.0e6,  # Hz, manual RBW (a setting such as $bw.res)
        "rbw_auto": True,  # the RBW follows the span (a setting)
        "span": 7.5e9,  # Hz, the span the automatic RBW uses (a setting)
        "steps": "1-3-10",  # the filter steps: 1-3-10 (nearest on a log scale) | 10% (E24 to 3 MHz, then 4/5/6/8 MHz)
        "rbw_min": 1.0,  # Hz, the narrowest RBW
        "rbw_max": 3.0e6,  # Hz, the widest RBW a manual entry reaches
        "auto_ratio": 100.0,  # span / RBW the automatic coupling aims at (X-Series: 106)
        "auto_max": 3.0e6,  # Hz, the widest RBW the automatic coupling picks
        "window": "gaussian",  # the window, and so the filter's shape: gaussian
        "fft_width": 64.0,  # the width one transform is to cover, in resolution bandwidths
    }
    units = {"rbw_min": "Hz", "rbw_max": "Hz", "auto_max": "Hz"}
    choices = {"steps": STEPS, "window": ("gaussian",)}

    # ---- the bandwidth ---------------------------------------------------------------------------

    def _steps(self) -> str:
        return self.choice("steps") or "1-3-10"

    def snap(self, value: float, hi: float | None = None) -> float:
        top = float(self.param("rbw_max")) if hi is None else hi
        return snap_filter(value, self._steps(), float(self.param("rbw_min")), top)

    def rbw(self) -> float:
        if bool(self.param("rbw_auto")):
            ratio = float(self.param("auto_ratio"))
            top = min(float(self.param("auto_max")), float(self.param("rbw_max")))
            return self.snap(min(float(self.param("span")) / ratio, top), top)
        return float(self.param("rbw"))

    # ---- the filter, in closed form (the analytic evaluation) -----------------------------------

    def response(self, d: np.ndarray, rbw: float, bucket: float, detector: str) -> np.ndarray:
        return line_response(d, rbw, bucket, detector)

    def noise_floor_mw(self, att: float, nf: float) -> float:
        return 10 ** ((KT0_DBM_HZ + nf + att + 10 * math.log10(NBW_FACTOR * self.rbw())) / 10)

    # ---- the filter, as a transform (the sampled evaluation) ------------------------------------

    def interval(self) -> float:
        """The sample interval the transform runs at, s: what covers ``fft_width`` resolution
        bandwidths in the usable half of the rate, as near as the down-converter comes."""
        wanted = 1.0 / (2.0 * float(self.param("fft_width")) * self.rbw())
        block, port = _upstream(self, "in")
        if isinstance(block, SpecanDdc):
            converter: Any = block
            return float(converter.interval_near(wanted))
        return float(block.sample_interval(port))  # a source with a rate of its own

    def points(self, dt_s: float | None = None) -> int:
        """Frequency points of one transform: a power of two with ``BINS_PER_RBW`` points a
        resolution bandwidth or more."""
        dt = self.interval() if dt_s is None else dt_s
        return 1 << max(3, math.ceil(math.log2(BINS_PER_RBW / (self.rbw() * dt))))

    def upstream_request(self, port: str, request: FrameRequest) -> FrameRequest:
        """What a request for ``out`` asks of the down-converter: the samples the window spans."""
        count = window_length(self.rbw(), request.dt_s)
        if request.count < count:
            raise ScpiError(
                -221,
                f"{self.uid}: {request.count} frequency points, but the window of this RBW spans "
                f"{count} samples at {1.0 / request.dt_s:.6g} Sa/s",
            )
        return replace(request, count=count)

    def frames(self, port: str, request: FrameRequest) -> SampleFrame:
        iq = self.input_frames("in", self.upstream_request(port, request))
        window = gaussian_window(iq.count, self.rbw(), request.dt_s)
        return SampleFrame(
            windowed_fft(iq.data, window, request.count),
            request.t0_ns,
            request.dt_s,
            BINS,
            center_hz=iq.center_hz,
            df_hz=1.0 / (request.count * request.dt_s),
            clipped=iq.clipped,
            notes=iq.notes,
        )

    def sample_interval(self, port: str) -> float:
        return self.interval()

    def input_interval(self, port: str) -> float:
        return self.interval()

    @op("coupled_rbw")
    def op_coupled_rbw(self, query: bool = False, value: float | None = None, **_: Any) -> OpResult:
        if query:
            return OpResult(self.reply_number(self.rbw(), 10))
        assert value is not None
        if not float(self.param("rbw_min")) <= value <= float(self.param("rbw_max")):
            raise ScpiError(-222)
        assert self.owner is not None
        self.owner.set_state("bw.res", self.snap(value))
        self.owner.set_state("bw.res_auto", False)
        return OpResult()
