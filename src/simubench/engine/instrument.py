"""Instrument runtime: state, components, signal wiring and the SCPI command layer (architecture.md 3)."""

from __future__ import annotations

import datetime as dt
import difflib
import itertools
import logging
import math
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..library.loader import COVERAGE_SUFFIX, _load_coverage_file
from ..models.definition import CommandSpec, InstrumentDefinition, ParamSpec, ResponseFormat, StateSpec
from ..scpi import values as V
from ..scpi.errors import ESR_BITS, ErrorQueue, ScpiError
from ..scpi.parse import Pattern, Token, Unit, parse_unit, resolve_tokens, split_units
from ..units import parse_number, parse_quantity
from .commands import (
    COMMON_BUILTINS,
    COMMON_DIRECTIONS,
    SYST_BUILTINS,
    _header_key,
    builtin_allowed,
    named_builtin,
    when_matches,
)
from .component import Component, OpResult, component_type, map_setting
from .signal import Signal

if TYPE_CHECKING:
    from ..models.bench import InstrumentInstance
    from .session import Session

_LADDERS = {"1-2-5": (1.0, 2.0, 5.0), "1-3": (1.0, 3.0), "1-2-4": (1.0, 2.0, 4.0), "1-10": (1.0,)}

log = logging.getLogger(__name__)
_HANDLED = b"\x00handled"  # a built-in command that sends no reply
#: Appended to -113 when a real model gets a command its definition does not have (D42).
NOT_MODELED = "not implemented in this definition"


def _is_channel_list(raw: bytes) -> bool:
    """A program data element that is a channel list ``(@…)``."""
    return raw.lstrip().startswith(b"(@")


def _enum_hit(raw: bytes, options: list[str] | None) -> bool:
    """Whether a data element spells one of an enum parameter's options (spaces and case aside)."""
    text = b"".join(raw.split()).decode("ascii", "replace").upper()
    return any("".join(o.split()).upper() == text for o in options or [])


#: Parameter errors a definition's declaration explains (tui-design.md 1.1): out of range (-222),
#: wrong type (-104), a value outside the declared options (-224). -224 with the "not modeled by
#: SimuBench" marker is a declared modeling gap instead, not a bad parameter.
BAD_PARAMETER_CODES = frozenset({-104, -222, -224})


def _header_forms(pattern: Pattern) -> list[str]:
    """Every spelling the header pattern accepts, long and short, for closest-match comparison."""
    choices: list[list[str | None]] = []
    for node in pattern.nodes:
        words = [node.long] if node.short == node.long else [node.long, node.short]
        if node.suffix:
            words = [w + "1" for w in words]
        choices.append([None, *words] if node.optional else words)
    out = []
    for combo in itertools.islice(itertools.product(*choices), 512):
        parts = [p for p in combo if p is not None]
        if parts:
            out.append(":".join(parts))
    return out


def _declared_range(spec: StateSpec | ParamSpec | None) -> str | None:
    """The range or options a definition declares, compactly: '-210 … 210 V' or 'SINusoid, SQUARE'."""
    if spec is None:
        return None
    options = getattr(spec, "options", None)
    if options:
        return ", ".join(options)
    lo, hi = getattr(spec, "min", None), getattr(spec, "max", None)
    if lo is None and hi is None:
        return None
    unit = getattr(spec, "unit", None) or ""
    low = "-∞" if lo is None else f"{lo:g}"
    high = "∞" if hi is None else f"{hi:g}"
    return f"{low} … {high}{f' {unit}' if unit else ''}"


@dataclass
class _Covered:
    """One entry of the definition's coverage table, ready for runtime lookups (D18)."""

    pattern: Pattern
    header: str
    level: str


def snap_ladder(value: float, ladder: str | list[float], lo: float | None, hi: float | None) -> float:
    """Snap ``value`` to the nearest rung on a log scale."""
    if value <= 0:
        return lo if lo is not None else value
    if isinstance(ladder, list):
        rungs = sorted(ladder)
    else:
        mant = _LADDERS[ladder]
        k0 = math.floor(math.log10(value))
        rungs = [m * 10.0**k for k in range(k0 - 1, k0 + 2) for m in mant]
    if lo is not None:
        rungs = [r for r in rungs if r >= lo * (1 - 1e-12)]
    if hi is not None:
        rungs = [r for r in rungs if r <= hi * (1 + 1e-12)]
    if not rungs:
        return value
    return min(rungs, key=lambda r: abs(math.log10(r) - math.log10(value)))


def parse_duration(d: str | float | None) -> int:
    if d is None:
        return 0
    if isinstance(d, (int, float)):
        return round(float(d) * 1e9)
    return round(parse_number(d, "S") * 1e9)


class _Ignored(Exception):
    """An unknown header on an instrument whose definition ignores unknown commands."""


@dataclass
class _Compiled:
    pattern: Pattern
    spec: CommandSpec


