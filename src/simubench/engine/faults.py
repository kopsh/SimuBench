"""Faults: injected now or preset in the bench, on the link, in an instrument or in a component.

Link faults are applied by :class:`~simubench.engine.session.Session` where every client's SCPI
traffic passes (PyVISA backend, SCPI sockets, UI, MCP), so a script sees the same failure whichever
way it is connected. Instrument and component faults act through the instrument's error queue,
its state, and component parameter overrides (hidden state).

Every change is an event (``kind: "fault"``) in the run record. Timing is in virtual time.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from ..models.bench import Fault

if TYPE_CHECKING:
    from .component import Component
    from .session import Session

LINK = {"timeout", "disconnect", "garble", "latency", "busy"}
ONCE = {"error", "trip"}
PARAM = {"drift", "hidden"}

Status = Literal["pending", "active", "ended"]


class LinkDown(ConnectionError):
    """The link to an instrument is cut (``disconnect`` fault)."""


@dataclass
class FaultState:
    id: str
    spec: Fault
    status: Status = "pending"
    started_ns: int | None = None
    ends_ns: int | None = None
    remaining: int | None = None
    source: str = "api"
    garbled: int = 0
    injected_ns: int = 0
    # What a parameter fault replaced, so that ending it puts it back.
    _component: Component | None = field(default=None, repr=False)
    _had_override: bool = False
    _previous: Any = None
    _base: float = 0.0

    def describe(self) -> str:
        s = self.spec
        target = s.component or s.instrument or ""
        extra = {
            "error": f" {s.code if s.code is not None else -300}",
            "latency": f" {s.latency_s or 0:g} s",
            "drift": f" {s.param} {s.drift_per_s or 0:+g}/s",
            "hidden": f" {s.param} = {s.value!r}",
        }.get(s.kind, "")
        return f"{s.kind} {target}{extra}".strip()


class FaultManager:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.faults: dict[str, FaultState] = {}
        self._ids = itertools.count(1)

    # ---- lifecycle ----------------------------------------------------------------------------------

    def inject(self, spec: Fault, source: str = "api") -> FaultState:
        """Validate and schedule a fault; it starts at once unless ``at_s`` is in the future."""
        self._check(spec)
        state = FaultState(
            f"f{next(self._ids)}", spec, source=source, remaining=spec.count, injected_ns=self.session.now_ns
        )
        self.faults[state.id] = state
        self._emit(state, "injected")
        self.tick()
        return state

    def clear(self, fault_id: str) -> FaultState:
        state = self.faults.get(fault_id)
        if state is None:
            raise KeyError(f"no fault {fault_id!r}")
        if state.status != "ended":
            self._end(state, "cleared")
        return state

    def tick(self) -> None:
        """Start, end and advance faults at the current virtual time."""
        now = self.session.now_ns
        for state in list(self.faults.values()):
            if state.status == "pending":
                # Never before the injection: a fault injected now starts now, not at time zero.
                start = max(round((state.spec.at_s or 0.0) * 1e9), state.injected_ns)
                if now >= start:
                    self._start(state, start)
            if state.status == "active":
                if state.ends_ns is not None and now >= state.ends_ns:
                    self._end(state, "expired")
                elif state.spec.kind == "drift":
                    self._apply_drift(state)

    def _start(self, state: FaultState, start_ns: int) -> None:
        s = state.spec
        state.status = "active"
        state.started_ns = max(start_ns, 0)
        if s.duration_s is not None:
            state.ends_ns = state.started_ns + round(s.duration_s * 1e9)
        self._emit(state, "active")
        if s.kind in ONCE:
            self._once(state)
            self._end(state, "done")
        elif s.kind in PARAM:
            comp = self._component(s.component or "")
            state._component = comp
            state._had_override = s.param in comp.overrides
            state._previous = comp.overrides.get(s.param or "")
            if s.kind == "hidden":
                comp.overrides[s.param or ""] = s.value
            else:
                state._base = float(comp.param(s.param or ""))
                self._apply_drift(state)
            self.session.invalidate(electrical=True)

    def _end(self, state: FaultState, why: str) -> None:
        if state.status == "active" and state.spec.kind in PARAM and state._component is not None:
            comp, key = state._component, state.spec.param or ""
            if state._had_override:
                comp.overrides[key] = state._previous
            else:
                comp.overrides.pop(key, None)
            self.session.invalidate(electrical=True)
        state.status = "ended"
        self._emit(state, why)

    def _once(self, state: FaultState) -> None:
        s = state.spec
        inst = self.session.instrument(s.instrument or "")
        code = s.code if s.code is not None else -300
        if s.kind == "trip":
            for path, spec in inst.specs.items():
                if spec.type == "bool" and path.rsplit(".", 1)[-1] == "output" and inst.state.get(path):
                    inst.set_state(path, False, source="fault")
            inst.push_error(code, s.message or "protection tripped")
        else:
            inst.push_error(code, s.message or "")

    def _apply_drift(self, state: FaultState) -> None:
        comp = state._component
        if comp is None or state.started_ns is None:
            return
        elapsed = (self.session.now_ns - state.started_ns) / 1e9
        comp.overrides[state.spec.param or ""] = state._base * (1 + (state.spec.drift_per_s or 0.0) * elapsed)
        self.session.invalidate(electrical=True)

    # ---- link ---------------------------------------------------------------------------------------

    def link(self, instrument: str) -> list[FaultState]:
        return [
            f
            for f in self.faults.values()
            if f.status == "active" and f.spec.kind in LINK and f.spec.instrument == instrument
        ]

    def use(self, state: FaultState) -> None:
        """Count one affected message; a fault with ``count`` ends when it is used up."""
        if state.remaining is None:
            return
        state.remaining -= 1
        if state.remaining <= 0:
            self._end(state, "used")

    def garble(self, state: FaultState, data: bytes) -> bytes:
        """Corrupt printable bytes of a response, keeping its terminator (reproducible per seed)."""
        rate = state.spec.garble_rate or 0.2
        body, term = (data[:-1], data[-1:]) if data.endswith(b"\n") else (data, b"")
        if not body:
            return data
        state.garbled += 1
        rng = np.random.default_rng([self.session.seed, int(state.id[1:]), state.garbled])
        out = bytearray(body)
        n = max(1, math.ceil(len(out) * rate))
        for i in rng.choice(len(out), size=min(n, len(out)), replace=False):
            out[int(i)] = int(rng.integers(0x21, 0x7F))
        return bytes(out) + term

    # ---- helpers ------------------------------------------------------------------------------------

    def _component(self, ref: str) -> Component:
        s = self.session
        if "/" in ref:
            inst, _, name = ref.partition("/")
            comps = s.instrument(inst).components
            if name not in comps:
                raise KeyError(f"{inst} has no component {name!r}; components: {', '.join(comps)}")
            return comps[name]
        if ref not in s.components or ref.startswith("wire"):
            raise KeyError(f"no DUT named {ref!r}")
        return s.components[ref]

    def _check(self, spec: Fault) -> None:
        if spec.kind in LINK or spec.kind in ONCE:
            if not spec.instrument:
                raise ValueError(f"{spec.kind}: instrument is required")
            self.session.instrument(spec.instrument)  # KeyError when unknown
        if spec.kind in PARAM:
            if not spec.component or not spec.param:
                raise ValueError(f"{spec.kind}: component and param are required")
            comp = self._component(spec.component)
            known = set(comp.defaults) | set(comp._params)
            if spec.param not in known:
                raise ValueError(
                    f"{spec.component} has no parameter {spec.param!r}; parameters: {', '.join(sorted(known))}"
                )
            if spec.kind == "drift":
                if spec.drift_per_s is None:
                    raise ValueError("drift: drift_per_s is required")
                try:
                    float(comp.param(spec.param))
                except (TypeError, ValueError):
                    raise ValueError(f"drift: {spec.component}.{spec.param} is not numeric") from None
            if spec.kind == "hidden" and spec.value is None:
                raise ValueError("hidden: value is required")
        if spec.kind == "latency" and not spec.latency_s:
            raise ValueError("latency: latency_s is required")

    def _emit(self, state: FaultState, status: str) -> None:
        self.session.emit(
            {
                "kind": "fault",
                "fault": state.id,
                "reason": status,
                "instrument": state.spec.instrument,
                "message": state.describe(),
                "source": state.source,
                # What was injected, so that a run record can be replayed (D36).
                **({"spec": state.spec.model_dump(mode="json")} if status == "injected" else {}),
            }
        )

    def snapshot(self) -> list[FaultState]:
        return list(self.faults.values())


__all__ = ["FaultManager", "FaultState", "LinkDown"]
