"""Checks of the definition's components: their types and references, options, and the individual deviations they declare."""

from __future__ import annotations

from ...models.definition import InstrumentDefinition
from ...scpi.errors import ScpiError
from ..component import Component
from ..deviation import case_label
from ..settings import validate_value
from ..validate import Report, _ptr, unknown_name
from .lookups import _protocol_gaps
from .params import _check_params


def _check_components(
    d: InstrumentDefinition, known: dict[str, type[Component]], report: Report
) -> dict[str, type[Component]]:
    """Components: instrument level by name, channel level as ch<n>.<name>. Returns the resolved
    name -> class table (known types only)."""
    count = d.channels.count
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
                unknown_name(
                    report,
                    "E-DEF-COMPONENT-UNKNOWN",
                    _ptr(*p, "type"),
                    f"组件类型 {spec.type!r} 不存在",
                    spec.type,
                    list(known),
                    spec.name,
                )
                continue
            for n in range(1, count + 1) if channel else (None,):
                classes[f"ch{n}.{spec.name}" if n else spec.name] = cls
            _check_params(d, spec, cls, p, channel, report)
            for key, message in cls.literal_problems(spec.params):
                report.add("error", "E-DEF-PARAM-VALUE", _ptr(*p, "params", key), message, None, spec.name)
    return classes


def _check_component_refs(d: InstrumentDefinition, known: dict[str, type[Component]], report: Report) -> None:
    """Parameters that name another component (Component.refs)."""
    declared = {
        "instrument": {c.name: known.get(c.type) for c in d.components},
        "channel": {c.name: known.get(c.type) for c in d.channel_components},
    }
    for section, specs in (("components", d.components), ("channel_components", d.channel_components)):
        for i, spec in enumerate(specs):
            cls = known.get(spec.type)
            if cls is None:  # an unknown component type: reported as E-DEF-COMPONENT-UNKNOWN elsewhere
                continue
            targets: dict[str, type | None] = {}
            for param, (base, where) in cls.refs.items():
                name = str(spec.params.get(param, cls.defaults.get(param)) or "")
                if not name and param in cls.optional_refs:
                    continue
                # A channel component resolves a name in its own channel first, then at instrument
                # level (Component.peer, #382): an instrument-level reference may name a sibling.
                scopes = ["channel", "instrument"] if section == "channel_components" else [where]
                if where == "channel":
                    scopes = ["channel"]
                pool = {k: v for scope in reversed(scopes) for k, v in declared[scope].items()}
                target = pool.get(name, False)
                targets[param] = target if isinstance(target, type) else None
                # A ref's target is a component type name (a str) or a Protocol the named
                # component's class must implement (engine.component: Acquisition, TraceSource).
                want = known.get(base) if isinstance(base, str) else None
                protocol = None if isinstance(base, str) else base
                scope_word = (
                    "每通道"
                    if scopes == ["channel"]
                    else "本通道或仪表级"
                    if "channel" in scopes
                    else "仪表级"
                )
                if target is False:
                    if want is not None:
                        options = [n for n, c in pool.items() if c and issubclass(c, want)]
                        expectation, addition = f"一个 {base} 组件", f"添加一个 {base} 组件"
                    else:
                        assert protocol is not None
                        options = [n for n, c in pool.items() if c and not _protocol_gaps(c, protocol)]
                        expectation = f"一个实现 {protocol.__name__} 协议的组件"
                        addition = f"添加一个实现 {protocol.__name__} 协议的组件"
                    report.add(
                        "error",
                        "E-DEF-COMPONENT-REF",
                        _ptr(section, i, "params", param),
                        f"{spec.name}.{param} 应指向{expectation}（{scope_word}），"
                        + (f"没有名为 {name!r} 的组件" if name else "没有填写"),
                        f"改为 {options[0]!r}" if options else addition,
                        spec.name,
                    )
                elif isinstance(target, type) and want is not None and not issubclass(target, want):
                    report.add(
                        "error",
                        "E-DEF-COMPONENT-REF",
                        _ptr(section, i, "params", param),
                        f"{spec.name}.{param} 指向的 {name!r} 是 {target.type_name}，应为 {base}",
                        None,
                        spec.name,
                    )
                elif (
                    isinstance(target, type)
                    and protocol is not None
                    and (gaps := _protocol_gaps(target, protocol))
                ):
                    report.add(
                        "error",
                        "E-DEF-COMPONENT-REF",
                        _ptr(section, i, "params", param),
                        f"{spec.name}.{param} 指向的 {name!r} 是 {target.type_name}，"
                        f"应实现 {protocol.__name__} 协议（缺 {', '.join(gaps)}）",
                        None,
                        spec.name,
                    )
            for key, message in cls.ref_problems(spec.params, targets):
                report.add(
                    "error", "E-DEF-PARAM-VALUE", _ptr(section, i, "params", key), message, None, spec.name
                )


