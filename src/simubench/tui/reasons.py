"""The reason dialog's content (tui-screens.md 9.1) and the source tracing behind it (D37).

The TUI traces a setting's current value itself: the last ``state`` event that changed the path,
paired with the ``scpi`` write that caused it (the engine emits the state event while processing
the write, before the write's own event — the same pairing the log uses). No such event: the
value comes from the bench file. Conclusions and calculations would come from the engine's causal
explanation; the interface does not expose one for instrument readings yet, so the dialog shows
the 设定 / 限值 / 读数 lines and never computes physics itself (tui-screens.md 9.1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .fmt import fmt, fmt_ns
from .i18n import t
from .log import LogLine, hint_text, source_label


@dataclass
class Evidence:
    """One line of the dialog: a value, where it lives, and where it came from."""

    label: str  # 设定 / 限值 / 读数 / 故障 / 错误 / 命令 / 输出 / 通信
    value: str  # ``电压 15.000 V`` — value text, unit included
    pointer: str = ""  # the state path or network it is read from
    source: str = ""  # ``← 00:00:00.020 脚本 :53122  :SOUR:VOLT 15``

    def lines(self) -> list[str]:
        out = [self.value + (f"  {self.pointer}" if self.pointer else "")]
        if self.source:
            out.append("  " + self.source)
        return out


@dataclass
class ReasonReport:
    """What the dialog shows (tui-screens.md 9.1): title, conclusion, evidence, keys."""

    title: str  # ``原因 · smu 电流 10.000 mA ▲``
    conclusion: str  # the one-sentence first line
    evidences: list[Evidence] = field(default_factory=list)
    hint: str | None = None  # the 9.2 wording when a command was not executed
    jump_seq: int | None = None  # the log line ``⏎`` jumps to


def plain_text(report: ReasonReport) -> str:
    """The dialog's content as plain text, for ``c`` to copy (tui-screens.md 9.1)."""
    out = [report.conclusion]
    for ev in report.evidences:
        out.append(f"{ev.label}  {ev.value}" + (f"  {ev.pointer}" if ev.pointer else ""))
        if ev.source:
            out.append("  " + ev.source)
    if report.hint:
        out.append(report.hint)
    return "\n".join(out)


# ---- source tracing (TUI-side, per tui-screens.md 9.1) ---------------------------------------------


def _scpi_write_for(events: Sequence[Mapping[str, Any]], instrument: str, t_ns: int) -> Mapping | None:
    """The write event a state change belongs to: same instrument, same tick."""
    for ev in reversed(events):
        if (
            ev.get("kind") == "scpi"
            and ev.get("dir") == "write"
            and ev.get("instrument") == instrument
            and int(ev.get("t_start_ns", ev.get("t_ns", -1))) == t_ns
        ):
            return ev
    return None


def _fault_event_for(events: Sequence[Mapping[str, Any]], t_ns: int) -> Mapping | None:
    """The fault that became active at ``t_ns`` (a state event sourced ``fault``)."""
    for ev in reversed(events):
        if (
            ev.get("kind") == "fault"
            and ev.get("reason") in ("active", "injected")
            and int(ev.get("t_ns", -1)) == t_ns
        ):
            return ev
    return None


def trace_source(events: Sequence[Mapping[str, Any]], instrument: str, path: str, lang: str) -> str | None:
    """Where a setting's current value came from (tui-screens.md 9.1), or None for the bench file."""
    for ev in reversed(events):
        if ev.get("kind") != "state" or ev.get("instrument") != instrument or ev.get("path") != path:
            continue
        t_ns = int(ev.get("t_ns", 0))
        src = ev.get("source")
        if src == "scpi":
            write = _scpi_write_for(events, instrument, t_ns)
            if write is not None:
                label = source_label(write, lang)
                return "← " + t(
                    "reason.fromCommand",
                    lang,
                    time=fmt_ns(t_ns),
                    source=label,
                    command=write.get("text") or "",
                )
            return "← " + t("reason.fromEvent", lang, time=fmt_ns(t_ns))
        if src == "configure":
            return "← " + t("reason.fromSettings", lang, time=fmt_ns(t_ns))
        if src == "fault":
            fault = _fault_event_for(events, t_ns)
            if fault is not None:
                return "← " + t("reason.fromFault", lang, id=fault.get("fault", ""))
            return "← " + t("reason.fromEvent", lang, time=fmt_ns(t_ns))
        if src:
            return "← " + t("reason.fromEvent", lang, time=fmt_ns(t_ns))
        return None  # a state change with no source is the bench file's initial value
    return None


