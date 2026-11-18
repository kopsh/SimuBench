"""The oscilloscope template (instrument-authoring.md 7.4, section 8): a data sheet's channels, scale
table, ADC, timebase, trigger, acquisition, measurements and waveform transfer become the scope
modules of section 8.4. The template derives behaviour only; the command table is the model's own
data, written from its manual (D81: every instrument's SCPI is its own, there is no "dialect" to
choose) — the IEEE 488.2 common commands and :SYSTem:ERRor are built into the engine.

Specifications are a record (``specs``); the ranges a unit deviates in are filled in separately
(``deviations``, per V/div in the scale table), never derived from the specifications (D3).
"""

from __future__ import annotations

import math
import re
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from ..components.measurements import MEASUREMENTS
from . import Drafting, InputProblem, Mnemonic, Template, register


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


class IdentityIn(_In):
    manufacturer: str = _f(..., "厂商", "Manufacturer", "As *IDN? names it")
    model: str = _f(..., "型号", "Model", "As *IDN? names it")
    serial: str = _f("SB000001", "序列号", "Serial number", "The unit's serial number in *IDN?")
    firmware: str = _f("1.0.0", "固件版本", "Firmware", "The firmware version in *IDN?")
    idn: str | None = _f(
        None, "*IDN? 格式", "*IDN? format",
        "Fields {manufacturer} (upper-cased), {model}, {serial}, {firmware}; default: the manufacturer "
        "as written here, then the other three, comma-separated",
    )  # fmt: skip
    description: str | None = _f(None, "说明", "Description", "One line about the instrument")

    def block(self) -> dict[str, Any]:
        """The definition's ``identity``: serial, firmware and the ``*IDN?`` format. Without an
        ``idn`` the manufacturer is written into the format as typed — the engine's
        ``{manufacturer}`` placeholder upper-cases it, which would break the promise above
        ("as *IDN? names it": a script checking ``Zhiyuan,ZY-DSO3104,…`` must get exactly that)."""
        literal = self.manufacturer.replace("{", "{{").replace("}", "}}")
        return {
            "idn": self.idn or f"{literal},{{model}},{{serial}},{{firmware}}",
            "serial": self.serial,
            "firmware": self.firmware,
        }


class ImpedanceIn(_In):
    name: str = _f(..., "关键字", "Keyword", "The command's keyword for this input, e.g. ONEMeg, FIFTy")
    ohms: float = _f(..., "输入电阻", "Input resistance", "Resistance to ground", "Ω", gt=0)
    capacitance_f: float = _f(0.0, "输入电容", "Input capacitance", "Capacitance to ground", "F", ge=0)
    label: str | None = _f(None, "显示名", "Label", "How the panel shows it, e.g. '1 MΩ'")


class CouplingIn(_In):
    keyword: str = _f(..., "关键字", "Keyword", "How the commands write this coupling, e.g. DC, AC, GND, D1M")
    coupling: Literal["DC", "AC", "GND"] = _f(..., "耦合", "Coupling", "What the keyword does to the signal")
    impedance: str | None = _f(
        None, "同时选择的输入", "Input it selects",
        "A keyword of the input impedances when this keyword also selects the input (D50: DC into 50 Ω); "
        "omit: the input impedance is a setting of its own",
    )  # fmt: skip
    label: str | None = _f(None, "显示名", "Label", "How the panel shows it, e.g. 'DC 50 Ω'")


class ChannelsIn(_In):
    count: int = _f(4, "通道数", "Channels", "Analog input channels", ge=1, le=8)
    keyword: str = _f(
        "CHANnel{n}", "通道名", "Channel name",
        "How the commands name channel {n} as a source, e.g. CHANnel{n}, C{n}, CH{n}",
    )  # fmt: skip
    label: str | None = _f(
        "CH{n}", "通道显示名", "Channel label", "How the panel shows channel {n}; omit: the name"
    )
    impedances: list[ImpedanceIn] = _f(
        ..., "输入阻抗", "Input impedances", "The selectable inputs, the power-on one first", min_length=1
    )
    bandwidth_hz: float = _f(..., "带宽", "Bandwidth", "-3 dB analog bandwidth", "Hz", gt=0)
    response: Literal["first_order", "brickwall", "gaussian", "bessel4", "butterworth4"] = _f(
        "first_order", "响应形状", "Response shape",
        "Shape of the bandwidth filter: first_order (an analog front end), brickwall (flat magnitude), "
        "gaussian (no ringing), bessel4 or butterworth4; -3 dB at the bandwidth or the limit (#607)",
    )  # fmt: skip
    bandwidth_limit_hz: float | None = _f(
        None,
        "带宽限制",
        "Bandwidth limit",
        "The limit filter's -3 dB point; omit: no bandwidth limit setting",
        "Hz",
        gt=0,
    )
    couplings: list[CouplingIn] = _f(
        [{"keyword": "DC", "coupling": "DC"}, {"keyword": "AC", "coupling": "AC"}],
        "耦合", "Couplings", "The selectable couplings, the power-on one first", min_length=1,
    )  # fmt: skip
    ac_corner_hz: float = _f(
        10.0, "交流耦合转折频率", "AC coupling corner", "-3 dB of the AC high-pass", "Hz", gt=0
    )
    displayed: list[int] = _f(
        [1], "上电显示的通道", "Displayed at power-on", "Channel numbers shown at power-on"
    )

    def name(self, n: int) -> str:
        return self.keyword.replace("{n}", str(n))

    def selects_input(self) -> bool:
        """The coupling keywords name the input too (D1M, D50): no input impedance setting."""
        return any(c.impedance for c in self.couplings)


class ScaleRowIn(_In):
    v_div: float = _f(
        ..., "档位", "V/div", "Vertical sensitivity at the input connector (probe 1X)", "V/div", gt=0
    )
    offset_v: float | None = _f(
        None, "偏置范围", "Offset range", "± offset this V/div allows (up to this row)", "V", gt=0
    )
    noise_v_rms: float | None = _f(None, "噪声", "Noise", "Input-referred noise at this V/div", "V rms", ge=0)
    gain_error_pct: float | None = _f(
        None,
        "增益误差限",
        "Gain error limit",
        "± the DC gain a unit may deviate by (individual deviation)",
        "%",
        ge=0,
    )
    offset_error_v: float | None = _f(
        None,
        "偏置误差限",
        "Offset error limit",
        "± the offset a unit may deviate by (individual deviation)",
        "V",
        ge=0,
    )


class VerticalIn(_In):
    scale_range: tuple[float, float] = _f(
        ..., "档位范围", "Scale range", "[min, max] V/div at the input connector (probe 1X)", "V/div"
    )
    default_scale: float = _f(
        1.0, "上电档位", "Power-on scale", "V/div at power-on (displayed)", "V/div", gt=0
    )
    table: list[ScaleRowIn] = _f(
        [], "档位表", "Scale table", "Per V/div, increasing: offset range, noise, error limits; empty: none"
    )
    noise_divs: float = _f(
        0.02, "输入噪声（格）", "Input noise (divisions)",
        "Input noise rms as a fraction of a division where the scale table gives no noise: an assumed "
        "value (about 0.6 LSB of an 8-bit ADC), not a data-sheet figure; 0: a noiseless input",
        "div", ge=0,
    )  # fmt: skip
    offset_max_v: float = _f(
        1000.0, "偏置设定上限", "Offset setting limit",
        "± offset the setting accepts when the table gives no offset ranges (displayed volts)", "V", gt=0,
    )  # fmt: skip
    out_of_range: Literal["error", "clamp"] = _f(
        "error", "超范围", "Out of range",
        "error: refuse a scale or offset outside the table with -222; clamp: take the nearest legal value",
    )  # fmt: skip
    probe_range: tuple[float, float] = _f(
        (0.001, 10000.0), "探头衰减比范围", "Probe ratio range", "[min, max] of the probe setting"
    )
    default_probe: float = _f(
        1.0, "上电探头衰减比", "Power-on probe ratio", "Probe setting at power-on", gt=0
    )
    ladder: bool = _f(
        True, "1-2-5 档位", "1-2-5 steps", "Scale writes snap to 1-2-5; off: any value in range"
    )
    probe_rescales: bool = _f(
        False, "探头比换算档位", "Probe ratio rescales", "Changing the probe ratio rescales scale and offset"
    )
    offset_dac_error_pct: float | None = _f(
        None, "偏置 DAC 误差限", "Offset DAC error limit", "± gain error of the offset DAC (individual deviation)", "%",
        ge=0,
    )  # fmt: skip


class AdcIn(_In):
    bits: int = _f(8, "位数", "Bits", "ADC resolution; 0: records are not quantized", ge=0, le=16)
    codes_per_div: float | None = _f(
        None, "每格码值", "Codes per division", "Default: 2^bits / divisions", gt=0
    )
    divisions: float = _f(10.0, "垂直格数", "Vertical divisions", "Divisions the ADC spans", gt=0)
    max_rate: float = _f(
        0.0, "最高采样率", "Max sample rate", "Native top sample rate; 0: no limit", "Sa/s", ge=0
    )
    max_rate_paired: float | None = _f(
        None,
        "成对时最高采样率",
        "Max rate when paired",
        "Top rate when both channels of a pair are on",
        "Sa/s",
        gt=0,
    )
    rate_pairs: list[list[int]] = _f(
        [], "交织映射", "Interleave pairs", "Channels sharing a converter, e.g. [[1, 2], [3, 4]]"
    )
    offset_sign: Literal["add", "subtract"] = _f(
        "add", "偏置方向", "Offset sign", "add: codes of volts + offset; subtract: volts - offset (Keysight)"
    )


class MemoryIn(_In):
    min: int = _f(..., "最小", "Minimum", "Smallest memory size, points", gt=0)
    max: int = _f(..., "最大", "Maximum", "Largest memory size, points", gt=0)
    default: int = _f(..., "上电值", "Power-on value", "Memory size at power-on, points", gt=0)


