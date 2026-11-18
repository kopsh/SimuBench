"""Oscilloscope trigger: edge and the advanced types, holdoff, the trigger search an acquisition
runs (split from ``scope``, #1386)."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import numpy as np

from ...engine.component import (
    OpResult,
    PortSpec,
    component,
    op,
)
from ...engine.noise import gaussian, stream_key
from ...engine.signal import Signal, highpass1, lowpass1
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope import FALLING, RISING, _ChannelBound, _upper, trigger_channel
from .scope_acquisition import ScopeAcquisition


@dataclass
class TriggerHit:
    """Where a trigger search found its edge: the instant (seconds after the search's base
    instant, interpolated between samples) and the first record-grid sample at or after it."""

    t_s: float
    index: int


@dataclass
class _Crossing:
    """A crossing of the trigger level found on a search grid: between the samples at ``before``
    and ``at`` (s), ``estimate`` linear between them (``final``: that is the instant; otherwise
    the grid was coarse and the instant is still to be bisected). ``direction`` None: no crossing,
    only how far the search has looked."""

    direction: str | None
    before: float
    at: float
    estimate: float
    final: bool


def _between(t_p: float, v_p: float, t_k: float, v_k: float, level: float) -> float:
    """Where the line from (t_p, v_p) to (t_k, v_k) meets ``level``, kept within the two instants."""
    frac = (level - v_p) / (v_k - v_p) if v_k != v_p else 1.0
    return t_p + min(max(frac, 0.0), 1.0) * (t_k - t_p)


def _period(sig: Signal) -> float | None:
    """The time after which the signal repeats (its lines are harmonics of one frequency), None
    when it has no line."""
    f = np.unique(sig.freq[(sig.freq > 0) & (np.abs(sig.amp) > 0)])
    if not f.size:
        return None
    g = float(f[0])
    for x in f[1:]:
        a, b = float(x), g
        while b > 1e-9 * float(x):  # Euclid, a remainder within rounding of a multiple is none
            r = a - b * round(a / b)
            a, b = b, (0.0 if abs(r) < 1e-9 * a else a % b)
        g = a
    return 1.0 / g


#: The trigger families that compare against both thresholds or the signal's state (#1184): each
#: keeps its own source setting and shares the edge search's machinery.
_ADVANCED = ("runt", "transition", "window", "timeout", "nedge")


@component("scope_trigger")
class ScopeTrigger(_ChannelBound):
    """Edge and pulse-width (glitch) trigger on its own analog path (instrument-authoring.md 8.4,
    G5).

    The trigger looks at the signal before the ADC, through its own ``coupling`` filter (a table
    ``filters`` of high-pass and low-pass corners per coupling value: AC, low- and high-frequency
    reject), a ``reject`` filter alongside it and ``hf_reject`` (filters keyed HF), with its own
    ``noise_rms``. An edge counts once the signal has been beyond the
    ``hysteresis_divs`` band on the far side of the level (``reject_hysteresis_divs`` while
    ``noise_reject`` is on); the instant is interpolated between samples, so a record reports the
    trigger to a fraction of a sample. After a trigger, the next is not taken before ``holdoff``
    seconds (while ``holdoff_on``).

    ``source``, ``level`` and ``slope`` select the edge (``rising`` and ``falling`` list the slope
    names that take each); a level or slope per channel is bound with the channel number written
    ``{n}``: ``$ch{n}.trigger_level``. ``external`` sources read the ``ext`` port (wired from an
    instrument input); ``line`` sources trigger on the rising zero crossings of the mains
    (``line_hz``, phase zero at the session's start). An auto mode looks ``search_windows``
    records ahead on the record's grid and on as far as ``auto_timeout_s`` of virtual time beyond
    (its auto timeout: a signal that repeats within it triggers at any timebase, #1331); one that
    finds nothing still takes only ``search_windows`` records. A normal or single acquisition
    waits up to ``search_horizon_s`` of virtual time for its edge. Beyond the record's grid the
    search takes a coarser one, and an edge found there is bisected on the signal (without the
    trigger's noise) to a thousandth of a record sample.

    ``mode`` ``glitch`` triggers on a pulse by its width instead (signal-chain.md 7, #956): a pulse
    of ``polarity`` runs from one crossing of the level to the next opposite one — the same
    interpolated crossings, hysteresis, coupling, noise and holdoff as the edge trigger — and
    ``qualifier`` compares that time with ``width_low`` and ``width_high``: ``less`` (narrower
    than ``width_high``), ``range`` (between the two) and ``outside`` (narrower than ``width_low``
    or wider than ``width_high``) trigger at the edge that ends the pulse; ``greater`` (wider than
    ``width_low``) triggers at the instant the pulse has lasted ``width_low`` (it need not have
    ended), or with ``greater_at: end`` at the edge that ends it. The
    whole pulse lies after the trigger is armed: one that began within the pre-trigger time or the
    holdoff does not count. ``slope`` plays no part. The search takes the record grid for as many
    samples as ``max_search_points`` allows (fewer for a signal of many lines), then a grid an
    eighth of the narrowest width bound the qualifier uses for as many again, the instants there
    bisected on the signal (without trigger noise); a pulse narrower than that grid can be missed
    there, and an acquisition that finds nothing within that reach waits, looking again at the
    next command (#1000).

    The advanced families (#1184) keep the same machinery on the steady-state signal — an object
    to trigger on without transient signals (C7): ``mode`` ``runt`` fires on a pulse that crosses
    ``low_threshold`` but never ``high_threshold`` (a negative one the mirror below
    ``high_threshold``), ``transition`` on an edge whose time between the two thresholds fails
    ``transition_mode`` (``gthan``/``lthan`` against ``transition_time``, ``range``/``orange``
    against ``transition_range_low``/``transition_range_high``), ``window`` on the signal entering or leaving the band between
    the two thresholds (``window_condition``; ``window_tpoint`` at the boundary instant or after
    ``window_time`` of staying put, and ``inside``/``outside`` conditions with a ``greater``/
    ``less`` duration qualifier), ``timeout`` when the signal has stayed high, low or unchanged
    for ``timeout_time``, and ``nedge`` on the ``nedge_count``-th edge of the family's slope.
    Each family keeps its own ``<family>_source`` (the edge trigger's source until one is set);
    the runt, transition and window comparisons read the per-source ``high_threshold``/
    ``low_threshold`` (``high_by_source``/``low_by_source`` name the sources' settings, like
    ``level_by_source``), and the hysteresis band follows ``hysteresis_setting`` — ``normal``,
    ``nreject`` (the noise-reject band) or ``hsensitivity`` (``hsensitivity_divs``, half the
    normal band when unset). An unset threshold leaves runt, transition and window nothing to
    compare against: the search finds nothing and a waiting acquisition looks again at the next
    command.
    """

    label = "触发"
    label_en = "Trigger"
    about = "在独立模拟通路上的边沿触发和脉宽（毛刺）触发。"
    about_en = "Edge and pulse-width (glitch) trigger on its own analog path."

    ports = {"ext": PortSpec("in", "signal", "external trigger input")}
    defaults = {
        "source": "$trigger.source",  # a channel (C1, CHANnel1), an external or a line source
        "level": "$trigger.level",  # display volts; per channel: $ch{n}.trigger_level
        # Each source's own level setting, by source word (#248), e.g. {CHANnel1: trigger.level1,
        # EXTernal: trigger.level5}; a source not listed uses `level`.
        "level_by_source": {},
        "slope": "$trigger.slope",  # per channel: $ch{n}.trigger_slope
        "rising": RISING,  # slope values that take a rising edge
        "falling": FALLING,  # slope values that take a falling edge
        "search_windows": 4,  # records an auto mode looks ahead on the record's grid
        "auto_timeout_s": 0.05,  # virtual time an auto mode looks ahead for an edge in all, s (#1331)
        "search_horizon_s": 10.0,  # virtual time a normal or single acquisition waits for an edge, s
        "max_search_points": 4_000_000,  # samples a search may take (a long horizon is searched coarser)
        "level_range_divs": None,  # the level stays within +-this many divisions of the source's screen
        "coupling": "DC",  # the trigger path's coupling: a key of filters, or DC
        "reject": "",  # a reject filter alongside the coupling (LFReject, HFReject): a key of filters
        "hf_reject": False,  # on: the filters keyed HF as well (a separate high-frequency reject)
        # coupling or reject -> [[highpass | lowpass, corner Hz], ...], e.g. {AC: [[highpass, 8]]}
        "filters": {},
        "hysteresis_divs": 0.16,  # half the band an edge must cross, in divisions of the source
        "noise_reject": False,  # on: the wider band below
        "reject_hysteresis_divs": 0.5,  # the band while noise_reject is on
        "noise_rms": 0.0,  # the trigger path's own noise, display volts rms
        # the trigger path's noise by the source's division: [[V/div, V rms], ...] (the row of the
        # largest division not above the source's), added to noise_rms (None: none, #1263)
        "noise_table": None,
        "bandwidth": math.inf,  # the trigger path's own bandwidth, Hz: a first-order low-pass (#1263)
        "holdoff_on": True,  # whether holdoff applies (e.g. a holdoff mode that is TIME)
        "holdoff": 0.0,  # s after a trigger before the next one is taken
        "external": ["EX", "EX5", "EXTernal", "EXT"],  # sources read from the ext port
        "line": ["LINE"],  # sources that trigger on the mains
        "line_hz": 50.0,  # mains frequency
        "mode": "edge",  # the trigger type: edge, glitch (a pulse by its width), runt,
        # transition, window, timeout or nedge (the Nth edge); each non-edge family keeps its own
        # source (see <family>_source) while edge and glitch share the main one
        "polarity": "positive",  # glitch: the pulse above the level (positive) or below it (negative)
        # glitch: narrower than width_high (less), wider than width_low (greater), between (range),
        # narrower than width_low or wider than width_high (outside)
        "qualifier": "less",
        "greater_at": "timeout",  # glitch greater: trigger once the pulse lasted width_low (timeout) or at its end
        "width_low": 20e-9,  # glitch: the width a greater or range pulse exceeds, s
        "width_high": 30e-9,  # glitch: the width a less or range pulse stays under, s
        # ---- the advanced families (#1184) ----
        "high_threshold": None,  # display volts, runt/transition/window; per source like the level
        "low_threshold": None,  # display volts, the pair's other half; per source like the level
        "high_by_source": {},  # each source's own threshold setting, by source word, e.g.
        "low_by_source": {},  # {CHANnel1: trigger.hth1, CHANnel2: trigger.hth2}; unlisted: the fallback above
        "hysteresis_setting": "normal",  # :TRIGger:HYSTeresis: normal, nreject (the wider noise-reject
        # band) or hsensitivity (the narrower band below); the band the edges arm and cross within
        "hsensitivity_divs": None,  # the hsensitivity band, in divisions of the source; None: half the normal band
        "runt_source": None,  # the runt family's source word; None: the main source
        "runt_polarity": "positive",  # the pulse between low and high threshold (positive) or below high (negative)
        "runt_qualified": False,  # on: the runt's width must exceed runt_time as well
        "runt_time": 250e-12,  # the time a qualified runt exceeds, s (manual: 250 ps to 30 ns)
        "transition_source": None,  # the transition family's source word; None: the main source
        "transition_type": "risetime",  # rise time (rising edge) or fall time (falling edge)
        "transition_mode": "gthan",  # the violation: gthan/lthan transition_time, or inside
        # (range) / outside (orange) transition_range
        "transition_time": 1e-9,  # s, the greater/less bound
        "transition_range_low": 10e-9,  # s, the minimum of the range modes
        "transition_range_high": 20e-9,  # s, the maximum of the range modes
        "window_source": None,  # the window family's source word; None: the main source
        "window_condition": "enter",  # enter, exit, inside or outside the band; inside/outside take a
        "window_qualifier": "",  # duration qualifier: gthan (held window_time) or lthan (left before window_time)
        "window_time": 1e-6,  # s, the duration the condition is compared with
        "window_tpoint": "boundary",  # enter/exit fire at the boundary instant (boundary) or once the
        # new state has lasted window_time (timeout)
        "timeout_source": None,  # the timeout family's source word; None: the main source
        "timeout_condition": "unchanged",  # high, low or unchanged
        "timeout_time": 1e-3,  # s, the time the state lasts before the trigger
        "nedge_source": None,  # the nth-edge family's source word; None: the main source
        "nedge_slope": None,  # the counted edges' slope; None: the main slope
        "nedge_count": 1,  # the edge that triggers: the nedge_count-th (1 is the first)
    }
    units = {
        "level": "V", "holdoff": "s", "search_horizon_s": "s", "auto_timeout_s": "s", "line_hz": "Hz", "noise_rms": "V", "bandwidth": "Hz",
        "width_low": "s", "width_high": "s", "runt_time": "s", "transition_time": "s",
        "window_time": "s", "timeout_time": "s",
    }  # fmt: skip
    choices = {
        "mode": ("edge", "glitch", "runt", "transition", "window", "timeout", "nedge"),
        "polarity": ("positive", "negative"),
        "qualifier": ("less", "greater", "range", "outside"),
        "greater_at": ("timeout", "end"),
        "hysteresis_setting": ("normal", "nreject", "hsensitivity"),
        "runt_polarity": ("positive", "negative"),
        "transition_type": ("risetime", "falltime"),
        "transition_mode": ("gthan", "lthan", "range", "orange"),
        "window_condition": ("enter", "exit", "inside", "outside"),
        "window_qualifier": ("", "gthan", "lthan"),
        "window_tpoint": ("boundary", "timeout"),
        "timeout_condition": ("high", "low", "unchanged"),
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_ns: int | None = None  # the instant of the latest trigger (holdoff)
        # The latest search and its result: a search is a pure function of its inputs, and the
        # status registers ask the same one again after every command of a line (#1000).
        self._memo: tuple[str, TriggerHit | None] | None = None
        self._source_scale: float | None = None  # the division of the source being searched

    def reset(self) -> None:
        self.last_ns = None
        self._memo = None

    def _search_key(self, sig: Signal, args: tuple[Any, ...], n: int | None) -> str:
        """Everything a search's result depends on: its arguments, the signal's lines, the
        holdoff's anchor and every parameter as it resolves now (the level and slope included)."""
        lines = hashlib.blake2b(digest_size=16)
        for a in (sig.freq, sig.amp, sig.t0_ns):
            lines.update(np.ascontiguousarray(a).tobytes())
        level = self.source_level(n)
        slope = self.channel_param("slope", n) if n is not None else self.param("slope")
        params = []
        for k in sorted(self.defaults):
            try:
                params.append(self.param(k))
            except KeyError:  # bound per channel ({n}): the level and slope above resolve it
                params.append(None)
        return repr((args, sig.dc, lines.hexdigest(), self.last_ns, level, slope, params))

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        """Refuse a level off the source channel's screen by more than ``level_range_divs``
        divisions around its offset (-222, the old value kept)."""
        divs = self.param("level_range_divs")
        if divs is None or key != "level":
            return
        n = self.source_channel()
        acq = next(
            (c for c in (self.owner.components.values() if self.owner else ())
             if isinstance(c, ScopeAcquisition) and c.param("trigger") == self.name),
            None,
        )  # fmt: skip
        if n is None or acq is None:
            return
        scale, offset = acq.scale_offset(n)
        lim = float(divs) * scale
        if not -lim - offset - 1e-12 <= float(new) <= lim - offset + 1e-12:
            raise ScpiError(-222, f"trigger level outside +-{divs} div")

    def select_taps(self) -> list[str] | None:
        """The trigger compares the analog signal at the ADC's input, before the digital filters
        (#1263): per channel, the component wired to the acquisition's converter."""
        if self.owner is None:
            return None
        acq = next(
            (c for c in self.owner.components.values()
             if isinstance(c, ScopeAcquisition) and c.param("trigger") == self.name),
            None,
        )  # fmt: skip
        if acq is None:
            return None
        adc = cast(Any, acq).adc_unit()  # ty sees the @component class as Component
        taps = []
        for n in range(1, self.owner.channel_count + 1):
            src = self.owner.wiring.source(adc.name, f"in{n}")
            if src is not None:
                taps.append(src[0])
        return taps

    def source_channel(self, source: str | None = None) -> int | None:
        """The channel the source names, None for external, line and the like."""
        return trigger_channel(self.param("source") if source is None else source)

    def source_kind(self, source: str | None = None) -> str:
        """channel | external | line | none."""
        src = str(self.param("source") if source is None else source).upper()
        if self.source_channel(src) is not None:
            return "channel"
        if src in _upper(self.param("external")):
            return "external"
        if src in _upper(self.param("line")):
            return "line"
        return "none"

    def family_source(self) -> str:
        """The source word the current mode's family looks at: each advanced family keeps its own
        source setting (#1184), the edge and glitch families share the main one."""
        key = {
            "runt": "runt_source",
            "transition": "transition_source",
            "window": "window_source",
            "timeout": "timeout_source",
            "nedge": "nedge_source",
        }.get(str(self.choice("mode")))
        if key is None:
            return str(self.param("source"))
        own = self.param(key)
        return str(self.param("source")) if own in (None, "") else str(own)

    def threshold(self, which: str, source: str) -> float | None:
        """The ``high`` or ``low`` threshold of a source in display volts: the source's own
        setting (``high_by_source``/``low_by_source``, like the level's), else the fallback
        (``high_threshold``/``low_threshold``), else None — unset, nothing to compare against."""
        by = {str(k).upper(): str(v) for k, v in dict(self.param(f"{which}_by_source") or {}).items()}
        if by and self.owner is not None:
            src = str(source).upper()
            for word, path in by.items():
                if src in (word, V.short_form(word)):
                    return float(self.owner.get_state(path))
        value = self.param(f"{which}_threshold")
        return None if value is None else float(value)

    @op("window_condition")
    def op_window_condition(
        self, condition: Any = None, qualifier: Any = None, query: bool = False, **_: Any
    ) -> OpResult:
        """``:TRIGger:WINDow:CONDition {ENTer | EXIT | INSide[,{GTHan | LTHan}] | OUTSide[,…]}``
        (manual p.1587): the duration qualifier belongs to inside and outside, and the query
        answers it only for those two (the manual's returned format). The words arrive as the raw
        parameter's list."""
        assert self.owner is not None
        cond, qual = self.bound_path("window_condition"), self.bound_path("window_qualifier")
        if cond is None or qual is None:
            raise ScpiError(-300, "the window condition is not bound to a setting")
        if query:
            word = str(self.owner.get_state(cond))
            if word in ("INSide", "OUTSide"):
                word = f"{word},{self.owner.get_state(qual)}"
            return OpResult(",".join(V.short_form(part) for part in word.split(",")).encode())
        given = list(condition) if isinstance(condition, list) else ([] if condition is None else [condition])

        def word(value: Any, options: tuple[str, ...]) -> str:
            raw = value if isinstance(value, bytes) else str(value).encode()
            return V.parse_enum(raw, options)

        if not given:
            raise ScpiError(-109, "a condition is required")
        words = word(given[0], ("ENTer", "EXIT", "INSide", "OUTSide"))
        self.owner.set_state(cond, words)
        if words in ("INSide", "OUTSide"):
            if len(given) > 1:  # the qualifier is optional: given, it replaces the setting
                self.owner.set_state(qual, word(given[1], ("GTHan", "LTHan")))
        elif len(given) > 1:
            raise ScpiError(
                -108, f"the qualifier belongs to {V.short_form('INSide')} and {V.short_form('OUTSide')}"
            )
        return OpResult()

    def level(self) -> float:
        """The trigger level in display volts (the scope screen draws it). With ``level_by_source``
        each source keeps its own level (#248: :TRIGger:LEVel <level>,<source>)."""
        by_source = {str(k).upper(): str(v) for k, v in dict(self.param("level_by_source") or {}).items()}
        if by_source and self.owner is not None:
            src = str(self.param("source")).upper()
            for word, path in by_source.items():
                if src in (word, V.short_form(word)):
                    return float(self.owner.get_state(path))
        level = self.channel_param("level", self.source_channel())
        return 0.0 if level is None else float(level)

    def source_level(self, n: int | None) -> float:
        """The level of the source a search looks at: channel ``n`` (None: the edge source, e.g.
        external). With ``level_by_source`` the channel's own level setting, the same one
        :meth:`level` draws (#1361: a level bound to one channel's setting answered CH1's for
        every source); otherwise ``level`` resolved for the channel."""
        if n is None:
            return self.level()
        by_source = dict(self.param("level_by_source") or {})
        if by_source and self.owner is not None:
            for word, path in by_source.items():
                if trigger_channel(word) == n:
                    return float(self.owner.get_state(str(path)))
        level = self.channel_param("level", n)
        return 0.0 if level is None else float(level)

    def path_signal(self, sig: Signal) -> Signal:
        """The source's signal as the trigger sees it: through the coupling's filters and the trigger
        path's own ``bandwidth`` (#1263)."""
        table = {str(k).upper(): v for k, v in (self.param("filters") or {}).items()}
        keys = [str(self.param("coupling")).upper(), str(self.param("reject") or "").upper()]
        if self.param("hf_reject") in (True, 1, "1", "ON", "on"):
            keys.append("HF")
        for key in keys:
            for kind, corner in table.get(key, []) if key else []:
                sig = sig.transfer(
                    (highpass1 if str(kind).lower() == "highpass" else lowpass1)(float(corner))
                )
        bandwidth = float(self.param("bandwidth"))
        if math.isfinite(bandwidth):
            sig = sig.transfer(lowpass1(bandwidth))
        return sig

    def noise_sigma(self) -> float:
        """The trigger path's noise, display volts rms: ``noise_rms`` and the ``noise_table`` row of
        the source's division (the search sets it, #1263)."""
        sigma = float(self.param("noise_rms") or 0.0)
        rows = sorted((float(r[0]), float(r[1])) for r in self.param("noise_table") or ())
        if rows:
            scale = rows[0][0] if self._source_scale is None else self._source_scale
            sigma += next((rms for div, rms in reversed(rows) if div <= scale * (1 + 1e-9)), rows[0][1])
        return sigma

    def _hysteresis(self, scale: float) -> float:
        setting = str(self.choice("hysteresis_setting") or "normal")
        normal = float(self.param("hysteresis_divs"))
        if setting == "nreject":  # the widest band, whether or not the old noise_reject flag is on
            divs = float(self.param("reject_hysteresis_divs"))
        elif setting == "hsensitivity":  # the narrowest band (a 4 GHz and above waveform)
            own = self.param("hsensitivity_divs")
            divs = normal / 2.0 if own is None else float(own)
        elif self.param("noise_reject") in (True, 1, "1", "ON", "on"):
            divs = float(self.param("reject_hysteresis_divs"))
        else:
            divs = normal
        return divs * scale

    def _noisy(
        self, v: np.typing.NDArray[np.float64], t0: int, offsets: np.typing.NDArray[np.float64]
    ) -> np.typing.NDArray[np.float64]:
        sigma = self.noise_sigma()
        if sigma <= 0 or self.session is None:
            return v
        # Keyed by the absolute instant (ps) of each sample: the same instant, the same noise; a
        # 256 GSa/s grid's samples (3.9 ps apart) each draw their own (#1263).
        key = stream_key(self.session.seed, self.uid, "trigger-noise")
        return v + sigma * gaussian(key, t0 * 1000 + np.round(offsets * 1e12).astype(np.int64))

    def search(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        pre: int,
        points: int,
        n: int | None,
        scale: float,
        horizon_s: float | None = None,
        earliest_s: float = 0.0,
    ) -> TriggerHit | None:
        """The first qualifying edge at least ``pre`` samples after ``t0`` (enough history before
        the trigger), past the holdoff and not before ``earliest_s``, within ``search_windows``
        records or, with ``horizon_s``, that much virtual time. None: no edge."""
        self._source_scale = scale  # the noise table's row follows the source's division (#1263)
        key = self._search_key(sig, (t0, dt, pre, points, n, scale, horizon_s, earliest_s), n)
        if self._memo is not None and self._memo[0] == key:
            return self._memo[1]
        hit = self._search(sig, t0, dt, pre, points, n, scale, horizon_s, earliest_s)
        self._memo = (key, hit)
        return hit

    def _search(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        pre: int,
        points: int,
        n: int | None,
        scale: float,
        horizon_s: float | None,
        earliest_s: float,
    ) -> TriggerHit | None:
        """:meth:`search` itself."""
        kind = self.source_kind(self.family_source())
        start_s = max(pre * dt, earliest_s)
        if self.last_ns is not None and self.param("holdoff_on") in (True, 1, "1", "ON", "on"):
            start_s = max(start_s, (self.last_ns - t0) * 1e-9 + float(self.param("holdoff")))
        span = max(points * int(self.param("search_windows")), pre + 2 * points) * dt
        horizon = max(span, float(horizon_s or 0.0))
        mode = self.choice("mode") or "edge"
        glitch = mode == "glitch"
        if glitch and kind not in ("channel", "external"):
            return None  # the mains has no pulse to time
        if kind == "line":
            period = 1.0 / float(self.param("line_hz"))
            phase = (t0 * 1e-9) % period
            t = math.ceil((start_s + phase) / period - 1e-12) * period - phase
            return TriggerHit(t, math.ceil(t / dt - 1e-9)) if t <= horizon else None
        if kind == "none":
            return None
        level = self.source_level(n)
        slope = str(self.channel_param("slope", n) if n is not None else self.param("slope")).upper()
        rising = slope in _upper(self.param("rising"))
        falling = slope in _upper(self.param("falling"))
        hyst = self._hysteresis(scale)
        sig = self.path_signal(sig)
        # An edge the signal cannot make (its level beyond the signal's whole swing, noise
        # included) is not searched for: a waiting acquisition looks again at every command.
        swing = float(np.abs(sig.amp).sum()) + 6.0 * self.noise_sigma()
        top, bottom = sig.dc + swing, sig.dc - swing
        if mode in _ADVANCED:
            return self._advanced(sig, t0, dt, start_s, span, horizon, mode, n, scale, hyst, top, bottom)
        can_rise = bottom < level - hyst and top >= level
        can_fall = top > level + hyst and bottom <= level
        if glitch:  # a pulse needs both of its edges
            if not (can_rise and can_fall):
                return None
            return self._glitch(sig, t0, dt, start_s, span, horizon, level, hyst)
        if not ((rising and can_rise) or (falling and can_fall)):
            return None
        # The first stretch on the record grid; further on, a grid as coarse as the signal allows
        # (sixteen samples a period of its highest line) and no finer than the record's.
        hit = self._scan(sig, t0, dt, start_s, span, level, hyst, rising, falling, dt)
        if hit is not None or horizon <= span:
            return hit
        step = self._far_step(sig, dt, span, horizon)
        return self._scan(
            sig, t0, dt, max(start_s, span), horizon, level, hyst, rising, falling, step, lead=False
        )

    def _far_step(self, sig: Signal, dt: float, span: float, horizon: float) -> float:
        """The grid beyond the first stretch of a search: as coarse as the signal allows (sixteen
        samples a period of its highest line), no finer than the record's."""
        lines = sig.freq[np.abs(sig.amp) > 1e-6 * max(1e-300, float(np.abs(sig.amp).max(initial=0.0)))]
        f_top = float(lines.max()) if lines.size else 0.0
        step = dt if f_top <= 0 else max(dt, 1.0 / (16.0 * f_top))
        # The work is samples times lines: a signal of many lines is searched on fewer samples.
        budget = float(self.param("max_search_points")) / max(1.0, sig.freq.size / 16.0)
        return max(step, (horizon - span) / budget)

    # ---- the advanced families (#1184): runt, transition, window, timeout, nedge ----------------
    #
    # The same steady-state signal the edge and glitch searches look at, the same hysteresis band
    # and instants; what changes is what qualifies. The pulse families (runt, transition) walk the
    # crossings of both thresholds, the state families (window, timeout) the runs of the signal's
    # state, nedge counts the edge trigger's own crossings.

    def _stretches(
        self, sig: Signal, dt: float, start_s: float, span: float, horizon: float, *bounds: float
    ) -> list[tuple[float, float]]:
        """The grids a pulse- or state-based search walks: the record grid for as many samples as
        ``max_search_points`` allows (fewer for a signal of many lines), then a grid an eighth of
        the narrowest bound the qualification compares, so a coarse grid still bounds the durations
        it judges (#1000)."""
        budget = float(self.param("max_search_points")) / max(1.0, sig.freq.size / 16.0)
        fine = min(span, horizon, start_s + budget * dt)
        stretches = [(fine, dt)]
        if horizon > fine:
            usable = [b for b in bounds if 0 < b < math.inf]
            step = max(dt, min(usable) / 8) if usable else self._far_step(sig, dt, fine, horizon)
            stretches.append((min(horizon, fine + step * budget), step))
        return stretches

    def _level_stream(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stretches: list[tuple[float, float]],
        level: float,
        hyst: float,
    ) -> Iterator[_Crossing]:
        """The crossings of one threshold, in time order, for the pulse families: the side of the
        threshold flips only beyond the hysteresis band, and the flip's own crossing of the level —
        between the last sample on the old side and this one — is what counts. A threshold of a
        pair is reached from its partner, not from beyond its own far band, so the edge search's
        arming rule would miss just the edges the pulse families look for (an edge whose top is
        barely past the threshold never sees the far side of it). The side the signal is on at the
        search's start is carried in: the crossing that put it there lies before the search."""
        side = 0  # -1 below the band, +1 above it, 0 not yet decided
        anchor: tuple[float, float] | None = None  # the last sample decisively on this side
        noisy = self.noise_sigma() > 0
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self._noisy(sig.sample_rel(t0, offsets), t0, offsets)
                # Only the samples beyond the band (decisive) move the side, and a flip is a
                # decisive sample on the other side from the decisive one before it: found with
                # numpy over the chunk, then walked in order (a per-sample loop cost seconds on a
                # noisy UXR record of a million samples).
                decisive = np.where(v > level + hyst, 1, np.where(v < level - hyst, -1, 0))
                nz = np.flatnonzero(decisive)
                if nz.size:
                    sides = decisive[nz]
                    before = np.concatenate(([side], sides[:-1]))
                    # a flip needs a side behind it (which is where its anchor sample came from)
                    flips = np.flatnonzero((before != 0) & (sides != before))
                    if flips.size:
                        ks = nz[flips]
                        # each flip's anchor: the decisive sample before it, in this chunk or the
                        # one carried in from the chunk before
                        prior = nz[np.maximum(flips - 1, 0)]
                        first = flips == 0
                        if first.any():
                            assert anchor is not None
                        t_p = np.where(first, anchor[0] if anchor else 0.0, offsets[prior])
                        v_p = np.where(first, anchor[1] if anchor else 0.0, v[prior])
                        t_ks, v_ks = offsets[ks], v[ks]
                        if noisy:
                            # the line between the two samples carries the estimate (_between)
                            same = v_ks == v_p
                            frac = np.where(same, 1.0, (level - v_p) / np.where(same, 1.0, v_ks - v_p))
                            estimates = t_p + np.clip(frac, 0.0, 1.0) * (t_ks - t_p)
                        for i, j in enumerate(flips):
                            # the level was crossed between the last sample on the old side and this
                            # one; without noise the crossing is bisected on the signal
                            rising = bool(sides[j] > 0)
                            a_t, t_k = float(t_p[i]), float(t_ks[i])
                            if noisy:
                                estimate = float(estimates[i])
                            else:
                                estimate = self._bisect(sig, t0, a_t, t_k, level, rising, dt)
                            yield _Crossing("rise" if rising else "fall", a_t, t_k, estimate, True)
                    side = int(sides[-1])
                    anchor = (float(offsets[nz[-1]]), float(v[nz[-1]]))
                prev = (float(offsets[-1]), float(v[-1]))
                a = prev[0] + step
        yield _Crossing(
            None,
            prev[0] if prev else start_s,
            prev[0] if prev else start_s,
            prev[0] if prev else start_s,
            True,
        )

    def _merged(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stretches: list[tuple[float, float]],
        low: float,
        high: float,
        hyst: float,
    ) -> Iterator[tuple[str, _Crossing]]:
        """The crossings of both thresholds in time order, ``(which, crossing)`` with ``which``
        ``low`` or ``high`` — each level walked by the pulse families' own rule
        (:meth:`_level_stream`). ``""`` carries the progress markers: how far the search has come
        where neither level crossed."""
        lows = self._level_stream(sig, t0, dt, start_s, stretches, low, hyst)
        highs = self._level_stream(sig, t0, dt, start_s, stretches, high, hyst)
        # Each stream's head, taken when needed; an ended stream's head stays None. A plain
        # two-way merge: the per-crossing work of a general one showed on noisy records (#1361).
        lo, hi = next(lows, None), next(highs, None)
        while lo is not None or hi is not None:
            lo_real = lo is not None and lo.direction is not None
            hi_real = hi is not None and hi.direction is not None
            if not lo_real and not hi_real:  # only progress markers ahead: hand them on
                if lo is not None:
                    yield ("", lo)
                    lo = next(lows, None)
                if hi is not None:
                    yield ("", hi)
                    hi = next(highs, None)
                continue
            # the earlier crossing first; at the same instant the high threshold's
            if lo is not None and lo_real and (hi is None or not hi_real or lo.at < hi.at):
                yield ("low", lo)
                lo = next(lows, None)
            elif hi is not None:
                yield ("high", hi)
                hi = next(highs, None)

    def _advanced(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        span: float,
        horizon: float,
        mode: str,
        n: int | None,
        scale: float,
        hyst: float,
        top: float,
        bottom: float,
    ) -> TriggerHit | None:
        """The advanced families (#1184), each on the steady-state signal with the edge search's
        arming and instants: runt and transition on the crossings of the two thresholds, window
        and timeout on the runs of the signal's state, nedge on the counted crossings."""
        source = self.family_source()
        level = self.source_level(n)
        noiseless = self.noise_sigma() <= 0
        period = _period(sig) if noiseless else None
        high = self.threshold("high", source)
        low = self.threshold("low", source)

        if mode == "nedge":
            slope = self.param("nedge_slope")
            if slope in (None, ""):
                slope = str(self.channel_param("slope", n) if n is not None else self.param("slope"))
            slope = str(slope).upper()
            rising = slope in _upper(self.param("rising"))
            falling = slope in _upper(self.param("falling"))
            directions = [d for d, on in (("rise", rising), ("fall", falling)) if on]
            if not directions or not (
                (rising and bottom < level - hyst and top >= level)
                or (falling and top > level + hyst and bottom <= level)
            ):
                return None
            count = max(1, int(self.param("nedge_count")))
            if period is not None:
                # The edges repeat each period: what one period shows is what every period shows,
                # so the nth lands in its own period — the horizon reaches that far (within the
                # sample budget; a search that cannot look that far waits and looks again at the
                # next command, as the glitch search does).
                probe_stop = min(span, 2.0 * period + dt)
                probe = [
                    c
                    for c in self._edges(sig, t0, dt, start_s, [(probe_stop, dt)], level, hyst)
                    if c.direction in directions
                ]
                per_period = max(len(probe) / max(probe_stop / period, 1.0), 1.0)
                horizon = min(
                    horizon,
                    max(probe_stop, start_s + period * math.ceil(count / per_period) + 2.0 * dt),
                )
            seen = 0
            for c in self._edges(
                sig, t0, dt, start_s, self._stretches(sig, dt, start_s, span, horizon), level, hyst
            ):
                if c.direction not in directions:
                    continue
                seen += 1
                if seen >= count:
                    if not c.final:  # found on the coarse grid: bisected on the signal
                        c.estimate = self._bisect(sig, t0, c.before, c.at, level, c.direction == "rise", dt)
                        c.final = True
                    return TriggerHit(c.estimate, math.ceil(c.estimate / dt - 1e-9))
            return None

        if mode == "timeout":
            limit = float(self.param("timeout_time"))
            cond = str(self.choice("timeout_condition") or "unchanged")
            if limit <= 0:
                return None
            if period is not None:  # one period shows every state run there is
                horizon = min(horizon, start_s + period + limit + 2.0 * dt)
            for state, entry, upto, _final in self._runs(
                sig, t0, dt, start_s, self._stretches(sig, dt, start_s, span, horizon, limit), level, hyst
            ):
                if cond != "unchanged" and state != cond:
                    continue
                begin = max(entry, start_s)  # armed here: the run counts from the arming
                if upto - begin >= limit:
                    return TriggerHit(begin + limit, math.ceil((begin + limit) / dt - 1e-9))
            return None

        if mode == "window":
            if high is None or low is None:  # no thresholds set: nothing to enter or leave
                return None
            high, low = float(high), float(low)
            if low > high:
                low, high = high, low
            condition = str(self.choice("window_condition") or "enter")
            wanted = "inside" if condition in ("enter", "inside") else "outside"
            qualifier = str(self.choice("window_qualifier") or "")
            tpoint = str(self.choice("window_tpoint") or "boundary")
            limit = float(self.param("window_time"))
            if not (top >= low and bottom <= high) or not (bottom < low - hyst or top > high + hyst):
                return None  # the signal never reaches the band, or never leaves it
            if period is not None:
                horizon = min(horizon, start_s + 2.0 * period + limit + 2.0 * dt)

            def instant(c: tuple) -> float:
                bound, d, before, at, estimate, final = c
                if final:
                    return estimate
                return self._bisect(sig, t0, before, at, bound, d == "rise", dt)

            for state, entry, exit_c, upto, _final in self._runs_window(
                sig, t0, dt, start_s, self._stretches(sig, dt, start_s, span, horizon, limit), low, high, hyst
            ):
                if state != wanted:
                    continue
                carried = entry is None  # the signal already sat in this state at the arming
                begin = (
                    start_s if carried else max(instant(entry), start_s)
                )  # armed here: the transition counts from then
                left = instant(exit_c) if exit_c is not None else None
                if condition in ("enter", "exit") and tpoint == "boundary":
                    if carried:
                        continue  # being in the state already is not a transition of the search
                    return TriggerHit(begin, math.ceil(begin / dt - 1e-9))
                if qualifier == "lthan":
                    # left before the time: the exit instant is the trigger
                    if left is not None and left - begin < limit:
                        return TriggerHit(left, math.ceil(left / dt - 1e-9))
                    continue
                held = (left if left is not None else upto) - begin
                if held >= limit:  # boundary timeout, or a greater inside/outside: still there
                    return TriggerHit(begin + limit, math.ceil((begin + limit) / dt - 1e-9))
            return None

        # ---- runt and transition: the crossings of both thresholds in time order ----------------
        if high is None or low is None:
            return None
        high, low = float(high), float(low)
        if low > high:
            low, high = high, low
        polarity = str(self.choice("runt_polarity") or "positive")
        ttype = str(self.choice("transition_type") or "risetime")
        if mode == "runt":
            opens = ("low", "rise") if polarity == "positive" else ("high", "fall")
            closes = ("low", "fall") if polarity == "positive" else ("high", "rise")
            rejects = {("high", "rise")} if polarity == "positive" else {("low", "fall")}
            entry = (
                (bottom < low - hyst and top >= low)
                if polarity == "positive"
                else (top > high + hyst and bottom <= high)
            )
            if not entry:
                return None
            qualified = V.flag(self.param("runt_qualified"))
            bounds = (float(self.param("runt_time")),)
        else:
            opens = ("low", "rise") if ttype == "risetime" else ("high", "fall")
            closes = ("high", "rise") if ttype == "risetime" else ("low", "fall")
            rejects = (
                {("low", "fall"), ("high", "fall")}
                if ttype == "risetime"
                else {("high", "rise"), ("low", "rise")}
            )
            entry = (
                (bottom < low - hyst and top > high)
                if ttype == "risetime"
                else (top > high + hyst and bottom < low)
            )
            if not entry:
                return None
            qualified = False
            tmode = str(self.choice("transition_mode") or "greater")
            t_limit = float(self.param("transition_time"))
            t_lo = float(self.param("transition_range_low"))
            t_hi = float(self.param("transition_range_high"))
            bounds = (t_limit,) if tmode in ("gthan", "lthan") else (t_lo, t_hi)
        if period is not None:  # a signal that repeats shows every pulse within two periods
            horizon = min(horizon, start_s + 2.0 * period + 2.0 * dt)
        levels = {"low": low, "high": high}

        def instant(c: _Crossing, which: str) -> float:
            if not c.final:  # found on the coarse grid: bisected on the signal
                c.estimate = self._bisect(sig, t0, c.before, c.at, levels[which], c.direction == "rise", dt)
                c.final = True
            return c.estimate

        def hit(t: float) -> TriggerHit:
            return TriggerHit(t, math.ceil(t / dt - 1e-9))

        began: tuple[str, _Crossing] | None = None  # the opening crossing of the pulse under way
        for which, c in self._merged(
            sig, t0, dt, start_s, self._stretches(sig, dt, start_s, span, horizon, *bounds), low, high, hyst
        ):
            if which == "":  # progress: a runt still open has lasted its time
                if mode == "runt" and qualified and began is not None:
                    begun = instant(began[1], began[0])
                    if c.at - begun > float(self.param("runt_time")):
                        return hit(begun + float(self.param("runt_time")))
                continue
            key = (which, str(c.direction))
            if began is not None and key in rejects:
                began = None
                continue
            if key == closes and began is not None:
                begun = instant(began[1], began[0])
                done = instant(c, which)
                if mode == "runt":
                    if not qualified or done - begun > float(self.param("runt_time")):
                        return hit(done)
                else:
                    edge_time = done - begun
                    inside = t_lo < edge_time < t_hi
                    if (
                        (tmode == "gthan" and edge_time > t_limit)
                        or (tmode == "lthan" and edge_time < t_limit)
                        or (tmode == "range" and inside)
                        or (tmode == "orange" and not inside)
                    ):
                        return hit(done)
                began = None
                continue
            if key == opens:
                began = (which, c)
            elif key == closes:
                began = None  # a close without an open: whatever ran before is over
        return None

    def _runs(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stretches: list[tuple[float, float]],
        level: float,
        hyst: float,
    ) -> Iterator[tuple[str, float, float, bool]]:
        """The runs of the signal's state against ``level`` (the timeout family): high above
        ``level + hyst``, low below ``level - hyst``, in between keeps the previous state (the
        hysteresis band; at the very start, with no state behind it, the nearer side decides).
        Yields ``(state, entry, upto, final)`` when a run closes — ``entry`` where it began,
        ``upto`` where it ended — and once more for the run still open where the search ends."""
        state = ""
        entry = 0.0
        anchor: tuple[float, float] | None = None  # the last sample decisively in this state
        noisy = self.noise_sigma() > 0
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self._noisy(sig.sample_rel(t0, offsets), t0, offsets)
                for k in range(count):
                    t_k, v_k = float(offsets[k]), float(v[k])
                    if v_k > level + hyst:
                        decisive = "high"
                    elif v_k < level - hyst:
                        decisive = "low"
                    else:
                        decisive = ""  # the hysteresis band keeps the state
                    new = decisive or state or ("high" if v_k >= level else "low")
                    if state and new != state:
                        yield (state, entry, t_k, True)
                        # the state truly began where the band edge was crossed, between the last
                        # sample on the other side and this one; bisected without noise, the line
                        # between the two samples carrying the estimate with it
                        bound = level + hyst if new == "high" else level - hyst
                        if anchor is not None and prev is not None:
                            if noisy:
                                entry = _between(anchor[0], anchor[1], t_k, v_k, bound)
                            else:
                                entry = self._bisect(sig, t0, anchor[0], t_k, bound, new == "high", dt)
                        else:
                            entry = t_k
                    elif not state:
                        entry = t_k
                    state = new
                    if decisive:
                        anchor = (t_k, v_k)  # a sample of the state the next flip is measured from
                    prev = (t_k, v_k)
                prev = (float(offsets[-1]), float(v[-1]))
                a = prev[0] + step
        if state:
            yield (state, entry, prev[0] if prev else entry, True)

    def _runs_window(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stretches: list[tuple[float, float]],
        low: float,
        high: float,
        hyst: float,
    ) -> Iterator[tuple[str, tuple | None, tuple | None, float, bool]]:
        """The runs of the signal inside and outside the band ``[low, high]`` (the window family):
        inside between the bounds, outside beyond either bound widened by the hysteresis, the ring
        between keeping the previous state. Yields ``(state, entry, exit, upto, final)`` when a run
        closes — ``exit`` the crossing out of it — and once for the run still open where the search
        ends (``exit`` None, ``upto`` how far the search has come). An entry or exit is
        ``(bound, direction, before, at, estimate, final)``: the crossing of the bound the signal
        actually crossed, its instant bisected later on the coarse grids."""
        state = ""  # "" until a sample decides one way or the other
        entry: tuple | None = None
        anchor: tuple[float, float] | None = None  # the last sample decisively in this state
        noisy = self.noise_sigma() > 0
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self._noisy(sig.sample_rel(t0, offsets), t0, offsets)
                for k in range(count):
                    t_k, v_k = float(offsets[k]), float(v[k])
                    if low <= v_k <= high:
                        decisive = "inside"
                    elif v_k > high + hyst or v_k < low - hyst:
                        decisive = "outside"
                    else:
                        decisive = ""  # the ring between the band and its hysteresis: stays
                    if decisive and decisive != state:
                        # the bound was crossed between the last sample decisively on the other
                        # side and this one (the ring in between never says so); the estimate is
                        # the line between the two samples, bisected by the consumer on the exact
                        # signal when there is no noise (final False). The run the signal already
                        # sits in at the search's start has no crossing behind it (entry None):
                        # being in the state is not the same as entering it.
                        cross = (
                            self._window_crossing(anchor, (t_k, v_k), low, high, noisy)
                            if anchor is not None
                            else None
                        )
                        if state:
                            yield (state, entry, cross, prev[0] if prev else t_k, noisy)
                        entry = cross
                        state = decisive
                    if decisive:
                        anchor = (t_k, v_k)  # a sample of the state the next crossing is found from
                    prev = (t_k, v_k)
                prev = (float(offsets[-1]), float(v[-1]))
                a = prev[0] + step
        if state and entry is not None:
            yield (state, entry, None, prev[0] if prev else start_s, True)

    @staticmethod
    def _window_crossing(
        before: tuple[float, float] | None, at: tuple[float, float], low: float, high: float, final: bool
    ) -> tuple:
        """The crossing into or out of the window band between the samples ``before`` and ``at``:
        into the band from above is the high bound falling, from below the low bound rising; out of
        it upward the high bound rising, downward the low bound falling."""
        (t_k, v_k) = at
        if before is not None:
            t_p, v_p = before
            if low <= v_k <= high:  # into the band
                bound, d = (high, "fall") if v_p > high else (low, "rise")
            elif v_k > high:  # out of it upward
                bound, d = high, "rise"
            else:  # out of it downward
                bound, d = low, "fall"
            return (bound, d, t_p, t_k, _between(t_p, v_p, t_k, v_k, bound), final)
        return (low, "rise", t_k, t_k, t_k, True)

    def _glitch(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        span: float,
        horizon: float,
        level: float,
        hyst: float,
    ) -> TriggerHit | None:
        """The first pulse of ``polarity`` whose width meets ``qualifier``, among the crossings from
        ``start_s`` on: the instant its closing edge crosses the level (less, range) or the instant
        it has lasted ``width_low`` (greater)."""
        opens, closes = ("rise", "fall") if self.choice("polarity") == "positive" else ("fall", "rise")
        qualifier = self.choice("qualifier")
        low, high = float(self.param("width_low")), float(self.param("width_high"))
        # A signal that repeats shows every pulse it has within two periods: none there, none ever
        # (a waiting acquisition searches again at every command).
        period = _period(sig) if self.noise_sigma() <= 0 else None
        if period is not None:
            horizon = min(horizon, max(start_s, 0.0) + 2.0 * period + 2.0 * dt)
        # The work is samples times lines, as for an edge's far search: the record grid for as
        # many samples as max_search_points allows (fewer for a signal of many lines), then a grid
        # an eighth of the narrowest width bound the qualifier uses for as many again. A grid
        # coarser than the widths judged would see pulses with a narrow gap between them as one
        # wide pulse (#1000); what lies beyond is looked at again at the next command.
        budget = float(self.param("max_search_points")) / max(1.0, sig.freq.size / 16.0)
        fine = min(span, horizon, start_s + budget * dt)
        stretches = [(fine, dt)]
        if horizon > fine:
            used = {"less": (high,), "greater": (low,)}.get(qualifier, (low, high))  # range, outside
            bounds = [w for w in used if 0 < w < math.inf]
            step = max(dt, min(bounds) / 8) if bounds else self._far_step(sig, dt, fine, horizon)
            stretches.append((min(horizon, fine + step * budget), step))

        def instant(c: _Crossing) -> float:
            if not c.final:  # found on the coarse grid: to a thousandth of a record sample
                c.estimate = self._bisect(sig, t0, c.before, c.at, level, c.direction == "rise", dt)
                c.final = True
            return c.estimate

        def hit(t: float) -> TriggerHit:
            return TriggerHit(t, math.ceil(t / dt - 1e-9))

        at_end = qualifier == "outside" or (qualifier == "greater" and self.choice("greater_at") == "end")

        def may_qualify(c: _Crossing, o: _Crossing) -> bool:
            """Whether the pulse from ``o`` to ``c`` can meet the qualifier, judged on the grid
            (the shortest and longest widths the two crossings allow)."""
            shortest, longest = c.before - o.at, c.at - o.before
            if qualifier == "less":
                return shortest < high
            if qualifier == "range":
                return shortest < high and longest > low
            if qualifier == "greater":
                return longest > low
            return shortest < low or longest > high  # outside

        def qualifies(width: float) -> bool:
            if qualifier == "less":
                return width < high
            if qualifier == "range":
                return low < width < high
            if qualifier == "greater":
                return width > low
            return width < low or width > high  # outside

        opened: _Crossing | None = None  # the opening edge of the pulse under way
        for c in self._edges(sig, t0, dt, start_s, stretches, level, hyst):
            if opened is not None and qualifier == "greater" and not at_end and c.at - opened.before > low:
                # Nothing closed the pulse before it had lasted width_low: ``c`` is the next
                # crossing, or how far the search has looked.
                begun = instant(opened)
                if (c.at if c.direction is None else instant(c)) - begun > low:
                    return hit(begun + low)
            if c.direction == opens:
                opened = c
            elif c.direction == closes:
                # The grid already bounds the width; the instants are refined only for a pulse
                # that may qualify.
                if (
                    opened is not None
                    and (qualifier != "greater" or at_end)
                    and may_qualify(c, opened)
                    and qualifies(instant(c) - instant(opened))
                ):
                    return hit(instant(c))
                opened = None
        return None

    def _edges(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stretches: list[tuple[float, float]],
        level: float,
        hyst: float,
    ) -> Iterator[_Crossing]:
        """The crossings of the level from ``start_s`` on, in time order, each found by the edge
        trigger's own rule (:meth:`_scan`: armed beyond the hysteresis band on the far side, taken
        at the first sample on the level's other side). ``stretches`` are (stop, step) grids
        searched one after the other, the first from one step before ``start_s`` (a sample that
        may arm but not cross). After every chunk of samples a crossing of no direction says how
        far the search has come."""
        armed = {"rise": False, "fall": False}
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            # A crossing on a coarser grid than the record's is bisected on the signal (#1331).
            final = not step > dt * (1 + 1e-9)
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self._noisy(sig.sample_rel(t0, offsets), t0, offsets)
                live = offsets >= start_s - 1e-15
                far = {"rise": np.nonzero(v < level - hyst)[0], "fall": np.nonzero(v > level + hyst)[0]}
                cross = {
                    "rise": np.nonzero((v >= level) & live)[0],
                    "fall": np.nonzero((v <= level) & live)[0],
                }
                at = {"rise": 0, "fall": 0}  # where each direction looks on from
                while True:
                    found: tuple[int, str] | None = None
                    for d in ("rise", "fall"):
                        if not armed[d]:
                            i = int(np.searchsorted(far[d], at[d]))
                            if i == far[d].size:
                                continue
                            armed[d], at[d] = True, int(far[d][i]) + 1
                        j = int(np.searchsorted(cross[d], at[d]))
                        if j < cross[d].size and (found is None or int(cross[d][j]) < found[0]):
                            found = (int(cross[d][j]), d)
                    if found is None:
                        break
                    k, d = found
                    armed[d], at[d] = False, k + 1
                    t_k, v_k = float(offsets[k]), float(v[k])
                    t_p, v_p = (float(offsets[k - 1]), float(v[k - 1])) if k > 0 else (prev or (t_k, v_k))
                    yield _Crossing(d, t_p, t_k, _between(t_p, v_p, t_k, v_k, level), final)
                prev = (float(offsets[-1]), float(v[-1]))
                yield _Crossing(None, prev[0], prev[0], prev[0], True)
                a = prev[0] + step

    def _instant(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        step: float,
        offsets: np.typing.NDArray[np.float64],
        v: np.typing.NDArray[np.float64],
        k: int,
        prev: tuple[float, float] | None,
        level: float,
        rising: bool,
    ) -> float:
        """The instant of the crossing taken at sample ``k``: between it and the sample before
        (``prev``: the last one of the chunk before), linear on the record's grid, bisected on a
        coarser one on the signal without the trigger's noise: a straight line between samples
        that may lie periods apart says nothing of where the edge is (#1331)."""
        t_k, v_k = float(offsets[k]), float(v[k])
        t_p, v_p = (float(offsets[k - 1]), float(v[k - 1])) if k > 0 else (prev or (t_k, v_k))
        if step > dt * (1 + 1e-9):
            return self._bisect(sig, t0, t_p, t_k, level, rising, dt)
        return _between(t_p, v_p, t_k, v_k, level)

    @staticmethod
    def _bisect(sig: Signal, t0: int, lo: float, hi: float, level: float, rising: bool, dt: float) -> float:
        """The crossing in (lo, hi] found on a coarse grid, to a thousandth of a record sample."""
        while hi - lo > dt * 1e-3:
            mid = (lo + hi) / 2
            v = float(sig.sample_rel(t0, np.array([mid]))[0])
            if (v >= level) == rising:
                hi = mid
            else:
                lo = mid
        return hi

    def _scan(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stop_s: float,
        level: float,
        hyst: float,
        rising: bool,
        falling: bool,
        step: float,
        lead: bool = True,
    ) -> TriggerHit | None:
        """Search [start_s, stop_s] on a grid of ``step`` (with ``lead``, its first sample one step
        before start_s, which may arm but not trigger). An edge is armed once the signal has been beyond
        the band on the far side of the level; it triggers at the first sample on the level's other
        side, the instant interpolated between that sample and the one before."""
        # Chunks grow from a few thousand samples: the first edge is usually early, and sampling
        # the whole window of a many-line signal up front costs more than the rest of the record.
        chunk = 1 << 12
        directions = [d for d, on in (("rise", rising), ("fall", falling)) if on]
        armed = dict.fromkeys(directions, False)
        prev: tuple[float, float] | None = None
        a = start_s - step if lead else start_s
        while a <= stop_s:
            count = min(chunk, max(2, math.ceil((stop_s - a) / step) + 1))
            chunk = min(chunk * 2, 1 << 18)
            offsets = a + step * np.arange(count, dtype=np.float64)
            v = self._noisy(sig.sample_rel(t0, offsets), t0, offsets)
            best: float | None = None
            for d in directions:
                far = v < level - hyst if d == "rise" else v > level + hyst
                cross = (v >= level if d == "rise" else v <= level) & (offsets >= start_s - 1e-15)
                if armed[d]:
                    after = 0
                else:
                    first = np.nonzero(far)[0]
                    if not first.size:
                        continue
                    after = int(first[0]) + 1
                    armed[d] = True
                hits = np.nonzero(cross[after:])[0]
                if not hits.size:
                    continue
                k = after + int(hits[0])
                t = self._instant(sig, t0, dt, step, offsets, v, k, prev, level, d == "rise")
                best = t if best is None else min(best, t)
            if best is not None:
                return TriggerHit(best, math.ceil(best / dt - 1e-9))
            prev = (float(offsets[-1]), float(v[-1]))
            a = float(offsets[-1]) + step
        return None
