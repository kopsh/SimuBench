"""The math functions of an oscilloscope (#1074): point-by-point operators, the FFT and its
measurements, over the records the acquisition keeps.

``scope_math`` holds a scope's math functions (``FUNCtion1..N``). A function is defined by its
operator and operands — channels, other functions or a constant — kept in the settings the
definition declares under one prefix per function (``function1.operator``, ``function1.source1``,
…), and its waveform is computed from the source records when it is read: a pure function of the
settings and the records, nothing kept between reads (D67). The operator vocabulary and behaviour
follow the Infiniium Programmer's Guide ch. 22 (``:FUNCtion<F>``):

* point-wise: ADD, SUBTract, MULTiply, DIVide, COMMonmode, ABSolute, INVert, SQUare, SQRT, MAGNify
  (a copy under its own scale), DELay (an X-origin shift) and GATing (a horizontal window);
* windowed: DIFFerentiate (central differences), INTegrate (cumulative trapezoid), AVERage and
  SMOoth (a centred running mean), MAXimum and MINimum (the extreme of each time bucket);
* filters: HIGHpass (single-pole) and LOWPass (a 4th-order Bessel-Thompson) — both computed over
  the whole source record, so a deep one is refused beyond the model rather than filtered tile by
  tile;
* transforms: FFTMagnitude, FFTPhase and PSDensity — the windowed FFT of the Infiniium set
  (RECTangular, HANNing, HAMMing, BHARris, FLATtop), as the one-sided magnitude in dBm/dBmV/dBµV/
  Watt/Vrms, phase in degrees, or power spectral density in dBm/Hz, Watt/Hz and V/√Hz. The
  transform's transform is the "effective memory depth" the manual names — the highest power of
  two not above the record — capped at ``fft_max_points``, and what cannot be met is refused,
  never silently truncated (#1101's rule). The windowed transform itself is
  ``library/components/specan_backend.py``'s convention (a coherent tone reads its own amplitude
  whatever the window); the five windows are this module's.

The FFT measurements of ``:MEASure:FFT`` (peak frequency and magnitude, deltas between peaks,
channel power, power spectral density, occupied bandwidth, total harmonic distortion) and the
annotated peak search (``:FUNCtion<F>:FFT:PEAK``) are answered from the same trace.

What this block does not model (the coverage tables note it per model): the amplitude
demodulation (ADEMod), the measurement trend and histogram functions (MTRend, MLOG, MHIStogram),
pattern averaging (PAVerage), the X-Y displays (VERSus, XYZ, XYZI) and the user-defined MATLAB
operators.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine.component import Acquisition, Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope_record import CHUNK, Points
from .specan_backend import noise_bandwidth

#: Window coefficients by SCPI word: a0 − Σ ai·cos(2πik/(N−1)) — Infiniium's five
#: (:FUNCtion<F>:FFT:WINDow); the flat top is the standard 5-term cosine sum.
WINDOWS: dict[str, tuple[float, ...]] = {
    "RECTangular": (1.0,),
    "HANNing": (0.5, 0.5),
    "HAMMing": (0.54, 0.46),
    "BHARris": (0.35875, 0.48829, 0.14128, 0.01168),
    "FLATtop": (0.21557895, 0.41663158, 0.277263158, 0.083578947, 0.006947368),
}
#: The windows by their upper-case word: a setting may hold the definition's own spelling.
WINDOW_WORDS = {word.upper(): word for word in WINDOWS}

#: The FFT vertical units (:FUNCtion<F>:FFT:VUNits): the magnitude family (dBm as DB, dBmV, dBµV,
#: Watt, Vrms) and the power-spectral-density family (dBm/Hz, Watt/Hz, V/√Hz).
VUNITS = ("DB", "DBMV", "DBUV", "WATT", "VRMS", "DBMHZ", "WATTHZ", "VRTHZ")

#: The transform operators (whole-record); everything else is computed span by span.
FFT_OPERATORS = frozenset({"FFTMagnitude", "FFTPhase", "PSDensity"})
#: The whole-record operators: the integral and the two filters need every source sample.
WHOLE_OPERATORS = FFT_OPERATORS | {"INTegrate", "HIGHpass", "LOWPass"}
#: The operators of one operand; the rest take two.
UNARY = (
    frozenset(
        {
            "ABSolute",
            "INVert",
            "SQUare",
            "SQRT",
            "MAGNify",
            "DELay",
            "GATing",
            "DIFF",
            "INTegrate",
            "SMOoth",
            "AVERage",
            "HIGHpass",
            "LOWPass",
            "MAXimum",
            "MINimum",
        }
    )
    | FFT_OPERATORS
)

#: Fewest samples a transform is taken of; a shorter record has none this model answers.
MIN_FFT_POINTS = 8

#: The operators whose horizontal scaling follows the source's timebase: the manual's list of the
#: functions :FUNCtion<F>:HORizontal:POSition/:RANGe refuse with -221.
_TIMEBASE_BOUND = {
    "ABSOLUTE",
    "ADD",
    "ADEMOD",
    "COMMONMODE",
    "DIFF",
    "DIVIDE",
    "HIGHPASS",
    "INTEGRATE",
    "INVERT",
    "LOWPASS",
    "MAXIMUM",
    "MINIMUM",
    "MULTIPLY",
    "SMOOTH",
    "AVERAGE",
    "SQUARE",
    "SQRT",
    "SUBTRACT",
}

#: The operator words as this block spells them, by their upper-case form (a setting holds the
#: definition's own spelling — INVert, SUBTract — and the block compares one form).
OPERATORS = (
    "ADD",
    "SUBTract",
    "MULTiply",
    "DIVide",
    "COMMonmode",
    "ABSolute",
    "INVert",
    "SQUare",
    "SQRT",
    "MAGNify",
    "DELay",
    "GATing",
    "DIFF",
    "INTegrate",
    "SMOoth",
    "AVERage",
    "HIGHpass",
    "LOWPass",
    "MAXimum",
    "MINimum",
    "FFTMagnitude",
    "FFTPhase",
    "PSDensity",
)
CANONICAL: dict[str, str] = {word.upper(): word for word in OPERATORS}


# ---- the point-wise operators (pure) ---------------------------------------------------------------


def running_mean(v: np.ndarray, w: int) -> np.ndarray:
    """The mean of every ``w`` samples centred on each point (edges clamped): SMOoth and AVERage."""
    if w <= 1:
        return v
    c = np.concatenate(([0.0], np.cumsum(v, dtype=np.float64)))
    k = np.arange(v.size)
    half = w // 2
    hi = np.clip(k + half + 1, 0, v.size)  # the window's end (excluded)
    lo = np.clip(k - (w - 1 - half), 0, v.size)  # its start
    return (c[hi] - c[lo]) / (hi - lo)


def derivative(v: np.ndarray, dt: float) -> np.ndarray:
    """The discrete derivative: central differences inside, one-sided at the ends."""
    return np.gradient(v, dt)


def integral(v: np.ndarray, dt: float) -> np.ndarray:
    """The cumulative trapezoid integral, in volt-seconds from the first sample."""
    return np.concatenate(([0.0], np.cumsum((v[1:] + v[:-1]) * 0.5 * dt)))


def _bessel4_sections() -> list[tuple[float, float]]:
    """The continuous 4th-order Bessel-Thompson filter as two second-order sections
    ``(a1, a2)`` — each a2/(s² + a1·s + a2), a unit DC gain whose product is the filter's —
    scaled so the filter's −3 dB point is 1 Hz (the n = 4 Bessel polynomial's own is not)."""
    poly = np.array([1.0, 10.0, 45.0, 105.0, 105.0])
    poles = np.roots(poly)
    w = 1.0  # walk |H(jw)| = 105/|poly(jw)| to 1/√2, then carry the poles to that frequency
    for _ in range(80):
        h = 105.0 / abs(np.polyval(poly, 1j * w))
        w *= 1.0 + (h / 0.70710678 - 1.0) * 0.5
    # Poles at ω = w carry the −3 dB point to 1 Hz when multiplied by 2π/w: the sections below
    # are those poles', so their own −3 dB is 1 Hz and s → s/bw moves it to the bandwidth set.
    scale = w / (2.0 * math.pi)
    sections: list[tuple[float, float]] = []
    for pair in (poles[:2], poles[2:]):
        sections.append(
            (float(-(pair[0] + pair[1]).real / scale), float((pair[0] * pair[1]).real / scale**2))
        )
    return sections


BESSEL4_SECTIONS = _bessel4_sections()


def lowpass(v: np.ndarray, dt: float, bw: float) -> np.ndarray:
    """A 4th-order Bessel-Thompson low pass of ``bw`` Hz (−3 dB): each continuous section
    a2/(s²+a1·s+a2) of the 1 Hz filter carried to ``bw`` (s → s/bw) and bilinear-transformed
    (Tustin, ``k = 2/dt``) — :FUNCtion<F>:LOWPass."""
    out = v.astype(np.float64, copy=True)
    k = 2.0 / dt
    for a1, a2 in BESSEL4_SECTIONS:
        b1, b2 = a1 * bw, a2 * bw * bw
        d0 = k * k + b1 * k + b2
        d1 = 2.0 * b2 - 2.0 * k * k
        d2 = k * k - b1 * k + b2
        x1 = x2 = y1 = y2 = 0.0
        for i in range(out.size):
            x0 = float(out[i])
            y0 = (b2 * (x0 + 2.0 * x1 + x2) - d1 * y1 - d2 * y2) / d0
            out[i] = y0
            x1, x2, y1, y2 = x0, x1, y0, y1
    return out


def highpass(v: np.ndarray, dt: float, bw: float) -> np.ndarray:
    """A single-pole high pass of ``bw`` Hz (−3 dB): :FUNCtion<F>:HIGHpass."""
    alpha = 1.0 / (1.0 + 2.0 * math.pi * bw * dt)  # RC/(RC+dt)
    out = np.empty_like(v)
    y = 0.0
    previous = 0.0
    for i in range(v.size):
        x = float(v[i])
        y = alpha * (y + x - previous)
        out[i] = y
        previous = x
    return out


def bucket_extreme(v: np.ndarray, m: int, most: bool) -> np.ndarray:
    """The maximum (``most``) or minimum of each run of ``m`` samples — MAXimum and MINimum, the
    extremes of each time bucket; the record shrinks ``m`` times. ``m`` 1 is the operand as it is."""
    if m <= 1:
        return v
    n = v.size // m * m
    groups = v[:n].reshape(-1, m)
    return groups.max(axis=1) if most else groups.min(axis=1)


def pointwise(kind: str, a: np.ndarray, b: np.ndarray | None = None) -> np.ndarray:
    """One point-wise operator over aligned arrays (``b`` None: unary): ADD, SUBTract, MULTiply,
    DIVide (a hole — NaN — where the divisor is 0), COMMonmode, ABSolute, INVert, SQUare, SQRT
    (NaN where negative) and MAGNify."""
    if kind == "ADD":
        return a + b
    if kind == "SUBTract":
        return a - b
    if kind == "MULTiply":
        return a * b
    if kind == "DIVide":
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(b != 0.0, a / np.where(b != 0.0, b, 1.0), np.nan)
    if kind == "COMMonmode":
        return (a + b) / 2.0
    if kind == "ABSolute":
        return np.abs(a)
    if kind == "INVert":
        return -a
    if kind == "SQUare":
        return a * a
    if kind == "SQRT":
        with np.errstate(invalid="ignore"):
            return np.where(a >= 0.0, np.sqrt(np.abs(a)), np.nan)
    if kind == "MAGNify":
        return a
    raise ValueError(f"no point-wise operator {kind!r}")


# ---- the transform (pure) ---------------------------------------------------------------------------


def window(kind: str, n: int) -> np.ndarray:
    """The window's coefficients — ``kind`` as the definition spells it or upper-case."""
    coeffs = WINDOWS[WINDOW_WORDS.get(str(kind).upper(), str(kind))]
    if len(coeffs) == 1:
        return np.ones(n)
    k = np.arange(n)
    w = np.full(n, coeffs[0])
    for i, a in enumerate(coeffs[1:], start=1):
        w -= a * np.cos(2.0 * np.pi * i * k / (n - 1))
    return w


