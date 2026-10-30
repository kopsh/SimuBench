"""Front-panel layouts (instrument-authoring.md 7.3).

A definition may declare its panel (``display.panel``). One that does not gets its class's default
layout when the class has one: a multimeter is a large reading with function and range keys, a
calibrator its output value with operate / standby keys. Both are taken from the definition's own
components and the settings bound to them, so a new multimeter has the same panel as the 34465A
without writing one. Classes with a dedicated screen (oscilloscope, generator, ...) do not use a
panel; the others without one keep the generic layout (readings and the settings list).
"""

from __future__ import annotations

import math
from typing import Any, Literal

from ..models.definition import (
    ComponentSpec,
    InstrumentDefinition,
    Panel,
    PanelControl,
    PanelGroup,
    PanelKey,
    PanelReadout,
    PanelSoftkey,
    StateSpec,
)

# Templates the UI draws with a screen of their own; a panel is not used for them.
DEDICATED = (
    "oscilloscope",
    "function_generator",
    "lockin",
    "spectrum_analyzer",
    "smu",
    "electronic_load",
    "rf_generator",
)

PanelSource = Literal["declared", "class_default"]


def template_of(d: InstrumentDefinition) -> str:
    return d.display.template or d.instrument_class


def effective_panel(d: InstrumentDefinition) -> tuple[Panel | None, PanelSource | None]:
    """The panel the UI draws for this definition, and where it comes from."""
    template = template_of(d)
    if template in DEDICATED:
        return None, None
    if d.display.panel is not None:
        return d.display.panel, "declared"
    build = _DEFAULTS.get(template)
    panel = build(d) if build else None
    if panel is None or not (panel.readouts or panel.groups):
        return None, None
    return panel, "class_default"


# ---- class defaults ---------------------------------------------------------------------------------


def _multimeter(d: InstrumentDefinition) -> Panel | None:
    meter = _first(d, "dmm_core")
    if meter is None:
        return None
    fn_state = _bound(meter, "function")
    fn_spec = d.state.get(fn_state) if fn_state else None
    digits = meter.params.get("digits")
    digits = int(digits) if isinstance(digits, (int, float)) and not isinstance(digits, bool) else 7
    digits = min(digits, 8)

    # The main display: the reading of the selected function.
    readouts: list[PanelReadout] = []
    to_function = _function_map(meter, fn_spec)
    for function, label, label_en in (("DC", "直流电压", "DC voltage"), ("AC", "交流电压", "AC voltage")):
        when: dict[str, Any] = {}
        if fn_state and to_function:
            options = [o for o, f in to_function.items() if f == function]
            if not options:
                continue
            when = {fn_state: options[0]}
        readouts.append(
            PanelReadout(
                reading=f"{meter.name}.{function}",
                label=label,
                label_en=label_en,
                unit="V",
                digits=digits,
                when=when,
            )
        )

    groups: list[PanelGroup] = []
    used: set[str] = set()
    if fn_state and fn_spec and fn_spec.type == "enum" and not fn_spec.readonly:
        keys = [
            PanelKey(value=o, label=_FUNCTION_KEYS.get(to_function.get(o, "")) if to_function else None)
            for o in fn_spec.options or []
        ]
        groups.append(
            PanelGroup(
                label="测量",
                label_en="Measure",
                controls=[PanelControl(state=fn_state, control="buttons", keys=keys)],
            )
        )
        used.add(fn_state)

    # Range keys: one per full-scale value, and AUTO.
    range_state, auto_state = _bound(meter, "range"), _bound(meter, "autorange")
    range_spec = d.state.get(range_state) if range_state else None
    auto_spec = d.state.get(auto_state) if auto_state else None
    controls: list[PanelControl] = []
    if range_state and range_spec and range_spec.type in ("float", "int") and not range_spec.readonly:
        values = _ranges(meter.params.get("ranges"))
        if values:
            off = {auto_state: False} if auto_spec and auto_spec.type == "bool" else {}
            keys = [PanelKey(value=v, label=_eng(v, range_spec.unit or "V"), sets=off) for v in values]
            auto_value = (range_spec.special or {}).get("AUTO")
            if off and auto_value is not None:
                keys.append(PanelKey(value=auto_value, label="AUTO", sets={auto_state: True}))
                used.add(auto_state)  # type: ignore[arg-type]
            controls.append(PanelControl(state=range_state, control="buttons", keys=keys))
        else:
            controls.append(PanelControl(state=range_state, control="number"))
        used.add(range_state)
    if auto_state and auto_state not in used and auto_spec and auto_spec.type == "bool":
        controls.append(PanelControl(state=auto_state, control="switch"))
        used.add(auto_state)
    if controls:
        groups.append(PanelGroup(label="量程", label_en="Range", controls=controls))

    # Further settings of the measurement (NPLC, input impedance, AC filter) and of the trigger.
    settings = _controls(d, [meter], used)
    if settings:
        groups.append(PanelGroup(label="设置", label_en="Settings", controls=settings))
    trigger = _controls(d, [c for c in d.components if c.type == "trigger_model"], used)
    if trigger:
        groups.append(PanelGroup(label="触发", label_en="Trigger", controls=trigger))
    return Panel(readouts=readouts, groups=groups, softkeys=_action_keys(d))


