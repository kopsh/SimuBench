"""Checks of the commands: headers, the settings and operations they name, replies, syntax, quantities and overlapping headers."""

from __future__ import annotations

import itertools
import re
from typing import Any

from ...models.definition import CommandSpec, InstrumentDefinition
from ...scpi import values as V
from ...scpi.parse import Pattern, Token
from ..commands import told_apart
from ..component import Component
from ..validate import Report, _ptr, unknown_name
from .lookups import _check_state_ref, _compile, _declared, _state_options, _state_spec, operations


def _check_command(
    d: InstrumentDefinition, i: int, cmd: CommandSpec, classes: dict[str, type[Component]], report: Report
) -> Pattern | None:
    """One command: its header, settings, operation, arguments, parameters and reply. Returns the
    compiled header (None when it does not parse)."""
    p = ("commands", i)
    pattern = _compile(cmd.header)
    if pattern is None:
        report.add(
            "error",
            "E-DEF-HEADER",
            _ptr(*p, "header"),
            f"命令头 {cmd.header!r} 无法解析",
            "节点写作 :WORD、[:WORD] 或 WORD<后缀名>，例如 [:SOURce<ch>]:FREQuency[:CW]",
            cmd.header,
        )
        return None
    if not cmd.set and not cmd.query:
        report.add(
            "error",
            "E-DEF-ACCESS",
            _ptr(*p),
            f"命令 {cmd.header} 既不可写也不可查：两种用法都会报错",
            "set 与 query 至少保留一个（只写的命令写 query: false，只查的写 set: false）",
            cmd.header,
        )
    # A channel-list parameter also runs the command per channel (#183): its settings and
    # operations are channel state, even without a <ch> header suffix.
    per_channel = any(n.suffix == "ch" for n in pattern.nodes) or any(
        p.type == "channel_list" for p in cmd.params
    )
    indexes = _index_values(cmd)
    if cmd.state is not None:
        for ref in _expand(cmd.state, indexes):
            _check_state_ref(d, ref, per_channel, _ptr(*p, "state"), cmd.header, report)
    if cmd.op is not None:
        _check_op(d, classes, cmd.op, per_channel, _ptr(*p, "op"), cmd.header, report)
    for key, value in cmd.args.items():
        if isinstance(value, str) and value.startswith("$"):  # an argument bound to a setting (#252)
            for ref in _expand(value[1:], indexes):  # a header suffix may pick it
                if not _check_state_ref(d, ref, per_channel, _ptr(*p, "args", key), cmd.header, report):
                    break
    if cmd.read is not None:
        for ref in _expand(cmd.read, indexes):
            _check_state_ref(d, ref, per_channel, _ptr(*p, "read"), cmd.header, report)
    for state, value in cmd.writes.items():
        for ref in _expand(state, indexes):  # an index may pick the setting (#248: VIEW CHAN2)
            _check_write(d, ref, value, per_channel, _ptr(*p, "writes", state), cmd.header, report)
    for k, ps in enumerate(cmd.params):
        if ps.state is not None:
            for ref in _expand(ps.state, indexes):  # every setting an index can pick
                if not _check_state_ref(
                    d, ref, per_channel, _ptr(*p, "params", k, "state"), cmd.header, report
                ):
                    break
        for j, mirrored in enumerate(ps.mirror or []):
            for ref in _expand(mirrored, indexes):
                if not _check_state_ref(
                    d, ref, per_channel, _ptr(*p, "params", k, "mirror", j), cmd.header, report
                ):
                    break
        for key, ref in (ps.keys or {}).items():
            if ref is not None:
                _check_state_ref(d, ref, per_channel, _ptr(*p, "params", k, "keys", key), cmd.header, report)
        if isinstance(ps.default, str):  # an omitted list read from a setting (#423)
            ref = ps.default.removeprefix("$")
            found = _check_state_ref(d, ref, False, _ptr(*p, "params", k, "default"), cmd.header, report)
            if found is not None and (ps.type != "channel_list" or found.type != "int_list"):
                report.add(
                    "error",
                    "E-DEF-PARAM",
                    _ptr(*p, "params", k, "default"),
                    f"{cmd.header}: 只有通道列表参数的 default 可以写成设置,且该设置须为 int_list",
                    "写 default: $scan.list(int_list),或写成通道号列表 [1]",
                    ps.default,
                )
        if ps.type == "quantities":
            _check_quantities(d, ps, per_channel, _ptr(*p, "params", k), cmd.header, report)
        for state, value in (ps.sets or {}).items():
            for ref in _expand(state, indexes):
                _check_write(
                    d, ref, value, per_channel, _ptr(*p, "params", k, "sets", state), cmd.header, report
                )
        for word, writes in (ps.keywords or {}).items():
            for state, value in writes.items():
                for ref in _expand(state, indexes):
                    _check_write(
                        d,
                        ref,
                        value,
                        per_channel,
                        _ptr(*p, "params", k, "keywords", word, state),
                        cmd.header,
                        report,
                    )
    if cmd.reply is not None:
        fields = _op_fields(d, classes, cmd.op) if cmd.op else set()
        _check_reply(d, cmd, per_channel, _ptr(*p, "reply"), report, fields)
    return pattern


