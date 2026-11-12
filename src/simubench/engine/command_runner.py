"""Instrument runtime: state, components, signal wiring and the SCPI command layer (architecture.md 3)."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..models.definition import CommandSpec, ParamSpec, ResponseFormat, StateSpec
from ..scpi import values as V
from ..scpi.errors import ScpiError
from ..scpi.parse import Pattern, Token, Unit
from ..units import parse_number, parse_quantity
from .commands import (
    COMMON_BUILTINS,
    COMMON_DIRECTIONS,
    named_builtin,
    when_matches,
)
from .component import OpResult, map_setting
from .settings import validate_value

if TYPE_CHECKING:
    from .instrument import Instrument


NOT_MODELED = "not implemented in this definition"

_HANDLED = b"\x00handled"  # a built-in command that sends no reply


@dataclass
class _Compiled:
    pattern: Pattern
    spec: CommandSpec


def _enum_hit(raw: bytes, options: list[str] | None) -> bool:
    """Whether a data element spells one of an enum parameter's options (spaces and case aside)."""
    text = b"".join(raw.split()).decode("ascii", "replace").upper()
    return any("".join(o.split()).upper() == text for o in options or [])


def _is_channel_list(raw: bytes) -> bool:
    """A program data element that is a channel list ``(@…)``."""
    return raw.lstrip().startswith(b"(@")


def parse_duration(d: str | float | None) -> int:
    if d is None:
        return 0
    if isinstance(d, (int, float)):
        return round(float(d) * 1e9)
    return round(parse_number(d, "S") * 1e9)


def _at(index: dict[str, int], ref: str) -> str:
    """``ref`` with each ``{name}`` replaced by that index parameter's value."""
    for name, value in index.items():
        ref = ref.replace("{" + name + "}", str(value))
    return ref