def one_sided_fft(v: np.ndarray, w: np.ndarray) -> np.ndarray:
    """The complex amplitude at 0, df, 2·df … Nyquist of the windowed samples: the two-sided
    transform folded onto the positive frequencies, so a coherent tone of peak amplitude A reads
    A at its bin whatever the window (DC and Nyquist, which have no mirror, are not doubled)."""
    spectrum = np.fft.rfft(v * w) / float(np.sum(w))
    doubled = spectrum * 2.0
    doubled[0] = spectrum[0]
    if v.size % 2 == 0 and doubled.size > 1:
        doubled[-1] = spectrum[-1]
    return doubled


def magnitude(x: np.ndarray, unit: str, impedance: float) -> np.ndarray:
    """The one-sided transform in a vertical unit: Vrms and its decibels (dBmV, dBµV), a power
    unit (dBm referenced to ``impedance``, Watt)."""
    rms = np.abs(x) / math.sqrt(2.0)
    if unit == "VRMS":
        return rms
    if unit == "WATT":
        return rms * rms / impedance
    if unit in ("DB", "DBMHZ"):
        with np.errstate(divide="ignore", invalid="ignore"):
            return 10.0 * np.log10(np.where(rms > 0, rms * rms / impedance / 1e-3, np.nan))
    scale = {"DBMV": 60.0, "DBUV": 120.0}[unit]
    with np.errstate(divide="ignore", invalid="ignore"):
        return 20.0 * np.log10(np.where(rms > 0, rms, np.nan)) + scale