class TimebaseIn(_In):
    scale_range: tuple[float, float] = _f(..., "时基范围", "Timebase range", "[min, max] s/div", "s/div")
    default_scale: float = _f(1e-3, "上电时基", "Power-on timebase", "s/div at power-on", "s/div", gt=0)
    points: list[int] = _f(
        [100, 250, 500, 1000, 2000, 5000, 10000], "记录长度", "Record lengths",
        "Points a transfer may read, increasing (the record length setting); unused with a memory size setting",
        min_length=1,
    )  # fmt: skip
    default_points: int = _f(1000, "上电记录长度", "Power-on record length", "Points at power-on", gt=0)
    memory_depth: int | None = _f(
        None, "存储深度", "Memory depth",
        "Omit: the record is the record length; given: sample rate = min(max rate, depth / 10 div) on a "
        "1-2-5 ladder",
        gt=0,
    )  # fmt: skip
    reference_divs: float = _f(
        5.0, "参考位置", "Reference position", "Divisions from the left the trigger sits at"
    )
    ladder: bool = _f(True, "1-2-5 时基", "1-2-5 timebase", "Timebase writes snap to 1-2-5; off: any value")
    memory_sizes: MemoryIn | None = _f(
        None, "存储深度设定", "Memory size setting",
        "Given: a memory size setting, and the record is exactly that many points over ten divisions",
    )  # fmt: skip
    clock_ppm: float | None = _f(
        None, "时基误差限", "Timebase error limit", "± ppm a unit's sample clock may deviate by (individual deviation)",
        "ppm", ge=0,
    )  # fmt: skip


class TriggerModeIn(_In):
    keyword: str = _f(..., "关键字", "Keyword", "How the commands write this mode, e.g. AUTO, NORMal, SINGle")
    meaning: Literal["auto", "normal", "single", "stop"] = _f(
        ..., "含义", "Meaning",
        "auto: free-runs without an edge; normal: waits for one; single: acquires once on the next "
        "trigger; stop: acquires nothing",
    )  # fmt: skip


class SourceIn(_In):
    keyword: str = _f(..., "关键字", "Keyword", "How the commands name this source, e.g. EXTernal, EX10")
    label: str | None = _f(None, "显示名", "Label", "How the panel shows it; omit: the keyword")


class TriggerIn(_In):
    modes: list[TriggerModeIn] = _f(
        [{"keyword": "AUTO", "meaning": "auto"}, {"keyword": "NORMal", "meaning": "normal"}],
        "触发方式", "Trigger modes", "The trigger mode setting's keywords, the power-on one first", min_length=1,
    )  # fmt: skip
    slopes: list[str] = _f(
        ["POSitive", "NEGative", "EITHer"], "触发沿", "Slopes", "The slope setting's keywords, the power-on one first",
        min_length=1,
    )  # fmt: skip
    external: list[SourceIn] = _f(
        [], "外触发源", "External sources",
        "Keywords of the sources on the EXT input (several for its ranges, e.g. EX and EX10); empty: no EXT input",
    )  # fmt: skip
    line: str | None = _f(None, "电源触发源", "Line source", "Keyword of the line (mains) source; omit: none")
    slope_edges: dict[str, Literal["rising", "falling", "either"]] = _f(
        {}, "触发沿含义", "Slope meanings",
        "Slope keyword -> the edge it takes (POS: rising), for keywords other than POSitive, NEGative, EITHer, "
        "RISing, FALLing; empty: those",
    )  # fmt: skip
    level_per_channel: bool = _f(
        False, "按通道的触发电平", "Level per channel",
        "Each channel keeps its own trigger level and slope; off: one level and slope for the trigger",
    )  # fmt: skip
    hysteresis_divs: float = _f(0.16, "迟滞", "Hysteresis", "Half the band an edge must cross", "div", ge=0)
    level_range_divs: float | None = _f(
        None,
        "电平范围",
        "Level range",
        "± divisions of the source's screen the level may be set to",
        "div",
        gt=0,
    )
    jitter_s: float | None = _f(
        None, "触发抖动", "Trigger jitter",
        "rms jitter of the trigger instant; omit: an assumed tenth of the fastest sample "
        "interval, or an assumed 100 ps when the ADC gives no top rate (assumed values, not a "
        "data-sheet figure); 0: no jitter",
        "s", ge=0,
    )  # fmt: skip
    auto_windows: int = _f(
        4,
        "自动触发等待",
        "Auto timeout",
        "Records an auto mode looks ahead for an edge before it free-runs",
        ge=1,
    )


class AcquisitionTypeIn(_In):
    keyword: str = _f(
        ..., "关键字", "Keyword", "How the commands write this type, e.g. NORMal, AVERage, PEAK"
    )
    kind: Literal["normal", "average", "peak"] = _f(
        ..., "含义", "Meaning", "normal: one record; average: the mean of several; peak: min/max per interval"
    )


class AcquisitionIn(_In):
    types: list[AcquisitionTypeIn] = _f(
        [{"keyword": "NORMal", "kind": "normal"}], "采集方式", "Acquisition types",
        "The acquisition type setting's keywords; a single normal type: no setting", min_length=1,
    )  # fmt: skip
    average_counts: list[int] = _f(
        [2, 4, 8, 16, 32, 64, 128, 256], "平均次数", "Average counts", "The average count setting's values"
    )
    completion: Literal["immediate", "pending"] = _f(
        "immediate", "单次完成", "Single completion",
        "immediate: a single acquisition is done at once; pending: in virtual time, after its trigger",
    )  # fmt: skip
    opc_waits: bool = _f(True, "*OPC? 等待采集", "*OPC? waits", "*OPC? waits for a pending acquisition")
    while_waiting: Literal["previous", "error"] = _f(
        "previous", "等待中读数", "Read while waiting", "previous: the last record; error: an error"
    )
    no_trigger: Literal["wait", "return"] = _f(
        "wait", "无触发时", "Without a trigger", "wait: *OPC? and a digitize block; return: they answer"
    )
    after_single: Literal["unchanged", "stop"] = _f(
        "unchanged", "单次之后", "After a single",
        "unchanged: the trigger mode stays single; stop: a completed single acquisition sets the stop mode",
    )  # fmt: skip
    reacquire: Literal["explicit", "every_read", "on_read_start"] = _f(
        "explicit", "重新采集", "Reacquire",
        "explicit: only an acquire command; every_read: each read takes a new record; on_read_start: a "
        "read from the first point does, the following pieces read the same record",
    )  # fmt: skip
    missing_record: Literal["error", "acquire"] = _f(
        "error",
        "无记录时读取",
        "Read without a record",
        "error: refused; acquire: the read takes an acquisition",
    )
    untriggered: Literal["search", "record"] = _f(
        "search", "未触发采集耗时", "Untriggered acquisition time",
        "The virtual time an acquisition without an edge takes: search (the trigger search) or record (one record)",
    )  # fmt: skip


class MeasurementsIn(_In):
    items: list[str] = _f(MEASUREMENTS, "测量项", "Measurements", f"From {', '.join(MEASUREMENTS)}")
    keywords: dict[str, str] = _f(
        {}, "测量关键字", "Measurement keywords",
        "Measurement -> how the commands name it (VPP: PKPK); not listed: its own name",
    )  # fmt: skip
    units: dict[str, str] = _f(
        {},
        "应答单位",
        "Reply units",
        "Measurement -> the unit word a reply carries (FREQuency: HZ); not listed: V",
    )
    slots: int = _f(
        0, "测量槽数", "Measurement slots",
        "Numbered slots a command fills with a measurement and a source (P1..P8); 0: none", ge=0, le=16,
    )  # fmt: skip
    empty_slot: str = _f(
        "NULL", "空槽关键字", "Empty slot keyword", "The keyword of a slot with no measurement"
    )

    def named(self) -> bool:
        """Replies name the measurement by its keyword and carry a unit word."""
        return bool(self.keywords or self.units or self.slots)


class FormatIn(_In):
    keyword: str = _f(..., "关键字", "Keyword", "How the commands write this format, e.g. BYTE, WORD, ASCii")
    kind: Literal["byte", "word", "ascii"] = _f(
        ..., "含义", "Meaning", "byte: 8-bit codes; word: 16-bit codes; ascii: volts as text"
    )


class TransferIn(_In):
    encoding: Literal["binary", "text"] = _f(
        "binary", "波形编码", "Waveform encoding",
        "binary: a block of codes in the format setting's formats; text: volts as text, the point window in settings",
    )  # fmt: skip
    source_setting: bool = _f(
        True,
        "传输源设定",
        "Source setting",
        "A setting chooses the channel a read returns; off: the command names it",
    )
    formats: list[FormatIn] = _f(
        [{"keyword": "WORD", "kind": "word"}, {"keyword": "BYTE", "kind": "byte"}, {"keyword": "ASCii", "kind": "ascii"}],
        "波形格式", "Waveform formats", "binary: the format setting's keywords, the power-on one first", min_length=1,
    )  # fmt: skip
    byte_codes_per_div: float | None = _f(
        None, "BYTE 每格码值", "Byte codes per division", "Default: the ADC's codes per division", gt=0
    )
    word_codes_per_div: float | None = _f(
        None, "WORD 每格码值", "Word codes per division", "Default: 256 × the byte codes", gt=0
    )
    byteorder: Literal["LSBFirst", "MSBFirst"] = _f("LSBFirst", "字节序", "Byte order", "At power-on")
    unsigned: bool = _f(False, "无符号", "Unsigned", "Codes unsigned at power-on")
    points_modes: list[str] = _f(
        [], "点数模式", "Points modes",
        "binary with a memory depth: keywords of a points mode setting, the screen's mode (at most 1000 points) "
        "first; empty: no such setting",
    )  # fmt: skip
    preamble: list[dict[str, Any]] = _f(
        [], "前导字段", "Preamble fields",
        "binary: the fields a preamble reply lists, in order ({figure: points}, {setting: …, map: …}, {value: 0}); "
        "empty: no preamble",
    )  # fmt: skip
    no_record: str = _f(
        "no acquisition for this source", "无记录时的说明", "No-record message",
        "The error text when a source has no record",
    )  # fmt: skip
    number_format: str = _f("%.6e", "数值格式", "Number format", "text: printf format of each value")
    separator: str = _f(" ", "分隔符", "Separator", "text: between values")
    descriptor: dict[str, str] = _f(
        {}, "描述块字段", "Descriptor fields",
        "text: name -> template of a descriptor reply ({points}, {dt:.9e}, {gain:.6e}…); empty: none",
    )  # fmt: skip
    window: bool = _f(
        False, "读取窗口设定", "Point window settings",
        "text: settings for the first point, count, step and segment of a read (waveform.fp/np/sp/sn)",
    )  # fmt: skip
    max_points: int | None = _f(
        None, "每次读取上限", "Points per read", "Most points one read returns; omit: no limit", gt=0
    )


