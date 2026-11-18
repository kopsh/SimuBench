"""The signal source template (#144, instrument-authoring.md 7.4): a function generator's or a
calibrator's data sheet — waveforms, frequency and amplitude ranges, amplitude units, duty, load
setting, output impedance, reference clock — becomes the source modules (waveform_synth ->
amplitude_level -> output_stage, reference_clock). The command table is the model's own data,
written from its manual (D81: every instrument's SCPI is its own, there is no "dialect" to choose).
The duty is a ratio or a percentage as declared, never guessed from its size.
"""

from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..components.sources import AmplitudeLevel
from . import Drafting, InputProblem, Mnemonic, Template, register
from .oscilloscope import CommandsIn, IdentityIn, _any

Shape = Literal["sine", "square", "pulse", "ramp", "triangle", "dc"]
#: shape -> waveform_synth function and ramp symmetry
SHAPES: dict[str, tuple[str, float | None]] = {
    "sine": ("SINusoid", None),
    "square": ("SQUare", None),
    "pulse": ("SQUare", None),
    "ramp": ("RAMP", 100.0),
    "triangle": ("RAMP", 50.0),
    "dc": ("DC", None),
}


def _f(default: Any, title: str, title_en: str, description: str, unit: str | None = None, **kw: Any) -> Any:
    extra: dict[str, Any] = {"title_en": title_en}
    if unit:
        extra["unit"] = unit
    return Field(default, title=title, description=description, json_schema_extra=extra, **kw)


def _group(model: type[BaseModel], title: str, title_en: str, description: str) -> Any:
    return Field(
        default_factory=model, title=title, description=description, json_schema_extra={"title_en": title_en}
    )


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class WaveformIn(_In):
    name: str = _f(..., "关键字", "Keyword", "The command's keyword for this waveform, e.g. SINusoid, PULSe")
    shape: Shape = _f(
        ..., "形状", "Shape", "sine, square (50 % or the duty), pulse (the duty), ramp, triangle, dc"
    )


class RangeIn(_In):
    min: float = _f(..., "最小", "Minimum", "Smallest value the setting accepts")
    max: float = _f(..., "最大", "Maximum", "Largest value the setting accepts")
    default: float = _f(..., "上电值", "Power-on value", "Value at power-on")


class FrequencyIn(RangeIn):
    square_max: float | None = _f(
        None,
        "方波与斜波上限",
        "Square and ramp limit",
        "Highest square or ramp frequency; omit: the range",
        "Hz",
        gt=0,
    )
    bandwidth_hz: float = _f(
        100e6,
        "谐波带宽",
        "Harmonic bandwidth",
        "Harmonics of square, pulse and ramp kept up to (edge speed)",
        "Hz",
        gt=0,
    )


class AmplitudeIn(RangeIn):
    min: float = _f(
        ..., "最小", "Minimum", "Smallest amplitude, as Vpp into the load setting (D48); positive with dBm"
    )
    max: float = _f(..., "最大", "Maximum", "Largest amplitude, as Vpp into the load setting (D48)")
    default: float = _f(
        ..., "上电值", "Power-on value", "Value at power-on, in the power-on unit (the first one)"
    )
    units: list[Literal["VPP", "VPK", "VRMS", "DBM"]] = _f(
        ["VPP"], "幅度单位", "Amplitude units", "Units the amplitude may be written in, the power-on one first",
        min_length=1,
    )  # fmt: skip
    label: str | None = _f(None, "显示名", "Label", "How the panel names the amplitude, e.g. '幅度 (Vpp)'")


class DutyIn(RangeIn):
    unit: Literal["ratio", "percent"] = _f(
        ..., "表示", "Representation", "ratio: 0.25 is 25 %; percent: 25 is 25 % (0.1 is 0.1 %)"
    )


class LoadIn(RangeIn):
    high_z_keywords: list[str] = _f(
        [],
        "高阻关键字",
        "High-Z keywords",
        "Words the load command takes for high-Z, e.g. INFinity (the short and the long form both match)",
    )
    hiz_from: float | None = _f(
        None,
        "高阻阈值",
        "High-Z threshold",
        "A numeric load setting of this many ohms or more is high-Z",
        "Ω",
        gt=0,
    )