class CommandRunner:
    """Parses and executes the program messages of one instrument (#895): the dispatch of a unit to a
    command, the setting / channel-list / quantities / operation commands, and the parsing and
    formatting of values. It acts on the instrument it is given; the instrument keeps the state."""

    def __init__(self, inst: Instrument) -> None:
        self.inst = inst

    def _dispatch(self, unit: Unit, tokens: tuple[Token, ...]) -> tuple[bytes | None, int]:
        self.inst.settle_pending()
        self.inst.sample_status()
        if unit.common:
            # A definition may give a common header its own behavior (e.g. a dialect's *CLS?).
            for compiled in self.inst._commands:
                if compiled.pattern.common and compiled.pattern.match(tokens) is not None:
                    return self._run(compiled, unit, None, {})
            word = unit.tokens[0].keyword
            if not self.inst.common._builtin_allowed(f"*{word}"):
                raise self.inst.errs._undefined(unit, tokens, builtin=f"*{word}" in COMMON_BUILTINS)
            q_ok, s_ok = COMMON_DIRECTIONS.get(word, (True, True))
            if (unit.query and not q_ok) or (not unit.query and not s_ok):
                raise self.inst.errs._undefined(unit, tokens)
            if word == "OPC" and unit.query:
                return self.inst.opc_query()
            if word == "WAI":
                return None, self.inst.wait_pending()
            if word == "TRG" and not unit.query:
                return self.inst.common._bus_trigger(unit)
            return self.inst.common._common(unit), 0
        refused: CommandSpec | None = None  # a header that matched, with other parameter values
        missing: str | None = None  # a header that matched, of an option this unit does not have
        for compiled in self.inst._commands:
            if compiled.pattern.common:
                continue
            caps = compiled.pattern.match(tokens)
            if caps is None:
                continue
            if compiled.spec.option and compiled.spec.option not in self.inst.options:
                missing = compiled.spec.option  # a later command on the header may answer instead
                continue
            if compiled.spec.when and not when_matches(compiled.spec, unit):
                refused = compiled.spec
                continue
            if missing is not None and not (compiled.spec.query if unit.query else compiled.spec.set):
                continue  # the fallback answers the other direction only: the option's is missing
            channel = caps.get("ch")
            if "ch" in caps and channel is not None:
                channel = self.inst.channel_map.index(channel)
            elif "ch" in caps:
                channel = 1  # no suffix written: the first channel
            suffixes = {k: (1 if v is None else v) for k, v in caps.items()}
            for name, allowed in (compiled.spec.suffixes or {}).items():
                if suffixes.get(name, 1) not in allowed:
                    raise ScpiError(-114, f"{unit.raw_header}: suffix {suffixes.get(name)} is not {allowed}")
            if "ch" in caps and caps["ch"] is None:
                suffixes["ch"] = self.inst.channel_map.number(1)  # the default channel's own number
            return self._run(compiled, unit, channel, suffixes)
        if refused is not None:
            raise ScpiError(-224, f"{unit.raw_header}: parameter not implemented in this definition")
        if missing is not None:
            raise ScpiError(-241, f"{unit.raw_header}: option {missing} is not installed")
        named = named_builtin(tokens)
        if named is not None and not self.inst.common._builtin_allowed(named):
            raise self.inst.errs._undefined(
                unit, tokens, builtin=True
            )  # the definition's builtins leave it out
        builtin = (
            self.inst.common._builtin(unit, tokens)
            if self.inst._builtins is None or named is not None
            else None
        )
        if builtin is _HANDLED:
            return None, 0
        if builtin is not None:
            return builtin, 0
        raise self.inst.errs._undefined(unit, tokens)

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
                body = self._format(self.inst.specs[path], self.inst.state[path], spec.format)
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

    def _reply(
        self,
        compiled: _Compiled,
        body: bytes,
        channel: int | None,
        suffixes: dict[str, int],
        fields: dict[str, str] | None = None,
    ) -> bytes:
        template = compiled.spec.reply
        mode = self.inst.common.echo_mode()
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
            return self._format(self.inst.specs[path], self.inst.state[path], compiled.spec.format).decode()

        return re.sub(r"\{([^{}]+)\}", field, template).encode()

    def _state_command(self, spec: CommandSpec, unit: Unit, channel: int | None) -> bytes | None:
        assert spec.state is not None
        path = self._resolve(spec.state, channel)
        st = self.inst.specs[path]
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
                    lo, hi = self.inst.limits(path)
                    v = lo if which.startswith("MIN") else hi if which.startswith("MAX") else st.default
                    return self._format(st, v * spec.factor if v is not None else v, spec.format)
                raise ScpiError(-108)
            value = self.inst.state[path]
            return self._format(st, value * spec.factor if spec.factor != 1 else value, spec.format)
        if not unit.params:
            if spec.set_value is None:
                raise ScpiError(-109)
            self.inst.set_state(path, spec.set_value)
            return None
        if st.type == "enum_list":
            try:
                self.inst.set_state(path, [V.parse_enum(p, st.options or []) for p in unit.params])
            except ScpiError as e:
                raise self.inst.errs._bad_parameter(e, path, st) from e
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
            self.inst.set_state(path, value)
        except ScpiError as e:
            raise self.inst.errs._bad_parameter(e, path, st) from e
        return None

    def _check_all(self, writes: Iterable[tuple[str, Any]]) -> dict[str, Any]:
        """Validate every staged write against its setting's declaration; raise, with the setting
        named, before any of them is applied. Later entries for the same path overwrite earlier
        ones, so a stage can put the command's constants first and let the arguments win."""
        checked: dict[str, Any] = {}
        for path, v in writes:
            try:
                checked[path] = validate_value(self.inst.specs[path], v)
            except ScpiError as e:
                raise self.inst.errs._bad_parameter(e, path, self.inst.specs[path]) from e
        return checked

    def _commit(self, checked: dict[str, Any]) -> None:
        """Apply the checked writes all-or-nothing (#550): a write a component refuses — because
        the settings together make no sense (#580) — takes back the ones before it. After the
        writes, the watchers of every written path may check the combination and refuse too."""
        done: list[tuple[str, Any]] = []
        try:
            for path, v in checked.items():
                before = self.inst.state[path]
                self.inst.set_state(path, v)
                done.append((path, before))
            for comp in {c.uid: c for path in checked for c in self.inst._watchers.get(path, ())}.values():
                comp.check_settings()
        except ScpiError:
            for path, before in reversed(done):
                self.inst.set_state(path, before)
            raise

    def _settings_command(self, spec: CommandSpec, unit: Unit, channel: int | None) -> bytes | None:
        """A command whose parameters each write a setting (``TRSE EDGE,SR,C1``); all are checked
        before any is set. The query answers the settings in the same order and form."""
        raw = list(unit.params)
        index = self._pick_indexes(spec, unit, raw, channel)
        if unit.query:
            if raw:
                raise ScpiError(-108)
            return self._settings_reply(spec, index, channel)
        new = self._parse_settings(spec, unit, raw, index, channel)
        if raw:
            raise ScpiError(-108)
        # The command's constants first: the function it selects applies to the parameters after it.
        staged: list[tuple[str, Any]] = [
            (self._resolve(_at(index, ref), channel), value) for ref, value in spec.writes.items()
        ]
        staged += list(new.items())
        self._commit(self._check_all(staged))
        return None

    def _pick_indexes(
        self, spec: CommandSpec, unit: Unit, raw: list[bytes], channel: int | None
    ) -> dict[str, int]:
        """The values of the index parameters, taken out of ``raw``. They pick the settings: a query
        carries them alone, in order; a set carries each at its own position (VIEW CHAN2;
        TRIG:LEV 0.5,CHAN2; PACU 1,AMPL,C1)."""
        index: dict[str, int] = {}
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
                raise self.inst.errs._bad_parameter(
                    ScpiError(-222, f"{ps.name} {value} out of range"), ps.name, ps
                )
            index[ps.name] = value
        return index

    def _settings_reply(self, spec: CommandSpec, index: dict[str, int], channel: int | None) -> bytes:
        """The query answer: each parameter's setting, in the command's order and form."""
        parts: list[str] = []
        for ps in spec.params:
            if ps.index and ps.echo:
                parts.append(self._index_text(ps, index[ps.name]))
            if ps.state is not None:
                parts.append(self._format_path(_at(index, ps.state), channel, spec))
            for key, ref in (ps.keys or {}).items():
                if ref is not None:
                    parts += [key, self._format_path(_at(index, ref), channel, spec)]
        while parts and parts[-1] in ("", '""'):  # an optional value not set: left out
            parts.pop()
        return ",".join(parts).encode()

    def _parse_settings(
        self, spec: CommandSpec, unit: Unit, raw: list[bytes], index: dict[str, int], channel: int | None
    ) -> dict[str, Any]:
        """The settings a set writes, parsed from ``raw`` (consumed); nothing is written here."""
        new: dict[str, Any] = {}
        for ps in spec.params:
            if ps.index:
                continue
            if ps.state is not None:
                if not raw:
                    if ps.optional:
                        continue
                    raise ScpiError(-109, f"missing {ps.name}")
                path = self._resolve(_at(index, ps.state), channel)
                token = raw.pop(0)
                instead = self._keyword_writes(ps, token)
                if instead is not None:
                    # A keyword with its own settings (#344: resolution MIN picks the finest NPLC).
                    for ref, constant in instead.items():
                        new[self._resolve(_at(index, ref), channel)] = constant
                    continue
                try:
                    value = self._parse_state(self.inst.specs[path], token, path)
                except ScpiError as e:
                    raise self.inst.errs._bad_parameter(e, path, self.inst.specs[path]) from e
                # `sets` goes with an explicit value (a number, MIN, MAX): a special word of the
                # setting (AUTO) or DEF keeps what the command's `writes` chose (#325:
                # CONF:VOLT:DC AUTO leaves autorange on).
                if not self._keyword_argument(self.inst.specs[path], token):
                    for ref, constant in (ps.sets or {}).items():
                        new[self._resolve(_at(index, ref), channel)] = constant
                new[path] = value
                continue
            items = raw[:]
            raw.clear()  # the name,value pairs take every parameter that is left
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
                    path = self._resolve(_at(index, ref), channel)
                    new[path] = self._parse_state(self.inst.specs[path], value_raw, path)
        return new

    def _format_path(self, ref: str, channel: int | None, spec: CommandSpec) -> str:
        path = self._resolve(ref, channel)
        return self._format(self.inst.specs[path], self.inst.state[path], spec.format).decode()

    def _channel_list_command(self, spec: CommandSpec, unit: Unit, channel: int | None) -> bytes | None:
        """A command with a channel list parameter (``VOLT 5,(@1:3)``) expands over the channels:
        a write happens once per channel (each channel's own range applies), a query answers the
        channels in list order, comma-joined. A query may lead with MIN/MAX/DEF
        (``VOLT? MAX,(@1)``) and answers each channel's range limit or default, like the plain
        state query. The command's parameters carry the value(s); the list itself writes no
        setting."""
        raw = list(unit.params)
        form, channels, values, whole = self._channel_list_args(spec, unit, raw)
        if raw:
            raise ScpiError(-108)
        if unit.query:
            return self._channel_list_reply(spec, channels, form)
        self._commit(self._check_all(self._channel_list_writes(spec, channels, values, whole)))
        return None

    def _channel_list_args(
        self, spec: CommandSpec, unit: Unit, raw: list[bytes]
    ) -> tuple[str | None, list[int], list[tuple[ParamSpec, bytes]], tuple[str, list[int]] | None]:
        """The MIN/MAX/DEF form word, the channel indexes, the (parameter, value) pairs and the
        channel list a set writes whole; ``raw`` is consumed."""
        form: str | None = None
        channels: list[int] = []
        values: list[tuple[ParamSpec, bytes]] = []
        whole: tuple[str, list[int]] | None = None
        for ps in spec.params:
            if ps.type == "channel_list":
                if raw:
                    numbers = V.parse_channel_list(
                        raw.pop(0), self.inst.channel_map.exists, **self._list_rules(each=True)
                    )
                elif ps.optional and unit.query and form is not None:
                    # NPLC? MAX: a limit asked without a list is answered once (#423); the
                    # limits are the same declaration for every channel
                    numbers = [self.inst.channel_map.number(1)]
                elif ps.optional:
                    numbers = self._omitted_list(ps)
                else:
                    raise ScpiError(-109, f"missing {ps.name}")
                channels = [self.inst.channel_map.index(n) for n in numbers]
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
        return form, channels, values, whole

    def _channel_list_reply(self, spec: CommandSpec, channels: list[int], form: str | None) -> bytes:
        """The query answer: each channel's setting (or, with a form word, its limit or default), in
        list order, comma-joined."""
        parts = []
        for n in channels:
            for ps in spec.params:
                if ps.type == "channel_list" or ps.state is None:
                    continue
                path = self._resolve(ps.state, n)
                st = self.inst.specs[path]
                if form is not None and st.type in ("float", "int"):
                    v = st.min if form.startswith("MIN") else st.max if form.startswith("MAX") else st.default
                    parts.append(self._format(st, v, spec.format).decode())
                else:
                    parts.append(self._format(st, self.inst.state[path], spec.format).decode())
        return ",".join(parts).encode()

    def _channel_list_writes(
        self,
        spec: CommandSpec,
        channels: list[int],
        values: list[tuple[ParamSpec, bytes]],
        whole: tuple[str, list[int]] | None,
    ) -> list[tuple[str, Any]]:
        """The settings a set writes, per channel."""
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
                staged.append((path, self._parse_state(self.inst.specs[path], r, path)))
                if ps.sets and not self._keyword_argument(self.inst.specs[path], r):
                    for ref, value in ps.sets.items():
                        staged.append((self._resolve(ref, n), value))
        if whole is not None:
            staged.append((whole[0], whole[1]))
        return staged

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
            raise self.inst.errs._bad_parameter(e, ps.name, ps) from e
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
            current = writes.get(path, self.inst.state.get(path))
            return value["then" if current == 0 else "else"]
        return value

    def _index_value(self, ps: ParamSpec, raw: Any, channel: int | None) -> int:
        """An index parameter's number: an int, an index word (``CHANnel2`` -> 2), or for a
        left-out one its ``index_default`` (a number or a $setting holding a word or number)."""
        if isinstance(raw, str) and raw.startswith("$"):
            raw = self.inst.state[self._resolve(raw[1:], channel)]
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
        selected = self.inst.definition.channels.selected
        if selected is not None:
            return [int(self.inst.state[selected])]
        if isinstance(ps.default, str):
            # a setting holding the list (#423: the DAQ970A's omitted list is the scan list)
            numbers = [int(n) for n in self.inst.state[ps.default.removeprefix("$")] or []]
            if not numbers:
                raise ScpiError(-221, f"no channel list given and {ps.default[1:]} is empty")
            return numbers
        return ps.default or [self.inst.channel_map.number(1)]

    def _list_rules(self, each: bool = False) -> dict[str, bool]:
        """The definition's channel list rules (#260, #418): descending ranges, repeated channels,
        an empty list. ``each``: a list a command runs over channel by channel (a write, a query,
        an operation such as MEAS? (@…)) — an empty one there is still -108, so a query is never
        silently left without an answer; ``empty`` admits ``(@)`` only where a list setting is
        written whole (ROUT:SCAN (@) clears the scan list, #420)."""
        rules = self.inst.definition.channels.lists
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
                value = st.special[word]
                if word.startswith(("MIN", "MAX", "DEF")) and path is not None:
                    # MIN/MAX are the setting's legal extremes *now*, and DEF stays legal too: a
                    # component narrowing the range (#437) narrows all three with it — the static
                    # table is only the fallback (issue 663: a load's slew follows its selected
                    # current range; issue 721: DEF clamps into the narrowed range like MIN/MAX).
                    lo, hi = self.inst.limits(path)
                    if word.startswith("MIN"):
                        value = lo
                    elif word.startswith("MAX"):
                        value = hi
                    else:
                        if lo is not None:
                            value = max(value, lo)
                        if hi is not None:
                            value = min(value, hi)
                return value
            multipliers = self.inst.definition.dialect.multipliers
            v = V.parse_multiplied(raw, multipliers) if multipliers and st.unit is None else None
            if v is None:
                lo, hi = self.inst.limits(path) if path is not None else (st.min, st.max)
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
                raw,
                self.inst.channel_map.exists if self.inst.channel_map.by_number else None,
                **self._list_rules(),
            )
        return V.parse_string(raw)

    def _format(self, st: StateSpec, value: Any, override: ResponseFormat | None = None) -> bytes:
        f = override or self.inst.definition.format
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
            long = f.enum == "long" or self.inst.common.long_form()
            return V.format_enum(value, long=long, as_defined=f.enum == "as_defined")
        if st.type == "enum_list":
            long = f.enum == "long" or self.inst.common.long_form()
            return b",".join(V.format_enum(v, long=long, as_defined=f.enum == "as_defined") for v in value)
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
            self.inst.channel_map.index(n)
            for ps in spec.params
            if ps.type == "channel_list" and ps.state is not None
            for n in self.inst.state[ps.state]
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
                        st = self.inst.specs[path]
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
        comp = self.inst.components.get(comp_name)
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
                map_setting(v, self.inst.state[self._resolve(str(v["from"])[1:], channel)])
                if isinstance(v, dict) and isinstance(v.get("from"), str)
                else self.inst.state[self._resolve(v[1:], channel)]
                if isinstance(v, str) and v.startswith("$")
                # a list's $path items too (#383: SNAPD? reads the four data-channel assignments)
                else [
                    self.inst.state[self._resolve(x[1:], channel)]
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
                    numbers = V.parse_channel_list(
                        raw[i], self.inst.channel_map.exists, **self._list_rules(each=True)
                    )
                    i += 1
                elif ps.optional:
                    numbers = self._omitted_list(ps)
                else:
                    raise ScpiError(-109, f"missing {ps.name}")
                if ps.state is not None:
                    # A list with a setting is written whole into it (an int_list, e.g. the scan
                    # list) and the operation runs once over it (#300: MEAS:VOLT:DC? (@101:110)).
                    self.inst.set_state(ps.state, list(numbers))
                    continue
                # Operations run once per channel with the channel index (as a <n> suffix does);
                # the list's numbers are the instrument's own (#207).
                out[ps.name] = [self.inst.channel_map.index(n) for n in numbers]
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
                    st = self.inst.specs.get(self._resolve(ps.state, 1)) if ps.state is not None else None
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
                raise self.inst.errs._bad_parameter(e, ps.name, ps) from e
        if i < len(raw):
            raise ScpiError(-108)
        return out

    def parse_setting(self, path: str, raw: bytes) -> Any:
        """A parameter as the setting at ``path`` reads it (units, special words, enum forms)."""
        return self._parse_state(self.inst.specs[path], raw, path)

    def format_setting(self, path: str) -> bytes:
        """The setting at ``path`` as the definition formats replies."""
        return self._format(self.inst.specs[path], self.inst.state[path])

    def format_number(self, value: float) -> bytes:
        """A number in the definition's response format (a reading, a measured value)."""
        f = self.inst.definition.format
        if f.number == "engineering":
            if f.significant:
                return V.format_engineering_digits(
                    value, f.significant, sign=f.sign, exponent_digits=f.exponent_digits
                )
            return V.format_engineering(value)
        return V.format_float(value, f.digits, style=f.number, sign=f.sign, exponent_digits=f.exponent_digits)