def _check_offset_convention(
    d: InstrumentDefinition, known: dict[str, type[Component]], report: Report
) -> None:
    """A quantizing ADC and the transfer reading its acquisition declare the offset convention each
    (``scope_adc.offset_sign`` and ``waveform_transfer.offset``, issue 667): the codes the transfer
    sends and the volts the ADC kept agree only when both say the same, so a mismatch is an error.
    Both defaults are read when a definition leaves one unset (the classes' defaults differ)."""
    by_name = {c.name: c for c in d.components}

    def value(spec: Any, key: str) -> str:
        cls = known[spec.type]
        return str(spec.params.get(key, cls.defaults.get(key))).lower()

    for i, xfer in enumerate(d.components):
        if xfer.type != "waveform_transfer" or isinstance(xfer.params.get("offset"), dict):
            continue  # not a transfer, or its offset is mapped from a setting
        if xfer.params.get("values") == "volts":
            continue  # volts only: the transfer never converts to codes
        acq = by_name.get(str(xfer.params.get("record", known[xfer.type].defaults.get("record"))))
        adc_spec = by_name.get(str(acq.params.get("adc", ""))) if acq is not None else None
        if adc_spec is None or adc_spec.type != "scope_adc" or not adc_spec.params.get("bits"):
            continue  # no ADC named, or it does not quantize
        want, got = value(adc_spec, "offset_sign"), value(xfer, "offset")
        if want != got:
            report.add(
                "error",
                "E-DEF-OFFSET-CONVENTION",
                _ptr("components", i, "params", "offset"),
                f"{xfer.name}.offset 是 {got!r}，而量化它的 {adc_spec.name}.offset_sign 是 {want!r}：码值与伏特的偏移约定不一致",
                f"改为 {want!r}",
                xfer.name,
            )


def _check_quantities(
    d: InstrumentDefinition,
    ps: Any,
    per_channel: bool,
    pointer: str,
    subject: str,
    report: Report,
) -> None:
    """A quantities parameter's table: every unit names a declared setting, its `sets` and the
    combine table's targets too, and every combine key is made of the table's units (5.2)."""
    units = ps.units or {}
    for name, entry in units.items():
        _check_state_ref(d, entry.state, per_channel, f"{pointer}/units/{name}/state", subject, report)
        for ref, value in entry.sets.items():
            _check_quantity_write(
                d, ref, value, per_channel, f"{pointer}/units/{name}/sets/{ref}", subject, report
            )
    for key, writes in (ps.combine or {}).items():
        kp = f"{pointer}/combine/{key}"
        for name in key.split(","):
            if name.strip().upper() not in {u.upper() for u in units}:
                report.add(
                    "error",
                    "E-DEF-QUANTITY",
                    kp,
                    f"组合表里的单位 {name.strip()!r} 不在 units 表中",
                    f"units 里的单位：{', '.join(units)}",
                    subject,
                )
        for ref, value in writes.items():
            _check_quantity_write(
                d, ref, value, per_channel, f"{pointer}/combine/{key}/{ref}", subject, report
            )


def _check_quantity_write(
    d: InstrumentDefinition,
    ref: str,
    value: Any,
    per_channel: bool,
    pointer: str,
    subject: str,
    report: Report,
) -> None:
    """One setting a quantities table writes (a `sets` entry or a combine target): the state must
    be declared and the value acceptable for it; ``{if_zero: <state>, then, else}`` decides on a
    state that must be declared too."""
    if isinstance(value, dict) and "if_zero" in value:
        _check_state_ref(d, str(value["if_zero"]), per_channel, f"{pointer}/if_zero", subject, report)
        for branch in ("then", "else"):
            if branch in value:
                _check_quantity_write(
                    d, ref, value[branch], per_channel, f"{pointer}/{branch}", subject, report
                )
        return
    spec = _check_state_ref(d, ref, per_channel, pointer, subject, report)
    if spec is None:
        return
    problem, fix = V.fit_value(spec, value)
    if problem:
        report.add(
            "error",
            "E-DEF-QUANTITY",
            pointer,
            f"{ref!r} {problem}",
            fix,
            subject,
        )


