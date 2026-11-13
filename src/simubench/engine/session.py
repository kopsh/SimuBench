"""Session: a running bench (architecture.md section 5).

The session owns the instruments, bench-level components (DUTs, accessories), the nets between
ports, the bench circuit and its solution, the virtual clock and the run record. Every change goes
through one of its action methods, which run one at a time under a lock (D9).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.bench import LIVE_CLOCKS, BenchDefinition
from ..scpi.errors import ScpiError
from ..scpi.parse import message_is_query
from . import sleep_mode
from .circuit import Circuit, Solution
from .component import Component, component_type
from .deviation import draw_overrides, set_override
from .faults import FaultManager, LinkDown
from .instrument import Instrument
from .portref import split
from .record import Recorder
from .signal import Signal
from .stream import (
    FRAME_CACHE_SAMPLES,
    FRAME_CACHE_SIZE,
    MAX_REQUEST_FANOUT,
    FrameRequest,
    SampleFrame,
    beyond_model,
    check_request,
)

Listener = Callable[[dict[str, Any]], None]
MAX_MODE_ITERATIONS = 40
log = logging.getLogger(__name__)

#: In command-driven mode, idle gaps shorter than this are script overhead and do not advance time.
MAX_TIME_SCALE = 1_000_000.0

IDLE_THRESHOLD_NS = 10_000_000
#: Idle gaps are rounded to this resolution so that sleeps produce stable virtual times.
IDLE_QUANTUM_NS = 1_000_000


@dataclass
class Net:
    id: str
    members: list[str] = field(default_factory=list)  # port references, e.g. "awg.OUT1"


@dataclass(frozen=True)
class CommandResult:
    """What one message did: its reply (None when the message is not a query, or when nothing
    came back — a timeout fault, a busy instrument), whether the message asked for a response
    (the parse layer's verdict, issue 627 — the transports need not guess ``"?" in command``),
    and the errors it produced, including ones a full error queue dropped or replaced with
    -350 (#483)."""

    reply: bytes | None
    errors: list[ScpiError]
    is_query: bool


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _check_scale(scale: float) -> None:
    if not (0 < scale <= MAX_TIME_SCALE) or scale != scale:
        raise ValueError(f"time scale must be above 0 and at most {MAX_TIME_SCALE:g}, not {scale!r}")


class Session:
    def __init__(
        self,
        bench: BenchDefinition,
        definitions: Callable[[str], Any],
        *,
        base_dir: Path | None = None,
        record_dir: Path | None = None,
        clock: str | None = None,
        seed: int | None = None,
        idle: str | None = None,
        record_raw: bool = False,
        time_scale: float | None = None,
    ) -> None:
        self.bench = bench
        self.base_dir = base_dir
        self.seed = bench.run.seed if seed is None else seed
        self.clock_mode = clock or bench.run.clock
        #: Virtual seconds per real second on a live clock: 1 on the wall clock, settable on
        #: ``scaled`` (``set_time_scale``). Time is ``_scaled_base_ns`` + real time since
        #: ``_scaled_ref_ns`` × scale, so changing the scale re-bases instead of jumping.
        self.time_scale = float(time_scale if time_scale is not None else bench.run.time_scale)
        if self.clock_mode == "wall":
            self.time_scale = 1.0
        _check_scale(self.time_scale)
        self.idle_mode = bench.run.idle if idle is None else idle
        if self.idle_mode not in ("wall", "sleep", "off"):
            raise ValueError(f"idle must be wall, sleep or off, not {self.idle_mode!r}")
        self.reveal_true_values = bench.run.reveal_true_values
        self.now_ns = 0
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        #: Recent events, so that a client connecting late sees what happened before (UI history).
        self.history: deque[dict[str, Any]] = deque(maxlen=20_000)
        self._signal_version = 0
        #: Per-component last consumed signal version (signal_changed's subscription registry).
        self._signal_seen: dict[str, int] = {}
        self._electrical_version = 0
        self._circuit: tuple[int, Circuit] | None = None
        self._solution: tuple[int, Solution, dict[str, Signal]] | None = None
        self._output_cache: dict[tuple[int, str, str, float | None], Signal] = {}
        #: The tuning point of the read in progress (:meth:`tuned`); None outside one.
        self.tune_hz: float | None = None
        #: Frames of the sample streams (D67), newest last: (signal version, uid, port, request) ->
        #: (frame, samples asked for along the chain to make it).
        self._frame_cache: OrderedDict[tuple[int, str, str, FrameRequest], tuple[SampleFrame, int]] = (
            OrderedDict()
        )
        #: One entry per :meth:`cached_frames` call in progress: the samples it has asked for so far.
        self._frame_asks: list[int] = []
        self._frame_cached = 0  # samples the frame cache holds
        self._wall_start = time.monotonic_ns()
        self._scaled_ref_ns = self._wall_start
        self._scaled_base_ns = 0
        self._last_wall = self._wall_start
        self.seq = 0
        self.event_id = 0  # id of the last emitted event; ids are stable cursors for event readers
        self.recorder = Recorder(record_dir, self, raw=record_raw) if record_dir else None
        #: Replaying a run record: time moves only where the record says (no idle steps).
        self.replaying = False
        #: Counters for metrics (M4): events by kind, circuit solves and the time they took.
        self.stats: dict[str, float] = {}
        #: The last solve ended without the piecewise-linear devices agreeing on a mode (D52): the
        #: operating point is the last iteration's, consistent but not regulated as asked.
        self.unsettled = False
        #: Set by the serving layer on a served session (``server.manager``): called with the
        #: caller's ``force`` before an explicit advance and raises to refuse it, because scripts
        #: are connected to the ports and their view of time would move (#121, issue 624).
        #: In-process sessions (pytest plugin, ``@simubench`` backend, replay) have no clients.
        self.confirm_advance: Callable[[bool], None] | None = None

        # ---- instruments and bench components ----
        self.instruments: dict[str, Instrument] = {}
        for name, inst in bench.instruments.items():
            self.instruments[name] = Instrument(name, definitions(inst.model), inst, self)
        self.components: dict[str, Component] = {}
        #: Drawn deviations of parts, '<part>.<parameter>' -> value.
        self.part_deviations: dict[str, Any] = {}
        for name, part in bench.parts.items():
            if name in self.instruments:
                raise ValueError(f"name {name!r} used by both an instrument and a part")
            comp = component_type(part.type)(name, part.params, None, self)
            for param, value in part.hidden.items():
                set_override(comp, param, value, invalidate=False)  # construction: nothing solved yet
            self.part_deviations.update(
                {f"{name}.{p}": v for p, v in draw_overrides(comp, part.deviation.items(), self.seed).items()}
            )
            self.components[name] = comp

        # ---- nets ----
        uf = _UnionFind()
        for i, wire in enumerate(bench.wires):
            chain = [wire.from_]
            for k, acc in enumerate(wire.accessories):
                acc_name = f"wire{i}.{acc.type}{k}"
                self.components[acc_name] = component_type(acc.type)(acc_name, acc.params, None, self)
                chain += [f"{acc_name}.IN", f"{acc_name}.OUT"]
            chain.append(wire.to)
            for ref in (wire.from_, wire.to):
                self._check_ref(ref)
            for a, b in zip(chain[0::2], chain[1::2], strict=True):
                uf.union(a, b)
        roots: dict[str, Net] = {}
        self.port_net: dict[str, str] = {}
        for ref in uf.parent:
            root = uf.find(ref)
            if root not in roots:
                roots[root] = Net(f"net-{len(roots)}")
            roots[root].members.append(ref)
            self.port_net[ref] = roots[root].id
        self.nets: dict[str, Net] = {n.id: n for n in roots.values()}

        if self.recorder:
            self.recorder.header()

        # 'sleep' intercepts time.sleep for this process while the bench is open (D47).
        if self.clock_mode == "command" and self.idle_mode == "sleep":
            sleep_mode.register(self)

        # ---- components with state in virtual time ----
        self._dynamic = [c for c in self.all_components() if c.dynamic]
        self._syncing = False
        self.sync()

        # ---- faults preset in the bench ----
        self.faults = FaultManager(self)
        for spec in bench.faults:
            self.faults.inject(spec, source="bench")

    # ---- references ---------------------------------------------------------------------------------

    def deviations(self) -> dict[str, Any]:
        """Every drawn individual deviation: '<instrument>/<component>.<parameter>' and
        '<part>.<parameter>' -> value (empty when every unit is nominal)."""
        out = {
            f"{name}/{k}": v for name, inst in self.instruments.items() for k, v in inst.deviations.items()
        }
        out.update(self.part_deviations)
        return out

    def _check_ref(self, ref: str) -> None:
        owner, port = split(ref)
        if owner in self.instruments:
            if port not in self.instruments[owner].ports:
                raise ValueError(f"wire end {ref!r}: instrument {owner!r} has no port {port!r}")
        elif owner in self.components:
            comp = self.components[owner]
            if port not in comp.ports or comp.ports[port].medium != "electrical":
                raise ValueError(f"wire end {ref!r}: {comp.type_name} has no electrical port {port!r}")
        else:
            raise ValueError(f"wire end {ref!r}: no instrument or DUT named {owner!r}")

    def port_ref(self, comp: Component, port: str) -> str | None:
        """External reference ('awg.OUT1') of a component's electrical port, if it is exposed."""
        if comp.owner is None:
            return f"{comp.name}.{port}"
        for pname, target in comp.owner.ports.items():
            if target == (comp.name, port):
                return f"{comp.owner.name}.{pname}"
        return None

    def node_of(self, comp: Component, port: str) -> str:
        ref = self.port_ref(comp, port)
        if ref is not None and ref in self.port_net:
            return self.port_net[ref]
        return f"{comp.uid}:{port}"  # an unconnected port gets a private node

    # ---- circuit and signals ------------------------------------------------------------------------

    def all_components(self) -> list[Component]:
        """Every component on the bench: the instruments' internal ones and the bench parts and
        wire accessories."""
        out: list[Component] = []
        for inst in self.instruments.values():
            out.extend(inst.components.values())
        out.extend(self.components.values())
        return out

    def circuit(self) -> Circuit:
        if self._circuit is None or self._circuit[0] != self._electrical_version:
            c = Circuit()
            for comp in self.all_components():
                comp.stamp(c, lambda p, comp=comp: self.node_of(comp, p))  # type: ignore[misc]
            self._circuit = (self._electrical_version, c)
        return self._circuit[1]

    def solve(self) -> tuple[Solution, dict[str, Signal]]:
        """Bring the operating point up to date and return it: the solved circuit and every
        source's EMF signal, at the current instant.

        Solving is lazy and memoized: nothing happens when nothing changed since the last solve.
        What a reading cannot get lazily is the mode iteration: piecewise-linear devices
        (D34/D52) re-derive their mode against the solved operating point inside this loop, so a
        component judging its own state (regulating CC or CV, tripped, conducting) calls this
        first and reads afterwards — the readings and the state flags then answer from one
        operating point, and repeat identically until something changes."""
        if self._solution is None or self._solution[0] != self._signal_version:
            piecewise = [c for c in self.all_components() if c.piecewise]
            for _ in range(MAX_MODE_ITERATIONS):
                emf: dict[str, Signal] = {}
                for comp in self.all_components():
                    emf.update(comp.emf())
                freqs: set[float] = {0.0}
                for sig in emf.values():
                    freqs.update(float(f) for f in set(sig.freq.tolist()))
                # The calling thread's CPU, not wall time: a solve that was merely descheduled or
                # waited on a busy machine is not a slower solve, and BLAS pool threads spinning
                # beside it are not solving (issue 209: the suite runs four-wide under xdist).
                started = time.thread_time()
                sol = self.circuit().solve(sorted(freqs))
                self.stats["solves"] = self.stats.get("solves", 0) + 1
                self.stats["solve_seconds"] = (
                    self.stats.get("solve_seconds", 0.0) + time.thread_time() - started
                )
                self._solution = (self._signal_version, sol, emf)
                # Piecewise-linear devices check their mode against the DC operating point.
                changed = False
                for comp in piecewise:

                    def dc(
                        port: str, comp: Component = comp, sol: Solution = sol, emf: dict[str, Signal] = emf
                    ) -> float:
                        node = self.node_of(comp, port)
                        return 0.0 if node not in sol.node_transfer else sol.node_signal(node, emf).mean()

                    def port_signal(
                        port: str, comp: Component = comp, sol: Solution = sol, emf: dict[str, Signal] = emf
                    ) -> Signal:
                        node = self.node_of(comp, port)
                        return Signal.zero() if node not in sol.node_transfer else sol.node_signal(node, emf)

                    dc.signal = port_signal  # type: ignore[attr-defined]  # the whole signal (AC limits, #570)
                    changed |= comp.update_mode(dc)
                if not changed:
                    self.unsettled = False
                    break
                self._signal_version += 1
                self._electrical_version += 1
            else:
                self._settle_unsettled()
        return self._solution[1], self._solution[2]

    def _settle_unsettled(self) -> None:
        """No mode agreed within MAX_MODE_ITERATIONS (D52): solve once more with the modes the
        last iteration chose and keep that, without asking the devices again. Every reading then
        comes from one operating point that satisfies the circuit (V and I agree with the source,
        P = V·I) and repeated queries answer the same until something changes. The first time it
        happens a ``solver`` event (reason ``unsettled``) goes into the run record (#258)."""
        emf: dict[str, Signal] = {}
        for comp in self.all_components():
            emf.update(comp.emf())
        freqs: set[float] = {0.0}
        for sig in emf.values():
            freqs.update(float(f) for f in set(sig.freq.tolist()))
        sol = self.circuit().solve(sorted(freqs))
        self.stats["solves"] = self.stats.get("solves", 0) + 1
        self._solution = (self._signal_version, sol, emf)
        if not self.unsettled:
            self.unsettled = True
            log.warning("piecewise-linear devices did not settle on a mode")
            self.emit(
                {
                    "kind": "solver",
                    "reason": "unsettled",
                    "message": "piecewise-linear devices did not settle on a mode; "
                    "the readings are the last iteration's operating point",
                }
            )

    def node_signal(self, node: str) -> Signal:
        sol, emf = self.solve()
        if node not in sol.node_transfer:
            return Signal.zero()
        return sol.node_signal(node, emf)

    def source_current(self, sid: str) -> float:
        """DC current an ideal source drives out of its node (piecewise devices read their own)."""
        sol, emf = self.solve()
        return sol.source_dc_current(sid, emf)

    def net_signal(self, net_id: str) -> Signal:
        return self.node_signal(net_id)

    def port_signal(self, comp: Component, port: str) -> Signal:
        return self.node_signal(self.node_of(comp, port))

    @contextlib.contextmanager
    def tuned(self, tune_hz: float | None) -> Generator[None]:
        """Evaluate signals with a receiver tuned to ``tune_hz`` (signal-chain.md 5.2): inside the
        block ``tune_hz`` is that frequency, and the outputs of a mixer and of what follows it
        (``Component.tuned_ports``) are the ones at that tuning. Re-entrant: leaving restores the
        outer value.

        The tuning point is an input of the evaluation, nothing more: it is part of
        :meth:`cached_output`'s key, it does not enter the run record, bump the signal version or
        emit an event — a read stays passive. Only the block that starts a read sets it (a
        spectrum analyzer's trace, an internal probe); the front end's blocks read it. Used under
        the session lock (commands and ``view()`` hold it), so it needs no thread-local."""
        outer = self.tune_hz
        self.tune_hz = None if tune_hz is None else float(tune_hz)
        try:
            yield
        finally:
            self.tune_hz = outer

    def cached_output(self, comp: Component, port: str) -> Signal:
        key = (self._signal_version, comp.uid, port, self.tune_hz)
        hit = self._output_cache.get(key)
        if hit is None:
            if len(self._output_cache) > 4096:
                self._output_cache.clear()
            hit = comp.output(port)
            self._output_cache[key] = hit
        return hit

    def cached_frames(self, comp: Component, port: str, request: FrameRequest) -> SampleFrame:
        """A samples output port's frame for one request (signal-chain.md 3.2). The cache is small
        and only serves repeated requests within a read and the internal probe; a new signal
        version drops it. Every sample asked for along the chain counts toward
        ``MAX_REQUEST_FANOUT`` — a cached frame counts what it cost to make, so whether a read is
        refused never depends on what was read before."""
        check_request(request)
        key = (self._signal_version, comp.uid, port, request)
        hit = self._frame_cache.get(key)
        asked = sum(self._frame_asks)
        if hit is None:
            self._charge(asked + request.count, comp, port)
            self._frame_asks.append(request.count)
            try:
                frame = comp.frames(port, request)
            finally:
                cost = self._frame_asks.pop()
            if frame.count != request.count:
                raise ValueError(
                    f"{comp.uid}.{port}: a frame of {frame.count} samples answers a request for {request.count}"
                )
            hit = (frame, cost)
            if self._frame_cache and next(iter(self._frame_cache))[0] != self._signal_version:
                self._frame_cache.clear()
                self._frame_cached = 0
            if frame.count <= FRAME_CACHE_SAMPLES:
                self._frame_cache[key] = hit
                self._frame_cached += frame.count
            while len(self._frame_cache) > FRAME_CACHE_SIZE or self._frame_cached > FRAME_CACHE_SAMPLES:
                self._frame_cached -= self._frame_cache.popitem(last=False)[1][0].count
        else:
            self._charge(asked + hit[1], comp, port)
            self._frame_cache.move_to_end(key)
        if self._frame_asks:
            self._frame_asks[-1] += hit[1]
        return hit[0]

    def reserve_frames(self, comp: Component, port: str, samples: int) -> None:
        """Refuse now a read that is about to ask ``comp``'s upstream for ``samples`` samples in
        several requests (an average of many acquisitions), when they would take it beyond
        ``MAX_REQUEST_FANOUT``: before the first of them is computed, not when the running count
        gets there (#986). Nothing is reserved — the requests are counted as they come."""
        self._charge(sum(self._frame_asks) + samples, comp, port)

    @staticmethod
    def _charge(total: int, comp: Component, port: str) -> None:
        if total > MAX_REQUEST_FANOUT:
            raise beyond_model(
                f"{comp.uid}.{port}: this read asks for {total} samples along the chain "
                f"(at most {MAX_REQUEST_FANOUT})"
            )

    def state_changed(
        self, inst: Instrument, path: str, old: Any, new: Any, *, electrical: bool, source: str
    ) -> None:
        self._signal_version += 1
        if electrical:
            self._electrical_version += 1
        self.emit(
            {"kind": "state", "instrument": inst.name, "path": path, "old": old, "new": new, "source": source}
        )
        self.sync()

    def invalidate(self, electrical: bool = False) -> None:
        """Components call this when their own runtime state changes the signals they produce."""
        self._signal_version += 1
        if electrical:
            self._electrical_version += 1
        self.sync()

    def signal_changed(self, comp: Component) -> bool:
        """Whether the signals changed since ``comp`` last consumed the change through this call
        (its subscription): true at most once per signal version.

        A refresh that must run once per version — a component's :meth:`Component.sync`, or a
        lazy read cache — guards its work with this instead of hand-keeping a seen version. The
        session remembers what each component consumed, so a fresh component reads as changed."""
        seen = self._signal_seen.get(comp.uid)
        if seen == self._signal_version:
            return False
        self._signal_seen[comp.uid] = self._signal_version
        return True

    # ---- events -------------------------------------------------------------------------------------

    def subscribe(self, fn: Listener) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def emit(self, event: dict[str, Any]) -> None:
        kind = str(event.get("kind"))
        self.stats[kind] = self.stats.get(kind, 0) + 1
        self.event_id += 1
        event = {"eid": self.event_id, "t_ns": self.now_ns, **event}
        self.history.append(event)
        if self.recorder:
            self.recorder.event(event)
        for fn in list(self._listeners):
            with contextlib.suppress(Exception):  # a misbehaving listener must not break the bench
                fn(event)

    def instrument_event(self, inst: Instrument, kind: str, data: dict[str, Any]) -> None:
        self.emit({"kind": kind, "instrument": inst.name, **data})

    # ---- clock --------------------------------------------------------------------------------------

    def follow_wall(self) -> None:
        """Engine-internal: wall clock only, bring virtual time up to real time (D47; passive
        views read the present). Called by actions (``_begin_action``) and by ``view`` — external
        code reads the bench through ``view``, never by calling this directly."""
        if self.clock_mode in LIVE_CLOCKS:
            self.sync()
            self.now_ns = max(self.now_ns, self._live_ns(time.monotonic_ns()))

    def _live_ns(self, wall_ns: int) -> int:
        """Virtual time at real time ``wall_ns`` on a live clock (wall, or scaled by ``time_scale``)."""
        return self._scaled_base_ns + round((wall_ns - self._scaled_ref_ns) * self.time_scale)

    def set_time_scale(self, scale: float) -> None:
        """Change a ``scaled`` clock's rate while it runs. Time already passed stays passed: the
        clock is first brought up to now and the new rate counts from there."""
        _check_scale(scale)
        with self._lock:
            if self.clock_mode != "scaled":
                raise RuntimeError("the time scale can only be set on a scaled clock")
            self.follow_wall()
            self._scaled_base_ns = self.now_ns
            self._scaled_ref_ns = time.monotonic_ns()
            self.time_scale = float(scale)

    def _begin_action(self) -> None:
        wall = time.monotonic_ns()
        if self.clock_mode in LIVE_CLOCKS:
            self.sync()
            self.now_ns = max(self.now_ns, self._live_ns(wall))
        elif self.clock_mode == "command" and not self.replaying and self.idle_mode == "wall":
            # Counting idle real time makes results depend on machine speed; only 'wall' does it
            # (D47). 'sleep' and 'off' move time only by command, advance and time.sleep.
            idle = wall - self._last_wall
            if idle >= IDLE_THRESHOLD_NS:
                step = (idle // IDLE_QUANTUM_NS) * IDLE_QUANTUM_NS
                self._advance(step, reason="idle")

    def _end_action(self) -> None:
        self._last_wall = time.monotonic_ns()

    def sync(self) -> None:
        """Let dynamic components record changes at the current instant (exactly, before time
        moves). A component calls this before reading what a dynamic component records; actions
        and passive views run it as part of their protocol."""
        if self._syncing or not getattr(self, "_dynamic", None):
            return
        self._syncing = True
        try:
            for _ in range(4):  # a component may change signals another one reads; settle that
                version = self._signal_version
                for comp in self._dynamic:
                    comp.sync()
                if self._signal_version == version:
                    break
        finally:
            self._syncing = False

    def _advance(self, dt_ns: int, reason: str) -> None:
        if dt_ns <= 0:
            return
        self.sync()
        self.now_ns += int(dt_ns)
        self._signal_version += 1  # time-dependent components (filters, sweeps) may change
        self.emit({"kind": "advance", "dt_ns": int(dt_ns), "reason": reason})
        self.faults.tick()

    # ---- passive views ------------------------------------------------------------------------------

    @contextlib.contextmanager
    def view(self, *, settle: Iterable[str] = ()) -> Iterator[Session]:
        """A passive view of the running bench: one consistent read (glossary 被动视图).

        External code reads the bench only through this. It holds the session lock, follows the
        wall clock, and settles the named instruments (waiting operations that have come due
        finish, as a command's arrival would). It does not advance the virtual clock (D47 idle
        belongs to actions), emit events, or touch the communication record (D32).

        Going around it — taking ``_lock``, calling ``follow_wall``/``settle_pending`` directly —
        silently drops part of the protocol: ``read_true_values`` once missed ``follow_wall`` that
        way and read stale times on the wall clock."""
        with self._lock:
            self.follow_wall()
            for name in settle:
                self.instrument(name).settle_pending()
            yield self

    def solution(self) -> tuple[Solution, dict[str, Signal]]:
        """The solved circuit and every source's EMF signal, at the current instant. A passive
        view's read (call it inside ``view()``) — :meth:`solve` by its passive name (#463)."""
        return self.solve()

    # ---- actions ------------------------------------------------------------------------------------

    def instrument(self, name: str) -> Instrument:
        try:
            return self.instruments[name]
        except KeyError:
            raise KeyError(f"no instrument named {name!r}") from None

    def write(self, name: str, data: bytes, client: str = "sdk") -> None:
        """Send a program message to an instrument (action: SCPI)."""
        with self._lock:
            self._begin_action()
            inst = self.instrument(name)
            link = self.faults.link(name)
            for f in link:
                if f.spec.kind == "disconnect":
                    self._end_action()
                    raise LinkDown(f"link to {name} is down (fault {f.id})")
                if f.spec.kind == "latency":
                    self._delay(f.spec.latency_s or 0.0)
            t0 = self.now_ns
            wall0 = time.time()
            dropped = next((f for f in link if f.spec.kind == "timeout"), None)
            if dropped is not None:
                # The message never reaches the instrument.
                self.faults.use(dropped)
                self.emit(
                    {
                        "kind": "scpi",
                        "dir": "write",
                        "instrument": name,
                        "data": data,
                        "client": client,
                        "fault": dropped.id,
                        "duration_ns": 0,
                    }
                )
                self._end_action()
                return
            duration = inst.receive(data)
            if self.clock_mode == "command" and duration:
                self._advance(duration, reason="command")
            self.seq += 1
            self.emit(
                {
                    "kind": "scpi",
                    "dir": "write",
                    "instrument": name,
                    "data": data,
                    "t_start_ns": t0,
                    "duration_ns": duration,
                    "wall": wall0,
                    "client": client,
                }
            )
            self._end_action()

    def read(self, name: str, client: str = "sdk") -> bytes | None:
        """Take the next queued response message, or None if nothing is queued."""
        with self._lock:
            self._begin_action()
            inst = self.instrument(name)
            link = self.faults.link(name)
            fault_id = None
            for f in link:
                if f.spec.kind == "disconnect":
                    self._end_action()
                    raise LinkDown(f"link to {name} is down (fault {f.id})")
                if f.spec.kind in ("timeout", "busy"):
                    # Nothing arrives; the response (if any) stays queued until the fault ends.
                    if f.spec.kind == "timeout":
                        self.faults.use(f)
                    self._end_action()
                    return None
                if f.spec.kind == "latency":
                    self._delay(f.spec.latency_s or 0.0)
            out = inst.take_output()
            if out is not None:
                garble = next((f for f in link if f.spec.kind == "garble"), None)
                if garble is not None:
                    out = self.faults.garble(garble, out)
                    fault_id = garble.id
                    self.faults.use(garble)
                event = {
                    "kind": "scpi",
                    "dir": "read",
                    "instrument": name,
                    "data": out,
                    "wall": time.time(),
                    "client": client,
                }
                if fault_id:
                    event["fault"] = fault_id
                self.emit(event)
            self._end_action()
            return out

    def exchange(self, name: str, data: bytes, client: str = "sdk", reads: int | None = None) -> list[bytes]:
        """Write a program message and take its responses in one action, so that no other client
        (a script on a port, the UI console) can take a response in between (#121).

        ``reads`` limits how many messages are taken; by default every queued one."""
        with self._lock:
            self.write(name, data, client)
            out: list[bytes] = []
            while reads is None or len(out) < reads:
                msg = self.read(name, client)
                if msg is None:
                    break
                out.append(msg)
            return out

    def clear_output(self, name: str, client: str = "sdk") -> None:
        """Device clear: drop every queued response message (the communication layer of
        IEEE 488.2 device clear, shared by the PyVISA backend and the HiSLIP server)."""
        with self._lock:
            inst = self.instrument(name)
            while inst.take_output() is not None:
                pass

    def _delay(self, seconds: float) -> None:
        """Link latency: virtual time on the command clock, real time on the wall clock."""
        if seconds <= 0:
            return
        if self.clock_mode == "command":
            self._advance(round(seconds * 1e9), reason="latency")
        elif self.clock_mode in LIVE_CLOCKS:
            time.sleep(min(seconds / self.time_scale, 5.0))  # real time for that much virtual time

    def query(self, name: str, data: bytes | str, client: str = "sdk") -> bytes:
        if isinstance(data, str):
            data = data.encode()
        self.write(name, data, client)
        out = self.read(name, client)
        return out if out is not None else b""

    def command(self, name: str, data: bytes | str, client: str = "sdk") -> CommandResult:
        """Send one message and take its response as one action, reporting the error-queue
        entries it added. Write and read are one exchange (#121): no other client's message can
        land in between, and a script's response cannot be taken here."""
        if isinstance(data, str):
            data = data.encode()
        with self._lock:
            inst = self.instrument(name)
            before = inst.errors.mark()
            got = self.exchange(name, data, client, reads=1)
            return CommandResult(
                got[0] if got else None, inst.errors.since(before), is_query=message_is_query(data)
            )

    def scpi(self, name: str, text: str) -> str:
        """Convenience for tests and notebooks: one message as one command (#121), decoded reply or ''."""
        out = self.command(name, text).reply
        return out.decode(errors="replace").rstrip("\n") if out is not None else ""

    def calendar(self) -> dt.datetime:
        """Calendar time now: the bench's run.start plus virtual time (instrument clocks, due dates)."""
        return self.bench.run.start + dt.timedelta(microseconds=self.now_ns // 1000)

    def seek(self, t_ns: int, reason: str = "replay") -> None:
        """Move virtual time forward to ``t_ns`` (replaying a record); never backwards."""
        with self._lock:
            if t_ns > self.now_ns:
                self._advance(t_ns - self.now_ns, reason=reason)

    def advance(self, seconds: float, reason: str = "explicit", force: bool = False) -> None:
        """Advance virtual time (action: advance). Works in every clock mode except wall.

        On a served session the serving layer's ``confirm_advance`` refuses while scripts are
        connected to the ports, unless ``force`` (issue 624); the guard travels with the call, so
        no facade can forget it.
        """
        with self._lock:
            if self.confirm_advance is not None:
                self.confirm_advance(force)
            if self.clock_mode in LIVE_CLOCKS:
                raise RuntimeError(f"cannot advance a {self.clock_mode} clock")
            self._advance(round(seconds * 1e9), reason=reason)

    def wait(self, seconds: float, reason: str = "wait") -> None:
        """Time a client spends waiting: a read that timed out, a wait for a service request.

        Virtual time passes on a command clock and real time on a wall clock, so the wait costs
        what the client waited. Unlike :meth:`advance` it is not refused while scripts are
        connected (#121, issue 624): the one waiting is one of them, and its own view of time is
        what moves. It is recorded like an advance and replays as one."""
        if seconds <= 0:
            return
        if self.clock_mode == "wall":
            time.sleep(seconds)
            return
        with self._lock:
            self._advance(round(seconds * 1e9), reason=reason)

    def configure(self, name: str, path: str, value: Any, client: str = "ui") -> None:
        """Out-of-band state change from the UI, MCP or SDK (action: configure)."""
        with self._lock:
            self._begin_action()
            try:
                self.instrument(name).set_state(path, value, source="configure", outside=True)
            finally:
                self._end_action()

    def panel(self, name: str, action: str, client: str = "ui") -> None:
        """A front-panel operation (Run, Stop, Single; D32): one ``panel`` event, no SCPI, no time."""
        with self._lock:
            self._begin_action()
            try:
                self.instrument(name).panel(action)
                self.emit({"kind": "panel", "instrument": name, "action": action, "source": client})
            finally:
                self._end_action()

    def set_parameter(
        self, owner: str, component: str, parameter: str, value: Any, client: str = "api"
    ) -> None:
        """Write a hidden parameter of the running bench (action: set_parameter): the one product
        door to the hidden state, and the ground D39's calibration-coefficient writes stand on.
        ``owner`` is the instrument or part name, ``component`` the component inside the
        instrument (empty for a part, which is its own component). Not in the communication
        record (D32, like a panel operation); one ``hidden`` event."""
        with self._lock:
            self._begin_action()
            try:
                if owner in self.components:
                    if component and component != owner:
                        raise KeyError(f"{owner} is a part: it has no component {component!r}")
                    comp = self.components[owner]
                elif owner in self.instruments and component in self.instruments[owner].components:
                    comp = self.instruments[owner].components[component]
                else:
                    raise KeyError(f"no part {owner!r} or component {owner}/{component!r}")
                set_override(comp, parameter, value)
                self.emit(
                    {
                        "kind": "hidden",
                        "owner": owner,
                        "component": component,
                        "parameter": parameter,
                        "value": value,
                        "source": client,
                    }
                )
            finally:
                self._end_action()

    def try_configure(self, name: str, path: str, value: Any, client: str = "ui") -> ScpiError | None:
        try:
            self.configure(name, path, value, client)
        except ScpiError as e:
            return e
        return None

    # ---- lifecycle ----------------------------------------------------------------------------------

    def close(self) -> None:
        sleep_mode.unregister(self)
        if self.recorder:
            self.recorder.close()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