class CommandsIn(_In):
    table: list[dict[str, Any]] = _f(
        [], "命令表", "Command table",
        "This model's own commands, from its manual: each a command of the definition (header and the setting "
        "or operation it binds, parameters, reply). The IEEE 488.2 common commands and :SYSTem:ERRor are "
        "built in; the rest starts empty",
    )  # fmt: skip
    unknown: Literal["error", "ignore"] = _f(
        "error",
        "未知命令",
        "Unknown commands",
        "error: -113; ignore: swallowed and logged (a contract bench)",
    )


class OscilloscopeInputs(_In):
    identity: IdentityIn = _f(..., "身份", "Identity", "Manufacturer, model, *IDN?")
    channels: ChannelsIn = _f(..., "通道", "Channels", "Inputs and their front ends")
    vertical: VerticalIn = _f(..., "垂直", "Vertical", "Scale table, offset, probe")
    adc: AdcIn = _f(..., "ADC", "ADC", "Converters and their sharing")
    timebase: TimebaseIn = _f(..., "时基", "Timebase", "Timebase and record")
    trigger: TriggerIn = _group(TriggerIn, "触发", "Trigger", "Edge trigger")
    acquisition: AcquisitionIn = _group(
        AcquisitionIn, "采集", "Acquisition", "Acquisition types, run control and synchronisation"
    )
    measurements: MeasurementsIn = _group(
        MeasurementsIn, "自动测量", "Measurements", "Measurements and their names"
    )
    transfer: TransferIn = _group(TransferIn, "波形传输", "Waveform transfer", "Encoding and its settings")
    settings: dict[str, dict[str, Any]] = _f(
        {}, "其他设定", "Other settings",
        "Settings the commands keep that no behaviour reads (a reference clock, a reply header switch): "
        "path -> the definition's setting",
    )  # fmt: skip
    syntax: dict[str, Any] = _f(
        {}, "报文写法", "Message syntax",
        "The definition's syntax: header echo, long form, number suffixes; empty: SCPI's",
    )  # fmt: skip
    format: dict[str, Any] = _f(
        {}, "应答格式", "Response format", "The definition's response format; empty: the default"
    )
    commands: CommandsIn = _group(CommandsIn, "命令", "Commands", "This model's command table")
    panel_actions: dict[str, str] = _f(
        {},
        "面板动作",
        "Panel actions",
        "The panel's buttons -> the command each sends, e.g. single: TRMD SINGLE",
    )
    specs: dict[str, Any] = _f(
        {},
        "技术指标",
        "Specifications",
        "The data sheet's figures as written there: a record, not used to compute",
    )


# ---- checks the schema cannot express --------------------------------------------------------------


def check(x: OscilloscopeInputs) -> list[InputProblem]:
    out: list[InputProblem] = []
    v, ch, tb, adc = x.vertical, x.channels, x.timebase, x.adc

    def bad(code: str, path: tuple[str | int, ...], message: str, suggestion: str | None = None) -> None:
        out.append(InputProblem(code, path, message, suggestion))

    if adc.bits and adc.codes_per_div:
        codes = 1 << adc.bits
        used = adc.codes_per_div * adc.divisions
        natural = codes / adc.divisions
        if used > codes:
            bad(
                "E-TPL-ADC-CODES", ("adc", "codes_per_div"),
                f"每格 {adc.codes_per_div:g} 码 × {adc.divisions:g} 格 = {used:g} 码，超过 {adc.bits} 位 ADC 的 {codes} 个码",
                f"减小每格码值（不写时按 2^位数/格数 = {natural:g}）",
            )  # fmt: skip
        elif used * 2 <= codes:
            out.append(
                InputProblem(
                    "W-TPL-ADC-CODES",
                    ("adc", "codes_per_div"),
                    f"每格 {adc.codes_per_div:g} 码只用了 {adc.bits} 位 ADC 的 {used / codes:.0%} 码值：屏幕上的"
                    f"分辨率相当于 {math.log2(used):.1f} 位",
                    f"不写每格码值（按 2^位数/格数 = {natural:g}），或改成手册的每格码值",
                    "warning",
                )
            )
    names = [z.name for z in ch.impedances]
    for i, name in enumerate(names):
        if names.index(name) != i:
            bad("E-TPL-DUPLICATE", ("channels", "impedances", i, "name"), f"输入阻抗关键字 {name} 重复")
    if "{n}" not in ch.keyword:
        bad(
            "E-TPL-MISSING",
            ("channels", "keyword"),
            f"通道名 {ch.keyword!r} 里要有 {{n}}（通道号）",
            "例如 CHANnel{n}",
        )
    keywords = [c.keyword for c in ch.couplings]
    for i, c in enumerate(ch.couplings):
        if keywords.index(c.keyword) != i:
            bad("E-TPL-DUPLICATE", ("channels", "couplings", i, "keyword"), f"耦合关键字 {c.keyword} 重复")
        if c.impedance is not None and c.impedance not in names:
            bad(
                "E-TPL-UNKNOWN", ("channels", "couplings", i, "impedance"),
                f"耦合 {c.keyword} 选择的输入 {c.impedance} 不在输入阻抗里", f"可用的输入：{', '.join(names)}",
            )  # fmt: skip
    for i, n in enumerate(ch.displayed):
        if not 1 <= n <= ch.count:
            bad("E-TPL-CHANNEL", ("channels", "displayed", i), f"通道 {n} 不存在（共 {ch.count} 个通道）")

    lo, hi = v.scale_range
    if not 0 < lo < hi:
        bad("E-TPL-RANGE", ("vertical", "scale_range"), f"档位范围 [{lo}, {hi}] 应满足 0 < 最小 < 最大")
    if not lo <= v.default_scale / v.default_probe <= hi:
        bad(
            "E-TPL-DEFAULT", ("vertical", "default_scale"), f"上电档位 {v.default_scale} V/div 不在档位范围内"
        )
    rows = v.table
    for i in range(1, len(rows)):
        if rows[i].v_div <= rows[i - 1].v_div:
            bad(
                "E-TPL-ORDER", ("vertical", "table", i, "v_div"),
                f"档位表应按 V/div 递增：第 {i} 行 {rows[i].v_div} 不大于上一行 {rows[i - 1].v_div}",
                "按从小到大的顺序列出档位",
            )  # fmt: skip
    for i, row in enumerate(rows):
        if not lo * (1 - 1e-9) <= row.v_div <= hi * (1 + 1e-9):
            bad(
                "E-TPL-RANGE",
                ("vertical", "table", i, "v_div"),
                f"档位 {row.v_div} V/div 不在档位范围 [{lo}, {hi}] 内",
            )
    for key, what in (
        ("offset_v", "偏置范围"), ("noise_v_rms", "噪声"), ("gain_error_pct", "增益误差限"),
        ("offset_error_v", "偏置误差限"),
    ):  # fmt: skip
        given = [getattr(r, key) is not None for r in rows]
        if any(given) and not all(given):
            for i, g in enumerate(given):
                if not g:
                    bad(
                        "E-TPL-MISSING-ROW", ("vertical", "table", i, key),
                        f"档位表的{what}缺第 {i} 行（{rows[i].v_div} V/div）：要么每行都给，要么都不给",
                    )  # fmt: skip
    if rows and rows[-1].offset_v is not None and rows[-1].v_div < hi * (1 - 1e-9):
        bad(
            "E-TPL-COVERAGE", ("vertical", "table"),
            f"偏置范围只覆盖到 {rows[-1].v_div} V/div，档位范围到 {hi} V/div",
            "在档位表末尾加上最大档位的一行",
        )  # fmt: skip
    plo, phi = v.probe_range
    if not 0 < plo <= v.default_probe <= phi:
        bad(
            "E-TPL-DEFAULT",
            ("vertical", "default_probe"),
            f"上电探头衰减比 {v.default_probe} 不在 [{plo}, {phi}] 内",
        )

    seen: dict[int, int] = {}
    for i, pair in enumerate(adc.rate_pairs):
        for j, n in enumerate(pair):
            if not 1 <= n <= ch.count:
                bad(
                    "E-TPL-CHANNEL", ("adc", "rate_pairs", i, j),
                    f"交织映射中的通道 {n} 不存在（共 {ch.count} 个通道）",
                )  # fmt: skip
            elif n in seen:
                bad("E-TPL-DUPLICATE", ("adc", "rate_pairs", i, j), f"通道 {n} 出现在两组交织中")
            seen[n] = i
    if adc.rate_pairs and adc.max_rate_paired is None:
        bad("E-TPL-MISSING", ("adc", "max_rate_paired"), "给了交织映射，还要给成对时的最高采样率")

    tlo, thi = tb.scale_range
    if not 0 < tlo < thi:
        bad("E-TPL-RANGE", ("timebase", "scale_range"), f"时基范围 [{tlo}, {thi}] 应满足 0 < 最小 < 最大")
    elif not tlo <= tb.default_scale <= thi:
        bad(
            "E-TPL-DEFAULT",
            ("timebase", "default_scale"),
            f"上电时基 {tb.default_scale} s/div 不在时基范围内",
        )
    if tb.memory_sizes is not None:
        m = tb.memory_sizes
        if not m.min <= m.default <= m.max:
            bad(
                "E-TPL-DEFAULT",
                ("timebase", "memory_sizes", "default"),
                f"上电存储深度 {m.default} 不在 [{m.min}, {m.max}] 内",
            )
    else:
        for i in range(1, len(tb.points)):
            if tb.points[i] <= tb.points[i - 1]:
                bad("E-TPL-ORDER", ("timebase", "points", i), "记录长度应递增")
        if tb.default_points not in tb.points:
            bad(
                "E-TPL-DEFAULT",
                ("timebase", "default_points"),
                f"上电记录长度 {tb.default_points} 不在记录长度列表中",
            )

    for i, m in enumerate(x.measurements.items):
        if m not in MEASUREMENTS:
            bad(
                "E-TPL-UNKNOWN", ("measurements", "items", i), f"没有测量 {m}",
                f"可用的测量：{', '.join(MEASUREMENTS)}",
            )  # fmt: skip
    for key in ("keywords", "units"):
        for m in getattr(x.measurements, key):
            if m not in x.measurements.items:
                bad(
                    "E-TPL-UNKNOWN", ("measurements", key, m), f"{m} 不在测量项里",
                    f"测量项：{', '.join(x.measurements.items)}",
                )  # fmt: skip
    if any(t.kind == "average" for t in x.acquisition.types) and not x.acquisition.average_counts:
        bad("E-TPL-MISSING", ("acquisition", "average_counts"), "有平均采集方式，还要给平均次数")
    if x.acquisition.after_single == "stop" and not any(m.meaning == "stop" for m in x.trigger.modes):
        bad(
            "E-TPL-MISSING", ("acquisition", "after_single"),
            "单次之后进入停止：触发方式里要有含义为 stop 的关键字",
        )  # fmt: skip
    if x.transfer.points_modes and (x.transfer.encoding != "binary" or tb.memory_depth is None):
        bad(
            "E-TPL-UNSUPPORTED", ("transfer", "points_modes"),
            "点数模式只用于二进制传输且给了存储深度的仪表",
        )  # fmt: skip
    return out


