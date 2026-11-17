"""A scope's record, and how a deep one is computed only where it is read (#1012).

A record up to ``EAGER_POINTS`` points is computed whole when it is acquired, as one frame of the
channel's chain (ADC, then the decimator's acquisition mode; D67) — exactly as before deep records
existed. A deeper record is not: the acquisition fixes its parameters (sample rate, points, trigger
position, the random stream) and captures what the chain computes from (:class:`AdcView`,
:class:`DecimatorView`: the input signal, the noise, the quantization, the acquisition mode), and
the samples are computed when something reads them, ``TILE`` samples at a time:

* a waveform read computes the tiles it reads; a sample's value depends only on its tile, so any
  two reads of it agree (a tile is a pure function of the captured chain and its position);
* the screen and the internal probe read an envelope: per pixel, the minimum and maximum of a
  fixed number of samples drawn across the pixel (:meth:`Record.envelope`), whatever the depth;
* a measurement that needs every sample reads the record whole (at most ``MAX_FRAME`` points);
  beyond that, the ones that reduce the record chunk by chunk (extremes, mean, RMS) still answer.

Changing a setting after the acquisition does not change the record: the views are captured values,
not the live components. Tiles are kept up to ``TILE_CACHE_SAMPLES`` samples; a tile read again
after it was dropped is computed again, to the same values. docs/signal-chain.md 1.4,
docs/real-models.md "深存储是怎样仿真的".
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from functools import lru_cache, partial

import numpy as np

from ...engine.noise import gaussian, stream_key, uniform
from ...engine.signal import Signal
from ...engine.stream import (
    MAX_FRAME,
    MAX_REQUEST_FANOUT,
    FrameRequest,
    SampleFormat,
    SampleFrame,
    beyond_model,
)
from . import adc

#: Records up to this many points are computed whole when acquired; deeper ones where they are read.
EAGER_POINTS = 1 << 22
#: Samples in one tile of a deep record (the unit it is computed and kept in), at the fewest: a signal
#: of one or two lines. A signal of more lines takes longer tiles, so the tile is synthesized as one
#: matrix product (``Signal.sample_rel``'s fast path), up to ``TILE_MAX``.
TILE = 1 << 12
TILE_MAX = 1 << 20
#: Samples x lines from which ``Signal.sample_rel`` synthesizes a uniform grid as a matrix product.
_FAST_SYNTHESIS = 1_000_000
#: Samples of tiles one deep record keeps at most.
TILE_CACHE_SAMPLES = 1 << 22
#: Samples the envelope of one pixel is drawn from.
ENVELOPE_SAMPLES = 64
#: Converter samples an envelope may compute at most (an average of many acquisitions, a wide
#: high-resolution boxcar): beyond it the screen is refused, as a frame beyond the model is.
ENVELOPE_BUDGET = 1 << 22
#: Samples one chunk of a chunked measurement holds.
CHUNK = 1 << 20


def quantize(
    volts: np.ndarray, scale: float, offset: float, bits: int, per_div: float, add: bool
) -> np.ndarray:
    """What a scope's ADC keeps: the codes of ``bits``, back in volts (``bits`` 0: the volts)."""
    if bits <= 0:
        return volts
    off = offset if add else -offset
    codes = adc.codes(volts, scale, off, per_div, bits)
    return codes / per_div * scale - off


