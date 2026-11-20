"""Oscilloscope acquisition: run control, the record, averaging and segments, the pending
acquisitions (split from ``scope``, #1386)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...engine.component import (
    Component,
    OpResult,
    PortSpec,
    ScreenState,
    binding_of,
    component,
    op,
)
from ...engine.signal import Signal
from ...engine.stream import (
    FrameRequest,
)
from ...scpi.errors import ScpiError
from .scope import (
    _SAMPLES_IN,
    ScopeAdc,
    ScopeTimebase,
    ScopeVertical,
    _displayed,
    _upper,
)
from .scope_pending import _Segment
from .scope_record import (
    Record,
)

if TYPE_CHECKING:
    from .scope_trigger import ScopeTrigger
from .scope_capture import Capture
from .scope_recorder import Recorder
from .scope_run import RunControl
from .scope_segments import Segments


@component("scope_acquisition")
class ScopeAcquisition(Component):
    """Run control, sampling and the records of a scope, built from the scope's modules.

    One component for every oscilloscope model, set by parameters.
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
      ``noise_key``: ``time`` (the absolute sample instants; ``noise_stream`` names a setting
      that selects another stream) or ``acquisition`` (the acquisition count, so pauses of any
      real length between commands give the same numbers); ``trigger_jitter_s``;
      * **processing** what the quantized codes go through: ``normal`` keeps them;
        ``average`` (``averages``) is the mean of that many acquisitions, each of them noisy and
        quantized, so the noise falls by √N and the resolution gains (it takes N acquisitions'
        time); ``peak`` examines each record interval at the ADC's native rate (at most
        ``peak_max_oversample`` samples per record sample) and keeps, of the interval's smallest
        and largest code, the one that departs farther from its ends — a pulse the record rate
        would step over shows up; ``eres`` smooths ``4**eres_bits`` codes (a boxcar), so the
        noise falls by ``2**eres_bits`` — that many more effective bits, at the boxcar's
        bandwidth.

    * **synchronization**: ``completion: immediate`` finishes a
      single acquisition, a digitize or a forced frame within the command that starts it;
      ``pending`` makes it an operation that finishes in virtual time, when the record after the
      trigger is full: until then the trigger status is waiting (then triggered), a read gets the
      previous record (``while_waiting: previous``) or an error (``error``), ``*OPC?`` and ``*WAI``
      wait for it unless ``opc_waits`` is false, and a digitize waits for it. Without a trigger it
      waits for one, looking again whenever a command arrives; ``no_trigger: wait`` leaves ``*OPC?``
      unanswered meanwhile (the client times out), ``return`` answers it at once.

    * **segmented memory**: with ``segments`` above one (while ``segmented``), a single
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
        "digitize_stops": False,  # :DIGitize leaves the scope stopped (the InfiniiVision's rule)
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
        self.capture = Capture(self)
        self.segments = Segments(self)
        self.recorder = Recorder(self)
        self.runner = RunControl(self)

    def reset(self) -> None:
        self.recorder.records = {}
        self.capture.overloaded = set()
        self.segments.segment_records = []
        self.recorder.acq_count = 0
        self.runner.running = True
        self.runner.armed = False
        self.runner.last_triggered = False
        self.runner.pending.clear()

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        """A segment index beyond the segment count is refused (-222); more segments than the
        memory holds at the present record length are refused (-221); the old value is kept."""
        if key == "segment_index":
            if not 1 <= int(new) <= self.segments.segment_count():
                raise ScpiError(-222, f"segment index outside 1 to {self.segments.segment_count()}")
            self.segments.select_segment(int(new))
            self.session.invalidate()
        elif key in ("segments", "segmented"):
            self.segments.check_memory()

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

    def get_state(self, path: str) -> Any:
        """A setting's value, read from the instrument (#1724: the blocks' name for it)."""
        assert self.owner is not None
        return self.owner.get_state(path)

    def set_state(self, path: str, value: Any) -> None:
        """A setting's value written on the instrument (the instrument's own judgment applies)."""
        assert self.owner is not None
        self.owner.set_state(path, value)

    def format_number(self, value: float) -> bytes:
        """A number in the definition's response format (a figure a read answers)."""
        assert self.owner is not None
        return self.owner.format_number(value)

    def format_setting(self, path: str) -> bytes:
        """A setting as the definition's command layer formats its replies."""
        assert self.owner is not None
        return self.owner.runner.format_setting(path)

    def status_event(self, name: str) -> None:
        """Latch a momentary event into the status registers (``TRG``, ``DONE``, ``AUTO``, ``ARM``)."""
        assert self.owner is not None
        self.owner.status_event(name)

    def wait_until(self, done_ns: int) -> int:
        """Advance the virtual time until ``done_ns`` (a pending acquisition's instant)."""
        assert self.owner is not None
        return self.owner.wait_until(done_ns)

    def now_ns(self) -> int:
        assert self.owner is not None
        return self.owner.now_ns()

    def opc_query(self) -> tuple[bytes | None, int]:
        """``*OPC?``: answered once every pending operation of the instrument is done."""
        assert self.owner is not None
        return self.owner.opc_query()

    def wait_pending(self) -> int:
        """``*WAI``: wait for every pending operation of the instrument."""
        assert self.owner is not None
        return self.owner.wait_pending()

    def arm_opc(self) -> None:
        """``*OPC``: report the operation-complete bit when the pending operations are done."""
        assert self.owner is not None
        self.owner.arm_opc()

    @property
    def on_panel(self) -> bool:
        """Whether the operation in progress was started from the front panel (no virtual time
        passes for a panel operation, D32)."""
        return self.owner is not None and self.owner.on_panel

    def warn(self, code: str, message: str) -> None:
        """Raise a warning about the instrument (once per code for the session)."""
        if self.owner is not None:
            self.owner.warn(code, message)

    def input_source(self, port: str) -> tuple[str, str] | None:
        """The component and port wired to a signal input of this component, None when nothing
        is (or the component is not placed in an instrument)."""
        return None if self.owner is None else self.owner.wiring.source(self.name, port)

    def channels(self) -> int:
        assert self.owner is not None
        return self.owner.channel_count

    def mode_is(self, key: str, value: Any = None) -> bool:
        """Whether the run mode (or ``value``) is one of the values listed in parameter ``key``."""
        return str(self.param("mode") if value is None else value).upper() in _upper(self.param(key))

    def mode_path(self) -> str | None:
        """The setting the run mode is bound to (None: it is a plain parameter)."""
        return binding_of(self._params.get("mode"))

    def set_mode(self, value: str) -> None:
        assert self.owner is not None
        path = self.mode_path()
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the run mode is not bound to a setting")
        self.owner.set_state(self.resolve_path(path), value)

    def display_signal(self, n: int) -> Signal:
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

    def timing(self) -> tuple[int, float, int, int]:
        """Points, sample interval, samples before the trigger and samples from it on."""
        return self.horizontal().timing(self._top_rate())

    # ---- sampling, noise and the ADC ----------------------------------------------------------------

    def averages(self) -> int:
        return self.decimator_unit().averages()

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
        """Delegates to :meth:`Capture.request`."""
        return self.capture.request(n, t0, start, dt, points, t_start_ns, m, frame, segment)

    def adc_codes_per_div(self) -> float:
        return self.adc_unit().codes_per_div()

    # ---- acquisition ------------------------------------------------------------------------------

    def compute(self, channels: list[int], frame: int | None = None) -> tuple[dict[int, Record], int, bool]:
        """Delegates to :meth:`Capture.compute`."""
        return self.capture.compute(channels, frame)

    def overload_bits(self) -> int:
        """Delegates to :meth:`Capture.overload_bits`."""
        return self.capture.overload_bits()

    @op("adc_clip_status")
    def adc_clip_status(self, channel: int | None = None, **_: Any) -> OpResult:
        """``:CHANnel<n>:ADC:CLIPped?``: whether the ADC clipped low, high or both since the last
        read or clear — 0, 1, 2 or 3; reading resets it (the guide, p. 484)."""
        return self.capture.adc_clip_status(channel, **_)

    @op("clipped_status")
    def clipped_status(self, channel: int | None = None, **_: Any) -> OpResult:
        """``:CHANnel<n>:CLIPped?``: whether the channel clipped at least once (0 or 1) since the
        last read or clear; reading resets it, and so does a change of the channel's scale or
        offset (the guide, p. 485)."""
        return self.capture.clipped_status(channel, **_)

    @op("clear_clip")
    def clear_clip(self, **_: Any) -> OpResult:
        """``:ACQuire:ADC:CLIPped:CLEar``: the clipping status of every channel clears at once
        (the guide, p. 319)."""
        return self.capture.clear_clip(**_)

    # ---- segments (segmented memory, #957) ------------------------------------------------------------

    def segment_count(self) -> int:
        """Delegates to :meth:`Segments.segment_count`."""
        return self.segments.segment_count()

    def acquire_segments(
        self, channels: list[int], taken: list[_Segment] | tuple[()] = (), force: bool = False
    ) -> tuple[list[_Segment], bool]:
        """Delegates to :meth:`Segments.acquire_segments`."""
        return self.segments.acquire_segments(channels, taken, force)

    def store_segments(self, segments: list[_Segment], used: int) -> int:
        """Delegates to :meth:`Segments.store_segments`."""
        return self.segments.store_segments(segments, used)

    def segment_time_tag(self) -> float:
        """Delegates to :meth:`Segments.segment_time_tag`."""
        return self.segments.segment_time_tag()

    def read_segments(self, channel: int) -> list[Record] | None:
        """Delegates to :meth:`Segments.read_segments`."""
        return self.segments.read_segments(channel)

    @op("set_segment_index")
    def set_segment_index(self, query: bool = False, index: int | None = None, **_: Any) -> OpResult:
        """``:ACQuire:SEGMented:INDex``: the segment the reads and the display use. Beyond the
        segments the latest acquisition has, the guide (p. 345) errors that the data is out of
        range and sets the index to the maximum segment number."""
        return self.segments.set_segment_index(query, index, **_)

    def triggers_now(self) -> bool:
        """Delegates to :meth:`Capture.triggers_now`."""
        return self.capture.triggers_now()

    def store(
        self,
        records: dict[int, Record],
        used: int,
        forced: bool = False,
        segment: int | None = None,
        of: int = 1,
    ) -> int:
        """Delegates to :meth:`Recorder.store`."""
        return self.recorder.store(records, used, forced, segment, of)

    # ---- run control ------------------------------------------------------------------------------

    def run_state(self) -> str:
        """Delegates to :meth:`RunControl.run_state`."""
        return self.runner.run_state()

    def status_flags(self) -> dict[str, bool]:
        """Delegates to :meth:`RunControl.status_flags`."""
        return self.runner.status_flags()

    @op("run_state")
    def op_run_state(self, **_: Any) -> OpResult:
        return self.runner.op_run_state(**_)

    def is_running(self) -> bool:
        """Delegates to :meth:`RunControl.is_running`."""
        return self.runner.is_running()

    def shows_live(self) -> bool:
        """Delegates to :meth:`RunControl.shows_live`."""
        return self.runner.shows_live()

    def screen_state(self, frame: int | None = None) -> ScreenState:
        """Delegates to :meth:`RunControl.screen_state`."""
        return self.runner.screen_state(frame)

    def _finish_single(self) -> None:
        """Delegates to :meth:`RunControl.finish_single`."""
        return self.runner.finish_single()

    def pending_mode(self) -> bool:
        """Delegates to :meth:`RunControl.pending_mode`."""
        return self.runner.pending_mode()

    def acquire_now(
        self, channels: list[int] | None = None, force: bool = False, stopped_too: bool = False
    ) -> int:
        """Delegates to :meth:`RunControl.acquire_now`."""
        return self.runner.acquire_now(channels, force, stopped_too)

    def poll(self) -> int:
        """Delegates to :meth:`RunControl.poll`."""
        return self.runner.poll()

    # ---- the pending acquisitions (completion: pending), a PendingAcquisitions ----------------------

    def waiting(self) -> bool:
        """Delegates to :meth:`RunControl.waiting`."""
        return self.runner.waiting()

    has_retry_pending = True

    def retry_pending(self) -> None:
        """Delegates to :meth:`RunControl.retry_pending`."""
        return self.runner.retry_pending()

    def read_record(self, channel: int, start: bool = True) -> tuple[Record | None, int]:
        """Delegates to :meth:`Recorder.read_record`."""
        return self.recorder.read_record(channel, start)

    def transfer_record(self, channel: int, first: int) -> tuple[Record | None, int]:
        """Delegates to :meth:`Recorder.transfer_record`."""
        return self.recorder.transfer_record(channel, first)

    def measure_record(self, channel: int) -> tuple[Record, int]:
        """Delegates to :meth:`Recorder.measure_record`."""
        return self.recorder.measure_record(channel)

    def mode_text(self) -> bytes:
        """Delegates to :meth:`RunControl.mode_text`."""
        return self.runner.mode_text()

    @op("run_mode")
    def run_mode(self, query: bool = False, mode: str | None = None, **_: Any) -> OpResult:
        """``:TRIGger:MODE``, ``TRMD``: a single mode arms one acquisition (and acquires at once if
        it can), a force mode takes one frame and stops, a stop mode stops."""
        return self.runner.run_mode(query, mode, **_)

    @op("single")
    def single(self, **_: Any) -> OpResult:
        """``ARM``: one acquisition, like setting the first single mode."""
        return self.runner.single(**_)

    @op("run")
    def run(self, **_: Any) -> OpResult:
        """``:TRIGger:RUN``: run again; in a single mode, arm one acquisition. A model whose stop is a
        mode (LeCroy's STOP) runs in its first auto mode."""
        return self.runner.run(**_)

    @op("stop")
    def stop(self, **_: Any) -> OpResult:
        return self.runner.stop(**_)

    @op("force_trigger")
    def force_trigger(self, **_: Any) -> OpResult:
        """``FRTR``: one acquisition now, triggered or not; a single acquisition then stops."""
        return self.runner.force_trigger(**_)

    @op("wait")
    def wait(self, timeout: float | None = None, **_: Any) -> OpResult:
        """``WAIT [timeout]``: until a pending single acquisition is done (or the timeout passes)."""
        return self.runner.wait(timeout, **_)

    @op("opc")
    def opc(self, query: bool = False, **_: Any) -> OpResult:
        """``*OPC?`` answers ``1`` once a pending single acquisition is done; ``*OPC`` sets the ESR bit."""
        return self.runner.opc(query, **_)

    # ---- declared status, acquisition type and figures ----------------------------------------------

    def status(self) -> str:
        """Delegates to :meth:`RunControl.status`."""
        return self.runner.status()

    @op("status")
    def status_query(self, **_: Any) -> OpResult:
        return self.runner.status_query(**_)

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
            current = str(self.get_state(path))
            spec = counts.get(current)
            if spec is None:
                return OpResult(current.encode())
            count = self.get_state(str(spec["setting"]))
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
        return self.recorder.figure(what, integer, significant, format, **_)

    @op("segment_list")
    def segment_list(
        self, what: str = "TTAG", source: Any = None, format: str = "%.10E", **_: Any
    ) -> OpResult:
        """One x figure per segment of the latest acquisition, comma-separated (the Infiniium's
        ``:WAVeform:SEGMented:XLISt?``): ``RELXorigin`` (each segment's first sample, seconds from
        its trigger), ``TTAG`` (its trigger, seconds after the first segment's), ``ABSXorigin`` (the
        two added) or ``OFFSet`` (where it starts in an all-segments transfer: one 0, as only the
        current segment is transferred). Without segments: the current record as one segment."""
        return self.segments.segment_list(what, source, format, **_)

    @op("analyze_segments")
    def analyze_segments(self, **_: Any) -> OpResult:
        """``:ACQuire:SEGMented:ANALyze``: accepted. Measurement statistics and persistence over
        all the segments are not modeled; a measurement reads the selected segment."""
        return self.segments.analyze_segments(**_)

    @op("clear_display")
    def clear_display(self, **_: Any) -> OpResult:
        """``:CDISplay``: the display clears and the associated measurements reset (the guide,
        p. 293) — the stored records go (a stopped scope shows nothing until its next
        acquisition, a running one refills them). Waveform memories are not erased; the
        measurement commands stay installed (their values recompute from what is read)."""
        return self.recorder.clear_display(**_)

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
        return self.runner.digitize(sources, **_)

    @op("self_cal")
    def self_cal(self, **_: Any) -> OpResult:
        """A user self-calibration: every channel re-derives its calibration constants against the
        scope's internal references (``scope_vertical.self_calibrate``; a channel without a
        calibration store has nothing to write). The reply is the diagnostic code ``0``; the
        command's ``reply`` template gives the model's words (e.g. ``*CAL {value}``)."""
        for n in range(1, self.channels() + 1):
            self.vertical_of(n).self_calibrate()
        return OpResult(b"0")

    @property
    def overloaded(self) -> Any:
        return self.capture.overloaded

    @overloaded.setter
    def overloaded(self, value: Any) -> None:
        self.capture.overloaded = value

    @property
    def segment_records(self) -> Any:
        return self.segments.segment_records

    @segment_records.setter
    def segment_records(self, value: Any) -> None:
        self.segments.segment_records = value

    @property
    def records(self) -> Any:
        return self.recorder.records

    @records.setter
    def records(self, value: Any) -> None:
        self.recorder.records = value

    @property
    def acq_count(self) -> Any:
        return self.recorder.acq_count

    @acq_count.setter
    def acq_count(self, value: Any) -> None:
        self.recorder.acq_count = value

    @property
    def armed(self) -> Any:
        return self.runner.armed

    @armed.setter
    def armed(self, value: Any) -> None:
        self.runner.armed = value

    @property
    def running(self) -> Any:
        return self.runner.running

    @running.setter
    def running(self, value: Any) -> None:
        self.runner.running = value

    @property
    def pending(self) -> Any:
        return self.runner.pending

    @pending.setter
    def pending(self, value: Any) -> None:
        self.runner.pending = value

    @property
    def last_triggered(self) -> Any:
        return self.runner.last_triggered

    @last_triggered.setter
    def last_triggered(self, value: Any) -> None:
        self.runner.last_triggered = value