# ---- the definition ------------------------------------------------------------------------------


def _setting(**kw: Any) -> dict[str, Any]:
    return {k: v for k, v in kw.items() if v is not None}


def _coupling_param(x: OscilloscopeInputs) -> Any:
    """What the front end reads as its coupling: the setting itself when its keywords are the
    couplings' names, else mapped from them (D1M is DC)."""
    cs = x.channels.couplings
    if all(c.keyword == c.coupling for c in cs):
        return "$ch.coupling"
    return {"from": "$ch.coupling", "map": {c.keyword: c.coupling for c in cs}}


def _front(x: OscilloscopeInputs) -> dict[str, Any]:
    ch, v = x.channels, x.vertical
    rows = v.table
    front: dict[str, Any] = {
        "coupling": _coupling_param(x),
        "bandwidth": ch.bandwidth_hz,
        "probe_ratio": "$ch.probe",
    }
    if ch.response != "first_order":
        front["response"] = ch.response
    if ch.bandwidth_limit_hz:
        front |= {"bw_limit": "$ch.bwlimit", "bw_limit_hz": ch.bandwidth_limit_hz}
    if ch.ac_corner_hz != 10.0:
        front["ac_corner"] = ch.ac_corner_hz
    if rows:
        front["scale_range"] = list(v.scale_range)
    if rows and rows[0].offset_v is not None:
        front["offset_ranges"] = [[r.v_div, r.offset_v] for r in rows]
    if rows and rows[0].noise_v_rms is not None:
        front["noise_table"] = [[r.v_div, r.noise_v_rms] for r in rows]
    elif v.noise_divs:  # no table of its own: the assumed default, which scales with the division
        front["noise_divs"] = v.noise_divs
    if v.out_of_range != "error":
        front["out_of_range"] = v.out_of_range
    return front


def _input_port(x: OscilloscopeInputs) -> dict[str, Any]:
    """The input's resistance and capacitance, from the impedance setting or, when the coupling
    keywords name the input too, from the coupling setting."""
    ch = x.channels
    by_name = {z.name: z for z in ch.impedances}
    if ch.selects_input():
        first = ch.impedances[0].name
        chosen = {c.keyword: by_name[c.impedance or first] for c in ch.couplings}
        path = "$ch.coupling"
    else:
        chosen = dict(by_name)
        path = "$ch.impedance"
    caps = {z.capacitance_f for z in chosen.values()}
    return {
        "z_in": {"from": path, "map": {k: z.ohms for k, z in chosen.items()}},
        "c_in": caps.pop()
        if len(caps) == 1
        else {"from": path, "map": {k: z.capacitance_f for k, z in chosen.items()}},
    }


def _adc(x: OscilloscopeInputs) -> dict[str, Any]:
    adc = x.adc
    out: dict[str, Any] = {}
    if adc.bits:
        out |= {"bits": adc.bits, "divs": adc.divisions}
        if adc.codes_per_div:
            out["codes_per_div"] = adc.codes_per_div
    if adc.offset_sign != "add":
        out["offset_sign"] = adc.offset_sign
    if adc.max_rate:
        out["max_rate"] = adc.max_rate
    if adc.rate_pairs:
        out |= {"rate_pairs": adc.rate_pairs, "max_rate_paired": adc.max_rate_paired}
    return out


def _deviations(x: OscilloscopeInputs) -> list[dict[str, Any]]:
    rows, v, tb = x.vertical.table, x.vertical, x.timebase
    out: list[dict[str, Any]] = []
    by = {"scale": [r.v_div for r in rows]}
    if rows and rows[0].gain_error_pct is not None:
        limit = {r.v_div: r.gain_error_pct * 1e4 for r in rows}  # % -> ppm
        out.append({"param": "front.gain_error_ppm", "by": by, "limit": limit, "distribution": "uniform"})
    if rows and rows[0].offset_error_v is not None:
        limit = {r.v_div: r.offset_error_v for r in rows}
        out.append({"param": "front.offset_error", "by": by, "limit": limit, "distribution": "uniform"})
    if v.offset_dac_error_pct:
        out.append(
            {"param": "front.dac_error_ppm", "limit": v.offset_dac_error_pct * 1e4, "distribution": "uniform"}
        )
    if tb.clock_ppm:
        out.append({"param": "timebase.clock_ppm", "limit": tb.clock_ppm, "distribution": "uniform"})
    return out


def _sync(x: OscilloscopeInputs) -> dict[str, Any]:
    aq, tr = x.acquisition, x.trigger
    out: dict[str, Any] = {}
    for key in ("completion", "opc_waits", "while_waiting", "no_trigger"):
        if getattr(aq, key) != AcquisitionIn.model_fields[key].default:
            out[key] = getattr(aq, key)
    # The assumed default (#731, #735): a tenth of the fastest sample interval; when the ADC gives
    # no top rate, an assumed 100 ps — the same tenth for a 1 GSa/s scope, not a data-sheet figure.
    jitter = tr.jitter_s if tr.jitter_s is not None else (0.1 / x.adc.max_rate if x.adc.max_rate else 1.0e-10)
    if jitter:
        out["trigger_jitter_s"] = jitter
    return out


class _Derived(NamedTuple):
    """What every part of the definition is built from."""

    names: list[str]
    labels: dict[str, str]
    trig_sources: list[str]
    trig_labels: dict[str, str]
    offset_max: float
    has_types: bool
    window_max: int


def _derive(x: OscilloscopeInputs) -> _Derived:
    ch, v, tb, tr, aq = x.channels, x.vertical, x.timebase, x.trigger, x.acquisition
    names = [ch.name(n) for n in range(1, ch.count + 1)]
    labels = {ch.name(n): ch.label.replace("{n}", str(n)) for n in range(1, ch.count + 1)} if ch.label else {}
    trig_sources, trig_labels = list(names), dict(labels)
    for src in tr.external:
        trig_sources.append(src.keyword)
        if src.label:
            trig_labels[src.keyword] = src.label
    if tr.line:
        trig_sources.append(tr.line)
    rows = v.table
    offsets = [r.offset_v for r in rows if r.offset_v is not None]
    offset_max = max(offsets) * v.probe_range[1] if offsets else v.offset_max_v
    has_types = len(aq.types) > 1 or aq.types[0].kind != "normal"
    window_max = tb.memory_sizes.max if tb.memory_sizes else tb.points[-1]
    return _Derived(names, labels, trig_sources, trig_labels, offset_max, has_types, window_max)


def _state(x: OscilloscopeInputs, c: _Derived) -> dict[str, Any]:
    """Instrument-level settings."""
    tb, tr, aq, xf, ms = x.timebase, x.trigger, x.acquisition, x.transfer, x.measurements
    state: dict[str, Any] = {
        "timebase.scale": _setting(
            type="float", unit="S", min=tb.scale_range[0], max=tb.scale_range[1], default=tb.default_scale,
            ladder="1-2-5" if tb.ladder else None, label="时基", label_en="Timebase",
        ),
    }  # fmt: skip
    if tb.memory_sizes is not None:
        mem = tb.memory_sizes
        state["memory.size"] = _setting(
            type="int",
            min=mem.min,
            max=mem.max,
            default=mem.default,
            label="存储深度",
            label_en="Memory size",
        )
    modes = [m.keyword for m in tr.modes]
    state["trigger.mode"] = _setting(
        type="enum", options=modes, default=modes[0], label="触发方式", label_en="Trigger mode"
    )
    state["trigger.source"] = _setting(
        type="enum", options=c.trig_sources, option_labels=c.trig_labels or None, default=c.names[0],
        label="触发源", label_en="Trigger source",
    )  # fmt: skip
    if not tr.level_per_channel:
        state["trigger.level"] = _setting(
            type="float",
            unit="V",
            min=-1000.0,
            max=1000.0,
            default=0.0,
            label="触发电平",
            label_en="Trigger level",
        )
        state["trigger.slope"] = _setting(
            type="enum",
            options=list(tr.slopes),
            default=tr.slopes[0],
            label="触发沿",
            label_en="Trigger slope",
        )
    if xf.source_setting:
        state["waveform.source"] = _setting(
            type="enum", options=c.names, option_labels=c.labels or None, default=c.names[0],
            label="波形传输源", label_en="Waveform source",
        )  # fmt: skip
    if xf.encoding == "binary":
        fmts = [f.keyword for f in xf.formats]
        state |= {
            "waveform.format": _setting(
                type="enum", options=fmts, default=fmts[0], label="波形格式", label_en="Waveform format"
            ),
            "waveform.byteorder": _setting(
                type="enum", options=["LSBFirst", "MSBFirst"], default=xf.byteorder, label="字节序",
                label_en="Byte order",
            ),
            "waveform.unsigned": _setting(type="bool", default=xf.unsigned, label="无符号", label_en="Unsigned"),
        }  # fmt: skip
        if tb.memory_sizes is None:
            state["waveform.points"] = _setting(
                type="int", min=tb.points[0], max=tb.points[-1], default=tb.default_points, ladder=list(tb.points),
                label="记录长度", label_en="Record length",
            )  # fmt: skip
        if xf.points_modes:
            state["waveform.points_mode"] = _setting(
                type="enum", options=list(xf.points_modes), default=xf.points_modes[0],
                label="点数模式", label_en="Points mode",
            )  # fmt: skip
    if xf.window:
        for key in ("sp", "np", "fp"):
            state[f"waveform.{key}"] = {"type": "int", "min": 0, "max": c.window_max, "default": 0}
        state["waveform.sn"] = {"type": "int", "min": 0, "max": 10000, "default": 0}
    if c.has_types:
        types = [t.keyword for t in aq.types]
        state["acquire.type"] = _setting(
            type="enum", options=types, default=types[0], label="采集方式", label_en="Acquisition type"
        )
        if any(t.kind == "average" for t in aq.types):
            state["acquire.count"] = _setting(
                type="int", min=min(aq.average_counts), max=max(aq.average_counts), default=aq.average_counts[0],
                ladder=sorted(aq.average_counts), label="平均次数", label_en="Average count",
            )  # fmt: skip
    if ms.slots:
        kinds = [ms.keywords.get(m, m) for m in ms.items]
        for i in range(1, ms.slots + 1):
            state[f"measure.p{i}.kind"] = {
                "type": "enum",
                "options": [ms.empty_slot, *kinds],
                "default": ms.empty_slot,
            }
            state[f"measure.p{i}.source"] = {"type": "enum", "options": list(c.names), "default": ""}
    state |= x.settings
    return state


