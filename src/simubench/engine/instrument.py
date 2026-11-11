"""Instrument runtime: state, components, signal wiring and the SCPI command layer (architecture.md 3)."""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..models.bench import LIVE_CLOCKS
from ..models.definition import InstrumentDefinition, StateSpec
from ..scpi.errors import ErrorQueue, ScpiError
from ..scpi.parse import Pattern, Token, Unit, parse_unit, resolve_tokens, split_units
from .builtin_commands import BuiltinCommands
from .channels import ChannelMap
from .command_runner import (
    CommandRunner,
    _Compiled,
)
from .commands import (
    _header_key,
)
from .component import Component, component_type, noise_problems
from .coverage_advice import CoverageAdvisor
from .error_reporter import ErrorReporter, _Ignored
from .portref import split
from .settings import SettingStore, validate_value
from .wiring import SignalWiring

if TYPE_CHECKING:
    from ..models.bench import InstrumentInstance
    from .session import Session

log = logging.getLogger(__name__)
#: Appended to -113 when a real model gets a command its definition does not have (D42).


def _port_target(instrument: str, pname: str, target: str) -> tuple[str, str]:
    """An external port's target halves; a dotless target is refused with the target echoed."""
    comp, port = split(target)
    if not comp:
        raise ValueError(
            f"instrument {instrument!r}: port {pname!r} target {target!r} is not <component>.<port>"
        )
    return comp, port


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
        self.runner = CommandRunner(self)
        self.errs = ErrorReporter(self)
        self.common = BuiltinCommands(self)
        self.channel_count = definition.channels.count
        self.channel_map = ChannelMap(definition)
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
        #: Components' status flags while a status register computes its condition: the outermost
        #: register fills it, the registers it asks reuse it (#602: without it every register
        #: recomputed every other one's flags, n! scans of a scope's trigger per command).
        self.status_flag_cache: dict[str, dict[str, bool]] | None = None
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

        self._init_state(definition, instance)
        self._init_components(definition, instance)
        self._init_watchers()
        self.wiring = SignalWiring(definition, self.channel_count, self.components, self.name)
        self._init_ports(definition)
        self._init_commands(definition)

    # ---- construction helpers -------------------------------------------------------------------

    def _init_state(self, definition: InstrumentDefinition, instance: InstrumentInstance) -> None:
        """Settings: the definition's, per channel with the bench's overrides, then the bench file's initial values."""
        self.settings = SettingStore(definition, self.channel_count, self.name)
        self.settings.reset(power_on=True)
        for path, value in instance.state.items():
            if path not in self.specs:
                raise ValueError(f"instrument {self.name!r}: unknown state {path!r} in bench file")
            self.state[path] = self.settings.coerce(path, value)

    def _init_components(self, definition: InstrumentDefinition, instance: InstrumentInstance) -> None:
        """Components, the bench's hidden state, and this unit's drawn deviations."""
        self.components: dict[str, Component] = {}
        for spec in definition.components:
            self._add_component(spec.name, spec.type, spec.params, None)
        for n in range(1, self.channel_count + 1):
            for spec in definition.channel_components:
                self._add_component(f"ch{n}.{spec.name}", spec.type, spec.params, n)
        #: Components that look again for the event they wait for whenever a command arrives.
        self._retrying = [c for c in self.components.values() if c.has_retry_pending]
        for comp_name, params in instance.hidden.items():
            if comp_name not in self.components:
                options = ", ".join(sorted(self.components))
                raise ValueError(
                    f"instrument {self.name!r}: hidden state for unknown component {comp_name!r} "
                    f"(components: {options})"
                )
            component = self.components[comp_name]
            for param_name in params:
                if param_name not in component._params:
                    options = ", ".join(sorted(component._params))
                    raise ValueError(
                        f"instrument {self.name!r}: hidden state for unknown parameter "
                        f"{comp_name}.{param_name} (parameters: {options})"
                    )
            component.overrides.update(params)
        #: This unit's drawn deviations, '<component>.<parameter>' -> value (empty when nominal).
        self.deviations: dict[str, Any] = {}
        if instance.deviation == "from_specs":
            self._draw_deviations()

    def _init_watchers(self) -> None:
        """Which components are told when a setting changes."""
        # state path -> components bound to it
        self._watchers: dict[str, list[Component]] = defaultdict(list)
        # state path -> {component: the parameter the change arrives through} for ``on_state``
        # (None: through ``watches`` or a nested mapping; the first binding wins when several of a
        # component's parameters bind the path, #436)
        self._watch_param: dict[str, dict[str, str | None]] = defaultdict(dict)
        for comp in self.components.values():
            for key, path in comp.bound_paths().items():
                # once per component, however many of its parameters bind the path (#436: a load
                # setting bound both as the level's load and as its range table's key)
                if comp not in self._watchers[path]:
                    self._watchers[path].append(comp)
                self._watch_param[path].setdefault(comp.uid, key)
            for path in comp.watched_paths():
                # the settings inside a nested mapping too (the 8588A's input impedance under the
                # function, #340)
                if path in self.specs and comp not in self._watchers[path]:
                    self._watchers[path].append(comp)
                    self._watch_param[path].setdefault(comp.uid, None)
            for path in comp.watches:  # read by path, still told of changes (#274)
                if path in self.specs and comp not in self._watchers[path]:
                    self._watchers[path].append(comp)
                    self._watch_param[path].setdefault(comp.uid, None)

    def _init_ports(self, definition: InstrumentDefinition) -> None:
        """External ports: name -> (component, port)."""
        self.ports: dict[str, tuple[str, str]] = {}
        for n in range(1, self.channel_count + 1):
            for pname, target in definition.ports.channel.items():
                comp, port = _port_target(self.name, pname, target)
                self.ports[pname.replace("{n}", str(n))] = (f"ch{n}.{comp}", port)
        for pname, target in definition.ports.instrument.items():
            comp, port = _port_target(self.name, pname, target)
            self.ports[pname] = (comp, port)

    def _init_commands(self, definition: InstrumentDefinition) -> None:
        """The compiled commands, the built-ins this model answers, and the declarations to cite for an unexecuted command."""
        self._commands = [_Compiled(Pattern.compile(c.header), c) for c in definition.commands]
        #: Built-in headers this model answers; a real model's manual may document only a few (D42).
        allowed = definition.builtins
        self._builtins = None if allowed is None else {_header_key(h) for h in allowed}
        #: The definition file, as the loader received the path; origin of the definition's own words.
        self.source_file: str | None = str(definition._source_file) if definition._source_file else None
        #: The coverage table and the -113 classification built on it (#895).
        self.coverage = CoverageAdvisor(
            definition,
            name=self.name,
            real=self.real,
            source_file=self.source_file,
            commands=lambda: ((c.pattern, c.spec.header) for c in self._commands),
        )

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
        if "noise" in cls.defaults and (problems := noise_problems(params.get("noise"))):
            raise ValueError(
                f"instrument {self.name!r}: {name}.noise "
                + "; ".join(f"{(sub + ': ') if sub else ''}{msg}" for sub, msg in problems)
            )
        self.components[name] = cls(name, params, self, self.session, channel)

    @property
    def specs(self) -> dict[str, StateSpec]:
        return self.settings.specs

    @property
    def state(self) -> dict[str, Any]:
        return self.settings.values

    @state.setter
    def state(self, values: dict[str, Any]) -> None:
        self.settings.values = values

    # ---- signal flow ------------------------------------------------------------------------------

    # ---- state --------------------------------------------------------------------------------------

    def get_state(self, path: str) -> Any:
        return self.settings.get(path)

    def set_state(self, path: str, value: Any, *, source: str = "scpi", internal: bool = False) -> None:
        """Set a state variable after validation; notify bound components; refuse on conflict.

        ``internal``: the instrument itself sets a read-only variable (e.g. calibration coefficients).
        A watcher may write other settings as a side effect (a unit switch converts the value); when
        any watcher refuses, everything the watchers wrote is rolled back with the refused value.
        """
        spec = self.specs.get(path)
        if spec is None:
            raise ScpiError(-113, f"no state {path!r}")
        value = validate_value(spec.model_copy(update={"readonly": False}) if internal else spec, value)
        old = self.state.get(path)
        if old == value:
            return
        before = dict(self.state)
        self.state[path] = value
        try:
            for comp in self._watchers.get(path, ()):
                # A component may take a value outside its legal range as the nearest legal one
                # (it returns that value) instead of refusing it.
                adjusted = comp.on_state(self._watch_param.get(path, {}).get(comp.uid), path, old, value)
                if adjusted is not None and adjusted != value:
                    value = validate_value(spec.model_copy(update={"readonly": False}), adjusted)
                    self.state[path] = value
        except ScpiError:
            self.state.update(before)
            raise
        if old == value:
            return
        self.session.state_changed(self, path, old, value, electrical=spec.electrical, source=source)

    # ---- operations that finish later --------------------------------------------------------------

    # ---- command execution lives in :class:`CommandRunner` (#895) -------------------------------------

    def format_number(self, value: float) -> bytes:
        """A number in the definition's response format (a reading, a measured value)."""
        return self.runner.format_number(value)

    # ---- BuiltinCommands (#895) ----------------------------------------

    @property
    def idn(self) -> str:
        return self.common.idn

    def stb(self, commit: bool = True) -> int:
        """The status byte (see :meth:`BuiltinCommands.stb`)."""
        return self.common.stb(commit=commit)

    def status_event(self, name: str) -> None:
        """Latch a momentary event into the status registers (see :meth:`BuiltinCommands.status_event`)."""
        self.common.status_event(name)

    # ---- ErrorReporter (#895) ----------------------------------------

    def push_error(self, *args: Any, **kwargs: Any) -> Any:
        return self.errs.push_error(*args, **kwargs)

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
        if self.session.clock_mode in LIVE_CLOCKS:
            time.sleep(min(remaining / 1e9 / self.session.time_scale, 5.0))
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
        self.settings.reset()
        for comp in self.components.values():
            comp.reset()
        self.session.state_changed(self, "*RST", None, None, electrical=True, source="scpi")

    # ---- message exchange ---------------------------------------------------------------------------

    def receive(self, message: bytes) -> int:
        """Execute a program message; queue responses. Returns the virtual time consumed (ns)."""
        if self._output:
            self._output.clear()
            self.errs._push(ScpiError(-410))
        responses: list[bytes] = []
        duration = 0
        self._elapsed_ns = 0
        path: tuple[Token, ...] = ()
        try:
            units = split_units(message)
        except ScpiError as e:
            self.errs._push(e)
            return 0
        for raw in units:
            try:
                unit = parse_unit(raw)
            except ScpiError as e:
                self.errs._push(e)
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
                self.errs._push(self.errs._parameter_error(e))
            except Exception as e:  # a model defect must not take the client's script down
                log.exception("%s: internal error executing %r", self.name, unit.raw_header)
                self.errs._push(
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

    # ---- command execution --------------------------------------------------------------------------

    def _execute(self, unit: Unit, tokens: tuple[Token, ...]) -> tuple[bytes | None, int]:
        try:
            return self.runner._dispatch(unit, tokens)
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

    # ---- replies: the dialect's header echo and the command's template ------------------------------

    # ---- settings --------------------------------------------------------------------------------------

    # ---- IEEE 488.2 common commands and SYSTem ------------------------------------------------------

    def clock(self) -> dt.datetime:
        return self.session.calendar() + self.clock_offset

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