class OutputIn(_In):
    impedance_ohms: float = _f(50.0, "输出阻抗", "Output impedance", "Series output impedance", "Ω", gt=0)
    impedance_error_pct: float | None = _f(
        None,
        "输出阻抗误差限",
        "Output impedance limit",
        "± a unit's output impedance may deviate by",
        "%",
        ge=0,
    )
    dangerous: bool = _f(
        True, "开输出需确认", "Output needs confirm", "Turning an output on asks an agent to confirm"
    )


class ClockIn(_In):
    sources: list[str] = _f(
        [],
        "时钟源",
        "Clock sources",
        "Keywords of the clock source command, internal first; empty: no command",
    )
    ppm: float | None = _f(
        None, "时钟误差限", "Clock error limit", "± ppm a unit's internal clock may deviate by (individual deviation)",
        "ppm", ge=0,
    )  # fmt: skip
    ref_input: bool = _f(
        False, "参考输入", "Reference input", "A 10 MHz REF IN port the external source locks to"
    )


class SignalSourceInputs(_In):
    identity: IdentityIn = _f(..., "身份", "Identity", "Manufacturer, model, *IDN?")
    channels: int = _f(2, "通道数", "Channels", "Output channels", ge=1, le=8)
    port: str = _f(
        "OUT{n}", "输出端口名", "Output port name", "How the front panel names output {n}, e.g. OUT{n}, CH{n}"
    )
    waveforms: list[WaveformIn] = _f(
        ...,
        "波形",
        "Waveforms",
        "Waveforms the function command offers, the power-on one first",
        min_length=1,
    )
    frequency: FrequencyIn = _f(..., "频率", "Frequency", "Frequency range, Hz")
    amplitude: AmplitudeIn = _f(
        ..., "幅度", "Amplitude", "Amplitude range in the power-on unit, and the units"
    )
    offset: RangeIn = Field(
        default_factory=lambda: RangeIn(min=-5.0, max=5.0, default=0.0),
        title="偏置",
        description="Offset range, V (defined for the load setting like the amplitude)",
        json_schema_extra={"title_en": "Offset", "unit": "V"},
    )
    phase: RangeIn | None = _f(None, "相位", "Phase", "Phase range, degrees; omit: no phase command", "deg")
    duty: DutyIn | None = _f(None, "占空比", "Duty cycle", "Duty of square and pulse; omit: 50 %, no command")
    load: LoadIn = _f(..., "负载设定", "Load setting", "The load the amplitude is defined for, Ω (D13)")
    output: OutputIn = _group(OutputIn, "输出", "Output", "Output stage")
    clock: ClockIn = _group(ClockIn, "参考时钟", "Reference clock", "Clock source and its error")
    settings: dict[str, dict[str, Any]] = _f(
        {}, "其他设定", "Other settings",
        "Settings the commands keep that no behaviour reads (a beeper, a display option): path -> the "
        "definition's setting",
    )  # fmt: skip
    syntax: dict[str, Any] = _f(
        {}, "报文写法", "Message syntax",
        "The definition's syntax: header echo, long form, number suffixes; empty: SCPI's",
    )  # fmt: skip
    format: dict[str, Any] = _f(
        {}, "应答格式", "Response format", "The definition's response format; empty: the default"
    )
    commands: CommandsIn = _group(CommandsIn, "命令", "Commands", "This model's command table")
    specs: dict[str, Any] = _f(
        {},
        "技术指标",
        "Specifications",
        "The data sheet's figures as written there: a record, not used to compute",
    )


# ---- checks the schema cannot express --------------------------------------------------------------