def _index_values(cmd: CommandSpec) -> dict[str, list[int]]:
    """The values each index parameter and each header suffix can take (at most 64 checked); both
    substitute ``{name}`` in the command's paths (a suffix picks its settings, #1074)."""
    out = {k: list(v) for k, v in (cmd.suffixes or {}).items()}
    for ps in cmd.params:
        if ps.index and ps.index_words:
            out[ps.name] = sorted(set(ps.index_words.values()))
        elif ps.index:
            lo = int(ps.min) if ps.min is not None else 1
            hi = int(ps.max) if ps.max is not None else lo
            out[ps.name] = list(range(lo, min(hi, lo + 63) + 1))
    return out


def _expand(ref: str, indexes: dict[str, list[int]]) -> list[str]:
    refs = [ref]
    for name, values in indexes.items():
        if "{" + name + "}" in ref:
            refs = [r.replace("{" + name + "}", str(v)) for r in refs for v in values]
    return refs


def _op_fields(d: InstrumentDefinition, classes: dict[str, type[Component]], ref: str) -> set[str]:
    """Names an operation gives its command's reply template (``op(fields=...)``)."""
    comp, _, name = ref.rpartition(".")
    cls = classes.get(f"ch1.{comp[3:]}" if comp.startswith("ch.") else comp)
    for attr in dir(cls) if cls is not None else []:
        fn = getattr(cls, attr)
        if getattr(fn, "__op_name__", None) == name:
            return set(getattr(fn, "__op_fields__", ()))
    return set()


def _check_reply(
    d: InstrumentDefinition,
    cmd: CommandSpec,
    per_channel: bool,
    pointer: str,
    report: Report,
    fields: set[str] | None = None,
) -> None:
    for name in re.findall(r"\{([^{}]*)\}", cmd.reply or ""):
        if name == "header" or name in (fields or ()):
            continue
        if name in ("value", "value_block"):  # value_block: the same reply as a block (#415)
            if cmd.state is None and cmd.op is None:
                report.add(
                    "error",
                    "E-DEF-REPLY",
                    pointer,
                    f"{{{name}}} 是命令绑定的状态或操作的应答，这条命令两者都没有",
                    "改用 {<状态路径>}",
                    cmd.header,
                )
            continue
        _spec, problem = _state_spec(d, name, per_channel)
        if problem:
            unknown_name(
                report,
                "E-DEF-REPLY",
                pointer,
                f"应答模板引用 {{{name}}}：{problem}",
                name,
                _state_options(d, per_channel),
                cmd.header,
                fallback="可用 {header}、{value} 与 {<状态路径>}",
                hint=lambda h: f"改为 {{{h}}}",
            )


def _check_syntax(d: InstrumentDefinition, report: Report) -> None:
    mode = d.syntax.echo_header
    if mode.startswith("$"):
        pointer = _ptr("syntax", "echo_header")
        spec = _check_state_ref(d, mode[1:], False, pointer, "syntax", report)
        if spec is not None:
            missing = {"OFF", "SHORT", "LONG"} - {o.upper() for o in spec.options or []}
            if spec.type != "bool" and (spec.type != "enum" or missing):
                report.add(
                    "error",
                    "E-DEF-SYNTAX",
                    pointer,
                    f"echo_header 绑定的状态 {mode[1:]!r} 要是取值为 OFF、SHORT、LONG 的枚举，或布尔开关",
                    'options: ["OFF", SHORT, LONG]，或 type: bool',
                    "syntax",
                )
    long_form = d.syntax.long_form
    if long_form:
        pointer = _ptr("syntax", "long_form")
        spec = _check_state_ref(d, long_form[1:], False, pointer, "syntax", report)
        if spec is not None and spec.type != "bool":
            report.add(
                "error",
                "E-DEF-SYNTAX",
                pointer,
                f"long_form 绑定的状态 {long_form[1:]!r} 要是布尔开关",
                "type: bool",
                "syntax",
            )