def _calibrator(d: InstrumentDefinition) -> Panel | None:
    out = _first(d, "calibrator_output")
    if out is None:
        return None
    fn_state = _bound(out, "function")
    fn_spec = d.state.get(fn_state) if fn_state else None
    ac = {fn_state: "ACV"} if fn_state and fn_spec and "ACV" in (fn_spec.options or []) else None

    readouts = [
        PanelReadout(reading=f"{out.name}.level", label="输出", label_en="Output", unit="V", digits=7)
    ]
    if ac is not None or not fn_state:
        readouts.append(
            PanelReadout(
                reading=f"{out.name}.frequency",
                label="频率",
                label_en="Frequency",
                unit="Hz",
                digits=6,
                when=ac or {},
            )
        )
    readouts.append(PanelReadout(reading=f"{out.name}.output", label="输出端", label_en="Terminal"))

    used: set[str] = set()
    controls: list[PanelControl] = []
    if fn_state and fn_spec and fn_spec.type == "enum" and not fn_spec.readonly:
        controls.append(PanelControl(state=fn_state, control="buttons"))
        used.add(fn_state)
    for param in ("level", "frequency"):
        path = _bound(out, param)
        spec = d.state.get(path) if path else None
        if path and spec and spec.type in ("float", "int") and not spec.readonly and path not in used:
            controls.append(PanelControl(state=path, control="number"))
            used.add(path)
    groups = [PanelGroup(label="输出", label_en="Output", controls=controls)] if controls else []

    operate = _bound(out, "operate")
    op_spec = d.state.get(operate) if operate else None
    if operate and op_spec and op_spec.type == "bool" and not op_spec.readonly:
        keys = [PanelKey(value=True, label="OPER"), PanelKey(value=False, label="STBY")]
        groups.append(
            PanelGroup(
                label="工作 / 待机",
                label_en="Operate / Standby",
                controls=[PanelControl(state=operate, control="buttons", keys=keys)],
            )
        )
    return Panel(readouts=readouts, groups=groups, softkeys=_action_keys(d))


_DEFAULTS = {"multimeter": _multimeter, "calibrator": _calibrator}
_FUNCTION_KEYS = {"DC": "DC V", "AC": "AC V"}


# ---- helpers -----------------------------------------------------------------------------------------


def _first(d: InstrumentDefinition, type_name: str) -> ComponentSpec | None:
    return next((c for c in d.components if c.type == type_name), None)


def _bound(spec: ComponentSpec, param: str) -> str | None:
    """The instrument-level setting a component parameter is bound to, if any."""
    value = spec.params.get(param)
    if isinstance(value, dict):
        value = value.get("from")
    if isinstance(value, str) and value.startswith("$") and not value.startswith("$ch"):
        return value[1:]
    return None


def _function_map(meter: ComponentSpec, fn_spec: StateSpec | None) -> dict[str, str]:
    """Setting value -> the meter's function (DC / AC)."""
    if fn_spec is None or fn_spec.type != "enum":
        return {}
    value = meter.params.get("function")
    mapping = value.get("map") if isinstance(value, dict) else None
    out: dict[str, str] = {}
    for option in fn_spec.options or []:
        target = (mapping or {}).get(option, option)
        if isinstance(target, str) and target.upper() in ("DC", "AC"):
            out[option] = target.upper()
    return out


def _ranges(value: Any) -> list[float]:
    """The full-scale values of a meter's ``ranges`` (one list, or one per function)."""
    lists = value.values() if isinstance(value, dict) else [value] if isinstance(value, list) else []
    found = {float(v) for lst in lists if isinstance(lst, list) for v in lst if isinstance(v, (int, float))}
    return sorted(found)


def _controls(d: InstrumentDefinition, specs: list[ComponentSpec], used: set[str]) -> list[PanelControl]:
    """A control for each writable setting the components' parameters are bound to."""
    out: list[PanelControl] = []
    for spec in specs:
        for param in spec.params:
            path = _bound(spec, param)
            st = d.state.get(path) if path else None
            if not path or st is None or st.readonly or path in used:
                continue
            kind = {"bool": "switch", "enum": "select", "float": "number", "int": "number"}.get(st.type)
            if kind is None:
                continue
            out.append(PanelControl(state=path, control=kind))  # type: ignore[arg-type]
            used.add(path)
    return out


def _action_keys(d: InstrumentDefinition) -> list[PanelSoftkey]:
    names = {"run": ("运行", "Run"), "stop": ("停止", "Stop"), "single": ("单次", "Single")}
    return [
        PanelSoftkey(label=names[a][0], label_en=names[a][1], action=a)  # type: ignore[arg-type]
        for a in d.display.actions
        if a in names
    ]


_PREFIXES = ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"))


def _eng(value: float, unit: str) -> str:
    """100 mV, 1 kΩ: a range key's text."""
    unit = {"OHM": "Ω", "HZ": "Hz", "S": "s"}.get(unit.upper(), unit)
    if value == 0 or not math.isfinite(value):
        return f"{value:g} {unit}"
    for scale, prefix in _PREFIXES:
        if scale > 1 and unit in ("V", "A"):
            continue  # a meter's ranges read 1000 V, not 1 kV
        if abs(value) >= scale * 0.999999:
            return f"{value / scale:g} {prefix}{unit}"
    return f"{value:g} {unit}"