def _default_vpp(x: SignalSourceInputs) -> float:
    """The power-on amplitude as Vpp into the load setting — the unit the amplitude range is
    declared in (D48), through the power-on waveform's peak-to-RMS ratio like the component does."""
    fn = SHAPES[x.waveforms[0].shape][0]
    per = AmplitudeLevel.VPP_PER_VRMS.get(fn, AmplitudeLevel.VPP_PER_VRMS["SINusoid"])
    unit = x.amplitude.units[0]
    if unit == "VPK":
        return 2 * x.amplitude.default
    if unit == "VRMS":
        return x.amplitude.default * per
    if unit == "DBM":
        r, hiz = x.load.default, x.load.hiz_from
        ohms = r if hiz is None or r < hiz else 50.0  # dbm_hiz_ohms, as amplitude_level refers it
        return math.sqrt(10 ** (x.amplitude.default / 10) * 1e-3 * ohms) * per
    return x.amplitude.default


def check(x: SignalSourceInputs) -> list[InputProblem]:
    out: list[InputProblem] = []

    def bad(code: str, path: tuple[str | int, ...], message: str, suggestion: str | None = None) -> None:
        out.append(InputProblem(code, path, message, suggestion))

    names = [w.name for w in x.waveforms]
    for i, name in enumerate(names):
        if names.index(name) != i:
            bad("E-TPL-DUPLICATE", ("waveforms", i, "name"), f"波形关键字 {name} 重复")
    for key in ("frequency", "amplitude", "offset", "phase", "duty", "load"):
        r = getattr(x, key)
        if r is None:
            continue
        if not r.min < r.max:
            bad("E-TPL-RANGE", (key, "max"), f"{key} 的最大值 {r.max} 应大于最小值 {r.min}")
            continue
        # The amplitude range is in Vpp (D48); the power-on default is in the power-on unit.
        value = _default_vpp(x) if key == "amplitude" else r.default
        span = f"[{r.min}, {r.max}]" + (" Vpp" if key == "amplitude" else "")
        if not r.min <= value <= r.max:
            bad(
                "E-TPL-DEFAULT", (key, "default"),
                f"{key} 的上电值 {r.default:g}"
                + (f"（{x.amplitude.units[0]}，折合 {value:.6g} Vpp）" if key == "amplitude" else "")
                + f" 不在 {span} 内",
            )  # fmt: skip
    if x.frequency.min <= 0:
        bad("E-TPL-RANGE", ("frequency", "min"), "频率的最小值应大于 0")
    if x.load.min <= 0:
        bad("E-TPL-RANGE", ("load", "min"), "负载设定的最小值应大于 0 Ω")
    if x.duty is not None:
        top = 1.0 if x.duty.unit == "ratio" else 100.0
        if x.duty.max > top or x.duty.min <= 0:
            bad(
                "E-TPL-RANGE", ("duty", "max" if x.duty.max > top else "min"),
                f"占空比按{'比例' if x.duty.unit == 'ratio' else '百分数'}表示，范围应在 (0, {top:g}] 内",
                "比例写 0.001–1，百分数写 0.1–100" if x.duty.unit == "ratio" else None,
            )  # fmt: skip
        if not any(w.shape in ("square", "pulse") for w in x.waveforms):
            bad("E-TPL-UNUSED", ("duty",), "给了占空比，但没有方波或脉冲波形")
    elif any(w.shape == "pulse" for w in x.waveforms):
        bad("E-TPL-MISSING", ("duty",), "有脉冲波形，还要给占空比（表示与范围）")
    if "DBM" in x.amplitude.units and x.amplitude.min <= 0:
        bad(
            "E-TPL-RANGE", ("amplitude", "min"),
            "含 dBm 时最小幅度应大于 0（真机下限通常为 1 mVpp）：0 Vpp 在 dBm 下没有定义",
        )  # fmt: skip
    if x.clock.ref_input and len(x.clock.sources) < 2:
        bad("E-TPL-MISSING", ("clock", "sources"), "有参考输入，还要给内部与外部两个时钟源关键字")
    return out


# ---- the definition ------------------------------------------------------------------------------


def _setting(**kw: Any) -> dict[str, Any]:
    return {k: v for k, v in kw.items() if v is not None}


