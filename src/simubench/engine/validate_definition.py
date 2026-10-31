"""Static checks of instrument definition files (instrument-authoring.md 5.1, issue 40).

A definition that loads (the JSON Schema accepts it) can still name a component type that does
not exist, an operation the component does not have or a state variable that is not declared; the
instrument would only fail when a session starts, or when the command is first sent. These checks
find such mistakes in the file, with the same diagnostics as bench validation (code, JSON pointer,
message, suggestion), so that people and agents correct a definition before running it (D9).
"""

from __future__ import annotations

import itertools
import re
from typing import Any

from pydantic import ValidationError

from ..library.macros import expand_macros
from ..library.migrate import FormatError, upgrade
from ..library.panels import DEDICATED, template_of
from ..models.definition import CommandSpec, ComponentSpec, InstrumentDefinition, PanelControl, StateSpec
from ..scpi import values as V
from ..scpi.errors import ScpiError
from ..scpi.parse import Pattern, Token
from .component import Component, binding_of, component_types
from .validate import Report, _closest, _ptr

_NODE = re.compile(r"\[:?[A-Za-z_]+(?:<\w+>)?\]|:?[A-Za-z_]+(?:<\w+>)?")


def validate_definition_data(data: Any) -> tuple[InstrumentDefinition | None, Report]:
    """Validate raw definition data (parsed YAML/JSON); return the model when the schema accepts it."""
    report = Report()
    try:
        data, _ = upgrade(data, "instrument")
    except FormatError as exc:
        report.add("error", "E-FORMAT", "/schema", str(exc), None)
        return None, report
    try:
        data = expand_macros(data)
    except ValueError as exc:
        report.add("error", "E-DEF-MACRO", "/components", str(exc), "把模块逐个写出")
        return None, report
    try:
        definition = InstrumentDefinition.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = [p for p in err["loc"] if not (isinstance(p, str) and p.startswith("function-"))]
            report.add("error", "E-SCHEMA", _ptr(*loc), err["msg"], None)
        return None, report
    validate_definition(definition, report)
    return definition, report