def _channel_state(x: OscilloscopeInputs, c: _Derived) -> dict[str, Any]:
    """Per-channel settings."""
    ch, v, tr = x.channels, x.vertical, x.trigger
    plo, phi = v.probe_range
    slo, shi = v.scale_range
    selects = ch.selects_input()
    coupling_labels = {k.keyword: k.label for k in ch.couplings if k.label}
    channel_state: dict[str, Any] = {
        "scale": _setting(
            type="float", unit="V", min=slo * plo, max=shi * phi, default=v.default_scale,
            ladder="1-2-5" if v.ladder else None, label="垂直档位", label_en="Scale",
        ),
        "offset": _setting(
            type="float", unit="V", min=-c.offset_max, max=c.offset_max, default=0.0, label="偏置", label_en="Offset"
        ),
        "coupling": _setting(
            type="enum", options=[k.keyword for k in ch.couplings], default=ch.couplings[0].keyword,
            option_labels=coupling_labels or None, electrical=True if selects else None,
            label="耦合", label_en="Coupling",
        ),
    }  # fmt: skip
    if not selects:
        channel_state["impedance"] = _setting(
            type="enum", options=[z.name for z in ch.impedances],
            option_labels={z.name: z.label for z in ch.impedances if z.label} or None,
            default=ch.impedances[0].name, electrical=True, label="输入阻抗", label_en="Input impedance",
        )  # fmt: skip
    channel_state["probe"] = _setting(
        type="float", display_unit="×", min=plo, max=phi, default=v.default_probe,
        label="探头衰减设定", label_en="Probe ratio",
    )  # fmt: skip
    if ch.bandwidth_limit_hz:
        channel_state["bwlimit"] = _setting(
            type="bool", default=False, label="带宽限制", label_en="Bandwidth limit"
        )
    channel_state["display"] = _setting(type="bool", default=False, label="显示", label_en="Display")
    if tr.level_per_channel:
        channel_state["trigger_level"] = _setting(
            type="float", unit="V", min=-c.offset_max, max=c.offset_max, default=0.0,
            label="触发电平", label_en="Trigger level",
        )  # fmt: skip
        channel_state["trigger_slope"] = _setting(
            type="enum",
            options=list(tr.slopes),
            default=tr.slopes[0],
            label="触发沿",
            label_en="Trigger slope",
        )
    return channel_state


def _acquisition(x: OscilloscopeInputs, c: _Derived) -> dict[str, Any]:
    """The acquisition component's parameters."""
    tr, aq = x.trigger, x.acquisition
    acq: dict[str, Any] = {
        "horizontal": "timebase", "trigger": "trigger", "adc": "adc", "vertical": "front", "probe": "probe",
    }  # fmt: skip
    single = [m.keyword for m in tr.modes if m.meaning == "single"]
    stop = [m.keyword for m in tr.modes if m.meaning == "stop"]
    if single:
        acq["single"] = single
    if stop:
        acq["stop"] = stop
    if aq.after_single == "stop":
        acq["after_single"] = stop[0]
    for key, param in (
        ("reacquire", "reacquire"),
        ("missing_record", "missing"),
        ("untriggered", "untriggered"),
    ):
        if getattr(aq, key) != AcquisitionIn.model_fields[key].default:
            acq[param] = getattr(aq, key)
    acq |= _sync(x)
    if c.has_types:
        acq["processing"] = {
            "from": "$acquire.type", "map": {t.keyword: t.kind for t in aq.types if t.kind != "normal"},
            "default": "normal",
        }  # fmt: skip
        if any(t.kind == "average" for t in aq.types):
            acq["averages"] = "$acquire.count"
    return acq


def _timebase_trigger(x: OscilloscopeInputs) -> tuple[dict[str, Any], dict[str, Any]]:
    """The timebase and trigger components' parameters."""
    tb, tr = x.timebase, x.trigger
    timebase: dict[str, Any] = {}
    if tb.memory_sizes is not None:
        timebase |= {"record_length": "exact", "points": "$memory.size"}
    elif tb.memory_depth:
        timebase |= {"record_length": "rate_ladder", "points": tb.memory_depth}
    if tb.reference_divs != 5.0:
        timebase["reference_divs"] = tb.reference_divs
    trigger: dict[str, Any] = {}
    if tr.level_per_channel:
        trigger |= {"level": "$ch{n}.trigger_level", "slope": "$ch{n}.trigger_slope"}
    if tr.auto_windows != 4:
        trigger["search_windows"] = tr.auto_windows
    if tr.hysteresis_divs != 0.16:
        trigger["hysteresis_divs"] = tr.hysteresis_divs
    if tr.level_range_divs:
        trigger["level_range_divs"] = tr.level_range_divs
    if tr.slope_edges:
        # The model's own slope words, with the edge each takes, beside the component's usual ones.
        trigger["rising"] = [
            *RISING_WORDS,
            *(k for k, e in tr.slope_edges.items() if e in ("rising", "either")),
        ]
        trigger["falling"] = [
            *FALLING_WORDS,
            *(k for k, e in tr.slope_edges.items() if e in ("falling", "either")),
        ]
    return timebase, trigger


#: The slope words the trigger component takes without being told (scope.RISING / FALLING).
RISING_WORDS = ["POSitive", "EITHer", "RISing", "ALTernate"]
FALLING_WORDS = ["NEGative", "EITHer", "FALLing", "ALTernate"]


def _measurements(x: OscilloscopeInputs) -> dict[str, Any]:
    ms = x.measurements
    params: dict[str, Any] = (
        {"source": "$waveform.source"} if x.transfer.source_setting else {"record": "acq"}
    )
    if ms.named():
        params["names"] = {ms.keywords.get(m, m): [m, ms.units.get(m, "V")] for m in ms.items}
    return params


def _transfer(x: OscilloscopeInputs, c: _Derived) -> dict[str, Any]:
    """The waveform_transfer component's parameters."""
    xf, tb = x.transfer, x.timebase
    if xf.encoding == "text":
        text: dict[str, Any] = {}
        if xf.window:
            text |= {"first": "$waveform.fp", "count": "$waveform.np", "step": "$waveform.sp"}
        if xf.max_points:
            text["max_points"] = xf.max_points
        text |= {"encoding": "text", "values": "volts", "format": xf.number_format, "separator": xf.separator}
        if xf.descriptor:
            text["descriptor"] = dict(xf.descriptor)
        return text
    adc = x.adc
    per_div = adc.codes_per_div or ((1 << adc.bits) / adc.divisions if adc.bits else 32.0)
    byte_cpd = xf.byte_codes_per_div or per_div
    word_cpd = xf.word_codes_per_div or byte_cpd * 256
    kinds = {f.keyword: f.kind for f in xf.formats}
    codes = {k: kind for k, kind in kinds.items() if kind != "ascii"}
    xfer: dict[str, Any] = {
        "encoding": {"from": "$waveform.format", "map": {k: "ascii" if kind == "ascii" else "block" for k, kind in kinds.items()}},
        "values": {"from": "$waveform.format", "map": {k: "volts" for k, kind in kinds.items() if kind == "ascii"}, "default": "codes"},
        "codes_per_div": {"from": "$waveform.format", "map": {k: byte_cpd if kind == "byte" else word_cpd for k, kind in codes.items()}, "default": byte_cpd},
        "adc_bits": {"from": "$waveform.format", "map": {k: 8 if kind == "byte" else 16 for k, kind in codes.items()}, "default": 8},
        "width": {"from": "$waveform.format", "map": {k: 8 if kind == "byte" else 16 for k, kind in codes.items()}, "default": 8},
        "unsigned": "$waveform.unsigned",
        "byteorder": "$waveform.byteorder",
        "no_record": xf.no_record,
    }  # fmt: skip
    if xf.source_setting:
        xfer["source"] = "$waveform.source"
    xfer["offset"] = adc.offset_sign  # the ADC's convention, spelled out: the two classes' defaults differ
    if tb.memory_depth is not None and tb.memory_sizes is None:
        if xf.points_modes:  # the screen's mode: 1000 points at most
            xfer |= {
                "max_points": {
                    "from": "$waveform.points_mode",
                    "map": {xf.points_modes[0]: 1000},
                    "default": 0,
                },
                "over_limit": "truncate",
            }
        xfer |= {"count": "$waveform.points", "count_mode": "spread"}
    elif xf.max_points:
        xfer["max_points"] = xf.max_points
    if xf.preamble:
        # The preamble is declared, not coded: the fields a preamble reply lists, in order.
        xfer["header"] = {"encoding": "text", "separator": ",", "fields": [dict(f) for f in xf.preamble]}
    return xfer


