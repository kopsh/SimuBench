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
from dataclasses import dataclass

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
    general: Levels | None = None,
    rise_fall: Levels | None = None,
) -> float:
    """VPP, VMAX, VMIN, VAVerage, VRMS, VAMPlitude (top - base), FREQuency and PERiod, and the
    shape measurements of ``PULSE_MEASUREMENTS`` (#248).

    ``general`` / ``rise_fall``: a model's measurement thresholds (:func:`threshold_levels`) —
    the top, base and the three reference levels with their crossing band; ``general`` serves
    every measurement but rise and fall times, which take ``rise_fall`` (the Infiniium's
    :MEASure:THResholds:GENeral / :RFALl). Without them: top and base by ``top_base``, the middle
    at ``mid``, rise and fall between 10 % and 90 %.

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
    if general is not None:
        levels, mid = (general.top, general.base), general.middle
        amp = general.top - general.base
        hysteresis = general.band / amp if amp > 0 else hysteresis
        min_band = 0.0
    if what in LEVEL_MEASUREMENTS:
        return level_measure(what, v, dt, general or default_levels(v, top_base, hysteresis, min_band))
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
        return _pulse_measure(what, v, dt, levels, mid, hysteresis, min_cycles, rise_fall)
    raise ScpiError(-113, f"measurement {what!r} not supported")


@dataclass(frozen=True)
class Levels:
    """A record's top and base and its three reference levels (volts), with the hysteresis band a
    crossing of them takes (volts, half its width)."""

    top: float
    base: float
    upper: float
    middle: float
    lower: float
    band: float

    def at(self, position: str) -> float:
        """The level an UPPer / MIDDle / LOWer word names."""
        word = position.strip().upper()
        if word.startswith("UPP"):
            return self.upper
        if word.startswith("LOW"):
            return self.lower
        if word.startswith("MIDD"):
            return self.middle
        raise ScpiError(-224, f"threshold {position!r}: UPPer, MIDDle or LOWer")


#: The settings of one threshold set, as :func:`threshold_levels` reads them: the method
#: (PERCent, ABSolute, HYSTeresis), the three percentages, the three absolute levels, the
#: hysteresis range and level, and how top and base are found (STANdard, HISTONLY, MINmax,
#: ABSolute with tb_top / tb_base).
DEFAULT_THRESHOLDS = {
    "method": "PERCent", "upper": 90.0, "middle": 50.0, "lower": 10.0,
    "abs_upper": 0.0, "abs_middle": 0.0, "abs_lower": 0.0, "hyst_range": 0.0, "hyst_level": 0.0,
    "tb_method": "STANdard", "tb_top": 0.0, "tb_base": 0.0,
}  # fmt: skip


def threshold_levels(
    v: np.ndarray, spec: dict, hysteresis: float = FREQ_HYSTERESIS, min_band: float = 0.0
) -> Levels | None:
    """The levels a threshold set (``DEFAULT_THRESHOLDS``' keys) gives a record. Top and base:
    STANdard the histogram's modes, else the extremes; HISTONLY the modes only (None when the
    histogram forms no levels); MINmax the extremes; ABSolute the set's ``tb_top`` / ``tb_base``.
    The levels: PERCent of base to top, ABSolute volts, or HYSTeresis ``hyst_level`` +- ``hyst_range``
    (the range is then the crossing band; otherwise ``hysteresis`` of top minus base, never below
    ``min_band``)."""
    s = {**DEFAULT_THRESHOLDS, **{k: v_ for k, v_ in spec.items() if v_ is not None}}
    if v.size == 0:
        return None
    vmax, vmin = float(v.max()), float(v.min())
    tb = str(s["tb_method"]).upper()
    if tb.startswith("ABS"):
        top, base = float(s["tb_top"]), float(s["tb_base"])
    elif tb.startswith("MIN"):
        top, base = vmax, vmin
    else:
        found = top_base(v)
        if found is None and tb.startswith("HIST"):
            return None
        top, base = found if found else (vmax, vmin)
    amp = top - base
    method = str(s["method"]).upper()
    if method.startswith("ABS"):
        upper, middle, lower = (float(s[k]) for k in ("abs_upper", "abs_middle", "abs_lower"))
        band = max(hysteresis * amp, min_band)
    elif method.startswith("HYST"):
        middle, rng = float(s["hyst_level"]), abs(float(s["hyst_range"]))
        upper, lower, band = middle + rng, middle - rng, rng
    else:
        upper, middle, lower = (base + float(s[k]) / 100.0 * amp for k in ("upper", "middle", "lower"))
        band = max(hysteresis * amp, min_band)
    return Levels(top, base, upper, middle, lower, band)


def default_levels(v: np.ndarray, top_base_rule: str, hysteresis: float, min_band: float) -> Levels:
    """A record's levels without a threshold set: 90 / 50 / 10 % of base to top (by ``top_base_rule``)."""
    vmax, vmin = (float(v.max()), float(v.min())) if v.size else (0.0, 0.0)
    found = top_base(v) if top_base_rule == "histogram" else None
    top, base = found if found else (vmax, vmin)
    amp = top - base
    return Levels(
        top, base, base + 0.9 * amp, base + 0.5 * amp, base + 0.1 * amp, max(hysteresis * amp, min_band)
    )


