"""Instrument runtime: state, components, signal wiring and the SCPI command layer (architecture.md 3)."""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..models.definition import CommandSpec, InstrumentDefinition, ParamSpec, ResponseFormat, StateSpec
from ..scpi import values as V
from ..scpi.errors import ESR_BITS, ErrorQueue, ScpiError
from ..scpi.parse import Pattern, Token, Unit, parse_unit, split_units
from ..units import parse_number
from .component import Component, OpResult, component_type
from .signal import Signal

if TYPE_CHECKING:
    from ..models.bench import InstrumentInstance
    from .session import Session

_LADDERS = {"1-2-5": (1.0, 2.0, 5.0), "1-3": (1.0, 3.0), "1-2-4": (1.0, 2.0, 4.0), "1-10": (1.0,)}

log = logging.getLogger(__name__)
_HANDLED = b"\x00handled"  # a built-in command that sends no reply
#: Appended to -113 when a real model gets a command its definition does not have (D42).
NOT_MODELED = "not modeled by SimuBench"
#: Headers every instrument answers without a definition entry (coverage tables list them too).
BUILTIN_HEADERS = (
    "*IDN",
    "*RST",
    "*CLS",
    "*OPC",
    "*WAI",
    "*ESR",
    "*ESE",
    "*SRE",
    "*STB",
    "*TST",
    ":SYSTem:ERRor[:NEXT]",
    ":SYSTem:ERRor:COUNt",
    ":SYSTem:ERRor:ALL",
    ":SYSTem:DATE",
    ":SYSTem:TIME",
)