def boxcar(v: np.ndarray, w: int) -> np.ndarray:
    """The centered boxcar average of ``w`` samples; at the edges the window runs past the record
    and the edge sample is repeated to fill it (high resolution, F4/D46: white noise falls by
    sqrt(w), the bandwidth by the window)."""
    if w <= 1:
        return v
    return _window_mean(np.pad(v, (w // 2, w - 1 - w // 2), mode="edge"), w)


def _window_mean(padded: np.ndarray, w: int) -> np.ndarray:
    """The mean of every ``w`` consecutive samples of ``padded`` (``len - w + 1`` of them)."""
    c = np.concatenate(([0.0], np.cumsum(padded)))
    return (c[w:] - c[:-w]) / w


def peak_detect(samples: np.ndarray, m: int) -> np.ndarray:
    """Peak detect over intervals of ``m`` samples: of each interval's smallest and largest value,
    the one that departs farther from the interval's ends."""
    grid = samples.reshape(-1, m)
    ends = (grid[:, 0] + grid[:, -1]) / 2
    lo, hi = grid.min(axis=1), grid.max(axis=1)
    return np.where(np.abs(hi - ends) >= np.abs(lo - ends), hi, lo)


@dataclass(frozen=True, eq=False)
class AdcView:
    """What a ``scope_adc`` frame of one channel is computed from, as values: the frame is a pure
    function of this and the request (``ScopeAdc.frames`` computes through it, so the two cannot
    differ).

    In signal order (#1266): each sample instant moves by the sampling clock's aperture jitter
    (``aperture`` s rms, its own draw per sample); the front end's harmonic distortion bends the
    signal's lines (``hd``: the coefficients of x² and x³ on its AC part relative to half the
    converter's span, ``half`` V; its DC is calibrated out, so a DC level reads as before); an interleaved converter of ``ilv`` sub-converters on the
    ``native_dt`` grid gives each its own gain and offset (a cosine across the sub-converters,
    ``ilv_gain`` relative and ``ilv_offset`` of ``half``): images at k·fs/N ± f and spurs at
    k·fs/N, in a record at the converter's own rate (a slower one is decimated from all of them); then the input noise (``sigma``) and the quantization. ``scale`` is the converter's
    V/div — the displayed one, or the hardware range's below a magnified setting (#1265)."""

    signal: Signal  # the channel's signal at the converter's input, display volts
    skew: float  # the probe's delay, s
    sigma: float  # the input noise, display volts RMS
    seed: int
    scale: float
    offset: float
    bits: int
    per_div: float
    add: bool  # the offset setting is added to the volts (else subtracted)
    hd: tuple[float, float] = (0.0, 0.0)  # x², x³ coefficients of the front end's distortion
    half: float = 0.0  # half the converter's span, display volts (the distortion's and spurs' unit)
    ilv: int = 0  # interleaved sub-converters (0, 1: none)
    ilv_gain: float = 0.0  # their gain mismatch, relative (images at k·fs/N ± f, each ilv_gain/2)
    ilv_offset: float = 0.0  # their offset mismatch, of half (spurs at k·fs/N)
    native_dt: float = 0.0  # the interleaved converter's own interval, s
    aperture: float = 0.0  # the sampling clock's aperture jitter, s rms

    def quantize(self, volts: np.ndarray) -> np.ndarray:
        return quantize(volts, self.scale, self.offset, self.bits, self.per_div, self.add)

    def _volts(
        self,
        t0_ns: int,
        offsets: np.ndarray,
        indexes: np.ndarray,
        key: tuple[object, ...],
        dt: float | None = None,
    ) -> np.ndarray:
        """The converter's input at the sample instants ``t0_ns`` + ``offsets`` (s), before the
        quantization; ``indexes`` place the samples in the random streams of ``key``."""
        when = offsets - self.skew
        volts = self.signal.sample_rel(t0_ns, when)
        if self.aperture > 0 and self.signal.freq.size:
            # Each instant moves by its own draw; to first order the sample moves by the slope
            # times it (the error is (2πf·σ)², 1e-6 for 15 fs at 50 GHz), so the grid stays regular
            # and keeps the fast synthesis.
            slope = replace(self.signal, amp=self.signal.amp * (2j * math.pi * self.signal.freq), dc=0.0)
            shift = self.aperture * gaussian(stream_key(self.seed, *key, "aperture"), indexes)
            volts = volts + slope.sample_rel(t0_ns, when) * shift
        middle = -self.offset if self.add else self.offset
        if (self.hd[0] or self.hd[1]) and self.half > 0 and self.signal.freq.size:
            # On the signal's lines, not its DC: the scope's calibration takes the static error out,
            # so a DC level reads as before; x²'s own mean (the lines' power) is taken out with it.
            x = (volts - self.signal.dc) / self.half
            power = float(np.sum(np.abs(self.signal.amp) ** 2)) / 2 / self.half**2
            volts = volts + self.half * (self.hd[0] * (x * x - power) + self.hd[1] * x * x * x)
        # The sub-converters take turns on the converter's own grid; a slower record is decimated
        # from all of them (the hardware samples at its full rate), so it shows none of their pattern.
        native = dt is not None and self.native_dt > 0 and abs(dt / self.native_dt - 1.0) < 1e-6
        if self.ilv > 1 and native and (self.ilv_gain or self.ilv_offset):
            n = self.ilv
            # the sub-converter of each sample: its place on the converter's own grid, counted from
            # time zero (whole nanoseconds first, so a late instant keeps its precision)
            per_ns = 1e-9 / self.native_dt
            base = math.fmod(t0_ns * per_ns, n)
            sub = np.mod(np.round(base + offsets / self.native_dt), n)
            pattern = np.cos(2 * math.pi * sub / n)
            volts = (volts - middle) * (1.0 + self.ilv_gain * pattern) + middle
            volts = volts + self.ilv_offset * self.half * pattern
        if self.sigma > 0:
            volts = volts + self.sigma * gaussian(stream_key(self.seed, *key), indexes)
        return volts

    def frame(self, request: FrameRequest) -> SampleFrame:
        volts = self._volts(request.t0_ns, request.offsets_s(), request.indexes(), request.key, request.dt_s)
        kept = self.quantize(volts)
        clipped, sides = False, None
        if self.bits > 0:
            top, bottom = self.quantize(np.array([1e30, -1e30]))
            sides = (bool(np.min(kept) <= bottom), bool(np.max(kept) >= top))
            clipped = sides[0] or sides[1]
        fmt = SampleFormat("volts", bits=self.bits, lsb=self.scale / self.per_div if self.bits > 0 else 0.0)
        return SampleFrame(kept, request.t0_ns, request.dt_s, fmt, clipped=clipped, clip_sides=sides)

    def at(self, request: FrameRequest, positions: np.ndarray, shift: int = 0) -> np.ndarray:
        """The samples at ``positions`` (any integers) of the grid ``request`` starts, their noise
        ``shift`` further along the random stream: what :meth:`frame` gives there, computed
        sample by sample (the screen's envelope)."""
        off = (request.first + positions.astype(np.float64)) * request.dt_s
        index = request.index0 + positions.astype(np.int64) + shift
        return self.quantize(self._volts(request.t0_ns, off, index, request.key, request.dt_s))


#: Samples a digital filter's kernel reaches on either side at the most (its warm-up): a filter far
#: narrower than the sample rate is truncated there (FilterView).
FILTER_MAX_REACH = 1 << 20
#: Periods of the cutoff frequency the kernel reaches on either side.
FILTER_PERIODS = 32


@lru_cache(maxsize=8)
def filter_kernel(response: str, fc: float, dt: float) -> np.ndarray:
    """The FIR kernel of a scope's digital bandwidth filter (#1262): the impulse response of the
    zero-phase ``response`` (``scope_vertical.response``'s shapes) at ``fc``, sampled every ``dt``,
    reaching ``FILTER_PERIODS`` periods of ``fc`` on either side (at most ``FILTER_MAX_REACH``
    samples), shaped by a Blackman window and scaled to unit gain at 0 Hz. The brick wall's and the
    Gaussian's responses are closed forms (a sinc, a Gaussian); the Bessel's and the Butterworth's
    the inverse transform of their responses. The window trades the brick wall's edge for a
    transition about 0.2·fc wide (stop band below -70 dB); a filter whose reach is cut at
    ``FILTER_MAX_REACH`` has a wider edge, in proportion."""
    from ...engine.signal import bessel4, butterworth4

    reach = int(min(FILTER_MAX_REACH, max(8, math.ceil(FILTER_PERIODS / (fc * dt)))))
    k = np.arange(-reach, reach + 1, dtype=np.float64)
    if response == "brickwall":
        h = 2 * fc * dt * np.sinc(2 * fc * dt * k)
    elif response == "gaussian":
        # |H| = exp(-ln2/2 (f/fc)^2): h(t) ∝ exp(-(2π fc t)^2 / (2 ln2))
        h = np.exp(-((2 * math.pi * fc * dt * k) ** 2) / (2 * math.log(2.0)))
    else:
        shape = {"bessel4": bessel4, "butterworth4": butterworth4}[response]
        n = 1 << (4 * reach).bit_length()
        full = np.fft.ifft(shape(fc)(np.fft.fftfreq(n, dt))).real
        h = np.concatenate((full[-reach:], full[: reach + 1]))
    h = h * np.blackman(2 * reach + 1)
    total = float(h.sum())
    kernel = h / total if total else h
    kernel.flags.writeable = False
    return kernel


def _or_sides(a: tuple[bool, bool] | None, b: tuple[bool, bool] | None) -> tuple[bool, bool] | None:
    if a is None or b is None:
        return a if b is None else b
    return (a[0] or b[0], a[1] or b[1])


@dataclass(frozen=True)
class FilterView:
    """What a ``scope_dsp_filter`` frame of one channel is computed from (#1262): a zero-phase
    ``response`` at ``fc`` applied to the converter's samples, or nothing (``fc`` None: the filter
    passes the samples on unchanged, bit for bit). ``m``: converter samples per output sample the
    filter works on (a record slower than the converter is filtered at the converter's rate, then
    decimated, as the hardware's DSP does; at most the block's ``max_oversample``)."""

    response: str = "brickwall"
    fc: float | None = None
    m: int = 1

    @property
    def active(self) -> bool:
        return self.fc is not None

    def reach(self, dt: float) -> int:
        """The kernel's reach on either side at output interval ``dt``, in converter samples."""
        if self.fc is None:
            return 0
        return len(filter_kernel(self.response, self.fc, dt / self.m)) // 2

    def frame(self, request: FrameRequest, upstream: Upstream) -> SampleFrame:
        if self.fc is None:
            return upstream(request)
        m = self.m
        kernel = filter_kernel(self.response, self.fc, request.dt_s / m)
        w = len(kernel) // 2
        wide = replace(
            request,
            count=request.count * m + 2 * w,
            dt_s=request.dt_s / m,
            first=request.first * m - w,
            index0=request.index0 * m - w,
        )
        up = upstream(wide)
        size = up.count + kernel.size - 1
        nfft = 1 << (size - 1).bit_length()
        full = np.fft.irfft(np.fft.rfft(up.data, nfft) * np.fft.rfft(kernel, nfft), nfft)
        out = full[2 * w : 2 * w + request.count * m : m]
        return SampleFrame(
            out,
            request.t0_ns,
            request.dt_s,
            SampleFormat("volts"),
            clipped=up.clipped,
            clip_sides=up.clip_sides,
        )


@dataclass(frozen=True, eq=False)
class ChainView:
    """A channel's converter and the digital filters after it, captured (a deep record keeps it):
    frames and single samples computed through all of them, in order."""

    adc: AdcView
    filters: tuple[FilterView, ...] = ()

    @property
    def signal(self) -> Signal:
        return self.adc.signal

    @property
    def seed(self) -> int:
        return self.adc.seed

    @property
    def cost(self) -> int:
        """Converter samples one output sample takes (the filters' oversampling)."""
        return math.prod(f.m for f in self.filters if f.active)

    def frame(self, request: FrameRequest) -> SampleFrame:
        upstream: Upstream = self.adc.frame
        for flt in self.filters:
            upstream = partial(flt.frame, upstream=upstream)
        return upstream(request)

    def at(self, request: FrameRequest, positions: np.ndarray, shift: int = 0) -> np.ndarray:
        """The samples at ``positions``: one by one through the converter when no filter is on;
        through a filter, a frame around each run of nearby positions (a filtered sample needs its
        neighbours), the same values a frame there gives."""
        if not any(f.active for f in self.filters):
            return self.adc.at(request, positions, shift)
        out = np.empty(positions.size)
        if not positions.size:
            return out
        order = np.argsort(positions, kind="stable")
        sorted_ = positions[order]
        reach = sum(f.reach(request.dt_s) for f in self.filters) + 1
        breaks = np.flatnonzero(np.diff(sorted_) > 2 * reach) + 1
        for lo, hi in zip(
            np.concatenate(([0], breaks)), np.concatenate((breaks, [sorted_.size])), strict=True
        ):
            a, b = int(sorted_[lo]), int(sorted_[hi - 1])
            part = replace(
                request, first=request.first + a, count=b - a + 1, index0=request.index0 + a + shift
            )
            out[order[lo:hi]] = self.frame(part).data[sorted_[lo:hi] - a]
        return out


Upstream = Callable[[FrameRequest], SampleFrame]


@dataclass(frozen=True)
class DecimatorView:
    """What a ``scope_decimator`` frame is computed from: the acquisition mode (``proc``), the
    converter samples per record sample (``m``, peak detect), the acquisitions an average takes
    (``count``) and the high-resolution boxcar (``w``)."""

    proc: str
    m: int
    count: int
    w: int

    @property
    def cost(self) -> int:
        """Converter samples one record sample takes."""
        return self.m * self.count

    def native(self, request: FrameRequest) -> FrameRequest:
        """The request on the converter's grid behind ``request``."""
        m = self.m
        return replace(request, first=request.first * m, count=request.count * m, dt_s=request.dt_s / m)

    def frame(
        self,
        request: FrameRequest,
        upstream: Upstream,
        reserve: Callable[[int], None] | None = None,
        part: int = 0,
        whole: int | None = None,
    ) -> SampleFrame:
        """The record-rate frame for ``request``. ``part``/``whole``: the request is samples
        ``part`` on of a record of ``whole`` samples (a deep record's tile): an average's later
        acquisitions continue the random stream after the whole record, and the boxcar sees the
        record's edges, not the tile's."""
        native = self.native(request)
        stride = native.count if whole is None else whole * self.m
        if self.count > 1 and reserve is not None:
            # Every acquisition of the average is one more request of the same read: one that would
            # go beyond the model is refused here, before any of them is computed (#986).
            reserve(self.count * native.count)
        if self.proc == "eres" and whole is not None and self.w > 1:
            return self._boxcar_part(request, native, upstream, part, whole)
        frame = upstream(native)
        volts, clipped, sides = frame.data, frame.clipped, frame.clip_sides
        if self.m > 1:
            volts = peak_detect(volts, self.m)
        elif self.proc == "average":
            for k in range(1, self.count):
                more = upstream(replace(native, index0=native.index0 + k * stride))
                volts, clipped = volts + more.data, clipped or more.clipped
                sides = _or_sides(sides, more.clip_sides)
            volts = volts / self.count
        elif self.proc == "eres":
            volts = boxcar(volts, self.w)
        return SampleFrame(volts, request.t0_ns, request.dt_s, frame.fmt, clipped=clipped, clip_sides=sides)

    def _boxcar_part(
        self, request: FrameRequest, native: FrameRequest, upstream: Upstream, part: int, whole: int
    ) -> SampleFrame:
        """A tile of a high-resolution record: its samples and the boxcar's reach on either side
        (the record's edge sample repeated where the reach runs past the record)."""
        w, n = self.w, request.count
        left, right = w // 2, w - 1 - w // 2
        lo, hi = max(0, part - left), min(whole, part + n + right)
        wide = replace(
            native, first=native.first - (part - lo), count=hi - lo, index0=native.index0 - (part - lo)
        )
        frame = upstream(wide)
        padded = np.pad(frame.data, (left - (part - lo), right - (hi - part - n)), mode="edge")
        return SampleFrame(
            _window_mean(padded, w),
            request.t0_ns,
            request.dt_s,
            frame.fmt,
            clipped=frame.clipped,
            clip_sides=frame.clip_sides,
        )

    def at(
        self, adc_view: AdcView | ChainView, request: FrameRequest, positions: np.ndarray, whole: int
    ) -> np.ndarray:
        """The record samples at ``positions`` (record indices) of the record ``request`` asks for
        whole (``whole`` points), each computed on its own."""
        native = self.native(request)
        m = self.m
        if m > 1:
            grid = (positions[:, None] * m + np.arange(m)[None, :]).reshape(-1)
            return peak_detect(adc_view.at(native, grid), m)
        if self.proc == "average" and self.count > 1:
            volts = adc_view.at(native, positions)
            for k in range(1, self.count):
                volts = volts + adc_view.at(native, positions, k * whole)
            return volts / self.count
        if self.proc == "eres" and self.w > 1:
            w = self.w
            near = np.clip(positions[:, None] + np.arange(w)[None, :] - w // 2, 0, whole - 1)
            return adc_view.at(native, near.reshape(-1)).reshape(-1, w).sum(axis=1) / w
        return adc_view.at(native, positions)


def tile_size(lines: int, cost: int) -> int:
    """The tile of a deep record whose signal has ``lines`` spectral lines and whose record sample
    takes ``cost`` converter samples: ``TILE``, longer for a signal of many lines (one matrix
    product per tile instead of one exponential per line and sample), and short enough that a
    tile's converter samples stay within the model's fan-out."""
    tile = TILE
    if lines > 2:
        while tile * lines <= _FAST_SYNTHESIS and tile < TILE_MAX:
            tile *= 2
    return max(1, min(tile, MAX_REQUEST_FANOUT // max(1, cost)))


class Points:
    """The indices of the points a read sends (a waveform transfer's selection): a range, so a
    read of a few points of a deep record never lists the record's indices."""

    def __init__(self, r: range) -> None:
        self.range = r

    @property
    def size(self) -> int:
        return len(self.range)

    def __len__(self) -> int:
        return len(self.range)

    def __getitem__(self, key: int) -> int:
        return self.range[key]

    def array(self) -> np.ndarray:
        r = self.range
        return np.arange(r.start, r.stop, r.step, dtype=np.int64)

    def tolist(self) -> list[int]:
        return list(self.range)


class DeepRecord:
    """The samples of a record deeper than ``EAGER_POINTS``, computed tile by tile where read.

    ``request`` is what the acquisition asked the channel's chain for (the whole record); ``adc``,
    the ``filters`` after it (#1262) and ``dec`` are the chain, captured when the record was
    acquired; ``correct`` the calibration's (gain, offset), if any."""

    def __init__(
        self,
        points: int,
        request: FrameRequest,
        adc_view: AdcView,
        dec: DecimatorView,
        correct: tuple[float, float] | None,
        filters: tuple[FilterView, ...] = (),
    ) -> None:
        self.points = points
        self.request = request
        self.adc = adc_view
        self.chain = ChainView(adc_view, filters)
        self.dec = dec
        self.correct = correct
        #: Converter samples one record sample takes along the whole chain.
        self.cost = dec.cost * self.chain.cost
        self.tile = tile_size(adc_view.signal.freq.size, self.cost)
        self._tiles: OrderedDict[int, np.ndarray] = OrderedDict()
        self._whole: np.ndarray | None = None

    def _corrected(self, volts: np.ndarray) -> np.ndarray:
        if self.correct is None:
            return volts
        gain, offset = self.correct
        return (volts - offset) * gain

    def _tile(self, t: int) -> np.ndarray:
        hit = self._tiles.get(t)
        if hit is not None:
            self._tiles.move_to_end(t)
            return hit
        a = t * self.tile
        n = min(self.tile, self.points - a)
        req = replace(
            self.request, first=self.request.first + a, count=n, index0=self.request.index0 + a * self.dec.m
        )
        values = self._corrected(self.dec.frame(req, self.chain.frame, part=a, whole=self.points).data)
        values.flags.writeable = False
        self._tiles[t] = values
        while len(self._tiles) * self.tile > TILE_CACHE_SAMPLES and len(self._tiles) > 1:
            self._tiles.popitem(last=False)
        return values

    def _charge(self, points: int, tiles: int) -> None:
        """Refuse, before any of it is computed, a read of more than ``MAX_FRAME`` points (what a
        read holds in memory) or whose ``tiles`` take more converter samples along the chain than
        the model's fan-out (``MAX_REQUEST_FANOUT``, as one frame's read is): read it in parts."""
        if points > MAX_FRAME:
            raise beyond_model(
                f"a read of {points} points at once (at most {MAX_FRAME}); read the record in parts"
            )
        total = tiles * self.tile * self.cost
        if total > MAX_REQUEST_FANOUT:
            raise beyond_model(
                f"this read of the record asks for {total} samples along the chain "
                f"(at most {MAX_REQUEST_FANOUT}); read fewer points at a time"
            )

    def _new(self, tiles: Iterable[int]) -> int:
        return sum(1 for t in tiles if t not in self._tiles)

    def span(self, a: int, b: int) -> np.ndarray:
        """Samples ``a`` to ``b`` (excluded)."""
        a, b = max(0, a), min(self.points, b)
        if b <= a:
            return np.zeros(0)
        if self._whole is not None:
            return self._whole[a:b]
        first, last = a // self.tile, (b - 1) // self.tile
        self._charge(b - a, self._new(range(first, last + 1)))
        parts = [self._tile(t) for t in range(first, last + 1)]
        joined = parts[0] if len(parts) == 1 else np.concatenate(parts)
        return joined[a - first * self.tile : b - first * self.tile]

    def take(self, index: np.ndarray | Points) -> np.ndarray:
        """The samples at ``index`` (ascending record indices, or the points of a read)."""
        if isinstance(index, Points):
            r = index.range
            if r.step == 1 or not r:
                return self.span(r.start, r.start + len(r))
            # every tile a step of the read lands in: one per point when the step spans a tile
            tiles = len(r) if r.step >= self.tile else (r[-1] // self.tile - r[0] // self.tile + 1)
            self._charge(len(r), tiles)
            index = index.array()
        if self._whole is not None:
            return self._whole[index]
        out = np.empty(index.size)
        if not index.size:
            return out
        tiles_of = index // self.tile
        bounds = np.flatnonzero(np.diff(tiles_of)) + 1
        starts = np.concatenate(([0], bounds))
        self._charge(index.size, self._new(int(t) for t in tiles_of[starts]))
        for lo, hi in zip(starts, np.concatenate((bounds, [index.size])), strict=True):
            t = int(tiles_of[lo])
            out[lo:hi] = self._tile(t)[index[lo:hi] - t * self.tile]
        return out

    def whole(self) -> np.ndarray:
        """Every sample, in memory: at most ``MAX_FRAME`` points. Kept once read."""
        if self._whole is None:
            if self.points > MAX_FRAME:
                raise beyond_model(
                    f"this needs the whole record of {self.points} points in memory (at most {MAX_FRAME}); "
                    "read it in parts"
                )
            whole = self.span(0, self.points)
            whole.flags.writeable = False
            self._whole = whole
            self._tiles.clear()
        return self._whole

    def at(self, positions: np.ndarray) -> np.ndarray:
        """Samples at ``positions``, each computed on its own (the envelope's draws)."""
        return self._corrected(self.dec.at(self.chain, self.request, positions, self.points))


class Record:
    """One channel's record: the samples (display volts, the probe ratio applied) and where they sit.

    ``volts`` gives a record computed whole; ``deep`` one computed where it is read (#1012). Read
    the samples through :attr:`points`, :meth:`span`, :meth:`take`, :meth:`chunks` and
    :meth:`envelope`; :attr:`volts` holds them all in memory (refused beyond ``MAX_FRAME``)."""

    def __init__(
        self,
        channel: int,
        t_start_ns: int,
        dt_s: float,
        trigger_index: int,
        scale: float,
        offset: float,
        triggered: bool,
        volts: np.ndarray | None = None,
        x_origin_s: float | None = None,
        trigger_ns: int | None = None,
        deep: DeepRecord | None = None,
    ) -> None:
        if (volts is None) == (deep is None):
            raise ValueError("a record has either its volts or a deep record's samples")
        self.channel = channel
        self.t_start_ns = t_start_ns  # instant of sample 0
        self.dt_s = dt_s
        self.trigger_index = trigger_index
        self.scale = scale
        self.offset = offset
        self.triggered = triggered
        #: Time of sample 0 relative to the trigger as the scope measured it, s: -trigger_index * dt
        #: plus the trigger's fraction of a sample and its jitter (G5). None: -trigger_index * dt.
        self.x_origin_s = x_origin_s
        self.trigger_ns = trigger_ns  # the trigger's instant (holdoff); None: untriggered
        self._volts = volts
        self.deep = deep
        #: V/div one ADC code step is a ``codes_per_div``-th of: the scale, or the hardware range's
        #: below a magnified division (#1265); the waveform transfer's codes and Y increment use it.
        self.code_scale = scale
        #: Which ends of the converter's range the acquisition touched, 1 low | 2 high, as the
        #: converter saw them (a digital filter after it moves the samples off the codes, #1262);
        #: None: read it from the samples.
        self.clip: int | None = None

    @property
    def origin(self) -> float:
        """Time of sample 0 relative to the trigger, s."""
        return self.x_origin_s if self.x_origin_s is not None else -self.trigger_index * self.dt_s

    @property
    def points(self) -> int:
        if self.deep is not None:
            return self.deep.points
        assert self._volts is not None
        return int(self._volts.size)

    @property
    def volts(self) -> np.ndarray:
        """Every sample, in memory (a deep record: refused beyond ``MAX_FRAME`` points)."""
        if self.deep is not None:
            return self.deep.whole()
        assert self._volts is not None
        return self._volts

    def span(self, a: int, b: int) -> np.ndarray:
        """Samples ``a`` to ``b`` (excluded)."""
        return self.deep.span(a, b) if self.deep is not None else self.volts[max(0, a) : max(0, b)]

    def take(self, index: np.ndarray | Points) -> np.ndarray:
        """The samples at ``index`` (ascending record indices, or the points of a read)."""
        if self.deep is not None:
            return self.deep.take(index)
        if isinstance(index, Points):
            r = index.range
            return self.volts[r.start : r.start + len(r) * r.step : r.step] if r else np.zeros(0)
        return self.volts[index]

    def chunks(self, a: int = 0, b: int | None = None) -> Iterator[np.ndarray]:
        """Samples ``a`` to ``b`` in chunks of at most ``CHUNK`` (fewer where a sample costs many
        converter samples, so each chunk stays within the model's fan-out): what is held in memory
        at once is bounded, the whole range is read."""
        b = self.points if b is None else b
        size = CHUNK
        if self.deep is not None:
            tile = self.deep.tile
            size = max(tile, (min(CHUNK, MAX_REQUEST_FANOUT // max(1, self.deep.cost)) // tile) * tile)
        for lo in range(a, b, size):
            yield self.span(lo, min(b, lo + size))

    def envelope(self, bins: int, key: tuple[object, ...] = ()) -> list[float]:
        """Per-bin minimum and maximum (``bins`` near-equal parts, flattened min, max, min, ...).
        A record computed whole (or a deep one cheap to compute whole) gives every sample's; a
        deep one gives those of up to ``ENVELOPE_SAMPLES`` samples drawn across each bin, the draws a
        pure function of ``key`` and the record (the screen does not compute the whole record)."""
        n = self.points
        edges = np.linspace(0, n, bins + 1, dtype=int)
        exact = self.deep is None or n * self.deep.cost <= ENVELOPE_BUDGET
        if self.deep is not None and not exact:
            dec = self.deep.dec
            # draws per bin: fewer where one draw costs many converter samples, at least one
            per = self.deep.cost * max(1, dec.w)
            k = max(1, min(ENVELOPE_SAMPLES, ENVELOPE_BUDGET // max(1, bins * per)))
            cost = bins * k * per
            if cost > ENVELOPE_BUDGET:
                raise beyond_model(
                    f"a screen of this record computes {cost} samples (at most {ENVELOPE_BUDGET})"
                )
        if exact:
            volts = self.volts if self.deep is None else self.span(0, n)
            chunks = (volts[edges[i] : edges[i + 1]] for i in range(bins))
            pairs = [(float(c.min()), float(c.max())) for c in chunks if c.size]
            return [v for pair in pairs for v in pair]
        deep = self.deep
        assert deep is not None
        u = uniform(stream_key(deep.adc.seed, "envelope", *key), np.arange(bins * k)).reshape(bins, k)
        lo, width = edges[:-1, None], (edges[1:] - edges[:-1])[:, None]
        positions = np.minimum(lo + np.floor(u * width).astype(np.int64), edges[1:, None] - 1)
        values = deep.at(positions.reshape(-1)).reshape(bins, k)
        return np.stack((values.min(axis=1), values.max(axis=1)), axis=1).reshape(-1).tolist()


def chunked_value(what: str, rec: Record, lo: int, hi: int) -> float:
    """A measurement that reduces the record chunk by chunk, on samples ``lo`` to ``hi``: VMAX, VMIN,
    VPP, VAVerage, VRMS, XMAX, XMIN (the index within the gate times dt, from the record's origin, as
    the measurement on a record in memory counts it). NaN on no samples."""
    count, total, squares = 0, 0.0, 0.0
    vmax, vmin, kmax, kmin = -math.inf, math.inf, -1, -1
    at = 0
    for chunk in rec.chunks(lo, hi):
        if chunk.size:
            i, j = int(np.argmax(chunk)), int(np.argmin(chunk))
            if chunk[i] > vmax:
                vmax, kmax = float(chunk[i]), at + i
            if chunk[j] < vmin:
                vmin, kmin = float(chunk[j]), at + j
            total += float(chunk.sum())
            squares += float(np.dot(chunk, chunk))
            count += chunk.size
        at += chunk.size
    if not count:
        return math.nan
    values = {
        "VMAX": vmax,
        "VMIN": vmin,
        "VPP": vmax - vmin,
        "VAVerage": total / count,
        "VRMS": math.sqrt(squares / count),
        "XMAX": rec.origin + kmax * rec.dt_s,
        "XMIN": rec.origin + kmin * rec.dt_s,
    }
    return values[what]


#: The measurements :func:`chunked_value` answers on a record too deep to hold in memory.
CHUNKED_MEASUREMENTS = frozenset({"VMAX", "VMIN", "VPP", "VAVerage", "VRMS", "XMAX", "XMIN"})
