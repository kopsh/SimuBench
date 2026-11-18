"""Oscilloscope acquisition: run control, the record, averaging and segments, the pending
acquisitions (split from ``scope``, #1386)."""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from ...engine.component import (
    Component,
    OpResult,
    PortSpec,
    ScreenState,
    binding_of,
    component,
    op,
)
from ...engine.noise import gaussian, stream_key, uniform
from ...engine.signal import Signal
from ...engine.stream import (
    MAX_FRAME,
    MAX_REQUEST_FANOUT,
    FrameRequest,
    ModelLimit,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope import (
    _SAMPLES_IN,
    ScopeAdc,
    ScopeDecimator,
    ScopeTimebase,
    ScopeVertical,
    _displayed,
    _upper,
    trigger_channel,
)
from .scope_pending import Acquisition, PendingAcquisitions, _Segment
from .scope_record import (
    EAGER_POINTS,
    DeepRecord,
    Record,
    channel_index,
)

if TYPE_CHECKING:
    from .scope_trigger import ScopeTrigger, TriggerHit


@component("scope_acquisition")
class ScopeAcquisition(Component):
    """Run control, sampling and the records of a scope, built from the scope's modules.

    One component for every oscilloscope model, set by parameters (instrument-authoring.md 3.2, 8.4).
    It names the modules it reads: ``horizontal`` (a ``scope_timebase``), ``trigger`` (a
    ``scope_trigger``), ``adc`` (a ``scope_adc``), and per channel ``vertical`` (a
    ``scope_vertical``: the scale and offset) and ``probe`` (a ``scope_probe``, whose output is wired
    to ``in<n>``). Written the earlier way, with the modules' parameters on the acquisition itself and
    no module named, the definition is expanded into the modules when it loads (a macro):

    * **run control** ``mode`` and the values that mean ``auto``, ``normal``, ``single``, ``stop``
      and ``force`` (one frame whatever the trigger, then stop); ``after_single`` is the mode a
      completed single acquisition leaves (empty: the mode stays and the scope stops).
      ``reacquire`` says when a read gets a new acquisition from a running scope: ``explicit``
      (only a digitize command), ``every_read`` or ``on_read_start`` (a read from the first point;
      later pieces read the same record). A pending single acquisition is retried on every read.
      ``missing: acquire`` reads a channel that has no record yet, ``error`` refuses it;
    * **noise** the input noise comes from the channel's ``scope_vertical`` ``noise_table``, per
      division at the input connector, in display volts times the probe ratio; it is keyed by
      ``noise_key``: ``time`` (the absolute sample instants, D46; ``noise_stream`` names a setting
      that selects another stream) or ``acquisition`` (the acquisition count, so pauses of any
      real length between commands give the same numbers); ``trigger_jitter_s``;
      * **processing** (F4, D46) what the quantized codes go through: ``normal`` keeps them;
        ``average`` (``averages``) is the mean of that many acquisitions, each of them noisy and
        quantized, so the noise falls by √N and the resolution gains (it takes N acquisitions'
        time); ``peak`` examines each record interval at the ADC's native rate (at most
        ``peak_max_oversample`` samples per record sample) and keeps, of the interval's smallest
        and largest code, the one that departs farther from its ends — a pulse the record rate
        would step over shows up; ``eres`` smooths ``4**eres_bits`` codes (a boxcar), so the
        noise falls by ``2**eres_bits`` — that many more effective bits, at the boxcar's
        bandwidth.

    * **synchronization** (instrument-authoring.md 8.4, G2): ``completion: immediate`` finishes a
      single acquisition, a digitize or a forced frame within the command that starts it;
      ``pending`` makes it an operation that finishes in virtual time, when the record after the
      trigger is full: until then the trigger status is waiting (then triggered), a read gets the
      previous record (``while_waiting: previous``) or an error (``error``), ``*OPC?`` and ``*WAI``
      wait for it unless ``opc_waits`` is false, and a digitize waits for it. Without a trigger it
      waits for one, looking again whenever a command arrives; ``no_trigger: wait`` leaves ``*OPC?``
      unanswered meanwhile (the client times out), ``return`` answers it at once.

    * **segmented memory** (#957): with ``segments`` above one (while ``segmented``), a single
      acquisition or a digitize takes that many records, each on its own trigger; the trigger is
      armed for the next ``rearm_s`` after a record is full, and the acquisition is done with its
      last segment (in an auto mode a segment whose trigger does not come within the auto timeout
      is taken untriggered; otherwise the acquisition waits). ``segment_index`` (from 1) selects
      the segment that reads, transfers and measurements use; op ``figure`` gives the number of
      segments acquired and the selected segment's time tag. ``memory_points`` limits segments
      times record length (-221). The first segment's noise is an unsegmented acquisition's; each
      later one has its own.

    Status events (for ``status_register`` bits of these names): ``TRG`` when a stored acquisition met
    its trigger, ``DONE`` for every stored acquisition, ``AUTO`` for one an auto mode took without
    its trigger, ``ARM`` when a run or a single acquisition arms the trigger.

    Per-channel ``ch<n>.display`` says which channels are acquired. The trigger status
    (``status_text``), an acquisition type with its count (``acquire_type``, ``acquire_counts``)
    and the read-only figures (op ``figure``: points, sample rate, acquisitions) are declared.
    Measurements are a ``scope_measurements`` module, the transfer and its preamble a
    ``waveform_transfer``.
    """

    label = "采集"
    label_en = "Acquisition"
    about = "示波器的运行控制、采样和记录。"
    about_en = "A scope's run control, sampling and records."

    ports: dict[str, PortSpec] = {}
    port_pattern = r"in\d+"  # inputs in1..inN are created by the instrument wiring
    pattern_port = _SAMPLES_IN  # each channel's samples at the record's rate (the decimator's output)
    refs = {
        "horizontal": ("scope_timebase", "instrument"),
        "trigger": ("scope_trigger", "instrument"),
        "adc": ("scope_adc", "instrument"),
        "decimator": ("scope_decimator", "instrument"),
        "vertical": ("scope_vertical", "channel"),
        "probe": ("scope_probe", "channel"),
    }
    optional_refs = frozenset({"decimator"})  # filled by the acquisition macro; asked for at an acquisition
    defaults = {
        "horizontal": "",  # the scope_timebase component
        "trigger": "",  # the scope_trigger component
        "adc": "",  # the scope_adc component
        "decimator": "",  # the scope_decimator component: the acquisition mode and what it costs in time
        "vertical": "",  # the per-channel scope_vertical component (its name without ch<n>.)
        "probe": "",  # the per-channel scope_probe component (its name without ch<n>.)
        "overhead_s": 1e-3,  # virtual time an acquisition takes besides its record, s
        "digitize_stops": False,  # :DIGitize leaves the scope stopped (the InfiniiVision's rule, #248)
        "mode": "$trigger.mode",  # the run mode setting
        "auto": ["AUTO"],  # modes that keep untriggered acquisitions
        "normal": ["NORMal"],  # modes that wait for a trigger
        "single": [],  # modes that acquire once on the next trigger
        "stop": [],  # modes that stop (none: a stop command stops)
        "force": [],  # modes that take one frame whatever the trigger, then stop
        "after_single": "",  # the mode a completed single acquisition sets (empty: unchanged)
        "reacquire": "explicit",  # explicit | every_read | on_read_start
        "missing": "error",  # error | acquire
        "untriggered": "search",  # the time an untriggered acquisition takes: search | record
        "noise_key": "acquisition",  # acquisition | time
        "noise_stream": "",  # a setting that selects another noise stream (time key)
        "trigger_jitter_s": 0.0,  # rms offset of each record around the trigger, s
        # op status: the trigger status as the model words it (stopped, waiting for a trigger,
        # triggered, running untriggered in an auto mode)
        "status_text": {"stopped": "STOP", "waiting": "READY", "triggered": "TRIG'D", "auto": "AUTO"},
        # op acquire_type: the acquisition type setting and, per type that takes a count, the
        # setting the count writes, its allowed values and how a query writes it, e.g.
        # {AVERage: {setting: acquire.average, values: [4, 16], format: "{kind},{value:d}"}}
        "acquire_type": "",
        "acquire_counts": {},  # type -> {setting, values, format} for the types that take a count
        "completion": "immediate",  # immediate | pending: when a single acquisition or digitize is done
        "opc_waits": True,  # *OPC?, *OPC and *WAI wait for a pending acquisition
        "while_waiting": "previous",  # previous | error: a read while an acquisition is pending
        "no_trigger": "wait",  # wait | return: *OPC? while armed for a trigger that has not come
        "segments": 1,  # records one acquisition takes, each on its own trigger (segmented memory); 1: one
        "segmented": True,  # whether segments applies (e.g. an acquisition mode that is SEGMented)
        "rearm_s": 0.0,  # s from a segment's full record until the trigger is armed for the next
        "segment_index": 1,  # the segment that reads and measurements use, from 1
        "memory_points": None,  # points the segments of one acquisition may take together (None: no limit)
    }
    units = {"rearm_s": "s", "overhead_s": "s", "trigger_jitter_s": "s"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.records: dict[int, Record] = {}
        self.segment_records: list[dict[int, Record]] = []  # the latest acquisition's segments, if segmented
        # the ADC's clip detectors' latches per channel (#1067): the ADC status (:ADC:CLIPped?,
        # low/high, read clears) and the channel's own (:CLIPped?, 0/1, read clears and a scale or
        # offset change away from the latched (scale, offset) resets it)
        self.clip_latches: dict[int, dict[str, Any]] = {}
        # channels whose input went beyond the maximum input range since :OVLRegister? was last read (#1264)
        self.overloaded: set[int] = set()
        self.acq_count = 0
        self.running = True
        self.armed = False
        self.last_triggered = False
        # ty sees the @component class as type[Component] and cannot match it to the protocol.
        self.pending = PendingAcquisitions(cast("Acquisition", self))

    def reset(self) -> None:
        self.records = {}
        self.overloaded = set()
        self.segment_records = []
        self.acq_count = 0
        self.running = True
        self.armed = False
        self.last_triggered = False
        self.pending.clear()

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        """A segment index beyond the segment count is refused (-222); more segments than the
        memory holds at the present record length are refused (-221); the old value is kept."""
        if key == "segment_index":
            if not 1 <= int(new) <= self.segment_count():
                raise ScpiError(-222, f"segment index outside 1 to {self.segment_count()}")
            self._select_segment(int(new))
            self.session.invalidate()
        elif key in ("segments", "segmented"):
            self._check_memory()

    # ---- the modules ------------------------------------------------------------------------------

    def _module(self, key: str, name: str | None = None) -> Any:
        comp = self.peer(name or str(self.param(key)), required=True)
        assert comp is not None
        return comp

    def horizontal(self) -> ScopeTimebase:
        return self._module("horizontal")  # type: ignore[no-any-return]

    def trigger_unit(self) -> ScopeTrigger:
        return self._module("trigger")  # type: ignore[no-any-return]

    def adc_unit(self) -> ScopeAdc:
        return self._module("adc")  # type: ignore[no-any-return]

    def decimator_unit(self) -> Any:
        """The scope_decimator module."""
        return self._module("decimator")

    def vertical_of(self, n: int) -> ScopeVertical:
        return self._module("vertical", f"ch{n}.{self.param('vertical')}")  # type: ignore[no-any-return]

    def probe_ratio(self, channel: int) -> float:
        return float(self._module("probe", f"ch{channel}.{self.param('probe')}").effective_ratio())

    def probe_skew(self, n: int) -> float:
        """Channel n's skew (the setting and the unit's own), seconds; 0 without a probe module."""
        if not self.param("probe"):
            return 0.0
        return self._module("probe", f"ch{n}.{self.param('probe')}").delay()  # type: ignore[no-any-return]

    def scale_offset(self, n: int) -> tuple[float, float]:
        """Channel n's scale (V/div) and offset (V) as displayed."""
        v = self.vertical_of(n)
        return float(v.param("scale")), float(v.param("offset"))

    # ---- helpers ----------------------------------------------------------------------------------

    def _st(self, path: str) -> Any:
        assert self.owner is not None
        return self.owner.get_state(path)

    def _channels(self) -> int:
        assert self.owner is not None
        return self.owner.channel_count

    def _is(self, key: str, value: Any = None) -> bool:
        """Whether the run mode (or ``value``) is one of the values listed in parameter ``key``."""
        return str(self.param("mode") if value is None else value).upper() in _upper(self.param(key))

    def _set_mode(self, value: str) -> None:
        assert self.owner is not None
        path = binding_of(self._params.get("mode"))
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the run mode is not bound to a setting")
        self.owner.set_state(self.resolve_path(path), value)

    def _display_signal(self, n: int) -> Signal:
        """Signal of channel n in display volts, before the converter: what the trigger compares
        (the analog path's shape when the vertical declares one, #1423)."""
        return self.adc_unit().trigger_signal(n)

    def trigger_level(self) -> float:
        """The trigger level in display volts (the scope screen draws it)."""
        return self.trigger_unit().level()

    def timebase_scale(self) -> float:
        return float(self.horizontal().param("scale"))

    def displayed(self) -> list[int]:
        return _displayed(self)

    # ---- record length ------------------------------------------------------------------------------

    def _top_rate(self) -> float:
        return self.adc_unit().top_rate(set(self.displayed()))

    def sample_rate(self) -> float:
        return self.horizontal().sample_rate(self._top_rate())

    def _timing(self) -> tuple[int, float, int, int]:
        """Points, sample interval, samples before the trigger and samples from it on."""
        return self.horizontal().timing(self._top_rate())

    # ---- sampling, noise and the ADC ----------------------------------------------------------------

    def _averages(self) -> int:
        return self.decimator_unit().averages()

    def _jitter(self, t_start_ns: int, frame: int | None = None, segment: int = 0) -> float:
        jitter = float(self.param("trigger_jitter_s"))
        if not jitter:
            return 0.0
        if frame is not None:  # a displayed frame of a running scope (shows_live), not an acquisition
            return jitter * float(
                gaussian(stream_key(self.session.seed, self.uid, "frame", frame), np.zeros(1))[0]
            )
        if self.param("noise_key") == "time":
            key = stream_key(self.session.seed, self.uid, "jitter")
            return jitter * float(gaussian(key, np.array([t_start_ns]))[0])
        # A later segment of the same acquisition has a stream of its own; the first keeps the key
        # an unsegmented acquisition has.
        tail = ("segment", segment) if segment else ()
        key = stream_key(self.session.seed, self.uid, "jitter", self.acq_count, *tail)
        return jitter * float(gaussian(key, np.zeros(1))[0])

    def _request(
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
            return FrameRequest(t0, points, dt, (self.uid, "frame", n, frame), first=start)
        if self.param("noise_key") == "time":
            native_dt = dt / m
            key = (self.uid, "noise", n, native_dt, str(self.param("noise_stream")))
            return FrameRequest(t0, points, dt, key, math.floor(t_start_ns * 1e-9 / native_dt), first=start)
        tail = ("segment", segment) if segment else ()
        return FrameRequest(t0, points, dt, (self.uid, "noise", n, self.acq_count, *tail), first=start)

    def adc_codes_per_div(self) -> float:
        return self.adc_unit().codes_per_div()

    # ---- acquisition ------------------------------------------------------------------------------

    def compute(self, channels: list[int], frame: int | None = None) -> tuple[dict[int, Record], int, bool]:
        """Sample the listed channels at the current instant without changing any state.

        Returns the records, the virtual time an acquisition takes (ns) and whether it triggered.
        ``frame`` numbers a frame the screen of a running scope shows: its noise is its own.
        """
        records, used, triggered, _ = self._acquire(channels, frame)
        return records, used, triggered

    def _acquire(
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
        points, nominal_dt, pre, post = self._timing()
        # A fast clock (positive clock_ppm) samples closer together than the nominal interval; the
        # record keeps the nominal dt the scope reports and reconstructs with, so time measurements
        # read low by that fraction (the spectrum analyzer's reference convention, G3).
        dt = nominal_dt / (1.0 + self.horizontal().clock_error())
        t0 = self.session.now_ns if t0 is None else t0
        earliest_s = 0.0 if earliest_ns is None else max(0.0, (earliest_ns - t0) * 1e-9)
        # An auto mode gives up after its auto timeout; a normal or single acquisition waits for
        # its edge, the virtual time it takes included (G5).
        hit = self._trigger_hit(
            t0, dt, pre, points, waits=not self._is("auto") and frame is None, earliest_s=earliest_s
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
                uniform(stream_key(self.session.seed, self.uid, "frame-phase", frame), np.zeros(1))[0]
            )
            t0 += round(phase * points * dt * 1e9)
        t_start_ns = t0 + round(start * dt * 1e9)
        m = self.decimator_unit().oversample()  # converter samples per record sample (peak detect)
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
            scale, offset = self.scale_offset(n)
            request = self._request(n, t0, start, dt, points, t_start_ns, m, frame, segment)
            # A deep record is computed where it is read (#1012); one up to EAGER_POINTS now, whole:
            # the channel's chain (ADC, then the decimator's acquisition mode) gives the record's
            # samples, the codes, as volts (D46: they are kept).
            deep = self._deep(n, request)
            volts, clip = None, None
            if deep is None:
                frames = self.input_frames(f"in{n}", request)
                volts = self.vertical_of(n).correct(frames.data)
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
            records[n].code_scale = self.adc_unit().converter_scale(
                scale, float(self.vertical_of(n).param("probe_ratio"))
            )
            records[n].clip = clip
        if triggered or self.param("untriggered") == "record":
            used = (k + max(post, 0)) * dt
        else:  # the whole trigger search
            used = points * int(self.trigger_unit().param("search_windows")) * dt
        used = used * self._averages() + float(self.param("overhead_s"))
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
        at = self.scale_offset(n)
        latch = self.clip_latches.setdefault(n, {"adc_low": False, "adc_high": False, "low": False, "at": at})
        latch["adc_low"] |= low
        latch["adc_high"] |= high
        latch["low"] |= low or high
        latch["at"] = at

    def _check_overload(self, channels: list[int], t0_ns: int, span_s: float) -> None:
        """Each acquired channel's input against its limits (#1264): beyond the maximum input range
        the channel's overload bit latches (:OVLRegister?, read clears); beyond the absolute maximum
        the instrument warns of possible damage (an event and the problems list)."""
        add = str(self.adc_unit().param("offset_sign")).lower() == "add"
        for n in channels:
            vertical = self.vertical_of(n)
            over, damage = vertical.overload(add, t0_ns, span_s)
            if over:
                self.overloaded.add(n)
            if damage and self.owner is not None:
                lo, hi = vertical.input_extremes(t0_ns, span_s)
                self.owner.warn(
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

    @op("adc_clip_status")
    def adc_clip_status(self, channel: int | None = None, **_: Any) -> OpResult:
        """``:CHANnel<n>:ADC:CLIPped?``: whether the ADC clipped low, high or both since the last
        read or clear — 0, 1, 2 or 3; reading resets it (the guide, p. 484)."""
        n = channel or 1
        latch = self.clip_latches.get(n)
        code = (2 if latch is not None and latch["adc_high"] else 0) + (
            1 if latch is not None and latch["adc_low"] else 0
        )
        if latch is not None:
            latch["adc_low"] = latch["adc_high"] = False
        return OpResult(str(code).encode())

    @op("clipped_status")
    def clipped_status(self, channel: int | None = None, **_: Any) -> OpResult:
        """``:CHANnel<n>:CLIPped?``: whether the channel clipped at least once (0 or 1) since the
        last read or clear; reading resets it, and so does a change of the channel's scale or
        offset (the guide, p. 485)."""
        n = channel or 1
        latch = self.clip_latches.get(n)
        code = 0
        if latch is not None and latch["low"]:
            code = 1 if latch["at"] == self.scale_offset(n) else 0
            latch["low"] = False  # the read resets it (a scale or offset change resets it too)
        return OpResult(str(code).encode())

    @op("clear_clip")
    def clear_clip(self, **_: Any) -> OpResult:
        """``:ACQuire:ADC:CLIPped:CLEar``: the clipping status of every channel clears at once
        (the guide, p. 319)."""
        self.clip_latches.clear()
        return OpResult()

    def _deep(self, n: int, request: FrameRequest) -> DeepRecord | None:
        """Channel n's record for ``request`` as a deep record — the chain captured, nothing
        computed — when it is deeper than ``EAGER_POINTS`` or its samples along the chain exceed the
        model's fan-out (an average of many acquisitions). None: computed whole now. A chain other
        than the scope's own ADC, its digital filters (#1262) and decimator is computed whole (and
        refused beyond the model)."""
        dec = self.decimator_unit()
        view = dec.view() if isinstance(dec, ScopeDecimator) else None
        # A high-resolution boxcar is a running sum along the whole record, whose rounding a tile
        # cannot repeat: such a record stays whole as far as the model holds one (MAX_FRAME).
        eager = MAX_FRAME if view is not None and view.proc == "eres" and view.w > 1 else EAGER_POINTS
        if view is None or (request.count <= eager and request.count * view.cost <= MAX_REQUEST_FANOUT):
            return None
        assert self.owner is not None and dec.owner is not None
        if self.owner.wiring.source(self.name, f"in{n}") != (dec.name, f"out{n}"):
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
            self.vertical_of(n).correction(),
            tuple(filters[::-1]),
        )

    def _trigger_hit(
        self, t0: int, dt: float, pre: int, points: int, waits: bool, earliest_s: float = 0.0
    ) -> TriggerHit | None:
        """The trigger the acquisition starting at ``t0`` finds (``waits``: as far as the trigger's
        search horizon; none before ``earliest_s``), or None."""
        trig = self.trigger_unit()
        # The mode's family looks at its own source (#1184): each advanced family keeps one, the
        # edge and glitch families share the main one.
        source = trig.family_source()
        n_trig = trig.source_channel(source)
        kind = trig.source_kind(source)
        factor = 1.0
        if kind == "channel":
            sig, scale_t = self._display_signal(n_trig), self.scale_offset(n_trig)[0]  # type: ignore[arg-type]
            factor = self.vertical_of(n_trig).noise_bandwidth_factor()  # type: ignore[arg-type]
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

    # ---- segments (segmented memory, #957) ------------------------------------------------------------

    def segment_count(self) -> int:
        """Records one acquisition takes: ``segments`` while ``segmented``, else one."""
        return max(1, int(self.param("segments"))) if V.flag(self.param("segmented")) else 1

    def _check_memory(self) -> None:
        """The segments of one acquisition fit the memory (``memory_points``), or -221."""
        count, limit = self.segment_count(), self.param("memory_points")
        if count > 1 and limit is not None and count * self._timing()[0] > float(limit):
            raise ScpiError(
                -221,
                f"{count} segments of {self._timing()[0]} points exceed the memory ({int(limit)} points)",
            )

    def acquire_segments(
        self, channels: list[int], taken: list[_Segment] | tuple[()] = (), force: bool = False
    ) -> tuple[list[_Segment], bool]:
        """The segments of one acquisition starting now, after those already ``taken``, without
        changing any state: each on its own trigger, the trigger armed for the next ``rearm_s``
        after a record is full (so with the pre-trigger time to fill first), the holdoff counted
        from the segment before. Returns the segments and whether all of them are there: a segment
        whose trigger does not come ends the list, unless the acquisition is forced or the mode is
        an auto mode (the segment is then taken untriggered)."""
        count = self.segment_count()
        self._check_memory()
        trig = self.trigger_unit()
        now = self.session.now_ns
        _, nominal_dt, pre, _ = self._timing()
        fill_ns = round(pre * nominal_dt * 1e9)
        overhead, rearm = (round(float(self.param(k)) * 1e9) for k in ("overhead_s", "rearm_s"))
        segments = list(taken)
        held = trig.last_ns
        try:
            while len(segments) < count:
                if segments:
                    # Armed since the record before was full and the re-arm time passed; when that
                    # is long ago the pre-trigger memory is full already, and the search starts now.
                    t0, earliest = max(segments[-1].end_ns + rearm, now - fill_ns), now
                    if segments[-1].triggered:
                        trig.last_ns = segments[-1].trigger_ns
                else:
                    t0, earliest = now, None
                records, used, triggered, trigger_ns = self._acquire(
                    channels, t0=t0, segment=len(segments), earliest_ns=earliest
                )
                if not (triggered or force or self._is("auto")):
                    return segments, False
                segments.append(_Segment(records, triggered, trigger_ns, t0 + used - overhead))
        finally:
            trig.last_ns = held
        return segments, True

    def store_segments(self, segments: list[_Segment], used: int) -> int:
        """Keep the segments of one acquisition (:meth:`store` for each); reads and measurements
        then use the one ``segment_index`` selects. Returns ``used``."""
        if len(segments) == 1:
            return self.store(segments[0].records, used)
        for k, seg in enumerate(segments):
            self.store(seg.records, used, segment=k, of=len(segments))
        self.segment_records = [dict(seg.records) for seg in segments]
        self._select_segment(int(self.param("segment_index")))
        return used

    def _selected(self, index: int | None = None) -> int:
        """The selected segment, from 0, among those the latest acquisition has."""
        i = int(self.param("segment_index")) if index is None else index
        return min(max(i, 1), max(len(self.segment_records), 1)) - 1

    def _select_segment(self, index: int) -> None:
        if self.segment_records:
            self.records = dict(self.segment_records[self._selected(index)])

    def segment_time_tag(self) -> float:
        """The selected segment's trigger, seconds after the first segment's (0 without segments)."""
        if not self.segment_records:
            return 0.0

        def instant(records: dict[int, Record]) -> int:
            rec = next(iter(records.values()))
            if rec.trigger_ns is not None:
                return rec.trigger_ns
            return rec.t_start_ns + round(rec.trigger_index * rec.dt_s * 1e9)

        return (instant(self.segment_records[self._selected()]) - instant(self.segment_records[0])) * 1e-9

    def read_segments(self, channel: int) -> list[Record] | None:
        """The latest acquisition's segments' records for a channel (``:WAVeform:SEGMented:ALL``,
        #1066), each segment one full record; None when the latest acquisition was not a segmented
        one or the channel has no records in it. Read as they are: the transfer concatenates."""
        if not self.segment_records:
            return None
        recs: list[Record] = []
        for seg in self.segment_records:
            rec = seg.get(channel)
            if rec is None:
                return None
            recs.append(rec)
        return recs

    @op("set_segment_index")
    def set_segment_index(self, query: bool = False, index: int | None = None, **_: Any) -> OpResult:
        """``:ACQuire:SEGMented:INDex``: the segment the reads and the display use. Beyond the
        segments the latest acquisition has, the guide (p. 345) errors that the data is out of
        range and sets the index to the maximum segment number."""
        assert self.owner is not None
        path = self.bound_path("segment_index")
        if path is None:
            raise ScpiError(-300, f"{self.uid}: segment_index is not bound to a setting")
        if query:
            return OpResult(str(int(self.owner.get_state(path))).encode())
        if index is None:
            raise ScpiError(-109, "an index is required")
        i = int(index)
        limit = max(1, len(self.segment_records))
        if i < 1 or i > limit:
            self.owner.set_state(path, min(max(i, 1), limit))
            raise ScpiError(-222, f"segment {i} is out of range: {limit} segment(s) acquired")
        self.owner.set_state(path, i)
        return OpResult()

    def triggers_now(self) -> bool:
        """Whether an acquisition starting now finds its trigger within its search windows (a
        continuous run's state: it re-arms on every trigger, so it is waiting while none comes)."""
        points, nominal_dt, pre, _ = self._timing()
        dt = nominal_dt / (1.0 + self.horizontal().clock_error())
        return self._trigger_hit(self.session.now_ns, dt, pre, points, waits=False) is not None

    def store(
        self,
        records: dict[int, Record],
        used: int,
        forced: bool = False,
        segment: int | None = None,
        of: int = 1,
    ) -> int:
        """Keep ``records`` as the latest acquisition and log its recipe (D36). Returns ``used``.

        ``forced``: read from a channel that had no record, whatever the run mode. ``segment``
        (from 0) ``of`` that many: one segment of a segmented acquisition (:meth:`store_segments`);
        the acquisition counts once, with its last segment."""
        if segment is None:
            self.segment_records = []  # the latest acquisition is not a segmented one
        self.records.update(records)
        instants = [r.trigger_ns for r in records.values() if r.triggered and r.trigger_ns is not None]
        if instants:
            self.trigger_unit().last_ns = max(instants)  # the holdoff runs from here
            if self.owner is not None:
                self.owner.status_event("TRG")  # the trigger event register (:TER?, #248)
        if self.owner is not None and records:
            # every stored acquisition is done (Infiniium :ADER?, #602); one an auto sweep took
            # without its trigger is an auto trigger (:ATER?)
            self.owner.status_event("DONE")
            if not instants and not forced and self._is("auto"):
                self.owner.status_event("AUTO")
        count = self.acq_count + 1
        if segment is None or segment == of - 1:
            self.acq_count = count
        self.session.invalidate()
        if self.owner is not None:
            # The acquisition's recipe: everything needed to regenerate it from the bench (D36).
            self.session.instrument_event(
                self.owner,
                "acquisition",
                {
                    "count": count,
                    **({} if segment is None else {"segment": segment + 1, "segments": of}),
                    "forced": forced,
                    "channels": {
                        str(ch): {
                            "t_start_ns": rec.t_start_ns,
                            "dt_s": rec.dt_s,
                            "points": rec.points,
                            "trigger_index": rec.trigger_index,
                            "x_origin_s": rec.origin,
                            "triggered": rec.triggered,
                            # a deep record is computed where it is read (#1012): no digest of it
                            **(
                                {"computed": "on_read"}
                                if rec.deep is not None
                                else {
                                    "sha256": hashlib.sha256(
                                        np.ascontiguousarray(rec.volts).tobytes()
                                    ).hexdigest()
                                }
                            ),
                        }
                        for ch, rec in sorted(records.items())
                    },
                    "seed": self.session.seed,
                },
            )
        return used

    # ---- run control ------------------------------------------------------------------------------

    def run_state(self) -> str:
        """RUN, STOP or SING (``:RSTate?``, #248): a single acquisition armed and not yet done reads
        SING, a stopped scope STOP, a running one RUN."""
        if self.armed and self._is("single"):
            return "SING"
        return "RUN" if self.is_running() else "STOP"

    def status_flags(self) -> dict[str, bool]:
        """RUN while acquiring, WAIT while waiting for a trigger (the operation status register).

        A single acquisition waits until it is done; a continuous run (auto, normal) waits while
        its trigger finds no edge now, whatever an earlier acquisition found (#352)."""
        running = self.run_state() != "STOP"
        if not running:
            return {"RUN": False, "WAIT": False}
        if self.waiting():
            return {"RUN": True, "WAIT": True}
        if self._is("auto") or self._is("normal"):
            return {"RUN": True, "WAIT": not self.triggers_now()}
        return {"RUN": True, "WAIT": not self.last_triggered}

    @op("run_state")
    def op_run_state(self, **_: Any) -> OpResult:
        return OpResult(self.run_state().encode())

    def is_running(self) -> bool:
        """Stopped by a stop mode (when the model has one) or by a stop command."""
        if self.param("stop"):
            return not self._is("stop")
        return self.running

    def shows_live(self) -> bool:
        """Whether the screen follows the signal (running in an auto or normal mode) rather than
        holding the last acquisition (stopped, or a single acquisition waiting)."""
        return self.is_running() and (self._is("auto") or self._is("normal")) and not self.armed

    def screen_state(self, frame: int | None = None) -> ScreenState:
        """What the screen shows (the :class:`~simubench.engine.component.ScreenSource` protocol):
        the screen's own rules live here — a running scope redraws the signal now (a frame is not an
        acquisition: no record, no virtual time, noise and untriggered phase of its own, D47); an
        untriggered normal mode holds; a frame beyond the model leaves the screen holding what it
        has (#986). ``frame`` is the projection's redraw counter (ServedSession.frames)."""
        stored = dict(self.records)
        live, records, shown = self.shows_live(), stored, False
        if live and frame is not None:
            try:
                drawn, _, triggered = self.compute(self.displayed(), frame=frame)
                # A triggered or auto frame replaces the records; an untriggered normal frame
                # does not (the screen keeps the last acquisition until the trigger comes).
                if triggered or self._is("auto") or not stored:
                    records, shown = drawn, True
            except ModelLimit:
                live = False  # the screen holds what it has, as a read of such an acquisition is refused
        try:
            _, dt, pre, _ = self._timing()
            screen_left = -pre * dt
        except (AttributeError, ModelLimit, ScpiError, ValueError):  # a sampling scope has no such timing
            screen_left = -5.0 * float(self._st("timebase.scale"))
        return ScreenState(
            live=live,
            shown=shown,
            records=records,
            stored=stored,
            waiting=bool(self.armed and self._is("single")),
            trigger_level=self.trigger_level(),
            trigger_source=channel_index(self._st("trigger.source")),
            acquisitions=int(self.acq_count),
            screen_left_s=screen_left,
        )

    def _finish_single(self) -> None:
        self.armed = False
        self.running = False
        after = str(self.param("after_single") or "")
        if after:
            self._set_mode(after)

    def _acquisition_channels(self, channel: int | None) -> list[int]:
        chans = set(self.displayed())
        if channel is not None and self.param("missing") == "acquire":
            chans.add(channel)
        return sorted(chans)

    def pending_mode(self) -> bool:
        """Acquisitions finish in virtual time; one a front-panel button starts finishes at once,
        as the panel's own (D32: no virtual time passes for a panel operation)."""
        on_panel = self.owner is not None and self.owner.on_panel
        return str(self.param("completion")) == "pending" and not on_panel

    def acquire_now(
        self, channels: list[int] | None = None, force: bool = False, stopped_too: bool = False
    ) -> int:
        """One acquisition attempt as the run mode allows it (``force``: whatever the trigger and the
        run state; ``stopped_too``: also when stopped, still waiting for a trigger). Returns the
        virtual time used (ns): with ``completion: pending`` the attempt is scheduled instead, and
        uses none."""
        if not force and not stopped_too and not self.is_running():
            return 0
        if self.pending_mode() and (force or stopped_too or self._is("single")):
            self.pending.schedule(channels, force)  # continuous runs (auto, normal) refresh at once
            return 0
        chans = self.displayed() if channels is None else channels
        if self.segment_count() > 1:
            segments, complete = self.acquire_segments(chans, force=force)
            self.last_triggered = complete and all(seg.triggered for seg in segments)
            if not complete:
                return 0  # waiting for a segment's trigger: the previous records stay
            used = segments[-1].end_ns + round(float(self.param("overhead_s")) * 1e9) - self.session.now_ns
            self.store_segments(segments, used)
            if self._is("single"):
                self._finish_single()
            return used
        records, used, triggered = self.compute(chans)
        self.last_triggered = triggered
        if force or triggered or self._is("auto"):
            self.store(records, used)
            if self._is("single"):
                self._finish_single()
        return used  # waiting for a trigger: the previous records stay

    def poll(self) -> int:
        """A single acquisition still waiting: try again now (the signal may have arrived since)."""
        if self.pending_mode():
            self.retry_pending()
            return 0
        return self.acquire_now() if self.armed and self._is("single") else 0

    # ---- the pending acquisitions (completion: pending), a PendingAcquisitions ----------------------

    def waiting(self) -> bool:
        """A single acquisition (or a digitize) not done yet."""
        return self.pending.waiting()

    has_retry_pending = True

    def retry_pending(self) -> None:
        """Armed for a trigger that had not come: look again now (the signal may have changed)."""
        self.pending.retry()

    def _keep_last_frame(self) -> None:
        """A running scope has been acquiring all along: before a single acquisition (or a stop)
        replaces the run, the frame it shows now is its latest acquisition, which a read gets
        until the single one is done (completion: pending)."""
        records, used, triggered = self.compute(self.displayed())
        if records and (triggered or self._is("auto")):
            self.store(records, used)

    def read_record(self, channel: int, start: bool = True) -> tuple[Record | None, int]:
        """The record a read uses, and the virtual time getting it took (``reacquire``, ``missing``).

        ``start``: the read starts a transfer (``on_read_start`` acquires anew only then)."""
        used = 0
        how = self.param("reacquire")
        fresh = how == "every_read" or (how == "on_read_start" and start)
        running = self.is_running() and (self._is("auto") or self._is("normal"))
        if self.pending_mode() and self.waiting():
            if str(self.param("while_waiting")) == "error":
                raise ScpiError(-221, "acquisition in progress")
        elif (fresh and running) or (self.armed and self._is("single")):
            used += self.acquire_now(self._acquisition_channels(channel))
        rec = self.records.get(channel)
        if rec is None and self.param("missing") == "acquire":
            # Nothing acquired on this channel yet (stopped, or no trigger): rather than an empty
            # trace, read what the channel shows now, and say so in the event history.
            records, t, _ = self.compute(self._acquisition_channels(channel))
            used += self.store(records, t, forced=True)
            rec = self.records[channel]
        return rec, used

    def transfer_record(self, channel: int, first: int) -> tuple[Record | None, int]:
        """The record a waveform transfer reads (``waveform_transfer``) and the time getting it took;
        ``first`` is the transfer's first point."""
        return self.read_record(channel, start=first == 0)

    def measure_record(self, channel: int) -> tuple[Record, int]:
        """The record a measurement uses: as a read, acquiring the channel when it has none."""
        rec, used = self.read_record(channel)
        if rec is None:
            records, t, _ = self.compute([channel])
            used += self.store(records, t, forced=True)
            rec = self.records[channel]
        return rec, used

    def mode_text(self) -> bytes:
        """The run mode as the definition formats its settings."""
        assert self.owner is not None
        path = self.resolve_path(binding_of(self._params.get("mode")) or "")
        return self.owner.runner.format_setting(path)

    @op("run_mode")
    def run_mode(self, query: bool = False, mode: str | None = None, **_: Any) -> OpResult:
        """``:TRIGger:MODE``, ``TRMD``: a single mode arms one acquisition (and acquires at once if
        it can), a force mode takes one frame and stops, a stop mode stops."""
        if query:
            used = self.poll()
            return OpResult(self.mode_text(), used)
        if mode is None:
            raise ScpiError(-109)
        self.pending.cancel()  # a new mode replaces whatever acquisition was waiting
        if self.pending_mode() and self.is_running() and (self._is("auto") or self._is("normal")):
            self._keep_last_frame()
        self._set_mode(mode)
        if self._is("force", mode):
            used = self.acquire_now(force=True)
            self.running, self.armed = False, False
            return OpResult(duration_ns=used)
        if self._is("single", mode):
            self.running, self.armed = True, True
            if self.owner is not None:
                self.owner.status_event("ARM")  # armed for a trigger (:AER?, #248)
            return OpResult(duration_ns=self.acquire_now())
        self.running, self.armed = not self._is("stop", mode), False
        return OpResult()

    @op("single")
    def single(self, **_: Any) -> OpResult:
        """``ARM``: one acquisition, like setting the first single mode."""
        return self.run_mode(mode=str(self.param("single")[0]))

    @op("run")
    def run(self, **_: Any) -> OpResult:
        """``:TRIGger:RUN``: run again; in a single mode, arm one acquisition. A model whose stop is a
        mode (LeCroy's STOP) runs in its first auto mode."""
        self.running = True
        if self.param("stop") and self._is("stop"):
            self._set_mode(str(self.param("auto")[0]))
        if self.owner is not None:
            self.owner.status_event("ARM")  # armed for a trigger (:AER?, #248)
        if self._is("single"):
            self.armed = True
            return OpResult(duration_ns=self.acquire_now())
        return OpResult()

    @op("stop")
    def stop(self, **_: Any) -> OpResult:
        self.pending.cancel()
        self.running, self.armed = False, False
        stops = self.param("stop")
        if stops:
            self._set_mode(str(stops[0]))
        return OpResult()

    @op("force_trigger")
    def force_trigger(self, **_: Any) -> OpResult:
        """``FRTR``: one acquisition now, triggered or not; a single acquisition then stops."""
        return OpResult(duration_ns=self.acquire_now(force=True))

    @op("wait")
    def wait(self, timeout: float | None = None, **_: Any) -> OpResult:
        """``WAIT [timeout]``: until a pending single acquisition is done (or the timeout passes)."""
        used = self.poll()
        if self.pending_mode() and self.owner is not None:
            if self.pending.due_ns is not None:
                used += self.owner.wait_until(self.pending.due_ns)
            elif timeout is not None and self.waiting():
                used += self.owner.wait_until(self.owner.now_ns() + round(float(timeout) * 1e9))
        return OpResult(duration_ns=used)

    @op("opc")
    def opc(self, query: bool = False, **_: Any) -> OpResult:
        """``*OPC?`` answers ``1`` once a pending single acquisition is done; ``*OPC`` sets the ESR bit."""
        assert self.owner is not None
        used = self.poll()
        if query:
            reply, waited = self.owner.opc_query()  # and whatever else the instrument has pending
            return OpResult(reply, used + waited)
        used += self.owner.wait_pending()
        self.owner.arm_opc()
        return OpResult(duration_ns=used)

    # ---- declared status, acquisition type and figures ----------------------------------------------

    def status(self) -> str:
        """The trigger status in the model's words (``status_text``)."""
        words = {**self.defaults["status_text"], **(self.param("status_text") or {})}
        if not self.is_running():
            return str(words["stopped"])
        if self._is("single"):
            assert self.owner is not None
            if self.pending.trigger_ns is not None and self.owner.now_ns() >= self.pending.trigger_ns:
                return str(words["triggered"])  # the record after the trigger is filling (pending)
            return str(words["waiting"])
        _, _, triggered = self.compute([])
        if self._is("auto"):
            return str(words["triggered" if triggered else "auto"])
        return str(words["triggered" if triggered else "waiting"])

    @op("status")
    def status_query(self, **_: Any) -> OpResult:
        return OpResult(self.status().encode())

    @op("acquire_type")
    def acquire_type_op(self, query: bool = False, kind: str | None = None, value: float | None = None,
                        **_: Any) -> OpResult:  # fmt: skip
        """``:ACQuire:TYPE AVERage,64``: the type, and the count a type takes (``acquire_counts``);
        a count not in the type's ``values`` is refused (-224). The query writes the type, with its
        count by the type's ``format``."""
        assert self.owner is not None
        path = self.resolve_path(binding_of(self._params.get("acquire_type")) or "")
        if not path:
            raise ScpiError(-300, f"{self.uid}: acquire_type names no setting")
        counts = self.param("acquire_counts") or {}
        if query:
            current = str(self._st(path))
            spec = counts.get(current)
            if spec is None:
                return OpResult(current.encode())
            count = self._st(str(spec["setting"]))
            fmt = str(spec.get("format", "{kind},{value}"))
            return OpResult(fmt.format(kind=current, value=count).encode())
        if kind is None:
            raise ScpiError(-109)
        spec = counts.get(kind)
        if spec is not None and value is not None:
            allowed = spec.get("values")
            if allowed is not None and not any(abs(float(value) - float(a)) < 1e-9 for a in allowed):
                raise ScpiError(-224, f"{kind} count must be one of {list(allowed)}")
            setting = str(spec["setting"])
            self.owner.set_state(setting, round(value) if self.owner.specs[setting].type == "int" else value)
        self.owner.set_state(path, kind)
        return OpResult()

    @op("figure")
    def figure(
        self, what: str = "points", integer: bool = False, significant: int = 0, format: str = "", **_: Any
    ) -> OpResult:
        """A read-only figure of the acquisition: ``points`` (record length), ``sample_rate``,
        ``count`` (acquisitions so far, an integer), ``segments`` (the segments the latest
        acquisition has, an integer) or ``time_tag`` (the selected segment's trigger, seconds after
        the first segment's). Numbers come in the definition's number
        format, as an integer with ``integer``, in engineering notation with ``significant``
        digits, or in a printf ``format``."""
        assert self.owner is not None
        if what == "count":
            return OpResult(str(self.acq_count).encode())
        if what == "segments":  # the segments the latest acquisition has (0: it was not segmented)
            return OpResult(str(len(self.segment_records)).encode())
        if integer and what == "points":
            return OpResult(str(int(self._timing()[0])).encode())
        if what == "points":
            value = float(self._timing()[0])
        elif what == "sample_rate":
            value = self.sample_rate()
        elif what == "time_tag":
            value = self.segment_time_tag()
        else:
            raise ScpiError(-300, f"{self.uid}: no figure {what!r}")
        if significant:
            return OpResult(V.format_engineering_digits(value, int(significant)))
        if format:
            return OpResult((format % value).encode())
        return OpResult(self.owner.format_number(value))

    @op("segment_list")
    def segment_list(
        self, what: str = "TTAG", source: Any = None, format: str = "%.10E", **_: Any
    ) -> OpResult:
        """One x figure per segment of the latest acquisition, comma-separated (the Infiniium's
        ``:WAVeform:SEGMented:XLISt?``): ``RELXorigin`` (each segment's first sample, seconds from
        its trigger), ``TTAG`` (its trigger, seconds after the first segment's), ``ABSXorigin`` (the
        two added) or ``OFFSet`` (where it starts in an all-segments transfer: one 0, as only the
        current segment is transferred). Without segments: the current record as one segment."""
        assert self.owner is not None
        kind = str(what).upper()
        n = trigger_channel(source) or 1
        segments = self.segment_records or ([dict(self.records)] if self.records else [])
        if not segments or n not in segments[0]:
            raise ScpiError(-230, "no acquisition for this source")

        def instant(rec: Record) -> int:
            if rec.trigger_ns is not None:
                return rec.trigger_ns
            return rec.t_start_ns + round(rec.trigger_index * rec.dt_s * 1e9)

        first = instant(segments[0][n])
        values: list[float] = []
        for seg in segments:
            rec = seg[n]
            tag = (instant(rec) - first) * 1e-9
            rel = float(rec.origin)
            if kind.startswith("REL"):
                values.append(rel)
            elif kind.startswith("ABS"):
                values.append(rel + tag)
            elif kind.startswith("TTAG"):
                values.append(tag)
            elif kind.startswith("OFFS"):
                return OpResult(b"0")
            else:
                raise ScpiError(-224, f"no segment figure {what!r}")
        return OpResult(",".join(format % v for v in values).encode())

    @op("analyze_segments")
    def analyze_segments(self, **_: Any) -> OpResult:
        """``:ACQuire:SEGMented:ANALyze``: accepted. Measurement statistics and persistence over
        all the segments are not modeled; a measurement reads the selected segment."""
        return OpResult()

    @op("clear_display")
    def clear_display(self, **_: Any) -> OpResult:
        """``:CDISplay``: the display clears and the associated measurements reset (the guide,
        p. 293) — the stored records go (a stopped scope shows nothing until its next
        acquisition, a running one refills them). Waveform memories are not erased; the
        measurement commands stay installed (their values recompute from what is read)."""
        self.records.clear()
        self.segment_records = []
        self.session.invalidate()
        return OpResult()

    @op("points_auto")
    def points_auto(self, query: bool = False, on: bool | None = None, **_: Any) -> OpResult:
        """``:ACQuire:POINts:AUTO``: whether the memory depth follows the AUTO value (the guide,
        p. 339). ON selects the AUTO depth, OFF keeps the current depth as a manual one."""
        return self.horizontal().auto_control("points", query, on)

    @op("srate_auto")
    def srate_auto(self, query: bool = False, on: bool | None = None, **_: Any) -> OpResult:
        """``:ACQuire:SRATe[:ANALog]:AUTO``: whether the sample rate follows AUTO (the guide,
        p. 352). ON selects AUTO, OFF keeps the present rate as a manual one."""
        return self.horizontal().auto_control("sample_rate", query, on, actual=self.sample_rate)

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
        used = self.acquire_now(chans, stopped_too=True)  # whatever the run state
        if self.pending_mode() and self.pending.due_ns is not None and self.owner is not None:
            used += self.owner.wait_until(self.pending.due_ns)  # a digitize returns once its record is full
        if self.param("digitize_stops"):
            self.running, self.armed = False, False  # the InfiniiVision stops after :DIGitize (#248)
        return OpResult(duration_ns=used)

    @op("self_cal")
    def self_cal(self, **_: Any) -> OpResult:
        """A user self-calibration: every channel re-derives its calibration constants against the
        scope's internal references (``scope_vertical.self_calibrate``; a channel without a
        calibration store has nothing to write). The reply is the diagnostic code ``0``; the
        command's ``reply`` template gives the model's words (e.g. ``*CAL {value}``)."""
        for n in range(1, self._channels() + 1):
            self.vertical_of(n).self_calibrate()
        return OpResult(b"0")
