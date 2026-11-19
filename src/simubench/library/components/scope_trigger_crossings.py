"""Level crossings in a signal: the sample stream of a level, its stretches and merges, the instant of a crossing by bisection, and a scan for one. Part of ``ScopeTrigger`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np

from ...engine.noise import gaussian, stream_key
from ...engine.signal import Signal
from .scope_trigger_host import TriggerHit, TriggerHost, _between, _Crossing


class Crossings:
    def __init__(self, host: TriggerHost) -> None:
        self.host = host

    def hysteresis(self, scale: float) -> float:
        setting = str(self.host.choice("hysteresis_setting") or "normal")
        normal = float(self.host.param("hysteresis_divs"))
        if setting == "nreject":  # the widest band, whether or not the old noise_reject flag is on
            divs = float(self.host.param("reject_hysteresis_divs"))
        elif setting == "hsensitivity":  # the narrowest band (a 4 GHz and above waveform)
            own = self.host.param("hsensitivity_divs")
            divs = normal / 2.0 if own is None else float(own)
        elif self.host.param("noise_reject") in (True, 1, "1", "ON", "on"):
            divs = float(self.host.param("reject_hysteresis_divs"))
        else:
            divs = normal
        return divs * scale

    def noisy(
        self, v: np.typing.NDArray[np.float64], t0: int, offsets: np.typing.NDArray[np.float64]
    ) -> np.typing.NDArray[np.float64]:
        sigma = self.host.sources.noise_sigma()
        if sigma <= 0 or self.host.session is None:
            return v
        # Keyed by the absolute instant (ps) of each sample: the same instant, the same noise; a
        # 256 GSa/s grid's samples (3.9 ps apart) each draw their own (#1263).
        key = stream_key(self.host.session.seed, self.host.uid, "trigger-noise")
        return v + sigma * gaussian(key, t0 * 1000 + np.round(offsets * 1e12).astype(np.int64))

    def far_step(self, sig: Signal, dt: float, span: float, horizon: float) -> float:
        """The grid beyond the first stretch of a search: as coarse as the signal allows (sixteen
        samples a period of its highest line), no finer than the record's."""
        lines = sig.freq[np.abs(sig.amp) > 1e-6 * max(1e-300, float(np.abs(sig.amp).max(initial=0.0)))]
        f_top = float(lines.max()) if lines.size else 0.0
        step = dt if f_top <= 0 else max(dt, 1.0 / (16.0 * f_top))
        # The work is samples times lines: a signal of many lines is searched on fewer samples.
        budget = float(self.host.param("max_search_points")) / max(1.0, sig.freq.size / 16.0)
        return max(step, (horizon - span) / budget)

    def stretches(
        self, sig: Signal, dt: float, start_s: float, span: float, horizon: float, *bounds: float
    ) -> list[tuple[float, float]]:
        """The grids a pulse- or state-based search walks: the record grid for as many samples as
        ``max_search_points`` allows (fewer for a signal of many lines), then a grid an eighth of
        the narrowest bound the qualification compares, so a coarse grid still bounds the durations
        it judges (#1000)."""
        budget = float(self.host.param("max_search_points")) / max(1.0, sig.freq.size / 16.0)
        fine = min(span, horizon, start_s + budget * dt)
        stretches = [(fine, dt)]
        if horizon > fine:
            usable = [b for b in bounds if 0 < b < math.inf]
            step = max(dt, min(usable) / 8) if usable else self.far_step(sig, dt, fine, horizon)
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
        noisy = self.host.sources.noise_sigma() > 0
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self.noisy(sig.sample_rel(t0, offsets), t0, offsets)
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
                                estimate = self.bisect(sig, t0, a_t, t_k, level, rising, dt)
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

    def merged(
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

    @staticmethod
    def bisect(sig: Signal, t0: int, lo: float, hi: float, level: float, rising: bool, dt: float) -> float:
        """The crossing in (lo, hi] found on a coarse grid, to a thousandth of a record sample."""
        while hi - lo > dt * 1e-3:
            mid = (lo + hi) / 2
            v = float(sig.sample_rel(t0, np.array([mid]))[0])
            if (v >= level) == rising:
                hi = mid
            else:
                lo = mid
        return hi

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
            return self.bisect(sig, t0, t_p, t_k, level, rising, dt)
        return _between(t_p, v_p, t_k, v_k, level)

    def scan(
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
            v = self.noisy(sig.sample_rel(t0, offsets), t0, offsets)
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
