"""The overview cards' content (tui-screens.md 4.1–4.3) as pure functions of the API data.

Each card is a list of lines; each line a list of ``(text, style)`` segments. Styles are the
semantic tokens of tui-screens.md 1.2 (``muted``, ``bad``, ``focus``, ``ok``, ``sim``,
``ch1``–``ch4``, ``bold``), resolved to concrete colors only at render time — here the content
is unit-testable without a terminal.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .fmt import braille, fmt
from .i18n import t

#: Segments of one line: (text, style tokens).
Line = list[tuple[str, str]]

_ACC = re.compile(r"^wire(?P<wire>\d+)\.(?P<type>[a-z_]+?)(?P<index>\d+)\.(?P<port>[A-Za-z]+)$")

#: Port direction → the arrow drawn in a card (inout ports source and sink; the mock draws →).
ARROWS = {"in": "←", "out": "→", "inout": "→"}


@dataclass
class CardData:
    """One overview card: its title, numbered key, body lines and whether anything is wrong."""

    name: str
    number: int = 0  # 1–9; 0 from the tenth card on
    title: Line = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # what `?` will explain (T2)
    remote: bool = False

    @property
    def title_text(self) -> str:
        return "".join(text for text, _ in self.title)


def _label(sv: Mapping[str, Any], lang: str) -> str:
    return sv.get("label" if lang == "zh" else "label_en") or sv.get("path", "").rsplit(".", 1)[-1]


def _state_map(info: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {sv["path"]: sv for sv in info.get("state", [])}


def _value(state: Mapping[str, dict], path: str) -> Any:
    sv = state.get(path)
    return None if sv is None else sv.get("value")


def _option(sv: Mapping[str, Any] | None, value: Any) -> str:
    """An enum with its option_labels when the definition gives them (tui-screens.md 1.4)."""
    if sv and isinstance(value, str):
        labels = sv.get("option_labels") or {}
        if value in labels:
            return labels[value]
    return "" if value is None else str(value)


def _read(readings: Mapping[str, Any] | None, key: str) -> Any:
    return (readings or {}).get("values", {}).get(key)


def _levels_by_net(levels: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {n["net"]: n for n in (levels or {}).get("nets", [])}


def _units_of(catalog: Mapping[str, Any], type_: str) -> dict[str, str]:
    doc = (catalog or {}).get(type_) or {}
    return {p["name"]: p.get("unit") for p in doc.get("params", []) if p.get("unit")}


def accessory_label(type_: str, params: Mapping[str, Any], lang: str) -> str:
    """An accessory's display name (tui-screens.md 4.3): ``10x 探头``, ``0.1 Ω 导线``, …"""
    if type_ == "passive_probe":
        return t("cards.accProbe", lang, value=f"{float(params.get('attenuation', 10)):g}")
    if type_ == "lead":
        return t("cards.accLead", lang, value=fmt(params.get("r", 0.0), "OHM"))
    if type_ == "coax_cable":
        return t("cards.accCoax", lang)
    return type_


def _net_of_ref(session: Mapping[str, Any], ref: str) -> str | None:
    inst, _, port = ref.rpartition(".")
    if not port or ref.startswith("wire"):
        return None
    for i in session.get("instruments", []):
        if i["name"] == inst:
            return next((p.get("net") for p in i["ports"] if p["name"] == port), None)
    for p in session.get("parts", []):
        if p["name"] == inst:
            return next((q.get("net") for q in p["ports"] if q["name"] == port), None)
    return None


def _members_of_net(session: Mapping[str, Any], net: str | None) -> list[str]:
    if not net:
        return []
    return next((n["members"] for n in session.get("nets", []) if n["id"] == net), [])


def _accessory_of(session: Mapping[str, Any], wire: int, index: int) -> Mapping[str, Any] | None:
    w = next((w for w in session.get("wires", []) if w.get("index") == wire), None)
    accs = (w or {}).get("accessories", [])
    return accs[index] if index < len(accs) else None