def build(x: SignalSourceInputs) -> dict[str, Any]:
    fr, am, of, du, ld, op, ck = (
        x.frequency,
        x.amplitude,
        x.offset,
        x.duty,
        x.load,
        x.output,
        x.clock,
    )
    names = [w.name for w in x.waveforms]
    shapes = {w.name: w.shape for w in x.waveforms}
    units = list(am.units)
    first_unit = units[0]

    channel_state: dict[str, Any] = {
        "function": _setting(type="enum", options=names, default=names[0], label="波形", label_en="Function"),
        "frequency": _setting(
            type="float", unit="HZ", min=fr.min, max=fr.max, default=fr.default, label="频率", label_en="Frequency"
        ),
        "amplitude": _setting(
            type="float", unit="V" if first_unit != "DBM" else None,
            min=None if len(units) > 1 or first_unit != "VPP" else am.min,
            max=None if len(units) > 1 or first_unit != "VPP" else am.max, default=am.default,
            label=am.label or "幅度", label_en="Amplitude",
        ),  # the range lives on amplitude_level as Vpp bounds (D48) unless VPP is the only unit
    }  # fmt: skip
    if len(units) > 1:
        channel_state["amplitude_unit"] = _setting(
            type="enum", options=units, default=first_unit, label="幅度单位", label_en="Amplitude unit"
        )
    channel_state["offset"] = _setting(
        type="float", unit="V", min=of.min, max=of.max, default=of.default, label="偏置", label_en="Offset"
    )
    if x.phase is not None:
        channel_state["phase"] = _setting(
            type="float", unit="DEG", min=x.phase.min, max=x.phase.max, default=x.phase.default,
            label="相位", label_en="Phase",
        )  # fmt: skip
    if du is not None:
        channel_state["duty"] = _setting(
            type="float", min=du.min, max=du.max, default=du.default, label="占空比", label_en="Duty cycle"
        )
    channel_state["output"] = _setting(
        type="bool", default=False, electrical=True, label="输出", label_en="Output"
    )
    channel_state["load"] = _setting(
        type="float", unit="OHM", min=ld.min, max=ld.max, default=ld.default,
        special={k: math.inf for k in ld.high_z_keywords} or None, label="输出负载设定", label_en="Output load",
    )  # fmt: skip
    state: dict[str, Any] = {}
    if ck.sources:
        state["clock_source"] = _setting(
            type="enum",
            options=list(ck.sources),
            default=ck.sources[0],
            label="时钟源",
            label_en="Clock source",
        )

    function = {"from": "$ch.function", "map": {n: SHAPES[s][0] for n, s in shapes.items()}}
    synth: dict[str, Any] = {"function": function, "frequency": "$ch.frequency"}
    symmetry = {
        n: SHAPES[s][1] for n, s in shapes.items() if SHAPES[s][1] is not None and SHAPES[s][1] != 100.0
    }
    if symmetry:
        synth["symmetry"] = {"from": "$ch.function", "map": symmetry, "default": 100.0}
    if x.phase is not None:
        synth["phase"] = "$ch.phase"
    if du is not None:
        synth |= {"duty": "$ch.duty", "duty_unit": du.unit}
    synth["bandwidth"] = fr.bandwidth_hz
    if fr.square_max:
        synth["square_max"] = fr.square_max
    if ck.sources or ck.ppm:
        synth["clock"] = "clock"
    level: dict[str, Any] = {
        "amplitude": "$ch.amplitude",
        "offset": "$ch.offset",
        "load_setting": "$ch.load",
        "vpp_min": am.min,
        "vpp_max": am.max,  # the range as Vpp into the load setting (D48)
    }
    if len(units) > 1:
        level["unit"] = "$ch.amplitude_unit"
    elif first_unit != "VPP":
        level["unit"] = first_unit
    if ld.hiz_from:
        level["hiz_from"] = ld.hiz_from

    components: list[dict[str, Any]] = []
    wiring: dict[str, Any] = {
        "channel": [["synth.out", "level.in"], ["level.out", "out.in"]],
    }
    ports: dict[str, Any] = {"channel": {x.port: "out.pin"}}
    if ck.sources or ck.ppm:
        clock: dict[str, Any] = {}
        if ck.sources:
            clock["source"] = "$clock_source"
        components.append({"name": "clock", "type": "reference_clock", "params": clock})
        if ck.ref_input:
            components.append({"name": "refin", "type": "input_port", "params": {"z_in": 50.0}})
            wiring["instrument"] = [["refin.out", "clock.ref"]]
            ports["instrument"] = {"REF": "refin.pin"}

    deviations: list[dict[str, Any]] = []
    if op.impedance_error_pct:
        deviations.append({"param": "out.z_out", "limit": op.impedance_error_pct, "unit": "%"})
    if ck.ppm:
        deviations.append({"param": "clock.ppm", "limit": ck.ppm, "distribution": "uniform"})

    commands = [dict(cmd) for cmd in x.commands.table]
    summary = ["function", "frequency", "amplitude", "offset"]
    if du is not None:
        summary.append("duty")
    summary += ["output", "load"]
    out: dict[str, Any] = {
        "manufacturer": x.identity.manufacturer,
        "model": x.identity.model,
        "class": "function_generator",
    }
    if x.identity.description:
        out["description"] = x.identity.description
    identity = x.identity.block()
    out["identity"] = identity
    if x.syntax:
        out["syntax"] = dict(x.syntax)
    if x.format:
        out["format"] = dict(x.format)
    if x.commands.unknown != "error":
        out["unknown_commands"] = x.commands.unknown
    out["channels"] = {"count": x.channels}
    state |= x.settings
    if state:
        out["state"] = state
    out |= {
        "channel_state": channel_state,
        "components": components,
        "channel_components": [
            {"name": "synth", "type": "waveform_synth", "params": synth},
            {"name": "level", "type": "amplitude_level", "params": level},
            {
                "name": "out",
                "type": "output_stage",
                "params": {"z_out": op.impedance_ohms, "enabled": "$ch.output"},
            },
        ],
        "wiring": wiring,
        "ports": ports,
        "display": {"template": "function_generator", "channel_summary": summary},
        "capabilities": ["function_generator"],
        "commands": commands,
    }
    if not components:
        del out["components"]
    if x.specs:
        out["specs"] = x.specs
    if deviations:
        out["deviations"] = deviations
    return out


