"""Automatic measurements on a record: the algorithms, as pure functions of the samples.

Every measurement takes the record's volts and its sample interval, and nothing else — no
component, no session, no state — so the module can be tested on its own. The scope's
``scope_measurements`` component (scope.py) applies a definition's rules around them (gate,
clipping, the crossing level and its hysteresis) and formats the replies; the oscilloscope
template's measurement vocabulary (``MEASUREMENTS``) is defined here too, so the template and
the component cannot drift apart.
"""

from __future__ import annotations

import itertools
import math

import numpy as np

from ...scpi.errors import ScpiError

#: The engine measurements a definition names for ``scope_measurements`` (the oscilloscope
#: template's measurement list; the shape measurements of ``PULSE_MEASUREMENTS`` and the time
#: measurements of ``TIME_MEASUREMENTS`` are extra vocabulary of the component).
MEASUREMENTS = ["VPP", "VMAX", "VMIN", "VAMPlitude", "VRMS", "VAVerage", "FREQuency", "PERiod"]

#: Bins of a record's histogram, from which the top and the base are read. Fine against any
#: ADC grid a record can have, so a modal bin is one level and its mean is that level.
TOP_BASE_BINS = 1000
#: A half of that histogram holds a level when its fullest bin holds at least this times the
#: average of the half's occupied bins; a ramp fills it evenly and has no levels to tell.
TOP_BASE_PEAK = 2.0
#: Half the frequency counter's hysteresis band, of top minus base: a crossing counts only
#: after the record reached below the band, so noise near the middle cannot fake one.
FREQ_HYSTERESIS = 0.1


def top_base(v: np.ndarray) -> tuple[float, float] | None:
    """Top and base of a record: the modes of its histogram in the upper and the lower half.

    AMPLitude and the frequency counter take the levels the waveform rests on, not the halves'
    medians (a sine's upper median sits at 0.707 of its peak) and not the extremes (a noise
    spike would count as amplitude). Each half must form a peak — its fullest bin holding at
    least twice the average of the half's occupied bins — otherwise no levels can be told (a
    ramp or triangle fills the histogram evenly) and None comes back; the caller falls back to
    the extremes. The level is the mean of the samples in its modal bin.
    """
    vmax, vmin = float(v.max()), float(v.min())
    if not vmax > vmin:
        return None
    counts, edges = np.histogram(v, bins=TOP_BASE_BINS, range=(vmin, vmax))
    centres = (edges[:-1] + edges[1:]) / 2
    mid = (vmax + vmin) / 2
    levels: list[float] = []
    for above in (True, False):
        idx = np.nonzero(centres > mid if above else centres < mid)[0]
        if not idx.size:
            return None
        top = idx[np.argmax(counts[idx])]
        taken = counts[idx][counts[idx] > 0]
        if counts[top] < TOP_BASE_PEAK * taken.mean() or taken.size < 2:
            return None
        sel = (v >= edges[top]) & (v <= edges[top + 1])
        levels.append(float(v[sel].mean()))
    return levels[0], levels[1]


# measure takes a ``top_base`` argument, which shadows the function in its body.
_TOP_BASE = top_base