def _is_part(session: Mapping[str, Any], ref: str) -> bool:
    inst, _, port = ref.rpartition(".")
    return (
        bool(port) and not ref.startswith("wire") and any(p["name"] == inst for p in session.get("parts", []))
    )


def _far_end(ref: str, session: Mapping[str, Any], lang: str, seen: set[str]) -> str:
    """The opposite end written per tui-screens.md 4.3: through an accessory to its far end."""
    if ref in seen:
        return ref
    seen.add(ref)
    m = _ACC.match(ref)
    if m is None:
        return ref
    acc = _accessory_of(session, int(m["wire"]), int(m["index"]))
    if acc is None:
        return ref
    other = next((p for p in acc.get("ports", []) if p["name"] != m["port"] and p.get("net")), None)
    name = accessory_label(acc.get("type", ""), acc.get("params", {}), lang)
    if other is None:
        return f"{name}.{m['port']}"
    mates = [r for r in _members_of_net(session, other["net"]) if r != f"{acc['name']}.{other['name']}"]
    return _far_end(mates[0], session, lang, seen) if mates else f"{name}.{other['name']}"


def opposite_ends(ref: str, session: Mapping[str, Any], lang: str) -> list[str]:
    """The other members of the net ``ref`` sits on, per tui-screens.md 4.3 (max two, then +N).

    A wire that passes through an accessory is written to the accessory's far end, with the
    accessory's display name appended: ``rc.OUT · 10x 探头``.
    """
    mates = [r for r in _members_of_net(session, _net_of_ref(session, ref)) if r != ref]
    rank = {"inst": 0, "part": 1, "acc": 2}

    def order(r: str) -> int:
        return rank["acc"] if _ACC.match(r) else (rank["part"] if _is_part(session, r) else rank["inst"])

    ends: list[str] = []
    for m in sorted(mates, key=order):
        text = _far_end(m, session, lang, set())
        acc_mate = _ACC.match(m)
        if acc_mate:
            acc = _accessory_of(session, int(acc_mate["wire"]), int(acc_mate["index"]))
            if acc is not None:
                name = accessory_label(acc["type"], acc.get("params", {}), lang)
                ends.append(f"{text} · {name}")
                continue
        ends.append(text)
    if len(mates) > 2:
        rest = [t("overview.plusN", lang, count=len(mates) - 2)]
        ends = [*ends[:2], *rest]
    return ends


def _wiring_line(info: Mapping[str, Any], session: Mapping[str, Any], lang: str) -> Line:
    """``连线 <端口> <方向> <对端>`` — several ports separated by three spaces (tui-screens.md 4.2)."""
    parts: list[str] = []
    muted_parts: list[bool] = []
    for p in info.get("ports", []):
        ref = f"{info['name']}.{p['name']}"
        ends = opposite_ends(ref, session, lang)
        arrow = ARROWS.get(p.get("direction", ""), "—")
        if ends:
            parts.append(f"{p['name']} {arrow} {' · '.join(ends)}")
            muted_parts.append(False)
        else:
            parts.append(f"{p['name']} —")
            muted_parts.append(True)
    segments: Line = [(t("overview.wiring", lang) + " ", "muted")]
    for i, (text, muted) in enumerate(zip(parts, muted_parts, strict=True)):
        if i:
            segments.append(("   ", "muted"))
        segments.append((text, "muted" if muted else ""))
    return segments


def _error_line(info: Mapping[str, Any], lang: str) -> Line:
    """``错误 <条数>``; greater than 0: ``错误 N · 最近 <最新代码>`` with the code in bad (4.2)."""
    errors = info.get("errors", [])
    if not errors:
        return [(t("overview.errors", lang) + " ", "muted"), ("0", "")]
    last = errors[-1]
    text = t("overview.lastError", lang, count=len(errors), code=last["code"])
    head, _, tail = text.partition(str(last["code"]))
    return [(head, ""), (str(last["code"]), "bad"), (tail, "")]


