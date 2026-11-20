"""One capture of a scope's frame: the request the chain is asked for, the trigger hit, the clipping latches and the overload check. Part of ``ScopeAcquisition`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math
from functools import partial
from typing import TYPE_CHECKING, Any

import numpy as np

from ...engine.component import (
    OpResult,
)
from ...engine.noise import gaussian, stream_key, uniform
from ...engine.signal import Signal
from ...engine.stream import (
    MAX_FRAME,
    MAX_REQUEST_FANOUT,
    FrameRequest,
)
from .scope import (
    ScopeAdc,
    ScopeDecimator,
)
from .scope_record import (
    EAGER_POINTS,
    DeepRecord,
    Record,
)

if TYPE_CHECKING:
    from .scope_trigger import TriggerHit
from .scope_acquisition_host import AcquisitionHost


class Capture:
    def __init__(self, host: AcquisitionHost) -> None:
        self.host = host
        # the ADC's clip detectors' latches per channel (#1067): the ADC status (:ADC:CLIPped?,
        # low/high, read clears) and the channel's own (:CLIPped?, 0/1, read clears and a scale or
        # offset change away from the latched (scale, offset) resets it)
        self.clip_latches: dict[int, dict[str, Any]] = {}
        # channels whose input went beyond the maximum input range since :OVLRegister? was last read (#1264)
        self.overloaded: set[int] = set()

    def _jitter(self, t_start_ns: int, frame: int | None = None, segment: int = 0) -> float:
        jitter = float(self.host.param("trigger_jitter_s"))
        if not jitter:
            return 0.0
        if frame is not None:  # a displayed frame of a running scope (shows_live), not an acquisition
            return jitter * float(
                gaussian(stream_key(self.host.session.seed, self.host.uid, "frame", frame), np.zeros(1))[0]
            )
        if self.host.param("noise_key") == "time":
            key = stream_key(self.host.session.seed, self.host.uid, "jitter")
            return jitter * float(gaussian(key, np.array([t_start_ns]))[0])
        # A later segment of the same acquisition has a stream of its own; the first keeps the key
        # an unsegmented acquisition has.
        tail = ("segment", segment) if segment else ()
        key = stream_key(self.host.session.seed, self.host.uid, "jitter", self.host.recorder.acq_count, *tail)
        return jitter * float(gaussian(key, np.zeros(1))[0])

    def request(
        self,
        n: int,
        t0: int,
        start: int,
        dt: float,
        points: int,
        t_start_ns: int,
        m: int,
        frame: int | None,
        segment: int = 0,
    ) -> FrameRequest:
        """What the acquisition asks channel n's chain for: ``points`` samples ``dt`` apart from
        sample ``start`` of the grid at ``t0``, and the random stream of its input noise — a pure
        function of the seed, the channel and the key. A displayed frame has a stream of its own;
        an acquisition's is keyed by the absolute sample instants (``noise_key: time``, D46) or by
        the acquisition count (and, after the first, the segment's number: the segments of one
        acquisition carry independent noise). The index counts on the converter's grid (``m``
        samples per record sample when peak detect oversamples)."""
        if frame is not None:
            return FrameRequest(t0, points, dt, (self.host.uid, "frame", n, frame), first=start)
        if self.host.param("noise_key") == "time":
            native_dt = dt / m
            key = (self.host.uid, "noise", n, native_dt, str(self.host.param("noise_stream")))
            return FrameRequest(t0, points, dt, key, math.floor(t_start_ns * 1e-9 / native_dt), first=start)
        tail = ("segment", segment) if segment else ()
        return FrameRequest(
            t0, points, dt, (self.host.uid, "noise", n, self.host.recorder.acq_count, *tail), first=start
        )

    def compute(self, channels: list[int], frame: int | None = None) -> tuple[dict[int, Record], int, bool]:
        """Sample the listed channels at the current instant without changing any state.

        Returns the records, the virtual time an acquisition takes (ns) and whether it triggered.
        ``frame`` numbers a frame the screen of a running scope shows: its noise is its own.
        """
        records, used, triggered, _ = self.acquire(channels, frame)
        return records, used, triggered

    def acquire(
        self,
        channels: list[int],
        frame: int | None = None,
        t0: int | None = None,
        segment: int = 0,
        earliest_ns: int | None = None,
    ) -> tuple[dict[int, Record], int, bool, int]:
        """:meth:`compute`, and the instant of the trigger (or of the record's trigger point) in ns.

        A later ``segment`` of a segmented acquisition is armed at ``t0`` (not now) and takes no
        trigger before ``earliest_ns``."""
        points, nominal_dt, pre, post = self.host.timing()
        # A fast clock (positive clock_ppm) samples closer together than the nominal interval; the
        # record keeps the nominal dt the scope reports and reconstructs with, so time measurements
        # read low by that fraction (the spectrum analyzer's reference convention, G3).
        dt = nominal_dt / (1.0 + self.host.horizontal().clock_error())
        t0 = self.host.session.now_ns if t0 is None else t0
        earliest_s = 0.0 if earliest_ns is None else max(0.0, (earliest_ns - t0) * 1e-9)
        # An auto mode gives up after its auto timeout; a normal or single acquisition waits for
        # its edge, the virtual time it takes included (G5).
        hit = self._trigger_hit(
            t0, dt, pre, points, waits=not self.host.mode_is("auto") and frame is None, earliest_s=earliest_s
        )
        triggered = hit is not None
        k = hit.index if hit is not None else pre
        start = k - pre
        if frame is not None and hit is None:
            # An untriggered frame of a running scope: a free-running scope starts each sweep at an
            # arbitrary instant, so the trace is not locked to the signal and slides from frame to
            # frame. The offset is a pure function of the seed and the frame number (no virtual time
            # passes, and a replay shows the same screen).
            phase = float(
                uniform(stream_key(self.host.session.seed, self.host.uid, "frame-phase", frame), np.zeros(1))[
                    0
                ]
            )
            t0 += round(phase * points * dt * 1e9)
        t_start_ns = t0 + round(start * dt * 1e9)
        m = self.host.decimator_unit().oversample()  # converter samples per record sample (peak detect)
        # The record samples on the clock's grid; where the trigger fell between two samples, and
        # the trigger's jitter, are in the record's time origin (the scope's xorigin), not in the
        # samples.
        jitter = self._jitter(t_start_ns, frame, segment)
        x_origin = (start * dt - hit.t_s - jitter) if hit is not None else -pre * dt
        trigger_ns = t0 + round(hit.t_s * 1e9) if hit is not None else None
        records: dict[int, Record] = {}
        # ``frame`` numbers a screen frame of a running scope (compute: no state changes), never an
        # acquisition: an acquisition — each segment of a segmented one, each average — comes here
        # with frame None and is checked; a screen frame latches nothing (#1304).
        if frame is None:
            self._check_overload(channels, t_start_ns, points * dt)
        for n in channels:
            scale, offset = self.host.scale_offset(n)
            request = self.request(n, t0, start, dt, points, t_start_ns, m, frame, segment)
            # A deep record is computed where it is read (#1012); one up to EAGER_POINTS now, whole:
            # the channel's chain (ADC, then the decimator's acquisition mode) gives the record's
            # samples, the codes, as volts (D46: they are kept).
            deep = self._deep(n, request)
            volts, clip = None, None
            if deep is None:
                frames = self.host.input_frames(f"in{n}", request)
                volts = self.host.vertical_of(n).correct(frames.data)
                self._latch_clip(n, frames)
                if frames.clip_sides is not None:
                    clip = (1 if frames.clip_sides[0] else 0) | (2 if frames.clip_sides[1] else 0)
            records[n] = Record(
                channel=n,
                t_start_ns=t_start_ns,
                dt_s=nominal_dt,
                trigger_index=pre,
                volts=volts,
                deep=deep,
                scale=scale,
                offset=offset,
                triggered=triggered,
                x_origin_s=x_origin * nominal_dt / dt,  # in the scope's own (nominal) time
                trigger_ns=trigger_ns,
            )
            records[n].code_scale = self.host.adc_unit().converter_scale(
                scale, float(self.host.vertical_of(n).param("probe_ratio"))
            )
            records[n].clip = clip
        if triggered or self.host.param("untriggered") == "record":
            used = (k + max(post, 0)) * dt
        else:  # the whole trigger search
            used = points * int(self.host.trigger_unit().param("search_windows")) * dt
        used = used * self.host.averages() + float(self.host.param("overhead_s"))
        return records, round(used * 1e9), triggered, t0 + round(k * dt * 1e9)

    def _latch_clip(self, n: int, frame: Any) -> None:
        """The ADC's clip detectors latch a channel's clipping until it is read or cleared
        (:CHANnel<N>:ADC:CLIPped? / :CHANnel<N>:CLIPped?, #1067). Only a record computed whole
        carries the ADC's codes; a deep one computes at read and does not latch."""
        if not frame.clipped:
            return
        fmt = frame.fmt
        if frame.clip_sides is not None:  # the converter's own (a filter after it keeps them, #1262)
            low, high = frame.clip_sides
        elif fmt.bits <= 0:
            return
        else:
            half, lsb = 1 << (fmt.bits - 1), float(fmt.lsb)
            low = bool(np.min(frame.data) <= -half * lsb)
            high = bool(np.max(frame.data) >= (half - 1) * lsb)
        at = self.host.scale_offset(n)
        latch = self.clip_latches.setdefault(n, {"adc_low": False, "adc_high": False, "low": False, "at": at})
        latch["adc_low"] |= low
        latch["adc_high"] |= high
        latch["low"] |= low or high
        latch["at"] = at

    def _check_overload(self, channels: list[int], t0_ns: int, span_s: float) -> None:
        """Each acquired channel's input against its limits (#1264): beyond the maximum input range
        the channel's overload bit latches (:OVLRegister?, read clears); beyond the absolute maximum
        the instrument warns of possible damage (an event and the problems list)."""
        add = str(self.host.adc_unit().param("offset_sign")).lower() == "add"
        for n in channels:
            vertical = self.host.vertical_of(n)
            over, damage = vertical.overload(add, t0_ns, span_s)
            if over:
                self.overloaded.add(n)
            if damage:
                lo, hi = vertical.input_extremes(t0_ns, span_s)
                self.host.warn(
                    f"CH{n}-OVERVOLTAGE",
                    f"CH{n} 的输入达到 {max(abs(lo), abs(hi)):.3g} V，超过最大输入 ±{float(vertical.param('max_input_v')):g} V，"
                    "真机可能损坏前端（Input beyond the absolute maximum: possible damage）",
                )

    def overload_bits(self) -> int:
        """The Overload Event Register: bit n-1 for each channel overloaded since the last read,
        which clears it (#1264)."""
        bits = sum(1 << (n - 1) for n in self.overloaded)
        self.overloaded = set()
        return bits

    def adc_clip_status(self, channel: int | None = None, **_: Any) -> OpResult:
        n = channel or 1
        latch = self.clip_latches.get(n)
        code = (2 if latch is not None and latch["adc_high"] else 0) + (
            1 if latch is not None and latch["adc_low"] else 0
        )
        if latch is not None:
            latch["adc_low"] = latch["adc_high"] = False
        return OpResult(str(code).encode())

    def clipped_status(self, channel: int | None = None, **_: Any) -> OpResult:
        n = channel or 1
        latch = self.clip_latches.get(n)
        code = 0
        if latch is not None and latch["low"]:
            code = 1 if latch["at"] == self.host.scale_offset(n) else 0
            latch["low"] = False  # the read resets it (a scale or offset change resets it too)
        return OpResult(str(code).encode())

    def clear_clip(self, **_: Any) -> OpResult:
        self.clip_latches.clear()
        return OpResult()

    def _deep(self, n: int, request: FrameRequest) -> DeepRecord | None:
        """Channel n's record for ``request`` as a deep record — the chain captured, nothing
        computed — when it is deeper than ``EAGER_POINTS`` or its samples along the chain exceed the
        model's fan-out (an average of many acquisitions). None: computed whole now. A chain other
        than the scope's own ADC, its digital filters (#1262) and decimator is computed whole (and
        refused beyond the model)."""
        dec = self.host.decimator_unit()
        view = dec.view() if isinstance(dec, ScopeDecimator) else None
        # A high-resolution boxcar is a running sum along the whole record, whose rounding a tile
        # cannot repeat: such a record stays whole as far as the model holds one (MAX_FRAME).
        eager = MAX_FRAME if view is not None and view.proc == "eres" and view.w > 1 else EAGER_POINTS
        if view is None or (request.count <= eager and request.count * view.cost <= MAX_REQUEST_FANOUT):
            return None
        assert dec.owner is not None
        if self.host.input_source(f"in{n}") != (dec.name, f"out{n}"):
            return None
        from .scope_dsp import ScopeDspFilter  # the filter module builds on this one

        filters = []
        src = dec.owner.wiring.source(dec.name, f"in{n}")
        converter = dec.owner.components.get(src[0]) if src is not None else None
        while (
            isinstance(converter, ScopeDspFilter) and src == (converter.name, f"out{n}") and len(filters) < 8
        ):
            filters.append(converter.view(n, request.dt_s))
            src = dec.owner.wiring.source(converter.name, f"in{n}")
            converter = dec.owner.components.get(src[0]) if src is not None else None
        if not isinstance(converter, ScopeAdc) or src != (converter.name, f"out{n}"):
            return None
        return DeepRecord(
            request.count,
            request,
            converter.view(n),
            view,
            self.host.vertical_of(n).correction(),
            tuple(filters[::-1]),
            checkpoint=partial(self.host.session.checkpoint, self.host, f"record{n}"),
        )

    def _trigger_hit(
        self, t0: int, dt: float, pre: int, points: int, waits: bool, earliest_s: float = 0.0
    ) -> TriggerHit | None:
        """The trigger the acquisition starting at ``t0`` finds (``waits``: as far as the trigger's
        search horizon; none before ``earliest_s``), or None."""
        trig = self.host.trigger_unit()
        # The mode's family looks at its own source (#1184): each advanced family keeps one, the
        # edge and glitch families share the main one.
        source = trig.family_source()
        n_trig = trig.source_channel(source)
        kind = trig.source_kind(source)
        factor = 1.0
        if kind == "channel":
            sig, scale_t = self.host.display_signal(n_trig), self.host.scale_offset(n_trig)[0]  # type: ignore[arg-type]
            factor = self.host.vertical_of(n_trig).noise_bandwidth_factor()  # type: ignore[arg-type]
        elif kind == "external":
            sig, scale_t = trig.input("ext"), 1.0
        else:
            sig, scale_t = Signal.zero(), 1.0
        # A waiting acquisition looks as far as its horizon; an auto one (or a screen frame) as
        # far as the auto timeout (#1331).
        horizon = trig.param("search_horizon_s") if waits else trig.param("auto_timeout_s")
        return trig.search(
            sig, t0, dt, pre, points, n_trig, scale_t,
            None if horizon is None else float(horizon),
            earliest_s, factor,
        )  # fmt: skip

    def triggers_now(self) -> bool:
        """Whether an acquisition starting now finds its trigger within its search windows (a
        continuous run's state: it re-arms on every trigger, so it is waiting while none comes)."""
        points, nominal_dt, pre, _ = self.host.timing()
        dt = nominal_dt / (1.0 + self.host.horizontal().clock_error())
        return self._trigger_hit(self.host.session.now_ns, dt, pre, points, waits=False) is not None
