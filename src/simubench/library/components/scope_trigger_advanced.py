"""The advanced trigger types (pattern, state, sequence and the like): one search over the record for the next hit. Part of ``ScopeTrigger`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math

from ...engine.signal import Signal
from ...scpi import values as V
from .scope import _upper
from .scope_trigger_host import TriggerHit, TriggerHost, _Crossing, _period


class Advanced:
    def __init__(self, host: TriggerHost) -> None:
        self.host = host

    def advanced(
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
        source = self.host.sources.family_source()
        level = self.host.sources.source_level(n)
        noiseless = self.host.sources.noise_sigma() <= 0
        period = _period(sig) if noiseless else None
        high = self.host.sources.threshold("high", source)
        low = self.host.sources.threshold("low", source)

        if mode == "nedge":
            slope = self.host.param("nedge_slope")
            if slope in (None, ""):
                slope = str(
                    self.host.channel_param("slope", n) if n is not None else self.host.param("slope")
                )
            slope = str(slope).upper()
            rising = slope in _upper(self.host.param("rising"))
            falling = slope in _upper(self.host.param("falling"))
            directions = [d for d, on in (("rise", rising), ("fall", falling)) if on]
            if not directions or not (
                (rising and bottom < level - hyst and top >= level)
                or (falling and top > level + hyst and bottom <= level)
            ):
                return None
            count = max(1, int(self.host.param("nedge_count")))
            if period is not None:
                # The edges repeat each period: what one period shows is what every period shows,
                # so the nth lands in its own period — the horizon reaches that far (within the
                # sample budget; a search that cannot look that far waits and looks again at the
                # next command, as the glitch search does).
                probe_stop = min(span, 2.0 * period + dt)
                probe = [
                    c
                    for c in self.host.runs.edges(sig, t0, dt, start_s, [(probe_stop, dt)], level, hyst)
                    if c.direction in directions
                ]
                per_period = max(len(probe) / max(probe_stop / period, 1.0), 1.0)
                horizon = min(
                    horizon,
                    max(probe_stop, start_s + period * math.ceil(count / per_period) + 2.0 * dt),
                )
            seen = 0
            for c in self.host.runs.edges(
                sig,
                t0,
                dt,
                start_s,
                self.host.crossings.stretches(sig, dt, start_s, span, horizon),
                level,
                hyst,
            ):
                if c.direction not in directions:
                    continue
                seen += 1
                if seen >= count:
                    if not c.final:  # found on the coarse grid: bisected on the signal
                        c.estimate = self.host.crossings.bisect(
                            sig, t0, c.before, c.at, level, c.direction == "rise", dt
                        )
                        c.final = True
                    return TriggerHit(c.estimate, math.ceil(c.estimate / dt - 1e-9))
            return None

        if mode == "timeout":
            limit = float(self.host.param("timeout_time"))
            cond = str(self.host.choice("timeout_condition") or "unchanged")
            if limit <= 0:
                return None
            if period is not None:  # one period shows every state run there is
                horizon = min(horizon, start_s + period + limit + 2.0 * dt)
            for state, entry, upto, _final in self.host.runs.runs(
                sig,
                t0,
                dt,
                start_s,
                self.host.crossings.stretches(sig, dt, start_s, span, horizon, limit),
                level,
                hyst,
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
            condition = str(self.host.choice("window_condition") or "enter")
            wanted = "inside" if condition in ("enter", "inside") else "outside"
            qualifier = str(self.host.choice("window_qualifier") or "")
            tpoint = str(self.host.choice("window_tpoint") or "boundary")
            limit = float(self.host.param("window_time"))
            if not (top >= low and bottom <= high) or not (bottom < low - hyst or top > high + hyst):
                return None  # the signal never reaches the band, or never leaves it
            if period is not None:
                horizon = min(horizon, start_s + 2.0 * period + limit + 2.0 * dt)

            def instant(c: tuple) -> float:
                bound, d, before, at, estimate, final = c
                if final:
                    return estimate
                return self.host.crossings.bisect(sig, t0, before, at, bound, d == "rise", dt)

            for state, entry, exit_c, upto, _final in self.host.runs.runs_window(
                sig,
                t0,
                dt,
                start_s,
                self.host.crossings.stretches(sig, dt, start_s, span, horizon, limit),
                low,
                high,
                hyst,
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
        polarity = str(self.host.choice("runt_polarity") or "positive")
        ttype = str(self.host.choice("transition_type") or "risetime")
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
            qualified = V.flag(self.host.param("runt_qualified"))
            bounds = (float(self.host.param("runt_time")),)
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
            tmode = str(self.host.choice("transition_mode") or "greater")
            t_limit = float(self.host.param("transition_time"))
            t_lo = float(self.host.param("transition_range_low"))
            t_hi = float(self.host.param("transition_range_high"))
            bounds = (t_limit,) if tmode in ("gthan", "lthan") else (t_lo, t_hi)
        if period is not None:  # a signal that repeats shows every pulse within two periods
            horizon = min(horizon, start_s + 2.0 * period + 2.0 * dt)
        levels = {"low": low, "high": high}

        def instant(c: _Crossing, which: str) -> float:
            if not c.final:  # found on the coarse grid: bisected on the signal
                c.estimate = self.host.crossings.bisect(
                    sig, t0, c.before, c.at, levels[which], c.direction == "rise", dt
                )
                c.final = True
            return c.estimate

        def hit(t: float) -> TriggerHit:
            return TriggerHit(t, math.ceil(t / dt - 1e-9))

        began: tuple[str, _Crossing] | None = None  # the opening crossing of the pulse under way
        for which, c in self.host.crossings.merged(
            sig,
            t0,
            dt,
            start_s,
            self.host.crossings.stretches(sig, dt, start_s, span, horizon, *bounds),
            low,
            high,
            hyst,
        ):
            if which == "":  # progress: a runt still open has lasted its time
                if mode == "runt" and qualified and began is not None:
                    begun = instant(began[1], began[0])
                    if c.at - begun > float(self.host.param("runt_time")):
                        return hit(begun + float(self.host.param("runt_time")))
                continue
            key = (which, str(c.direction))
            if began is not None and key in rejects:
                began = None
                continue
            if key == closes and began is not None:
                begun = instant(began[1], began[0])
                done = instant(c, which)
                if mode == "runt":
                    if not qualified or done - begun > float(self.host.param("runt_time")):
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
