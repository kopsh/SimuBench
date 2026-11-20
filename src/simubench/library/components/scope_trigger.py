"""Oscilloscope trigger: edge and the advanced types, holdoff, the trigger search an acquisition
runs (split from ``scope``, #1386)."""

from __future__ import annotations

import hashlib
import math
from typing import Any, cast

import numpy as np

from ...engine.component import (
    OpResult,
    PortSpec,
    component,
    op,
)
from ...engine.signal import Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope import FALLING, RISING, _ChannelBound, _upper
from .scope_acquisition import ScopeAcquisition
from .scope_trigger_advanced import Advanced
from .scope_trigger_crossings import Crossings
from .scope_trigger_host import TriggerHit as TriggerHit
from .scope_trigger_host import _between as _between
from .scope_trigger_host import _Crossing as _Crossing
from .scope_trigger_host import _period as _period
from .scope_trigger_runs import Runs
from .scope_trigger_sources import Sources

#: The trigger families that compare against both thresholds or the signal's state (#1184): each
#: keeps its own source setting and shares the edge search's machinery.
_ADVANCED = ("runt", "transition", "window", "timeout", "nedge")


@component("scope_trigger")
class ScopeTrigger(_ChannelBound):
    """Edge and pulse-width (glitch) trigger on its own analog path.

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
    (its auto timeout: a signal that repeats within it triggers at any timebase); one that
    finds nothing still takes only ``search_windows`` records. A normal or single acquisition
    waits up to ``search_horizon_s`` of virtual time for its edge. Beyond the record's grid the
    search takes a coarser one, and an edge found there is bisected on the signal (without the
    trigger's noise) to a thousandth of a record sample.

    ``mode`` ``glitch`` triggers on a pulse by its width instead: a pulse
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
    next command.

    The advanced families keep the same machinery on the steady-state signal — an object
    to trigger on without transient signals: ``mode`` ``runt`` fires on a pulse that crosses
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
        # Each source's own level setting, by source word, e.g. {CHANnel1: trigger.level1,
        # EXTernal: trigger.level5}; a source not listed uses `level`.
        "level_by_source": {},
        "slope": "$trigger.slope",  # per channel: $ch{n}.trigger_slope
        "rising": RISING,  # slope values that take a rising edge
        "falling": FALLING,  # slope values that take a falling edge
        "search_windows": 4,  # records an auto mode looks ahead on the record's grid
        "auto_timeout_s": 0.05,  # virtual time an auto mode looks ahead for an edge in all, s
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
        # largest division not above the source's), added to noise_rms (None: none)
        "noise_table": None,
        "bandwidth": math.inf,  # the trigger path's own bandwidth, Hz: a first-order low-pass
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
        # ---- the advanced families ----
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
        self.sources = Sources(self)
        self.crossings = Crossings(self)
        self.runs = Runs(self)
        self.advanced = Advanced(self)
        self.last_ns: int | None = None  # the instant of the latest trigger (holdoff)
        # The latest search and its result: a search is a pure function of its inputs, and the
        # status registers ask the same one again after every command of a line (#1000).
        self._memo: tuple[str, TriggerHit | None] | None = None

    def reset(self) -> None:
        self.last_ns = None
        self._memo = None

    def _search_key(self, sig: Signal, args: tuple[Any, ...], n: int | None) -> str:
        """Everything a search's result depends on: its arguments, the signal's lines, the
        holdoff's anchor and every parameter as it resolves now (the level and slope included)."""
        lines = hashlib.blake2b(digest_size=16)
        for a in (sig.freq, sig.amp, sig.t0_ns):
            lines.update(np.ascontiguousarray(a).tobytes())
        level = self.sources.source_level(n)
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
        n = self.sources.source_channel()
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

    def get_state(self, path: str) -> Any:
        """A setting's value, read from the instrument (#1724: the blocks' name for it)."""
        assert self.owner is not None
        return self.owner.get_state(path)

    def select_taps(self) -> list[str] | None:
        """The components whose outputs the trigger search watches per channel: those wired to the
        acquisition's converter's inputs, ahead of a track-and-hold sampler (#1579). The wiring
        and the channel count are read here — the instrument's own knowledge, not the blocks'
        (#1724)."""
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
            # the comparator splits off ahead of a track-and-hold sampler (#1579): tap what feeds it
            while src is not None and getattr(self.owner.components.get(src[0]), "track_and_hold", False):
                src = self.owner.wiring.source(src[0], "in")
            if src is not None:
                taps.append(src[0])
        return taps

    def source_channel(self, source: str | None = None) -> int | None:
        """Delegates to :meth:`Sources.source_channel`."""
        return self.sources.source_channel(source)

    def source_kind(self, source: str | None = None) -> str:
        """Delegates to :meth:`Sources.source_kind`."""
        return self.sources.source_kind(source)

    def family_source(self) -> str:
        """Delegates to :meth:`Sources.family_source`."""
        return self.sources.family_source()

    def threshold(self, which: str, source: str) -> float | None:
        """Delegates to :meth:`Sources.threshold`."""
        return self.sources.threshold(which, source)

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
        """Delegates to :meth:`Sources.level`."""
        return self.sources.level()

    def source_level(self, n: int | None) -> float:
        """Delegates to :meth:`Sources.source_level`."""
        return self.sources.source_level(n)

    def path_signal(self, sig: Signal) -> Signal:
        """Delegates to :meth:`Sources.path_signal`."""
        return self.sources.path_signal(sig)

    def noise_sigma(self) -> float:
        """Delegates to :meth:`Sources.noise_sigma`."""
        return self.sources.noise_sigma()

    def _hysteresis(self, scale: float) -> float:
        """Delegates to :meth:`Crossings.hysteresis`."""
        return self.crossings.hysteresis(scale)

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
        noise_factor: float = 1.0,
    ) -> TriggerHit | None:
        """The first qualifying edge at least ``pre`` samples after ``t0`` (enough history before
        the trigger), past the holdoff and not before ``earliest_s``, within ``search_windows``
        records or, with ``horizon_s``, that much virtual time. None: no edge. ``noise_factor``
        scales the noise table as the source channel's front end scales its own (#1427)."""
        self.sources.source_scale = scale  # the noise table's row follows the source's division (#1263)
        self.sources.noise_factor = noise_factor
        key = self._search_key(sig, (t0, dt, pre, points, n, scale, horizon_s, earliest_s, noise_factor), n)
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
        kind = self.sources.source_kind(self.sources.family_source())
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
        level = self.sources.source_level(n)
        slope = str(self.channel_param("slope", n) if n is not None else self.param("slope")).upper()
        rising = slope in _upper(self.param("rising"))
        falling = slope in _upper(self.param("falling"))
        hyst = self.crossings.hysteresis(scale)
        sig = self.sources.path_signal(sig)
        # An edge the signal cannot make (its level beyond the signal's whole swing, noise
        # included) is not searched for: a waiting acquisition looks again at every command.
        swing = float(np.abs(sig.amp).sum()) + 6.0 * self.sources.noise_sigma()
        top, bottom = sig.dc + swing, sig.dc - swing
        if mode in _ADVANCED:
            return self.advanced.advanced(
                sig, t0, dt, start_s, span, horizon, mode, n, scale, hyst, top, bottom
            )
        can_rise = bottom < level - hyst and top >= level
        can_fall = top > level + hyst and bottom <= level
        if glitch:  # a pulse needs both of its edges
            if not (can_rise and can_fall):
                return None
            return self.runs.glitch(sig, t0, dt, start_s, span, horizon, level, hyst)
        if not ((rising and can_rise) or (falling and can_fall)):
            return None
        # The first stretch on the record grid; further on, a grid as coarse as the signal allows
        # (sixteen samples a period of its highest line) and no finer than the record's.
        hit = self.crossings.scan(sig, t0, dt, start_s, span, level, hyst, rising, falling, dt)
        if hit is not None or horizon <= span:
            return hit
        step = self.crossings.far_step(sig, dt, span, horizon)
        return self.crossings.scan(
            sig, t0, dt, max(start_s, span), horizon, level, hyst, rising, falling, step, lead=False
        )

    # ---- the advanced families (#1184): runt, transition, window, timeout, nedge ----------------
    #
    # The same steady-state signal the edge and glitch searches look at, the same hysteresis band
    # and instants; what changes is what qualifies. The pulse families (runt, transition) walk the
    # crossings of both thresholds, the state families (window, timeout) the runs of the signal's
    # state, nedge counts the edge trigger's own crossings.

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
        """Delegates to :meth:`Crossings.scan`."""
        return self.crossings.scan(sig, t0, dt, start_s, stop_s, level, hyst, rising, falling, step, lead)