#: The example's own command table: an ACME CAL-4 as its manual lists it (data, not a command set).
EXAMPLE_COMMANDS: list[dict[str, Any]] = [
        {"header": ":CHANnel<ch>:BASE:WAVe", "state": "ch.function"},
        {"header": ":CHANnel<ch>:BASE:FREQuency", "state": "ch.frequency"},
        {"header": ":CHANnel<ch>:BASE:AMPLitude", "state": "ch.amplitude"},
        {"header": ":CHANnel<ch>:BASE:OFFSet", "state": "ch.offset"},
        {"header": ":CHANnel<ch>:BASE:DUTY", "state": "ch.duty"},
        {"header": ":CHANnel<ch>:AMPLitude:UNIT", "state": "ch.amplitude_unit"},
        {"header": ":CHANnel<ch>:LOAD", "state": "ch.load"},
        {"header": ":CHANnel<ch>:OUTPut", "state": "ch.output", "dangerous": True},
        {"header": ":SYSTem:CLKSource", "state": "clock_source"},
]  # fmt: skip


def example() -> dict[str, Any]:
    """A 4-channel calibration source: square, pulse (0.1 %–5 % duty), sine, DC; VPP, VRMS, dBm;
    and the command table of its manual."""
    return {
        "identity": {"manufacturer": "ACME", "model": "CAL-4", "serial": "AC4000001", "firmware": "1.2.0"},
        "channels": 4,
        "waveforms": [
            {"name": "SQUare", "shape": "square"},
            {"name": "PULSe", "shape": "pulse"},
            {"name": "SINe", "shape": "sine"},
            {"name": "DC", "shape": "dc"},
        ],
        "frequency": {"min": 0.01, "max": 1.0e8, "default": 1000.0, "bandwidth_hz": 1.0e9},
        "amplitude": {"min": 0.001, "max": 10.0, "default": 0.1, "units": ["VPP", "VRMS", "DBM"]},
        "offset": {"min": -5.0, "max": 5.0, "default": 0.0},
        "duty": {"min": 0.1, "max": 99.9, "default": 50.0, "unit": "percent"},
        "load": {"min": 1.0, "max": 1.0e6, "default": 50.0, "high_z_keywords": ["INF"], "hiz_from": 10000.0},
        "output": {"impedance_ohms": 50.0, "impedance_error_pct": 1.0},
        "clock": {"sources": ["INTernal", "EXTernal"], "ppm": 2.0, "ref_input": True},
        "port": "CH{n}",
        "commands": {"table": EXAMPLE_COMMANDS},
        "specs": {"amplitude_accuracy": "±1 % of setting ± 1 mV"},
    }