def _header_key(header: str) -> str:
    """A header as written in a definition, compared without case, spaces or the root colon."""
    return "".join(header.split()).lstrip(":").upper()


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
        self.errors = ErrorQueue(definition.error_queue or 20, definition.errors.get(-350))
        #: A real model (D42): follows its manual's error texts and refuses what it does not model.
        self.real = definition.reference is not None
        self.esr = 0
        self.ese = 0
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
        for comp_name, params in instance.hidden.items():
            if comp_name not in self.components:
                raise ValueError(f"instrument {name!r}: hidden state for unknown component {comp_name!r}")
            self.components[comp_name].overrides.update(params)

        # state path -> components bound to it
        self._watchers: dict[str, list[Component]] = defaultdict(list)
        for comp in self.components.values():
            for path in comp.bound_paths().values():
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

    # ---- construction helpers -------------------------------------------------------------------

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
        if spec.type == "enum":
            return V.parse_enum(str(value).encode(), spec.options or [])
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

    def upstream_signal(self, comp: Component, port: str) -> Signal:
        src = self._upstream.get((comp.name, port))
        if src is None:
            return Signal.zero()
        return self.session.cached_output(self.components[src[0]], src[1])

    # ---- state --------------------------------------------------------------------------------------

    def get_state(self, path: str) -> Any:
        try:
            return self.state[path]
        except KeyError:
            raise KeyError(f"instrument {self.name!r} has no state {path!r}") from None

    def set_state(self, path: str, value: Any, *, source: str = "scpi", internal: bool = False) -> None:
        """Set a state variable after validation; notify bound components; refuse on conflict.

        ``internal``: the instrument itself sets a read-only variable (e.g. calibration coefficients).
        """
        spec = self.specs.get(path)
        if spec is None:
            raise ScpiError(-113, f"no state {path!r}")
        value = self._validate(spec.model_copy(update={"readonly": False}) if internal else spec, value)
        old = self.state.get(path)
        if old == value:
            return
        self.state[path] = value
        try:
            for comp in self._watchers.get(path, ()):
                comp.on_state(path, old, value)
        except ScpiError:
            self.state[path] = old
            raise
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
            if math.isnan(v) or (spec.min is not None and v < spec.min * (1 - 1e-12) - 1e-300):
                raise ScpiError(-222)
            if spec.max is not None and v > spec.max * (1 + 1e-12) + 1e-300:
                raise ScpiError(-222)
            return round(v) if spec.type == "int" else v
        if spec.type == "enum":
            if value not in (spec.options or []):
                raise ScpiError(-224)
            return value
        if spec.type == "bool":
            return bool(value)
        return str(value)

    def reset(self) -> None:
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
            tokens = unit.tokens
            if not unit.common and not unit.absolute and path:
                tokens = path + tokens
            if not unit.common:
                path = tokens[:-1]
            try:
                resp, d = self._execute(unit, tokens)
                duration += d
                if resp is not None:
                    responses.append(resp)
            except ScpiError as e:
                self._push(e)
            except Exception as e:  # a model defect must not take the client's script down
                log.exception("%s: internal error executing %r", self.name, unit.raw_header)
                self._push(
                    ScpiError(-300, f"internal simulator error in {unit.raw_header}: {type(e).__name__}")
                )
        if responses:
            self._output.append(b";".join(responses) + b"\n")
        return duration

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
                err = ScpiError(err.code, err.detail, text=table if table is not None else err.standard_text)
        self.errors.push(err)
        self.esr |= 1 << ESR_BITS[err.esr_class]
        data: dict[str, Any] = {"code": err.code, "message": err.message}
        if err.text is not None and detail and detail not in err.message:
            data["message"] = f"{err.message} ({detail})"
        if (err.text is not None and err.text.endswith(NOT_MODELED)) or NOT_MODELED in detail:
            data["reason"] = "not_modeled"  # a command, or a parameter value, SimuBench does not model
        self.session.instrument_event(self, "error", data)

    def _builtin_header(self, tokens: tuple[Token, ...]) -> bool:
        """Whether the tokens name a built-in header this model is allowed to answer."""
        for header in BUILTIN_HEADERS:
            pattern = Pattern.compile(header)
            if not pattern.common and pattern.match(tokens) is not None:
                return self._builtin_allowed(header)
        return False

    def _error_reply(self, err: ScpiError) -> bytes:
        """An error queue entry as :SYSTem:ERRor? writes it, in the manual's form for a real model."""
        if err.code == 0 and 0 in self.definition.errors:
            err = ScpiError(0, text=self.definition.errors[0])
        sign = "+" if self.definition.format.error_code == "signed" and err.code >= 0 else ""
        return f'{sign}{err.code},"{err.message}"'.encode()

    def _undefined(self, header: str) -> ScpiError:
        """-113 for a header the definition does not have; a real model says it is not modeled.

        Raises :class:`_Ignored` instead when the definition ignores unknown commands.
        """
        if self.definition.unknown_commands == "ignore":
            raise _Ignored(header)
        if not self.real:
            return ScpiError(-113, header)
        base = self.definition.errors.get(-113, ScpiError(-113).standard_text)
        return ScpiError(-113, header, text=f"{base};{NOT_MODELED}")

    def push_error(self, code: int, detail: str = "") -> None:
        """Queue a device error from a component (e.g. an overload)."""
        self._push(ScpiError(code, detail))

    # ---- command execution --------------------------------------------------------------------------

    def _builtin_allowed(self, header: str) -> bool:
        return self._builtins is None or _header_key(header) in self._builtins

    def _execute(self, unit: Unit, tokens: tuple[Token, ...]) -> tuple[bytes | None, int]:
        try:
            return self._dispatch(unit, tokens)
        except _Ignored:
            pass
        # unknown_commands: ignore — swallowed without an error or a reply, but never silently (D16).
        self.session.instrument_event(
            self, "ignored", {"header": unit.raw_header, "query": unit.query, "reason": "unknown_command"}
        )
        return None, 0

    def _dispatch(self, unit: Unit, tokens: tuple[Token, ...]) -> tuple[bytes | None, int]:
        if unit.common:
            # A definition may give a common header its own behavior (e.g. a dialect's *CLS?).
            for compiled in self._commands:
                if compiled.pattern.common and compiled.pattern.match(tokens) is not None:
                    return self._run(compiled, unit, None, {})
            if not self._builtin_allowed(f"*{unit.tokens[0].keyword}"):
                raise self._undefined(unit.raw_header)
            return self._common(unit), 0
        for compiled in self._commands:
            if compiled.pattern.common:
                continue
            caps = compiled.pattern.match(tokens)
            if caps is None:
                continue
            channel = caps.get("ch")
            if "ch" in caps:
                channel = 1 if channel is None else channel
                if not 1 <= channel <= self.channel_count:
                    raise ScpiError(-114, f"channel {channel} does not exist")
            return self._run(compiled, unit, channel, {k: (1 if v is None else v) for k, v in caps.items()})
        builtin = (
            self._builtin(unit, tokens) if self._builtins is None or self._builtin_header(tokens) else None
        )
        if builtin is _HANDLED:
            return None, 0
        if builtin is not None:
            return builtin, 0
        raise self._undefined(unit.raw_header)

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
            result = self._op_command(spec, unit, channel, suffixes)
            response = result.response
            if response is not None and spec.reply is not None:  # an operation's reply in a template
                response = self._reply(compiled, response, channel, suffixes)
            return response, result.duration_ns + parse_duration(spec.duration)
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
        self, compiled: _Compiled, body: bytes, channel: int | None, suffixes: dict[str, int]
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

        def field(m: re.Match[str]) -> str:
            name = m.group(1)
            if name == "header":
                return header
            if name == "value":
                return body.decode(errors="replace")
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
                    v = (
                        st.min
                        if which.startswith("MIN")
                        else st.max
                        if which.startswith("MAX")
                        else st.default
                    )
                    return self._format(st, v, spec.format)
                raise ScpiError(-108)
            return self._format(st, self.state[path], spec.format)
        if not unit.params:
            raise ScpiError(-109)
        if len(unit.params) > 1 and st.type != "string":
            raise ScpiError(-108)
        value = self._parse_state(st, unit.params[0])
        self.set_state(path, value)
        return None

    def _settings_command(self, spec: CommandSpec, unit: Unit, channel: int | None) -> bytes | None:
        """A command whose parameters each write a setting (``TRSE EDGE,SR,C1``); all are checked
        before any is set. The query answers the settings in the same order and form."""
        if unit.query:
            if unit.params:
                raise ScpiError(-108)
            parts: list[str] = []
            for ps in spec.params:
                if ps.state is not None:
                    parts.append(self._format_path(ps.state, channel, spec))
                for key, ref in (ps.keys or {}).items():
                    if ref is not None:
                        parts += [key, self._format_path(ref, channel, spec)]
            return ",".join(parts).encode()
        raw = list(unit.params)
        new: dict[str, Any] = {}
        for ps in spec.params:
            if ps.state is not None:
                if not raw:
                    if ps.optional:
                        continue
                    raise ScpiError(-109, f"missing {ps.name}")
                path = self._resolve(ps.state, channel)
                new[path] = self._parse_state(self.specs[path], raw.pop(0))
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
                    raise ScpiError(-224, f"{unit.raw_header} parameter {name} not modeled by SimuBench")
                ref = keys[name]
                if ref is not None:
                    path = self._resolve(ref, channel)
                    new[path] = self._parse_state(self.specs[path], value_raw)
        if raw:
            raise ScpiError(-108)
        checked = {path: self._validate(self.specs[path], v) for path, v in new.items()}
        for path, v in checked.items():
            self.set_state(path, v)
        return None

    def _format_path(self, ref: str, channel: int | None, spec: CommandSpec) -> str:
        path = self._resolve(ref, channel)
        return self._format(self.specs[path], self.state[path], spec.format).decode()

    def parse_setting(self, path: str, raw: bytes) -> Any:
        """A parameter as the setting at ``path`` reads it (units, special words, enum forms)."""
        return self._parse_state(self.specs[path], raw)

    def format_setting(self, path: str) -> bytes:
        """The setting at ``path`` as the definition formats replies."""
        return self._format(self.specs[path], self.state[path])

    def _parse_state(self, st: StateSpec, raw: bytes) -> Any:
        if st.type in ("float", "int"):
            word = raw.decode(errors="replace").strip().upper()
            if st.special and word in st.special:
                return st.special[word]
            multipliers = self.definition.dialect.multipliers
            v = V.parse_multiplied(raw, multipliers) if multipliers and st.unit is None else None
            if v is None:
                v = V.parse_float(raw, st.unit, st.min, st.max, st.default)
            if st.ratio and v > 1:
                v /= 100.0  # a percentage
            return v
        if st.type == "bool":
            return V.parse_bool(raw)
        if st.type == "enum":
            return V.parse_enum(raw, st.options or [])
        return V.parse_string(raw)

    def _format(self, st: StateSpec, value: Any, override: ResponseFormat | None = None) -> bytes:
        f = override or self.definition.format
        if st.type == "float":
            if st.special and value in st.special.values() and not math.isfinite(value):
                return next(k for k, v in st.special.items() if v == value).encode()
            if f.number == "engineering":
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
            return V.format_enum(value, long=f.enum == "long", as_defined=f.enum == "as_defined")
        return V.format_string(str(value))

    def _op_command(
        self, spec: CommandSpec, unit: Unit, channel: int | None, suffixes: dict[str, int]
    ) -> OpResult:
        assert spec.op is not None
        comp_ref, op_name = spec.op.rsplit(".", 1)
        comp_name = self._resolve(comp_ref, channel)
        comp = self.components.get(comp_name)
        if comp is None:
            raise ScpiError(-300, f"component {comp_name!r} missing")
        fn = comp.operation(op_name)
        if fn is None:
            raise ScpiError(-300, f"operation {spec.op!r} missing")
        args = {**spec.args, **self._parse_params(spec.params, unit.params)}
        result = fn(query=unit.query, channel=channel, suffixes=suffixes, **args)
        return result if isinstance(result, OpResult) else OpResult()

    def _parse_params(self, specs: list[ParamSpec], raw: tuple[bytes, ...]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        i = 0
        for ps in specs:
            if ps.type == "float_list":
                out[ps.name] = V.parse_float_list(raw[i:], ps.unit) if i < len(raw) else []
                i = len(raw)
                continue
            if ps.type == "raw":
                out[ps.name] = list(raw[i:])
                i = len(raw)
                continue
            if i >= len(raw):
                if not ps.optional:
                    raise ScpiError(-109, f"missing {ps.name}")
                out[ps.name] = None
                continue
            r = raw[i]
            i += 1
            if ps.type == "float":
                v = V.parse_float(r, ps.unit, ps.min, ps.max)
                if (ps.min is not None and v < ps.min) or (ps.max is not None and v > ps.max):
                    raise ScpiError(-222)
                out[ps.name] = v
            elif ps.type == "int":
                out[ps.name] = V.parse_int(r)
            elif ps.type == "bool":
                out[ps.name] = V.parse_bool(r)
            elif ps.type == "enum":
                out[ps.name] = V.parse_enum(r, ps.options or [])
            elif ps.type == "block":
                out[ps.name] = V.parse_block(r)
            else:
                out[ps.name] = V.parse_string(r)
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

    def stb(self) -> int:
        stb = 0
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
            return None
        if word == "OPC":
            if q:
                return b"1"
            self.esr |= 1
            return None
        if word == "WAI" and not q:
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
        raise self._undefined(unit.raw_header)

    _SYST_ERR = Pattern.compile(":SYSTem:ERRor[:NEXT]")
    _SYST_ERR_COUNT = Pattern.compile(":SYSTem:ERRor:COUNt")
    _SYST_ERR_ALL = Pattern.compile(":SYSTem:ERRor:ALL")
    _SYST_DATE = Pattern.compile(":SYSTem:DATE")
    _SYST_TIME = Pattern.compile(":SYSTem:TIME")

    def clock(self) -> dt.datetime:
        return self.session.calendar() + self.clock_offset

    def _builtin(self, unit: Unit, tokens: tuple[Token, ...]) -> bytes | None:
        """A built-in command's reply; ``_HANDLED`` for one without a reply, None when not built in."""
        if unit.query and self._SYST_ERR.match(tokens) is not None:
            return self._error_reply(self.errors.pop())
        if unit.query and self._SYST_ERR_COUNT.match(tokens) is not None:
            return str(len(self.errors)).encode()
        if unit.query and self._SYST_ERR_ALL.match(tokens) is not None:
            items = []
            while len(self.errors):
                items.append(self._error_reply(self.errors.pop()))
            return b",".join(items or [self._error_reply(ScpiError(0))])
        if self._SYST_DATE.match(tokens) is not None or self._SYST_TIME.match(tokens) is not None:
            return self._clock_command(unit, date=self._SYST_DATE.match(tokens) is not None)
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
