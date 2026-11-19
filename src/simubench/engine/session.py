"""Session: a running bench (architecture.md section 5).

The session owns the instruments, bench-level components (DUTs, accessories) and the nets between
ports, and is the facade of the bench's actions and passive views: every change goes through one
of its action methods, which run one at a time under a lock (D9). What has state of its own lives
in a collaborator (#1508, #1531): the virtual clock (``clock``), the event stream (``events``), the
signal and frame caches (``cache``), the operating point (``solver``) and the instruments' files
(``files``).
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import Unknown
from ..models.bench import BenchDefinition
from ..scpi.errors import ScpiError
from ..scpi.parse import message_is_query
from . import nets, sleep_mode
from .budget import ComputeBudget, aborted
from .circuit import Circuit, Solution
from .clock import Clock
from .component import Component, component_type
from .deviation import draw_overrides, set_override
from .evalcache import EvalCache
from .evalcontext import EvalContext
from .eventbus import EventBus
from .faults import FaultManager, LinkDown
from .instrument import Instrument
from .instrument_files import InstrumentFiles
from .portref import split
from .record import Recorder
from .signal import Signal
from .solver import Solver
from .stream import (
    FrameRequest,
    SampleFrame,
)

log = logging.getLogger(__name__)


def _header_of(data: bytes) -> str:
    """The first command header of a program message, for an advance event (#1260). An empty
    program-message unit — ``;`` leading, trailing or alone — is tolerated the way a real
    instrument tolerates it (#1282): the first non-empty unit names the message, or nothing does."""
    for unit in data.decode(errors="replace").strip().split(";"):
        words = unit.split()
        if words:
            return words[0][:80]
    return ""


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
        files_dir: Path | None = None,
        compute_budget_s: float | None = None,
    ) -> None:
        self.bench = bench
        self.base_dir = base_dir
        self.seed = bench.run.seed if seed is None else seed
        self.reveal_true_values = bench.run.reveal_true_values
        self._lock = threading.RLock()
        #: Virtual time, how it moves and the time actions (:mod:`.clock`, D47).
        self.clock = Clock(
            self,
            self._lock,
            mode=clock or bench.run.clock,
            time_scale=time_scale if time_scale is not None else bench.run.time_scale,
            idle=bench.run.idle if idle is None else idle,
            start=bench.run.start,
        )
        #: The event stream: history, metrics, the run record's feed, the listeners (:mod:`.eventbus`).
        self.events = EventBus(lambda: self.clock.now_ns)
        #: The instruments' virtual file systems (#1069): ``<files_dir>/<instrument>/``, kept across
        #: runs; without ``files_dir`` a temporary folder of this session, removed when it closes.
        self.files = InstrumentFiles(files_dir, bench.instruments)
        self._signal_version = 0
        #: Per-component last consumed signal version (signal_changed's subscription registry).
        self._signal_seen: dict[str, int] = {}
        self._electrical_version = 0
        #: The ambient inputs of the evaluation in progress (:meth:`evaluating`).
        self._context = EvalContext()
        #: What was evaluated under (version, port, context): signals and sample-stream frames.
        self.cache = EvalCache(checkpoint=self.checkpoint)
        #: How long one action or view may compute, and the cancel flag (:mod:`.budget`, D88).
        self.budget = ComputeBudget(compute_budget_s)
        #: A ``view`` is in progress: it emits nothing, so an abort in it leaves no event (D55).
        self._in_view = False
        self.seq = 0
        self.recorder = Recorder(record_dir, self, raw=record_raw) if record_dir else None
        if self.recorder:
            self.events.record = self.recorder.event
        #: The operating point: the circuit, its memoized solution and the mode iteration.
        self.solver = Solver(self)
        #: Port reference -> net id, empty until the wires below build the nets. Here from birth:
        #: the instruments are constructed first and the instrument seam's contract (InstrumentHost,
        #: #1380) carries ``port_net``, so the host answers for it before the wiring runs.
        self.port_net: dict[str, str] = {}

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

        # ---- nets (nets.py, issue 1381: the same walk the bench validation groups) ----
        uf = nets.UnionFind()
        for i, wire in enumerate(bench.wires):
            for k, acc in enumerate(wire.accessories):
                name = nets.accessory_name(i, k, acc.type)
                self.components[name] = component_type(acc.type)(name, acc.params, None, self)
            for ref in (wire.from_, wire.to):
                self._check_ref(ref)
            nets.union_wire(uf, i, wire)
        self.nets: dict[str, Net] = {}
        self.port_net: dict[str, str] = {}
        for members in uf.groups():
            net = Net(f"net-{len(self.nets)}", members)
            self.nets[net.id] = net
            for ref in members:
                self.port_net[ref] = net.id

        if self.recorder:
            self.recorder.header()

        # 'sleep' intercepts time.sleep for this process while the bench is open (D47).
        if self.clock.mode == "command" and self.clock.idle_mode == "sleep":
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
            if port not in comp.ports or comp.ports[port].medium not in nets.WIRED_MEDIA:
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

    def component_at(self, ref: str) -> tuple[Component, str] | None:
        """The component and port behind a bench port reference ('dca.OPT1', 'att.IN')."""
        owner, port = split(ref)
        if owner in self.instruments:
            target = self.instruments[owner].ports.get(port)
            if target is None:
                return None
            return self.instruments[owner].components[target[0]], target[1]
        comp = self.components.get(owner)
        return (comp, port) if comp is not None and port in comp.ports else None

    def net_feed(self, comp: Component, port: str, medium: str) -> tuple[Component, str] | None:
        """The one output of ``medium`` on the net at an input of ``comp`` exposed on the bench
        (:func:`.nets.feed`); None without a net or a source on it."""
        ref = self.port_ref(comp, port)
        net = None if ref is None else self.port_net.get(ref)
        if net is None:
            return None
        return nets.feed(self.nets[net].members, self.component_at, comp, medium)

    @property
    def now_ns(self) -> int:
        """Virtual time now, in ns (:attr:`Clock.now_ns`): the bench's present, read everywhere."""
        return self.clock.now_ns

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
        return self.solver.circuit()

    def solve(self) -> tuple[Solution, dict[str, Signal]]:
        """Bring the operating point up to date and return it: the solved circuit and every
        source's EMF signal, at the current instant (:meth:`Solver.solve`). A component judging
        its own state calls this first and reads afterwards."""
        return self.solver.solve()

    @property
    def unsettled(self) -> bool:
        """The last solve ended without the piecewise-linear devices agreeing on a mode (D52)."""
        return self.solver.unsettled

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

    @property
    def context(self) -> EvalContext:
        """The ambient inputs of the evaluation in progress: a frozen value, the tail of both
        caches' keys (:mod:`.evalcontext`)."""
        return self._context

    @property
    def tune_hz(self) -> float | None:
        """The tuning point of the read in progress (:meth:`tuned`); None outside one."""
        return self._context.tune_hz

    @property
    def instant_ns(self) -> int:
        """The instant signals are evaluated for: the one :meth:`at_instant` set, else now."""
        at = self._context.at_ns
        return self.now_ns if at is None else at

    @contextlib.contextmanager
    def evaluating(self, **changes: Any) -> Generator[None]:
        """Evaluate under the current context with ``changes`` (``EvalContext`` field names): the
        one way the context changes, which :meth:`tuned` and :meth:`at_instant` name. Re-entrant:
        leaving restores the outer context.

        The context is an input of the evaluation and nothing more: it is part of the caches' keys,
        it does not enter the run record, bump the signal version or emit an event — a read stays
        passive. Used under the session lock (commands and ``view()`` hold it), so it needs no
        thread-local."""
        outer = self._context
        self._context = outer.model_copy(update=changes)
        try:
            yield
        finally:
            self._context = outer

    def tuned(self, tune_hz: float | None) -> contextlib.AbstractContextManager[None]:
        """Evaluate signals with a receiver tuned to ``tune_hz`` (signal-chain.md 5.2): inside the
        block ``tune_hz`` is that frequency, and the outputs of a mixer and of what follows it
        (``Component.tuned_ports``) are the ones at that tuning. Only the block that starts a read
        sets it (a spectrum analyzer's trace, an internal probe); the front end's blocks read it."""
        return self.evaluating(tune_hz=None if tune_hz is None else float(tune_hz))

    def at_instant(self, t_ns: int | None) -> contextlib.AbstractContextManager[None]:
        """Evaluate signals as they were at instant ``t_ns`` (≤ now) under the current settings
        (#1103): a block whose output moves in time by itself — a laser sweeping its wavelength, an
        attenuator settling — reads :attr:`instant_ns` instead of ``now_ns``. A logger catching up
        on points it missed between two commands uses it."""
        return self.evaluating(at_ns=None if t_ns is None else int(t_ns))

    def cached_output(self, comp: Component, port: str) -> Signal:
        return self.cache.output(self._signal_version, comp, port, self._context)

    def cached_frames(self, comp: Component, port: str, request: FrameRequest) -> SampleFrame:
        """A samples output port's frame for one request (signal-chain.md 3.2); see
        :meth:`EvalCache.frames` for the cache and the fan-out count."""
        return self.cache.frames(self._signal_version, comp, port, request, self._context)

    def reserve_frames(self, comp: Component, port: str, samples: int) -> None:
        """Refuse now a read about to ask ``comp``'s upstream for ``samples`` samples in several
        requests when they would pass ``MAX_REQUEST_FANOUT`` (#986); see :meth:`EvalCache.reserve`."""
        self.cache.reserve(comp, port, samples)

    def checkpoint(self, comp: Component, port: str) -> None:
        """Where a read that has run out of budget, or was cancelled, ends: called before each
        frame is computed (:class:`EvalCache`). Raises :class:`ComputeAborted`, which every seam
        answers as a read beyond the model (-221); inside an action it also leaves an ``abort``
        event in the run record (a view emits nothing, D55), for the replay to end that action the
        same way (D88). Nothing a
        replay times or hears counts, only the abort the record has."""
        reason = self.budget.reason(live=not self.clock.replaying)
        if reason is None:
            return
        detail = f"{comp.uid}.{port}: {self.budget.describe(reason)}"
        if not self._in_view:
            event = {"kind": "abort", "reason": reason, "at": f"{comp.uid}.{port}", "message": detail}
            if self.budget.seconds is not None:
                event["budget_s"] = self.budget.seconds
            self.events.emit(event)
        raise aborted(reason, detail)

    def cancel(self) -> None:
        """End the read in progress at its next checkpoint. Safe from any thread, and without the
        session lock: that is the point — closing a session, or a client that gave up, must not
        wait for a long read to finish (D88)."""
        self.budget.cancel()

    def state_changed(
        self, inst: Instrument, path: str, old: Any, new: Any, *, electrical: bool, source: str
    ) -> None:
        self._signal_version += 1
        if electrical:
            self._electrical_version += 1
        self.events.emit(
            {"kind": "state", "instrument": inst.name, "path": path, "old": old, "new": new, "source": source}
        )
        self.sync()

    @property
    def signal_version(self) -> int:
        """Counts every change of what the signals are; caches and memos are keyed by it."""
        return self._signal_version

    @property
    def electrical_version(self) -> int:
        """Counts the changes that need the circuit rebuilt."""
        return self._electrical_version

    def revise(self, *, electrical: bool) -> None:
        """Count a change of the signals, and of the circuit if ``electrical``, without the sync
        :meth:`invalidate` runs (the solver's mode loop is inside a solve)."""
        self._signal_version += 1
        if electrical:
            self._electrical_version += 1

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

    # ---- time -------------------------------------------------------------------------------------

    def _begin_action(self) -> None:
        self.budget.arm()
        self.clock.begin_action()

    def _end_action(self) -> None:
        self.budget.disarm()
        self.clock.end_action()

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
            outer, self._in_view = self._in_view, True
            self.budget.arm()  # a view computes too: a probe of a deep record has the budget as well
            try:
                self.clock.follow_wall()
                for name in settle:
                    self.instrument(name).settle_pending()
                yield self
            finally:
                self._in_view = outer
                if not outer:
                    self.budget.disarm()

    def solution(self) -> tuple[Solution, dict[str, Signal]]:
        """The solved circuit and every source's EMF signal, at the current instant. A passive
        view's read (call it inside ``view()``) — :meth:`solve` by its passive name (#463)."""
        return self.solve()

    # ---- actions ------------------------------------------------------------------------------------

    def instrument(self, name: str) -> Instrument:
        try:
            return self.instruments[name]
        except KeyError:
            raise Unknown(f"no instrument named {name!r}") from None

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
                    self.clock.delay(f.spec.latency_s or 0.0)
            t0 = self.now_ns
            wall0 = time.time()
            dropped = next((f for f in link if f.spec.kind == "timeout"), None)
            if dropped is not None:
                # The message never reaches the instrument.
                self.faults.use(dropped)
                self.events.emit(
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
            if self.clock.mode == "command" and duration:
                self.clock.step(duration, reason="command", instrument=name, header=_header_of(data))
            self.seq += 1
            self.events.emit(
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
                    self.clock.delay(f.spec.latency_s or 0.0)
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
                self.events.emit(event)
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

    def advance(self, seconds: float, reason: str = "explicit", force: bool = False) -> None:
        """Advance virtual time (action: advance; :meth:`Clock.advance`). The SDK's own name for
        it (``bench_session.advance`` in a pytest test); the engine and the services call the clock."""
        self.clock.advance(seconds, reason, force)

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
                self.events.emit({"kind": "panel", "instrument": name, "action": action, "source": client})
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
                        raise Unknown(f"{owner} is a part: it has no component {component!r}")
                    comp = self.components[owner]
                elif owner in self.instruments and component in self.instruments[owner].components:
                    comp = self.instruments[owner].components[component]
                else:
                    raise Unknown(f"no part {owner!r} or component {owner}/{component!r}")
                set_override(comp, parameter, value)
                self.events.emit(
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
        self.files.close()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