#: Measurements read from the levels and the shape around the first edges (the Infiniium's
#: names): the reference levels themselves, the local overshoot / preshoot / undershoot voltages
#: and the undershoot percent, the area under the record or its first cycle.
LEVEL_MEASUREMENTS = (
    "VUPPer", "VMIDdle", "VLOWer", "VOVershoot", "VPReshoot", "VUNDershoot", "UNDershoot",
    "VOVershoot:FALLing", "VPReshoot:FALLing", "VUNDershoot:FALLing", "UNDershoot:FALLing",
    "AREA", "AREA:CYCLe", "AREA:CYCLe:FALLing",
)  # fmt: skip


def _edges(v: np.ndarray, dt: float, lv: Levels) -> list[tuple[float, int]]:
    return crossings(v, dt, lv.middle, lv.band)


def level_measure(what: str, v: np.ndarray, dt: float, lv: Levels) -> float:
    """One of ``LEVEL_MEASUREMENTS`` (``:FALLing`` selects the falling-edge / negative-pulse form).

    * VUPPer / VMIDdle / VLOWer: the reference levels.
    * VOVershoot: the record's extreme after the first rising (falling) middle crossing, up to the
      next opposite one — the local maximum (minimum) of the overshoot.
    * VPReshoot: the extreme before that edge, back to the previous opposite crossing.
    * VUNDershoot / UNDershoot: within the first positive (negative) pulse, from where the record
      first reaches the top (base) to where the closing edge last leaves the upper (lower) level,
      the lowest (highest) value; UNDershoot is its depth below the top (above the base) in
      percent of top minus base, 0 when the pulse never sags.
    * AREA: the integral of the record over the screen (V·s); AREA:CYCLe over the first complete
      cycle, rising (falling) middle crossing to the next.
    """
    if v.size == 0:
        return math.nan
    word, _, kind = what.partition(":")
    falling = what.endswith("FALLing")
    if word == "VUPPer":
        return lv.upper
    if word == "VMIDdle":
        return lv.middle
    if word == "VLOWer":
        return lv.lower
    if word == "AREA" and kind == "":
        return float(np.sum(v) * dt)
    edges = _edges(v, dt, lv)
    sign = -1 if falling else 1
    idx = [i for i, (_t, d) in enumerate(edges) if d == sign]
    if word == "AREA":
        if len(idx) < 2:
            return math.nan
        # the cycle runs to the next crossing in the same direction
        t0 = edges[idx[0]][0]
        t1 = next((edges[j][0] for j in idx[1:]), None)
        if t1 is None:
            return math.nan
        a, b = math.ceil(t0 / dt), math.floor(t1 / dt)
        return float(np.sum(v[a : b + 1]) * dt)
    if not idx:
        return math.nan
    k = idx[0]
    t_edge = edges[k][0]
    i_edge = round(t_edge / dt)
    if word == "VOVershoot":
        t_end = edges[k + 1][0] if k + 1 < len(edges) else (v.size - 1) * dt
        seg = v[i_edge : round(t_end / dt) + 1]
        return float(seg.max() if not falling else seg.min()) if seg.size else math.nan
    if word == "VPReshoot":
        t_start = edges[k - 1][0] if k > 0 else 0.0
        seg = v[round(t_start / dt) : i_edge + 1]
        return float(seg.min() if not falling else seg.max()) if seg.size else math.nan
    # undershoot: inside the first pulse that begins with this edge, from where it first reaches
    # the top (base) to where its closing edge leaves the upper (lower) level for good
    if k + 1 >= len(edges):
        return math.nan
    seg = v[i_edge : round(edges[k + 1][0] / dt) + 1]
    reached = np.nonzero(seg >= lv.top)[0] if not falling else np.nonzero(seg <= lv.base)[0]
    if not reached.size:
        return math.nan
    inside = np.nonzero(seg >= lv.upper)[0] if not falling else np.nonzero(seg <= lv.lower)[0]
    body = seg[reached[0] : inside[-1] + 1]
    local = float(body.min() if not falling else body.max())
    if word == "VUNDershoot":
        return local
    amp = lv.top - lv.base
    if amp <= 0:
        return math.nan
    depth = (lv.top - local) if not falling else (local - lv.base)
    return max(0.0, depth) / amp * 100.0


