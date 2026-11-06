"""The instrument screen's row renderers and the error reconciliation (issue 635): pure
functions over the data the screen already has (lang, info, state, readings, the log lines), so
they unit-test without a terminal — cards.py's standing rule, now kept here too. The screen picks
the renderer per template and turns the rows into text; these functions only decide what the rows
say. ``read_away`` carries the queue's one domain rule: a slot belongs to the newest matching
error event, so an entry turns grey the moment the ``:SYSTem:ERRor?`` that reads it away lands.
"""

from __future__ import annotations

from typing import Any

from . import controls as C
from . import i18n
from .fmt import braille_rows, cell_len, fmt, fmt_ns, pad, ralign
from .log import source_label


def row(
    label: str, value: str, *, bold: bool = True, mark: str = "", width: int = 6
) -> list[tuple[str, str]]:
    return [
        (pad(label, max(width, cell_len(label) + 1)), "muted"),
        (value, "bold" if bold else ""),
        (" " + mark if mark else "", "bad"),
    ]


def flag(label: str, on: bool, tokens: str = "ok") -> list[tuple[str, str]]:
    return [(label, tokens if on else "muted"), ("  ", "")]


def value_of(readings: dict[str, Any], name: str) -> Any:
    """A reading by its name, whatever component gives it (``synth.frequency`` -> frequency)."""
    values = readings.get("values", {})
    if name in values:
        return values[name]
    return next((v for k, v in values.items() if k.rsplit(".", 1)[-1] == name), None)


def smu_rows(lang: str, state: dict[str, dict], readings: dict[str, Any]) -> list[list[tuple[str, str]]]:
    """状态 / 电压 / 电流 columns, 设定 next to 限值; ▲ on the limited column (5.2)."""
    readings = readings.get("values", {})
    limited = bool(readings.get("core.limited"))
    regulation = str(readings.get("core.regulation") or "")
    voltage_limited = limited and regulation == "CV"
    current_limited = limited and regulation == "CC"
    label_w = 1 + max(
        cell_len(i18n.t(k, lang)) for k in ("instrument.set", "instrument.actual", "instrument.limit")
    )
    value_w = 10
    status_w = 1 + max(
        cell_len(i18n.t(k, lang)) for k in ("cards.output", "instrument.function", "instrument.regulation")
    )
    status_col = status_w + 8  # On / Off, VOLTage / CURRent, CV / CC

    def sv_text(path: str) -> str:
        sv = state.get(path) or {}
        return C.summary_text(sv.get("value"), sv, lang)

    def cell(label: str, value: str, mark: str = "") -> list[tuple[str, str]]:
        bold = value not in (i18n.t("common.on", lang), i18n.t("common.off", lang))
        return [
            (pad(label, label_w), "muted"),
            (ralign(value, value_w), "bold" if bold else ""),
            (" " + mark if mark else "", "bad" if mark else ""),
        ]

    col = label_w + value_w + 2
    header = [
        (pad(i18n.t("instrument.colStatus", lang), status_col + 2), "muted"),
        (pad(i18n.t("instrument.colVoltage", lang), col), "muted"),
        (pad(i18n.t("instrument.colCurrent", lang), col), "muted"),
    ]

    def status(label: str, value: str, tokens: str = "") -> list[tuple[str, str]]:
        return [(pad(label, status_w), "muted"), (pad(value, max(1, status_col - status_w)), tokens)]

    status_rows = [
        status(i18n.t("cards.output", lang), sv_text("output.enabled")),
        status(i18n.t("instrument.function", lang), sv_text("source.function")),
    ]
    regulation_row = status(
        i18n.t("instrument.regulation", lang), regulation or "—", "bad" if limited else ""
    )
    gap = [(" " * 2, "")]
    v1 = cell(i18n.t("instrument.set", lang), sv_text("source.voltage.level"))
    c1 = cell(i18n.t("instrument.set", lang), sv_text("source.current.level"))
    v2 = cell(i18n.t("instrument.actual", lang), fmt(readings.get("core.voltage"), "V"))
    c2 = cell(i18n.t("instrument.actual", lang), fmt(readings.get("core.current"), "A"))
    v3 = cell(
        i18n.t("instrument.limit", lang),
        sv_text("compliance.voltage.limit"),
        "▲" if voltage_limited else "",
    )
    c3 = cell(
        i18n.t("instrument.limit", lang),
        sv_text("compliance.current.limit"),
        "▲" if current_limited else "",
    )
    return [
        header,
        status_rows[0] + gap + v1 + gap + c1,
        status_rows[1] + gap + v2 + gap + c2,
        regulation_row + gap + v3 + gap + c3,
    ]