def _fmt_state(state: Mapping[str, dict], path: str, lang: str) -> str:
    sv = state.get(path) or {}
    value = _value(state, path)
    if sv.get("type") == "bool" or isinstance(value, bool):
        return t("common.on" if value else "common.off", lang)
    if sv.get("type") == "enum":
        return _option(sv, value)
    unit = sv.get("display_unit") or sv.get("unit")
    return fmt(value if isinstance(value, (int, float)) else None, unit)


# ---- the per-class templates (tui-screens.md 4.2) -------------------------------------------------


def _channel_rows(
    info: Mapping[str, Any], state: Mapping[str, dict], level_of: Callable, lang: str
) -> list[Line]:
    """Oscilloscope: one line per open channel, closed ones merged onto one line (4.2)."""
    rows: list[Line] = []
    closed: list[str] = []
    channels = sorted({sv.get("channel") for sv in info.get("state", []) if sv.get("channel")})
    for ch in channels:
        name = f"CH{ch}"
        if not _value(state, f"ch{ch}.display"):
            closed.append(f"{name} " + t("common.off", lang))
            continue
        port = next((p for p in info["ports"] if p.get("channel") == ch), None)
        level = level_of(port.get("net")) if port else None
        wave = braille(level.get("wave", []), 6) if level else "⠀" * 6
        scale = fmt(_value(state, f"ch{ch}.scale"), "V") + "/div"
        coupling = _option(state.get(f"ch{ch}.coupling"), _value(state, f"ch{ch}.coupling"))
        rows.append(
            [
                (f"{name} ", f"ch{ch % 4 + 1}"),
                (t("common.on", lang) + " ", ""),
                (scale + " ", ""),
                (coupling + "  ", ""),
                (wave, f"ch{ch % 4 + 1}"),
            ]
        )
    if closed:
        rows.append([(t("cards.channelsOff", lang, items="  ".join(closed)), "muted")])
    return rows


def scope_rows(
    info: Mapping[str, Any],
    state: Mapping[str, dict],
    level_of: Callable,
    screen: Mapping[str, Any] | None,
    lang: str,
) -> list[Line]:
    """Oscilloscope template rows: channels, timebase with run state, trigger (4.2)."""
    rows = _channel_rows(info, state, level_of, lang)
    run = ""
    if screen:
        run = (
            t("cards.waiting", lang)
            if screen.get("waiting")
            else (t("cards.running", lang) if screen.get("running") else t("cards.stopped", lang))
        )
    tb = fmt(_value(state, "timebase.scale"), "S") + "/div"
    rows.append([(t("cards.timebase", lang) + " ", "muted"), (tb + (f" · {run}" if run else ""), "")])
    src = _option(state.get("trigger.source"), _value(state, "trigger.source"))
    slope = "↑" if str(_value(state, "trigger.slope")).lower().startswith("ri") else "↓"
    level = fmt(_value(state, "trigger.level"), "V")
    mode = _option(state.get("trigger.mode"), _value(state, "trigger.mode"))
    rows.append([(t("cards.trigger", lang) + " ", "muted"), (f"{src} {slope} {level} {mode}", "")])
    return rows


def generator_rows(info: Mapping[str, Any], state: Mapping[str, dict], lang: str) -> list[Line]:
    """Function generator template rows: one per channel, then the load settings (4.2)."""
    rows: list[Line] = []
    loads: list[str] = []
    channels = sorted({sv.get("channel") for sv in info.get("state", []) if sv.get("channel")})
    for ch in channels:
        on = bool(_value(state, f"ch{ch}.output"))
        wave = _option(state.get(f"ch{ch}.function"), _value(state, f"ch{ch}.function"))
        freq = fmt(_value(state, f"ch{ch}.frequency"), "HZ")
        amp = fmt(_value(state, f"ch{ch}.amplitude"), "V")
        rows.append(
            [
                (f"CH{ch} ", "muted"),
                (t("common.on" if on else "common.off", lang) + " ", ""),
                (f"{wave} {freq} {amp}", ""),
            ]
        )
        loads.append(f"CH{ch} " + fmt(_value(state, f"ch{ch}.load"), "OHM"))
    rows.append([(t("cards.load", lang) + " ", "muted"), (" · ".join(loads), "")])
    return rows