def pulse_window_mean(v: np.ndarray, dt: float, lv: Levels, percent: float, positive: bool) -> float:
    """The mean of the first positive (negative) pulse within the window of ``percent`` of its
    width at the middle level, centred in it (the Infiniium's :MEASure:PTOP / PBASe)."""
    edges = _edges(v, dt, lv)
    first = 1 if positive else -1
    for (t, a), (u, b) in itertools.pairwise(edges):
        if a == first and b == -first:
            w = (u - t) * min(max(percent, 0.0), 100.0) / 100.0
            c = (t + u) / 2
            lo, hi = math.ceil((c - w / 2) / dt), math.floor((c + w / 2) / dt)
            seg = v[max(lo, 0) : min(hi, v.size - 1) + 1]
            return float(seg.mean()) if seg.size else math.nan
    return math.nan


def bursts(v: np.ndarray, dt: float, lv: Levels, idle: float) -> list[tuple[float, float]]:
    """The complete bursts of the record: (first edge, last edge) of each run of middle crossings
    whose gaps are shorter than ``idle``, with at least ``idle`` without an edge before and after it
    (the record's ends count as edges' absence)."""
    times = [t for t, _d in _edges(v, dt, lv)]
    if not times:
        return []
    end = (v.size - 1) * dt
    runs: list[list[float]] = [[times[0]]]
    for t in times[1:]:
        if t - runs[-1][-1] >= idle:
            runs.append([t])
        else:
            runs[-1].append(t)
    out = []
    for k, run in enumerate(runs):
        before = run[0] - (runs[k - 1][-1] if k > 0 else 0.0)
        after = (runs[k + 1][0] if k + 1 < len(runs) else end) - run[-1]
        if before >= idle and after >= idle:
            out.append((run[0], run[-1]))
    return out


def burst_measure(what: str, v: np.ndarray, dt: float, lv: Levels, idle: float) -> float:
    """BWIDth (first complete burst's first to last edge), BPERiod (start to start of the first two
    complete bursts) or BINterval (end of the first to start of the second)."""
    found = bursts(v, dt, lv, idle)
    if what == "BWIDth":
        return found[0][1] - found[0][0] if found else math.nan
    if len(found) < 2:
        return math.nan
    if what == "BPERiod":
        return found[1][0] - found[0][0]
    return found[1][0] - found[0][1]


def crossing_voltage(a: np.ndarray, b: np.ndarray) -> float:
    """The voltage where two records of the same timing cross, at the crossing nearest the record's
    centre (linear between samples); NaN when they do not cross."""
    n = min(a.size, b.size)
    d = a[:n] - b[:n]
    zero = np.nonzero(d == 0)[0]
    k = np.nonzero(np.sign(d[:-1]) * np.sign(d[1:]) < 0)[0]
    if zero.size and (not k.size or abs(zero - n / 2).min() <= abs(k - n / 2).min()):
        return float(a[int(zero[np.argmin(np.abs(zero - n / 2))])])  # they meet on a sample
    if not k.size:
        return math.nan
    i = int(k[np.argmin(np.abs(k - n / 2))])
    frac = d[i] / (d[i] - d[i + 1])
    return float(a[i] + (a[i + 1] - a[i]) * frac)


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
    rise_fall: Levels | None = None,
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
    # The real instruments' method (InfiniiVision 2000 X-Series Programmer's Guide,
    # :MEASure:OVERshoot): overshoot = (Vmax - Vtop)/(Vtop - Vbase), the top and the base from
    # the histogram. On a noisy record the extremum rides a few sigma of the noise above the
    # level cluster, so a clean sine reads a few percent: the noise excursion's order, not the
    # signal's shape (#782).
    if what == "OVERshoot":
        return max(0.0, vmax - top) / amp * 100.0
    if what == "PREShoot":
        return max(0.0, base - vmin) / amp * 100.0
    band = hysteresis * amp
    if what in ("RISetime", "FALLtime"):
        lo_v, hi_v, b = (
            (rise_fall.lower, rise_fall.upper, rise_fall.band)
            if rise_fall is not None
            else (base + 0.1 * amp, base + 0.9 * amp, band)
        )
        low = crossings(v, dt, lo_v, b / 4)
        high = crossings(v, dt, hi_v, b / 4)
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