def panel_rows(
    lang: str,
    info: dict[str, Any],
    readings: dict[str, Any],
    width: int | None = None,
    budget: int | None = None,
) -> list[list[tuple[str, str]]]:
    """Readouts on top (numbers bold), then one column per panel group, as many as fit
    (tui-screens.md 5.2). A group block the ``budget`` has no room for is left out whole
    (#374, #454): a group title above the fold misleads."""
    panel = info["display"]["panel"]
    state = {sv["path"]: sv for sv in info.get("state", [])}
    values = readings.get("values", {})
    rows: list[list[tuple[str, str]]] = []
    shown = []
    for ro in panel.get("readouts", []):
        when = ro.get("when") or {}
        if when and not all((state.get(p) or {}).get("value") == v for p, v in when.items()):
            continue
        shown.append(ro)
    label_w = max((cell_len(ro.get("label_en") or ro.get("label") or "") for ro in shown), default=0) + 1
    for ro in shown:
        label = ro.get("label_en") or ro.get("label") or ro.get("reading", "")
        value = values.get(ro.get("reading", ""))
        if isinstance(value, int) and not isinstance(value, bool) and not ro.get("unit"):
            rows.append([(pad(label, label_w), "muted"), (str(value), "bold")])  # a count (#355)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            text = fmt(value, ro.get("unit"), ro.get("digits") or 5)
            rows.append([(pad(label, label_w), "muted"), (text, "bold")])
        else:
            rows.append([(pad(label, label_w), "muted"), (C.summary_text(value, None, lang), "")])
    if rows:
        rows.append([("", "")])
    groups = {
        (group.get("label_en") or group.get("label") or ""): [
            pc["state"] for pc in group.get("controls", []) if pc.get("state")
        ]
        for group in panel.get("groups", [])
    }
    group_budget = budget - len(rows) if budget is not None else None
    return rows + grouped_columns(lang, groups, state, width, budget=group_budget)