def smu_rows(
    info: Mapping[str, Any], state: Mapping[str, dict], readings: Mapping[str, Any] | None, lang: str
) -> list[Line]:
    """SMU template rows: source, measurement, and the active limit (4.2)."""
    function = _option(state.get("source.function"), _value(state, "source.function"))
    forcing = str(_value(state, "source.function") or "VOLTage").upper().startswith("V")
    set_path = "source.voltage.level" if forcing else "source.current.level"
    set_unit = "V" if forcing else "A"
    setting = fmt(_value(state, set_path), set_unit)
    out = bool(_value(state, "output.enabled"))
    rows: list[Line] = [
        [
            (t("cards.source", lang) + " ", "muted"),
            (f"{function} {setting} ", ""),
            (t("cards.output", lang) + " ", "muted"),
            (t("common.on" if out else "common.off", lang), ""),
        ]
    ]
    rows.append(
        [
            (t("cards.measure", lang) + " ", "muted"),
            (fmt(_read(readings, "core.voltage"), "V") + "  ", ""),
            (fmt(_read(readings, "core.current"), "A"), ""),
        ]
    )
    if bool(_read(readings, "core.limited")):
        regulation = str(_read(readings, "core.regulation") or "CC")
        limit = _read(readings, "core.limit")
        kind = "cards.ccLimited" if regulation == "CC" else "cards.cvLimited"
        rows.append(
            [
                (regulation, "bad"),
                (" " + t(kind, lang, limit=fmt(limit, "A" if regulation == "CC" else "V")), ""),
            ]
        )
    return rows


def meter_rows(info: Mapping[str, Any], readings: Mapping[str, Any] | None, lang: str) -> list[Line]:
    """Multimeter / calibrator template rows: one line per panel readout (4.2)."""
    panel = (info.get("display") or {}).get("panel") or {}
    state = _state_map(info)
    rows: list[Line] = []
    for ro in panel.get("readouts", []):
        when = ro.get("when") or {}
        if when and not all(_value(state, path) == value for path, value in when.items()):
            continue
        label = ro.get("label" if lang == "zh" else "label_en") or ro.get("reading", "")
        value = _read(readings, ro.get("reading", ""))
        rows.append([(label + " ", "muted"), (fmt(value, ro.get("unit"), ro.get("digits") or 5), "bold")])
    return rows


def generic_rows(info: Mapping[str, Any], state: Mapping[str, dict], lang: str) -> list[Line]:
    """No template: the summary's state variables, one per line (4.2)."""
    summary = (info.get("display") or {}).get("summary") or []
    paths = list(summary) or [sv["path"] for sv in info.get("state", []) if not sv.get("readonly")][:4]
    return [
        [(_label(state[p], lang) + " ", "muted"), (_fmt_state(state, p, lang), "")]
        for p in paths
        if p in state
    ]


def _params_line(part: Mapping[str, Any], units: Mapping[str, str], lang: str) -> Line:
    """A part's main parameters, up to three with units (tui-screens.md 4.2)."""
    items = []
    for name, value in (part.get("params") or {}).items():
        unit = units.get(name)
        if unit is None or len(items) == 3:
            continue
        items.append(f"{name.capitalize()} {fmt(value, unit)}")
    return [(" · ".join(items) if items else t("cards.params", lang), "")]


