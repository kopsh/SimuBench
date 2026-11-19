"""Checks of the front panel (instrument-authoring.md 7.3)."""

from __future__ import annotations

from typing import Any

from ...models.definition import InstrumentDefinition, PanelControl, StateSpec
from ...models.panels import DEDICATED, template_of
from ...scpi import values as V
from ..validate import Report, _ptr, unknown_name
from .lookups import _state_options, _state_spec

_CONTROL_TYPES = {
    "switch": ("bool",),
    "select": ("enum",),
    "number": ("float", "int"),
    "buttons": ("enum", "bool", "float", "int", "string"),
}


#: A model with more settings than this, whole-instrument or per channel, declares its panel (D74):
#: without one the inspector shows only the summary, which the canvas card also uses.
PANEL_EXPECTED_GLOBAL = 100


PANEL_EXPECTED_CHANNEL = 40


def _check_panel(d: InstrumentDefinition, report: Report) -> None:
    panel = d.display.panel
    if panel is None:
        if len(d.state) > PANEL_EXPECTED_GLOBAL or len(d.channel_state) > PANEL_EXPECTED_CHANNEL:
            report.add(
                "warning",
                "W-DEF-PANEL-MISSING",
                _ptr("display"),
                f"整机 {len(d.state)} 个、每通道 {len(d.channel_state)} 个设置却没有声明面板：检查器只能放 display.summary（画布卡片也用它），"
                "其余要靠查找",
                "在 display.panel.groups（与 channel_groups）中写出前面板先给的设置",
                "panel",
            )
        return
    base = ("display", "panel")
    template = template_of(d)
    if template in DEDICATED and (panel.readouts or panel.softkeys):
        report.add(
            "warning",
            "W-DEF-PANEL-IGNORED",
            _ptr(*base),
            f"{template} 有专用屏幕，面板的读数与软键不用，只有分组放到检查器上",
            "去掉 display.panel 的 readouts 与 softkeys",
            "panel",
        )
    components = {c.name for c in d.components} | {
        f"ch{n}.{c.name}" for c in d.channel_components for n in range(1, d.channels.count + 1)
    }

    def setting(path: str, pointer: str) -> StateSpec | None:
        spec, problem = _state_spec(d, path, False)
        if problem:
            unknown_name(
                report, "E-DEF-PANEL-STATE", pointer, problem, path, _state_options(d, False), "panel"
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
                # a `when` value may be a list: the condition holds for any of them (7.3)
                for item in v if isinstance(v, list) else [v]:
                    value(path, spec, item, _ptr(*pointer, path))

    for i, readout in enumerate(panel.readouts):
        p = (*base, "readouts", i)
        comp, _, name = readout.reading.rpartition(".")
        if not comp or not name or comp not in components:
            unknown_name(
                report,
                "E-DEF-PANEL-READING",
                _ptr(*p, "reading"),
                f"读数 {readout.reading!r} 应写作 <组件>.<读数>，"
                + (f"没有组件 {comp!r}" if comp else "缺少组件名"),
                comp or readout.reading,
                sorted(components),
                "panel",
                hint=lambda h, n=name or "DC": f"改为 {h}.{n}",
            )
        settings(readout.when, (*p, "when"))

    if panel.channel_groups and not d.channel_state:
        report.add(
            "error",
            "E-DEF-PANEL-STATE",
            _ptr(*base, "channel_groups"),
            "定义没有通道设置（channel_state），channel_groups 无处可放",
            "把这些控件移到 groups，写完整的设置路径",
            "panel",
        )
    for key, prefix in (("groups", ""), ("channel_groups", "ch1.")):
        if key == "channel_groups" and not d.channel_state:
            break
        for g, group in enumerate(getattr(panel, key)):
            settings(_in_channel(group.when, prefix, d), (*base, key, g, "when"))
            for c, control in enumerate(group.controls):
                p = (*base, key, g, "controls", c)
                spec = setting(prefix + control.state, _ptr(*p, "state"))
                settings(_in_channel(control.when, prefix, d), (*p, "when"))
                if spec is not None:
                    _check_control(control, spec, p, report)
                    for k, key_ in enumerate(control.keys or []):
                        value(control.state, spec, key_.value, _ptr(*p, "keys", k, "value"))
                        settings(key_.sets, (*p, "keys", k, "sets"))

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


def _in_channel(when: dict[str, Any], prefix: str, d: InstrumentDefinition) -> dict[str, Any]:
    """A channel group's ``when``: a channel setting's name without its prefix reads as ch1's."""
    if not prefix:
        return when
    return {(k if k in d.state else prefix + k): v for k, v in when.items()}


def _check_control(control: PanelControl, spec: StateSpec, p: tuple[Any, ...], report: Report) -> None:
    if control.control is None:
        # The setting's own field; a readonly one cannot be set from a panel.
        if spec.readonly:
            report.add(
                "error",
                "E-DEF-PANEL-CONTROL",
                _ptr(*p, "state"),
                f"{control.state} 是只读状态，不能放控件",
                "把它作为读数显示，或去掉这个控件",
                "panel",
            )
        return
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
    """Whether a value the panel writes or compares fits the setting, and the fix when it does
    not: the same rule the engine applies (fit_value), except that an option must be written as
    itself — a panel key lights by comparing its value with the stored one, so a word form
    (``VOLT`` for ``VOLTage``) would write the setting and never light the key."""
    problem, fix = V.fit_value(spec, v)
    if problem is None and fix is not None:
        return f"{v!r} 要写作选项的原样 {fix!r}", f"改为 {fix!r}"
    return problem, fix