def period(
    v: np.ndarray,
    dt: float,
    levels: tuple[float, float] | None = None,
    mid: float | None = None,
    hysteresis: float = FREQ_HYSTERESIS,
    min_cycles: int = 1,
    min_band: float = 0.0,
) -> float | None:
    """The record's period, or None when it holds no complete cycle.

    The counter runs on the hysteresis band the top and the base set around the middle (the
    10 % / 50 % / 90 % reference levels; frequency counts the middle one): a rising crossing
    counts only after the record last reached below the band's lower edge, so noise and
    quantization near the middle cannot fake crossings, and a swing that never gets above the
    band's upper edge counts for nothing. Each crossing is interpolated on the middle for
    sub-sample time; the period is the least-squares slope of crossing time against crossing
    number, which noise on the single crossings averages out of. The band comes from the top
    and the base, falling back to the extremes when the histogram forms no peaks (a triangle
    has no levels to tell but a well-defined span).
    """
    if levels is None:
        levels = top_base(v)
    if levels is None:
        vmax, vmin = float(v.max()), float(v.min())
        levels = (vmax, vmin) if vmax > vmin else None
    if levels is None:
        return None
    top, base = levels
    if mid is None:
        mid = (top + base) / 2
    # The counter's band is the signal's own span scaled, but never below ``min_band``: a real
    # counter's band is sized in divisions of the screen, so a trace of noise alone (whose span
    # IS the noise) counts no crossings and the period stays unmeasurable (#745).
    h = max(hysteresis * (top - base), min_band)
    n = v.size
    idx = np.arange(n)
    last_below = np.maximum.accumulate(np.where(v <= mid - h, idx, -1))
    last_above = np.maximum.accumulate(np.where(v >= mid + h, idx, -1))
    if v[0] <= mid:  # the record opens in the lower half: its first rise is countable
        last_below = np.maximum(last_below, 0)
    above_before = np.concatenate(([-1], last_above[:-1]))  # the hit's own sample does not count
    hit = np.nonzero((last_below > above_before) & (v >= mid + h))[0]
    if hit.size < max(2, min_cycles + 1):
        return None
    times = np.empty(hit.size)
    for j, i in enumerate(hit):
        seg = v[last_below[i] : i + 1]  # the swing: from not above the middle to above the band
        k = last_below[i] + int(np.nonzero((seg[:-1] <= mid) & (seg[1:] > mid))[0][0])
        frac = (mid - float(v[k])) / (float(v[k + 1]) - float(v[k]))
        times[j] = (k + min(max(frac, 0.0), 1.0)) * dt
    seq = np.arange(times.size, dtype=float)
    seq -= seq.mean()
    slope = float(np.dot(seq, times - times.mean()) / np.dot(seq, seq))
    return slope if slope > 0 else None


def measure(
    what: str,
    v: np.ndarray,
    dt: float,
    *,
    top_base: str = "histogram",
    mid: float | None = None,
    hysteresis: float = FREQ_HYSTERESIS,
    min_cycles: int = 1,
    min_band: float = 0.0,
) -> float:
    """VPP, VMAX, VMIN, VAVerage, VRMS, VAMPlitude (top - base), FREQuency and PERiod, and the
    shape measurements of ``PULSE_MEASUREMENTS`` (#248).

    ``top_base``: the levels from the histogram (``histogram``) or the extremes (``extremes``);
    ``mid``: the crossing level in volts (None: halfway between top and base); ``hysteresis``: half
    the counter's band, of top minus base; ``min_cycles``: complete cycles a period needs.
    Unmeasurable — fewer cycles in the record — comes back as NaN, which the callers report as
    the definition's invalid value.
    """
    if v.size == 0:
        return math.nan
    vmax, vmin = float(v.max()), float(v.min())
    levels = _TOP_BASE(v) if top_base == "histogram" else ((vmax, vmin) if vmax > vmin else None)
    if what == "VPP":
        return vmax - vmin
    if what == "VMAX":
        return vmax
    if what == "VMIN":
        return vmin
    if what == "VAVerage":
        return float(v.mean())
    if what == "VRMS":
        return float(np.sqrt(np.mean(v**2)))
    if what == "VAMPlitude":
        return levels[0] - levels[1] if levels else vmax - vmin
    if what in ("FREQuency", "PERiod"):
        found = period(v, dt, levels, mid, hysteresis, min_cycles, min_band)
        if found is None:
            return math.nan
        return 1.0 / found if what == "FREQuency" else found
    if what in PULSE_MEASUREMENTS:
        return _pulse_measure(what, v, dt, levels, mid, hysteresis, min_cycles)
    raise ScpiError(-113, f"measurement {what!r} not supported")


#: Measurements of a waveform's shape (#248): top/base, overshoot/preshoot (percent of top minus
#: base), 10–90 % rise and fall times, pulse widths at the middle level, duty cycles (percent),
#: and edge and pulse counts over the record.
PULSE_MEASUREMENTS = (
    "VTOP",
    "VBASe",
    "OVERshoot",
    "PREShoot",
    "RISetime",
    "FALLtime",
    "PWIDth",
    "NWIDth",
    "DUTYcycle",
    "NDUTy",
    "PEDGes",
    "NEDGes",
    "PPULses",
    "NPULses",
)