def validate_definition(definition: InstrumentDefinition, report: Report | None = None) -> Report:
    report = report or Report()
    d = definition
    known = component_types()
    count = d.channels.count

    # ---- components: instrument level by name, channel level as ch<n>.<name> ----
    classes: dict[str, type[Component]] = {}  # resolved name -> class (known types only)
    for section, specs, channel in (
        ("components", d.components, False),
        ("channel_components", d.channel_components, True),
    ):
        seen: set[str] = set()
        for i, spec in enumerate(specs):
            p = (section, i)
            if spec.name in seen:
                report.add(
                    "error",
                    "E-DEF-COMPONENT-DUPLICATE",
                    _ptr(*p, "name"),
                    f"组件名 {spec.name!r} 重复，后一个会覆盖前一个",
                    "为其中一个换一个名称",
                    spec.name,
                )
            seen.add(spec.name)
            cls = known.get(spec.type)
            if cls is None:
                hint = _closest(spec.type, list(known))
                report.add(
                    "error",
                    "E-DEF-COMPONENT-UNKNOWN",
                    _ptr(*p, "type"),
                    f"组件类型 {spec.type!r} 不存在",
                    f"改为 {hint!r}" if hint else None,
                    spec.name,
                )
                continue
            for n in range(1, count + 1) if channel else (None,):
                classes[f"ch{n}.{spec.name}" if n else spec.name] = cls
            _check_params(d, spec, cls, p, channel, report)

    # ---- parameters that name another component (Component.refs) ----
    declared = {
        "instrument": {c.name: known.get(c.type) for c in d.components},
        "channel": {c.name: known.get(c.type) for c in d.channel_components},
    }
    for section, specs in (("components", d.components), ("channel_components", d.channel_components)):
        for i, spec in enumerate(specs):
            cls = known.get(spec.type)
            for param, (base, where) in (cls.refs if cls else {}).items():
                name = str(spec.params.get(param, cls.defaults.get(param)) or "")  # type: ignore[union-attr]
                if not name and param in cls.optional_refs:  # type: ignore[union-attr]
                    continue
                target = declared[where].get(name, False)
                want = known.get(base)
                if target is False:
                    options = [n for n, c in declared[where].items() if c and want and issubclass(c, want)]
                    report.add(
                        "error",
                        "E-DEF-COMPONENT-REF",
                        _ptr(section, i, "params", param),
                        f"{spec.name}.{param} 应指向一个 {base} 组件（{'每通道' if where == 'channel' else '仪表级'}），"
                        + (f"没有名为 {name!r} 的组件" if name else "没有填写"),
                        f"改为 {options[0]!r}" if options else f"添加一个 {base} 组件",
                        spec.name,
                    )
                elif target is not None and want is not None and not issubclass(target, want):
                    report.add(
                        "error",
                        "E-DEF-COMPONENT-REF",
                        _ptr(section, i, "params", param),
                        f"{spec.name}.{param} 指向的 {name!r} 是 {target.type_name}，应为 {base}",
                        None,
                        spec.name,
                    )

    # ---- individual deviations: a parameter of a declared component ----
    specs_by_name = {c.name: c for c in (*d.components, *d.channel_components)}
    for i, dev in enumerate(d.deviations):
        p = ("deviations", i, "param")
        spec = specs_by_name.get(dev.component)
        cls = known.get(spec.type) if spec else None
        if spec is None:
            hint = _closest(dev.component, list(specs_by_name))
            report.add(
                "error",
                "E-DEF-DEVIATION",
                _ptr(*p),
                f"没有组件 {dev.component!r}，无法给它个体偏差",
                f"改为 {hint!r}" if hint else None,
            )
            continue
        if cls is None:
            continue  # reported with the component
        if dev.parameter not in cls.defaults:
            hint = _closest(dev.parameter, list(cls.defaults))
            report.add(
                "error",
                "E-DEF-DEVIATION",
                _ptr(*p),
                f"{spec.type} 没有参数 {dev.parameter!r}",
                f"改为 {dev.component}.{hint}" if hint else None,
            )
            continue
        nominal = spec.params.get(dev.parameter, cls.defaults[dev.parameter])
        if isinstance(nominal, bool) or not isinstance(nominal, (int, float, dict, str)):
            report.add(
                "error",
                "E-DEF-DEVIATION",
                _ptr(*p),
                f"{dev.param} 不是数值参数，不能有个体偏差",
            )
        elif dev.unit and isinstance(nominal, (int, float)) and nominal == 0:
            report.add(
                "warning",
                "W-DEF-DEVIATION-ZERO",
                _ptr("deviations", i, "unit"),
                f"{dev.param} 的标称值为 0，按 {dev.unit} 的相对偏差不起作用",
                "去掉 unit，把 limit 写成参数本身的单位",
            )

    # ---- signal wiring and exposed ports ----
    def check_end(ref: str, pointer: str, in_channel: bool) -> None:
        comp, _, port = ref.rpartition(".")
        if in_channel:  # inside a channel, components are named without the ch<n>. prefix
            cls = classes.get(f"ch1.{comp}")
            options = [s.name for s in d.channel_components]
        else:
            cls = classes.get(comp)
            options = [s.name for s in d.components] + [f"ch{{n}}.{s.name}" for s in d.channel_components]
        if not comp or cls is None:
            if comp and _declared(d, comp, in_channel):
                return  # an unknown component type, reported above
            hint = _closest(comp or ref, options)
            report.add(
                "error",
                "E-DEF-WIRE-COMPONENT",
                pointer,
                f"{ref!r}：没有名为 {comp or ref!r} 的组件",
                f"改为 {hint}.{port}" if hint else "写作 <组件>.<端口>",
            )
            return
        if not _has_port(cls, port):
            hint = _closest(port, list(cls.ports))
            report.add(
                "error",
                "E-DEF-WIRE-PORT",
                pointer,
                f"{ref!r}：{cls.type_name} 没有端口 {port!r}",
                f"改为 {comp}.{hint}" if hint else f"可用端口：{', '.join(cls.ports) or '无'}",
            )

    for i, (src, dst) in enumerate(d.wiring.channel):
        for k, ref in enumerate((src, dst)):
            check_end(ref, _ptr("wiring", "channel", i, k), True)
    for i, (src, dst) in enumerate(d.wiring.instrument):
        for k, ref in enumerate((src, dst)):
            check_end(ref.replace("{n}", "1"), _ptr("wiring", "instrument", i, k), False)
    for pname, target in d.ports.channel.items():
        check_end(target, _ptr("ports", "channel", pname), True)
    for pname, target in d.ports.instrument.items():
        check_end(target.replace("{n}", "1"), _ptr("ports", "instrument", pname), False)

    # ---- commands ----
    compiled: list[Pattern | None] = []
    for i, cmd in enumerate(d.commands):
        p = ("commands", i)
        pattern = _compile(cmd.header)
        compiled.append(pattern)
        if pattern is None:
            report.add(
                "error",
                "E-DEF-HEADER",
                _ptr(*p, "header"),
                f"命令头 {cmd.header!r} 无法解析",
                "节点写作 :WORD、[:WORD] 或 WORD<后缀名>，例如 [:SOURce<ch>]:FREQuency[:CW]",
                cmd.header,
            )
            continue
        per_channel = any(n.suffix == "ch" for n in pattern.nodes)
        if cmd.state is not None:
            _check_state_ref(d, cmd.state, per_channel, _ptr(*p, "state"), cmd.header, report)
        if cmd.op is not None:
            _check_op(d, classes, cmd.op, per_channel, _ptr(*p, "op"), cmd.header, report)
        indexes = _index_values(cmd)
        for k, ps in enumerate(cmd.params):
            if ps.state is not None:
                for ref in _expand(ps.state, indexes):  # every setting an index can pick
                    if not _check_state_ref(
                        d, ref, per_channel, _ptr(*p, "params", k, "state"), cmd.header, report
                    ):
                        break
            for key, ref in (ps.keys or {}).items():
                if ref is not None:
                    _check_state_ref(
                        d, ref, per_channel, _ptr(*p, "params", k, "keys", key), cmd.header, report
                    )
            if ps.type == "quantities":
                _check_quantities(d, ps, per_channel, _ptr(*p, "params", k), cmd.header, report)
        if cmd.reply is not None:
            fields = _op_fields(d, classes, cmd.op) if cmd.op else set()
            _check_reply(d, cmd, per_channel, _ptr(*p, "reply"), report, fields)
    _check_overlaps(d, compiled, report)
    _check_dialect(d, report)
    _check_panel(d, report)
    return report


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
    if (
        spec is not None
        and spec.type == "enum"
        and str(value).upper() not in {o.upper() for o in spec.options or []}
    ):
        report.add(
            "error",
            "E-DEF-QUANTITY",
            pointer,
            f"{ref!r} 不接受 {value!r}",
            f"可选值：{', '.join(spec.options or [])}",
            subject,
        )