def bench_pointer(instrument: str, path: str | None = None) -> str:
    """``/instruments/smu/state`` — the JSON pointer of a value the bench file initializes."""
    return f"/instruments/{instrument}/state"


# ---- the reports ------------------------------------------------------------------------------------


def _label_of(info: Mapping[str, Any], path: str, lang: str) -> str:
    for sv in info.get("state", []):
        if sv.get("path") == path:
            return sv.get("label" if lang == "zh" else "label_en") or path
    return path


def _value_text(info: Mapping[str, Any], path: str, lang: str) -> str:
    for sv in info.get("state", []):
        if sv.get("path") == path:
            value = sv.get("value")
            if isinstance(value, bool):
                return t("common.on" if value else "common.off", lang)
            if isinstance(value, (int, float)):
                return fmt(float(value), sv.get("display_unit") or sv.get("unit"))
            return str(value)
    return "—"


def limited_report(
    info: Mapping[str, Any],
    readings: Mapping[str, Any] | None,
    events: Sequence[Mapping[str, Any]],
    lang: str,
) -> ReasonReport | None:
    """The SMU at its limit (tui-screens.md 5.4): 设定 / 限值 / 读数 (tui-screens.md 9.1)."""
    values = (readings or {}).get("values", {})
    if not values.get("core.limited"):
        return None
    regulation = str(values.get("core.regulation") or "CC")
    instrument = info.get("name", "")
    # CC: the current limit is active while the source forces a voltage; CV the other way round.
    set_path = "source.current.level" if regulation == "CV" else "source.voltage.level"
    limit_path = "compliance.current.limit"
    if regulation == "CC":
        conclusion = t(
            "reason.limitedCC",
            lang,
            limit=fmt(float(values.get("core.limit") or 0), "A"),
            actual=fmt(float(values.get("core.voltage") or 0), "V"),
            setting=fmt(float(_state_value(info, set_path) or 0), "V"),
        )
        subject = t("reason.subjectCurrent", lang, value=fmt(float(values.get("core.current") or 0), "A"))
    else:
        conclusion = t(
            "reason.limitedCV",
            lang,
            limit=fmt(float(values.get("core.limit") or 0), "V"),
            actual=fmt(float(values.get("core.current") or 0), "A"),
            setting=fmt(float(_state_value(info, set_path) or 0), "A"),
        )
        subject = t("reason.subjectVoltage", lang, value=fmt(float(values.get("core.voltage") or 0), "V"))
    evidences = [
        Evidence(
            t("reason.evSetting", lang),
            f"{_label_of(info, set_path, lang)} {_value_text(info, set_path, lang)}",
            set_path,
            trace_source(events, instrument, set_path, lang)
            or "← " + t("reason.fromBench", lang, pointer=bench_pointer(instrument)),
        ),
        Evidence(
            t("reason.evLimit", lang),
            f"{_label_of(info, limit_path, lang)} {_value_text(info, limit_path, lang)}",
            limit_path,
            trace_source(events, instrument, limit_path, lang)
            or "← " + t("reason.fromBench", lang, pointer=bench_pointer(instrument)),
        ),
        Evidence(
            t("reason.evReading", lang),
            f"{t('reason.voltage', lang)} {fmt(float(values.get('core.voltage') or 0), 'V')} · "
            f"{t('reason.current', lang)} {fmt(float(values.get('core.current') or 0), 'A')}",
        ),
    ]
    return ReasonReport(
        t("reason.titleFor", lang, instrument=instrument, subject=subject), conclusion, evidences
    )


def _state_value(info: Mapping[str, Any], path: str) -> Any:
    for sv in info.get("state", []):
        if sv.get("path") == path:
            return sv.get("value")
    return None