class Instrument:
    """A running instrument inside a session."""

    def __init__(
        self,
        name: str,
        definition: InstrumentDefinition,
        instance: InstrumentInstance,
        session: Session,
    ) -> None:
        self.name = name
        self.definition = definition
        self.instance = instance
        self.session = session
        self.channel_count = definition.channels.count
        #: The numbers SCPI uses for the channels (#207, D51): number -> channel index, and back.
        #: Empty when the definition declares no numbering: the numbers are the indexes 1..count.
        self._channels_by_number: dict[int, int] = {}
        self._numbers_by_channel: dict[int, int] = {}
        offset = 0
        for block in definition.channels.numbering:
            for i in range(block.count):
                self._channels_by_number[block.first + i] = offset + i + 1
                self._numbers_by_channel[offset + i + 1] = block.first + i
            offset += block.count
        self.errors = ErrorQueue(definition.error_queue or 20, definition.errors.get(-350))
        #: A real model (D42): follows its manual's error texts and refuses what it does not model.
        self.real = definition.reference is not None
        self.esr = 0
        self.ese = 0
        #: Operations that finish at a later virtual instant: key -> (done at, called when done).
        #: *OPC? and *WAI wait for them; *OPC sets ESR bit 0 once they are all done.
        self.pending: dict[str, tuple[int, Callable[[], None] | None]] = {}
        #: Pending operations *OPC, *OPC? and *WAI do not wait for (the model says so, e.g. a scope
        #: whose *OPC? answers at once while an acquisition runs).
        self.unwaited: set[str] = set()
        #: Operations waiting for an event that may never come (a scope armed for a trigger that does
        #: not occur): while any is waiting, *OPC? does not answer, as on hardware the reply never comes.
        self.blockers: set[str] = set()
        self._status_register_cache: list[Component] | None = None  # see _status_registers()
        #: A front-panel operation is executing (D32): an acquisition it starts finishes at once.
        self.on_panel = False
        self._opc_armed = False
        self._elapsed_ns = 0  # virtual time the program message being executed has used so far
        self.sre = 0
        self._output: list[bytes] = []
        self.serial = instance.serial or definition.identity.serial
        self.firmware = instance.firmware or definition.identity.firmware
        #: The instrument's own clock runs on the session calendar plus whatever :SYSTem:DATE/TIME set.
        self.clock_offset = dt.timedelta(0)

        # ---- state ----
        self.specs: dict[str, StateSpec] = {}
        for key, spec in definition.state.items():
            self.specs[key] = spec
        for n in range(1, self.channel_count + 1):
            over = definition.channels.overrides.get(n, {})
            for key, spec in definition.channel_state.items():
                patch = over.get(key)
                self.specs[f"ch{n}.{key}"] = spec.model_copy(update=patch) if patch else spec
        #: The definition's spec of a setting whose bounds a component moved at run time.
        self._spec_origin: dict[str, StateSpec] = {}
        self.state: dict[str, Any] = {}
        self._reset_state()
        for path, value in instance.state.items():
            if path not in self.specs:
                raise ValueError(f"instrument {name!r}: unknown state {path!r} in bench file")
            self.state[path] = self._coerce_initial(path, value)

        # ---- components ----
        self.components: dict[str, Component] = {}
        for spec in definition.components:
            self._add_component(spec.name, spec.type, spec.params, None)
        for n in range(1, self.channel_count + 1):
            for spec in definition.channel_components:
                self._add_component(f"ch{n}.{spec.name}", spec.type, spec.params, n)
        #: Components that look again for the event they wait for whenever a command arrives.
        self._retrying = [
            c for c in self.components.values() if type(c).retry_pending is not Component.retry_pending
        ]
        for comp_name, params in instance.hidden.items():
            if comp_name not in self.components:
                options = ", ".join(sorted(self.components))
                raise ValueError(
                    f"instrument {name!r}: hidden state for unknown component {comp_name!r} "
                    f"(components: {options})"
                )
            component = self.components[comp_name]
            for param_name in params:
                if param_name not in component._params:
                    options = ", ".join(sorted(component._params))
                    raise ValueError(
                        f"instrument {name!r}: hidden state for unknown parameter "
                        f"{comp_name}.{param_name} (parameters: {options})"
                    )
            component.overrides.update(params)
        #: This unit's drawn deviations, '<component>.<parameter>' -> value (empty when nominal).
        self.deviations: dict[str, Any] = {}
        if instance.deviation == "from_specs":
            self._draw_deviations()

        # state path -> components bound to it
        self._watchers: dict[str, list[Component]] = defaultdict(list)
        for comp in self.components.values():
            for path in comp.bound_paths().values():
                # once per component, however many of its parameters bind the path (#436: a load
                # setting bound both as the level's load and as its range table's key)
                if comp not in self._watchers[path]:
                    self._watchers[path].append(comp)
            for path in comp.watched_paths():
                # the settings inside a nested mapping too (the 8588A's input impedance under the
                # function, #340)
                if path in self.specs and comp not in self._watchers[path]:
                    self._watchers[path].append(comp)
            for path in comp.watches:  # read by path, still told of changes (#274)
                if path in self.specs and comp not in self._watchers[path]:
                    self._watchers[path].append(comp)

        # ---- signal wiring: (dst component, dst port) -> (src component, src port) ----
        self._upstream: dict[tuple[str, str], tuple[str, str]] = {}
        for n in range(1, self.channel_count + 1):
            for src, dst in definition.wiring.channel:
                self._wire(f"ch{n}.{src}", f"ch{n}.{dst}")
        for src, dst in definition.wiring.instrument:
            if "{n}" in src or "{n}" in dst:
                for n in range(1, self.channel_count + 1):
                    self._wire(src.replace("{n}", str(n)), dst.replace("{n}", str(n)))
            else:
                self._wire(src, dst)

        # ---- external ports: name -> (component, port) ----
        self.ports: dict[str, tuple[str, str]] = {}
        for n in range(1, self.channel_count + 1):
            for pname, target in definition.ports.channel.items():
                comp, port = target.rsplit(".", 1)
                self.ports[pname.replace("{n}", str(n))] = (f"ch{n}.{comp}", port)
        for pname, target in definition.ports.instrument.items():
            comp, port = target.rsplit(".", 1)
            self.ports[pname] = (comp, port)

        self._commands = [_Compiled(Pattern.compile(c.header), c) for c in definition.commands]
        #: Built-in headers this model answers; a real model's manual may document only a few (D42).
        allowed = definition.builtins
        self._builtins = None if allowed is None else {_header_key(h) for h in allowed}
        # ---- why a command was not executed: the declarations to cite (tui-design.md 1.1, D18) ----
        #: The definition file, as the loader received the path; origin of the definition's own words.
        self.source_file: str | None = str(definition._source_file) if definition._source_file else None
        #: The coverage table's entries and its file; None when the definition has no table (a broken
        #: or missing table falls back to the definition alone). Loaded on first use: reading it here
        #: would put its cost on the session's idle wall clock (D47), shifting every later instant.
        self._coverage: list[_Covered] | None = None
        self._coverage_file: str | None = None
        self._coverage_tried = False
        #: Every header spelling the instrument answers, built once for the -113's suggestion.
        self._header_keys: dict[str, str] | None = None
        self._header_key_list: list[str] | None = None

    # ---- construction helpers -------------------------------------------------------------------

    def _draw_deviations(self) -> None:
        """Individual deviations within the definition's ranges, from the bench seed; a value the
        bench writes in ``hidden`` stays (metrology-authoring.md 7)."""
        from .deviation import deviation_keys, draw_overrides

        for spec in self.definition.deviations:
            for key in deviation_keys(self, spec.component, spec.parameter):
                cname = key.removesuffix(f".{spec.parameter}")
                comp = self.components.get(cname)
                if comp is None:
                    raise ValueError(f"instrument {self.name!r}: deviation of unknown component {cname!r}")
                if spec.parameter not in comp._params:
                    raise ValueError(
                        f"instrument {self.name!r}: deviation of unknown parameter {cname}.{spec.parameter!r}"
                    )
                # draw_overrides appends (comp.name, parameter) itself: the stream stays
                # (instrument, component, parameter), exactly as the draws have always been keyed.
                drawn = draw_overrides(comp, [(spec.parameter, spec)], self.session.seed, self.name)
                for param, value in drawn.items():
                    self.deviations[f"{cname}.{param}"] = value

    def _add_component(self, name: str, type_name: str, params: dict[str, Any], channel: int | None) -> None:
        cls = component_type(type_name)
        self.components[name] = cls(name, params, self, self.session, channel)

    def internal_wires(self) -> list[tuple[str, str]]:
        """Signal wiring inside the instrument as ``('<comp>.<port>', '<comp>.<port>')`` (from, to)."""
        return [(f"{sc}.{sp}", f"{dc}.{dp}") for (dc, dp), (sc, sp) in self._upstream.items()]

    def _wire(self, src: str, dst: str) -> None:
        sc, sp = src.rsplit(".", 1)
        dc, dp = dst.rsplit(".", 1)
        for c in (sc, dc):
            if c not in self.components:
                raise ValueError(f"instrument {self.name!r}: wiring refers to unknown component {c!r}")
        self._upstream[(dc, dp)] = (sc, sp)

    def _coerce_initial(self, path: str, value: Any) -> Any:
        spec = self.specs[path]
        if spec.type == "enum":  # "" is an enum not set yet (an optional value a query leaves out)
            return "" if value == "" else V.parse_enum(str(value).encode(), spec.options or [])
        if spec.type == "float":
            return float(value)
        if spec.type == "int":
            return int(value)
        if spec.type == "bool":
            return bool(value)
        return value

    def _reset_state(self) -> None:
        for path, spec in self.specs.items():
            value = spec.rst if spec.rst is not None else spec.default
            if value is None:
                value = {"float": 0.0, "int": 0, "bool": False, "string": ""}.get(spec.type)
                if spec.type == "enum":
                    value = (spec.options or [""])[0]
            self.state[path] = self._coerce_initial(path, value) if value is not None else value

    # ---- signal flow ------------------------------------------------------------------------------

    def has_upstream(self, comp: Component, port: str) -> bool:
        """Whether a signal wire inside the instrument feeds this component's port."""
        return (comp.name, port) in self._upstream

    def upstream_signal(self, comp: Component, port: str) -> Signal:
        src = self._upstream.get((comp.name, port))
        if src is None:
            return Signal.zero()
        return self.session.cached_output(self.components[src[0]], src[1])

    # ---- state --------------------------------------------------------------------------------------

    def set_bounds(self, path: str, *, minimum: float | None = None, maximum: float | None = None) -> None:
        """Move a numeric setting's limits at run time, relative to the definition's: a combination
        that widens a channel's range (#259: the E36312A's series pair programs output 2 to 50 V).
        Validation, MIN/MAX and the state view follow; ``None`` keeps the definition's limit."""
        origin = self._spec_origin.setdefault(path, self.specs[path])
        update = {
            "min": origin.min if minimum is None else minimum,
            "max": origin.max if maximum is None else maximum,
        }
        self.specs[path] = origin.model_copy(update=update)

    def restore_bounds(self, path: str) -> None:
        """The definition's limits again (see :meth:`set_bounds`)."""
        origin = self._spec_origin.pop(path, None)
        if origin is not None:
            self.specs[path] = origin

    def get_state(self, path: str) -> Any:
        try:
            return self.state[path]
        except KeyError:
            raise KeyError(f"instrument {self.name!r} has no state {path!r}") from None

    def set_state(self, path: str, value: Any, *, source: str = "scpi", internal: bool = False) -> None:
        """Set a state variable after validation; notify bound components; refuse on conflict.

        ``internal``: the instrument itself sets a read-only variable (e.g. calibration coefficients).
        A watcher may write other settings as a side effect (a unit switch converts the value); when
        any watcher refuses, everything the watchers wrote is rolled back with the refused value.
        """
        spec = self.specs.get(path)
        if spec is None:
            raise ScpiError(-113, f"no state {path!r}")
        value = self._validate(spec.model_copy(update={"readonly": False}) if internal else spec, value)
        old = self.state.get(path)
        if old == value:
            return
        before = dict(self.state)
        self.state[path] = value
        try:
            for comp in self._watchers.get(path, ()):
                # A component may take a value outside its legal range as the nearest legal one
                # (it returns that value) instead of refusing it.
                adjusted = comp.on_state(path, old, value)
                if adjusted is not None and adjusted != value:
                    value = self._validate(spec.model_copy(update={"readonly": False}), adjusted)
                    self.state[path] = value
        except ScpiError:
            self.state.update(before)
            raise
        if old == value:
            return
        self.session.state_changed(self, path, old, value, electrical=spec.electrical, source=source)

    def _validate(self, spec: StateSpec, value: Any) -> Any:
        if spec.readonly:
            raise ScpiError(-221, "read-only setting")
        if spec.type in ("float", "int"):
            v = float(value)
            special_values = set((spec.special or {}).values())
            if v in special_values:
                return v
            if spec.ladder is not None:
                v = snap_ladder(v, spec.ladder, spec.min, spec.max)
            # The relative tolerance widens the range on both signs (#407: a minimum of -5 V must
            # accept -5; min * (1 - 1e-12) would move a negative bound inwards).
            if math.isnan(v) or (spec.min is not None and v < spec.min - abs(spec.min) * 1e-12 - 1e-300):
                raise ScpiError(-222)
            if spec.max is not None and v > spec.max + abs(spec.max) * 1e-12 + 1e-300:
                raise ScpiError(-222)
            return round(v) if spec.type == "int" else v
        if spec.type == "enum":
            if value not in (spec.options or []):
                raise ScpiError(-224)
            return value
        if spec.type == "bool":
            return bool(value)
        if spec.type == "enum_list":
            # A set of options kept in the options' order, whatever order it was written in
            # (#252: :FORM:ELEM:SENS answers voltage, current, … however the list was given).
            chosen = set(value)
            if not chosen or not chosen <= set(spec.options or []):
                raise ScpiError(-224)
            return [o for o in spec.options or [] if o in chosen]
        if spec.type == "int_list":
            out = [int(v) for v in value]
            for v in out:
                if spec.min is not None and v < spec.min:
                    raise ScpiError(-222)
                if spec.max is not None and v > spec.max:
                    raise ScpiError(-222)
            return out
        return str(value)

    # ---- operations that finish later --------------------------------------------------------------

    def now_ns(self) -> int:
        """The instrument's present: the session's time plus what the current message has used."""
        return self.session.now_ns + self._elapsed_ns

    def register_pending(
        self, key: str, done_ns: int, on_done: Callable[[], None] | None = None, *, waited: bool = True
    ) -> None:
        """An operation that finishes at virtual time ``done_ns``; ``on_done`` runs once it has.

        ``waited``: *OPC, *OPC? and *WAI wait for it (False: they answer regardless)."""
        self.pending[key] = (int(done_ns), on_done)
        if waited:
            self.unwaited.discard(key)
        else:
            self.unwaited.add(key)

    def cancel_pending(self, key: str) -> None:
        self.pending.pop(key, None)
        self.unwaited.discard(key)

    def block(self, key: str, waiting: bool) -> None:
        """Mark (or clear) an operation that waits for an event that may never come."""
        if waiting:
            self.blockers.add(key)
        else:
            self.blockers.discard(key)

    def _waited(self) -> list[int]:
        return [done for key, (done, _) in self.pending.items() if key not in self.unwaited]

    def settle_pending(self) -> None:
        """Engine-internal: let waiting operations look for their event, then finish what is due
        by now; an armed *OPC sets ESR bit 0 once nothing it waits for is left. Every command
        dispatch settles; passive views settle through ``Session.view(settle=...)`` — not by
        calling this from outside the engine."""
        for comp in self._retrying:
            comp.retry_pending()
        now = self.now_ns()
        for key, (done, on_done) in sorted(self.pending.items(), key=lambda kv: kv[1][0]):
            if done <= now:
                del self.pending[key]
                self.unwaited.discard(key)
                if on_done is not None:
                    on_done()
        if self._opc_armed and not self._waited() and not self.blockers:
            self._opc_armed = False
            self.esr |= 1

    def _status_registers(self) -> list[Component]:
        """The instrument's status registers, in component order. The filter runs once; the
        component set is fixed after the instrument is built."""
        if self._status_register_cache is None:
            self._status_register_cache = [
                comp for comp in self.components.values() if comp.type_name == "status_register"
            ]
        return self._status_register_cache

    def sample_status(self) -> None:
        """Let every status register latch the condition transitions since its last look (#575):
        each command dispatch samples, so a condition that comes and goes between two reads of
        the event register is still latched (through PTR/NTR)."""
        for comp in self._status_registers():
            comp.latch_conditions()  # type: ignore[attr-defined]

    def wait_until(self, done_ns: int) -> int:
        """Wait for virtual time ``done_ns``: on the command clock, the time the caller's message
        uses; on the wall clock the call waits for real (at most 5 s, like link latency) and uses
        none; a stepped clock moves only when stepped, so nothing is waited."""
        remaining = int(done_ns) - self.now_ns()
        if remaining <= 0:
            return 0
        if self.session.clock_mode == "command":
            return remaining
        if self.session.clock_mode == "wall":
            time.sleep(min(remaining / 1e9, 5.0))
            self.session.follow_wall()
        return 0

    def wait_pending(self) -> int:
        """Wait until every pending operation is done (``*OPC?``, ``*WAI``), see :meth:`wait_until`."""
        waited = self._waited()
        if not waited:
            return 0
        return self.wait_until(max(waited))

    def opc_query(self) -> tuple[bytes | None, int]:
        """``*OPC?``: ``1`` once what it waits for is done; no reply while an operation waits for an
        event that does not come (the client's read times out, as on hardware)."""
        used = self.wait_pending()
        return (None if self.blockers else b"1"), used

    def arm_opc(self) -> None:
        """``*OPC``: ESR bit 0 now, or once the pending operations are done."""
        if self._waited() or self.blockers:
            self._opc_armed = True
        else:
            self.esr |= 1

    def reset(self) -> None:
        self.pending.clear()
        self.unwaited.clear()
        self.blockers.clear()
        self._opc_armed = False
        self._reset_state()
        for comp in self.components.values():
            comp.reset()
        self.session.state_changed(self, "*RST", None, None, electrical=True, source="scpi")

    # ---- message exchange ---------------------------------------------------------------------------

    def receive(self, message: bytes) -> int:
        """Execute a program message; queue responses. Returns the virtual time consumed (ns)."""
        if self._output:
            self._output.clear()
            self._push(ScpiError(-410))
        responses: list[bytes] = []
        duration = 0
        self._elapsed_ns = 0
        path: tuple[Token, ...] = ()
        try:
            units = split_units(message)
        except ScpiError as e:
            self._push(e)
            return 0
        for raw in units:
            try:
                unit = parse_unit(raw)
            except ScpiError as e:
                self._push(e)
                continue
            tokens = resolve_tokens(unit, path)
            if not unit.common:
                path = tokens[:-1]
            try:
                resp, d = self._execute(unit, tokens)
                duration += d
                self._elapsed_ns = duration
                if resp is not None:
                    responses.append(resp)
            except ScpiError as e:
                self._push(self._parameter_error(e))
            except Exception as e:  # a model defect must not take the client's script down
                log.exception("%s: internal error executing %r", self.name, unit.raw_header)
                self._push(
                    ScpiError(-300, f"internal simulator error in {unit.raw_header}: {type(e).__name__}")
                )
        if responses:
            self._output.append(b";".join(responses) + b"\n")
        self._elapsed_ns = 0
        return duration

    PANEL_ACTIONS = ("run", "stop", "single")

    def panel(self, action: str) -> None:
        """A front-panel operation (D32): Run, Stop or Single pressed on the instrument itself.

        Not a SCPI command: nothing reaches the communication log, the error queue or a script's
        pending reply, and no virtual time passes. ``display.actions`` may name the command the
        model's own button is equivalent to (run inside the instrument); otherwise the acquisition
        component's ``run``, ``stop`` and ``single`` operations (``digitize`` for a scope without a
        single mode). A refused action raises the ScpiError instead of queuing it."""
        if action not in self.PANEL_ACTIONS:
            raise ScpiError(-224, f"no front-panel action {action!r}")
        self.on_panel = True  # what the button starts is the panel's own: no virtual time passes
        try:
            self._panel(action)
        finally:
            self.on_panel = False

    def _panel(self, action: str) -> None:
        command = self.definition.display.actions.get(action)
        if command:
            path: tuple[Token, ...] = ()
            for raw in split_units(command.encode()):
                unit = parse_unit(raw)
                tokens = resolve_tokens(unit, path)
                if not unit.common:
                    path = tokens[:-1]
                self._execute(unit, tokens)  # the reply and the time it would take are a panel's own
            return
        acq = self.components.get("acq")
        if acq is None:
            raise ScpiError(-224, f"{self.name} has no front-panel {action}")
        name = action
        if action == "single" and not acq.param("single"):
            name = "digitize"  # no single mode: one acquisition of the displayed channels
        fn = acq.operation(name)
        if fn is None:
            raise ScpiError(-224, f"{self.name} has no front-panel {action}")
        fn()

    def take_output(self) -> bytes | None:
        """Pop the next response message, or None when nothing is queued."""
        return self._output.pop(0) if self._output else None

    @property
    def has_output(self) -> bool:
        return bool(self._output)

    def _push(self, err: ScpiError) -> None:
        detail = err.detail
        if err.text is None:
            table = self.definition.errors.get(err.code)
            if table is not None or self.real:
                # A real model answers with its manual's text only; the detail stays in the event.
                err = replace(err, text=table if table is not None else err.standard_text)
        self.errors.push(err)
        self.esr |= 1 << ESR_BITS[err.esr_class]
        data: dict[str, Any] = {"code": err.code, "message": err.message}
        if err.text is not None and detail and detail not in err.message:
            data["message"] = f"{err.message} ({detail})"
        reason = err.reason
        if reason is None and (
            (err.text is not None and err.text.endswith(NOT_MODELED)) or NOT_MODELED in detail
        ):
            reason = "not_modeled"  # a command, or a parameter value, this definition does not implement
        if reason is not None:
            data["reason"] = reason
        for key in ("origin", "closest", "param", "range"):
            value = getattr(err, key)
            if value is not None:
                data[key] = value
        self.session.instrument_event(self, "error", data)

    def _error_reply(self, err: ScpiError) -> bytes:
        """An error queue entry as :SYSTem:ERRor? writes it, in the manual's form for a real model."""
        if err.code == 0 and 0 in self.definition.errors:
            err = ScpiError(0, text=self.definition.errors[0])
        sign = "+" if self.definition.format.error_code == "signed" and err.code >= 0 else ""
        return f'{sign}{err.code},"{err.message}"'.encode()

    def _undefined(self, unit: Unit, tokens: tuple[Token, ...], *, builtin: bool = False) -> ScpiError:
        """-113 for a header the definition does not have, classified by where its declaration
        lives (tui-design.md 1.1): the coverage table, the definition, or the model's ``builtins``.
        The reply says the header is not implemented *in this definition*: the basis (a vendor
        manual, a design specification or a contract) belongs to the definition's author (#172).

        ``builtin``: a built-in header the definition's ``builtins`` leaves out. Raises
        :class:`_Ignored` instead when the definition ignores unknown commands.
        """
        header = unit.raw_header
        if self.definition.unknown_commands == "ignore":
            raise _Ignored(header)
        if builtin:
            return ScpiError(
                -113,
                header,
                text=self._undefined_text(header),
                reason="builtin_excluded",
                origin=self.source_file,
            )
        reason, origin, closest = self._classify_header(unit, tokens)
        return ScpiError(
            -113, header, text=self._undefined_text(header), reason=reason, origin=origin, closest=closest
        )

    def _undefined_text(self, header: str) -> str | None:
        """The -113 reply: a definition with a reference says the header is not implemented in it."""
        if not self.real:
            return None
        base = self.definition.errors.get(-113, ScpiError(-113).standard_text)
        return f"{base};{NOT_MODELED}"

    def _coverage_entries(self) -> list[_Covered] | None:
        """The definition's coverage table, loaded on first use (D18)."""
        if not self._coverage_tried:
            self._coverage_tried = True
            self._load_coverage()
        return self._coverage

    def _load_coverage(self) -> None:
        """The coverage table next to the definition file, for runtime lookups (D18); a missing or
        unreadable table leaves the instrument running on the definition alone."""
        if self.source_file is None:
            return
        path = Path(self.source_file)
        table = path.with_name(path.stem + COVERAGE_SUFFIX)
        if not table.is_file():
            return
        cov = _load_coverage_file(table, table.stat().st_mtime)
        if cov is None:
            return
        entries: list[_Covered] = []
        try:
            for _, entry in cov.entries():  # type: ignore[union-attr]
                try:
                    entries.append(_Covered(Pattern.compile(entry.header), entry.header, entry.level))
                except ValueError:
                    continue  # a header the pattern language cannot read: no runtime match for it
        except Exception:
            return  # an unexpected table shape: the definition alone decides
        self._coverage = entries
        self._coverage_file = str(table)

    def _classify_header(self, unit: Unit, tokens: tuple[Token, ...]) -> tuple[str, str | None, str | None]:
        """(reason, origin, closest) for a header the definition does not execute."""
        coverage = self._coverage_entries()
        if coverage is not None:
            covered = next((e for e in coverage if self._covered(e, unit, tokens)), None)
            if covered is not None:
                # A command the author listed (or one of theirs used in a form the model lacks).
                return "not_modeled", self._coverage_file, None
            return "not_in_model", self._coverage_file, self._closest_header(unit)
        if not self.real:
            return "not_in_definition", self.source_file, self._closest_header(unit)
        return "not_modeled", self.source_file, None  # no coverage table: the old classification

    @staticmethod
    def _covered(entry: _Covered, unit: Unit, tokens: tuple[Token, ...]) -> bool:
        """Whether the message unit uses the coverage entry's command (library/coverage.py `_matches`)."""
        if entry.pattern.common:
            return unit.common and unit.tokens[0].keyword == entry.pattern.nodes[0].long
        return not unit.common and entry.pattern.match(tokens) is not None

    def _closest_header(self, unit: Unit) -> str | None:
        """The author's command closest to the one the client sent (its own long and short
        spellings), or None when none is reasonably close."""
        sent = _header_key(unit.raw_header)
        if not sent:
            return None
        if self._header_keys is None:
            keys: dict[str, str] = {}
            for entry in self._coverage_entries() or ():
                try:
                    for form in _header_forms(entry.pattern):
                        keys.setdefault(_header_key(form), entry.header)
                except Exception:
                    continue
            for compiled in self._commands:
                for form in _header_forms(compiled.pattern):
                    keys.setdefault(_header_key(form), compiled.spec.header)
            self._header_keys = keys
            self._header_key_list = list(keys)
        hit = difflib.get_close_matches(sent, self._header_key_list, n=1, cutoff=0.6)
        return self._header_keys[hit[0]] if hit else None

    def push_error(self, code: int, detail: str = "") -> None:
        """Queue a device error from a component (e.g. an overload)."""
        self._push(ScpiError(code, detail))

    def _parameter_error(self, err: ScpiError) -> ScpiError:
        """A parameter error a command raised without naming its parameter (a component's check):
        still classified, with the declared range left out (tui-design.md 1.1). Device errors never
        come through here — they are queued with :meth:`push_error` instead."""
        if err.reason is None and err.code in BAD_PARAMETER_CODES and NOT_MODELED not in err.detail:
            return replace(err, reason="bad_parameter")
        return err

    def _bad_parameter(
        self, err: ScpiError, param: str | None, spec: StateSpec | ParamSpec | None
    ) -> ScpiError:
        """A parameter error raised where the definition's declaration is known: name the parameter
        and its declared range. The "not implemented in this definition" refusals keep their own category."""
        if err.reason is not None or err.code not in BAD_PARAMETER_CODES or NOT_MODELED in err.detail:
            return err
        return replace(
            err, reason="bad_parameter", origin=self.source_file, param=param, range=_declared_range(spec)
        )

    # ---- command execution --------------------------------------------------------------------------

    def _builtin_allowed(self, header: str) -> bool:
        return builtin_allowed(self.definition, header)

    def _execute(self, unit: Unit, tokens: tuple[Token, ...]) -> tuple[bytes | None, int]:
        try:
            return self._dispatch(unit, tokens)
        except _Ignored:
            pass
        # unknown_commands: ignore — swallowed without an error or a reply, but never silently (D16).
        self.session.instrument_event(
            self,
            "ignored",
            {"header": unit.raw_header, "query": unit.query, "reason": "ignored", "origin": self.source_file},
        )
        return None, 0

    # ---- the instrument's channel numbers (#207, D51) -------------------------------------------

    def limits(self, path: str) -> tuple[float | None, float | None]:
        """A setting's (min, max) now: the declared ones, narrowed by any component watching it
        (:meth:`Component.state_limits`, #437)."""
        st = self.specs[path]
        lo, hi = st.min, st.max
        for comp in self._watchers.get(path, ()):
            narrowed = comp.state_limits(path)
            if narrowed is None:
                continue
            if narrowed[0] is not None:
                lo = narrowed[0] if lo is None else max(lo, narrowed[0])
            if narrowed[1] is not None:
                hi = narrowed[1] if hi is None else min(hi, narrowed[1])
        return lo, hi

    def channel_number(self, channel: int) -> int:
        """The channel's number on the bus: 101 for a DAQ's first channel, else its index."""
        return self._numbers_by_channel.get(channel, channel)

    def channel_exists(self, number: int) -> bool:
        """Whether a SCPI channel number names a channel of this instrument."""
        if self._channels_by_number:
            return number in self._channels_by_number
        return 1 <= number <= self.channel_count

    def channel_index(self, number: int) -> int:
        """The channel index (``ch<n>`` in the definition) that a SCPI channel number names."""
        if self._channels_by_number:
            index = self._channels_by_number.get(number)
            if index is None:
                raise ScpiError(-114, f"channel {number} does not exist")
            return index
        if not 1 <= number <= self.channel_count:
            raise ScpiError(-114, f"channel {number} does not exist")
        return number

    def _dispatch(self, unit: Unit, tokens: tuple[Token, ...]) -> tuple[bytes | None, int]:
        self.settle_pending()
        self.sample_status()
        if unit.common:
            # A definition may give a common header its own behavior (e.g. a dialect's *CLS?).
            for compiled in self._commands:
                if compiled.pattern.common and compiled.pattern.match(tokens) is not None:
                    return self._run(compiled, unit, None, {})
            word = unit.tokens[0].keyword
            if not self._builtin_allowed(f"*{word}"):
                raise self._undefined(unit, tokens, builtin=f"*{word}" in COMMON_BUILTINS)
            q_ok, s_ok = COMMON_DIRECTIONS.get(word, (True, True))
            if (unit.query and not q_ok) or (not unit.query and not s_ok):
                raise self._undefined(unit, tokens)
            if word == "OPC" and unit.query:
                return self.opc_query()
            if word == "WAI":
                return None, self.wait_pending()
            if word == "TRG" and not unit.query:
                return self._bus_trigger(unit)
            return self._common(unit), 0
        refused: CommandSpec | None = None  # a header that matched, with other parameter values
        for compiled in self._commands:
            if compiled.pattern.common:
                continue
            caps = compiled.pattern.match(tokens)
            if caps is None:
                continue
            if compiled.spec.when and not when_matches(compiled.spec, unit):
                refused = compiled.spec
                continue
            channel = caps.get("ch")
            if "ch" in caps and channel is not None:
                channel = self.channel_index(channel)
            elif "ch" in caps:
                channel = 1  # no suffix written: the first channel
            suffixes = {k: (1 if v is None else v) for k, v in caps.items()}
            for name, allowed in (compiled.spec.suffixes or {}).items():
                if suffixes.get(name, 1) not in allowed:
                    raise ScpiError(-114, f"{unit.raw_header}: suffix {suffixes.get(name)} is not {allowed}")
            if "ch" in caps and caps["ch"] is None:
                suffixes["ch"] = self.channel_number(1)  # the default channel's own number
            return self._run(compiled, unit, channel, suffixes)
        if refused is not None:
            raise ScpiError(-224, f"{unit.raw_header}: parameter not implemented in this definition")
        named = named_builtin(tokens)
        if named is not None and not self._builtin_allowed(named):
            raise self._undefined(unit, tokens, builtin=True)  # the definition's builtins leave it out
        builtin = self._builtin(unit, tokens) if self._builtins is None or named is not None else None
        if builtin is _HANDLED:
            return None, 0
        if builtin is not None:
            return builtin, 0
        raise self._undefined(unit, tokens)

    def _run(
        self, compiled: _Compiled, unit: Unit, channel: int | None, suffixes: dict[str, int]
    ) -> tuple[bytes | None, int]:
        """Execute a command of the definition (a setting, a component operation or several settings)."""
        spec = compiled.spec
        if (unit.query and not spec.query) or (not unit.query and not spec.set):
            # A header the definition has, used the wrong way: an error even when unknown ones are ignored.
            raise ScpiError(-113, f"{unit.raw_header} is {'not a query' if unit.query else 'a query only'}")
        if spec.state is not None:
            body = self._state_command(spec, unit, channel)
        elif spec.op is not None:
            if unit.query and spec.read is not None:
                # The same header: set calls the operation, query reads the setting it writes.
                path = self._resolve(spec.read, channel)
                body = self._format(self.specs[path], self.state[path], spec.format)
                return body, parse_duration(spec.duration)
            result = self._op_command(compiled, spec, unit, channel, suffixes)
            return result.response, result.duration_ns + parse_duration(spec.duration)
        elif any(p.type == "channel_list" for p in spec.params):
            body = self._channel_list_command(spec, unit, channel)
        elif any(p.type == "quantities" for p in spec.params):
            body = self._quantities_command(spec, unit, channel)
        else:
            body = self._settings_command(spec, unit, channel)
        if body is not None:
            body = self._reply(compiled, body, channel, suffixes)
        return body, parse_duration(spec.duration)

    def _resolve(self, ref: str, channel: int | None) -> str:
        if ref.startswith("ch.") and channel is not None:
            return f"ch{channel}.{ref[3:]}"
        return ref

    # ---- replies: the dialect's header echo and the command's template ------------------------------

    def echo_mode(self) -> str:
        """off, short or long: whether and how replies repeat the command header (dialect.echo_header)."""
        mode = self.definition.dialect.echo_header
        if mode.startswith("$"):
            mode = str(self.state.get(mode[1:], "off"))
        return mode.lower()

    def _reply(
        self,
        compiled: _Compiled,
        body: bytes,
        channel: int | None,
        suffixes: dict[str, int],
        fields: dict[str, str] | None = None,
    ) -> bytes:
        template = compiled.spec.reply
        mode = self.echo_mode()
        if template is None and mode == "off":
            return body
        template = template or "{header} {value}"
        header = ""
        if mode != "off":
            header = compiled.pattern.echo(suffixes, long=mode == "long")
        else:
            template = template.replace("{header} ", "").replace("{header}", "")
        if not body:  # nothing to show: one blank, not two ('C1:INSP " "')
            template = template.replace(" {value}", "{value}")

        def field(m: re.Match[str]) -> str:
            name = m.group(1)
            if name == "header":
                return header
            if name == "value":
                return body.decode(errors="replace")
            # the reply as a definite-length block (DAQ970A ROUT:SCAN? answers #13(@), #415)
            if name == "value_block":
                return V.format_block(body).decode(errors="replace")
            if fields and name in fields:
                return fields[name]
            path = self._resolve(name, channel)
            return self._format(self.specs[path], self.state[path], compiled.spec.format).decode()

        return re.sub(r"\{([^{}]+)\}", field, template).encode()

    # ---- settings --------------------------------------------------------------------------------------

    def _state_command(self, spec: CommandSpec, unit: Unit, channel: int | None) -> bytes | None:
        assert spec.state is not None
        path = self._resolve(spec.state, channel)
        st = self.specs[path]
        if unit.query:
            if unit.params:
                which = V.parse_string(unit.params[0]).upper()
                if st.type in ("float", "int") and which in (
                    "MIN",
                    "MINIMUM",
                    "MAX",
                    "MAXIMUM",
                    "DEF",
                    "DEFAULT",
                ):
                    lo, hi = self.limits(path)
                    v = lo if which.startswith("MIN") else hi if which.startswith("MAX") else st.default
                    return self._format(st, v * spec.factor if v is not None else v, spec.format)
                raise ScpiError(-108)
            value = self.state[path]
            return self._format(st, value * spec.factor if spec.factor != 1 else value, spec.format)
        if not unit.params:
            if spec.set_value is None:
                raise ScpiError(-109)
            self.set_state(path, spec.set_value)
            return None
        if st.type == "enum_list":
            try:
                self.set_state(path, [V.parse_enum(p, st.options or []) for p in unit.params])
            except ScpiError as e:
                raise self._bad_parameter(e, path, st) from e
            return None
        if len(unit.params) > 1 and st.type != "string":
            raise ScpiError(-108)
        try:
            if spec.factor != 1:
                # The command's number is the setting times factor (:TIMebase:RANGe is ten :SCALe).
                word = V.parse_string(unit.params[0]).strip().upper()
                keyword = word in ("MIN", "MINIMUM", "MAX", "MAXIMUM", "DEF", "DEFAULT") or word in (
                    st.special or {}
                )
                v = V.parse_float(unit.params[0], st.unit)
                value = self._parse_state(st, unit.params[0], path) if keyword else v / spec.factor
            else:
                value = self._parse_state(st, unit.params[0], path)
            self.set_state(path, value)
        except ScpiError as e:
            raise self._bad_parameter(e, path, st) from e
        return None

    def _check_all(self, writes: Iterable[tuple[str, Any]]) -> dict[str, Any]:
        """Validate every staged write against its setting's declaration; raise, with the setting
        named, before any of them is applied. Later entries for the same path overwrite earlier
        ones, so a stage can put the command's constants first and let the arguments win."""
        checked: dict[str, Any] = {}
        for path, v in writes:
            try:
                checked[path] = self._validate(self.specs[path], v)
            except ScpiError as e:
                raise self._bad_parameter(e, path, self.specs[path]) from e
        return checked

    def _commit(self, checked: dict[str, Any]) -> None:
        """Apply the checked writes all-or-nothing (#550): a write a component refuses — because
        the settings together make no sense (#580) — takes back the ones before it. After the
        writes, the watchers of every written path may check the combination and refuse too."""
        done: list[tuple[str, Any]] = []
        try:
            for path, v in checked.items():
                before = self.state[path]
                self.set_state(path, v)
                done.append((path, before))
            for comp in {c.uid: c for path in checked for c in self._watchers.get(path, ())}.values():
                comp.check_settings()
        except ScpiError:
            for path, before in reversed(done):
                self.set_state(path, before)
            raise

    def _settings_command(self, spec: CommandSpec, unit: Unit, channel: int | None) -> bytes | None:
        """A command whose parameters each write a setting (``TRSE EDGE,SR,C1``); all are checked
        before any is set. The query answers the settings in the same order and form."""
        raw = list(unit.params)
        index: dict[str, int] = {}
        # Index parameters pick the settings. A query carries them alone, in order; a set carries
        # each at its own position (VIEW CHAN2; TRIG:LEV 0.5,CHAN2; PACU 1,AMPL,C1).
        index_params = [(k, ps) for k, ps in enumerate(spec.params) if ps.index]
        tokens: dict[str, bytes | None] = {}
        if unit.query:
            for _k, ps in index_params:
                tokens[ps.name] = raw.pop(0) if raw else None
        else:
            for k, ps in reversed(index_params):
                tokens[ps.name] = raw.pop(k) if k < len(raw) else None
        for _k, ps in index_params:
            token = tokens[ps.name]
            if token is None:
                if ps.index_default is None:
                    raise ScpiError(-109, f"missing {ps.name}")
                value = self._index_value(ps, ps.index_default, channel)
            else:
                value = self._index_value(ps, token, channel)
            if (ps.min is not None and value < ps.min) or (ps.max is not None and value > ps.max):
                raise self._bad_parameter(ScpiError(-222, f"{ps.name} {value} out of range"), ps.name, ps)
            index[ps.name] = value

        def at(ref: str) -> str:
            for name, value in index.items():
                ref = ref.replace("{" + name + "}", str(value))
            return ref

        if unit.query:
            if raw:
                raise ScpiError(-108)
            parts: list[str] = []
            for ps in spec.params:
                if ps.index and ps.echo:
                    parts.append(self._index_text(ps, index[ps.name]))
                if ps.state is not None:
                    parts.append(self._format_path(at(ps.state), channel, spec))
                for key, ref in (ps.keys or {}).items():
                    if ref is not None:
                        parts += [key, self._format_path(at(ref), channel, spec)]
            while parts and parts[-1] in ("", '""'):  # an optional value not set: left out
                parts.pop()
            return ",".join(parts).encode()
        new: dict[str, Any] = {}
        for ps in spec.params:
            if ps.index:
                continue
            if ps.state is not None:
                if not raw:
                    if ps.optional:
                        continue
                    raise ScpiError(-109, f"missing {ps.name}")
                path = self._resolve(at(ps.state), channel)
                token = raw.pop(0)
                instead = self._keyword_writes(ps, token)
                if instead is not None:
                    # A keyword with its own settings (#344: resolution MIN picks the finest NPLC).
                    for ref, constant in instead.items():
                        new[self._resolve(at(ref), channel)] = constant
                    continue
                try:
                    value = self._parse_state(self.specs[path], token, path)
                except ScpiError as e:
                    raise self._bad_parameter(e, path, self.specs[path]) from e
                # `sets` goes with an explicit value (a number, MIN, MAX): a special word of the
                # setting (AUTO) or DEF keeps what the command's `writes` chose (#325:
                # CONF:VOLT:DC AUTO leaves autorange on).
                if not self._keyword_argument(self.specs[path], token):
                    for ref, constant in (ps.sets or {}).items():
                        new[self._resolve(at(ref), channel)] = constant
                new[path] = value
                continue
            items, raw = raw, []
            if not items and not ps.optional:
                raise ScpiError(-109, f"missing {ps.name}")
            if len(items) % 2:
                raise ScpiError(-109, f"{ps.name}: parameters come in name,value pairs")
            keys = {k.upper(): ref for k, ref in (ps.keys or {}).items()}
            for name_raw, value_raw in zip(items[::2], items[1::2], strict=True):
                name = V.parse_string(name_raw).upper()
                if name not in keys:
                    raise ScpiError(
                        -224, f"{unit.raw_header} parameter {name} not implemented in this definition"
                    )
                ref = keys[name]
                if ref is not None:
                    path = self._resolve(at(ref), channel)
                    new[path] = self._parse_state(self.specs[path], value_raw, path)
        if raw:
            raise ScpiError(-108)
        # The command's constants first: the function it selects applies to the parameters after it.
        staged: list[tuple[str, Any]] = [
            (self._resolve(at(ref), channel), value) for ref, value in spec.writes.items()
        ]
        staged += list(new.items())
        self._commit(self._check_all(staged))
        return None

    def _format_path(self, ref: str, channel: int | None, spec: CommandSpec) -> str:
        path = self._resolve(ref, channel)
        return self._format(self.specs[path], self.state[path], spec.format).decode()

    def _channel_list_command(self, spec: CommandSpec, unit: Unit, channel: int | None) -> bytes | None:
        """A command with a channel list parameter (``VOLT 5,(@1:3)``) expands over the channels:
        a write happens once per channel (each channel's own range applies), a query answers the
        channels in list order, comma-joined. A query may lead with MIN/MAX/DEF
        (``VOLT? MAX,(@1)``) and answers each channel's range limit or default, like the plain
        state query. The command's parameters carry the value(s); the list itself writes no
        setting."""
        raw = list(unit.params)
        form: str | None = None
        channels: list[int] = []
        values: list[tuple[ParamSpec, bytes]] = []
        whole: tuple[str, list[int]] | None = None
        for ps in spec.params:
            if ps.type == "channel_list":
                if raw:
                    numbers = V.parse_channel_list(
                        raw.pop(0), self.channel_exists, **self._list_rules(each=True)
                    )
                elif ps.optional and unit.query and form is not None:
                    # NPLC? MAX: a limit asked without a list is answered once (#423); the
                    # limits are the same declaration for every channel
                    numbers = [self.channel_number(1)]
                elif ps.optional:
                    numbers = self._omitted_list(ps)
                else:
                    raise ScpiError(-109, f"missing {ps.name}")
                channels = [self.channel_index(n) for n in numbers]
                if ps.state is not None and not unit.query:
                    whole = (ps.state, list(numbers))  # written whole with the rest (#300)
            elif unit.query:
                if form is None and raw:
                    word = V.parse_string(raw[0]).upper()
                    if word in ("MIN", "MINIMUM", "MAX", "MAXIMUM", "DEF", "DEFAULT"):
                        form = word
                        raw.pop(0)
                continue  # beyond the form word a query carries only the channel list
            elif not raw or (ps.optional and _is_channel_list(raw[0])):
                if ps.optional:
                    continue  # left out: a (@…) here is the channel list after it (#300)
                raise ScpiError(-109, f"missing {ps.name}")
            else:
                values.append((ps, raw.pop(0)))
        if raw:
            raise ScpiError(-108)
        if unit.query:
            parts = []
            for n in channels:
                for ps in spec.params:
                    if ps.type == "channel_list" or ps.state is None:
                        continue
                    path = self._resolve(ps.state, n)
                    st = self.specs[path]
                    if form is not None and st.type in ("float", "int"):
                        v = (
                            st.min
                            if form.startswith("MIN")
                            else st.max
                            if form.startswith("MAX")
                            else st.default
                        )
                        parts.append(self._format(st, v, spec.format).decode())
                    else:
                        parts.append(self._format(st, self.state[path], spec.format).decode())
            return ",".join(parts).encode()
        staged: list[tuple[str, Any]] = []
        # The command's own constants first (#300: CONF:VOLT:AC 10,(@101) selects the function),
        # per channel when they name a channel setting; then each parameter's `sets` beside its
        # value (not for DEF or a special word, as on a command without a list).
        for n in channels:
            for ref, value in spec.writes.items():
                staged.append((self._resolve(ref, n), value))
        for n in channels:
            for ps, r in values:
                instead = self._keyword_writes(ps, r)
                if instead is not None:
                    # A keyword with its own settings, per channel (#354: a CONF range MIN/MAX/DEF
                    # on a per-channel measurement configuration).
                    for ref, constant in instead.items():
                        staged.append((self._resolve(ref, n), constant))
                    continue
                path = self._resolve(ps.state, n)  # type: ignore[arg-type]
                staged.append((path, self._parse_state(self.specs[path], r, path)))
                if ps.sets and not self._keyword_argument(self.specs[path], r):
                    for ref, value in ps.sets.items():
                        staged.append((self._resolve(ref, n), value))
        if whole is not None:
            staged.append((whole[0], whole[1]))
        self._commit(self._check_all(staged))
        return None

    def _quantities_command(self, spec: CommandSpec, unit: Unit, channel: int | None) -> bytes | None:
        """A command whose parameters are 'value unit' pairs (``OUT 1 V, 1 kHz``), each written into
        the setting its unit names, a combination writing more (metrology-authoring.md 5.2). All
        writes are checked before any is set; the query answers through the reply template."""
        ps = next(p for p in spec.params if p.type == "quantities")
        if unit.query:
            if unit.params:
                raise ScpiError(-108)
            return b""  # the reply template composes the answer from the settings
        if not unit.params:
            raise ScpiError(-109)
        try:
            writes = self._parse_quantities(ps, unit.params, channel)
        except ScpiError as e:
            raise self._bad_parameter(e, ps.name, ps) from e
        self._commit(self._check_all(writes.items()))
        return None

    def _parse_quantities(self, ps: ParamSpec, raw: tuple[bytes, ...], channel: int | None) -> dict[str, Any]:
        assert ps.units is not None
        order = {u.upper(): i for i, u in enumerate(ps.units)}
        used: dict[str, float] = {}
        writes: dict[str, Any] = {}
        for r in raw:
            text = V.parse_string(r)
            try:
                value, base = parse_quantity(text)
            except ValueError:
                raise ScpiError(-104, f"expected a number, got {text!r}") from None
            if not base:
                raise ScpiError(ps.missing_unit_error, f"{text!r}: a unit is required")
            name = next((u for u in ps.units if u.upper() == base), None)
            if name is None:
                # a unit the real instrument takes but this model does not (OUT 1 A on a model of
                # the voltage functions only): refused as not modeled, not as a bad unit (#341)
                self._refuse_not_modeled(ps.not_modeled, base.encode())
                raise ScpiError(ps.unknown_unit_error, f"unit {base!r} is not one this command takes")
            if name in used:
                raise ScpiError(ps.duplicate_unit_error, f"unit {name} is given twice")
            used[name] = value
            entry = ps.units[name]
            writes[self._resolve(entry.state, channel)] = value
            for ref, v in entry.sets.items():
                writes[self._resolve(ref, channel)] = v
        combo = ",".join(sorted(used, key=lambda u: order[u.upper()]))
        combine = self._combine_of(ps)
        if ps.combine is not None:
            if combo not in combine:
                raise ScpiError(ps.combine_error, f"the unit combination {combo} is not allowed here")
            first: dict[str, Any] = {}
            for target, value in combine[combo].items():
                first[self._resolve(target, channel)] = self._conditional_write(value, writes, channel)
            # the combination's settings (the function the values are for) go in first (#550: a
            # 5730A OUT 1 MOHM selects OHM before the level is checked against its ranges)
            writes = {**first, **{k: v for k, v in writes.items() if k not in first}}
        return writes

    def _combine_of(self, ps: ParamSpec) -> dict[str, dict[str, Any]]:
        """The combine table keyed the way _parse_quantities keys a combination: units joined with
        commas in the units table's order (a definition may write the key in any order)."""
        assert ps.units is not None and ps.combine is not None
        order = {u.upper(): i for i, u in enumerate(ps.units)}
        out: dict[str, dict[str, Any]] = {}
        for key, value in ps.combine.items():
            names = [k.strip().upper() for k in key.split(",")]
            if all(n in order for n in names):
                out[",".join(sorted(set(names), key=lambda n: order[n]))] = value
        return out

    def _conditional_write(self, value: Any, writes: dict[str, Any], channel: int | None) -> Any:
        """A combine entry's value: ``{if_zero: <state>, then: ..., else: ...}`` decides by the
        value the state has once this command's writes are in (0 Hz is DC, 5.2)."""
        if isinstance(value, dict) and "if_zero" in value:
            path = self._resolve(str(value["if_zero"]), channel)
            current = writes.get(path, self.state.get(path))
            return value["then" if current == 0 else "else"]
        return value

    def parse_setting(self, path: str, raw: bytes) -> Any:
        """A parameter as the setting at ``path`` reads it (units, special words, enum forms)."""
        return self._parse_state(self.specs[path], raw, path)

    def format_setting(self, path: str) -> bytes:
        """The setting at ``path`` as the definition formats replies."""
        return self._format(self.specs[path], self.state[path])

    def _index_value(self, ps: ParamSpec, raw: Any, channel: int | None) -> int:
        """An index parameter's number: an int, an index word (``CHANnel2`` -> 2), or for a
        left-out one its ``index_default`` (a number or a $setting holding a word or number)."""
        if isinstance(raw, str) and raw.startswith("$"):
            raw = self.state[self._resolve(raw[1:], channel)]
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            return int(raw)
        text = raw if isinstance(raw, bytes) else str(raw).encode()
        if ps.index_words:
            if not (ps.index_numbers and text.strip().lstrip(b"+-").isdigit()):
                word = V.parse_enum(text, list(ps.index_words))
                return int(ps.index_words[word])
            # the number itself, as the SR86x takes it (CDSP 0 = CDSP DAT1, #333)
            n = V.parse_int(text)
            if n not in ps.index_words.values():
                raise ScpiError(-222, f"index {n} is not one of {sorted(ps.index_words.values())}")
            return n
        return V.parse_int(text)

    def _index_text(self, ps: ParamSpec, value: int) -> str:
        if ps.index_words:
            for word, n in ps.index_words.items():
                if n == value:
                    return V.short_form(word)
        return str(value)

    def _omitted_list(self, ps: ParamSpec) -> list[int]:
        """The channels of a channel list left out: the selected output when the definition
        declares one (#295), else the parameter's default, else the first channel."""
        selected = self.definition.channels.selected
        if selected is not None:
            return [int(self.state[selected])]
        if isinstance(ps.default, str):
            # a setting holding the list (#423: the DAQ970A's omitted list is the scan list)
            numbers = [int(n) for n in self.state[ps.default.removeprefix("$")] or []]
            if not numbers:
                raise ScpiError(-221, f"no channel list given and {ps.default[1:]} is empty")
            return numbers
        return ps.default or [self.channel_number(1)]

    def _list_rules(self, each: bool = False) -> dict[str, bool]:
        """The definition's channel list rules (#260, #418): descending ranges, repeated channels,
        an empty list. ``each``: a list a command runs over channel by channel (a write, a query,
        an operation such as MEAS? (@…)) — an empty one there is still -108, so a query is never
        silently left without an answer; ``empty`` admits ``(@)`` only where a list setting is
        written whole (ROUT:SCAN (@) clears the scan list, #420)."""
        rules = self.definition.channels.lists
        return {
            "descending": rules.descending,
            "repeated": rules.repeated,
            "empty": rules.empty and not each,
        }

    @staticmethod
    def _keyword_writes(ps: ParamSpec, raw: bytes) -> dict[str, Any] | None:
        """The settings a parameter's ``keywords`` table gives for this argument, if it names it."""
        if not ps.keywords:
            return None
        word = V.parse_string(raw).strip().upper()
        word = {"MINIMUM": "MIN", "MAXIMUM": "MAX", "DEFAULT": "DEF"}.get(word, word)
        return ps.keywords.get(word)

    @staticmethod
    def _keyword_argument(st: StateSpec, raw: bytes) -> bool:
        """Whether a numeric argument is DEF or one of the setting's special words (AUTO)."""
        word = raw.decode(errors="replace").strip().upper()
        return word.startswith("DEF") or bool(st.special and word in st.special)

    @classmethod
    def _keyword_word(cls, ps: ParamSpec, st: StateSpec | None, raw: bytes) -> bool:
        """Whether a float argument names a keyword of its parameter or of the state it binds
        (#354): MIN/MAX/DEF of the parameter's ``keywords`` table, or DEF / a special word of
        the setting — not a number, and read per state by the binding layer."""
        if cls._keyword_writes(ps, raw) is not None:
            return True
        return st is not None and cls._keyword_argument(st, raw)

    @staticmethod
    def _refuse_not_modeled(words: dict[str, str] | None, raw: bytes) -> None:
        """A word the real instrument takes but this definition does not model (``not_modeled``,
        #360): -224, classified as not modeled, with the definition's reason."""
        if not words:
            return
        word = raw.decode(errors="replace").strip()
        for declared, reason in words.items():
            if V.matches_word(word, declared):
                raise ScpiError(-224, f"{declared}: {reason}; {NOT_MODELED}")

    def _parse_state(self, st: StateSpec, raw: bytes, path: str | None = None) -> Any:
        self._refuse_not_modeled(st.not_modeled, raw)
        if st.type in ("float", "int"):
            word = raw.decode(errors="replace").strip().upper()
            if st.special and word in st.special:
                return st.special[word]
            multipliers = self.definition.dialect.multipliers
            v = V.parse_multiplied(raw, multipliers) if multipliers and st.unit is None else None
            if v is None:
                lo, hi = self.limits(path) if path is not None else (st.min, st.max)
                v = V.parse_float(raw, st.unit, lo, hi, st.default)
            return v
        if st.type == "bool":
            return V.parse_bool(raw)
        if st.type == "enum":
            return V.parse_enum(raw, st.options or [])
        if st.type == "int_list":
            # A declared channel numbering is the list's domain too (#207); without one the
            # state's own min/max check the numbers, as before.
            return V.parse_channel_list(
                raw, self.channel_exists if self._channels_by_number else None, **self._list_rules()
            )
        return V.parse_string(raw)

    def format_number(self, value: float) -> bytes:
        """A number in the definition's response format (a reading, a measured value)."""
        f = self.definition.format
        if f.number == "engineering":
            if f.significant:
                return V.format_engineering_digits(
                    value, f.significant, sign=f.sign, exponent_digits=f.exponent_digits
                )
            return V.format_engineering(value)
        return V.format_float(value, f.digits, style=f.number, sign=f.sign, exponent_digits=f.exponent_digits)

    def _format(self, st: StateSpec, value: Any, override: ResponseFormat | None = None) -> bytes:
        f = override or self.definition.format
        if st.type == "float":
            if st.special and value in st.special.values() and not math.isfinite(value):
                return next(k for k, v in st.special.items() if v == value).encode()
            if f.number == "si":
                return V.format_si(value, f.significant or 9, f.unit or st.unit or "", sign=f.sign)
            if f.number == "engineering":
                if f.significant:
                    return V.format_engineering_digits(
                        value, f.significant, sign=f.sign, exponent_digits=f.exponent_digits
                    )
                return V.format_engineering(value)
            return V.format_float(
                value, f.digits, style=f.number, sign=f.sign, exponent_digits=f.exponent_digits
            )
        if st.type == "int":
            if f.integer == "engineering":
                return V.format_engineering(int(value))
            return (b"+" if f.integer == "signed" and int(value) >= 0 else b"") + V.format_int(value)
        if st.type == "bool":
            return V.format_bool(value, word=f.boolean == "word")
        if st.type == "enum":
            if st.replies and value in st.replies:
                return st.replies[value].encode()  # the instrument's own query word (#268)
            return V.format_enum(value, long=f.enum == "long", as_defined=f.enum == "as_defined")
        if st.type == "enum_list":
            return b",".join(
                V.format_enum(v, long=f.enum == "long", as_defined=f.enum == "as_defined") for v in value
            )
        if st.type == "int_list":
            return V.format_channel_list(value)
        return V.format_string(str(value))

    def _op_command(
        self,
        compiled: _Compiled,
        spec: CommandSpec,
        unit: Unit,
        channel: int | None,
        suffixes: dict[str, int],
    ) -> OpResult:
        assert spec.op is not None
        comp_ref, op_name = spec.op.rsplit(".", 1)
        args = {**spec.args, **self._parse_params(spec.params, unit.params)}
        # A channel list written whole into a setting (the scan list) names the channels the
        # command's channel settings apply to (#365).
        listed = [
            self.channel_index(n)
            for ps in spec.params
            if ps.type == "channel_list" and ps.state is not None
            for n in self.state[ps.state]
        ]
        staged: list[tuple[str, Any]] = []
        if spec.writes:
            # The command selects settings before its operation runs (#308); a channel setting is
            # written for every channel of a list that names the channels (#365: MEAS:RES? (@101)).
            for target in listed or [channel]:
                for ref, value in spec.writes.items():
                    staged.append((self._resolve(ref, target), value))
        for ps in spec.params:  # an operation's parameter bound to a setting writes it (#300)
            if ps.type != "channel_list" and ps.state is not None and args.get(ps.name) is not None:
                for target in listed or [channel]:
                    path = self._resolve(ps.state, target)
                    value = args[ps.name]
                    if isinstance(value, bytes):
                        # A keyword word (#354): its own settings, or the value DEF / a special
                        # word stands for; the parameter's `sets` do not apply, as on a command
                        # without a list.
                        instead = self._keyword_writes(ps, value)
                        if instead is not None:
                            for ref, constant in instead.items():
                                staged.append((self._resolve(ref, target), constant))
                            continue
                        st = self.specs[path]
                        staged.append((path, self._parse_state(st, value, path)))
                        continue
                    staged.append((path, value))
                    for ref, constant in (ps.sets or {}).items():
                        staged.append((self._resolve(ref, target), constant))
        # after the command's constants: the arguments win
        self._commit(self._check_all(staged))
        list_spec = next((p for p in spec.params if p.type == "channel_list" and p.state is None), None)
        if list_spec is None:
            result = self._run_op(comp_ref, op_name, unit, channel, suffixes, args)
            if not unit.query:
                # A set never answers, even when the operation returns a result (CAL adjusts; CAL?
                # reports it). Only a query's reply goes on the wire.
                return OpResult(duration_ns=result.duration_ns)
            response = result.response
            if response is not None and spec.reply is not None:  # an operation's reply in a template
                response = self._reply(compiled, response, channel, suffixes, result.fields)
            return OpResult(response=response, duration_ns=result.duration_ns)
        responses = []
        duration = 0
        for n in args.pop(list_spec.name):
            # the operation runs once per channel; each reply is templated in its channel's
            # context, then the replies join in list order
            result = self._run_op(comp_ref, op_name, unit, n, suffixes, args)
            duration += result.duration_ns
            response = result.response
            if response is not None and spec.reply is not None:
                response = self._reply(compiled, response, n, suffixes, result.fields)
            if unit.query and response is not None:
                responses.append(response)
        if not unit.query:
            return OpResult(duration_ns=duration)
        return OpResult(response=b",".join(responses), duration_ns=duration)

    def _run_op(
        self,
        comp_ref: str,
        op_name: str,
        unit: Unit,
        channel: int | None,
        suffixes: dict[str, int],
        args: dict[str, Any],
    ) -> OpResult:
        comp_name = self._resolve(comp_ref, channel)
        comp = self.components.get(comp_name)
        if comp is None:
            raise ScpiError(-300, f"component {comp_name!r} missing")
        fn = comp.operation(op_name)
        if fn is None:
            raise ScpiError(-300, f"operation {comp_ref}.{op_name!r} missing")
        # An argument written as $path is that setting's value (#252: the elements a FETCh? answers
        # follow :FORMat:ELEMents:SENSe); as {from: $path, map: {...}} it goes through the same
        # mapping a parameter or a wiring binding takes (#364: OUTR? reads the quantity DDEF picks).
        args = {
            k: (
                map_setting(v, self.state[self._resolve(str(v["from"])[1:], channel)])
                if isinstance(v, dict) and isinstance(v.get("from"), str)
                else self.state[self._resolve(v[1:], channel)]
                if isinstance(v, str) and v.startswith("$")
                # a list's $path items too (#383: SNAPD? reads the four data-channel assignments)
                else [
                    self.state[self._resolve(x[1:], channel)]
                    if isinstance(x, str) and x.startswith("$")
                    else x
                    for x in v
                ]
                if isinstance(v, list)
                else v
            )
            for k, v in args.items()
        }
        result = fn(query=unit.query, channel=channel, suffixes=suffixes, **args)
        return result if isinstance(result, OpResult) else OpResult()

    def _parse_params(self, specs: list[ParamSpec], raw: tuple[bytes, ...]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        i = 0
        for ps in specs:
            if ps.type == "channel_list":
                if i < len(raw):
                    numbers = V.parse_channel_list(raw[i], self.channel_exists, **self._list_rules(each=True))
                    i += 1
                elif ps.optional:
                    numbers = self._omitted_list(ps)
                else:
                    raise ScpiError(-109, f"missing {ps.name}")
                if ps.state is not None:
                    # A list with a setting is written whole into it (an int_list, e.g. the scan
                    # list) and the operation runs once over it (#300: MEAS:VOLT:DC? (@101:110)).
                    self.set_state(ps.state, list(numbers))
                    continue
                # Operations run once per channel with the channel index (as a <n> suffix does);
                # the list's numbers are the instrument's own (#207).
                out[ps.name] = [self.channel_index(n) for n in numbers]
                continue
            if ps.type == "float_list":
                out[ps.name] = V.parse_float_list(raw[i:], ps.unit) if i < len(raw) else []
                i = len(raw)
                continue
            if ps.type == "raw":
                out[ps.name] = list(raw[i:])
                i = len(raw)
                continue
            # a (@…) is this parameter's own value when it is one of its words (53230A (@2), #385)
            own = (
                ps.type == "enum"
                and i < len(raw)
                and _is_channel_list(raw[i])
                and _enum_hit(raw[i], ps.options)
            )
            if i >= len(raw) or (ps.optional and _is_channel_list(raw[i]) and not own):
                if not ps.optional:
                    raise ScpiError(-109, f"missing {ps.name}")
                out[ps.name] = None  # left out: a (@…) here belongs to a channel list (#300)
                continue
            r = raw[i]
            i += 1
            self._refuse_not_modeled(ps.not_modeled, r)
            try:
                if ps.type == "float":
                    # the state's words are the same on every channel: channel 1's spec tells
                    st = self.specs.get(self._resolve(ps.state, 1)) if ps.state is not None else None
                    if (ps.keywords or (st is not None and st.special)) and self._keyword_word(ps, st, r):
                        out[ps.name] = r  # a keyword word: the binding layer reads it per state (#354)
                    else:
                        v = V.parse_float(r, ps.unit, ps.min, ps.max)
                        if (ps.min is not None and v < ps.min) or (ps.max is not None and v > ps.max):
                            raise ScpiError(-222)
                        out[ps.name] = v
                elif ps.type == "int":
                    n = V.parse_int(r)
                    # declared bounds hold for an operation's integer as for its float (FETCh? 2
                    # on a meter that has only the primary reading)
                    if (ps.min is not None and n < ps.min) or (ps.max is not None and n > ps.max):
                        raise ScpiError(-222)
                    out[ps.name] = n
                elif ps.type == "bool":
                    out[ps.name] = V.parse_bool(r)
                elif ps.type == "enum":
                    out[ps.name] = V.parse_enum(r, ps.options or [])
                elif ps.type == "block":
                    out[ps.name] = V.parse_block(r)
                else:
                    out[ps.name] = V.parse_string(r)
            except ScpiError as e:
                raise self._bad_parameter(e, ps.name, ps) from e
        if i < len(raw):
            raise ScpiError(-108)
        return out

    # ---- IEEE 488.2 common commands and SYSTem ------------------------------------------------------

    @property
    def idn(self) -> str:
        d = self.definition
        return (self.instance.idn or d.identity.idn).format(
            manufacturer=d.manufacturer.upper(), model=d.model, serial=self.serial, firmware=self.firmware
        )

    def status_event(self, name: str) -> None:
        """A momentary event (a trigger, a range change) for the instrument's status registers:
        every ``status_register`` with a bit of that name latches it (#248, #273)."""
        for comp in self._status_registers():
            comp.event(name)  # type: ignore[attr-defined]

    def stb(self) -> int:
        stb = 0
        for comp in self._status_registers():  # status registers' summary bits (#248, #273)
            bit = comp.param("summary_bit")
            if bit is not None and comp.summary():  # type: ignore[attr-defined]
                stb |= 1 << int(bit)
        if len(self.errors):
            stb |= 1 << 2
        if self._output:
            stb |= 1 << 4
        if self.esr & self.ese:
            stb |= 1 << 5
        if stb & self.sre & ~(1 << 6):
            stb |= 1 << 6
        return stb

    def _common(self, unit: Unit) -> bytes | None:
        word = unit.tokens[0].keyword
        q = unit.query
        if word == "IDN" and q:
            return self.idn.encode()
        if word == "RST" and not q:
            self.reset()
            return None
        if word == "CLS" and not q:
            self.errors.clear()
            self.esr = 0
            for comp in self._status_registers():
                comp.clear()  # type: ignore[attr-defined]
            self._opc_armed = False
            return None
        if word == "OPC" and not q:
            self.arm_opc()
            return None
        if word == "ESR" and q:
            v, self.esr = self.esr, 0
            return str(v).encode()
        if word == "ESE":
            if q:
                return str(self.ese).encode()
            self.ese = V.parse_int(unit.params[0]) & 0xFF if unit.params else 0
            return None
        if word == "SRE":
            if q:
                return str(self.sre).encode()
            self.sre = V.parse_int(unit.params[0]) & 0xFF if unit.params else 0
            return None
        if word == "STB" and q:
            return str(self.stb()).encode()
        if word == "TST" and q:
            return b"0"
        raise self._undefined(unit, unit.tokens)

    def _bus_trigger(self, unit: Unit) -> tuple[bytes | None, int]:
        """``*TRG``: the bus trigger of every component with a ``bus_trigger`` operation (each
        channel's trigger model of a multi-channel SMU, #382). A component that refuses it (not
        waiting for a trigger) is left out while another one takes it; when none takes it, the
        first one's error stands — a single trigger model answers exactly as before."""
        fns = [fn for comp in self.components.values() if (fn := comp.operation("bus_trigger"))]
        if not fns:
            raise self._undefined(unit, unit.tokens)
        response: bytes | None = None
        duration = 0
        first_error: ScpiError | None = None
        taken = False
        for fn in fns:
            try:
                result = fn()
            except ScpiError as e:
                first_error = first_error or e
                continue
            taken = True
            response = response if response is not None else result.response
            duration = max(duration, result.duration_ns)
        if not taken and first_error is not None:
            raise first_error
        return response, duration

    def clock(self) -> dt.datetime:
        return self.session.calendar() + self.clock_offset

    def _builtin(self, unit: Unit, tokens: tuple[Token, ...]) -> bytes | None:
        """A built-in command's reply; ``_HANDLED`` for one without a reply, None when not built in."""
        err, count, all_, date, time_ = (p for _h, p, _q, _s in SYST_BUILTINS)
        if unit.query and err.match(tokens) is not None:
            return self._error_reply(self.errors.pop())
        if unit.query and count.match(tokens) is not None:
            return str(len(self.errors)).encode()
        if unit.query and all_.match(tokens) is not None:
            items = []
            while len(self.errors):
                items.append(self._error_reply(self.errors.pop()))
            return b",".join(items or [self._error_reply(ScpiError(0))])
        if date.match(tokens) is not None or time_.match(tokens) is not None:
            return self._clock_command(unit, date=date.match(tokens) is not None)
        return None

    def _clock_command(self, unit: Unit, *, date: bool) -> bytes:
        """:SYSTem:DATE yyyy,mm,dd and :SYSTem:TIME hh,mm,ss (IEEE 488.2 style), on the session calendar."""
        now = self.clock()
        if unit.query:
            if date:
                return f"{now.year:04d},{now.month:02d},{now.day:02d}".encode()
            return f"{now.hour:02d},{now.minute:02d},{now.second + now.microsecond / 1e6:06.3f}".encode()
        if len(unit.params) != 3:
            raise ScpiError(-109 if len(unit.params) < 3 else -108)
        try:
            a, b, c = (float(V.parse_float(p)) for p in unit.params)
            if date:
                target = now.replace(year=int(a), month=int(b), day=int(c))
            else:
                sec = int(c)
                target = now.replace(
                    hour=int(a), minute=int(b), second=sec, microsecond=round((c - sec) * 1e6)
                )
        except ValueError:
            raise ScpiError(-222) from None
        self.clock_offset += target - now
        return _HANDLED

    # ---- observability ------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.definition.model,
            "definition": self.definition.id,
            "idn": self.idn,
            "state": dict(self.state),
            "errors": [{"code": e.code, "message": e.message} for e in self.errors.peek_all()],
        }