def _port_line(
    part: Mapping[str, Any], port: Mapping[str, Any], level: Mapping[str, Any] | None, session, lang: str
) -> Line:
    """``<端口> ←|→ <对端>  <有效值> <缩略图 4 字符>`` (tui-screens.md 4.2)."""
    ref = f"{part['name']}.{port['name']}"
    ends = opposite_ends(ref, session, lang)
    arrow = ARROWS.get(port.get("direction", ""), "—")
    head = f"{port['name']} {arrow} " + (" · ".join(ends) if ends else "—")
    segments: Line = [(head, "" if ends else "muted")]
    if level is not None:
        dc, rms = level.get("dc", 0.0), level.get("rms", 0.0)
        value = dc if (rms == 0 and dc != 0) else rms
        segments.append(("  " + fmt(value, "V") + " ", ""))
        segments.append((braille(level.get("wave", []), 4), "" if level.get("active") else "muted"))
    return segments


def instrument_card(
    info: Mapping[str, Any],
    session: Mapping[str, Any],
    readings: Mapping[str, Any] | None,
    level_of: Callable,
    screen: Mapping[str, Any] | None,
    lang: str,
) -> CardData:
    """One instrument's card (tui-screens.md 4.2)."""
    state = _state_map(info)
    template = (info.get("display") or {}).get("template")
    if template == "oscilloscope":
        rows = scope_rows(info, state, level_of, screen, lang)
    elif template == "function_generator":
        rows = generator_rows(info, state, lang)
    elif template == "smu":
        rows = smu_rows(info, state, readings, lang)
    elif template in ("multimeter", "calibrator"):
        rows = meter_rows(info, readings, lang)
    else:
        rows = generic_rows(info, state, lang)
    lines = [
        [(info.get("idn", ""), "")],
        *rows,
        _wiring_line(info, session, lang),
        _error_line(info, lang),
    ]
    title: Line = [(info["name"], ""), (f" · {info['model']}", "muted")]
    card = CardData(name=info["name"], title=title, lines=lines)
    card.remote = bool(info.get("remote")) and not info.get("local")
    for e in info.get("errors", []):
        card.problems.append(f"{e['code']} {e['message']}")
    return card


def part_card(
    part: Mapping[str, Any],
    session: Mapping[str, Any],
    level_of: Callable,
    units: Mapping[str, str],
    lang: str,
) -> CardData:
    """One part's card (tui-screens.md 4.2): main parameters, then one line per port."""
    lines: list[Line] = [_params_line(part, units, lang)]
    for port in part.get("ports", []):
        net = port.get("net")
        lines.append(_port_line(part, port, level_of(net) if net else None, session, lang))
    title: Line = [(part["name"], "")]
    if part.get("label"):
        title.append((f" · {part['label']}", "muted"))
    if part.get("dut"):
        title.append((" · DUT", "focus"))
    return CardData(name=part["name"], title=title, lines=lines)


def build_cards(
    session: Mapping[str, Any],
    readings: Mapping[str, Mapping[str, Any]] | None = None,
    levels: Mapping[str, Any] | None = None,
    screens: Mapping[str, Mapping[str, Any]] | None = None,
    catalog: Mapping[str, Any] | None = None,
    lang: str = "zh",
) -> list[CardData]:
    """All cards in bench-file order (instruments then parts), numbered 1–9 (tui-screens.md 4.1)."""
    readings = readings or {}
    screens = screens or {}
    by_net = _levels_by_net(levels)

    def level_of(net: str | None) -> dict[str, Any] | None:
        return by_net.get(net) if net else None

    cards: list[CardData] = []
    for info in session.get("instruments", []):
        cards.append(
            instrument_card(
                info, session, readings.get(info["name"]), level_of, screens.get(info["name"]), lang
            )
        )
    for part in session.get("parts", []):
        cards.append(part_card(part, session, level_of, _units_of(catalog or {}, part.get("type", "")), lang))
    for n, card in enumerate(cards[:9], start=1):
        card.number = n
    for card in cards:
        if card.number:
            card.title.insert(0, (f"{card.number} ", "muted"))
        if card.remote:
            card.title.append((f" · {t('remote.badge', lang)}", "muted"))
    return cards