def _check_options(d: InstrumentDefinition, known: dict[str, type[Component]], report: Report) -> None:
    """Options (选件, #1004): ids once each, parameters of declared components (checked like the
    components' own), and commands that name a declared option."""
    specs = {c.name: (c, False) for c in d.components} | {c.name: (c, True) for c in d.channel_components}
    ids = [o.id for o in d.options]
    for i, option in enumerate(d.options):
        if option.id in ids[:i]:
            report.add(
                "error",
                "E-DEF-OPTION",
                _ptr("options", i, "id"),
                f"选件 {option.id!r} 重复",
                "为其中一个换一个名称，或合并两项",
                option.id,
            )
        for cname, params in option.params.items():
            if cname not in specs:
                unknown_name(
                    report,
                    "E-DEF-OPTION",
                    _ptr("options", i, "params", cname),
                    f"选件 {option.id} 指向不存在的组件 {cname!r}",
                    cname,
                    list(specs),
                    option.id,
                )
                continue
            spec, channel = specs[cname]
            cls = known.get(spec.type)
            if cls is not None:  # an unknown type is reported with the component
                fitted = spec.model_copy(update={"params": params})
                _check_params(d, fitted, cls, ("options", i, "params", cname), channel, report, at=())
    for i, cmd in enumerate(d.commands):
        if cmd.option is not None and cmd.option not in ids:
            unknown_name(
                report,
                "E-DEF-OPTION",
                _ptr("commands", i, "option"),
                f"命令 {cmd.header!r} 所属的选件 {cmd.option!r} 没有在 options 中声明",
                cmd.option,
                ids,
                cmd.header,
                fallback="在 options 中声明这个选件" if not ids else None,
            )


def _check_deviation_cases(d: InstrumentDefinition, report: Report) -> None:
    """Deviations for some settings (D70): instrument-wide settings that exist and values they can
    take, and no two deviations of one parameter for the same settings (or two without any)."""
    seen: dict[tuple[str, str], int] = {}
    for i, dev in enumerate(d.deviations):
        for path, wanted in (dev.when or {}).items():
            ptr = _ptr("deviations", i, "when", path)
            spec = d.state.get(path)
            if spec is None:
                unknown_name(
                    report,
                    "E-DEF-DEVIATION-WHEN",
                    ptr,
                    f"个体偏差的条件引用的设置 {path!r} 不存在（条件只用整机设置，不用按通道的设置）",
                    path,
                    list(d.state),
                )
                continue
            for value in wanted if isinstance(wanted, list) else [wanted]:
                try:
                    validate_value(spec, value, allow_readonly=True)
                except ScpiError:
                    report.add(
                        "error",
                        "E-DEF-DEVIATION-WHEN",
                        ptr,
                        f"个体偏差的条件里 {path} 的取值 {value!r} 不是这个设置能取的值",
                        "写成设置的选项之一，或在它的范围内",
                        path,
                    )
        declared = [o.id for o in d.options]
        if dev.option is not None and dev.option not in declared:
            unknown_name(
                report,
                "E-DEF-OPTION",
                _ptr("deviations", i, "option"),
                f"个体偏差属于未声明的选件 {dev.option!r}",
                dev.option,
                declared,
            )
        key = (dev.param, case_label(dev.when))
        if key in seen:
            report.add(
                "error",
                "E-DEF-DEVIATION-WHEN",
                _ptr("deviations", i, "when" if dev.when else "param"),
                f"{dev.param} 已有一条{'同样条件的' if dev.when else '不带条件的'}个体偏差（第 {seen[key] + 1} 条）",
                "每组设置只写一条；不带 when 的一条覆盖其余情况",
                dev.param,
            )
        else:
            seen[key] = i


def _check_deviations(d: InstrumentDefinition, known: dict[str, type[Component]], report: Report) -> None:
    """Individual deviations: a parameter of a declared component."""
    _check_deviation_cases(d, report)
    specs_by_name = {c.name: c for c in (*d.components, *d.channel_components)}
    for i, dev in enumerate(d.deviations):
        p = ("deviations", i, "param")
        spec = specs_by_name.get(dev.component)
        cls = known.get(spec.type) if spec else None
        if spec is None:
            unknown_name(
                report,
                "E-DEF-DEVIATION",
                _ptr(*p),
                f"没有组件 {dev.component!r}，无法给它个体偏差",
                dev.component,
                list(specs_by_name),
            )
            continue
        if cls is None:
            continue  # reported with the component
        if dev.parameter not in cls.defaults:
            unknown_name(
                report,
                "E-DEF-DEVIATION",
                _ptr(*p),
                f"{spec.type} 没有参数 {dev.parameter!r}",
                dev.parameter,
                list(cls.defaults),
                hint=lambda h, comp=dev.component: f"改为 {comp}.{h}",
            )
            continue
        nominal = spec.params.get(dev.parameter, cls.defaults[dev.parameter])
        if (dev.bands is not None) != (dev.parameter in cls.curves):
            curve = dev.parameter in cls.curves
            report.add(
                "error",
                "E-DEF-DEVIATION-BANDS",
                _ptr("deviations", i, "bands" if dev.bands is not None else "param"),
                f"{dev.param} 是随频率变化的曲线，个体偏差要按频段写"
                if curve
                else f"{dev.param} 不是随频率变化的曲线，不能按频段给个体偏差",
                "写 bands: [[f_lo, f_hi], …]，limit 为一个数或每段一个"
                if curve
                else "去掉 bands；按表抽取用 by",
            )
        elif dev.bands is not None:
            continue
        elif isinstance(nominal, bool) or not isinstance(nominal, (int, float, dict, str)):
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
