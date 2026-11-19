"""Swept spectrum analyzer as its real blocks (D50, D1 #187).

The analog front end passes signals from block to block (signal-chain.md 5.2, #952): the generic
``input_port`` (the 50 Ω termination, the analyzer's only circuit-layer part) feeds ``specan_input``
(the step attenuator and the mixer-overload check), ``specan_mixer`` (the conversion to the IF,
against ``specan_lo``) and ``specan_if`` (the Gaussian RBW filter, the IF gain); each has an ``out``
an internal probe can read. ``specan_lo`` is the local oscillator: where the receiver is tuned, the
±10 ppm 10 MHz reference (``reference_clock``) its frequency scale runs off, and how fast it sweeps.
The mixer's output and what follows it depend on that tuning: they read ``Session.tune_hz``, set
by whoever starts the read (the trace, a probe), and are the center frequency's outside one.

The blocks live in these modules: this one has the trace (the driver block, which walks the chain); ``specan_frontend`` the analog front end above, ``specan_detect`` the detector and the video filter (signal-chain.md 3.4). The trace's own work is shared out (#1524): ``specan_sweep`` makes a sweep, ``specan_markers`` places the marker, and ``specan_state`` is what the trace remembers and what those two may ask of it.

The blocks behind are data-level: ``specan_detector`` (positive peak / sample / average / RMS and
the log conversion), ``specan_vbw`` (the video filter's bandwidth) and ``specan_trace`` (sweep
timing, the RF history, the trace modes in virtual time, markers and the settings couplings the
scan owns). The trace is evaluated analytically: the lines at the head of the front end's chain,
drawn through the RBW response at every point, plus the noise of the chain's cascaded stages
(``engine.rfcascade``) — the display is calibrated to the input, so an ideal chain's gains cancel.

Lines are exact: every spectral line on RF_IN is drawn through the RBW filter at its power V²/2R.
Noise is the receiver's kT0·F·NBW plus the attenuation, drawn per resolution cell from a
counter-based generator keyed by (seed, sweep, point, cell), so a trace is a pure function of the
settings, the signals RF_IN carried, the seed and the sweep number: two reads at one virtual
instant are identical. The split keeps that property and every reply of the old monolith (see
``tests/test_specan_migration.py``).
"""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np

from ...engine import rfcascade
from ...engine.component import (
    Component,
    OpResult,
    RfStageSpec,
    component,
    op,
)
from ...engine.stream import ModelLimit
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .adc import analog_chain
from .reading_math import axis
from .specan_backend import (
    line_response,
    snap_rbw,
)
from .specan_detect import SpecanDetector, SpecanVbw
from .specan_frontend import SpecanIf, SpecanInput, SpecanLo, SpecanMixer
from .specan_markers import Markers
from .specan_state import HoldState, LinesCache, TraceHistory, TraceSettings, same_signal
from .specan_sweep import Sweeper

F_MAX = 7.5e9


