"""The oscilloscope template (instrument-authoring.md 7.4, section 8): a data sheet's channels, scale
table, ADC, timebase, trigger, acquisition, measurements and waveform transfer become the scope
modules of section 8.4 and a generic SCPI oscilloscope command set.

Specifications are a record (``specs``); the ranges a unit deviates in are filled in separately
(``deviations``, per V/div in the scale table), never derived from the specifications (D3).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import InputProblem, Template, register

MEASUREMENTS = ["VPP", "VMAX", "VMIN", "VAMPlitude", "VRMS", "VAVerage", "FREQuency", "PERiod"]


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


class IdentityIn(_In):
    manufacturer: str = _f(..., "厂商", "Manufacturer", "As *IDN? names it")
    model: str = _f(..., "型号", "Model", "As *IDN? names it")
    serial: str = _f("SB000001", "序列号", "Serial number", "The unit's serial number in *IDN?")
    firmware: str = _f("1.0.0", "固件版本", "Firmware", "The firmware version in *IDN?")
    idn: str | None = _f(
        None, "*IDN? 格式", "*IDN? format",
        "Fields {manufacturer}, {model}, {serial}, {firmware}; default: the four, comma-separated",
    )  # fmt: skip
    description: str | None = _f(None, "说明", "Description", "One line about the instrument")


class ImpedanceIn(_In):
    name: str = _f(..., "关键字", "Keyword", "The command's keyword for this input, e.g. ONEMeg, FIFTy")
    ohms: float = _f(..., "输入电阻", "Input resistance", "Resistance to ground", "Ω", gt=0)
    capacitance_f: float = _f(0.0, "输入电容", "Input capacitance", "Capacitance to ground", "F", ge=0)
    label: str | None = _f(None, "显示名", "Label", "How the panel shows it, e.g. '1 MΩ'")


class ChannelsIn(_In):
    count: int = _f(4, "通道数", "Channels", "Analog input channels", ge=1, le=8)
    impedances: list[ImpedanceIn] = _f(
        ..., "输入阻抗", "Input impedances", "The selectable inputs, the power-on one first", min_length=1
    )
    bandwidth_hz: float = _f(..., "带宽", "Bandwidth", "-3 dB analog bandwidth", "Hz", gt=0)
    response: Literal["first_order"] = _f(
        "first_order", "响应形状", "Response shape",
        "Frequency response of the front end (only a first-order low-pass is modeled so far)",
    )  # fmt: skip
    bandwidth_limit_hz: float | None = _f(
        None,
        "带宽限制",
        "Bandwidth limit",
        "The limit filter's -3 dB point; omit: no limit command",
        "Hz",
        gt=0,
    )
    couplings: list[Literal["DC", "AC", "GND"]] = _f(
        ["DC", "AC"], "耦合", "Couplings", "Selectable couplings, the power-on one first", min_length=1
    )
    ac_corner_hz: float = _f(
        10.0, "交流耦合转折频率", "AC coupling corner", "-3 dB of the AC high-pass", "Hz", gt=0
    )
    displayed: list[int] = _f(
        [1], "上电显示的通道", "Displayed at power-on", "Channel numbers shown at power-on"
    )


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
        "Points :WAVeform:POINts accepts, increasing", min_length=1,
    )  # fmt: skip
    default_points: int = _f(1000, "上电记录长度", "Power-on record length", "Points at power-on", gt=0)
    memory_depth: int | None = _f(
        None, "存储深度", "Memory depth",
        "Omit: the record is the points setting; given: sample rate = min(max rate, depth / 10 div) on a "
        "1-2-5 ladder",
        gt=0,
    )  # fmt: skip
    reference_divs: float = _f(
        5.0, "参考位置", "Reference position", "Divisions from the left the trigger sits at"
    )
    ladder: bool = _f(True, "1-2-5 时基", "1-2-5 timebase", "Timebase writes snap to 1-2-5; off: any value")
    memory_sizes: MemoryIn | None = _f(
        None, "存储深度设定", "Memory size setting",
        "lecroy-scope: the record is exactly this many points over ten divisions (MSIZ)",
    )  # fmt: skip
    clock_ppm: float | None = _f(
        None, "时基误差限", "Timebase error limit", "± ppm a unit's sample clock may deviate by (individual deviation)",
        "ppm", ge=0,
    )  # fmt: skip


class TriggerIn(_In):
    sweeps: list[str] = _f(
        ["AUTO", "NORMal"], "触发方式", "Sweep modes",
        "Keywords of :TRIGger:SWEep; AUTO and NORMal are understood, SINGle arms one acquisition", min_length=1,
    )  # fmt: skip
    slopes: list[str] = _f(
        ["POSitive", "NEGative", "EITHer"], "触发沿", "Slopes", "Keywords of :TRIGger:SLOPe, the power-on one first",
        min_length=1,
    )  # fmt: skip
    external: bool = _f(False, "外触发", "External trigger", "An EXT input and the EXTernal source")
    line: bool = _f(False, "电源触发", "Line trigger", "The LINE source")
    hysteresis_divs: float = _f(0.16, "迟滞", "Hysteresis", "Half the band an edge must cross", "div", ge=0)
    level_range_divs: float | None = _f(
        None,
        "电平范围",
        "Level range",
        "± divisions of the source's screen the level may be set to",
        "div",
        gt=0,
    )
    jitter_s: float = _f(0.0, "触发抖动", "Trigger jitter", "rms jitter of the trigger instant", "s", ge=0)
    auto_windows: int = _f(
        4,
        "自动触发等待",
        "Auto timeout",
        "Records an auto mode looks ahead for an edge before it free-runs",
        ge=1,
    )


class AcquisitionIn(_In):
    types: list[Literal["NORMal", "AVERage", "PEAK"]] = _f(
        ["NORMal"], "采集方式", "Acquisition types", ":ACQuire:TYPE keywords; NORMal alone: no command",
        min_length=1,
    )  # fmt: skip
    average_counts: list[int] = _f(
        [2, 4, 8, 16, 32, 64, 128, 256], "平均次数", "Average counts", ":ACQuire:COUNt values (AVERage)"
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
        "wait", "无触发时", "Without a trigger", "wait: *OPC? and :DIGitize block; return: they answer"
    )


class TransferIn(_In):
    formats: list[Literal["BYTE", "WORD", "ASCii"]] = _f(
        ["WORD", "BYTE", "ASCii"], "波形格式", "Waveform formats", "The power-on format first", min_length=1
    )
    byte_codes_per_div: float | None = _f(
        None, "BYTE 每格码值", "BYTE codes per division", "Default: the ADC's codes per division", gt=0
    )
    word_codes_per_div: float | None = _f(
        None, "WORD 每格码值", "WORD codes per division", "Default: 256 × the BYTE codes", gt=0
    )
    byteorder: Literal["LSBFirst", "MSBFirst"] = _f("LSBFirst", "字节序", "Byte order", "At power-on")
    unsigned: bool = _f(False, "无符号", "Unsigned", "Codes unsigned at power-on")
    max_points: int | None = _f(
        None,
        "每次读取上限",
        "Points per read",
        "lecroy-scope: most points one INSPECT? returns; omit: no limit",
        gt=0,
    )


class DialectIn(_In):
    command_set: Literal["scpi-scope", "lecroy-scope"] = _f(
        "scpi-scope", "命令集", "Command set",
        "scpi-scope: :CHANnel<n>, :TIMebase, :TRIGger, :DIGitize, :WAVeform, :MEASure; lecroy-scope: C<n>:VDIV, "
        "TDIV, MSIZ, TRSE, TRMD, PACU / C<n>:PAVA?, WAVEFORM_SETUP and C<n>:INSPECT? text readback, echoed headers",
    )  # fmt: skip
    unknown_commands: Literal["error", "ignore"] = _f(
        "error",
        "未知命令",
        "Unknown commands",
        "error: -113; ignore: swallowed and logged (a contract bench)",
    )
    points_mode: bool = _f(
        False, ":WAVeform:POINts:MODE", ":WAVeform:POINts:MODE",
        "scpi-scope: NORMal (at most 1000 points), MAXimum and RAW points modes",
    )  # fmt: skip
    single: Literal["digitize", "arm", "none"] = _f(
        "digitize", ":SINGle", ":SINGle",
        "digitize: acquire once and stop; arm: arm one acquisition on the next trigger; none: no :SINGle",
    )  # fmt: skip
    run_stop: bool = _f(False, ":RUN / :STOP", ":RUN / :STOP", "Run and stop commands")


class OscilloscopeInputs(_In):
    identity: IdentityIn = _f(..., "身份", "Identity", "Manufacturer, model, *IDN?")
    channels: ChannelsIn = _f(..., "通道", "Channels", "Inputs and their front ends")
    vertical: VerticalIn = _f(..., "垂直", "Vertical", "Scale table, offset, probe")
    adc: AdcIn = _f(..., "ADC", "ADC", "Converters and their sharing")
    timebase: TimebaseIn = _f(..., "时基", "Timebase", "Timebase and record")
    trigger: TriggerIn = _group(TriggerIn, "触发", "Trigger", "Edge trigger")
    acquisition: AcquisitionIn = _group(
        AcquisitionIn, "采集", "Acquisition", "Acquisition types and synchronisation"
    )
    measurements: list[str] = _f(MEASUREMENTS, "自动测量", "Measurements", f"From {', '.join(MEASUREMENTS)}")
    transfer: TransferIn = _group(TransferIn, "波形传输", "Waveform transfer", ":WAVeform encoding")
    dialect: DialectIn = _group(DialectIn, "命令方言", "Dialect", "Command set and variants")
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

    names = [z.name for z in ch.impedances]
    for i, name in enumerate(names):
        if names.index(name) != i:
            bad("E-TPL-DUPLICATE", ("channels", "impedances", i, "name"), f"输入阻抗关键字 {name} 重复")
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
    for i in range(1, len(tb.points)):
        if tb.points[i] <= tb.points[i - 1]:
            bad("E-TPL-ORDER", ("timebase", "points", i), "记录长度应递增")
    if tb.default_points not in tb.points:
        bad(
            "E-TPL-DEFAULT",
            ("timebase", "default_points"),
            f"上电记录长度 {tb.default_points} 不在记录长度列表中",
        )

    for i, m in enumerate(x.measurements):
        if m not in MEASUREMENTS:
            bad(
                "E-TPL-UNKNOWN", ("measurements", i), f"没有测量 {m}",
                f"可用的测量：{', '.join(MEASUREMENTS)}",
            )  # fmt: skip
    if x.dialect.command_set == "lecroy-scope":
        if tb.memory_sizes is None:
            bad(
                "E-TPL-MISSING",
                ("timebase", "memory_sizes"),
                "lecroy-scope 的记录长度由 MSIZ 设定，要给存储深度设定的范围",
            )
        else:
            m = tb.memory_sizes
            if not m.min <= m.default <= m.max:
                bad(
                    "E-TPL-DEFAULT",
                    ("timebase", "memory_sizes", "default"),
                    f"上电存储深度 {m.default} 不在 [{m.min}, {m.max}] 内",
                )
        for i, z in enumerate(ch.impedances):
            if not (z.ohms >= 1e5 or abs(z.ohms - 50.0) < 1e-6):
                bad(
                    "E-TPL-UNSUPPORTED", ("channels", "impedances", i, "ohms"),
                    "lecroy-scope 的耦合关键字（D1M、A1M、D50）只表示 1 MΩ 或 50 Ω 的输入",
                )  # fmt: skip
    if "AVERage" in x.acquisition.types and not x.acquisition.average_counts:
        bad("E-TPL-MISSING", ("acquisition", "average_counts"), "有平均采集方式，还要给平均次数")
    return out


# ---- the definition ------------------------------------------------------------------------------


def _ch_options(n: int) -> tuple[list[str], dict[str, str]]:
    names = [f"CHANnel{i}" for i in range(1, n + 1)]
    return names, {name: f"CH{i}" for i, name in enumerate(names, 1)}


def _setting(**kw: Any) -> dict[str, Any]:
    return {k: v for k, v in kw.items() if v is not None}


def _front(x: OscilloscopeInputs, coupling: Any, bw_limit: bool = True) -> dict[str, Any]:
    ch, v = x.channels, x.vertical
    rows = v.table
    front: dict[str, Any] = {"coupling": coupling, "bandwidth": ch.bandwidth_hz, "probe_ratio": "$ch.probe"}
    if ch.bandwidth_limit_hz and bw_limit:
        front |= {"bw_limit": "$ch.bwlimit", "bw_limit_hz": ch.bandwidth_limit_hz}
    if ch.ac_corner_hz != 10.0:
        front["ac_corner"] = ch.ac_corner_hz
    if rows:
        front["scale_range"] = list(v.scale_range)
    if rows and rows[0].offset_v is not None:
        front["offset_ranges"] = [[r.v_div, r.offset_v] for r in rows]
    if rows and rows[0].noise_v_rms is not None:
        front["noise_table"] = [[r.v_div, r.noise_v_rms] for r in rows]
    if v.out_of_range != "error":
        front["out_of_range"] = v.out_of_range
    return front


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
    if tr.jitter_s:
        out["trigger_jitter_s"] = tr.jitter_s
    return out


def build(x: OscilloscopeInputs) -> dict[str, Any]:
    if x.dialect.command_set == "lecroy-scope":
        return _build_lecroy(x)
    ch, v, adc, tb, tr, aq, xf, di = (
        x.channels, x.vertical, x.adc, x.timebase, x.trigger, x.acquisition, x.transfer, x.dialect,
    )  # fmt: skip
    names, labels = _ch_options(ch.count)
    trig_sources, trig_labels = list(names), dict(labels)
    if tr.external:
        trig_sources.append("EXTernal")
        trig_labels["EXTernal"] = "EXT"
    if tr.line:
        trig_sources.append("LINE")
    rows = v.table
    offsets = [[r.v_div, r.offset_v] for r in rows] if rows and rows[0].offset_v is not None else None
    plo, phi = v.probe_range
    slo, shi = v.scale_range
    offset_max = max(r[1] for r in offsets) * phi if offsets else v.offset_max_v
    has_types = len(aq.types) > 1 or aq.types != ["NORMal"]

    state: dict[str, Any] = {
        "timebase.scale": _setting(
            type="float", unit="S", min=tb.scale_range[0], max=tb.scale_range[1], default=tb.default_scale,
            ladder="1-2-5" if tb.ladder else None, label="时基", label_en="Timebase",
        ),
        "trigger.source": _setting(
            type="enum", options=trig_sources, option_labels=trig_labels, default=names[0],
            label="触发源", label_en="Trigger source",
        ),
        "trigger.level": _setting(
            type="float", unit="V", min=-1000.0, max=1000.0, default=0.0, label="触发电平", label_en="Trigger level"
        ),
        "trigger.slope": _setting(
            type="enum", options=list(tr.slopes), default=tr.slopes[0], label="触发沿", label_en="Trigger slope"
        ),
        "trigger.mode": _setting(
            type="enum", options=list(tr.sweeps), default=tr.sweeps[0], label="触发方式", label_en="Trigger mode"
        ),
        "waveform.source": _setting(
            type="enum", options=names, option_labels=labels, default=names[0],
            label="波形传输源", label_en="Waveform source",
        ),
        "waveform.format": _setting(
            type="enum", options=list(xf.formats), default=xf.formats[0], label="波形格式", label_en="Waveform format"
        ),
        "waveform.byteorder": _setting(
            type="enum", options=["LSBFirst", "MSBFirst"], default=xf.byteorder, label="字节序", label_en="Byte order"
        ),
        "waveform.unsigned": _setting(type="bool", default=xf.unsigned, label="无符号", label_en="Unsigned"),
        "waveform.points": _setting(
            type="int", min=tb.points[0], max=tb.points[-1], default=tb.default_points, ladder=list(tb.points),
            label="记录长度", label_en="Record length",
        ),
    }  # fmt: skip
    if di.points_mode:
        state["waveform.points_mode"] = _setting(
            type="enum", options=["NORMal", "MAXimum", "RAW"], default="NORMal",
            label="点数模式", label_en="Points mode",
        )  # fmt: skip
    if has_types:
        state["acquire.type"] = _setting(
            type="enum",
            options=list(aq.types),
            default=aq.types[0],
            label="采集方式",
            label_en="Acquisition type",
        )
        if "AVERage" in aq.types:
            state["acquire.count"] = _setting(
                type="int", min=min(aq.average_counts), max=max(aq.average_counts), default=aq.average_counts[0],
                ladder=sorted(aq.average_counts), label="平均次数", label_en="Average count",
            )  # fmt: skip

    channel_state: dict[str, Any] = {
        "scale": _setting(
            type="float", unit="V", min=slo * plo, max=shi * phi, default=v.default_scale,
            ladder="1-2-5" if v.ladder else None, label="垂直档位", label_en="Scale",
        ),
        "offset": _setting(
            type="float", unit="V", min=-offset_max, max=offset_max, default=0.0, label="偏置", label_en="Offset"
        ),
        "coupling": _setting(
            type="enum", options=list(ch.couplings), default=ch.couplings[0], label="耦合", label_en="Coupling"
        ),
        "impedance": _setting(
            type="enum", options=[z.name for z in ch.impedances],
            option_labels={z.name: z.label for z in ch.impedances if z.label} or None,
            default=ch.impedances[0].name, electrical=True, label="输入阻抗", label_en="Input impedance",
        ),
        "probe": _setting(
            type="float", display_unit="×", min=plo, max=phi, default=v.default_probe,
            label="探头衰减设定", label_en="Probe ratio",
        ),
    }  # fmt: skip
    if ch.bandwidth_limit_hz:
        channel_state["bwlimit"] = _setting(
            type="bool", default=False, label="带宽限制", label_en="Bandwidth limit"
        )
    channel_state["display"] = _setting(type="bool", default=False, label="显示", label_en="Display")

    acq: dict[str, Any] = {
        "horizontal": "timebase", "trigger": "trigger", "adc": "adc", "vertical": "front", "probe": "probe",
    }  # fmt: skip
    sweeps = {s.upper() for s in tr.sweeps}
    if "SINGLE" in sweeps:
        acq["single"] = [s for s in tr.sweeps if s.upper() == "SINGLE"]
    acq |= _sync(x)
    if has_types:
        kinds = {"AVERage": "average", "PEAK": "peak"}
        acq["processing"] = {
            "from": "$acquire.type", "map": {t: kinds[t] for t in aq.types if t in kinds}, "default": "normal"
        }  # fmt: skip
        if "AVERage" in aq.types:
            acq["averages"] = "$acquire.count"

    timebase: dict[str, Any] = {}
    if tb.memory_depth:
        timebase |= {"record_length": "rate_ladder", "points": tb.memory_depth}
    if tb.reference_divs != 5.0:
        timebase["reference_divs"] = tb.reference_divs
    trigger: dict[str, Any] = {}
    if tr.auto_windows != 4:
        trigger["search_windows"] = tr.auto_windows
    if tr.hysteresis_divs != 0.16:
        trigger["hysteresis_divs"] = tr.hysteresis_divs
    if tr.level_range_divs:
        trigger["level_range_divs"] = tr.level_range_divs
    adc_p = _adc(x)

    per_div = adc.codes_per_div or ((1 << adc.bits) / adc.divisions if adc.bits else 32.0)
    byte_cpd = xf.byte_codes_per_div or per_div
    word_cpd = xf.word_codes_per_div or byte_cpd * 256
    fmap = {"BYTE": "block", "WORD": "block", "ASCii": "ascii"}
    xfer: dict[str, Any] = {
        "source": "$waveform.source",
        "encoding": {"from": "$waveform.format", "map": {f: fmap[f] for f in xf.formats}},
        "values": {"from": "$waveform.format", "map": {"ASCii": "volts"}, "default": "codes"},
        "codes_per_div": {"from": "$waveform.format", "map": {"BYTE": byte_cpd, "WORD": word_cpd}, "default": byte_cpd},
        "adc_bits": {"from": "$waveform.format", "map": {"BYTE": 8, "WORD": 16}, "default": 8},
        "width": {"from": "$waveform.format", "map": {"BYTE": 8, "WORD": 16}, "default": 8},
        "unsigned": "$waveform.unsigned",
        "byteorder": "$waveform.byteorder",
        "no_record": "no acquisition for this source; send :DIGitize first",
    }  # fmt: skip
    if adc.offset_sign == "subtract":
        xfer["offset"] = "subtract"
    # A memory deeper than the points read: the points are spread over the record and the preamble
    # describes what is sent (format, type, points, count, x and y increments, origins, references).
    declared = tb.memory_depth is not None
    if declared:
        kind = (
            {"setting": "acquire.type", "map": {"NORMal": 0, "PEAK": 1, "AVERage": 2}}
            if has_types
            else {"value": 0}
        )
        count = (
            {"setting": "acquire.count", "when": {"acquire.type": "AVERage"}, "else": 1}
            if "AVERage" in aq.types
            else {"value": 1}
        )
        if di.points_mode:  # NORMal: the screen's 1000 points at most
            xfer |= {
                "max_points": {"from": "$waveform.points_mode", "map": {"NORMal": 1000}, "default": 0},
                "over_limit": "truncate",
            }
        xfer |= {
            "count": "$waveform.points",
            "count_mode": "spread",
            "header": {
                "encoding": "text",
                "separator": ",",
                "fields": [
                    {"setting": "waveform.format", "map": {"BYTE": 0, "WORD": 1, "ASCii": 4}},
                    kind,
                    {"figure": "points"},
                    count,
                    {"figure": "x_increment", "format": "%.9E"},
                    {"figure": "x_start", "format": "%.9E"},
                    {"value": 0},
                    {"figure": "y_increment", "format": "%.6E"},
                    {"figure": "y_origin", "format": "%.6E"},
                    {"figure": "y_reference"},
                ],
            },
        }

    zmap = {z.name: z.ohms for z in ch.impedances}
    cmap = {z.name: z.capacitance_f for z in ch.impedances}
    caps = set(cmap.values())
    front = _front(x, "$ch.coupling")
    probe: dict[str, Any] = {}
    if v.probe_rescales:
        probe["rescales"] = ["$ch.scale", "$ch.offset"]

    deviations = _deviations(x)

    commands: list[dict[str, Any]] = [
        {"header": ":CHANnel<ch>:SCALe", "state": "ch.scale"},
        {"header": ":CHANnel<ch>:OFFSet", "state": "ch.offset"},
        {"header": ":CHANnel<ch>:COUPling", "state": "ch.coupling"},
        {"header": ":CHANnel<ch>:IMPedance", "state": "ch.impedance"},
        {"header": ":CHANnel<ch>:PROBe", "state": "ch.probe"},
    ]
    if ch.bandwidth_limit_hz:
        commands.append({"header": ":CHANnel<ch>:BWLimit", "state": "ch.bwlimit"})
    commands += [
        {"header": ":CHANnel<ch>:DISPlay", "state": "ch.display"},
        {"header": ":TIMebase[:MAIN]:SCALe", "state": "timebase.scale"},
        {"header": ":TRIGger[:EDGE]:SOURce", "state": "trigger.source"},
        {"header": ":TRIGger[:EDGE]:LEVel", "state": "trigger.level"},
        {"header": ":TRIGger[:EDGE]:SLOPe", "state": "trigger.slope"},
        {"header": ":TRIGger:SWEep", "state": "trigger.mode"},
        {"header": ":DIGitize", "op": "acq.digitize", "query": False, "params": [{"name": "sources", "type": "raw"}]},
    ]  # fmt: skip
    if di.single == "digitize":
        commands.append({"header": ":SINGle", "op": "acq.digitize", "query": False})
    elif di.single == "arm":
        if "SINGLE" not in sweeps:
            acq["single"] = ["SINGle"]
        commands.append({"header": ":SINGle", "op": "acq.single", "query": False})
    if di.run_stop:
        commands += [
            {"header": ":RUN", "op": "acq.run", "query": False},
            {"header": ":STOP", "op": "acq.stop", "query": False},
        ]
    if has_types:
        commands.append({"header": ":ACQuire:TYPE", "state": "acquire.type"})
        if "AVERage" in aq.types:
            commands.append({"header": ":ACQuire:COUNt", "state": "acquire.count"})
    commands += [
        {"header": ":WAVeform:SOURce", "state": "waveform.source"},
        {"header": ":WAVeform:FORMat", "state": "waveform.format"},
        {"header": ":WAVeform:BYTeorder", "state": "waveform.byteorder"},
        {"header": ":WAVeform:UNSigned", "state": "waveform.unsigned"},
        {"header": ":WAVeform:POINts", "state": "waveform.points"},
        *([{"header": ":WAVeform:POINts:MODE", "state": "waveform.points_mode"}] if di.points_mode else []),
        {"header": ":WAVeform:PREamble", "op": "xfer.header" if declared else "acq.preamble", "set": False},
        {"header": ":WAVeform:DATA", "op": "xfer.data", "set": False},
    ]
    for m in x.measurements:
        commands.append({
            "header": f":MEASure:{m}", "op": "meas.measure", "set": False, "args": {"what": m},
            "params": [{"name": "source", "type": "string", "optional": True}],
        })  # fmt: skip

    channels: dict[str, Any] = {"count": ch.count}
    shown = {n: {"display": {"default": True}} for n in ch.displayed}
    if shown:
        channels["overrides"] = shown
    identity: dict[str, Any] = {"serial": x.identity.serial, "firmware": x.identity.firmware}
    if x.identity.idn:
        identity["idn"] = x.identity.idn
    channel_summary = ["display", "scale", "offset", "coupling", "impedance", "probe"]
    out: dict[str, Any] = {
        "manufacturer": x.identity.manufacturer,
        "model": x.identity.model,
        "class": "oscilloscope",
        "description": x.identity.description,
        "identity": identity,
        "channels": channels,
        "state": state,
        "channel_state": channel_state,
        "components": [
            {"name": "acq", "type": "scope_acquisition", "params": acq},
            {"name": "timebase", "type": "scope_timebase", "params": timebase},
            {"name": "trigger", "type": "scope_trigger", "params": trigger},
            {"name": "adc", "type": "scope_adc", "params": adc_p},
            {"name": "meas", "type": "scope_measurements", "params": {"source": "$waveform.source"}},
            {"name": "xfer", "type": "waveform_transfer", "params": xfer},
        ],
        "channel_components": [
            {
                "name": "inp",
                "type": "input_port",
                "params": {
                    "z_in": {"from": "$ch.impedance", "map": zmap},
                    "c_in": caps.pop() if len(caps) == 1 else {"from": "$ch.impedance", "map": cmap},
                },
            },
            {"name": "front", "type": "scope_vertical", "params": front},
            {"name": "probe", "type": "scope_probe", "params": probe},
        ],
        "wiring": {
            "channel": [["inp.out", "front.in"], ["front.out", "probe.in"]],
            "instrument": [["ch{n}.probe.out", "acq.in{n}"]],
        },
        "ports": {"channel": {"CH{n}": "inp.pin"}},
        "display": {
            "template": "oscilloscope",
            "summary": ["timebase.scale", "trigger.source", "trigger.level", "trigger.slope"],
            "channel_summary": channel_summary,
        },
        "capabilities": ["oscilloscope"],
        "commands": commands,
    }
    if tr.external:
        out["components"].append({"name": "extin", "type": "input_port", "params": {"z_in": 1.0e6}})
        out["wiring"]["instrument"].append(["extin.out", "trigger.ext"])
        out["ports"]["instrument"] = {"EXT": "extin.pin"}
    if di.unknown_commands != "error":
        out["unknown_commands"] = di.unknown_commands
    if x.specs:
        out["specs"] = x.specs
    if deviations:
        out["deviations"] = deviations
    if not out["description"]:
        del out["description"]
    return out


#: LeCroy's measurement names (PACU, C<n>:PAVA?) for the engine's measurements.
LECROY_MEASUREMENTS = {
    "VAMPlitude": "AMPL", "VPP": "PKPK", "VMAX": "MAX", "VMIN": "MIN", "VAVerage": "MEAN", "VRMS": "RMS",
    "FREQuency": "FREQ", "PERiod": "PER",
}  # fmt: skip
UNITS = {"FREQuency": "HZ", "PERiod": "S"}


def _lecroy_couplings(
    x: OscilloscopeInputs,
) -> tuple[list[str], dict[str, str], dict[str, float], dict[str, str]]:
    """LeCroy's coupling keywords say coupling and input together: D1M, A1M, D50, GND."""
    ch = x.channels
    options: list[str] = []
    labels: dict[str, str] = {}
    ohms: dict[str, float] = {}
    kind: dict[str, str] = {}
    for z in ch.impedances:
        high = z.ohms >= 1e5
        for c in ch.couplings:
            if c == "GND" or (c == "AC" and not high):
                continue
            key = f"{c[0]}{'1M' if high else '50'}"
            if key not in options:
                options.append(key)
                labels[key] = f"{c} {'1 MΩ' if high else '50 Ω'}"
                ohms[key] = z.ohms
                kind[key] = c
    if "GND" in ch.couplings:
        options.append("GND")
        labels["GND"] = "GND"
        ohms["GND"] = ch.impedances[0].ohms
        kind["GND"] = "GND"
    return options, labels, ohms, kind