# ---- drafts from a script (#145, D81) ---------------------------------------------------------------

#: How a waveform keyword reads as a shape (by its leading letters).
KEYWORD_SHAPES = [("PULS", "pulse"), ("SQU", "square"), ("SIN", "sine"), ("TRI", "triangle"), ("RAMP", "ramp"),
                  ("DC", "dc")]  # fmt: skip
#: Keywords for the shapes a script does not use (a data sheet's own replace them).
DEFAULT_WAVES = [("SINusoid", "sine"), ("SQUare", "square"), ("PULSe", "pulse"), ("RAMP", "ramp"),
                 ("TRIangle", "triangle"), ("DC", "dc")]  # fmt: skip

_SRC = r"(?:(?:SOUR|SOURCE|CHAN|CHANNEL|CH|C)#?:)?"
_BASE = r"(?:BASE:)?"

#: Spellings many sources share, each for one setting (D81: it binds a command, it does not pick a
#: vendor). Used only where no library model knows the script's header.
MNEMONICS = [
    Mnemonic(f"{_SRC}{_BASE}{_any('WAVe', 'FUNCtion')}(?::{_any('SHAPe')})?", {"state": "ch.function"}),
    Mnemonic(f"{_SRC}{_BASE}{_any('FREQuency')}(?::{_any('CW')})?", {"state": "ch.frequency"}),
    Mnemonic(f"{_SRC}{_any('AMPLitude', 'VOLTage')}:{_any('UNIT')}", {"state": "ch.amplitude_unit"}),
    Mnemonic(
        f"{_SRC}{_BASE}{_any('AMPLitude', 'VOLTage')}(?::{_any('LEVel')})?(?::{_any('IMMediate')})?(?::{_any('AMPLitude')})?",
        {"state": "ch.amplitude"},
    ),
    Mnemonic(
        f"{_SRC}{_BASE}(?:{_any('VOLTage')}(?::{_any('LEVel')})?(?::{_any('IMMediate')})?:)?{_any('OFFSet')}",
        {"state": "ch.offset"},
    ),
    Mnemonic(f"{_SRC}{_BASE}{_any('PHASe')}(?::{_any('ADJust')})?", {"state": "ch.phase"}),
    Mnemonic(
        f"{_SRC}{_BASE}(?:{_any('DUTY', 'DCYCle')}|{_any('FUNCtion')}:{_any('SQUare')}:{_any('DCYCle')})",
        {"state": "ch.duty"},
    ),
    Mnemonic(f"(?:{_SRC}|(?:OUTP|OUTPUT)#?:){_any('LOAD')}", {"state": "ch.load"}),
    Mnemonic(
        f"(?:(?:OUTP|OUTPUT)#?(?::{_any('STATe')})?|{_SRC}{_any('OUTPut')})",
        {"state": "ch.output", "dangerous": True},
    ),
    Mnemonic(
        f"(?:{_any('SYSTem')}:{_any('CLKSource')}|(?:{_any('SOURce')}:)?{_any('ROSCillator')}:{_any('SOURce')})",
        {"state": "clock_source"},
    ),
]


