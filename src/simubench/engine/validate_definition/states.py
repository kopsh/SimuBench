"""Checks of the settings: the words a state accepts, initial values and the constraints between settings (D69)."""

from __future__ import annotations

import re
from typing import Any

from ...models.definition import InstrumentDefinition, StateSpec
from ...scpi import values as V
from ...scpi.errors import ScpiError
from ..component import Component, binding_of
from ..constraints import broken
from ..settings import validate_value
from ..validate import Report, _ptr, unknown_name


def _setting_key(ref: str) -> tuple[bool, str]:
    """A state binding as (per channel, name): ``ch.x``, ``ch{n}.x`` and ``ch2.x`` are channel ``x``."""
    m = re.fullmatch(r"ch(?:\d+|\{n\})?\.(.+)", ref)
    return (True, m.group(1)) if m else (False, ref)


def _check_words(d: InstrumentDefinition, known: dict[str, type[Component]], report: Report) -> None:
    """``words`` (#1115): every resolver a setting names is one a component bound to that setting
    implements (``Component.resolvers``), and ``special`` has no two keys that are one word."""
    bound: dict[tuple[bool, str], list[type[Component]]] = {}
    for specs in (d.components, d.channel_components):
        for spec in specs:
            cls = known.get(spec.type)
            if cls is None:
                continue
            for value in spec.params.values():
                ref = binding_of(value)
                if ref is not None:
                    bound.setdefault(_setting_key(ref), []).append(cls)
    for table, channel in ((d.state, False), (d.channel_state, True)):
        for path, st in table.items():
            keys = list((st.special or {}).keys())
            for i, a in enumerate(keys):
                for b in keys[i + 1 :]:
                    if V.matches_word(a, b):
                        report.add(
                            "error",
                            "E-DEF-SPECIAL-DUPLICATE",
                            _ptr("channel_state" if channel else "state", path, "special", b),
                            f"special 的键 {a!r} 与 {b!r} 是同一个词（长短形式、大小写只算一个）",
                            f"只留一个，写成混合大小写的 {a if len(a) >= len(b) else b!r}",
                            path,
                        )
            if not st.words:
                continue
            classes = bound.get((channel, path), [])
            offered = sorted({r for c in classes for r in c.resolvers})
            for word, resolver in st.words.items():
                ptr = _ptr("channel_state" if channel else "state", path, "words", word)
                if not classes:
                    report.add(
                        "error",
                        "E-DEF-WORDS-UNBOUND",
                        ptr,
                        f"设置 {path} 声明了 words，但没有组件的参数绑定它，写这个词只会报 -224",
                        "把设置绑定到实现了该解析器的组件的参数，或去掉 words",
                        path,
                    )
                elif resolver not in offered:
                    unknown_name(
                        report,
                        "E-DEF-WORDS-RESOLVER",
                        ptr,
                        f"绑定 {path} 的组件都不实现解析器 {resolver!r}",
                        resolver,
                        offered,
                        path,
                    )


#: What the runtime stores for a setting with no ``default`` (``Settings.reset``): enums take their
#: first option, the rest these.
_EMPTY_INITIAL = {"float": 0.0, "int": 0, "bool": False, "string": ""}


def _initial(spec: StateSpec, value: Any) -> Any:
    if value is not None:
        return value
    if spec.type == "enum":
        return (spec.options or [""])[0]
    return _EMPTY_INITIAL.get(spec.type)


def _check_initial_values(d: InstrumentDefinition, report: Report) -> None:
    """A setting's power-on value (``default``) and ``*RST`` value (``rst``) are values the setting
    itself accepts: in range, a ``special`` keyword's number, an option, or what the definition
    declares as "not given" (``unset``; an enum's ``""``). A missing ``default`` counts as the value
    the runtime stores for it (0, the first option). This is what catches a range edited past the
    default, or a ``default`` or ``special`` keyword lost while a copy was edited (#P0: the UXR1104B
    copy's ``display.persistence`` powered on at 0 s under its 0.1 s minimum)."""
    for section, specs in (("state", d.state), ("channel_state", d.channel_state or {})):
        for path, spec in specs.items():
            for field_name in ("default", "rst"):
                raw = getattr(spec, field_name)
                if raw is None and field_name == "rst":
                    continue  # *RST falls back to the power-on value, checked as default
                value = _initial(spec, raw)
                if value is None or (spec.type == "enum" and value == ""):
                    continue
                if spec.unset is not None and value == spec.unset:
                    continue
                try:
                    validate_value(spec, value, allow_readonly=True)
                except (ScpiError, TypeError, ValueError):
                    shown = "未写（按 " + repr(value) + "）" if raw is None else repr(raw)
                    report.add(
                        "error",
                        "E-DEF-STATE-INITIAL",
                        _ptr(section, path, field_name),
                        f"设置 {path} 的{'上电值 default' if field_name == 'default' else ' *RST 值 rst'} "
                        f"{shown} 不是它自己接受的值（{_accepted(spec)}）",
                        "改成范围内的值、一个 special 关键字的数值，或用 unset 声明“未给出”的数值",
                        f"state:{path}",
                    )


def _accepted(spec: StateSpec) -> str:
    if spec.type in ("float", "int"):
        parts = [
            f"{spec.min if spec.min is not None else '-∞'} … {spec.max if spec.max is not None else '∞'}"
        ]
        if spec.special:
            parts.append("special " + ", ".join(spec.special))
        return "；".join(parts)
    if spec.type == "enum":
        return "选项 " + ", ".join(spec.options or [])
    return spec.type


def _check_constraints(d: InstrumentDefinition, report: Report) -> None:
    """Constraints (#1013): instrument-wide settings that exist, values they can hold, a coerced
    value that is not itself forbidden, and power-on and *RST states that keep every constraint."""
    for i, c in enumerate(d.constraints):
        groups: list[tuple[str, dict[str, Any]]] = [("when", c.when), ("forbid", c.forbid)]
        if c.coerce is not None:
            groups.append(("coerce", c.coerce))
        for field, table in groups:
            for path, wanted in table.items():
                ptr = _ptr("constraints", i, field, path)
                spec = d.state.get(path)
                if spec is None:
                    unknown_name(
                        report,
                        "E-DEF-CONSTRAINT",
                        ptr,
                        f"约束引用的设置 {path!r} 不存在（约束只用整机设置，不用按通道的设置）",
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
                            "E-DEF-CONSTRAINT",
                            ptr,
                            f"约束里 {path} 的取值 {value!r} 不是这个设置能取的值",
                            "写成设置的选项之一，或在它的范围内",
                            path,
                        )
        for path, value in (c.coerce or {}).items():
            if value in c.forbid.get(path, []):
                report.add(
                    "error",
                    "E-DEF-CONSTRAINT",
                    _ptr("constraints", i, "coerce", path),
                    f"coerce 把 {path} 改成的 {value!r} 本身是被禁止的取值",
                    "改成允许的取值",
                    path,
                )
    if d.constraints:
        for label, state in (
            ("开机", {p: s.default for p, s in d.state.items()}),
            ("*RST 后", {p: (s.rst if s.rst is not None else s.default) for p, s in d.state.items()}),
        ):
            c = broken(d.constraints, d.state, state)
            if c is not None:
                report.add(
                    "error",
                    "E-DEF-CONSTRAINT",
                    _ptr("constraints", d.constraints.index(c)),
                    f"{label}的设置就违反这条约束",
                    "调整相关设置的 default / rst，使初始状态满足约束",
                    None,
                )