def _build_lecroy(x: OscilloscopeInputs) -> dict[str, Any]:
    """A LeCroy-style scope: short and long headers echoed in replies (COMM_HEADER), engineering
    numbers, per-channel trigger level and slope, measurement slots, text waveform readback."""
    ch, v, tb, tr, xf, di = x.channels, x.vertical, x.timebase, x.trigger, x.transfer, x.dialect
    n = ch.count
    mem = tb.memory_sizes
    assert mem is not None  # check() requires it
    chans = [f"C{i}" for i in range(1, n + 1)]
    sources = list(chans) + (["EX", "EX10"] if tr.external else []) + (["LINE"] if tr.line else [])
    couplings, coupling_labels, ohms, kind = _lecroy_couplings(x)
    plo, phi = v.probe_range
    slo, shi = v.scale_range
    offsets = [r.offset_v for r in v.table if r.offset_v is not None]
    offset_max = max(offsets) * phi if offsets else v.offset_max_v
    meas_names = [LECROY_MEASUREMENTS[m] for m in x.measurements]
    slot = {"type": "enum", "options": ["NULL", *meas_names], "default": "NULL"}

    state: dict[str, Any] = {
        "timebase.scale": _setting(
            type="float", unit="S", min=tb.scale_range[0], max=tb.scale_range[1], default=tb.default_scale,
            ladder="1-2-5" if tb.ladder else None, label="时基", label_en="Timebase",
        ),
        "memory.size": _setting(
            type="int", min=mem.min, max=mem.max, default=mem.default, label="存储深度", label_en="Memory size"
        ),
        "trigger.mode": _setting(
            type="enum", options=["AUTO", "NORMal", "SINGLE", "STOP"], default="AUTO",
            label="触发方式", label_en="Trigger mode",
        ),
        "trigger.type": {"type": "enum", "options": ["EDGE"], "default": "EDGE"},
        "trigger.source": _setting(
            type="enum", options=sources, default="C1", label="触发源", label_en="Trigger source"
        ),
        "trigger.hold": {"type": "enum", "options": ["OFF", "TI", "PS", "PL", "IS", "IL", "P2", "I2"], "default": "OFF"},
        "interleaved": _setting(type="bool", default=False, label="随机交错采样", label_en="Random interleaved sampling"),
        "refclock": _setting(
            type="enum", options=["INTERNAL", "EXTERNAL"], default="INTERNAL", label="参考时钟", label_en="Reference clock"
        ),
        "comm.header": {"type": "enum", "options": ["OFF", "SHORT", "LONG"], "default": "SHORT"},
        "waveform.sp": {"type": "int", "min": 0, "max": mem.max, "default": 0},
        "waveform.np": {"type": "int", "min": 0, "max": mem.max, "default": 0},
        "waveform.fp": {"type": "int", "min": 0, "max": mem.max, "default": 0},
        "waveform.sn": {"type": "int", "min": 0, "max": 10000, "default": 0},
    }  # fmt: skip
    for i in range(1, 9):
        state[f"measure.p{i}.kind"] = dict(slot)
        state[f"measure.p{i}.source"] = {"type": "enum", "options": list(chans), "default": ""}
    channel_state: dict[str, Any] = {
        "display": _setting(type="bool", default=False, label="显示", label_en="Trace"),
        "coupling": _setting(
            type="enum", options=couplings, default=couplings[0], electrical=True, label="耦合", label_en="Coupling",
            option_labels=coupling_labels,
        ),
        "scale": _setting(
            type="float", unit="V", min=slo * plo, max=shi * phi, default=v.default_scale,
            ladder="1-2-5" if v.ladder else None, label="垂直档位", label_en="Scale",
        ),
        "offset": _setting(
            type="float", unit="V", min=-offset_max, max=offset_max, default=0.0, label="偏移", label_en="Offset"
        ),
        "trigger_level": _setting(
            type="float", unit="V", min=-offset_max, max=offset_max, default=0.0,
            label="触发电平", label_en="Trigger level",
        ),
        "trigger_slope": _setting(
            type="enum", options=list(tr.slopes), default=tr.slopes[0], label="触发沿", label_en="Trigger slope"
        ),
        "probe": _setting(type="float", min=plo, max=phi, default=v.default_probe, label="衰减", label_en="Attenuation"),
    }  # fmt: skip

    acq: dict[str, Any] = {
        "horizontal": "timebase", "trigger": "trigger", "adc": "adc", "vertical": "front", "probe": "probe",
        # TRMD SINGLE acquires once and becomes STOP; a read from the first point takes a new acquisition
        # in AUTO and NORM, the following pieces read the same record; a channel without a record is
        # acquired when read.
        "single": ["SINGLE"], "stop": ["STOP"], "after_single": "STOP",
        "reacquire": "on_read_start", "missing": "acquire", "untriggered": "record",
        **_sync(x),
    }  # fmt: skip
    trigger: dict[str, Any] = {"level": "$ch{n}.trigger_level", "slope": "$ch{n}.trigger_slope"}
    if tr.auto_windows != 4:
        trigger["search_windows"] = tr.auto_windows
    if tr.hysteresis_divs != 0.16:
        trigger["hysteresis_divs"] = tr.hysteresis_divs
    xfer: dict[str, Any] = {
        "first": "$waveform.fp", "count": "$waveform.np", "step": "$waveform.sp",
        **({"max_points": xf.max_points} if xf.max_points else {}),
        "encoding": "text", "values": "volts", "format": "%.6e", "separator": " ",
        "descriptor": {
            "WAVE_ARRAY_COUNT": "{points}", "FIRST_POINT": "{first}", "SPARSING_FACTOR": "{step}",
            "HORIZ_INTERVAL": "{dt:.9e}", "HORIZ_OFFSET": "{x_offset:.9e}", "VERTICAL_OFFSET": "{offset:.6e}",
            "VERTICAL_GAIN": "{gain:.6e}", "TIMEBASE": "{timebase:.6e}", "VOLT_PER_DIV": "{scale:.6e}",
            "ACQ_COUNT": "{count}",
        },
    }  # fmt: skip
    names = {LECROY_MEASUREMENTS[m]: [m, UNITS.get(m, "V")] for m in x.measurements}
    caps = {z.capacitance_f for z in ch.impedances}
    inp: dict[str, Any] = {
        "z_in": {"from": "$ch.coupling", "map": ohms},
        "c_in": caps.pop() if len(caps) == 1 else {"from": "$ch.coupling", "map": {
            k: next(z.capacitance_f for z in ch.impedances if z.ohms == r) for k, r in ohms.items()
        }},
    }  # fmt: skip
    inquire = [
        {"name": "name", "type": "string"},
        {"name": "fmt", "type": "enum", "options": ["FLOAT"], "optional": True},
    ]
    commands: list[dict[str, Any]] = [
        {"header": "*CLS", "op": "sys.clear_status"},
        {"header": "*OPC", "op": "acq.opc"},
        {"header": ":SYSTem:PRESet", "op": "sys.reset", "query": False},
        {"header": "Comm_HeaDeR", "state": "comm.header"},
        {"header": "C<ch>:TRAce", "state": "ch.display"},
        {"header": "C<ch>:CouPLing", "state": "ch.coupling"},
        {"header": "C<ch>:Volt_DIV", "state": "ch.scale", "reply": "{header} {value} V"},
        {"header": "C<ch>:OFfSeT", "state": "ch.offset", "reply": "{header} {value} V"},
        {"header": "C<ch>:ATTeNuation", "state": "ch.probe"},
        {"header": "C<ch>:TRig_LeVel", "state": "ch.trigger_level", "reply": "{header} {value} V"},
        {"header": "C<ch>:TRig_SLope", "state": "ch.trigger_slope"},
        {"header": "Time_DIV", "state": "timebase.scale", "reply": "{header} {value} S"},
        {"header": "Memory_SIZe", "state": "memory.size", "format": {"integer": "engineering"}},
        {"header": "InterLeaVeD", "state": "interleaved"},
        {"header": "Reference_CLocK", "state": "refclock"},
        {
            "header": "TRig_SElect",
            "params": [
                {"name": "type", "type": "enum", "state": "trigger.type"},
                {"name": "rest", "type": "pairs", "optional": True,
                 "keys": {"SR": "trigger.source", "HT": "trigger.hold", "HV": None}},
            ],
        },
        {
            "header": "TRig_MoDe", "op": "acq.run_mode", "reply": "{header} {value}",
            "params": [{"name": "mode", "type": "enum", "options": ["AUTO", "NORMal", "SINGLE", "STOP"], "optional": True}],
        },
        {"header": "ARM_acquisition", "op": "acq.single", "query": False},
        {"header": "STOP", "op": "acq.stop", "query": False},
        {"header": "FoRce_TRigger", "op": "acq.force_trigger", "query": False},
        {"header": "WAIT", "op": "acq.wait", "query": False,
         "params": [{"name": "timeout", "type": "float", "unit": "S", "optional": True}]},
        {
            "header": "PArameter_CUstom",
            "params": [
                {"name": "slot", "type": "int", "index": True, "min": 1, "max": 8},
                {"name": "kind", "type": "enum", "state": "measure.p{slot}.kind"},
                {"name": "source", "type": "enum", "state": "measure.p{slot}.source", "optional": True},
            ],
        },
        {"header": "C<ch>:PArameter_VAlue", "op": "meas.named", "set": False,
         "reply": "{header} {kind},{value} {unit},{status}", "params": [{"name": "kind", "type": "string"}]},
        {"header": "WaveForm_SetUp", "params": [
            {"name": "setup", "type": "pairs", "keys": {"SP": "waveform.sp", "NP": "waveform.np", "FP": "waveform.fp", "SN": "waveform.sn"}}
        ]},
        {"header": "C<ch>:INSPect", "when": {"name": "DATA_ARRAY_1"}, "op": "xfer.data", "set": False,
         "reply": '{header} " {value} "', "params": inquire},
        {"header": "C<ch>:INSPect", "when": {"name": "SIMPLE"}, "op": "xfer.data", "set": False,
         "reply": '{header} " {value} "', "params": inquire},
        {"header": "C<ch>:INSPect", "when": {"name": "WAVEDESC"}, "op": "xfer.describe", "set": False,
         "reply": '{header} " {value} "', "params": [{"name": "name", "type": "string"}]},
    ]  # fmt: skip
    channels: dict[str, Any] = {"count": n}
    shown = {i: {"display": {"default": True}} for i in ch.displayed}
    if shown:
        channels["overrides"] = shown
    identity: dict[str, Any] = {"serial": x.identity.serial, "firmware": x.identity.firmware}
    if x.identity.idn:
        identity["idn"] = x.identity.idn
    out: dict[str, Any] = {
        "manufacturer": x.identity.manufacturer,
        "model": x.identity.model,
        "class": "oscilloscope",
        "identity": identity,
        "dialect": {"echo_header": "$comm.header", "multipliers": {"K": 1.0e3, "M": 1.0e6, "MA": 1.0e6, "G": 1.0e9}},
        "format": {"number": "engineering", "boolean": "word", "enum": "short"},
        "channels": channels,
        "state": state,
        "channel_state": channel_state,
        "components": [
            {"name": "sys", "type": "system"},
            {"name": "acq", "type": "scope_acquisition", "params": acq},
            {"name": "timebase", "type": "scope_timebase", "params": {"record_length": "exact", "points": "$memory.size"}},
            {"name": "trigger", "type": "scope_trigger", "params": trigger},
            {"name": "adc", "type": "scope_adc", "params": _adc(x)},
            {"name": "xfer", "type": "waveform_transfer", "params": xfer},
            {"name": "meas", "type": "scope_measurements", "params": {"record": "acq", "names": names}},
        ],
        "channel_components": [
            {"name": "inp", "type": "input_port", "params": inp},
            {"name": "front", "type": "scope_vertical",
             "params": _front(x, {"from": "$ch.coupling", "map": kind}, bw_limit=False)},
            {"name": "probe", "type": "scope_probe"},
        ],
        "wiring": {
            "channel": [["inp.out", "front.in"], ["front.out", "probe.in"]],
            "instrument": [["ch{n}.probe.out", "acq.in{n}"]],
        },
        "ports": {"channel": {"CH{n}": "inp.pin"}},
        "display": {
            "template": "oscilloscope",
            "summary": ["timebase.scale", "memory.size", "trigger.mode", "trigger.source"],
            "channel_summary": ["display", "scale", "offset", "coupling", "trigger_level"],
            "actions": {"single": "TRMD SINGLE"},
        },
        "capabilities": ["oscilloscope"],
        "commands": commands,
    }  # fmt: skip
    if x.identity.description:
        out["description"] = x.identity.description
    if di.unknown_commands != "error":
        out["unknown_commands"] = di.unknown_commands
    if tr.external:
        out["components"].append({"name": "extin", "type": "input_port", "params": {"z_in": 1.0e6}})
        out["wiring"]["instrument"].append(["extin.out", "trigger.ext"])
        out["ports"]["instrument"] = {"EXT": "extin.pin"}
    if x.specs:
        out["specs"] = x.specs
    deviations = _deviations(x)
    if deviations:
        out["deviations"] = deviations
    return out