def fault_report(
    fault: Mapping[str, Any],
    info: Mapping[str, Any] | None,
    events: Sequence[Mapping[str, Any]],
    lang: str,
) -> ReasonReport:
    """A fault acting on this instrument (trip or a communication fault), tui-screens.md 5.4."""
    spec = fault.get("spec") or {}
    fid = fault.get("id", "")
    kind = spec.get("kind", "")
    source = source_label({"kind": "fault", "source": fault.get("source", "")}, lang)
    description = fault.get("description") or spec.get("message") or kind
    if kind == "trip":
        conclusion = t("reason.trip", lang, id=fid, source=source, description=description)
    else:
        conclusion = t("reason.commFault", lang, id=fid, source=source, description=description)
    evidences = [
        Evidence(
            t("reason.evFault", lang),
            description,
            "",
            "← " + t("reason.fromFaultSource", lang, id=fid, source=source),
        )
    ]
    if kind == "trip" and info is not None:
        evidences.append(
            Evidence(
                t("reason.evOutput", lang),
                t("common.on" if _state_value(info, "output.enabled") else "common.off", lang),
                "output.enabled",
                trace_source(events, info.get("name", ""), "output.enabled", lang) or "",
            )
        )
    return ReasonReport(
        t("reason.titleFault", lang, instrument=(info or {}).get("name", ""), id=fid), conclusion, evidences
    )


def error_report(line: LogLine, lang: str) -> ReasonReport:
    """A command's error (tui-screens.md 9.1): the error, its command, the 9.2 wording."""
    conclusion = t("reason.errorCmd", lang, code=line.error_code or "", message=line.error_message or "")
    evidences = [
        Evidence(
            t("reason.evCommand", lang),
            line.command,
            "",
            "← "
            + t("reason.fromCommand", lang, time=fmt_ns(line.t_ns), source=line.source, command=line.command)
            if line.command
            else "",
        )
    ]
    report = ReasonReport(
        t("reason.titleError", lang, instrument=line.instrument, code=line.error_code or ""),
        conclusion,
        evidences,
        hint=hint_text(line, lang),
        jump_seq=line.seq,
    )
    return report


def silence_report(
    instrument: str, channel: int, silence: Sequence[Mapping[str, Any]], lang: str
) -> ReasonReport | None:
    """Why a scope channel shows no signal (tui-screens.md 6.3, 9.1)."""
    if not silence:
        return None
    conclusion = t("scope.silenceLine", lang, ch=f"CH{channel}", reason=silence_text(silence[0], lang))
    evidences = [Evidence(t("reason.evSilence", lang), silence_text(s, lang)) for s in silence]
    return ReasonReport(
        t("reason.titleFor", lang, instrument=instrument, subject=f"CH{channel}"), conclusion, evidences
    )


def silence_text(silence: Mapping[str, Any], lang: str) -> str:
    """One silence reason as text (tui-screens.md 6.3)."""
    kind = silence.get("kind")
    source = silence.get("source") or ""
    if kind == "unconnected":
        return t("scope.silUnconnected", lang)
    if kind == "source_off":
        return t("scope.silSourceOff", lang, source=source)
    if kind == "zero_setting":
        return t("scope.silZeroSetting", lang, source=source)
    if kind == "no_source":
        return t("scope.silNoSource", lang)
    if kind == "fault":
        return t("scope.silFault", lang, id=silence.get("fault_id") or "")
    return silence.get("detail") or str(kind)


def setting_report(
    info: Mapping[str, Any], path: str, events: Sequence[Mapping[str, Any]], lang: str
) -> ReasonReport:
    """A focused setting (为什么是这个值, D37): value, path, traced source."""
    instrument = info.get("name", "")
    label = _label_of(info, path, lang)
    value = _value_text(info, path, lang)
    source = trace_source(events, instrument, path, lang)
    evidence = Evidence(
        t("reason.evSetting", lang),
        f"{label} {value}",
        path,
        source or "← " + t("reason.fromBench", lang, pointer=bench_pointer(instrument, path)),
    )
    conclusion = t("reason.settingIs", lang, label=label, value=value)
    return ReasonReport(
        t("reason.titleFor", lang, instrument=instrument, subject=label), conclusion, [evidence]
    )