def as_density(values: np.ndarray, unit: str, enbw_hz: float) -> np.ndarray:
    """A magnitude trace as its per-Hz density: the bin's value spread over the window's
    equivalent noise bandwidth — dBm/Hz, Watt/Hz, or the amplitude density V/√Hz."""
    if unit == "WATTHZ":
        return values / enbw_hz
    if unit == "VRTHZ":
        return values / math.sqrt(enbw_hz)
    return values - 10.0 * np.log10(enbw_hz)  # DBMHZ


def phase(x: np.ndarray, t_delay_s: float, freqs: np.ndarray) -> np.ndarray:
    """The transform's phase in degrees, turned by the time delay an FFTPhase function sets."""
    return np.degrees(np.angle(x)) + 360.0 * freqs * t_delay_s


def find_peaks(freqs: np.ndarray, values: np.ndarray, level: float) -> list[int]:
    """Indices of the trace's local maxima, by rising frequency."""
    if values.size < 3:
        return []
    rising = (values[1:-1] >= values[:-2]) & (values[1:-1] >= values[2:]) & (values[1:-1] > level)
    return [int(i) + 1 for i in np.flatnonzero(rising)]


def occupied_bandwidth(freqs: np.ndarray, power: np.ndarray, pct: float) -> float:
    """The narrowest band of bins that holds ``pct`` percent of the trace's power, as a bandwidth
    in Hz — the occupied-bandwidth measurement, a two-pointer walk over the cumulative sum."""
    total = float(np.sum(power))
    if total <= 0.0 or not 0.0 < pct < 100.0:
        return math.nan
    c = np.concatenate(([0.0], np.cumsum(power)))
    want = total * pct / 100.0
    best_lo, best_hi, hi = 0, len(power) - 1, 0
    for lo in range(len(power)):
        if hi < lo:
            hi = lo
        while hi < len(power) and c[hi + 1] - c[lo] < want:
            hi += 1
        if hi < len(power) and c[hi + 1] - c[lo] >= want and hi - lo < best_hi - best_lo:
            best_lo, best_hi = lo, hi
        if hi >= len(power):
            break
    return float(freqs[best_hi] - freqs[best_lo])


def band_power(power: np.ndarray, freqs: np.ndarray, center: float, width: float) -> float:
    """The summed power of the bins within ``width`` of ``center``, in the trace's power unit."""
    inside = np.abs(freqs - center) <= width
    return float(np.sum(power[inside])) if np.any(inside) else 0.0


def bin_power(values: np.ndarray, unit: str, impedance: float, df: float) -> np.ndarray:
    """A trace's power per bin in Watt, whatever its unit: the magnitude units hold the bin's own
    power, the density units hold power per Hz (times the bin's width)."""
    if unit in ("DB", "DBMHZ"):
        p = np.power(10.0, values / 10.0) * 1e-3  # W (DB), W/Hz (DBMHZ)
        return p * df if unit == "DBMHZ" else p
    if unit == "WATT":
        return values
    if unit == "WATTHZ":
        return values * df
    volts = np.abs(values)  # VRMS; VRTHZ is V/√Hz, its square a density
    if unit == "VRTHZ":
        return volts * volts * df / impedance
    return volts * volts / impedance


# ---- the record --------------------------------------------------------------------------------------


class Operand:
    """One operand of a function as its setting spells it: a channel, another function, a
    constant (a float word) or unset."""

    __slots__ = ("channel", "constant", "function", "word")

    def __init__(self, word: str, channels: int, functions: int) -> None:
        name = word.strip().upper()
        self.word = name
        self.channel: int | None = None
        self.function: int | None = None
        self.constant: float | None = None
        if not name:
            return
        for prefix in ("CHANNEL", "CHAN", "CH"):
            if name.startswith(prefix) and name.removeprefix(prefix).isdigit():
                n = int(name.removeprefix(prefix))
                if not 1 <= n <= channels:
                    raise ScpiError(-224, f"{word}: no channel {n}")
                self.channel = n
                return
        for prefix in ("FUNCTION", "FUNC", "F"):
            if name.startswith(prefix) and name.removeprefix(prefix).isdigit():
                f = int(name.removeprefix(prefix))
                if not 1 <= f <= functions:
                    raise ScpiError(-224, f"{word}: no function {f}")
                self.function = f
                return
        try:
            self.constant = float(word)
        except ValueError:
            raise ScpiError(-224, f"{word}: not a source") from None


def aligned(first: Any, second: Any) -> None:
    """Whether two records sit on one sample grid (one acquisition): else the point-wise
    operators over both are a settings conflict, -221."""
    if first.dt_s != second.dt_s or first.origin != second.origin or first.points != second.points:
        raise ScpiError(-221, "the operands are not on one sample grid (acquire them together)")