def example() -> dict[str, Any]:
    """A 4-channel 200 MHz, 8-bit scope with a data sheet's scale table and deviation ranges."""
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
        "trigger": {"sweeps": ["AUTO", "NORMal", "SINGle"], "external": True, "jitter_s": 1.0e-11},
        "acquisition": {"types": ["NORMal", "AVERage", "PEAK"], "completion": "pending"},
        "transfer": {"formats": ["BYTE", "WORD", "ASCii"], "byteorder": "MSBFirst", "unsigned": True},
        "dialect": {"single": "arm", "run_stop": True},
        "specs": {
            "bandwidth": "200 MHz",
            "dc_gain_accuracy": "±3 % full scale (≥ 10 mV/div); ±4 % (< 10 mV/div)",
        },
    }


# ---- from a script (#145) ---------------------------------------------------------------------------


def _base(variant: str) -> dict[str, Any]:
    """A variant's inputs before the script is read: generic values a data sheet would replace."""
    common: dict[str, Any] = {
        "identity": {"manufacturer": "Unknown", "model": "SCOPE"},
        "channels": {
            "count": 4,
            "impedances": [
                {"name": "ONEMeg", "ohms": 1.0e6, "capacitance_f": 1.5e-11, "label": "1 MΩ"},
                {"name": "FIFTy", "ohms": 50.0, "capacitance_f": 1.5e-11, "label": "50 Ω"},
            ],
            "bandwidth_hz": 2.0e8,
            "bandwidth_limit_hz": 2.0e7,
            "couplings": ["DC", "AC", "GND"],
        },
        "adc": {"bits": 8, "divisions": 10.0, "max_rate": 2.0e9},
    }
    if variant == "lecroy-scope":
        return {
            **common,
            "vertical": {"scale_range": [1.0e-3, 10.0], "default_scale": 0.05, "ladder": False, "offset_max_v": 100.0,
                         "probe_range": [1.0e-4, 1.0e4]},
            "adc": {"bits": 12, "divisions": 10.0, "max_rate": 2.0e10},
            "timebase": {"scale_range": [2.0e-11, 1000.0], "default_scale": 1.0e-3, "ladder": False,
                         "memory_sizes": {"min": 500, "max": 50_000_000, "default": 10_000}},
            "trigger": {"slopes": ["POSitive", "NEGative"], "external": True, "line": True},
            "dialect": {"command_set": "lecroy-scope"},
        }  # fmt: skip
    return {
        **common,
        "vertical": {"scale_range": [1.0e-3, 10.0], "default_scale": 1.0, "probe_range": [0.1, 1000.0]},
        "timebase": {
            "scale_range": [1.0e-9, 50.0],
            "points": [100, 250, 500, 1000, 2000, 5000, 10000, 100000],
        },
        "trigger": {"sweeps": ["AUTO", "NORMal", "SINGle"], "external": True, "line": True},
        "acquisition": {"types": ["NORMal", "AVERage", "PEAK"]},
        "dialect": {"command_set": "scpi-scope", "run_stop": True, "points_mode": True},
    }


