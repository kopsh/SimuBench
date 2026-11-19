"""The pure maths of the scope's math functions: the operator tables and the signal processing behind them (running mean, derivative, integral, filters, windows, the FFT and its peaks). No component here (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math

import numpy as np

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

#: The FFT vertical units (:FUNCtion<F>:FFT:VUNits): the magnitude family (dBm as DB, dBV, dBmV,
#: dBµV, Watt, Vrms) and the power-spectral-density family (dBm/Hz, Watt/Hz, V/√Hz).
VUNITS = ("DB", "DBV", "DBMV", "DBUV", "WATT", "VRMS", "DBMHZ", "WATTHZ", "VRTHZ")

#: A model's word for a unit this block spells otherwise (the setting holds the model's word).
VUNIT_WORDS = {"DECIBEL": "DBV"}

#: The vertical unit's word in the guide's ``:WAVeform:YUNits?`` list (pp. 1647, #1066): the dB
#: family reads DECibels, the Watt family WATT, the volt family VOLT.
GUIDE_UNIT_WORDS = {
    "DB": "DECibels",
    "DBV": "DECibels",
    "DBMV": "DECibels",
    "DBUV": "DECibels",
    "DBMHZ": "DECibels",
    "DECIBEL": "DECibels",
    "VRMS": "VOLT",
    "VRTHZ": "VOLT",
    "WATT": "WATT",
    "WATTHZ": "WATT",
}

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
            "LN",
            "LOG",
            "EXP",
            "TEN",
            "LINear",
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
    "EXP",
    "HIGHPASS",
    "INTEGRATE",
    "INVERT",
    "LN",
    "LINEAR",
    "LOG",
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
    "LN",
    "LOG",
    "EXP",
    "TEN",
    "LINear",
    "FFTMagnitude",
    "FFTPhase",
    "PSDensity",
)

CANONICAL: dict[str, str] = {word.upper(): word for word in OPERATORS}

#: A model may spell the transform plainly: the 2000 X-series (:FUNCtion:OPERation FFT) has one
#: FFT, the magnitude one — the phase and density are named transforms (:FUNCtion<F>:FFT:PHASE).
CANONICAL["FFT"] = "FFTMagnitude"


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
    (NaN where negative), the logarithms and exponentials (a hole where the argument is not in
    the domain) and MAGNify."""
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
    if kind == "LN":
        with np.errstate(invalid="ignore"):
            return np.where(a > 0.0, np.log(np.where(a > 0.0, a, 1.0)), np.nan)
    if kind == "LOG":
        with np.errstate(invalid="ignore"):
            return np.where(a > 0.0, np.log10(np.where(a > 0.0, a, 1.0)), np.nan)
    if kind == "EXP":
        return np.exp(a)
    if kind == "TEN":
        return np.power(10.0, a)
    if kind == "MAGNify":
        return a
    raise ValueError(f"no point-wise operator {kind!r}")


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
    """The one-sided transform in a vertical unit: Vrms and its decibels (dBV, dBmV, dBµV), a power
    unit (dBm referenced to ``impedance``, Watt)."""
    rms = np.abs(x) / math.sqrt(2.0)
    if unit == "VRMS":
        return rms
    if unit == "WATT":
        return rms * rms / impedance
    if unit in ("DB", "DBMHZ"):
        with np.errstate(divide="ignore", invalid="ignore"):
            return 10.0 * np.log10(np.where(rms > 0, rms * rms / impedance / 1e-3, np.nan))
    scale = {"DBV": 0.0, "DBMV": 60.0, "DBUV": 120.0}[unit]
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
