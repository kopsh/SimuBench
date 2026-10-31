"""The signal source template (#144, instrument-authoring.md 7.4): a function generator's or a
calibrator's data sheet — waveforms, frequency and amplitude ranges, amplitude units, duty, load
setting, output impedance, reference clock — becomes the source modules (waveform_synth ->
amplitude_level -> output_stage, reference_clock) and a SCPI source command set.

Two command sets: ``scpi-source`` (``[:SOURce<n>]:FUNCtion``, ``:VOLTage``, ``:OUTPut<n>``) and
``channel-base`` (a tree per channel: ``:CHANnel<n>:BASE:WAVe``, ``:BASE:AMPLitude``, ``:LOAD``,
``:OUTPut``). The duty is a ratio or a percentage as declared, never guessed from its size.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import InputProblem, Template, register
from .oscilloscope import IdentityIn

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
    model_config = ConfigDict(extra="forbid")


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
        [], "高阻关键字", "High-Z keywords", "Words the load command takes for high-Z, e.g. INF, INFinity"
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


class DialectIn(_In):
    command_set: Literal["scpi-source", "channel-base"] = _f(
        "scpi-source", "命令集", "Command set",
        "scpi-source: [:SOURce<n>]:FUNCtion, :VOLTage, :OUTPut<n>; channel-base: :CHANnel<n>:BASE:WAVe, "
        ":BASE:FREQuency, :BASE:AMPLitude, :LOAD, :OUTPut",
    )  # fmt: skip
    unknown_commands: Literal["error", "ignore"] = _f(
        "error",
        "未知命令",
        "Unknown commands",
        "error: -113; ignore: swallowed and logged (a contract bench)",
    )


class SignalSourceInputs(_In):
    identity: IdentityIn = _f(..., "身份", "Identity", "Manufacturer, model, *IDN?")
    channels: int = _f(2, "通道数", "Channels", "Output channels", ge=1, le=8)
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
    dialect: DialectIn = _group(DialectIn, "命令方言", "Dialect", "Command set and unknown commands")
    specs: dict[str, Any] = _f(
        {},
        "技术指标",
        "Specifications",
        "The data sheet's figures as written there: a record, not used to compute",
    )


# ---- checks the schema cannot express --------------------------------------------------------------


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
        elif not r.min <= r.default <= r.max:
            bad("E-TPL-DEFAULT", (key, "default"), f"{key} 的上电值 {r.default} 不在 [{r.min}, {r.max}] 内")
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
    if "DBM" in x.amplitude.units and x.amplitude.units[0] == "DBM" and x.amplitude.min < -200:
        bad("E-TPL-RANGE", ("amplitude", "min"), "dBm 的最小值不合理")
    if x.clock.ref_input and len(x.clock.sources) < 2:
        bad("E-TPL-MISSING", ("clock", "sources"), "有参考输入，还要给内部与外部两个时钟源关键字")
    return out


# ---- the definition ------------------------------------------------------------------------------


def _setting(**kw: Any) -> dict[str, Any]:
    return {k: v for k, v in kw.items() if v is not None}


def build(x: SignalSourceInputs) -> dict[str, Any]:
    fr, am, of, du, ld, op, ck, di = (
        x.frequency,
        x.amplitude,
        x.offset,
        x.duty,
        x.load,
        x.output,
        x.clock,
        x.dialect,
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
            type="float", unit="V" if first_unit != "DBM" else None, min=am.min, max=am.max, default=am.default,
            label=am.label or "幅度", label_en="Amplitude",
        ),
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
    level: dict[str, Any] = {"amplitude": "$ch.amplitude", "offset": "$ch.offset", "load_setting": "$ch.load"}
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
    ports: dict[str, Any] = {
        "channel": {"CH{n}" if di.command_set == "channel-base" else "OUT{n}": "out.pin"}
    }
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

    commands = _commands(x, len(units) > 1)
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
    identity: dict[str, Any] = {"serial": x.identity.serial, "firmware": x.identity.firmware}
    if x.identity.idn:
        identity["idn"] = x.identity.idn
    out["identity"] = identity
    if di.unknown_commands != "error":
        out["unknown_commands"] = di.unknown_commands
    out["channels"] = {"count": x.channels}
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


def _commands(x: SignalSourceInputs, unit_setting: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    dangerous = {"dangerous": True} if x.output.dangerous else {}
    if x.dialect.command_set == "scpi-source":
        out += [
            {"header": "[:SOURce<ch>]:FUNCtion[:SHAPe]", "state": "ch.function"},
            {"header": "[:SOURce<ch>]:FREQuency[:CW]", "state": "ch.frequency"},
            {"header": "[:SOURce<ch>]:VOLTage[:LEVel][:IMMediate][:AMPLitude]", "state": "ch.amplitude"},
            {"header": "[:SOURce<ch>]:VOLTage[:LEVel][:IMMediate]:OFFSet", "state": "ch.offset"},
        ]
        if unit_setting:
            out.append({"header": "[:SOURce<ch>]:VOLTage:UNIT", "state": "ch.amplitude_unit"})
        if x.phase is not None:
            out.append({"header": "[:SOURce<ch>]:PHASe[:ADJust]", "state": "ch.phase"})
        if x.duty is not None:
            out.append({"header": "[:SOURce<ch>]:FUNCtion:SQUare:DCYCle", "state": "ch.duty"})
        out += [
            {"header": ":OUTPut<ch>[:STATe]", "state": "ch.output", **dangerous},
            {"header": ":OUTPut<ch>:LOAD", "state": "ch.load"},
        ]
        if x.clock.sources:
            out.append({"header": "[:SOURce]:ROSCillator:SOURce", "state": "clock_source"})
        return out
    out += [
        {"header": ":CHANnel<ch>:BASE:WAVe", "state": "ch.function"},
        {"header": ":CHANnel<ch>:BASE:FREQuency", "state": "ch.frequency"},
        {"header": ":CHANnel<ch>:BASE:AMPLitude", "state": "ch.amplitude"},
        {"header": ":CHANnel<ch>:BASE:OFFSet", "state": "ch.offset"},
    ]
    if x.phase is not None:
        out.append({"header": ":CHANnel<ch>:BASE:PHASe", "state": "ch.phase"})
    if x.duty is not None:
        out.append({"header": ":CHANnel<ch>:BASE:DUTY", "state": "ch.duty"})
    if unit_setting:
        out.append({"header": ":CHANnel<ch>:AMPLitude:UNIT", "state": "ch.amplitude_unit"})
    out += [
        {"header": ":CHANnel<ch>:LOAD", "state": "ch.load"},
        {"header": ":CHANnel<ch>:OUTPut", "state": "ch.output", **dangerous},
    ]
    if x.clock.sources:
        out.append({"header": ":SYSTem:CLKSource", "state": "clock_source"})
    return out


def example() -> dict[str, Any]:
    """A 4-channel calibration source: square, pulse (0.1 %–5 % duty), sine, DC; VPP, VRMS, dBm."""
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
        "amplitude": {"min": 0.0, "max": 10.0, "default": 0.1, "units": ["VPP", "VRMS", "DBM"]},
        "offset": {"min": -5.0, "max": 5.0, "default": 0.0},
        "duty": {"min": 0.1, "max": 99.9, "default": 50.0, "unit": "percent"},
        "load": {"min": 1.0, "max": 1.0e6, "default": 50.0, "high_z_keywords": ["INF"], "hiz_from": 10000.0},
        "output": {"impedance_ohms": 50.0, "impedance_error_pct": 1.0},
        "clock": {"sources": ["INTernal", "EXTernal"], "ppm": 2.0, "ref_input": True},
        "dialect": {"command_set": "channel-base"},
        "specs": {"amplitude_accuracy": "±1 % of setting ± 1 mV"},
    }


TEMPLATE = register(
    Template(
        id="signal_source",
        version=1,
        category="function_generator",
        title="信号源",
        title_en="Signal source",
        description="函数发生器与校准源：波形、频率与幅度范围、幅度单位（Vpp、Vpk、Vrms、dBm）、占空比（比例或百分数，"
        "按声明）、负载设定、输出阻抗、参考时钟；通用 SCPI 信号源命令集或按通道的命令树。技术指标只作记录（D3）",
        inputs=SignalSourceInputs,
        example=example,
        build=build,
        check=check,
    )
)