def _index_values(cmd: CommandSpec) -> dict[str, list[int]]:
    """The values each index parameter can take (at most 64 checked)."""
    out = {}
    for ps in cmd.params:
        if ps.index:
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
        if name == "value":
            if cmd.state is None and cmd.op is None:
                report.add(
                    "error",
                    "E-DEF-REPLY",
                    pointer,
                    "{value} 是命令绑定的状态或操作的应答，这条命令两者都没有",
                    "改用 {<状态路径>}",
                    cmd.header,
                )
            continue
        _spec, problem = _state_spec(d, name, per_channel)
        if problem:
            hint = _closest(name, _state_options(d, per_channel))
            report.add(
                "error",
                "E-DEF-REPLY",
                pointer,
                f"应答模板引用 {{{name}}}：{problem}",
                f"改为 {{{hint}}}" if hint else "可用 {header}、{value} 与 {<状态路径>}",
                cmd.header,
            )


def _check_dialect(d: InstrumentDefinition, report: Report) -> None:
    mode = d.dialect.echo_header
    if not mode.startswith("$"):
        return
    pointer = _ptr("dialect", "echo_header")
    spec = _check_state_ref(d, mode[1:], False, pointer, "dialect", report)
    if spec is None:
        return
    missing = {"OFF", "SHORT", "LONG"} - {o.upper() for o in spec.options or []}
    if spec.type != "enum" or missing:
        report.add(
            "error",
            "E-DEF-DIALECT",
            pointer,
            f"echo_header 绑定的状态 {mode[1:]!r} 要是取值为 OFF、SHORT、LONG 的枚举",
            'options: ["OFF", SHORT, LONG]',
            "dialect",
        )


def _declared(d: InstrumentDefinition, name: str, channel: bool) -> bool:
    specs = d.channel_components if channel else d.components
    if not channel and name.startswith("ch"):
        name = name.partition(".")[2]
        specs = d.channel_components
    return any(s.name == name for s in specs)


def _has_port(cls: type[Component], port: str) -> bool:
    return port in cls.ports or bool(cls.port_pattern and re.fullmatch(cls.port_pattern, port))


def _compile(header: str) -> Pattern | None:
    """The header's pattern, or None when part of the text is not a header node."""
    text = header.strip()
    if text.startswith("*"):
        return Pattern.compile(text) if re.fullmatch(r"\*[A-Za-z]+", text) else None
    if not text or _NODE.sub("", text):
        return None
    try:
        return Pattern.compile(text)
    except ValueError:
        return None