def variants() -> dict[str, dict[str, Any]]:
    out = {}
    for name in ("scpi-scope", "lecroy-scope"):
        base = _base(name)
        base["channels"] = {**base["channels"], "count": 8}  # every channel a script may name
        out[name] = base
    return out


def prefill(variant: str, commands: list[Any], hints: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    from .draft import channels_used, default, found, matching, number, units

    x = _base(variant)
    filled = []
    us = units(commands)
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
    for key in ("bandwidth_hz",):
        filled.append(default(("channels", key), x["channels"][key]))
    filled.append(default(("vertical", "scale_range"), x["vertical"]["scale_range"]))
    filled.append(default(("timebase", "scale_range"), x["timebase"]["scale_range"]))
    filled.append(default(("adc", "bits"), x["adc"]["bits"]))
    if variant == "lecroy-scope":
        sizes = [(number(u.args[0]), u.line) for u in matching(us, r"MSIZ|MEMORY_SIZE") if u.args]
        sizes = [(v, ln) for v, ln in sizes if v and v > 1]
        if sizes:
            top, line = max(sizes)
            mem = x["timebase"]["memory_sizes"]
            mem["max"] = max(mem["max"], int(top))
            filled.append(found(("timebase", "memory_sizes", "max"), mem["max"], line))
        filled.append(
            default(("dialect", "unknown_commands"), "error", "契约若要求忽略未建模命令，改为 ignore")
        )
        return x, filled
    pts = [(number(u.args[0]), u.line) for u in matching(us, r"WAV(EFORM)?:POIN(TS)?") if u.args]
    pts = [(int(v), ln) for v, ln in pts if v and v > 1]
    points = x["timebase"]["points"]
    for v, line in pts:
        if v not in points:
            points.append(v)
            filled.append(found(("timebase", "points"), v, line, "脚本读取的点数"))
    points.sort()
    if matching(us, r"WAV(EFORM)?:POIN(TS)?:MODE"):
        x["timebase"]["memory_depth"] = 100_000
        filled.append(
            default(("timebase", "memory_depth"), 100_000, "脚本用了点数模式：存储深度取 100 kpts，待确认")
        )
    fmts = {a.upper()[:3] for u in matching(us, r"WAV(EFORM)?:FORM(AT)?") for a in u.args}
    order = [f for f in ("BYTE", "WORD", "ASCii") if f.upper()[:3] in fmts] or ["WORD", "BYTE", "ASCii"]
    x["transfer"] = {"formats": order + [f for f in ("WORD", "BYTE", "ASCii") if f not in order]}
    meas = sorted(
        {u.header.lstrip(":").split(":")[1].rstrip("?").upper() for u in matching(us, r"MEAS(URE)?:\w+")}
    )
    known = {m.upper()[:4]: m for m in MEASUREMENTS}
    x["measurements"] = sorted({known[m[:4]] for m in meas if m[:4] in known} | set(MEASUREMENTS))
    filled.append(
        default(
            ("acquisition", "completion"), "immediate", "单次采集当场完成；真机在虚拟时间中完成时改为 pending"
        )
    )
    return x, filled


TEMPLATE = register(
    Template(
        id="oscilloscope",
        version=1,
        category="oscilloscope",
        title="示波器",
        title_en="Oscilloscope",
        description="通道、档位表、ADC、时基、边沿触发、采集方式与同步、自动测量、波形传输；通用 SCPI 示波器命令集。"
        "技术指标只作记录，个体偏差的范围在档位表与时基中单独填写（D3）",
        inputs=OscilloscopeInputs,
        example=example,
        build=build,
        check=check,
        variants=variants,
        prefill=prefill,
    )
)