@component("specan_trace")
class SpecanTrace(Component):
    """The trace and scan engine (D1 #187): the analysis window and its settings couplings, the
    sweep timing in virtual time, the RF history, the trace modes (write / average / max / min hold), the
    markers, and the SCPI operations of the display. It reads the analog front end by its wiring:
    from the IF filter upstream to the input, the lines at the head of that chain and the stages'
    cascaded noise figure (the analytic evaluation of signal-chain.md 5.4)."""

    label = "迹线"
    label_en = "Trace"
    about = "频谱仪的迹线与扫描：分析窗口、扫描时间、迹线模式和标记。"
    about_en = "The analyzer's trace and scan: window, sweep timing, trace modes and markers."

    refs = {
        "input": ("specan_input", "instrument"),
        "lo": ("specan_lo", "instrument"),
        "if_filter": ("specan_if", "instrument"),
        "rbw": ("specan_rbw", "instrument"),
        "detector": ("specan_detector", "instrument"),
        "vbw": ("specan_vbw", "instrument"),
    }
    defaults = {
        "input": "",  # the specan_input (attenuation, overload)
        "lo": "",  # the specan_lo (tuning, reference error, sweep rate)
        "if_filter": "",  # the specan_if: the end of the analog front end's chain
        "rbw": "",  # the specan_rbw: the resolution bandwidth and its filter, the end of the digital chain
        # How a trace is made (signal-chain.md 5.4): "analytic" draws every spectral line through
        # the resolution filter's response and adds the receiver's noise per resolution cell;
        # "sampled" tunes across the span and transforms the ADC's samples at each tuning.
        "evaluation": "analytic",
        "detector": "",  # the specan_detector
        "vbw": "",  # the specan_vbw
        "max_hold_sweeps": 999,  # sweeps max or min hold keeps (older ones are dropped, with a warning)
        # A center the window cannot hold at the present span (#292): "slide" keeps the span and
        # moves the center inside the band; "span" keeps the center and narrows the span to fit.
        "center_fit": "slide",
        "hold_count": False,  # a single sweep in max/min hold takes the average/hold count of sweeps (X-Series), not one
        "marker_off_reply": "error",  # a read of an off marker: error (-221) | nan (9.91E+37, the X-Series)
        "delta_x": "absolute",  # X of a delta marker: absolute | offset (from the reference marker, the X-Series)
        "f_max": F_MAX,  # Hz, the highest frequency the window reaches (the model's frequency option, #616)
        "span_min": 10.0,  # Hz, the narrowest swept span (zero span is not modeled)
        "sweep_min": 1.0e-3,  # s, the shortest swept sweep time
        "sweep_max": 10.0e3,  # s, the longest swept sweep time (X-Series: 4000 s)
    }
    units = {
        "f_max": "Hz",
        "span_min": "Hz",
        "sweep_min": "S",
        "sweep_max": "S",
    }
    choices = {
        "marker_off_reply": ("error", "nan"),
        "delta_x": ("absolute", "offset"),
        "evaluation": ("analytic", "sampled"),
    }
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: RF_IN since the restart and the sweeps drawn from it; the front end's lines for the read
        #: in progress; what the hold modes keep (specan_state.py).
        self.history = TraceHistory()
        self.lines = LinesCache()
        self.holds = HoldState()
        self.sweeper = Sweeper(self)
        self.markers = Markers(self)

    # ---- the blocks ------------------------------------------------------------------------------

    def block(self, key: str) -> Any:
        return self.ref(key)

    def input_block(self) -> SpecanInput:
        return self.block("input")

    def lo_block(self) -> SpecanLo:
        return self.block("lo")

    def if_block(self) -> SpecanIf:
        return self.block("if_filter")

    def rbw_block(self) -> Any:  # the SpecanRbw
        return self.block("rbw")

    def sampled(self) -> bool:
        return self.choice("evaluation") == "sampled"

    def detector_block(self) -> SpecanDetector:
        return self.block("detector")

    def vbw_block(self) -> SpecanVbw:
        return self.block("vbw")

    def setting(self, path: str) -> Any:
        assert self.owner is not None
        return self.owner.get_state(path)

    def set_setting(self, path: str, value: Any) -> None:
        assert self.owner is not None
        self.owner.set_state(path, value)

    def option(self, path: str, default: Any) -> Any:
        """A setting the definition may leave out (the #274 marker group): its value, else the
        behaviour of an analyzer with one always-on normal marker."""
        assert self.owner is not None
        return self.owner.get_state(path) if path in self.owner.specs else default

    def rbw(self) -> float:
        return self.rbw_block().rbw()

    def vbw(self) -> float:
        return self.vbw_block().vbw()

    def auto_sweep(self) -> float:
        """The coupled sweep time: what the RBW/VBW need to settle over the span, and no faster
        than the LO can sweep it (``lo_rate``, #292)."""
        rbw = self.rbw()
        span = float(self.setting("freq.span"))
        if self.sampled():
            # every tuning takes the signal its transform (and the filters ahead of it) needs
            plan = self.sweeper.plan(*self.window(), int(self.setting("sweep.points")))
            settle = plan.segments * plan.acquisition
        else:
            settle = 2.5 * span / (rbw * min(rbw, self.vbw()))
        return max(float(self.param("sweep_min")), settle, self.lo_block().sweep_floor(span))

    def sweep_time(self) -> float:
        return self.auto_sweep() if self.setting("sweep.time_auto") else float(self.setting("sweep.time"))

    def attenuation(self) -> float:
        return self.input_block().attenuation()

    def window(self) -> tuple[float, float]:
        c, s = float(self.setting("freq.center")), float(self.setting("freq.span"))
        return c - s / 2, c + s / 2

    def chain(self) -> list[Any]:
        """The analog front end's stages, input first: the IF filter and every ``RfStage`` found
        walking the wiring upstream from it."""
        return analog_chain(self.if_block())

    def noise_figure(self) -> float:
        """The receiver's noise figure the display's floor runs on, dB. The earlier way: the
        cascade of the stages behind the input block, at the center frequency, and the attenuation
        is added where the floor is computed — the display is referred to the input. When the
        input block declares its preamplifier's own figure (#954), its stage already carries the
        attenuator, and the whole chain's cascade is the input-referred figure."""
        center = float(self.setting("freq.center"))
        with self.session.tuned(center):
            stages = [block.stage(center) for block in self.chain()]
        if stages and stages[0].noise_figure_db is not None:
            nf = rfcascade.cascade(stages).noise_figure_db
        else:
            behind = stages[1:]
            nf = rfcascade.cascade(behind).noise_figure_db if behind else None
        if nf is None:
            raise ScpiError(-300, "no stage of the analyzer's front end declares a noise figure")
        return nf

    def _cascade(self) -> RfStageSpec | None:
        """The chain's cascaded figures at the center frequency, or None when no stage declares an
        intercept — then the display draws no distortion products and no compression (#954)."""
        center = float(self.setting("freq.center"))
        with self.session.tuned(center):
            stages = [block.stage(center) for block in self.chain()]
        spec = rfcascade.cascade(stages)
        return (
            spec
            if (math.isfinite(spec.iip2_dbm) or math.isfinite(spec.iip3_dbm) or math.isfinite(spec.p1db_dbm))
            else None
        )

    def settings(self) -> TraceSettings:
        start, stop = self.window()
        att = self.attenuation()
        # The floor's attenuation: 0 when the input block's stage is the whole receiver's figure
        # (it already carries the attenuator), the real one in the earlier way.
        center = float(self.setting("freq.center"))
        head_nf = self.input_block().stage(center).noise_figure_db
        floor_att = 0.0 if head_nf is not None else att
        return TraceSettings(
            start=start,
            stop=stop,
            points=int(self.setting("sweep.points")),
            rbw=self.rbw(),
            sweep=self.sweep_time(),
            att=att,
            detector=self.detector_block().in_use(),
            nf=self.noise_figure(),
            floor_att=floor_att,
            cascade=self._cascade(),
        )

    # ---- restarts and signal history ------------------------------------------------------------

    def _trace_key(self) -> tuple[Any, ...]:
        return (
            *self.settings().key(),
            self.setting("trace.type"),
            int(self.setting("aver.count")),
            self.lo_block().error(),
        )

    def restart(self) -> None:
        now = self.session.now_ns
        self.history.restart_ns = now
        current = self.history.signals[-1][1] if self.history.signals else self.input_block().signal()
        self.history.signals = [(now, current)]
        self.history.sweeps.clear()
        self.holds.held = None
        self.holds.held_upto = -1
        self.holds.warned = False

    def reset(self) -> None:
        self.holds.frozen = None

    def sync(self) -> None:
        if not self.session.signal_changed(self):
            return
        self.lo_block().refresh()
        key = self._trace_key()
        if key != self.history.key:
            self.history.key = key
            self.history.signals = [(self.session.now_ns, self.input_block().signal())]
            self.restart()
            return
        sig = self.input_block().signal()
        last = self.history.signals[-1][1] if self.history.signals else None
        if last is None or not same_signal(last, sig):
            self.history.signals.append((self.session.now_ns, sig))
            if self.sampled():
                # The chain samples what RF_IN carries; it has no earlier signal to go back to.
                # A change of the input therefore starts the trace again: hold and average do
                # not span it (a limit of the sampled evaluation).
                self.restart()

    # ---- traces ---------------------------------------------------------------------------------

    def current_sweep(self, st: TraceSettings) -> int:
        return max(0, int((self.session.now_ns - self.history.restart_ns) * 1e-9 / st.sweep + 1e-9))

    def trace(self) -> tuple[np.ndarray, np.ndarray]:
        """Frequencies and levels (dBm) of the trace as the display shows it now."""
        self.session.sync()
        st = self.settings()
        x = np.asarray(axis(st.start, st.stop, st.points, "LIN"))
        if not self.setting("init.continuous") and self.holds.frozen is not None:
            return x, self.holds.frozen
        return x, self._trace_at(self.current_sweep(st), st)

    def _trace_at(self, s_now: int, st: TraceSettings) -> np.ndarray:
        mode = self.setting("trace.type")
        if mode == "AVERage":
            count = int(self.setting("aver.count"))
            sweeps = range(max(0, s_now - count + 1), s_now + 1)
            return np.mean([self.sweeper.sweep(s, st) for s in sweeps], axis=0)
        if mode in ("MAXHold", "MINHold"):
            pick = np.maximum if mode == "MAXHold" else np.minimum
            limit = int(self.param("max_hold_sweeps"))
            first = max(0, s_now - limit + 1)
            if first > 0 and not self.holds.warned:
                self.holds.warned = True
                assert self.owner is not None
                self.owner.push_error(
                    -221, f"{'max' if mode == 'MAXHold' else 'min'} hold keeps the newest {limit} sweeps"
                )
            if self.holds.held is None or self.holds.held_upto < first - 1 or self.holds.held_upto > s_now:
                self.holds.held, self.holds.held_upto = None, first - 1
            for s in range(self.holds.held_upto + 1, s_now + 1):
                tr = self.sweeper.sweep(s, st)
                self.holds.held = tr.copy() if self.holds.held is None else pick(self.holds.held, tr)
                self.holds.held_upto = s
            assert self.holds.held is not None
            return self.holds.held
        return self.sweeper.sweep(s_now, st)

    # ---- overload -------------------------------------------------------------------------------

    def overload(self) -> bool:
        return self.input_block().overload()

    # ---- readings (UI) --------------------------------------------------------------------------

    def readings(self) -> dict[str, Any]:
        st = self.settings()
        try:
            x, y = self.trace()
        except ModelLimit as limit:
            # the panel says why there is no trace; a command reads the same refusal as an error
            return {
                "values": {"start": st.start, "stop": st.stop, "rbw": st.rbw, "beyond_model": limit.detail}
            }
        mx = float(self.setting("marker.x"))
        i = int(np.argmin(np.abs(x - mx)))
        return {
            "values": {
                "start": st.start,
                "stop": st.stop,
                "rbw": st.rbw,
                "vbw": self.vbw(),
                "sweep_time": st.sweep,
                "uncal": bool(st.sweep < self.auto_sweep() * (1 - 1e-9)),
                "attenuation": st.att,
                "overload": self.overload(),
                "ref_level": float(self.setting("display.ref_level")),
                "marker_x": float(x[i]),
                "marker_y": float(y[i]),
                "reference": self.lo_block().in_use(),
                "sweep": self.current_sweep(st),
            },
            "traces": [
                {
                    "name": "trace1",
                    "t0_s": st.start,
                    "dt_s": (st.stop - st.start) / max(st.points - 1, 1),
                    "values": y.tolist(),
                }
            ],
        }

    # ---- operations: settings with couplings ----------------------------------------------------

    def _window_set(self, center: float, span: float) -> None:
        f_max, span_min = float(self.param("f_max")), float(self.param("span_min"))
        if not span_min <= span <= f_max or not 0.0 <= center <= f_max:
            raise ScpiError(-222)
        if str(self.param("center_fit")) == "span":
            # keep the center, narrow the span to what the band leaves around it
            span = max(span_min, min(span, 2 * center, 2 * (f_max - center)))
        center = min(max(center, span / 2), f_max - span / 2)  # the window slides, no error
        self.set_setting("freq.span", span)
        self.set_setting("freq.center", center)

    @op("frequency")
    def op_frequency(
        self, query: bool = False, what: str = "center", value: float | None = None, **_: Any
    ) -> OpResult:
        c, s = float(self.setting("freq.center")), float(self.setting("freq.span"))
        start, stop = c - s / 2, c + s / 2
        if query:
            return OpResult(
                self.reply_number({"center": c, "span": s, "start": start, "stop": stop}[what], 15)
            )
        assert value is not None
        if what in ("start", "stop") and not 0.0 <= value <= float(self.param("f_max")):
            raise ScpiError(-222)  # an edge past the tuning range (#616)
        if what == "center":
            self._window_set(value, s)
        elif what == "span":
            self._window_set(c, value)
        elif what == "start":
            self._window_set((value + stop) / 2, stop - value)
        else:
            self._window_set((start + value) / 2, value - start)
        return OpResult()

    @op("coupled_sweep")
    def op_coupled_sweep(self, query: bool = False, value: float | None = None, **_: Any) -> OpResult:
        """The sweep time: a manual value turns the automatic coupling off."""
        if query:
            return OpResult(self.reply_number(self.sweep_time(), 10))
        assert value is not None
        if not float(self.param("sweep_min")) <= value <= float(self.param("sweep_max")):
            raise ScpiError(-222)
        self.set_setting("sweep.time", value)
        self.set_setting("sweep.time_auto", False)
        return OpResult()

    @op("status")
    def op_status(self, what: str = "uncal", **_: Any) -> OpResult:
        return OpResult(V.format_bool(self.sweep_time() < self.auto_sweep() * (1 - 1e-9)))

    @op("trace_data")
    def op_trace_data(self, name: str | None = None, **_: Any) -> OpResult:
        if name and name.upper() not in ("TRACE1", "TRAC1", "1"):
            raise ScpiError(-224, f"unknown trace {name!r}")
        _x, y = self.trace()
        why = self.sweeper.truncation(self.history.signal_at(self.session.now_ns), self.settings())
        if why:
            assert self.owner is not None
            self.owner.push_error(-221, why)
        return OpResult(b",".join(self.reply_number(v, 7) for v in y))

    @op("trace_clear")
    def op_trace_clear(self, **_: Any) -> OpResult:
        self.restart()
        self.holds.frozen = None
        return OpResult()

    @op("initiate")
    def op_initiate(self, **_: Any) -> OpResult:
        """Single sweep(s): the averaging count in AVERage mode (and in the hold modes with
        ``hold_count``), else one; takes their sweep time."""
        st = self.settings()
        counted = ("AVERage", "MAXHold", "MINHold") if V.flag(self.param("hold_count")) else ("AVERage",)
        n = int(self.setting("aver.count")) if self.setting("trace.type") in counted else 1
        self.session.sync()
        start_sweep = self.current_sweep(st)
        traces = [self.sweeper.sweep(s, st) for s in range(start_sweep, start_sweep + n)]
        mode = self.setting("trace.type")
        if mode == "AVERage":
            self.holds.frozen = np.mean(traces, axis=0)
        elif mode == "MAXHold":
            self.holds.frozen = np.max(traces, axis=0)
        elif mode == "MINHold":
            self.holds.frozen = np.min(traces, axis=0)
        else:
            self.holds.frozen = traces[-1]
        return OpResult(duration_ns=round(n * st.sweep * 1e9))

    # ---- markers (specan_markers.py) ---------------------------------------------------------------

    watches = ("marker.mode", "marker.enabled")

    def tune_marks(self) -> list[tuple[Literal["marker", "reference"], float]]:
        """The marker while it is on, and the fixed reference a delta marker reads against."""
        return self.markers.tune_marks()

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        self.markers.on_state(key, path, old, new)

    @op("marker")
    def op_marker(
        self, query: bool = False, what: str = "x", value: float | None = None, **_: Any
    ) -> OpResult:
        return self.markers.op_marker(query, what, value)

    @op("marker_off")
    def op_marker_off(self, **_: Any) -> OpResult:
        """:CALCulate:MARKer:AOFF — every marker off (and back to normal mode)."""
        return self.markers.op_marker_off()


__all__ = [
    "F_MAX",
    "SpecanDetector",
    "SpecanIf",
    "SpecanInput",
    "SpecanLo",
    "SpecanMixer",
    "SpecanTrace",
    "SpecanVbw",
    "line_response",
    "snap_rbw",
]