class MathRecord:
    """A math function's waveform: the operator over its operands' records, computed where it is
    read. It speaks the Record interface the transfer, the measurements and the screen use
    (``points``, ``volts``, ``span``, ``take``, ``chunks``, ``envelope``); a transform's trace
    carries frequencies — ``dt_s`` is a bin's width, the origin the first bin's frequency.

    ``spec`` is the operator fixed at the read that built it (the operator word, its parameters,
    the output's grid and scale); an operator with a bounded neighbourhood is computed span by
    span from the sources' spans, the others (the integral, the filters, the transforms) over the
    whole source record — a source deeper than the model holds is refused, never shortened."""

    def __init__(
        self,
        spec: dict[str, Any],
        sources: list[Any],
        constants: list[float | None],
        volts: np.ndarray | None = None,
    ) -> None:
        self.spec = spec
        self.sources = sources
        self.constants = constants  # one entry per source, the constant where the source is None
        self._volts = volts
        first = next((r for r in sources if r is not None), None)
        self.channel = 0  # a function, not a channel: nothing names one
        self.dt_s = float(spec["dt_s"])
        self.scale = float(spec["scale"])
        self.offset = float(spec["offset"])
        self.triggered = bool(getattr(first, "triggered", True)) if first is not None else True
        self.x_origin_s = float(spec["x_origin_s"])
        self.t_start_ns = int(getattr(first, "t_start_ns", 0)) if first is not None else 0
        self.trigger_index = int(getattr(first, "trigger_index", 0)) if first is not None else 0
        self.x_hz = bool(spec.get("x_hz", False))  # a frequency-domain trace

    @property
    def points(self) -> int:
        if self._volts is not None:
            return int(self._volts.size)
        return int(self.spec["points"])

    @property
    def origin(self) -> float:
        return self.x_origin_s

    @property
    def volts(self) -> np.ndarray:
        """Every sample, in memory (a lazy trace: computed whole)."""
        if self._volts is None:
            self._volts = self.span(0, self.points)
        return self._volts

    def span(self, a: int, b: int) -> np.ndarray:
        """Samples ``a`` to ``b`` (excluded): the operator over the sources' matching spans."""
        a, b = max(0, a), min(self.points, b)
        if b <= a:
            return np.zeros(0)
        if self._volts is not None:
            return self._volts[a:b]
        return apply_span(self, a, b)

    def take(self, index: Any) -> np.ndarray:
        if isinstance(index, Points):  # a transfer's selection: contiguous or strided
            r = index.range
            if not r:
                return np.zeros(0)
            if r.step == 1:
                return self.span(r.start, r.start + len(r))
            return self.volts[r.start : r.start + len(r) * r.step : r.step]
        index = np.asarray(index)
        if index.size == 0:
            return np.zeros(0)
        return self.volts[index]

    def chunks(self, a: int = 0, b: int | None = None) -> Any:
        b = self.points if b is None else b
        for lo in range(a, b, CHUNK):
            yield self.span(lo, min(b, lo + CHUNK))

    def envelope(self, bins: int, key: tuple[object, ...] = ()) -> list[float]:
        edges = np.linspace(0, self.points, bins + 1, dtype=int)
        values = self.volts
        pairs = [
            (float(c.min()), float(c.max()))
            for c in (values[edges[i] : edges[i + 1]] for i in range(bins))
            if c.size
        ]
        return [v for pair in pairs for v in pair]


def apply_span(rec: MathRecord, a: int, b: int) -> np.ndarray:
    """The operator over one span of a lazy record (the whole recipe is in ``rec.spec``)."""
    operator = rec.spec["operator"]
    params = rec.spec["params"]
    src = rec.sources[0]
    if operator in ("ADD", "SUBTract", "MULTiply", "DIVide", "COMMonmode"):
        second = rec.sources[1]
        if src is not None and second is not None:
            aligned(src, second)
        va = src.span(a, b) if src is not None else np.full(b - a, rec.constants[0])
        vb = second.span(a, b) if second is not None else np.full(b - a, rec.constants[1])
        return pointwise(operator, va, vb)
    if operator in ("ABSolute", "INVert", "SQUare", "SQRT", "MAGNify"):
        return pointwise(operator, src.span(a, b))
    if operator in ("DELay", "GATing"):
        off = int(rec.spec["index0"])
        return src.span(a + off, b + off)
    if operator == "DIFF":
        w = 1  # one neighbour each side; the ends are one-sided, as over the whole record
        lo, hi = max(0, a - w), min(src.points, b + w)
        out = derivative(src.span(lo, hi), rec.dt_s)
        return out[a - lo : a - lo + (b - a)]
    if operator in ("SMOoth", "AVERage"):
        w = max(1, int(params["window"])) // 2
        lo, hi = max(0, a - w), min(src.points, b + w)
        out = running_mean(src.span(lo, hi), max(1, int(params["window"])))
        return out[a - lo : a - lo + (b - a)]
    if operator in ("MAXimum", "MINimum"):
        m = max(1, int(params["bucket"]))
        if m == 1:
            return src.span(a, b)
        return bucket_extreme(src.span(a * m, b * m), m, operator == "MAXimum")
    raise ScpiError(-300, f"operator {operator!r} has no span")


