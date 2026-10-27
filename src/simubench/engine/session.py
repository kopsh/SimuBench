"""Session: a running bench (architecture.md section 5).

The session owns the instruments, bench-level components (DUTs, accessories), the nets between
ports, the bench circuit and its solution, the virtual clock and the run record. Every change goes
through one of its action methods, which run one at a time under a lock (D9).
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.bench import BenchDefinition
from ..scpi.errors import ScpiError
from .circuit import Circuit, Solution
from .component import Component, component_type
from .instrument import Instrument
from .record import Recorder
from .signal import Signal

Listener = Callable[[dict[str, Any]], None]

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
    ) -> None:
        self.bench = bench
        self.base_dir = base_dir
        self.seed = bench.run.seed if seed is None else seed
        self.clock_mode = clock or bench.run.clock
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
        self.recorder = Recorder(record_dir, self) if record_dir else None

        # ---- instruments and bench components ----
        self.instruments: dict[str, Instrument] = {}
        for name, inst in bench.instruments.items():
            self.instruments[name] = Instrument(name, definitions(inst.model), inst, self)
        self.components: dict[str, Component] = {}
        for name, dut in bench.duts.items():
            if name in self.instruments:
                raise ValueError(f"name {name!r} used by both an instrument and a DUT")
            comp = component_type(dut.type)(name, dut.params, None, self)
            comp.overrides.update(dut.hidden)
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

    # ---- references ---------------------------------------------------------------------------------

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
            emf: dict[str, Signal] = {}
            for comp in self._all_components():
                emf.update(comp.emf())
            freqs: set[float] = {0.0}
            for sig in emf.values():
                freqs.update(float(f) for f in set(sig.freq.tolist()))
            sol = self.circuit().solve(sorted(freqs))
            self._solution = (self._signal_version, sol, emf)
        return self._solution[1], self._solution[2]

    def node_signal(self, node: str) -> Signal:
        sol, emf = self._solve()
        if node not in sol.node_transfer:
            return Signal.zero()
        return sol.node_signal(node, emf)

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

    def invalidate(self, electrical: bool = False) -> None:
        """Components call this when their own runtime state changes the signals they produce."""
        self._signal_version += 1
        if electrical:
            self._electrical_version += 1

    # ---- events -------------------------------------------------------------------------------------

    def subscribe(self, fn: Listener) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def emit(self, event: dict[str, Any]) -> None:
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

    def _begin_action(self) -> None:
        wall = time.monotonic_ns()
        if self.clock_mode == "wall":
            self.now_ns = max(self.now_ns, wall - self._wall_start)
        elif self.clock_mode == "command":
            idle = wall - self._last_wall
            if idle >= IDLE_THRESHOLD_NS:
                step = (idle // IDLE_QUANTUM_NS) * IDLE_QUANTUM_NS
                self._advance(step, reason="idle")

    def _end_action(self) -> None:
        self._last_wall = time.monotonic_ns()

    def _advance(self, dt_ns: int, reason: str) -> None:
        if dt_ns <= 0:
            return
        self.now_ns += int(dt_ns)
        self._signal_version += 1  # time-dependent components (filters, sweeps) may change
        self.emit({"kind": "advance", "dt_ns": int(dt_ns), "reason": reason})

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
            t0 = self.now_ns
            wall0 = time.time()
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
            out = inst.take_output()
            if out is not None:
                self.emit({"kind": "scpi", "dir": "read", "instrument": name, "data": out, "client": client})
            self._end_action()
            return out

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

    def advance(self, seconds: float) -> None:
        """Advance virtual time (action: advance). Works in every clock mode except wall."""
        with self._lock:
            if self.clock_mode == "wall":
                raise RuntimeError("cannot advance a wall clock")
            self._advance(round(seconds * 1e9), reason="explicit")

    def configure(self, name: str, path: str, value: Any, client: str = "ui") -> None:
        """Out-of-band state change from the UI, MCP or SDK (action: configure)."""
        with self._lock:
            self._begin_action()
            try:
                self.instrument(name).set_state(path, value, source="configure")
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
        if self.recorder:
            self.recorder.close()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