def _state_spec(d: InstrumentDefinition, path: str, per_channel: bool) -> tuple[StateSpec | None, str | None]:
    """The state variable a binding names, and a message when it does not resolve."""
    if path.startswith("ch."):
        if not per_channel:
            return (
                None,
                f"{path!r} 是通道状态，这里没有通道号（命令头需要 <ch>，或绑定写作 ch<n>.{path[3:]}）",
            )
        spec = d.channel_state.get(path[3:])
        return spec, None if spec else f"没有通道状态 {path[3:]!r}"
    if path.startswith("ch{n}."):  # a channel's setting, the channel chosen by the component
        spec = d.channel_state.get(path[6:])
        return spec, None if spec else f"没有通道状态 {path[6:]!r}"
    m = re.fullmatch(r"ch(\d+)\.(.+)", path)
    if m and m.group(2) in d.channel_state:
        if not 1 <= int(m.group(1)) <= d.channels.count:
            return None, f"{path!r}：仪表只有 {d.channels.count} 个通道"
        return d.channel_state[m.group(2)], None
    spec = d.state.get(path)
    return spec, None if spec else f"没有状态 {path!r}"


def _state_options(d: InstrumentDefinition, per_channel: bool) -> list[str]:
    return list(d.state) + ([f"ch.{k}" for k in d.channel_state] if per_channel else [])


def _check_state_ref(
    d: InstrumentDefinition, path: str, per_channel: bool, pointer: str, subject: str, report: Report
) -> StateSpec | None:
    spec, problem = _state_spec(d, path, per_channel)
    if problem:
        hint = _closest(path, _state_options(d, per_channel))
        report.add(
            "error", "E-DEF-STATE-UNKNOWN", pointer, problem, f"改为 {hint!r}" if hint else None, subject
        )
    return spec


def _check_params(
    d: InstrumentDefinition,
    spec: ComponentSpec,
    cls: type[Component],
    p: tuple[Any, ...],
    channel: bool,
    report: Report,
) -> None:
    for key, value in spec.params.items():
        pp = _ptr(*p, "params", key)
        if key not in cls.defaults:
            hint = _closest(key, list(cls.defaults))
            report.add(
                "warning",
                "W-DEF-PARAM-UNKNOWN",
                pp,
                f"{spec.type} 没有参数 {key!r}，它不会起作用",
                f"改为 {hint!r}" if hint else None,
                spec.name,
            )
        mapping = value if isinstance(value, dict) and "from" in value else None
        ref = binding_of(value)
        if ref is None:
            continue
        st = _check_state_ref(
            d, ref, channel, _ptr(*p, "params", key, "from") if mapping else pp, spec.name, report
        )
        if st is not None and mapping and mapping.get("map"):
            _check_map_keys(st, mapping["map"], (*p, "params", key, "map"), spec.name, report)
            continue  # the table decides the parameter's type
        default = cls.defaults.get(key)
        if st is not None and default is not None and not _compatible(default, st):
            report.add(
                "error",
                "E-DEF-PARAM-TYPE",
                pp,
                f"{spec.type}.{key} 是{_kind(default)}，绑定的状态 {value[1:]!r} 是 {st.type}",
                f"绑定一个{_kind(default)}状态",
                spec.name,
            )


def _check_map_keys(
    st: StateSpec, table: dict[str, Any], where: tuple[Any, ...], subject: str, report: Report
) -> None:
    """A key of ``map`` that the setting can never take never applies."""
    if st.type != "enum":
        return
    options = {o.upper() for o in st.options or []}
    for k in table:
        if str(k).upper() not in options:
            hint = _closest(str(k), list(st.options or []))
            report.add(
                "warning",
                "W-DEF-MAP-KEY",
                _ptr(*where, k),
                f"设置的取值中没有 {k!r}，这一项不会生效",
                f"改为 {hint!r}" if hint else f"可选值：{', '.join(st.options or [])}",
                subject,
            )


def _kind(default: Any) -> str:
    if isinstance(default, bool):
        return "布尔量"
    if isinstance(default, (int, float)):
        return "数值"
    return "文字"