def build(x: OscilloscopeInputs) -> dict[str, Any]:
    c = _derive(x)
    ch, tr = x.channels, x.trigger
    timebase, trigger = _timebase_trigger(x)
    commands = [dict(cmd) for cmd in x.commands.table]
    channels: dict[str, Any] = {"count": ch.count}
    shown = {n: {"display": {"default": True}} for n in ch.displayed}
    if shown:
        channels["overrides"] = shown
    components = [
        {"name": "acq", "type": "scope_acquisition", "params": _acquisition(x, c)},
        {"name": "timebase", "type": "scope_timebase", "params": timebase},
        {"name": "trigger", "type": "scope_trigger", "params": trigger},
        {"name": "adc", "type": "scope_adc", "params": _adc(x)},
        {"name": "meas", "type": "scope_measurements", "params": _measurements(x)},
        {"name": "xfer", "type": "waveform_transfer", "params": _transfer(x, c)},
    ]
    if any(str(cmd.get("op") or "").startswith("sys.") for cmd in commands):
        components.insert(0, {"name": "sys", "type": "system"})  # *CLS, a preset: the commands use it
    summary = ["timebase.scale", *(["memory.size"] if x.timebase.memory_sizes else [])]
    summary += (
        ["trigger.mode", "trigger.source"]
        if tr.level_per_channel
        else ["trigger.source", "trigger.level", "trigger.slope"]
    )
    channel_summary = ["display", "scale", "offset", "coupling"]
    channel_summary += [] if ch.selects_input() else ["impedance"]
    channel_summary += ["trigger_level"] if tr.level_per_channel else ["probe"]
    display: dict[str, Any] = {
        "template": "oscilloscope",
        "summary": summary,
        "channel_summary": channel_summary,
    }
    if x.panel_actions:
        display["actions"] = dict(x.panel_actions)
    probe: dict[str, Any] = {"rescales": ["$ch.scale", "$ch.offset"]} if x.vertical.probe_rescales else {}
    out: dict[str, Any] = {
        "manufacturer": x.identity.manufacturer,
        "model": x.identity.model,
        "class": "oscilloscope",
        "description": x.identity.description,
        "identity": x.identity.block(),
        "channels": channels,
        "state": _state(x, c),
        "channel_state": _channel_state(x, c),
        "components": components,
        "channel_components": [
            {"name": "inp", "type": "input_port", "params": _input_port(x)},
            {"name": "front", "type": "scope_vertical", "params": _front(x)},
            {"name": "probe", "type": "scope_probe", "params": probe},
        ],
        "wiring": {
            "channel": [["inp.out", "front.in"], ["front.out", "probe.in"]],
            "instrument": [["ch{n}.probe.out", "acq.in{n}"]],
        },
        "ports": {"channel": {"CH{n}": "inp.pin"}},
        "display": display,
        "capabilities": ["oscilloscope"],
        "commands": commands,
    }
    if x.syntax:
        out["syntax"] = dict(x.syntax)
    if x.format:
        out["format"] = dict(x.format)
    if tr.external:
        out["components"].append({"name": "extin", "type": "input_port", "params": {"z_in": 1.0e6}})
        out["wiring"]["instrument"].append(["extin.out", "trigger.ext"])
        out["ports"]["instrument"] = {"EXT": "extin.pin"}
    if x.commands.unknown != "error":
        out["unknown_commands"] = x.commands.unknown
    if x.specs:
        out["specs"] = x.specs
    deviations = _deviations(x)
    if deviations:
        out["deviations"] = deviations
    if not out["description"]:
        del out["description"]
    return out


#: The example's own command table: an ACME DSO-204 as its manual lists it (data, not a command set).
EXAMPLE_COMMANDS: list[dict[str, Any]] = [
        {"header": ":CHANnel<ch>:SCALe", "state": "ch.scale"},
        {"header": ":CHANnel<ch>:OFFSet", "state": "ch.offset"},
        {"header": ":CHANnel<ch>:COUPling", "state": "ch.coupling"},
        {"header": ":CHANnel<ch>:IMPedance", "state": "ch.impedance"},
        {"header": ":CHANnel<ch>:PROBe", "state": "ch.probe"},
        {"header": ":CHANnel<ch>:BWLimit", "state": "ch.bwlimit"},
        {"header": ":CHANnel<ch>:DISPlay", "state": "ch.display"},
        {"header": ":TIMebase[:MAIN]:SCALe", "state": "timebase.scale"},
        {"header": ":TRIGger[:EDGE]:SOURce", "state": "trigger.source"},
        {"header": ":TRIGger[:EDGE]:LEVel", "state": "trigger.level"},
        {"header": ":TRIGger[:EDGE]:SLOPe", "state": "trigger.slope"},
        {"header": ":TRIGger:SWEep", "state": "trigger.mode"},
        {"header": ":DIGitize", "op": "acq.digitize", "query": False, "params": [{"name": "sources", "type": "raw"}]},
        {"header": ":SINGle", "op": "acq.single", "query": False},
        {"header": ":RUN", "op": "acq.run", "query": False},
        {"header": ":STOP", "op": "acq.stop", "query": False},
        {"header": ":ACQuire:TYPE", "state": "acquire.type"},
        {"header": ":ACQuire:COUNt", "state": "acquire.count"},
        {"header": ":WAVeform:SOURce", "state": "waveform.source"},
        {"header": ":WAVeform:FORMat", "state": "waveform.format"},
        {"header": ":WAVeform:BYTeorder", "state": "waveform.byteorder"},
        {"header": ":WAVeform:UNSigned", "state": "waveform.unsigned"},
        {"header": ":WAVeform:POINts", "state": "waveform.points"},
        {"header": ":WAVeform:PREamble", "op": "xfer.header", "set": False},
        {"header": ":WAVeform:DATA", "op": "xfer.data", "set": False},
        {"header": ":MEASure:VPP", "op": "meas.measure", "set": False, "args": {"what": "VPP"}, "params": [{"name": "source", "type": "string", "optional": True}]},
        {"header": ":MEASure:VMAX", "op": "meas.measure", "set": False, "args": {"what": "VMAX"}, "params": [{"name": "source", "type": "string", "optional": True}]},
        {"header": ":MEASure:VMIN", "op": "meas.measure", "set": False, "args": {"what": "VMIN"}, "params": [{"name": "source", "type": "string", "optional": True}]},
        {"header": ":MEASure:VAMPlitude", "op": "meas.measure", "set": False, "args": {"what": "VAMPlitude"}, "params": [{"name": "source", "type": "string", "optional": True}]},
        {"header": ":MEASure:VRMS", "op": "meas.measure", "set": False, "args": {"what": "VRMS"}, "params": [{"name": "source", "type": "string", "optional": True}]},
        {"header": ":MEASure:VAVerage", "op": "meas.measure", "set": False, "args": {"what": "VAVerage"}, "params": [{"name": "source", "type": "string", "optional": True}]},
        {"header": ":MEASure:FREQuency", "op": "meas.measure", "set": False, "args": {"what": "FREQuency"}, "params": [{"name": "source", "type": "string", "optional": True}]},
        {"header": ":MEASure:PERiod", "op": "meas.measure", "set": False, "args": {"what": "PERiod"}, "params": [{"name": "source", "type": "string", "optional": True}]},
]  # fmt: skip

#: The example's preamble reply, field by field (its manual's :WAVeform:PREamble).
EXAMPLE_PREAMBLE: list[dict[str, Any]] = [
    {"setting": "waveform.format", "map": {"BYTE": 0, "WORD": 1, "ASCii": 4}},
    {"setting": "acquire.type", "map": {"NORMal": 0, "PEAK": 1, "AVERage": 2}},
    {"figure": "points"},
    {"setting": "acquire.count", "when": {"acquire.type": "AVERage"}, "else": 1},
    {"figure": "x_increment", "format": "%+.6E"},
    {"figure": "x_start", "format": "%+.6E"},
    {"value": 0},
    {"figure": "y_increment", "format": "%+.6E"},
    {"figure": "y_origin", "format": "%+.6E"},
    {"figure": "y_reference"},
]


def example() -> dict[str, Any]:
    """A 4-channel 200 MHz, 8-bit scope with a data sheet's scale table and deviation ranges, and
    the command table of its manual."""
    table = [
        {"v_div": 0.001, "offset_v": 2.0, "noise_v_rms": 1.0e-4, "gain_error_pct": 4.0, "offset_error_v": 0.0021},
        {"v_div": 0.01, "offset_v": 2.0, "noise_v_rms": 2.0e-4, "gain_error_pct": 3.0, "offset_error_v": 0.003},
        {"v_div": 0.2, "offset_v": 2.0, "noise_v_rms": 2.0e-3, "gain_error_pct": 3.0, "offset_error_v": 0.022},
        {"v_div": 5.0, "offset_v": 50.0, "noise_v_rms": 5.0e-2, "gain_error_pct": 3.0, "offset_error_v": 0.502},
    ]  # fmt: skip
    return {
        "identity": {"manufacturer": "ACME", "model": "DSO-204", "serial": "AC2040001", "firmware": "2.1.0"},
        "channels": {
            "count": 4,
            "impedances": [{"name": "ONEMeg", "ohms": 1.0e6, "capacitance_f": 1.1e-11, "label": "1 MΩ"}],
            "bandwidth_hz": 2.0e8,
            "bandwidth_limit_hz": 2.0e7,
        },
        "vertical": {
            "scale_range": [0.001, 5.0],
            "default_scale": 1.0,
            "table": table,
            "out_of_range": "clamp",
            "probe_range": [0.1, 1000.0],
            "default_probe": 1.0,
            "probe_rescales": True,
            "offset_dac_error_pct": 1.0,
        },
        "adc": {
            "bits": 8,
            "codes_per_div": 25.0,
            "divisions": 10.0,
            "max_rate": 2.0e9,
            "max_rate_paired": 1.0e9,
            "rate_pairs": [[1, 2], [3, 4]],
        },
        "timebase": {
            "scale_range": [2.0e-9, 50.0],
            "default_scale": 1.0e-3,
            "points": [100, 250, 500, 1000, 2000, 5000, 10000],
            "default_points": 1000,
            "memory_depth": 100000,
            "clock_ppm": 25.0,
        },
        "trigger": {
            "modes": [
                {"keyword": "AUTO", "meaning": "auto"},
                {"keyword": "NORMal", "meaning": "normal"},
                {"keyword": "SINGle", "meaning": "single"},
            ],
            "external": [{"keyword": "EXTernal", "label": "EXT"}],
            "jitter_s": 1.0e-11,
        },
        "acquisition": {
            "types": [
                {"keyword": "NORMal", "kind": "normal"},
                {"keyword": "AVERage", "kind": "average"},
                {"keyword": "PEAK", "kind": "peak"},
            ],
            "completion": "pending",
        },
        "transfer": {
            "formats": [
                {"keyword": "BYTE", "kind": "byte"},
                {"keyword": "WORD", "kind": "word"},
                {"keyword": "ASCii", "kind": "ascii"},
            ],
            "byteorder": "MSBFirst",
            "unsigned": True,
            "preamble": EXAMPLE_PREAMBLE,
        },
        "commands": {"table": EXAMPLE_COMMANDS},
        "specs": {
            "bandwidth": "200 MHz",
            "dc_gain_accuracy": "±3 % full scale (≥ 10 mV/div); ±4 % (< 10 mV/div)",
        },
    }


