"""The instrument screen's controls (tui-screens.md 5.2–5.3) as data, plus number input.

A :class:`Control` is one focusable thing: a switch, a select, a number, a panel button group
member, or a softkey. Screens render ``[label]`` for each and ask this module how to act on the
keys. Writes go through the app's client with the tui header; everything here is pure.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .fmt import as_number, fmt, fmt_int
from .i18n import t

#: Prefixes a number input may carry (``5000 mV``, ``1 kHz``); case matters for m/M.
_PREFIXES = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "M": 1e6,
    "G": 1e9,
}

_NUMBER = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(\S*)$")


def _norm_unit(unit: str | None) -> str:
    return (unit or "").strip().lower().replace("Ω", "ohm").replace("µ", "u").replace("μ", "u")


def parse_number(text: str, unit: str | None = None) -> float:
    """``5``, ``5 V``, ``5000 mV``, ``5e0`` → a value in the state's unit (tui-screens.md 10.2).

    A bare number is taken as the state's unit; a suffixed one is converted when its unit is
    the same quantity (a prefix plus the state's unit). Anything else raises :class:`ValueError`.
    """
    m = _NUMBER.match(text.strip())
    if m is None:
        raise ValueError("not a number")
    value = float(m.group(1))
    given = m.group(2)
    if not given:
        return value
    mine = _norm_unit(unit)
    if not mine:
        raise ValueError("no unit")
    # One leading prefix, then the unit core; ``5e0``-style numbers never reach here (regex \S*).
    core = given
    scale = 1.0
    if len(given) > 1 and given[0] in _PREFIXES:
        candidate = _norm_unit(given[1:])
        if candidate == mine:
            scale, core = _PREFIXES[given[0]], given[1:]
    if core != given and _norm_unit(core) == mine:
        return value * scale
    if _norm_unit(given) == mine:
        return value
    raise ValueError(f"unit {given}")


@dataclass
class Control:
    """One focusable control of the instrument screen (tui-screens.md 5.3)."""

    kind: str  # switch | select | number | buttons | softkey
    label: str
    path: str | None = None  # the state path it reads and writes
    value: Any = None  # the current value (state); buttons: the value their key writes
    spec: Mapping[str, Any] | None = None  # the StateVarInfo, for range / options / unit
    step: float | None = None  # the panel's step for ``+``/``-``
    unit: str | None = None
    keys: list[Mapping[str, Any]] = field(default_factory=list)  # buttons: the PanelKeys
    action: str | None = None  # softkey: the panel action (run / stop / single)
    sets: Mapping[str, Any] = field(default_factory=dict)  # further settings written along
    dangerous: bool = False
    electrical: bool = False
    readonly: bool = False

    @property
    def writable(self) -> bool:
        return not self.readonly and (self.path is not None or self.action is not None)


def spec_of(info: Mapping[str, Any], path: str | None) -> Mapping[str, Any] | None:
    if path is None:
        return None
    return next((sv for sv in info.get("state", []) if sv.get("path") == path), None)


def value_of(info: Mapping[str, Any], path: str | None) -> Any:
    sv = spec_of(info, path)
    return None if sv is None else sv.get("value")


def label_of(sv: Mapping[str, Any], lang: str, fallback: str = "") -> str:
    return sv.get("label" if lang == "zh" else "label_en") or fallback or sv.get("path", "")


def _latin(text: Any) -> str | None:
    """``text`` when it is written in Latin script, else None: the terminal shows no CJK (D23)."""
    if not text:
        return None
    text = str(text)
    return text if all(ord(c) < 0x2E80 for c in text) else None


def _control_for(path: str, spec: Mapping[str, Any], lang: str, step: float | None = None) -> Control:
    """The control one state variable is set with (tui-screens.md 5.3)."""
    kind = {"bool": "switch", "enum": "select", "float": "number", "int": "number"}.get(
        spec.get("type"), "number"
    )
    return Control(
        kind=kind,
        label=label_of(spec, lang, fallback=path.rsplit(".", 1)[-1]),
        path=path,
        value=spec.get("value"),
        spec=spec,
        step=step,
        unit=spec.get("display_unit") or spec.get("unit"),
        dangerous=bool(spec.get("dangerous")),
        electrical=bool(spec.get("electrical")),
        readonly=bool(spec.get("readonly")),
    )


# ---- per-template control sets (tui-screens.md 5.2) -----------------------------------------------


def smu_controls(info: Mapping[str, Any], lang: str) -> list[Control]:
    """输出、功能、电压设定、电流设定、电流限值、电压限值."""
    paths = [
        "output.enabled",
        "source.function",
        "source.voltage.level",
        "source.current.level",
        "compliance.current.limit",
        "compliance.voltage.limit",
    ]
    out: list[Control] = []
    for path in paths:
        spec = spec_of(info, path)
        if spec is not None:
            out.append(_control_for(path, spec, lang))
    return out


def generator_controls(info: Mapping[str, Any], lang: str, channel: int | None = None) -> list[Control]:
    """每通道：输出、波形、频率、幅度、偏置、负载 (only ``channel``'s when it is given: each
    channel box carries its own buttons, #283)."""
    channels = sorted({sv.get("channel") for sv in info.get("state", []) if sv.get("channel")})
    if channel is not None:
        channels = [ch for ch in channels if ch == channel]
    suffixes = ["output", "function", "frequency", "amplitude", "offset", "load"]
    out: list[Control] = []
    for ch in channels:
        for suffix in suffixes:
            path = f"ch{ch}.{suffix}"
            spec = spec_of(info, path)
            if spec is not None and not spec.get("readonly"):
                out.append(_control_for(path, spec, lang))
    return out


def generic_controls(info: Mapping[str, Any], lang: str) -> list[Control]:
    """The generic template: one control per writable state variable."""
    return [
        _control_for(sv["path"], sv, lang)
        for sv in info.get("state", [])
        if not sv.get("readonly") and sv.get("type") in ("bool", "enum", "float", "int")
    ]


def panel_controls(group: Mapping[str, Any], info: Mapping[str, Any], lang: str) -> list[Control]:
    """A panel group's controls (tui-screens.md 5.2): one entry per control and per key."""
    out: list[Control] = []
    for pc in group.get("controls", []):
        path = pc.get("state")
        spec = spec_of(info, path)
        kind = pc.get("control")
        if kind == "buttons":
            for key in pc.get("keys") or []:
                value = key.get("value")
                key_spec = spec or {}
                if spec is not None and isinstance(value, str) and spec.get("type") == "enum":
                    key_spec = spec
                # A key labelled only in Chinese (the definitions' option_labels) reads the enum's
                # own word instead — the same mnemonic the rows and selects show (D23, #454).
                label = (
                    key.get("label" if lang == "zh" else "label_en")
                    or _latin(key.get("label"))
                    or str(value)
                )
                out.append(
                    Control(
                        kind="buttons",
                        label=label,
                        path=path,
                        value=value,
                        spec=key_spec,
                        sets=key.get("sets") or {},
                        dangerous=bool((spec or {}).get("dangerous")),
                        electrical=bool((spec or {}).get("electrical")),
                    )
                )
        elif spec is None:
            continue
        else:
            control = _control_for(path, spec, lang, step=pc.get("step"))
            if pc.get("unit"):
                control.unit = pc["unit"]
            # An explicitly declared label only in Chinese gives way to the setting's own (D23).
            override = pc.get("label" if lang == "zh" else "label_en") or _latin(pc.get("label"))
            if override:
                control.label = override
            out.append(control)
    return out


def softkey_controls(panel: Mapping[str, Any], lang: str) -> list[Control]:
    """The panel's softkeys (tui-screens.md 5.3): actions go to POST /panel, states to configure."""
    out: list[Control] = []
    for sk in panel.get("softkeys", []):
        # A softkey labelled only in Chinese reads its action's (or state's) name instead (D23, #454).
        label = (
            sk.get("label" if lang == "zh" else "label_en")
            or _latin(sk.get("label"))
            or str(sk.get("action") or sk.get("state") or "")
        )
        out.append(
            Control(
                kind="softkey",
                label=label,
                path=sk.get("state"),
                value=sk.get("value"),
                action=sk.get("action"),
                sets=sk.get("sets") or {},
            )
        )
    return out


# ---- acting on a control ---------------------------------------------------------------------------


def control_state(info: Mapping[str, Any], control: Control) -> Any:
    """The control's current value from the (possibly newer) instrument info."""
    return value_of(info, control.path)


def is_lit(info: Mapping[str, Any], control: Control) -> bool:
    """A ``buttons`` control is lit while its value is set and its ``sets`` hold (7.3)."""
    if control.kind != "buttons" or control.path is None:
        return False
    if control_state(info, control) != control.value:
        return False
    return all(value_of(info, p) == v for p, v in control.sets.items())


def apply_sets(control: Control, value: Any = None) -> list[tuple[str, Any]]:
    """The (path, value) writes a control performs: its own path, then its ``sets``."""
    writes: list[tuple[str, Any]] = []
    if control.path is not None:
        writes.append((control.path, control.value if value is None else value))
    for path, v in control.sets.items():
        writes.append((path, v))
    return writes


def ladder_values(spec: Mapping[str, Any]) -> list[float]:
    """The 1-2-5 ladder between the spec's min and max, for paths ending ``.scale`` and friends."""
    lo = float(spec.get("min") or 0.0)
    hi = float(spec.get("max") or 0.0)
    if hi <= lo:
        return []
    out: list[float] = []
    k = math.floor(math.log10(lo)) - 1
    while k < math.log10(hi) + 2:
        for m in (1.0, 2.0, 5.0):
            value = m * 10.0**k
            if lo - 1e-12 * abs(value) <= value <= hi + 1e-12 * abs(value):
                out.append(value)
        k += 1
    return out


def step_for(control: Control) -> float | None:
    """``+``/``-``: the panel's step, else the 1-2-5 ladder for ``.scale`` paths, else None."""
    if control.step:
        return control.step
    if control.path and control.path.endswith(".scale") and control.spec:
        values = ladder_values(control.spec)
        return values[0] if values else None
    return None


def ladder_nudge(value: float, spec: Mapping[str, Any], direction: int) -> float:
    """The next (``+``) or previous (``-``) value of the 1-2-5 ladder, clamped to the range."""
    values = ladder_values(spec)
    if not values:
        return value
    if value <= 0:
        return values[0]
    for v in values:
        if v > value * (1 + 1e-9):
            if direction > 0:
                return v
            break
    for v in reversed(values):
        if v < value * (1 - 1e-9):
            if direction < 0:
                return v
            break
    return values[-1] if direction < 0 else values[0]


def nudge(control: Control, info: Mapping[str, Any], direction: int) -> float | None:
    """The value ``+``/``-`` writes, or None when the control has no step (按 e 输入数值)."""
    spec = control.spec or {}
    value = control_state(info, control)
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float)):
        return None
    value = float(value)
    if control.step:
        lo = spec.get("min")
        hi = spec.get("max")
        out = value + direction * control.step
        if lo is not None and out < float(lo):
            out = float(lo)
        if hi is not None and out > float(hi):
            out = float(hi)
        return out
    if control.path and control.path.endswith(".scale"):
        return ladder_nudge(value, spec, direction)
    return None


