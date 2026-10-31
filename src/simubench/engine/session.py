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
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.bench import BenchDefinition
from ..scpi.errors import ScpiError
from . import sleep_mode
from .circuit import Circuit, Solution
from .component import Component, component_type
from .deviation import draw as draw_deviation
from .faults import FaultManager, LinkDown
from .instrument import Instrument
from .record import Recorder
from .signal import Signal

Listener = Callable[[dict[str, Any]], None]
MAX_MODE_ITERATIONS = 40
log = logging.getLogger(__name__)

#: In command-driven mode, idle gaps shorter than this are script overhead and do not advance time.
IDLE_THRESHOLD_NS = 10_000_000
#: Idle gaps are rounded to this resolution so that sleeps produce stable virtual times.
IDLE_QUANTUM_NS = 1_000_000


@dataclass
class Net:
    id: str
    members: list[str] = field(default_factory=list)  # port references, e.g. "awg.OUT1"


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
    ) -> None:
        self.bench = bench
        self.base_dir = base_dir
        self.seed = bench.run.seed if seed is None else seed
        self.clock_mode = clock or bench.run.clock
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
        self._electrical_version = 0
        self._circuit: tuple[int, Circuit] | None = None
        self._solution: tuple[int, Solution, dict[str, Signal]] | None = None
        self._output_cache: dict[tuple[int, str, str], Signal] = {}
        self._wall_start = time.monotonic_ns()
        self._last_wall = self._wall_start
        self.seq = 0
        self.event_id = 0  # id of the last emitted event; ids are stable cursors for event readers
        self.recorder = Recorder(record_dir, self, raw=record_raw) if record_dir else None
        #: Replaying a run record: time moves only where the record says (no idle steps).
        self.replaying = False
        #: Counters for metrics (M4): events by kind, circuit solves and the time they took.
        self.stats: dict[str, float] = {}

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
            comp.overrides.update(part.hidden)
            for param, rng in part.deviation.items():
                if param not in comp.overrides:
                    value = draw_deviation(rng, comp.param(param), self.seed, name, param)
                    comp.overrides[param] = value
                    self.part_deviations[f"{name}.{param}"] = value
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
        self._dynamic = [c for c in self._all_components() if c.dynamic]
        self._syncing = False
        self._sync()

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
        owner, _, port = ref.rpartition(".")
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

    def _all_components(self) -> Iterable[Component]:
        for inst in self.instruments.values():
            yield from inst.components.values()
        yield from self.components.values()

    def circuit(self) -> Circuit:
        if self._circuit is None or self._circuit[0] != self._electrical_version:
            c = Circuit()
            for comp in self._all_components():
                comp.stamp(c, lambda p, comp=comp: self.node_of(comp, p))  # type: ignore[misc]
            self._circuit = (self._electrical_version, c)
        return self._circuit[1]

    def _solve(self) -> tuple[Solution, dict[str, Signal]]:
        if self._solution is None or self._solution[0] != self._signal_version:
            piecewise = [c for c in self._all_components() if c.piecewise]
            for _ in range(MAX_MODE_ITERATIONS):
                emf: dict[str, Signal] = {}
                for comp in self._all_components():
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

                    changed |= comp.update_mode(dc)
                if not changed:
                    break
                self._signal_version += 1
                self._electrical_version += 1
            else:
                log.warning("piecewise-linear devices did not settle on a mode")
        return self._solution[1], self._solution[2]

    def node_signal(self, node: str) -> Signal:
        sol, emf = self._solve()
        if node not in sol.node_transfer:
            return Signal.zero()
        return sol.node_signal(node, emf)

    def source_current(self, sid: str) -> float:
        """DC current an ideal source drives out of its node (piecewise devices read their own)."""
        sol, emf = self._solve()
        return sol.source_dc_current(sid, emf)

    def net_signal(self, net_id: str) -> Signal:
        return self.node_signal(net_id)

    def port_signal(self, comp: Component, port: str) -> Signal:
        return self.node_signal(self.node_of(comp, port))

    def cached_output(self, comp: Component, port: str) -> Signal:
        key = (self._signal_version, comp.uid, port)
        hit = self._output_cache.get(key)
        if hit is None:
            if len(self._output_cache) > 4096:
                self._output_cache.clear()
            hit = comp.output(port)
            self._output_cache[key] = hit
        return hit

    def state_changed(
        self, inst: Instrument, path: str, old: Any, new: Any, *, electrical: bool, source: str
    ) -> None:
        self._signal_version += 1
        if electrical:
            self._electrical_version += 1
        self.emit(
            {"kind": "state", "instrument": inst.name, "path": path, "old": old, "new": new, "source": source}
        )
        self._sync()

    def invalidate(self, electrical: bool = False) -> None:
        """Components call this when their own runtime state changes the signals they produce."""
        self._signal_version += 1
        if electrical:
            self._electrical_version += 1
        self._sync()

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
        """Wall clock only: bring virtual time up to real time (passive views read the present)."""
        if self.clock_mode == "wall":
            self._sync()
            self.now_ns = max(self.now_ns, time.monotonic_ns() - self._wall_start)

    def _begin_action(self) -> None:
        wall = time.monotonic_ns()
        if self.clock_mode == "wall":
            self._sync()
            self.now_ns = max(self.now_ns, wall - self._wall_start)
        elif self.clock_mode == "command" and not self.replaying and self.idle_mode == "wall":
            # Counting idle real time makes results depend on machine speed; only 'wall' does it
            # (D47). 'sleep' and 'off' move time only by command, advance and time.sleep.
            idle = wall - self._last_wall
            if idle >= IDLE_THRESHOLD_NS:
                step = (idle // IDLE_QUANTUM_NS) * IDLE_QUANTUM_NS
                self._advance(step, reason="idle")

    def _end_action(self) -> None:
        self._last_wall = time.monotonic_ns()

    def _sync(self) -> None:
        """Let dynamic components record changes at the current instant (exactly, before time moves)."""
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
        self._sync()
        self.now_ns += int(dt_ns)
        self._signal_version += 1  # time-dependent components (filters, sweeps) may change
        self.emit({"kind": "advance", "dt_ns": int(dt_ns), "reason": reason})
        self.faults.tick()

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
        elif self.clock_mode == "wall":
            time.sleep(min(seconds, 5.0))

    def query(self, name: str, data: bytes | str, client: str = "sdk") -> bytes:
        if isinstance(data, str):
            data = data.encode()
        self.write(name, data, client)
        out = self.read(name, client)
        return out if out is not None else b""

    def scpi(self, name: str, text: str) -> str:
        """Convenience for tests and notebooks: write text, return the decoded reply (or '')."""
        out = self.query(name, text)
        return out.decode(errors="replace").rstrip("\n")

    def calendar(self) -> dt.datetime:
        """Calendar time now: the bench's run.start plus virtual time (instrument clocks, due dates)."""
        return self.bench.run.start + dt.timedelta(microseconds=self.now_ns // 1000)

    def seek(self, t_ns: int, reason: str = "replay") -> None:
        """Move virtual time forward to ``t_ns`` (replaying a record); never backwards."""
        with self._lock:
            if t_ns > self.now_ns:
                self._advance(t_ns - self.now_ns, reason=reason)

    def advance(self, seconds: float, reason: str = "explicit") -> None:
        """Advance virtual time (action: advance). Works in every clock mode except wall."""
        with self._lock:
            if self.clock_mode == "wall":
                raise RuntimeError("cannot advance a wall clock")
            self._advance(round(seconds * 1e9), reason=reason)

    def configure(self, name: str, path: str, value: Any, client: str = "ui") -> None:
        """Out-of-band state change from the UI, MCP or SDK (action: configure)."""
        with self._lock:
            self._begin_action()
            try:
                self.instrument(name).set_state(path, value, source="configure")
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