# ---- drafts from a script (#145, D81) ---------------------------------------------------------------


def _kw(word: str) -> str:
    """A SCPI keyword as a regex over an upper-cased header node: its short form or its long form."""
    short = "".join(ch for ch in word if ch.isupper() or ch.isdigit() or ch == "_") or word.upper()
    long = word.upper()
    return f"(?:{re.escape(short)}|{re.escape(long)})" if short != long else re.escape(long)


def _any(*words: str) -> str:
    return "(?:" + "|".join(_kw(w) for w in words) + ")"


_CH = r"(?:C|CH|CHAN|CHANNEL)#"
_TRIG = _any("TRIGger") + "(?::" + _any("EDGE") + ")?"
_WAV = _any("WAVeform")


def _measure(m: re.Match[str]) -> dict[str, Any] | None:
    word = m.group("item")
    for name in MEASUREMENTS:
        if re.fullmatch(_kw(name), word):
            return {
                "op": "meas.measure", "set": False, "args": {"what": name},
                "params": [{"name": "source", "type": "string", "optional": True}],
            }  # fmt: skip
    return None


#: Spellings many scopes share, each for one setting or operation (D81: it binds a command, it does
#: not pick a vendor). Used only where no library model knows the script's header.
MNEMONICS = [
    Mnemonic(f"{_CH}:{_any('SCALe', 'VDIV', 'VOLT_DIV')}", {"state": "ch.scale"}),
    Mnemonic(f"{_CH}:{_any('OFFSet', 'OFST')}", {"state": "ch.offset"}),
    Mnemonic(f"{_CH}:{_any('COUPling', 'CPL')}", {"state": "ch.coupling"}),
    Mnemonic(f"{_CH}:{_any('IMPedance')}", {"state": "ch.impedance"}),
    Mnemonic(f"{_CH}:{_any('PROBe', 'ATTN', 'ATTeNuation')}", {"state": "ch.probe"}),
    Mnemonic(f"{_CH}:{_any('DISPlay', 'TRAce')}", {"state": "ch.display"}),
    Mnemonic(f"{_CH}:{_any('BWLimit')}", {"state": "ch.bwlimit"}),
    Mnemonic(f"{_CH}:{_any('TRLV', 'TRIG_LEVEL')}", {"state": "ch.trigger_level"}),
    Mnemonic(f"{_CH}:{_any('TRSL', 'TRIG_SLOPE')}", {"state": "ch.trigger_slope"}),
    Mnemonic(
        f"(?:{_any('TIMebase')}(?::{_any('MAIN')})?:{_any('SCALe')}|{_any('TDIV', 'TIME_DIV')})",
        {"state": "timebase.scale"},
    ),
    Mnemonic(f"{_TRIG}:{_any('SOURce')}", {"state": "trigger.source"}),
    Mnemonic(f"{_TRIG}:{_any('LEVel')}", {"state": "trigger.level"}),
    Mnemonic(f"{_TRIG}:{_any('SLOPe')}", {"state": "trigger.slope"}),
    Mnemonic(
        f"(?:{_any('TRIGger')}:{_any('SWEep', 'MODE')}|{_any('TRMD', 'TRIG_MODE')})",
        {"state": "trigger.mode"},
    ),
    Mnemonic(
        _any("DIGitize"),
        {"op": "acq.digitize", "query": False, "params": [{"name": "sources", "type": "raw"}]},
    ),
    Mnemonic(_any("SINGle", "ARM_acquisition", "ARM"), {"op": "acq.single", "query": False}),
    Mnemonic(_any("RUN"), {"op": "acq.run", "query": False}),
    Mnemonic(_any("STOP"), {"op": "acq.stop", "query": False}),
    Mnemonic(f"{_any('ACQuire')}:{_any('TYPE')}", {"state": "acquire.type"}),
    Mnemonic(f"{_any('ACQuire')}:{_any('COUNt')}", {"state": "acquire.count"}),
    Mnemonic(
        f"(?:{_any('MSIZ', 'MEMORY_SIZE')}|{_any('ACQuire')}:{_any('MDEPth')})", {"state": "memory.size"}
    ),
    Mnemonic(f"{_WAV}:{_any('SOURce')}", {"state": "waveform.source"}),
    Mnemonic(f"{_WAV}:{_any('FORMat')}", {"state": "waveform.format"}),
    Mnemonic(f"{_WAV}:{_any('BYTeorder')}", {"state": "waveform.byteorder"}),
    Mnemonic(f"{_WAV}:{_any('UNSigned')}", {"state": "waveform.unsigned"}),
    Mnemonic(f"{_WAV}:{_any('POINts')}:{_any('MODE')}", {"state": "waveform.points_mode"}),
    Mnemonic(f"{_WAV}:{_any('POINts')}", {"state": "waveform.points"}),
    Mnemonic(f"{_WAV}:{_any('PREamble')}", {"op": "xfer.header", "set": False}),
    Mnemonic(f"{_WAV}:{_any('DATA')}", {"op": "xfer.data", "set": False}),
    Mnemonic(f"{_any('MEASure')}:(?P<item>\\w+)", _measure),
]


def _draft_base() -> dict[str, Any]:
    """Generic inputs a data sheet would replace; no commands (the draft learns them)."""
    return {
        "identity": {"manufacturer": "Unknown", "model": "SCOPE"},
        "channels": {
            "count": 4,
            "impedances": [
                {"name": "ONEMeg", "ohms": 1.0e6, "capacitance_f": 1.5e-11, "label": "1 MΩ"},
                {"name": "FIFTy", "ohms": 50.0, "capacitance_f": 1.5e-11, "label": "50 Ω"},
            ],
            "bandwidth_hz": 2.0e8,
            "couplings": [
                {"keyword": "DC", "coupling": "DC"},
                {"keyword": "AC", "coupling": "AC"},
                {"keyword": "GND", "coupling": "GND"},
            ],
        },
        "vertical": {"scale_range": [1.0e-3, 10.0], "default_scale": 1.0, "probe_range": [1.0e-4, 1.0e4]},
        "adc": {"bits": 8, "divisions": 10.0, "max_rate": 2.0e9},
        "timebase": {
            "scale_range": [1.0e-11, 1000.0],
            "points": [100, 250, 500, 1000, 2000, 5000, 10000, 100000],
        },
        "commands": {"table": []},
    }


def _basics(x: dict[str, Any], commands: list[Any], hints: dict[str, Any]) -> list[Any]:
    from .draft import channels_used, default, found

    filled = []
    model = hints.get("model")
    if model:
        x["identity"]["model"] = model[0]
        filled.append(found(("identity", "model"), model[0], model[1], "*IDN? 校验的型号串"))
    else:
        filled.append(default(("identity", "model"), "SCOPE"))
    filled.append(default(("identity", "manufacturer"), "Unknown"))
    used = channels_used(commands)
    if used:
        n, line = max(used)
        x["channels"]["count"] = max(n, 4)
        filled.append(
            found(("channels", "count"), x["channels"]["count"], line,
                  f"脚本中最大的通道号为 {n}（循环里的通道号读作 1）；少于 4 时按 4 通道，待确认")
        )  # fmt: skip
    for path in (
        ("channels", "bandwidth_hz"),
        ("vertical", "scale_range"),
        ("timebase", "scale_range"),
        ("adc", "bits"),
    ):
        node: Any = x
        for key in path:
            node = node[key]
        filled.append(default(path, node))
    filled.append(
        default(
            ("acquisition", "completion"), "immediate", "单次采集当场完成；真机在虚拟时间中完成时改为 pending"
        )
    )
    filled.append(default(("commands", "unknown"), "error", "契约若要求忽略未建模命令，改为 ignore"))
    return filled


def _component(d: Any, type_name: str) -> dict[str, Any]:
    comp = next((c for c in [*d.components, *d.channel_components] if c.type == type_name), None)
    return dict(comp.params) if comp is not None else {}


def _learned(path: tuple[str | int, ...], value: Any, teacher: Any) -> Any:
    from . import Filled, pointer

    return Filled(
        pointer(*path),
        value,
        "library",
        None,
        f"照 {teacher.id}" if teacher is not None else "默认值，待确认",
    )


