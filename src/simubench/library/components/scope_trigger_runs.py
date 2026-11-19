"""Pulse-width, window and glitch triggers: the runs a level makes, the window crossings, glitches and edges. Part of ``ScopeTrigger`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np

from ...engine.signal import Signal
from .scope_trigger_host import TriggerHit, TriggerHost, _between, _Crossing, _period


class Runs:
    def __init__(self, host: TriggerHost) -> None:
        self.host = host

    def runs(
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
        noisy = self.host.sources.noise_sigma() > 0
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self.host.crossings.noisy(sig.sample_rel(t0, offsets), t0, offsets)
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
                                entry = self.host.crossings.bisect(
                                    sig, t0, anchor[0], t_k, bound, new == "high", dt
                                )
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

    def runs_window(
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
        noisy = self.host.sources.noise_sigma() > 0
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self.host.crossings.noisy(sig.sample_rel(t0, offsets), t0, offsets)
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

    def glitch(
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
        opens, closes = ("rise", "fall") if self.host.choice("polarity") == "positive" else ("fall", "rise")
        qualifier = self.host.choice("qualifier")
        low, high = float(self.host.param("width_low")), float(self.host.param("width_high"))
        # A signal that repeats shows every pulse it has within two periods: none there, none ever
        # (a waiting acquisition searches again at every command).
        period = _period(sig) if self.host.sources.noise_sigma() <= 0 else None
        if period is not None:
            horizon = min(horizon, max(start_s, 0.0) + 2.0 * period + 2.0 * dt)
        # The work is samples times lines, as for an edge's far search: the record grid for as
        # many samples as max_search_points allows (fewer for a signal of many lines), then a grid
        # an eighth of the narrowest width bound the qualifier uses for as many again. A grid
        # coarser than the widths judged would see pulses with a narrow gap between them as one
        # wide pulse (#1000); what lies beyond is looked at again at the next command.
        budget = float(self.host.param("max_search_points")) / max(1.0, sig.freq.size / 16.0)
        fine = min(span, horizon, start_s + budget * dt)
        stretches = [(fine, dt)]
        if horizon > fine:
            used = {"less": (high,), "greater": (low,)}.get(qualifier, (low, high))  # range, outside
            bounds = [w for w in used if 0 < w < math.inf]
            step = (
                max(dt, min(bounds) / 8) if bounds else self.host.crossings.far_step(sig, dt, fine, horizon)
            )
            stretches.append((min(horizon, fine + step * budget), step))

        def instant(c: _Crossing) -> float:
            if not c.final:  # found on the coarse grid: to a thousandth of a record sample
                c.estimate = self.host.crossings.bisect(
                    sig, t0, c.before, c.at, level, c.direction == "rise", dt
                )
                c.final = True
            return c.estimate

        def hit(t: float) -> TriggerHit:
            return TriggerHit(t, math.ceil(t / dt - 1e-9))

        at_end = qualifier == "outside" or (
            qualifier == "greater" and self.host.choice("greater_at") == "end"
        )

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
        for c in self.edges(sig, t0, dt, start_s, stretches, level, hyst):
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

    def edges(
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
                v = self.host.crossings.noisy(sig.sample_rel(t0, offsets), t0, offsets)
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