def _compatible(default: Any, st: StateSpec) -> bool:
    """Enumerations pass to numbers and switches: components read named values (FIFTy, OFF)."""
    if isinstance(default, bool):
        return st.type in ("bool", "int", "enum")
    if isinstance(default, (int, float)):
        return st.type in ("float", "int", "bool", "enum")
    if isinstance(default, str) and default.startswith("$"):
        return True  # bound by default: the component reads a setting, of any type
    if isinstance(default, str):
        return st.type in ("enum", "string")
    return True


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
        hint = _closest(comp, names)
        report.add(
            "error",
            "E-DEF-OP-UNKNOWN",
            pointer,
            f"{ref!r}：没有名为 {comp!r} 的组件",
            f"改为 {hint}.{name}" if hint else "写作 <组件>.<操作>（通道组件写作 ch.<组件>.<操作>）",
            subject,
        )
        return
    ops = operations(cls)
    if name not in ops:
        hint = _closest(name, ops)
        report.add(
            "error",
            "E-DEF-OP-UNKNOWN",
            pointer,
            f"{cls.type_name} 没有操作 {name!r}",
            f"改为 {comp}.{hint}" if hint else f"可用操作：{', '.join(ops) or '无'}",
            subject,
        )


def operations(cls: type[Component]) -> list[str]:
    """Names of the operations a component type offers (``@op``)."""
    return sorted({n for a in dir(cls) if (n := getattr(getattr(cls, a), "__op_name__", None))})


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


def _told_apart(a: CommandSpec, b: CommandSpec) -> bool:
    """Two commands on one header that ``when`` tells apart: some parameter value differs."""
    if not a.when or not b.when:
        return False
    return any(k in b.when and str(v).upper() != str(b.when[k]).upper() for k, v in a.when.items())