def _draft_base() -> dict[str, Any]:
    """Generic inputs a data sheet would replace; no commands (the draft learns them)."""
    return {
        "identity": {"manufacturer": "Unknown", "model": "SOURCE"},
        "channels": 2,
        "waveforms": [{"name": n, "shape": sh} for n, sh in DEFAULT_WAVES],
        "frequency": {"min": 1.0e-3, "max": 1.1e9, "default": 1000.0, "bandwidth_hz": 1.0e9},
        "amplitude": {"min": 0.001, "max": 20.0, "default": 0.1, "units": ["VPP"]},
        "offset": {"min": -10.0, "max": 10.0, "default": 0.0},
        "phase": {"min": -360.0, "max": 360.0, "default": 0.0},
        "duty": {"min": 1.0e-6, "max": 1.0, "default": 0.5, "unit": "ratio"},
        "load": {"min": 1.0, "max": 1.0e9, "default": 50.0, "high_z_keywords": ["INFinity"], "hiz_from": 10000.0},
        "commands": {"table": []},
    }  # fmt: skip


def _basics(x: dict[str, Any], commands: list[Any], hints: dict[str, Any]) -> list[Any]:
    from .draft import CHANNEL_NODE, channels_used, default, found, units

    filled = []
    model = hints.get("model")
    if model:
        x["identity"]["model"] = model[0]
        filled.append(found(("identity", "model"), model[0], model[1], "*IDN? 校验的型号串"))
    else:
        filled.append(default(("identity", "model"), "SOURCE"))
    filled.append(default(("identity", "manufacturer"), "Unknown"))
    used = channels_used(commands)
    n, line = max(used) if used else (1, None)
    x["channels"] = max(n, 4)  # a channel named by a variable reads as 1: room for the ones it may be
    if line is not None:
        filled.append(
            found(
                ("channels",),
                x["channels"],
                line,
                f"脚本中最大的通道号为 {n}（循环里的通道号读作 1），待确认",
            )
        )
    # The output's front-panel name follows how the commands name a channel (CHANnel1 -> CH1).
    for u in units(commands):
        named = [m for node in u.header.lstrip(":").split(":") if (m := CHANNEL_NODE.fullmatch(node))]
        if named and named[0].group(1).upper().startswith(("C", "CH")):
            x["port"] = "CH{n}"
            filled.append(found(("port",), "CH{n}", u.line, "输出端口名按命令里的通道名，待确认"))
            break
    filled.append(default(("frequency", "max"), x["frequency"]["max"]))
    filled.append(default(("output", "impedance_ohms"), 50.0))
    filled.append(
        default(("load", "hiz_from"), x["load"]["hiz_from"], "不小于该值的负载设定视为高阻，待确认")
    )
    filled.append(default(("commands", "unknown"), "error", "契约若要求忽略未建模命令，改为 ignore"))
    return filled


def _enable(x: dict[str, Any], target: str, teacher: Any) -> list[Any]:
    """Switch on the input a learned command needs (D81)."""
    from . import Filled, pointer

    def put(path: tuple[str, ...], value: Any) -> list[Any]:
        node = x
        for key in path[:-1]:
            node = node.setdefault(key, {})
        if node.get(path[-1]) == value:
            return []
        node[path[-1]] = value
        return [Filled(pointer(*path), value, "default", None, "命令用到它：默认值，待确认")]

    if target == "ch.amplitude_unit" and len(x.get("amplitude", {}).get("units", [])) < 2:
        return put(("amplitude", "units"), ["VPP", "VPK", "VRMS", "DBM"])
    if target == "ch.duty" and not x.get("duty"):
        return put(("duty",), {"min": 1.0e-6, "max": 1.0, "default": 0.5, "unit": "ratio"})
    if target == "ch.phase" and not x.get("phase"):
        return put(("phase",), {"min": -360.0, "max": 360.0, "default": 0.0})
    if target == "clock_source" and not x.get("clock", {}).get("sources"):
        return put(("clock", "sources"), ["INTernal", "EXTernal"])
    return []