def range_text(spec: Mapping[str, Any]) -> str:
    """``-210.000 V … 210.000 V``: a number control's declared range (tui-screens.md 10.2)."""
    unit = spec.get("display_unit") or spec.get("unit")
    lo, hi = spec.get("min"), spec.get("max")
    if lo is None and hi is None:
        return ""
    if spec.get("type") == "int":
        return f"{lo if lo is not None else '−∞'} … {hi if hi is not None else '∞'}" + (
            f" {unit}" if unit else ""
        )
    parts = []
    for v in (lo, hi):
        if v is None:
            parts.append("∞" if v is hi else "−∞")
        else:
            parts.append(fmt(float(v), unit, digits=6))
    return f"{parts[0]} … {parts[1]}"


def options_of(spec: Mapping[str, Any]) -> list[tuple[str, str]]:
    """An enum's options as the instrument spells them: the definitions' ``option_labels`` are
    Chinese and the terminal console is English only (D23)."""
    return [(o, o) for o in spec.get("options") or []]


def summary_text(value: Any, spec: Mapping[str, Any] | None, lang: str) -> str:
    """A state value as the channel rows show it: 开/关, option labels, or an engineering number."""
    if isinstance(value, bool):
        return t("common.on" if value else "common.off", lang)
    value = as_number(value)
    if spec and spec.get("type") == "int" and isinstance(value, (int, float)) and math.isfinite(value):
        return fmt_int(value, spec.get("display_unit") or spec.get("unit"))
    if spec and spec.get("type") == "enum" and isinstance(value, str):
        return value  # the mnemonic: option_labels are Chinese, the TUI is English only (D23)
    if spec and isinstance(value, (int, float)) and not isinstance(value, bool):
        unit = spec.get("display_unit") or spec.get("unit")
        return fmt(float(value), unit)
    if value is None:
        return "—"
    return str(value)


def inactive_under_auto(state: Mapping[str, Mapping[str, Any]], path: str) -> bool:
    """A manual setting whose automatic switch (``<path>_auto``) is on: the stored value is not
    the one in effect, so the rows read ``AUTO`` (docs/ui-design.md, #279/#283)."""
    auto = state.get(f"{path}_auto")
    return bool(auto) and auto.get("type") == "bool" and auto.get("value") is True


def channel_paths(info: Mapping[str, Any], channel: int | None) -> list[str]:
    """The state paths of one channel (None: the whole instrument's unchannelled states)."""
    return [sv["path"] for sv in info.get("state", []) if sv.get("channel") == channel]
