"""The edge-based operations: a value at an edge, a pair of edges, the automatic thresholds and trigger levels. Part of ``ScopeMeasurements`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine.component import (
    OpResult,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .measurements import (
    burst_measure,
    crossing_voltage,
    crossings,
    edge_spec,
    measure,
    pulse_window_mean,
    threshold_levels,
    top_base,
)
from .scope import trigger_channel
from .scope_measurements_host import MeasurementsHost


class Edges:
    def __init__(self, host: MeasurementsHost) -> None:
        self.host = host

    def op_at(
        self, what: str = "", source: list[bytes] | None = None, channel: int | None = None, **_: Any
    ) -> OpResult:
        args, channels = self.host.sources(source)
        if any(a.upper() == "NONE" for a in args):  # a bookmark on no waveform has no value
            return self.host.reply(math.nan, 0)
        n = channels[0] if channels else self.host.channel_of(channel)
        if channels:
            self.host.follow(n, self.host.source_word(n))
        rec, used = self.host.values.record(n)
        dt, origin = rec.dt_s, rec.origin
        if what == "VTIMe":
            if not args:
                raise ScpiError(-109, "VTIMe? takes a time")
            x = (V.parse_float(args[0].encode(), "S") - origin) / dt
            size = rec.points
            if size == 0 or not 0 <= x <= size - 1:
                return self.host.reply(math.nan, used)
            k = min(int(x), size - 2) if size > 1 else 0
            frac = x - k
            pair = rec.span(k, k + 2)  # the two samples around the time: a deep record computes no more
            value = float(pair[0]) if size == 1 else float(pair[0] + (pair[1] - pair[0]) * frac)
            return self.host.reply(value, used)
        v = np.asarray(rec.volts, dtype=float)
        if what == "TEDGe":
            position = "MIDDle"
            if args and args[0].strip().upper().startswith(("UPP", "MIDD", "LOW")):
                position, args = args[0], args[1:]  # the Infiniium's <meas_thres_txt> first
            sign, occurrence = edge_spec(args[0] if args else None)
            cross = [t for t, d in self.host.values.crossings_of(v, rec, position) if d == sign]
        elif what == "TVALue":
            if not args:
                raise ScpiError(-109, "TVALue? takes a value")
            level = V.parse_float(args[0].encode(), "V")
            sign, occurrence = edge_spec(args[1] if len(args) > 1 else None)
            span = float(v.max() - v.min()) if v.size else 0.0
            cross = [
                t
                for t, d in crossings(v, dt, level, float(self.host.param("hysteresis")) * span)
                if d == sign
            ]
        else:
            raise ScpiError(-224, f"measurement {what!r} takes no arguments here")
        value = origin + cross[occurrence - 1] if len(cross) >= occurrence else math.nan
        return self.host.reply(value, used)

    def op_pair(
        self,
        what: str = "",
        source: list[bytes] | None = None,
        define: list[Any] | None = None,
        **_: Any,
    ) -> OpResult:
        assert self.host.owner is not None
        words, channels = self.host.sources(source)
        if what == "DELTatime" and len(channels) == 1:
            channels = channels * 2
        if len(channels) < 2:
            pair = [self.host.key(self.host.owner.get_state(p)) for p in (self.host.param("sources") or [])]
            channels = (channels + pair[len(channels) :])[:2]
        if len(channels) < 2:
            raise ScpiError(-109, f"{what}? needs two sources")
        direction = next((w.upper() for w in words if w.upper().startswith(("RIS", "FALL"))), "RISING")
        sign = -1 if direction.startswith("FALL") else 1
        hyst = float(self.host.param("hysteresis"))
        recs, used = [], 0
        for n in channels:
            rec, u = self.host.values.record(n)
            used += u
            recs.append(rec)
        if what == "CROSsing":
            a, b = (np.asarray(r.volts, dtype=float) for r in recs)
            return self.host.reply(crossing_voltage(a, b), used)
        if what == "DELTatime":
            d = list(define or ["RISing", 1, "MIDDle", "RISing", 1, "MIDDle"])
            times = []
            for rec, (dirn, number, position) in zip(recs, (d[0:3], d[3:6]), strict=True):
                want = str(dirn).upper()
                edges = [
                    t
                    for t, e in self.host.values.crossings_of(
                        np.asarray(rec.volts, dtype=float), rec, str(position)
                    )
                    if want.startswith("EITH") or e == (-1 if want.startswith("FALL") else 1)
                ]
                k = int(number)
                times.append(rec.origin + edges[k - 1] if len(edges) >= k else math.nan)
            return self.host.reply(times[1] - times[0], used)
        times = []
        for rec in recs:
            v = np.asarray(rec.volts, dtype=float)
            edges = [t for t, e in self.host.values.crossings_of(v, rec) if e == sign]
            times.append(rec.origin + edges[0] if edges else math.nan)
        delay = times[1] - times[0]
        if what == "DELay":
            return self.host.reply(delay, used)
        first = np.asarray(recs[0].volts, dtype=float)
        period = measure("PERiod", first, recs[0].dt_s, hysteresis=hyst)
        if not (math.isfinite(period) and period > 0):
            return self.host.reply(math.nan, used)
        # folded into one period, -180..+180 degrees: the first edges may lie whole periods apart
        return self.host.reply((delay / period * 360.0 + 180.0) % 360.0 - 180.0, used)

    def op_auto_thresholds(self, source: Any = None, channel: int | None = None, **_: Any) -> OpResult:
        assert self.host.owner is not None
        if not self.host.param("thresholds"):
            raise ScpiError(-300, f"{self.host.uid}: thresholds names no settings")
        n = self.host.channel_of(channel, source)
        if n < 0:
            raise ScpiError(-224, "the threshold sets are per channel")
        rec, used = self.host.values.record(n)
        v = np.asarray(rec.volts, dtype=float)
        lv = threshold_levels(
            v, {"method": "PERCent"}, float(self.host.param("hysteresis")), self.host.values.band_floor(rec)
        )
        if lv is None:
            return OpResult(duration_ns=used)
        prefix = str(self.host.param("thresholds"))
        self.host.owner.set_state(f"{prefix}.general{n}.hyst_level", lv.middle)
        self.host.owner.set_state(f"{prefix}.general{n}.hyst_range", lv.band)
        return OpResult(duration_ns=used)

    def op_with_args(self, what: str = "", source: list[bytes] | None = None, **_: Any) -> OpResult:
        words, channels = self.host.sources(source)
        n = channels[0] if channels else self.host.channel_of(None)
        if not words:
            raise ScpiError(-109, f"{what}? takes a number after the source")
        number = V.parse_float(words[0].encode(), "S" if what.startswith("B") else None)
        rec, used = self.host.values.record(n)
        v = np.asarray(rec.volts, dtype=float)
        lv = self.host.values.levels(v, rec, "general")
        if lv is None or v.size == 0:
            return self.host.reply(math.nan, used)
        if what in ("PTOP", "PBASe"):
            return self.host.reply(pulse_window_mean(v, rec.dt_s, lv, number, what == "PTOP"), used)
        if what in ("BWIDth", "BPERiod", "BINTerval"):
            if number <= 0:
                raise ScpiError(-222, "the idle time must be positive")
            return self.host.reply(burst_measure(what, v, rec.dt_s, lv, number), used)
        raise ScpiError(-224, f"measurement {what!r} takes no number here")

    def op_auto_trigger_levels(self, source_only: bool = False, **_: Any) -> OpResult:
        assert self.host.owner is not None
        acq = self.host.acq()
        only = None
        if source_only:
            trig = getattr(acq, "ref", lambda _n: None)("trigger")
            only = trigger_channel(trig.param("source")) if trig is not None else None
            if only is None:
                return OpResult()  # an external or line source: no waveform to take 50 % of
        for key, path in (self.host.param("trigger_levels") or {}).items():
            n = int(key)
            if (n != only) if only is not None else (n not in acq.displayed()):
                continue  # FIFTy: the trigger source's level, shown on screen or not
            coupling = str(self.host.owner.state.get(f"ch{n}.coupling", "DC")).upper()
            if coupling.startswith("AC"):
                level = 0.0
            else:
                rec, _ = acq.measure_record(n)
                v = np.asarray(rec.volts, dtype=float)
                levels = top_base(v) if v.size else None
                top, base = levels if levels else ((float(v.max()), float(v.min())) if v.size else (0.0, 0.0))
                level = (top + base) / 2
            self.host.owner.set_state(str(path), level)
        return OpResult()