def _refine(x: dict[str, Any], bound: dict[str, list[tuple[list[str], int]]]) -> list[Any]:
    """Data-sheet values the bound commands' arguments show."""
    from .draft import default, found, number

    filled: list[Any] = []
    seen: list[tuple[str, str, int]] = []
    for args, line in bound.get("ch.function", []):
        for a in args[:1]:
            shape = next((sh for pre, sh in KEYWORD_SHAPES if a.upper().startswith(pre)), None)
            if shape and all(a.upper() != w.upper() for w, _, _ in seen):
                seen.append((a, shape, line))
    if seen:
        # The script's keywords as it writes them, then generic ones for the shapes it does not use.
        shapes = {sh for _, sh, _ in seen}
        names = {w.upper() for w, _, _ in seen}
        x["waveforms"] = [{"name": w, "shape": sh} for w, sh, _ in seen] + [
            {"name": n, "shape": sh} for n, sh in DEFAULT_WAVES if sh not in shapes and n.upper() not in names
        ]
        filled += [found(("waveforms",), w, ln, "脚本使用的波形关键字") for w, _sh, ln in seen]
    for args, line in bound.get("ch.amplitude_unit", []):
        filled += [found(("amplitude", "units"), a.upper(), line) for a in args[:1]]
    amps = [v for args, _ in bound.get("ch.amplitude", []) for a in args[:1] if (v := number(a)) is not None]
    if amps and max(amps) > x["amplitude"]["max"]:
        x["amplitude"]["max"] = max(amps)
    filled.append(default(("amplitude", "max"), x["amplitude"]["max"]))
    duties = [
        (v, ln, a) for args, ln in bound.get("ch.duty", []) for a in args[:1] if (v := number(a)) is not None
    ]
    if duties:
        if any(v > 1 for v, _, _ in duties):
            x["duty"] = {"min": 0.0001, "max": 100.0, "default": 50.0, "unit": "percent"}
            v, ln, a = max(duties)
            filled.append(found(("duty", "unit"), "percent", ln, f"脚本写 {a}（大于 1）：按百分数"))
        else:
            v, ln, a = duties[0]
            filled.append(
                found(
                    ("duty", "unit"),
                    "ratio",
                    ln,
                    f"脚本写 {a}（不大于 1）：按比例；若实为百分数请改为 percent，待确认",
                )
            )
    for args, ln in bound.get("ch.load", []):
        for a in args[:1]:
            v = number(a)
            if v is None and a.upper() not in {k.upper() for k in x["load"]["high_z_keywords"]}:
                x["load"]["high_z_keywords"].append(a)
                filled.append(found(("load", "high_z_keywords"), a, ln))
            elif v is not None:
                filled.append(found(("load",), v, ln, "脚本写的负载设定"))
    for args, ln in bound.get("clock_source", []):
        for a in args[:1]:
            sources = x.setdefault("clock", {}).setdefault("sources", [])
            if re.fullmatch(r"[A-Za-z]\w*", a) and a.upper() not in {s.upper() for s in sources}:
                sources.insert(0, a)
            filled.append(found(("clock", "sources"), a, ln))
    return filled


DRAFTING = Drafting(base=_draft_base, basics=_basics, mnemonics=MNEMONICS, enable=_enable, refine=_refine)


TEMPLATE = register(
    Template(
        id="signal_source",
        version=2,
        category="function_generator",
        title="信号源",
        title_en="Signal source",
        description="函数发生器与校准源：波形、频率与幅度范围、幅度单位（Vpp、Vpk、Vrms、dBm）、占空比（比例或百分数，"
        "按声明）、负载设定、输出阻抗、参考时钟，以及这个型号自己的命令表（照手册逐条写，只带公共命令起步，D81）。"
        "技术指标只作记录（D3）",
        inputs=SignalSourceInputs,
        example=example,
        build=build,
        check=check,
        drafting=DRAFTING,
    )
)
