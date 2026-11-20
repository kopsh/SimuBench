"""The signal source template's drafting half (D81): the mnemonics many sources share and the hooks a
draft from a script fills the signal source template through (split from ``signal_source``, #1728)."""

from __future__ import annotations

import re
from typing import Any

from . import Drafting, Mnemonic
from .oscilloscope_draft import _any

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