def _check_write(
    d: InstrumentDefinition,
    path: str,
    value: Any,
    per_channel: bool,
    pointer: str,
    subject: str,
    report: Report,
) -> None:
    """A constant written by a command fits its setting, by the rule the engine applies to the
    same value (fit_value): an option in any SCPI word form (the run time stores the option it
    names), a boolean, or a number in range with its special values exempt."""
    spec = _check_state_ref(d, path, per_channel, pointer, subject, report)
    if spec is None:
        return
    problem, fix = V.fit_value(spec, value)
    if problem:
        if spec.type == "enum":
            problem = f"{path!r} {problem}"
        report.add("error", "E-DEF-WRITE", pointer, problem, fix, subject)


def _check_op(
    d: InstrumentDefinition,
    classes: dict[str, type[Component]],
    ref: str,
    per_channel: bool,
    pointer: str,
    subject: str,
    report: Report,
) -> None:
    comp, _, name = ref.rpartition(".")
    channel = comp.startswith("ch.")
    key = f"ch1.{comp[3:]}" if channel else comp
    if channel and not per_channel:
        report.add(
            "error",
            "E-DEF-OP-UNKNOWN",
            pointer,
            f"{ref!r} 是通道组件的操作，这里没有通道号",
            "命令头需要 <ch>，例如 :CHANnel<ch>:...",
            subject,
        )
        return
    cls = classes.get(key)
    if cls is None:
        if comp and _declared(d, comp[3:] if channel else comp, channel):
            return  # an unknown component type, reported above
        names = [s.name for s in d.components] + [f"ch.{s.name}" for s in d.channel_components]
        unknown_name(
            report,
            "E-DEF-OP-UNKNOWN",
            pointer,
            f"{ref!r}：没有名为 {comp!r} 的组件",
            comp,
            names,
            subject,
            fallback="写作 <组件>.<操作>（通道组件写作 ch.<组件>.<操作>）",
            hint=lambda h, op=name: f"改为 {h}.{op}",
        )
        return
    ops = operations(cls)
    if name not in ops:
        unknown_name(
            report,
            "E-DEF-OP-UNKNOWN",
            pointer,
            f"{cls.type_name} 没有操作 {name!r}",
            name,
            ops,
            subject,
            fallback=f"可用操作：{', '.join(ops) or '无'}",
            hint=lambda h, c=comp: f"改为 {c}.{h}",
        )


def _samples(pattern: Pattern) -> list[tuple[Token, ...]]:
    """Every header form the pattern accepts, up to the spelling of each node and the suffix value."""
    choices = []
    for node in pattern.nodes:
        forms: list[Token | None] = [Token(node.long, None)]
        if node.suffix:
            forms.append(Token(node.long, 1))
        if node.optional:
            forms.append(None)
        choices.append(forms)
    out = []
    for combo in itertools.islice(itertools.product(*choices), 512):
        out.append(tuple(t for t in combo if t is not None))
    return out


def _check_overlaps(d: InstrumentDefinition, compiled: list[Pattern | None], report: Report) -> None:
    """The first command whose header matches wins: a later one that an earlier one also matches
    never runs for those forms.

    An earlier pattern can match a form only if the form's first keyword is one its leading nodes
    (the optional ones up to the first required one) can start with, so each form is tried against
    those patterns alone, in command order — the same verdict as trying every earlier command,
    without the quadratic walk a model with a thousand commands paid on every read."""
    starts: dict[str, list[tuple[int, Pattern]]] = {}
    for i, pi in enumerate(compiled):
        if pi is None:
            continue
        words: set[str] = set()
        for node in pi.nodes:
            words.update((node.long, node.short))
            if not node.optional:
                break
        for word in words:
            starts.setdefault(word, []).append((i, pi))
    for j, pj in enumerate(compiled):
        if pj is None:
            continue
        for tokens in _samples(pj):
            if not tokens:
                continue
            i = next(
                (
                    i
                    for i, pi in starts.get(tokens[0].keyword, ())
                    if i < j
                    and pi.common == pj.common
                    and pi.match(tokens) is not None
                    and not told_apart(d.commands[i], d.commands[j])
                ),
                None,
            )
            if i is None:
                continue
            form = ("*" if pj.common else ":") + ":".join(
                f"{t.keyword}{'' if t.suffix is None else t.suffix}" for t in tokens
            )
            report.add(
                "error",
                "E-DEF-HEADER-DUPLICATE",
                _ptr("commands", j, "header"),
                f"命令头 {d.commands[j].header!r} 与第 {i} 条 {d.commands[i].header!r} 都匹配 {form}；"
                "先写的那条生效，这条不会执行",
                "删除重复的一条，或让两个命令头互不重叠",
                d.commands[j].header,
            )
            break
