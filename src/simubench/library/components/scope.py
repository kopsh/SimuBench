"""Oscilloscope components: analog front end and acquisition (trigger, sampling, quantization, transfer)."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal, highpass1, lowpass1
from ...scpi import values as V
from ...scpi.errors import ScpiError
from ...units import parse_number

#: Vertical full scale is 8 divisions; 16-bit codes span ±4 divisions.
DIVS_V = 8
DIVS_H = 10


@component("scope_frontend")
class ScopeFrontend(Component):
    """Coupling and bandwidth of one channel, applied to the voltage at the input connector.

    Analog first-order filters (physics, D13): AC coupling is a ``ac_corner`` high-pass and the
    bandwidth a first-order low-pass at ``bandwidth`` (or ``bw_limit_hz`` when the limit is on).
    """

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    defaults = {
        "coupling": "DC",
        "bandwidth": 200e6,
        "bw_limit": False,
        "bw_limit_hz": 20e6,
        "ac_corner": 10.0,
    }
    units = {"bandwidth": "Hz", "bw_limit_hz": "Hz", "ac_corner": "Hz"}

    def output(self, port: str) -> Signal:
        sig = self.input("in")
        coupling = self.param("coupling")
        if coupling == "GND":
            return Signal.zero()
        if coupling == "AC":
            sig = sig.transfer(highpass1(float(self.param("ac_corner"))))
        return sig.transfer(lowpass1(min(self._limit(), float(self.param("bandwidth")))))

    def _limit(self) -> float:
        """Bandwidth limit: on/off (``bw_limit_hz``) or a named filter such as Siglent's 20M / FULL."""
        limit = self.param("bw_limit")
        if isinstance(limit, str):
            if limit.upper() in ("FULL", "OFF"):
                return math.inf
            return parse_number(limit.upper().removesuffix("HZ").replace("M", "e6").replace("K", "e3"))
        return float(self.param("bw_limit_hz")) if limit else math.inf


def channel_index(source: object) -> int:
    """Channel number of a source name: CHANnel2 (sb-*) or C2 (Siglent)."""
    text = str(source).upper()
    for prefix in ("CHANNEL", "CHAN", "C"):
        if text.startswith(prefix) and text[len(prefix) :].isdigit():
            return int(text[len(prefix) :])
    return 1


#: Edge slope names across models: SCPI-style and Siglent-style.
RISING = {"POSitive", "EITHer", "RISing", "ALTernate"}
FALLING = {"NEGative", "EITHer", "FALLing", "ALTernate"}


@dataclass
class Record:
    channel: int
    t_start_ns: int  # instant of sample 0
    dt_s: float
    trigger_index: int
    volts: np.ndarray  # display volts (probe ratio applied)
    scale: float
    offset: float
    triggered: bool


