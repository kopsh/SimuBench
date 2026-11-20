"""The oscilloscope template's drafting half (D81): the mnemonics many scopes share and the hooks a
draft from a script fills the oscilloscope template through (split from ``oscilloscope``, #1728)."""

from __future__ import annotations

import re
from typing import Any

from ..components.measurements import MEASUREMENTS
from . import Drafting, Mnemonic


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
    from .oscilloscope import FALLING_WORDS, RISING_WORDS  # the build half's; it imports DRAFTING

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