def _check_overlaps(d: InstrumentDefinition, compiled: list[Pattern | None], report: Report) -> None:
    """The first command whose header matches wins: a later one that an earlier one also matches
    never runs for those forms."""
    for j, pj in enumerate(compiled):
        if pj is None:
            continue
        for tokens in _samples(pj):
            if not tokens:
                continue
            i = next(
                (
                    i
                    for i, pi in enumerate(compiled[:j])
                    if pi is not None
                    and pi.common == pj.common
                    and pi.match(tokens) is not None
                    and not _told_apart(d.commands[i], d.commands[j])
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


# ---- the front panel (instrument-authoring.md 7.3) ---------------------------------------------------

_CONTROL_TYPES = {
    "switch": ("bool",),
    "select": ("enum",),
    "number": ("float", "int"),
    "buttons": ("enum", "bool", "float", "int", "string"),
}


def _check_panel(d: InstrumentDefinition, report: Report) -> None:
    panel = d.display.panel
    if panel is None:
        return
    base = ("display", "panel")
    template = template_of(d)
    if template in DEDICATED:
        report.add(
            "warning",
            "W-DEF-PANEL-IGNORED",
            _ptr(*base),
            f"{template} 有专用屏幕，界面不使用面板声明",
            "去掉 display.panel",
            "panel",
        )
    components = {c.name for c in d.components} | {
        f"ch{n}.{c.name}" for c in d.channel_components for n in range(1, d.channels.count + 1)
    }

    def setting(path: str, pointer: str) -> StateSpec | None:
        spec, problem = _state_spec(d, path, False)
        if problem:
            hint = _closest(path, _state_options(d, False))
            report.add(
                "error", "E-DEF-PANEL-STATE", pointer, problem, f"改为 {hint!r}" if hint else None, "panel"
            )
        return spec

    def value(path: str, spec: StateSpec, v: Any, pointer: str) -> None:
        problem, hint = _panel_value(spec, v)
        if problem:
            report.add("error", "E-DEF-PANEL-VALUE", pointer, f"{path}：{problem}", hint, "panel")

    def settings(values: dict[str, Any], pointer: tuple[Any, ...]) -> None:
        for path, v in values.items():
            spec = setting(path, _ptr(*pointer, path))
            if spec is not None:
                value(path, spec, v, _ptr(*pointer, path))

    for i, readout in enumerate(panel.readouts):
        p = (*base, "readouts", i)
        comp, _, name = readout.reading.rpartition(".")
        if not comp or not name or comp not in components:
            hint = _closest(comp or readout.reading, sorted(components))
            report.add(
                "error",
                "E-DEF-PANEL-READING",
                _ptr(*p, "reading"),
                f"读数 {readout.reading!r} 应写作 <组件>.<读数>，"
                + (f"没有组件 {comp!r}" if comp else "缺少组件名"),
                f"改为 {hint}.{name or 'DC'}" if hint else None,
                "panel",
            )
        settings(readout.when, (*p, "when"))

    for g, group in enumerate(panel.groups):
        for c, control in enumerate(group.controls):
            p = (*base, "groups", g, "controls", c)
            spec = setting(control.state, _ptr(*p, "state"))
            if spec is not None:
                _check_control(control, spec, p, report)
                for k, key in enumerate(control.keys or []):
                    value(control.state, spec, key.value, _ptr(*p, "keys", k, "value"))
                    settings(key.sets, (*p, "keys", k, "sets"))

    for k, key in enumerate(panel.softkeys):
        p = (*base, "softkeys", k)
        if key.action is not None:
            if key.action not in d.display.actions and "acq" not in components:
                report.add(
                    "error",
                    "E-DEF-PANEL-ACTION",
                    _ptr(*p, "action"),
                    f"面板动作 {key.action!r} 既没有写在 display.actions 中，也没有采集组件 acq 执行它",
                    f"在 display.actions 中写出 {key.action} 对应的命令",
                    "panel",
                )
        elif key.state is not None:
            spec = setting(key.state, _ptr(*p, "state"))
            if spec is not None:
                if spec.readonly:
                    report.add(
                        "error",
                        "E-DEF-PANEL-CONTROL",
                        _ptr(*p, "state"),
                        f"{key.state} 是只读状态",
                        None,
                        "panel",
                    )
                value(key.state, spec, key.value, _ptr(*p, "value"))
        settings(key.sets, (*p, "sets"))


def _check_control(control: PanelControl, spec: StateSpec, p: tuple[Any, ...], report: Report) -> None:
    kinds = _CONTROL_TYPES[control.control]
    if spec.readonly:
        report.add(
            "error",
            "E-DEF-PANEL-CONTROL",
            _ptr(*p, "state"),
            f"{control.state} 是只读状态，不能放控件",
            "把它作为读数显示，或去掉这个控件",
            "panel",
        )
    elif spec.type not in kinds:
        fits = [k for k, types in _CONTROL_TYPES.items() if spec.type in types and k != "buttons"]
        report.add(
            "error",
            "E-DEF-PANEL-CONTROL",
            _ptr(*p, "control"),
            f"{control.state} 是 {spec.type}，不能用 {control.control} 控件",
            f"改为 {fits[0]}" if fits else "改为 buttons 并写出 keys",
            "panel",
        )
    if control.control == "buttons" and not control.keys and spec.type not in ("enum", "bool"):
        report.add(
            "error",
            "E-DEF-PANEL-CONTROL",
            _ptr(*p, "keys"),
            f"{control.state} 是 {spec.type}，按键组要写出 keys（每个键的取值）",
            "例如 keys: [{value: 0.1, label: 100 mV}, {value: 1, label: 1 V}]",
            "panel",
        )
    if control.keys is not None and control.control != "buttons":
        report.add(
            "error", "E-DEF-PANEL-CONTROL", _ptr(*p, "keys"), "keys 只用于 buttons 控件", None, "panel"
        )
    if (control.step is not None or control.unit is not None) and control.control != "number":
        report.add(
            "error",
            "E-DEF-PANEL-CONTROL",
            _ptr(*p, "step" if control.step is not None else "unit"),
            "step 与 unit 只用于 number 控件",
            None,
            "panel",
        )


def _panel_value(spec: StateSpec, v: Any) -> tuple[str | None, str | None]:
    """Whether a value the panel writes or compares fits the setting, and the fix when it does not."""
    if spec.type == "enum":
        options = spec.options or []
        if isinstance(v, str) and v in options:
            return None, None
        try:
            fixed = V.parse_enum(str(v).encode(), options)
        except ScpiError:
            return f"{v!r} 不是可选值（{', '.join(options)}）", None
        return f"{v!r} 要写作选项的原样 {fixed!r}", f"改为 {fixed!r}"
    if spec.type == "bool":
        return (None, None) if isinstance(v, bool) else (f"{v!r} 不是 true / false", None)
    if spec.type in ("float", "int"):
        if isinstance(v, str) and v.upper() in {k.upper() for k in spec.special or {}}:
            return None, None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return f"{v!r} 不是数值", None
        if (spec.min is not None and v < spec.min and v not in (spec.special or {}).values()) or (
            spec.max is not None and v > spec.max
        ):
            return f"{v!r} 超出范围 [{spec.min}, {spec.max}]", None
        return None, None
    return (None, None) if isinstance(v, str) else (f"{v!r} 不是字符串", None)