@component("scope_acquisition")
class ScopeAcquisition(Component):
    """Timebase, edge trigger, sampling and waveform transfer for all channels of a scope."""

    ports: dict[str, PortSpec] = {}
    port_pattern = r"in\d+"  # inputs in1..inN are created by the instrument wiring
    defaults = {"overhead_s": 1e-3, "search_windows": 4}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.records: dict[int, Record] = {}
        self.acq_count = 0

    def reset(self) -> None:
        self.records = {}

    # ---- helpers ----------------------------------------------------------------------------------

    def _st(self, path: str) -> Any:
        assert self.owner is not None
        return self.owner.get_state(path)

    def _channels(self) -> int:
        assert self.owner is not None
        return self.owner.channel_count

    def _display_signal(self, n: int) -> Signal:
        """Signal of channel n in display volts: front-end output times the probe ratio."""
        return self.input(f"in{n}").scale(float(self._st(f"ch{n}.probe")))

    def trigger_level(self) -> float:
        """The trigger level in display volts (the scope screen draws it)."""
        return float(self._st("trigger.level"))

    def _timing(self) -> tuple[int, float, int, int]:
        points = int(self._st("waveform.points"))
        scale = float(self._st("timebase.scale"))
        dt = DIVS_H * scale / points
        pre = points // 2
        return points, dt, pre, points - pre

    def _trigger_search(self, sig: Signal, t0: int, dt: float, pre: int, points: int) -> tuple[int, bool]:
        """Return (trigger sample index measured from t0, triggered?)."""
        level = float(self._st("trigger.level"))
        slope = self._st("trigger.slope")
        n_ch = channel_index(self._st("trigger.source"))
        hyst = 0.02 * DIVS_V * float(self._st(f"ch{n_ch}.scale"))
        span = points * int(self.param("search_windows"))
        idx = np.arange(span)
        v = sig.sample_rel(t0, idx * dt)
        rising = slope in RISING
        falling = slope in FALLING
        best: int | None = None
        if rising:
            armed = np.nonzero(v < level - hyst)[0]
            armed = armed[armed >= pre - 1]
            if armed.size:
                after = np.nonzero((v >= level) & (idx > armed[0]))[0]
                if after.size:
                    best = int(after[0])
        if falling:
            armed = np.nonzero(v > level + hyst)[0]
            armed = armed[armed >= pre - 1]
            if armed.size:
                after = np.nonzero((v <= level) & (idx > armed[0]))[0]
                if after.size and (best is None or after[0] < best):
                    best = int(after[0])
        if best is None or best < pre:
            return pre, False
        return best, True

    # ---- acquisition ------------------------------------------------------------------------------

    def compute(self, channels: list[int]) -> tuple[dict[int, Record], int, bool]:
        """Sample the listed channels at the current instant without changing any state.

        Returns the records, the virtual time an acquisition takes (ns) and whether it triggered.
        """
        points, dt, pre, post = self._timing()
        t0 = self.session.now_ns
        n_trig = channel_index(self._st("trigger.source"))
        trig_sig = self._display_signal(n_trig)
        k, triggered = self._trigger_search(trig_sig, t0, dt, pre, points)
        start = k - pre
        idx = np.arange(points)
        records: dict[int, Record] = {}
        for n in channels:
            sig = self._display_signal(n)
            v = sig.sample_rel(t0, (start + idx) * dt)
            records[n] = Record(
                channel=n,
                t_start_ns=t0 + round(start * dt * 1e9),
                dt_s=dt,
                trigger_index=pre,
                volts=v,
                scale=float(self._st(f"ch{n}.scale")),
                offset=float(self._st(f"ch{n}.offset")),
                triggered=triggered,
            )
        used = (k + post) * dt + float(self.param("overhead_s"))
        if not triggered:
            used = points * int(self.param("search_windows")) * dt + float(self.param("overhead_s"))
        return records, round(used * 1e9), triggered

    def displayed(self) -> list[int]:
        return [n for n in range(1, self._channels() + 1) if self._st(f"ch{n}.display")]

    def acquire(self, channels: list[int]) -> int:
        """Acquire the listed channels at the current instant. Returns the virtual time used (ns)."""
        records, used, triggered = self.compute(channels)
        if not triggered and self._st("trigger.mode") == "NORMal":
            return used  # no trigger: the previous records stay
        self.records.update(records)
        self.acq_count += 1
        self.session.invalidate()
        if self.owner is not None:
            # The acquisition's recipe: everything needed to regenerate it from the bench (D36).
            self.session.instrument_event(
                self.owner,
                "acquisition",
                {
                    "count": self.acq_count,
                    "channels": {
                        str(ch): {
                            "t_start_ns": rec.t_start_ns,
                            "dt_s": rec.dt_s,
                            "points": int(rec.volts.size),
                            "trigger_index": rec.trigger_index,
                            "triggered": rec.triggered,
                            "sha256": hashlib.sha256(np.ascontiguousarray(rec.volts).tobytes()).hexdigest(),
                        }
                        for ch, rec in sorted(records.items())
                    },
                    "seed": self.session.seed,
                },
            )
        return used

    @op("digitize")
    def digitize(self, sources: list[bytes] | None = None, **_: Any) -> OpResult:
        chans: list[int] = []
        for raw in sources or []:
            name = V.parse_string(raw).upper()
            m = name.removeprefix("CHANNEL").removeprefix("CHAN")
            if not m.isdigit() or not 1 <= int(m) <= self._channels():
                raise ScpiError(-224, f"unknown source {name!r}")
            chans.append(int(m))
            assert self.owner is not None
            self.owner.set_state(f"ch{int(m)}.display", True)
        if not chans:
            chans = self.displayed()
        return OpResult(duration_ns=self.acquire(chans))

    def _source(self) -> int:
        return channel_index(self._st("waveform.source"))

    def transfer_record(self, channel: int, first: int) -> tuple[Record | None, int]:
        """The record a waveform transfer reads (``waveform_transfer``), and the virtual time getting
        it took: the last acquisition. ``first`` is the transfer's first point (a read that starts
        at 0 may acquire anew on models that do)."""
        return self.records.get(channel), 0

    def _record(self) -> Record:
        rec = self.records.get(self._source())
        if rec is None:
            raise ScpiError(-230, "no acquisition for this source; send :DIGitize first")
        return rec

    def _codes(self, rec: Record) -> tuple[np.ndarray, float, int]:
        fmt = self._st("waveform.format")
        full = DIVS_V / 2 * rec.scale
        span = 128 if fmt == "BYTE" else 32768
        unsigned = bool(self._st("waveform.unsigned"))
        codes = np.round((rec.volts - rec.offset) / full * span)
        lo, hi = -span, span - 1
        codes = np.clip(codes, lo, hi).astype(np.int64)
        yref = 0
        if unsigned:
            codes = codes + span
            yref = span
        return codes, full / span, yref

    @op("preamble")
    def preamble(self, **_: Any) -> OpResult:
        rec = self._record()
        _, yinc, yref = self._codes(rec)
        fmt = {"BYTE": 0, "WORD": 1, "ASCii": 4}[self._st("waveform.format")]
        pts = rec.volts.size
        xorg = -rec.trigger_index * rec.dt_s
        fields = [
            str(fmt).encode(),
            b"0",
            str(pts).encode(),
            b"1",
            V.format_float(rec.dt_s),
            V.format_float(xorg),
            b"0",
            V.format_float(yinc),
            V.format_float(rec.offset),
            str(yref).encode(),
        ]
        return OpResult(b",".join(fields))

    # ---- measurements -----------------------------------------------------------------------------

    def _measure_record(self, channel: int) -> Record:
        rec = self.records.get(channel)
        if rec is None:
            self.acquire([channel])
            rec = self.records[channel]
        return rec

    @op("measure")
    def measure(
        self, suffixes: dict[str, int] | None = None, source: str | None = None, **kw: Any
    ) -> OpResult:
        """Automatic measurements on the latest record.

        VPP, VMAX, VMIN, VAMPlitude, VRMS, VAVerage, FREQuency and PERiod.
        """
        what = kw.get("what") or ""
        n = self._source()
        if source:
            name = source.upper()
            m = name.removeprefix("CHANNEL").removeprefix("CHAN")
            if not m.isdigit():
                raise ScpiError(-224, f"unknown source {source!r}")
            n = int(m)
        rec = self._measure_record(n)
        v = rec.volts
        value = _measure(what, v, rec.dt_s)
        return OpResult(V.format_float(value))


def _measure(what: str, v: np.ndarray, dt: float) -> float:
    vmax, vmin = float(v.max()), float(v.min())
    mid = (vmax + vmin) / 2
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
        hi = v[v >= mid]
        lo = v[v < mid]
        return float(np.median(hi) - np.median(lo)) if hi.size and lo.size else vmax - vmin
    if what in ("FREQuency", "PERiod"):
        s = (v - mid) < 0  # not signbit: quantized records hold -0.0, which is not below the middle
        rising = np.nonzero(s[:-1] & ~s[1:])[0]
        if rising.size < 2:
            return math.nan
        # Interpolate each crossing for sub-sample accuracy.
        x0, x1 = v[rising] - mid, v[rising + 1] - mid
        t = (rising + (-x0) / (x1 - x0)) * dt
        period = float(np.mean(np.diff(t)))
        return 1.0 / period if what == "FREQuency" else period
    raise ScpiError(-113, f"measurement {what!r} not supported")
