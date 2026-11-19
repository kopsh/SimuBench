"""The swept analyzer's trace engine, as state objects and the small interface its helpers read
(#1524): what a trace remembers, and what the helpers that draw it may ask of the engine.

``SpecanTrace`` (``specan.py``) used to keep eleven loose attributes and every method that drew a
trace or placed a marker on it. The attributes are three small models here — the RF history and
the sweeps computed from it (:class:`TraceHistory`), the front end's lines for the read in
progress (:class:`LinesCache`) and what the hold modes keep (:class:`HoldState`) — and the methods
live in two collaborators that read the engine only through :class:`TraceHost`: ``Sweeper``
(``specan_sweep.py``: the lines and the two evaluations of a sweep) and ``Markers``
(``specan_markers.py``: the marker and its peak search).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from ...engine.component import RfStageSpec
from ...engine.signal import Signal

if TYPE_CHECKING:
    from ...engine.instrument import Instrument
    from ...engine.session import Session
    from .specan_markers import Markers
    from .specan_sweep import Sweeper

#: Sweeps kept in :attr:`TraceHistory.sweeps` before the cache starts over.
SWEEP_CACHE_SIZE = 4096


def same_signal(a: Signal, b: Signal) -> bool:
    """Whether two signals are the same lines (the history appends a signal only when it changed)."""
    return (
        a.dc == b.dc
        and a.freq.shape == b.freq.shape
        and bool(np.array_equal(a.freq, b.freq))
        and bool(np.array_equal(a.amp, b.amp))
        and bool(np.array_equal(a.t0_ns, b.t0_ns))
    )


class _State(BaseModel):
    """Mutable (the engine updates it in place); fields may hold arrays and signals."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SweepPlan(BaseModel):
    """How a sampled sweep is taken (signal-chain.md 5.4): the span cut into ``segments``
    tunings, each a transform of ``points`` frequency points ``df`` apart of which the middle
    ``per`` are kept, laid side by side from ``origin`` upward."""

    model_config = ConfigDict(frozen=True)

    dt: float  # s, the interval the transform's samples are at (nominal)
    points: int
    df: float  # Hz
    per: int
    segments: int
    origin: float  # Hz, the frequency of the first point kept
    cost: int  # samples one tuning asks for along the whole chain
    acquisition: float  # s of signal one tuning takes at the ADC


class TraceSettings(BaseModel):
    """The settings one trace read draws with. Identity matters: :class:`LinesCache` is keyed by
    the object, and a new read builds a new one."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    start: float
    stop: float
    points: int
    rbw: float
    sweep: float
    att: float
    detector: str
    nf: float
    #: The attenuation the noise floor adds on top of the figure: the real one in the earlier way
    #: (the figure is behind the attenuator), 0 when the input block's own figure is the whole
    #: receiver's (#954).
    floor_att: float = 0.0
    #: The chain's cascaded figures at the center frequency, when any stage declares intercepts:
    #: the distortion products and the compression the display draws (#954). None: an ideal chain.
    cascade: RfStageSpec | None = None

    def key(self) -> tuple[Any, ...]:
        cascade = self.cascade
        figures = (cascade.gain_db, cascade.iip2_dbm, cascade.iip3_dbm, cascade.p1db_dbm) if cascade else ()
        return (
            self.start,
            self.stop,
            self.points,
            self.rbw,
            self.sweep,
            self.att,
            self.detector,
            self.nf,
            *figures,
        )


class TraceHistory(_State):
    """What RF_IN carried since the trace restarted, and the sweeps drawn from it."""

    restart_ns: int = 0
    #: The settings the history was started under (:meth:`SpecanTrace._trace_key`).
    key: tuple[Any, ...] | None = None
    #: RF_IN since the restart: (from, signal), oldest first.
    signals: list[tuple[int, Signal]] = Field(default_factory=list)
    #: Finished sweeps by (sweep number, index of the signal they were drawn from).
    sweeps: dict[tuple[int, int], np.ndarray] = Field(default_factory=dict)

    def latest(self) -> Signal | None:
        return self.signals[-1][1] if self.signals else None

    def restart_at(self, now_ns: int, current: Signal) -> None:
        """The trace starts again at ``now_ns`` from the signal RF_IN carries."""
        self.restart_ns = now_ns
        self.signals = [(now_ns, current)]
        self.sweeps.clear()

    def index(self, t_ns: int) -> int:
        """Which of the signals was the input at ``t_ns`` (the first, before any)."""
        idx = 0
        for i, (t, _) in enumerate(self.signals):
            if t <= t_ns:
                idx = i
            else:
                break
        return idx

    def signal_at(self, t_ns: int) -> Signal:
        sig = self.signals[0][1] if self.signals else Signal.zero()
        for t, s in self.signals:
            if t <= t_ns:
                sig = s
            else:
                break
        return sig

    def keep(self, sweep: int, version: int, trace: np.ndarray) -> np.ndarray:
        """Remember a finished sweep (the cache starts over when it is full)."""
        if len(self.sweeps) > SWEEP_CACHE_SIZE:
            self.sweeps.clear()
        self.sweeps[(sweep, version)] = trace
        return trace


class LinesCache(_State):
    """The front end's lines for the signal and settings one trace read is sweeping over: the
    sweeps of an average (or a trace cache refill) reuse them instead of re-deriving the chain
    for every sweep. Identity-keyed: a new read builds a new settings object."""

    sig: Signal | None = None
    st: TraceSettings | None = None
    lines: tuple[np.ndarray, np.ndarray, str | None] | None = None


class HoldState(_State):
    """What the trace modes keep between reads."""

    held: np.ndarray | None = None  # the max / min hold so far
    held_upto: int = -1  # the newest sweep in ``held``
    frozen: np.ndarray | None = None  # the trace a single sweep took
    warned: bool = False  # the hold's sweep limit was reported once

    def clear(self) -> None:
        """A restart drops the running hold (a frozen single sweep stays until it is cleared)."""
        self.held = None
        self.held_upto = -1
        self.warned = False


class TraceHost(Protocol):
    """What the trace's helpers ask of the engine: its settings and the blocks of the chain, the
    state objects, the instrument and the session."""

    owner: Instrument | None
    session: Session
    history: TraceHistory
    lines: LinesCache
    sweeper: Sweeper
    markers: Markers

    @property
    def uid(self) -> str: ...

    def setting(self, path: str) -> Any: ...
    def set_setting(self, path: str, value: Any) -> None: ...
    def option(self, path: str, default: Any) -> Any: ...
    def choice(self, key: str) -> str | None: ...
    def reply_number(self, value: float, digits: int = 6) -> bytes: ...
    def input_block(self) -> Any: ...  # SpecanInput (ty reads a @component class as a variable)
    def lo_block(self) -> Any: ...  # SpecanLo (ty reads a @component class as a variable)
    def if_block(self) -> Any: ...  # SpecanIf (ty reads a @component class as a variable)
    def rbw_block(self) -> Any: ...  # SpecanRbw (ty reads a @component class as a variable)
    def detector_block(self) -> Any: ...  # SpecanDetector (ty reads a @component class as a variable)
    def chain(self) -> list[Any]: ...
    def sampled(self) -> bool: ...
    def settings(self) -> TraceSettings: ...
    def current_sweep(self, st: TraceSettings) -> int: ...
    def trace(self) -> tuple[np.ndarray, np.ndarray]: ...