def _enable(x: dict[str, Any], target: str, teacher: Any) -> list[Any]:
    """Switch on, or describe as ``teacher`` has it, what a learned command binds (D81)."""
    out: list[Any] = []
    ch, tb, tr, aq, xf, ms = (
        x.setdefault(k, {})
        for k in ("channels", "timebase", "trigger", "acquisition", "transfer", "measurements")
    )

    def put(group: dict[str, Any], path: tuple[str, ...], key: str, value: Any) -> None:
        if group.get(key) != value:
            group[key] = value
            out.append(_learned((*path, key), value, teacher))

    t_state = teacher.state if teacher is not None else {}
    t_chan = teacher.channel_state if teacher is not None else {}
    if target == "memory.size":
        spec = t_state.get("memory.size")
        sizes = (
            {"min": int(spec.min), "max": int(spec.max), "default": int(spec.default)}
            if spec
            else {"min": 500, "max": 50_000_000, "default": 10_000}
        )
        if not tb.get("memory_sizes"):
            put(tb, ("timebase",), "memory_sizes", sizes)
    elif target in ("ch.trigger_level", "ch.trigger_slope"):
        put(tr, ("trigger",), "level_per_channel", True)
        if teacher is not None and "trigger_slope" in t_chan and t_chan["trigger_slope"].options:
            slopes = list(t_chan["trigger_slope"].options)
            put(tr, ("trigger",), "slopes", slopes)
            known = {w.upper() for w in (*RISING_WORDS, *FALLING_WORDS)}
            guess = (
                ("POS", "rising"),
                ("RIS", "rising"),
                ("NEG", "falling"),
                ("FALL", "falling"),
                ("EITH", "either"),
            )
            edges = {
                k: next((e for p, e in guess if k.upper().startswith(p)), "rising")
                for k in slopes
                if k.upper() not in known
            }
            if edges:
                put(tr, ("trigger",), "slope_edges", edges)
    elif target == "ch.bwlimit":
        if not ch.get("bandwidth_limit_hz"):
            put(ch, ("channels",), "bandwidth_limit_hz", 2.0e7)
    elif target == "waveform.points_mode":
        spec = t_state.get("waveform.points_mode")
        put(
            xf,
            ("transfer",),
            "points_modes",
            list(spec.options) if spec and spec.options else ["NORMal", "MAXimum", "RAW"],
        )
        if not tb.get("memory_depth"):
            put(tb, ("timebase",), "memory_depth", 100_000)
    elif target in ("acquire.type", "acquire.count"):
        spec = t_state.get("acquire.type")
        processing = (
            _component(teacher, "scope_acquisition").get("processing") if teacher is not None else None
        )
        kinds = processing.get("map", {}) if isinstance(processing, dict) else {}
        keywords = list(spec.options) if spec and spec.options else ["NORMal", "AVERage", "PEAK"]
        guess = {"AVER": "average", "PEAK": "peak"}
        types = [
            {
                "keyword": k,
                "kind": kinds.get(k)
                or next((v for p, v in guess.items() if k.upper().startswith(p)), "normal"),
            }
            for k in keywords
        ]
        if not any(t["kind"] != "normal" for t in aq.get("types", [])):
            put(aq, ("acquisition",), "types", types)
    elif target in ("waveform.fp", "waveform.np", "waveform.sp", "waveform.sn", "xfer.describe") or (
        target in ("xfer.data", "xfer.header") and teacher is not None
    ):
        params = _component(teacher, "waveform_transfer") if teacher is not None else {}
        if params.get("encoding") == "text" or target.startswith("waveform.") or target == "xfer.describe":
            put(xf, ("transfer",), "encoding", "text")
            put(
                xf,
                ("transfer",),
                "source_setting",
                "waveform.source" in t_state if teacher is not None else False,
            )
            for key, param in (
                ("number_format", "format"),
                ("separator", "separator"),
                ("max_points", "max_points"),
            ):
                if isinstance(params.get(param), (str, int)) and not isinstance(params.get(param), bool):
                    put(xf, ("transfer",), key, params[param])
            if isinstance(params.get("descriptor"), dict):
                put(xf, ("transfer",), "descriptor", dict(params["descriptor"]))
            if params.get("first") or target.startswith("waveform."):
                put(xf, ("transfer",), "window", True)
        elif isinstance(params.get("header"), dict) and params["header"].get("fields"):
            put(xf, ("transfer",), "preamble", [dict(f) for f in params["header"]["fields"]])
    elif target.startswith(("measure.p", "meas.")):
        if target.startswith("measure.p"):
            slots = sum(1 for k in t_state if re.fullmatch(r"measure\.p\d+\.kind", k)) or 8
            put(ms, ("measurements",), "slots", slots)
            kind = t_state.get("measure.p1.kind")
            if kind is not None and kind.options:
                put(ms, ("measurements",), "empty_slot", kind.options[0])
        names = _component(teacher, "scope_measurements").get("names") if teacher is not None else None
        if isinstance(names, dict) and names:
            known = {k: v for k, v in names.items() if isinstance(v, list) and v and v[0] in MEASUREMENTS}
            put(ms, ("measurements",), "items", list(dict.fromkeys(v[0] for v in known.values())))
            put(ms, ("measurements",), "keywords", {v[0]: k for k, v in known.items()})
            put(ms, ("measurements",), "units", {v[0]: v[1] for v in known.values() if len(v) > 1})
    elif target == "ch.coupling" and teacher is not None and "coupling" in t_chan:
        spec = t_chan["coupling"]
        keywords = list(spec.options or [])
        front = next((c for c in teacher.channel_components if c.type == "scope_vertical"), None)
        inp = next((c for c in teacher.channel_components if c.type == "input_port"), None)
        cmap = front.params.get("coupling") if front is not None else None
        kinds = cmap.get("map", {}) if isinstance(cmap, dict) else {}
        zmap = inp.params.get("z_in") if inp is not None else None
        ohms = zmap.get("map", {}) if isinstance(zmap, dict) and zmap.get("from") == "$ch.coupling" else {}
        by_ohms = {float(z["ohms"]): z["name"] for z in ch.get("impedances", [])}
        labels = spec.option_labels or {}
        couplings = []
        for k in keywords:
            entry: dict[str, Any] = {
                "keyword": k,
                "coupling": kinds.get(k, k if k in ("DC", "AC", "GND") else "DC"),
            }
            if k in ohms and kinds.get(k) != "GND":
                entry["impedance"] = by_ohms.get(float(ohms[k]))
            if labels.get(k):
                entry["label"] = labels[k]
            couplings.append(entry)
        if couplings and [c["keyword"] for c in ch.get("couplings", [])] != keywords:
            put(ch, ("channels",), "couplings", couplings)
    elif target in ("trigger.mode", "acq.run_mode", "acq.single", "acq.stop") and teacher is not None:
        params = _component(teacher, "scope_acquisition")
        single = set(params.get("single") or [])
        stop = set(params.get("stop") or [])
        spec = t_state.get("trigger.mode")
        if spec is not None and spec.options:
            modes = [
                {
                    "keyword": k,
                    "meaning": "single"
                    if k in single
                    else "stop"
                    if k in stop
                    else "auto"
                    if k.upper().startswith("AUTO")
                    else "normal",
                }
                for k in spec.options
            ]
            if [m["keyword"] for m in tr.get("modes", [])] != list(spec.options):
                put(tr, ("trigger",), "modes", modes)
        if params.get("after_single") and stop:
            put(aq, ("acquisition",), "after_single", "stop")
        for key, param in (
            ("reacquire", "reacquire"),
            ("missing_record", "missing"),
            ("untriggered", "untriggered"),
        ):
            if param in params:
                put(aq, ("acquisition",), key, params[param])
    elif target == "trigger.source" and teacher is not None and "trigger.source" in t_state:
        options = list(t_state["trigger.source"].options or [])
        first = next((o for o in options if re.fullmatch(r"\D+1", o)), None)
        if first is not None:
            prefix = first[:-1]
            put(ch, ("channels",), "keyword", prefix + "{n}")
            if not t_state["trigger.source"].option_labels:
                put(ch, ("channels",), "label", None)
            others = [o for o in options if not re.fullmatch(re.escape(prefix) + r"\d+", o)]
            line = next((o for o in others if "LINE" in o.upper()), None)
            put(tr, ("trigger",), "external", [{"keyword": o} for o in others if o != line])
            put(tr, ("trigger",), "line", line)
    return out


def _refine(x: dict[str, Any], bound: dict[str, list[tuple[list[str], int]]]) -> list[Any]:
    """Data-sheet values the bound commands' arguments show."""
    from .draft import found, number

    out: list[Any] = []
    tb, tr = x.setdefault("timebase", {}), x.setdefault("trigger", {})
    for args, line in bound.get("waveform.points", []):
        v = number(args[0]) if args else None
        points = tb.setdefault("points", [100, 250, 500, 1000, 2000, 5000, 10000])
        if v and v > 1 and int(v) not in points:
            points.append(int(v))
            points.sort()
            out.append(found(("timebase", "points"), int(v), line, "脚本读取的点数"))
    for args, line in bound.get("memory.size", []):
        v = number(args[0]) if args else None
        mem = tb.get("memory_sizes")
        if v and mem and v > mem["max"]:
            mem["max"] = int(v)
            out.append(found(("timebase", "memory_sizes", "max"), mem["max"], line))
    modes = tr.setdefault(
        "modes", [{"keyword": "AUTO", "meaning": "auto"}, {"keyword": "NORMal", "meaning": "normal"}]
    )
    for args, line in bound.get("trigger.mode", []):
        for a in args[:1]:
            if (
                a
                and not any(m["keyword"].upper() == a.upper() for m in modes)
                and re.fullmatch(r"[A-Za-z]\w*", a)
            ):
                meaning = next(
                    (
                        v
                        for p, v in (("SING", "single"), ("STOP", "stop"), ("AUTO", "auto"))
                        if a.upper().startswith(p)
                    ),
                    "normal",
                )
                modes.append({"keyword": a, "meaning": meaning})
                out.append(found(("trigger", "modes"), a, line, f"脚本写的触发方式，读作 {meaning}，待确认"))
    return out


def _resemble(x: dict[str, Any], model: Any) -> list[Any]:
    """The converter of the scope the script talks most like: better placeholders than generic
    ones (a script's timing assumes its rate), still a data sheet's to confirm."""
    out: list[Any] = []
    adc = _component(model, "scope_adc")
    group = x.setdefault("adc", {})
    for key, param in (("bits", "bits"), ("max_rate", "max_rate"), ("divisions", "divs")):
        value = adc.get(param)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and group.get(key) != value:
            group[key] = value
            out.append(_learned(("adc", key), value, model))
    return out


DRAFTING = Drafting(
    base=_draft_base, basics=_basics, mnemonics=MNEMONICS, enable=_enable, refine=_refine, resemble=_resemble
)


TEMPLATE = register(
    Template(
        id="oscilloscope",
        version=2,
        category="oscilloscope",
        title="示波器",
        title_en="Oscilloscope",
        description="通道、档位表、ADC、时基、触发、采集方式与运行控制、自动测量、波形传输，以及这个型号自己的命令表"
        "（照手册逐条写，只带公共命令起步，D81）。技术指标只作记录，个体偏差的范围在档位表与时基中单独填写（D3）",
        inputs=OscilloscopeInputs,
        example=example,
        build=build,
        check=check,
        drafting=DRAFTING,
    )
)