def readings_rows(readings: dict[str, Any]) -> list[list[tuple[str, str]]]:
    """The numeric readings of an instrument without a template view (an electronic load, an RF
    generator): ``component.name value`` (#282)."""
    values = readings.get("values", {})
    items = [(k, v) for k, v in values.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not items:
        return []
    label_w = max(cell_len(k) for k, _ in items) + 1
    rows = [[(pad(k, label_w), "muted"), (fmt(float(v), None, 5), "bold")] for k, v in items[:8]]
    return [*rows, [("", "")]]


def spectrum_rows(
    lang: str, readings: dict[str, Any], state: dict[str, dict], width: int, budget: int | None = None
) -> list[list[tuple[str, str]]]:
    """The analyzer's trace as braille (4 rows of dots per text row), the marker, RBW/VBW and
    sweep time, then its settings (#282) — the same data the web screen draws."""
    values = readings.get("values", {})
    traces = readings.get("traces") or []
    cols = max(10, width - 4)
    rows: list[list[tuple[str, str]]] = []
    trace = next((t for t in traces if t.get("name") == "trace1"), traces[0] if traces else None)
    if trace and trace.get("values"):
        wave = [float(v) for v in trace["values"]]
        for line in braille_rows(wave, cols, 4):
            rows.append([(line, "ch1")])
        lo, hi = min(wave), max(wave)
        rows.append([(f"{fmt(hi, 'dBm', 4)} top · {fmt(lo, 'dBm', 4)} floor", "muted")])
    key = next((k for k in values if k.endswith(".marker_x")), None)
    if key:
        base = key.rsplit(".", 1)[0]
        mx, my = values.get(f"{base}.marker_x"), values.get(f"{base}.marker_y")
        rows.append(
            [
                ("Marker ", "muted"),
                (fmt(float(mx), "Hz", 6), "bold"),
                ("  ", ""),
                (fmt(float(my), "dBm", 4), "bold"),
            ]
        )
        line: list[tuple[str, str]] = []
        for name, label, unit in (
            ("rbw", "RBW", "Hz"),
            ("vbw", "VBW", "Hz"),
            ("sweep_time", "Sweep", "s"),
        ):
            v = values.get(f"{base}.{name}")
            if isinstance(v, (int, float)):
                line += [(f"{label} ", "muted"), (fmt(float(v), unit, 3), ""), ("   ", "")]
        if values.get(f"{base}.uncal"):
            line.append(("UNCAL", "bad"))
        if line:
            rows.append(line)
    rows.append([("", "")])
    return rows + generic_rows(lang, state, width, budget - len(rows) if budget is not None else None)


def rf_rows(
    lang: str,
    state: dict[str, dict],
    readings: dict[str, Any],
    width: int,
    budget: int | None = None,
) -> list[list[tuple[str, str]]]:
    """The RF source as the web screen shows it (#355): frequency and level, RF and modulation
    flags, the reference, the sweep point and the output's spectral lines — then its settings
    in three groups. Not the synthesizer's internal quantities. A group block the ``budget``
    has no room for is left out whole (#374): a title alone above the fold misleads."""
    on = lambda path: (state.get(path) or {}).get("value") is True  # noqa: E731
    freq = value_of(readings, "frequency")
    level = (state.get("power.level") or {}).get("value")
    rows: list[list[tuple[str, str]]] = []
    if isinstance(freq, (int, float)):
        rows.append([(pad("Frequency", 10), "muted"), (fmt(float(freq), "Hz", 10), "bold")])
    if isinstance(level, (int, float)):
        rows.append([(pad("Level", 10), "muted"), (fmt(float(level), "dBm", 4), "bold")])
    external = (state.get("rosc.source") or {}).get("value") == "EXTernal"
    locked = value_of(readings, "locked") is not False
    flags = flag("RF ON" if on("output.enabled") else "RF OFF", on("output.enabled"))
    for mod in ("AM", "FM", "PM"):
        flags += flag(mod, on(f"{mod.lower()}.state"))
    reference = ("EXT locked" if locked else "EXT missing") if external else "INT ref"
    flags += [(reference, ("ok" if locked else "bad") if external else "muted")]
    rows.append(flags)
    detail: list[tuple[str, str]] = []
    mode = str((state.get("freq.mode") or {}).get("value") or "CW")
    if mode.upper() not in ("CW", "FIX", "FIXED"):
        point, points = value_of(readings, "point"), value_of(readings, "points")
        detail += [("Point ", "muted"), (f"{int(point or 0)}/{int(points or 0)}", ""), ("   ", "")]
    lines = value_of(readings, "lines")
    if isinstance(lines, (int, float)):
        detail += [("Lines ", "muted"), (str(int(lines)), ""), ("   ", "")]
    beta = value_of(readings, "beta")
    if isinstance(beta, (int, float)) and beta > 0:
        omitted = float(value_of(readings, "omitted_power") or 0.0) * 100
        detail += [
            ("β ", "muted"),
            (f"{beta:.4g}", ""),
            ("   ", ""),
            ("omitted ", "muted"),
            (f"{omitted:.3f} %", ""),
        ]
    if detail:
        rows.append(detail)
    rows.append([("", "")])
    groups = {
        "Carrier": ["freq.cw", "power.level", "output.enabled"],
        "Modulation": ["am.state", "am.depth", "fm.state", "fm.dev", "pm.state", "pm.dev"],
        "Sweep": ["freq.mode", "sweep.start", "sweep.stop", "sweep.points", "sweep.dwell"],
    }
    group_budget = budget - len(rows) if budget is not None else None
    return rows + grouped_columns(lang, groups, state, width, budget=group_budget)


def load_rows(
    lang: str, state: dict[str, dict], readings: dict[str, Any], width: int
) -> list[list[tuple[str, str]]]:
    """The electronic load as the web screen shows it (#355): measured V, I and P, the input,
    the mode and whether it holds its setting — then its settings."""
    rows: list[list[tuple[str, str]]] = []
    for name, label, unit, digits in (
        ("voltage", "V", "V", 6),
        ("current", "I", "A", 6),
        ("power", "P", "W", 4),
    ):
        v = value_of(readings, name)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            rows.append([(pad(label, 3), "muted"), (fmt(float(v), unit, digits), "bold")])
    enabled = (state.get("input.enabled") or {}).get("value") is True
    mode = (state.get("function.mode") or {}).get("value")
    regulating = value_of(readings, "regulating") is not False
    flags = flag("INPUT ON" if enabled else "INPUT OFF", enabled)
    if mode is not None:
        # The mode as the front panel names it: CC, CV, CR, CP.
        short = {"CURR": "CC", "VOLT": "CV", "RES": "CR", "POW": "CP"}
        word = str(mode).upper()
        flags += [
            (next((v for k, v in short.items() if word.startswith(k)), str(mode)), "bold"),
            ("  ", ""),
        ]
    if enabled and not regulating:
        flags += [("UNREG", "bad")]
    rows += [flags, [("", "")]]
    settings = [
        p
        for p in (
            "function.mode",
            "current.level",
            "voltage.level",
            "resistance.level",
            "power.level",
            "source.current.level",
            "source.voltage.level",
            "source.resistance.level",
            "source.power.level",
            "input.enabled",
        )
        if p in state
    ]
    return rows + grouped_columns(lang, {"Settings": settings}, state, width)


def lockin_rows(
    lang: str, readings: dict[str, Any], state: dict[str, dict], width: int
) -> list[list[tuple[str, str]]]:
    """X, Y, R and θ of the lock-in's output (#282), then its settings."""
    values = readings.get("values", {})
    rows: list[list[tuple[str, str]]] = []
    for name, unit in (("X", "V"), ("Y", "V"), ("R", "V"), ("THETA", "DEG")):
        key = next((k for k in values if k.rsplit(".", 1)[-1] == name), None)
        if key is None:
            continue
        label = "θ" if name == "THETA" else name
        rows.append([(pad(label, 3), "muted"), (fmt(float(values[key]), unit, 5), "bold")])
    if rows:
        rows.append([("", "")])
    return rows + generic_rows(lang, state, width)


def generic_rows(
    lang: str, state: dict[str, dict], width: int | None = None, budget: int | None = None
) -> list[list[tuple[str, str]]]:
    """State variables grouped by first path segment, as many columns as fit (5.2); a group
    block the ``budget`` has no room for is left out whole (#374)."""
    groups: dict[str, list[str]] = {}
    for path in state:
        groups.setdefault(path.split(".", 1)[0], []).append(path)
    return grouped_columns(lang, groups, state, width, budget)


def grouped_columns(
    lang: str,
    groups: dict[str, list[str]],
    state: dict[str, dict],
    width: int | None = None,
    budget: int | None = None,
) -> list[list[tuple[str, str]]]:
    """``label value`` rows under their group name, one column per group. Each column is as
    wide as its widest row (labels and values aligned, #283); as many columns go side by side
    as ``width`` holds, and the next ones form a block below — nothing wraps or is cut (#282).
    ``budget`` is the rows this call may emit: a block it has no room for is left out whole
    with the blocks after it (#374) — a group title above the fold misleads."""
    columns: list[list[tuple[str, str]]] = []  # per column: (label, value) rows; row 0 = title
    for name, paths in list(groups.items())[:3]:  # up to three groups, as before (5.2)
        col: list[tuple[str, str]] = [(name, "")]
        for path in paths:
            sv = state.get(path)
            if sv is None:
                continue
            label = C.label_of(sv, lang, fallback=path.rsplit(".", 1)[-1])
            value = (
                "AUTO" if C.inactive_under_auto(state, path) else C.summary_text(sv.get("value"), sv, lang)
            )
            col.append((label, value))
        if len(col) > 1:
            columns.append(col)
    label_w = [max((cell_len(lab) for lab, _ in col[1:]), default=0) for col in columns]
    col_w = [
        max(cell_len(col[0][0]), max((label_w[k] + 1 + cell_len(v) for _, v in col[1:]), default=0))
        for k, col in enumerate(columns)
    ]
    limit = width - 2 if width and width > 2 else 10_000  # the box's padding
    blocks: list[list[int]] = []
    for k in range(len(columns)):
        if blocks and sum(col_w[j] + 3 for j in blocks[-1]) + col_w[k] <= limit:
            blocks[-1].append(k)
        else:
            blocks.append([k])
    rows: list[list[tuple[str, str]]] = []
    emitted = 0
    for b, block in enumerate(blocks):
        height = max((len(columns[k]) for k in block), default=0)
        if budget is not None and emitted + (1 if b else 0) + height > budget:
            break
        if b:
            rows.append([("", "")])
            emitted += 1
        for n in range(height):
            row: list[tuple[str, str]] = []
            for k in block:
                col = columns[k]
                if n == 0:
                    row.append((pad(col[0][0], col_w[k]), "muted"))
                elif n < len(col):
                    label, value = col[n]
                    row.append((pad(label, label_w[k] + 1), "muted"))
                    row.append((pad(value, col_w[k] - label_w[k] - 1), "bold"))
                else:
                    row.append((" " * col_w[k], ""))
                row.append((" " * 3, ""))
            if any(cell_len(v.strip()) for v, _ in row):
                rows.append(row)
                emitted += 1
    return rows


def reason_line(
    lang: str,
    template: str,
    info: dict[str, Any],
    readings: dict[str, Any],
    faults: list[dict[str, Any]],
) -> list[tuple[str, str]] | None:
    """The 原因 line of a troubled channel box, from existing data only (tui-screens.md 5.4)."""
    readings = readings.get("values", {})
    if template == "smu" and readings.get("core.limited"):
        regulation = str(readings.get("core.regulation") or "CC")
        set_path = "source.current.level" if regulation == "CV" else "source.voltage.level"
        state = {sv["path"]: sv for sv in info.get("state", [])}
        setting = C.summary_text((state.get(set_path) or {}).get("value"), state.get(set_path), lang)
        if regulation == "CV":
            text = i18n.t(
                "reason.limitedCV",
                lang,
                limit=fmt(readings.get("core.limit"), "V"),
                actual=fmt(readings.get("core.current"), "A"),
                setting=setting,
            )
        else:
            text = i18n.t(
                "reason.limitedCC",
                lang,
                limit=fmt(readings.get("core.limit"), "A"),
                actual=fmt(readings.get("core.voltage"), "V"),
                setting=setting,
            )
        return [(i18n.t("reason.title", lang), "focus"), ("  " + text, "")]
    for fault in faults:
        spec = fault.get("spec") or {}
        source = source_label({"kind": "fault", "source": fault.get("source", "")}, lang)
        if spec.get("kind") == "trip":
            text = i18n.t(
                "reason.trip",
                lang,
                id=fault.get("id", ""),
                source=source,
                description=fault.get("description", ""),
            )
            return [(i18n.t("reason.title", lang), "focus"), ("  " + text, "bad")]
        if spec.get("kind") in ("timeout", "disconnect", "garble", "latency", "busy"):
            text = i18n.t(
                "reason.commFault",
                lang,
                id=fault.get("id", ""),
                source=source,
                description=fault.get("description", ""),
            )
            return [(i18n.t("reason.title", lang), "focus"), ("  " + text, "")]
    return None


def read_away(
    lang: str,
    events: list[dict[str, Any]],
    queue: list[dict[str, Any]],
    lines: list[Any],
    instrument: str,
) -> list[tuple[int, str, str]]:
    """Errors seen in the event stream that the queue no longer holds, oldest first.

    A queue slot belongs to the newest matching event, so an entry turns grey the moment the
    ``:SYSTem:ERRor?`` that reads it away lands (tui-screens.md 5.1 #4, 14).
    """
    # `events` stays the full stream: the origin lookup needs the fault events on it, so the
    # error-only filter lands in its own name.
    error_events = [e for e in events if e.get("kind") == "error" and e.get("instrument") == instrument]
    remaining: dict[tuple[int, str], int] = {}
    for e in queue:
        key = (int(e["code"]), str(e["message"]))
        remaining[key] = remaining.get(key, 0) + 1
    out: list[tuple[int, str, str]] = []
    for ev in reversed(error_events):
        key = (int(ev.get("code") or 0), str(ev.get("message") or ""))
        if remaining.get(key, 0) > 0:
            remaining[key] -= 1  # still queued: the newest identical events fill the slots
            continue
        command, source = error_origin(lang, lines, events, ev)
        origin = ""
        if command:
            origin = "← " + " ".join(
                part for part in (fmt_ns(int(ev.get("t_ns", 0))), source, command) if part
            )
        out.append((int(ev.get("code") or 0), str(ev.get("message") or ""), origin))
    out.reverse()
    return out


def error_origin(
    lang: str, lines: list[Any], events: list[dict[str, Any]], ev: dict[str, Any]
) -> tuple[str, str]:
    """The command that caused the error and its source, from the log's pairing (7.1)."""
    seq = ev.get("seq")
    for line in lines:
        if line.error_seq == seq:
            return line.command, line.source
    # An error no command owns (a tripped fault): name the fault and its source.
    for other in events:
        if (
            other.get("kind") == "fault"
            and other.get("reason") in ("active", "injected")
            and int(other.get("t_ns", -2)) == int(ev.get("t_ns", -1))
        ):
            source = source_label({"kind": "fault", "source": other.get("source", "")}, lang)
            return i18n.t("log.faultActive", lang, id=other.get("fault", "")), source
    return "", source_label(ev, lang)