@component("scope_math")
class ScopeMath(Component):
    """A scope's math functions (``FUNCtion1..N``, #1074): the operators, the transforms and the
    measurements over their traces, computed from the acquisition's records when they are read.

    Each function's definition lives in the settings under ``prefix`` + its number —
    ``function1.operator``, ``function1.source1``, ``function1.display``, ``function1.range``, …
    — declared by the definition; the block reads them by path, so one block serves every
    function a model has. A source names a channel, another function (a cycle is -221) or a
    constant. Model limits: a transform longer than ``fft_max_points`` points or a span beyond
    ``fft_span_max`` is refused (-221/-222), never truncated; an unknown operand is -224."""

    refs = {"record": (Acquisition, "instrument")}
    defaults = {
        "record": "acq",  # the acquisition component holding the source records
        "prefix": "function",  # function f's settings live under <prefix><f>., e.g. function1.operator
        "functions": 2,  # how many math functions the model has (F1..Fn; another number is -114)
        "channels": 4,  # the channel operands the sources may name (CHANnel1..N)
        "operators": (),  # the operator words this model accepts; empty: every one this block knows
        "fft_max_points": 1 << 20,  # most points a transform is taken of; beyond: -221, not truncated
        "fft_span_max": None,  # most transform span the model sets, Hz; None: the sample rate's Nyquist
        "impedance": 50.0,  # Ω, the power units' reference when the model has no impedance setting
        "digits": 6,  # digits of a number a measurement of this block answers
        "invalid_value": "9.99E+37",  # what a measurement that cannot be made answers
        "peak_sort": "DMAG",  # the order the annotated peaks list in (a setting): DMAG, IMAG, IFR, DFR
        "resolution_scale": "",  # the timebase scale setting a :FFT:RESolution write follows, e.g. timebase.scale
    }

    # ---- the functions' definitions ----------------------------------------------------------

    def _setting(self, f: int, name: str, default: Any = None) -> Any:
        assert self.owner is not None
        path = f"{self.param('prefix')}{f}.{name}"
        if path not in self.owner.definition.state:
            return default
        value = self.owner.get_state(path)
        return default if value is None else value

    def _number(self, value: float) -> bytes:
        """A number in the definition's own reply format (the instrument's NR3, not the
        component's): the manual's sample replies are the response format's."""
        assert self.owner is not None
        return self.owner.format_number(value)

    def _function(self, f: int) -> int:
        if not 1 <= f <= int(self.param("functions")):
            raise ScpiError(-114, f"function {f} is not 1..{self.param('functions')}")
        return f

    def _operator(self, f: int) -> str:
        word = str(self._setting(f, "operator", "NONE") or "NONE").strip().upper()
        allowed = tuple(str(a).upper() for a in (self.param("operators") or ()))
        if word in ("NONE", ""):
            return "NONE"
        if word not in CANONICAL:
            raise ScpiError(-224, f"{word}: no such operator")
        if allowed and word not in allowed:
            raise ScpiError(-224, f"{word}: no such operator on this model")
        return CANONICAL[word]

    def _operand(self, f: int, which: int) -> Operand:
        return Operand(
            str(self._setting(f, f"source{which}", "") or ""),
            int(self.param("channels")),
            int(self.param("functions")),
        )

    # ---- the records ---------------------------------------------------------------------------

    def function_record(
        self, f: int, start: bool = True, seen: frozenset[int] = frozenset()
    ) -> tuple[MathRecord | None, int]:
        """Function ``f``'s waveform over its sources' records as they are read now (``start``:
        the read starts a transfer), and the virtual time getting the sources took. None: the
        function has no operator."""
        f = self._function(f)
        if f in seen:
            raise ScpiError(-221, f"function {f} takes its own output as a source (a cycle)")
        operator = self._operator(f)
        if operator in ("NONE", ""):
            return None, 0
        acq = self.ref("record")
        if acq is None or not isinstance(acq, Acquisition):
            raise ScpiError(-300, f"{self.uid}: no acquisition to take sources from")
        records: list[Any] = [None, None]
        constants: list[float | None] = [None, None]
        used = 0
        for which in (1, 2):
            operand = self._operand(f, which)
            if which == 2 and (operator in UNARY or not operand.word):
                break
            if not operand.word:
                raise ScpiError(-221, f"function {f} has no source")
            if operand.function is not None:
                rec, u = self.function_record(operand.function, start, seen | {f})
                if rec is None:
                    raise ScpiError(-221, f"function {operand.function} is not defined")
                records[which - 1] = rec
                used += u
            elif operand.channel is not None:
                rec, u = acq.read_record(operand.channel, start)
                used += u
                if rec is None:
                    raise ScpiError(-230, f"channel {operand.channel} has no record")
                records[which - 1] = rec
            else:
                constants[which - 1] = operand.constant
        if records[0] is None and records[1] is None and constants[0] is None:
            raise ScpiError(-221, f"function {f} has no source")
        spec = self._spec(f, operator, records)
        if operator in WHOLE_OPERATORS:
            return self._whole(spec, records, constants), used
        return MathRecord(spec, records, constants), used

    def read_function(self, f: int, start: bool = True) -> tuple[MathRecord | None, int]:
        """The record a read of function ``f`` uses (the transfer's ``Acquisition.read_record``)."""
        return self.function_record(f, start)

    def transfer_function(self, f: int, first: int) -> tuple[MathRecord | None, int]:
        """The record a waveform transfer of function ``f`` reads (``first``: its first point)."""
        return self.function_record(f, first == 0)

    def measure_function(self, f: int) -> tuple[MathRecord, int]:
        """The record a measurement on function ``f`` uses; a function without one is -230."""
        rec, used = self.function_record(f)
        if rec is None:
            raise ScpiError(-230, f"function {f} is not defined")
        return rec, used

    # ---- building the operator ------------------------------------------------------------------

    def _spec(self, f: int, operator: str, records: list[Any]) -> dict[str, Any]:
        """The operator's recipe, fixed at the read: the parameters it reads now, the output's
        grid (dt, x origin, points) and its vertical scale and offset."""
        source = next(r for r in records if r is not None)
        if bool(getattr(source, "x_hz", False)) and operator not in (
            "ADD",
            "SUBTract",
            "MULTiply",
            "DIVide",
            "COMMonmode",
            "MAGNify",
        ):
            raise ScpiError(-221, f"{operator}: the source is a frequency-domain trace")
        dt, x0 = float(source.dt_s), float(source.origin)
        points = int(source.points)
        params: dict[str, Any] = {}
        index0 = 0
        if operator == "DELay":
            x0 += float(self._setting(f, "delay", 0.0) or 0.0)
        elif operator == "GATing":
            start = float(self._setting(f, "gate_start", 0.0) or 0.0)
            stop = float(self._setting(f, "gate_stop", 0.0) or 0.0)
            if stop <= start:
                raise ScpiError(-221, "the gating window is empty")
            index0 = max(0, min(points, math.ceil((start - x0) / dt)))
            end = max(0, min(points, math.ceil((stop - x0) / dt)))
            points = max(0, end - index0)
            x0 += index0 * dt
        elif operator in ("SMOoth", "AVERage"):
            key = "smooth" if operator == "SMOoth" else "average"
            params["window"] = max(1, int(float(self._setting(f, key, 1) or 1)))
        elif operator in ("MAXimum", "MINimum"):
            params["bucket"] = max(1, int(float(self._setting(f, "bucket", 1) or 1)))
            points = points // params["bucket"]
        spec: dict[str, Any] = {
            "f": f,
            "operator": operator,
            "params": params,
            "dt_s": dt,
            "x_origin_s": x0,
            "points": points,
            "index0": index0,
            "scale": 1.0,
            "offset": 0.0,
        }
        if operator in FFT_OPERATORS:
            self._fft_spec(f, operator, source, spec)
        else:
            spec["scale"], spec["offset"] = self._vertical(f, source)
        return spec

    def _vertical(self, f: int, source: Any) -> tuple[float, float]:
        """The function's vertical scale (a division) and offset: manual (the ``range`` and
        ``offset`` settings, the range a division is an eighth of) or automatic, the source's."""
        if str(self._setting(f, "vertical", "AUTO") or "AUTO").upper().startswith("MAN"):
            return float(self._setting(f, "range", 8.0) or 8.0) / 8.0, float(
                self._setting(f, "offset", 0.0) or 0.0
            )
        return float(getattr(source, "scale", 1.0)), float(getattr(source, "offset", 0.0))

    def _whole(self, spec: dict[str, Any], records: list[Any], constants: list[float | None]) -> MathRecord:
        """The operator computed over the whole source record: the integral, the filters and the
        transforms (a transform's trace replaces the grid — its bins, its unit, its span)."""
        source = next(r for r in records if r is not None)
        operator = spec["operator"]
        v = np.asarray(source.volts, dtype=np.float64)
        if operator == "INTegrate":
            volts = integral(v, spec["dt_s"])
        elif operator == "HIGHpass":
            volts = highpass(v, spec["dt_s"], float(self._setting(spec["f"], "hp_bw", 1e3) or 1e3))
        elif operator == "LOWPass":
            volts = lowpass(v, spec["dt_s"], float(self._setting(spec["f"], "lp_bw", 1e3) or 1e3))
        else:
            volts = self._compute_fft(spec, v)
        rec = MathRecord(spec, records, constants, volts=volts)
        if operator in FFT_OPERATORS and not str(
            self._setting(spec["f"], "vertical", "AUTO") or "AUTO"
        ).upper().startswith("MAN"):
            lo, hi = float(np.nanmin(volts)), float(np.nanmax(volts))  # the trace's own scale
            rec.scale = (hi - lo) / 8.0 if hi > lo else max(abs(hi), abs(lo), 1e-12) / 4.0
            rec.offset = (hi + lo) / 2.0
        return rec

    # ---- the transform --------------------------------------------------------------------------

    def _fft_spec(self, f: int, operator: str, source: Any, spec: dict[str, Any]) -> None:
        """The transform's recipe: its length (the effective memory depth, capped), its window,
        its vertical unit, impedance and span — the trace's grid replaces the source's."""
        n = int(source.points)
        points = 1
        while points * 2 <= n and points * 2 <= int(self.param("fft_max_points")):
            points *= 2
        if points < MIN_FFT_POINTS:
            raise ScpiError(-221, f"a record of {n} points has no transform this model computes")
        dt = float(source.dt_s)
        rate = 1.0 / dt
        unit = str(self._setting(f, "vunits", "DB") or "DB").upper()
        window_word = str(self._setting(f, "window", "HANNing") or "HANNing").upper()
        if window_word not in WINDOW_WORDS:
            raise ScpiError(-224, f"{window_word}: no such window")
        window_kind = WINDOW_WORDS[window_word]
        # A span of 0 Hz is a nonsense request, so the settings without a default (0.0 at
        # power-on) read as unset — the trace then spans the whole one-sided band, as the
        # instrument's AUTO does.
        span = self._setting(f, "span") or None
        center = self._setting(f, "center") or None
        start = self._setting(f, "start") or None
        stop = self._setting(f, "stop") or None
        if span is None and center is None and start is None and stop is None:
            start, stop = 0.0, rate / 2.0  # the whole one-sided span
        elif span is not None or center is not None:
            span = float(span) if span is not None else float(stop or 0.0) - float(start or 0.0)
            center = float(center) if center is not None else (float(stop or 0.0) + float(start or 0.0)) / 2.0
            start, stop = center - span / 2.0, center + span / 2.0
        else:
            start, stop = float(start or 0.0), float(stop or rate / 2.0)
        most = float(self.param("fft_span_max") or rate)
        if stop <= start or stop - start > most:
            raise ScpiError(-222, f"the span {stop - start:g} Hz is beyond this model's {most:g} Hz")
        spec["fft"] = {
            "points": points,
            "dt_s": dt,
            "unit": unit,
            "window": window_kind,
            "start": start,
            "stop": stop,
            "impedance": float(
                self._setting(f, "impedance", self.param("impedance")) or self.param("impedance")
            ),
            "t_delay": float(self._setting(f, "tdelay", 0.0) or 0.0),
            "detector": str(self._setting(f, "detector", "OFF") or "OFF").upper(),
            "detector_points": int(float(self._setting(f, "detector_points", 0) or 0)),
        }
        spec["x_hz"] = True

    def _compute_fft(self, spec: dict[str, Any], v: np.ndarray) -> np.ndarray:
        """The transform's trace: the windowed one-sided FFT, in its unit, over its span, then
        the detector's decimation. The trace's grid (bin width, first frequency) lands in the
        spec — a record reading it answers a frequency axis."""
        fft = spec["fft"]
        n = int(fft["points"])
        w = window(fft["window"], n)
        x = one_sided_fft(v[:n], w)
        df = 1.0 / (n * fft["dt_s"])
        freqs = np.arange(x.size) * df
        if spec["operator"] == "FFTPhase":
            trace = phase(x, fft["t_delay"], freqs)
        else:
            trace = magnitude(x, fft["unit"], fft["impedance"])
            if spec["operator"] == "PSDensity":
                trace = as_density(trace, fft["unit"], noise_bandwidth(w, fft["dt_s"]))
        inside = np.flatnonzero((freqs >= fft["start"]) & (freqs <= fft["stop"]))
        if not inside.size:
            raise ScpiError(-221, "the span holds no point of this trace")
        trace = trace[inside]
        spec["dt_s"] = df
        spec["x_origin_s"] = float(freqs[inside[0]])
        detector = fft["detector"]
        k = int(fft["detector_points"])
        if detector != "OFF" and k > 0 and trace.size > k:
            edges = np.linspace(0, trace.size, k + 1, dtype=int)
            buckets = [trace[edges[i] : edges[i + 1]] for i in range(k)]
            if detector == "SAMPle":
                trace = np.array([b[b.size // 2] for b in buckets])
            elif detector == "AVERage":
                trace = np.array([b.mean() for b in buckets])
            elif detector == "PPOSitive":
                trace = np.array([b.max() for b in buckets])
            elif detector == "PNEGative":
                trace = np.array([b.min() for b in buckets])
            else:  # NORMal: the sample furthest from the bucket's mean each bucket
                trace = np.array([b[np.argmax(np.abs(b - b.mean()))] for b in buckets])
        return trace

    # ---- the FFT measurements ---------------------------------------------------------------------

    def _trace(self, f: int) -> tuple[MathRecord, np.ndarray, np.ndarray]:
        """Function ``f``'s transform trace, its frequencies and its values (a time-domain
        function is a settings conflict: the FFT measurements need a transform)."""
        rec, _ = self.measure_function(f)
        if not rec.x_hz:
            raise ScpiError(-221, f"function {f} is not a transform")
        freqs = rec.origin + np.arange(rec.points) * rec.dt_s
        return rec, freqs, np.asarray(rec.volts, dtype=float)

    def _peaks(self, rec: MathRecord, freqs: np.ndarray, values: np.ndarray, level_dbm: float) -> list[int]:
        """The trace's peaks above a level in dBm, ordered as the ``peak_sort`` parameter asks."""
        impedance = float(rec.spec["fft"]["impedance"])
        unit = str(rec.spec["fft"]["unit"])
        order = str(self.param("peak_sort") or "DMAG").upper()
        peaks = [
            i
            for i in find_peaks(freqs, values, -math.inf)
            if _in_dbm(float(values[i]), unit, impedance) > level_dbm
        ]
        if order.startswith("IF"):
            peaks.sort(key=lambda i: freqs[i])
        elif order.startswith("DFR"):
            peaks.sort(key=lambda i: -freqs[i])
        elif order.startswith("IMA"):
            peaks.sort(key=lambda i: values[i])
        else:  # DMAG: by decreasing magnitude
            peaks.sort(key=lambda i: -values[i])
        return peaks

    @op("fft_peak", fields=("kind",))
    def op_fft_peak(
        self, kind: str = "FREQuency", suffixes: dict[str, int] | None = None, **_: Any
    ) -> OpResult:
        """``:FUNCtion<f>:FFT:PEAK:FREQuency?`` / ``…:MAGNitude?``: the annotated peaks (above
        the function's peak level, at most its peak count), comma-separated in the ``peak_sort``
        order; the annotation off (``peak_state``) or none found: ``invalid_value``."""
        f = self._function(_suffix(suffixes))
        rec, freqs, values = self._trace(f)
        if not V.flag(self._setting(f, "peak_state", True)):
            return OpResult(str(self.param("invalid_value")).encode())
        level = float(self._setting(f, "peak_level", -math.inf) or -math.inf)
        peaks = self._peaks(rec, freqs, values, level)
        count = int(float(self._setting(f, "peak_count", 0) or 0))
        if count > 0:
            peaks = peaks[:count]
        if not peaks:
            return OpResult(str(self.param("invalid_value")).encode())
        column = freqs if kind.upper().startswith("FREQ") else values
        return OpResult(b",".join(self._number(float(column[i])) for i in peaks))

    @op("fft_measure", fields=("kind",))
    def op_fft_measure(
        self,
        kind: str = "FREQuency",
        source: Any = None,
        peak: int = 1,
        peak2: int = 1,
        level: float | None = -60.0,
        center: float = 0.0,
        bandwidth: float = 0.0,
        percent: float = 99.0,
        mode: str | None = None,
        fundamental: float | None = None,
        **_: Any,
    ) -> OpResult:
        """``:MEASure:FFT:<kind>? <source>,…``: FREQuency and MAGNitude of a peak, DFRequency and
        DMAGnitude between two peaks (numbered low to high above ``level`` dBm), CPOWer of a band
        and its PSD, OBW at ``percent`` of the power, THDistortion about the fundamental."""
        f = _source_function(source, int(self.param("functions")))
        rec, freqs, values = self._trace(f)
        fft = rec.spec["fft"]
        kind = kind.upper()
        invalid = str(self.param("invalid_value")).encode()

        def number(value: float) -> bytes:
            return self._number(value)

        if kind in ("FREQUENCY", "MAGNITUDE", "DFREQUENCY", "DMAGNITUDE"):
            threshold = float(level) if level is not None else -60.0
            peaks = [
                i
                for i in find_peaks(freqs, values, -math.inf)
                if _in_dbm(float(values[i]), str(fft["unit"]), float(fft["impedance"])) > threshold
            ]
            peaks.sort(key=lambda i: freqs[i])  # numbered from low to high frequency
            try:
                if kind in ("FREQUENCY", "MAGNITUDE"):
                    i = peaks[int(peak) - 1]
                    value = float(freqs[i]) if kind == "FREQUENCY" else float(values[i])
                else:
                    i, j = peaks[int(peak) - 1], peaks[int(peak2) - 1]
                    value = (
                        float(freqs[j] - freqs[i]) if kind == "DFREQUENCY" else float(values[j] - values[i])
                    )
            except IndexError:
                return OpResult(invalid)
            return OpResult(number(value))
        power = bin_power(values, str(fft["unit"]), float(fft["impedance"]), rec.dt_s)
        if kind in ("CPOWER", "PSD"):
            value = band_power(power, freqs, float(center), float(bandwidth) / 2.0)
            if kind == "PSD":
                value = value / float(bandwidth) if bandwidth > 0 else math.nan
            return OpResult(number(10.0 * math.log10(value / 1e-3) if value > 0 else -math.inf))
        if kind == "OBW":
            return OpResult(number(occupied_bandwidth(freqs, power, float(percent))))
        if kind == "THDISTORTION":
            if str(mode or "").upper().startswith("PEAK"):
                # the fundamental is the first peak above the level (the manual's PEAK form)
                found = self._peaks(rec, freqs, values, float(level if level is not None else -60.0))
                fund = float(freqs[found[0]]) if found else 0.0
            else:
                fund = float(fundamental or 0.0)
            half = 1.5 * rec.dt_s  # the fundamental and each harmonic: the peak bin ± its neighbours
            base = band_power(power, freqs, fund, half)
            if base <= 0.0:
                return OpResult(invalid)
            harm, h = 0.0, 2
            while fund * h <= freqs[-1]:
                harm += band_power(power, freqs, fund * h, half)
                h += 1
            return OpResult(number(100.0 * math.sqrt(harm / base)))
        raise ScpiError(-300, f"{self.uid}: no FFT measurement {kind!r}")

    @op("definition")
    def op_definition(self, suffixes: dict[str, int] | None = None, **_: Any) -> OpResult:
        """``:FUNCtion<f>?``: the function's operands as set — the operator word rides the reply
        header (":FUNC1:ADD CHAN1,CHAN2") when headers are on, the definition's reply template."""
        f = self._function(_suffix(suffixes))
        parts = []
        for which in (1, 2):
            word = str(self._setting(f, f"source{which}", "") or "")
            if which == 2 and (self._operator(f) in UNARY or not word):
                continue
            parts.append(word)
        return OpResult(",".join(parts).encode())

    @op("h_field", fields=("field",))
    def op_h_field(
        self,
        field: str = "horizontal",
        value: Any = None,
        query: bool = False,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """``:FUNCtion<f>:HORizontal``, ``…:POSition`` and ``…:RANGe``: the function's horizontal
        scaling. The operators whose horizontal scaling follows the source's timebase (ADD,
        SUBTract, MULTiply, DIVide, COMMonmode, ABSolute, DIFF, INTegrate, INVert, HIGHpass,
        LOWPass, MAXimum, MINimum, SMOoth, AVERage, SQUare, SQRT — the manual's list) refuse the
        write with -221; on an FFTMagnitude the position and range are the center frequency and
        the span, on the others they are the function's own."""
        f = self._function(_suffix(suffixes))
        operator = self._operator(f)
        if operator.upper() in _TIMEBASE_BOUND:
            raise ScpiError(
                -221,
                f"{operator} follows the source's timebase: use :TIMebase:POSition/:RANGe",
            )
        fft = operator in FFT_OPERATORS
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if field == "horizontal":
            if query or value is None:
                return OpResult(str(self._setting(f, "horizontal", "AUTO") or "AUTO").encode())
            word = V.parse_string(value if isinstance(value, bytes) else str(value).encode()).upper()
            if word not in ("AUTO", "MANUAL"):
                raise ScpiError(-224, f"{value}: AUTO or MANual")
            return self._set_h(f, "horizontal", "MANual" if word == "MANUAL" else "AUTO")
        # position/range: a frequency on a transform, seconds elsewhere
        name = {"position": "center" if fft else "h_position", "range": "span" if fft else "h_range"}[field]
        path_value = self._setting(f, name)
        if query or value is None:
            if fft and path_value is None:
                # an FFT without an explicit window answers its whole one-sided span
                pair = self.measure_function(f)
                g = pair[0].spec
                return OpResult(
                    self._number(
                        float(g["x_origin_s"])
                        if field == "position"
                        else float(g["points"]) * float(g["dt_s"])
                    )
                )
            if path_value is None:
                return OpResult(self._number(0.0))
            return OpResult(self._number(float(path_value)))
        if isinstance(value, bytes):
            number = float(V.parse_number(value.decode("ascii", errors="replace")))
        elif isinstance(value, str):
            number = float(V.parse_number(value))
        else:
            number = float(value)
        return self._set_h(f, name, number)

    def _set_h(self, f: int, name: str, value: Any) -> OpResult:
        """Write one of the function's horizontal settings, marking the mode manual when the
        manual says a position/range write does (``horizontal`` AUTO → MANual)."""
        assert self.owner is not None
        if name in ("center", "span", "h_position", "h_range"):
            h_path = f"{self.param('prefix')}{f}.horizontal"
            if h_path in self.owner.definition.state and str(
                self.owner.get_state(h_path) or "AUTO"
            ).upper().startswith("AUTO"):
                self.owner.set_state(h_path, "MANual", source="scpi", internal=True)
        self.owner.set_state(f"{self.param('prefix')}{f}.{name}", value, source="scpi", internal=True)
        return OpResult()

    @op("fft_resolution")
    def op_fft_resolution(
        self,
        query: bool = False,
        resolution: float | None = None,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """``:FUNCtion<f>:FFT:RESolution``: the bandwidth the trace's bins answer — the bin width
        times the window's equivalent noise bandwidth, as the manual defines the resolution. A
        write moves the timebase's scale (the setting ``resolution_scale`` names), as the
        instrument reaches a resolution by sampling longer."""
        f = self._function(_suffix(suffixes))
        fft = self.measure_function(f)[0].spec["fft"]
        enbw = noise_bandwidth(window(str(fft["window"]), int(fft["points"])), float(fft["dt_s"]))
        if query or resolution is None:
            return OpResult(self._number(enbw))
        name = str(self.param("resolution_scale") or "")
        if not name or self.owner is None or self.resolve_path(name) not in self.owner.definition.state:
            raise ScpiError(-221, "this model's timebase does not follow the FFT resolution")
        window_s = int(fft["points"]) * float(fft["dt_s"])  # the transform's span now
        self.owner.set_state(
            self.resolve_path(name),
            window_s * (enbw / float(resolution)) / 10.0,  # longer by the resolution ratio
            source="scpi",
            internal=True,
        )
        return OpResult()


def _suffix(suffixes: dict[str, int] | None) -> int:
    """The ``<f>`` a command carried; 1 when it carried none."""
    if not suffixes:
        return 1
    for key in ("f", "F"):
        if key in suffixes:
            return int(suffixes[key])
    return int(next(iter(suffixes.values())))


def function_index(source: Any) -> int | None:
    """The function a source word names (FUNCtion<F>/FUNC<F>/F<F>), None when it names no
    function — how a transfer or a measurement tells a math source from a channel."""
    if isinstance(source, bytes):
        source = V.parse_string(source)
    word = str(source or "").strip().upper()
    for prefix in ("FUNCTION", "FUNC", "F"):
        if word.startswith(prefix) and word.removeprefix(prefix).isdigit():
            return int(word.removeprefix(prefix))
    return None


def _source_function(source: Any, functions: int) -> int:
    """The function a measurement's ``<source>`` names (FUNCtion<F>/FUNC<F>/F<F>); -224 else."""
    f = function_index(source)
    if f is not None and 1 <= f <= functions:
        return f
    raise ScpiError(-224, f"{source!r}: not a function")


def _in_dbm(value: float, unit: str, impedance: float) -> float:
    """A trace value turned into dBm, whatever its unit (the peak threshold is in dBm)."""
    if unit in ("WATT", "WATTHZ"):
        return 10.0 * math.log10(value / 1e-3) if value > 0 else -math.inf
    if unit in ("VRMS", "VRTHZ"):
        p = value * value / impedance
        return 10.0 * math.log10(p / 1e-3) if p > 0 else -math.inf
    if unit == "DBMV":
        return value - 60.0 + 30.0 - 10.0 * math.log10(impedance)
    if unit == "DBUV":
        return value - 120.0 + 30.0 - 10.0 * math.log10(impedance)
    return value  # DB and DBMHZ are dBm already