def crossings(v: np.ndarray, dt: float, level: float, band: float) -> list[tuple[float, int]]:
    """Times (s) the record crosses ``level`` with ``band`` of hysteresis: (time, +1 rising /
    -1 falling), each linearly interpolated between the two samples around the level."""
    out: list[tuple[float, int]] = []
    state = 0  # +1 above level + band, -1 below level - band
    for k in range(v.size):
        x = float(v[k])
        new = 1 if x > level + band else -1 if x < level - band else state
        if state != 0 and new != state:
            j = k
            while j > 0 and (float(v[j - 1]) - level) * new > 0:
                j -= 1  # back to the sample that crosses the level itself
            a, b = float(v[j - 1]) if j > 0 else x, float(v[j])
            frac = 0.0 if a == b else (level - a) / (b - a)
            out.append(((j - 1 + min(max(frac, 0.0), 1.0)) * dt, new))
        state = new
    return out


def _pulse_measure(
    what: str,
    v: np.ndarray,
    dt: float,
    levels: tuple[float, float] | None,
    mid: float | None,
    hysteresis: float,
    min_cycles: int,
) -> float:
    vmax, vmin = float(v.max()), float(v.min())
    top, base = levels if levels else (vmax, vmin)
    amp = top - base
    if what == "VTOP":
        return top
    if what == "VBASe":
        return base
    if amp <= 0:
        return math.nan
    if what == "OVERshoot":
        return max(0.0, vmax - top) / amp * 100.0
    if what == "PREShoot":
        return max(0.0, base - vmin) / amp * 100.0
    band = hysteresis * amp
    if what in ("RISetime", "FALLtime"):
        low = crossings(v, dt, base + 0.1 * amp, band / 4)
        high = crossings(v, dt, base + 0.9 * amp, band / 4)
        sign = 1 if what == "RISetime" else -1
        for t0, d in low if sign > 0 else high:
            if d != sign:
                continue
            ends = [t for t, e in (high if sign > 0 else low) if e == sign and t >= t0]
            if ends:
                return ends[0] - t0  # the first complete edge on the record
        return math.nan
    level = mid if mid is not None else base + amp / 2
    cross = crossings(v, dt, level, band)
    if what in ("PEDGes", "NEDGes"):
        return float(sum(1 for _t, d in cross if d == (1 if what == "PEDGes" else -1)))
    if what in ("PPULses", "NPULses"):
        first = 1 if what == "PPULses" else -1
        return float(sum(1 for (_t, a), (_u, b) in itertools.pairwise(cross) if a == first and b == -first))
    widths = {1: [], -1: []}  # positive / negative pulse widths
    for (t, a), (u, b) in itertools.pairwise(cross):
        if b == -a:
            widths[a].append(u - t)
    if what in ("PWIDth", "NWIDth"):
        w = widths[1 if what == "PWIDth" else -1]
        return w[0] if w else math.nan
    pos, neg = widths[1], widths[-1]
    if not pos or not neg:
        return math.nan
    found = pos[0] + neg[0]
    return (pos[0] if what == "DUTYcycle" else neg[0]) / found * 100.0


#: Time measurements (#350): relative to the trigger (the record's origin), at the middle level.
TIME_MEASUREMENTS = ("XMAX", "XMIN", "BRATe")


def mid_crossings(
    v: np.ndarray, dt: float, hysteresis: float, min_band: float = 0.0
) -> list[tuple[float, int]]:
    """The record's crossings of its middle level (halfway between top and base), as in
    :func:`_pulse_measure`; the band never drops below ``min_band`` (a screen-referenced floor,
    so noise alone counts no crossings, #745)."""
    levels = top_base(v)
    top, base = levels if levels else (float(v.max()), float(v.min()))
    amp = top - base
    if amp <= 0:
        return []
    return crossings(v, dt, base + amp / 2, max(hysteresis * amp, min_band))


def edge_spec(word: str | None) -> tuple[int, int]:
    """``+2`` / ``-1`` / ``3``: the slope (+1 rising, -1 falling; rising when unsigned) and the
    occurrence (1 = the first), as :MEASure:TEDGe? and TVALue? take them."""
    text = (word or "+1").strip()
    sign = -1 if text.startswith("-") else 1
    digits = text.lstrip("+-")
    if not digits.isdigit() or int(digits) < 1:
        raise ScpiError(-224, f"edge {word!r}: a slope (+/-) and an occurrence from 1")
    return sign, int(digits)
