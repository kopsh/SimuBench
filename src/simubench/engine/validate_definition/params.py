"""Checks of a component's parameters: their types and ranges, the enumerated words, nested and mapped values (instrument-authoring.md 4)."""

from __future__ import annotations

from typing import Any

from ...models.definition import ComponentSpec, InstrumentDefinition, StateSpec
from ..component import Component, binding_of, noise_problems
from ..validate import Report, _ptr, unknown_name
from .lookups import _check_state_ref


def _check_noise(value: Any, pp: str, spec: ComponentSpec, report: Report) -> None:
    """The reading-noise parameter ``noise`` (#746) must be spelled right: a wrong key or value
    would otherwise read as no noise, or fail at the first reading (#752)."""
    for sub, message in noise_problems(value):
        report.add(
            "error",
            "E-DEF-NOISE",
            f"{pp}/{sub}" if sub else pp,
            f"{spec.name}.noise：{message}",
            "noise 只接受 ppm_of_range（≥0）、ref_time_s（>0）、table（量程→rms，≥0）、rms（绝对 rms，≥0，或“读数量 → rms”的映射；与 ppm_of_range/table 互斥）",
            spec.name,
        )


def _check_params(
    d: InstrumentDefinition,
    spec: ComponentSpec,
    cls: type[Component],
    p: tuple[Any, ...],
    channel: bool,
    report: Report,
    at: tuple[str, ...] = ("params",),
) -> None:
    """``at``: where the parameters sit under ``p`` (an option's are directly there)."""
    for key, value in spec.params.items():
        pp = _ptr(*p, *at, key)
        if key not in cls.defaults:
            unknown_name(
                report,
                "W-DEF-PARAM-UNKNOWN",
                pp,
                f"{spec.type} 没有参数 {key!r}，它不会起作用",
                key,
                list(cls.defaults),
                spec.name,
                severity="warning",
            )
        if key == "noise" and key in cls.defaults and binding_of(value) is None:
            _check_noise(value, pp, spec, report)
        mapping = value if isinstance(value, dict) and "from" in value else None
        ref = binding_of(value)
        words = cls.choices.get(key)
        if words and ref is None and isinstance(value, str):
            _check_choice(value, words, pp, f"{spec.type}.{key}", spec.name, report)
        if ref is None:
            continue
        st = _check_state_ref(
            d, ref, channel, _ptr(*p, *at, key, "from") if mapping else pp, spec.name, report
        )
        if st is not None and words:
            # Every value the setting can take must be a word the parameter knows (#308).
            table = (mapping or {}).get("map") or {}
            if _int_range_mapped(st, mapping):
                # A small int setting whose every value the map turns into a word (the SR86x's
                # enumerations are indexes with word aliases, #333); a nested mapping as a value
                # is resolved at run time from its own setting.
                values = (
                    [True, False] if st.type == "bool" else range(int(st.min), int(st.max) + 1)  # type: ignore[arg-type]
                )
                for i in values:
                    word = table.get(str(i), table.get(i, table.get(str(int(i)))))
                    if not isinstance(word, dict):
                        _check_choice(str(word), words, pp, f"{spec.type}.{key}", spec.name, report)
            elif st.type != "enum":
                report.add(
                    "error",
                    "E-DEF-PARAM-VALUE",
                    pp,
                    f"{spec.type}.{key} 只取 {', '.join(words)}，绑定的状态 {ref!r} 是 {st.type}，可取其他值",
                    "绑定一个枚举状态",
                    spec.name,
                )
            else:
                _check_enum_words(d, st, mapping, words, channel, pp, f"{spec.type}.{key}", spec.name, report)
        if mapping:
            _check_nested(d, mapping, channel, (*p, *at, key), spec.name, report)
        if st is not None and mapping and mapping.get("map"):
            _check_map_keys(st, mapping["map"], (*p, *at, key, "map"), spec.name, report)
            continue  # the table decides the parameter's type
        default = cls.defaults.get(key)
        if st is not None and default is not None and not _compatible(default, st):
            report.add(
                "error",
                "E-DEF-PARAM-TYPE",
                pp,
                f"{spec.type}.{key} 是{_kind(default)}，绑定的状态 {value[1:]!r} 是 {st.type}",
                f"绑定一个{_kind(default)}状态",
                spec.name,
            )


def _check_enum_words(
    d: InstrumentDefinition,
    st: Any,
    mapping: dict[str, Any] | None,
    words: tuple[str, ...],
    channel: bool,
    pointer: str,
    what: str,
    subject: str,
    report: Report,
) -> None:
    """Every option of an enum setting, through the mapping, must give a word the parameter knows.
    A mapped value that is itself a mapping of another setting (resolved at run time, e.g. the
    SMA100B's sweep step mode taken from the frequency or the level sweep's, #335) is checked the
    same way against that setting's options."""
    table = (mapping or {}).get("map") or {}
    for option in st.options or []:
        word = table.get(option, (mapping or {}).get("default", option))
        if isinstance(word, dict) and isinstance(word.get("from"), str):
            ref = binding_of(word)
            inner = _check_state_ref(d, ref, channel, pointer, subject, report) if ref else None
            if inner is not None and inner.type == "enum":
                _check_enum_words(d, inner, word, words, channel, pointer, what, subject, report)
            continue
        if isinstance(word, str) and word.startswith("$"):
            continue  # another setting's value, resolved at run time
        _check_choice(str(word), words, pointer, what, subject, report)


def _int_range_mapped(st: Any, mapping: dict[str, Any] | None) -> bool:
    """An int setting with a small declared range (at most 64 values) whose map names every value,
    or a bool setting whose map names both (keys "True"/"False" or "1"/"0", #429)."""
    table = (mapping or {}).get("map") or {}
    if st.type == "bool" and table:
        return all(any(k in table for k in keys) for keys in (("True", "1", True), ("False", "0", False)))
    if st.type != "int" or st.min is None or st.max is None or not table:
        return False
    lo, hi = int(st.min), int(st.max)
    return hi - lo <= 64 and all(str(i) in table or i in table for i in range(lo, hi + 1))


def _check_choice(
    word: str, words: tuple[str, ...], pointer: str, what: str, subject: str, report: Report
) -> None:
    """A word parameter's value must be one of its words, long or short form (#308)."""
    from ...scpi.values import short_form

    if word.upper() in {w.upper() for w in words} | {short_form(w) for w in words}:
        return
    report.add(
        "error",
        "E-DEF-PARAM-VALUE",
        pointer,
        f"{what} 不认 {word!r}",
        f"可选值：{', '.join(words)}",
        subject,
    )


def _check_nested(
    d: InstrumentDefinition,
    mapping: dict[str, Any],
    channel: bool,
    where: tuple[Any, ...],
    subject: str,
    report: Report,
) -> None:
    """The settings read inside a mapping's values (D70: a ``map`` or ``table`` entry, or the
    ``default``, that is ``$path`` or another mapping) must exist, and a nested ``map``'s keys must
    be values its setting can take — the same checks the outer mapping gets, at its own place."""
    entries = [
        *((("map", k), v) for k, v in dict(mapping.get("map") or {}).items()),
        *((("table", k), v) for k, v in dict(mapping.get("table") or {}).items()),
        (("default",), mapping.get("default")),
    ]
    for at, inner in entries:
        ref = binding_of(inner)
        if ref is None:
            continue
        nested = inner if isinstance(inner, dict) else None
        pointer = _ptr(*where, *at, "from") if nested else _ptr(*where, *at)
        st = _check_state_ref(d, ref, channel, pointer, subject, report)
        if nested is None:
            continue
        if st is not None and nested.get("map"):
            _check_map_keys(st, nested["map"], (*where, *at, "map"), subject, report)
        _check_nested(d, nested, channel, (*where, *at), subject, report)


def _check_map_keys(
    st: StateSpec, table: dict[str, Any], where: tuple[Any, ...], subject: str, report: Report
) -> None:
    """A key of ``map`` that the setting can never take never applies."""
    if st.type != "enum":
        return
    options = {o.upper() for o in st.options or []}
    for k in table:
        if str(k).upper() not in options:
            unknown_name(
                report,
                "W-DEF-MAP-KEY",
                _ptr(*where, k),
                f"设置的取值中没有 {k!r}，这一项不会生效",
                str(k),
                list(st.options or []),
                subject,
                severity="warning",
                fallback=f"可选值：{', '.join(st.options or [])}",
            )


def _kind(default: Any) -> str:
    if isinstance(default, bool):
        return "布尔量"
    if isinstance(default, (int, float)):
        return "数值"
    return "文字"


def _compatible(default: Any, st: StateSpec) -> bool:
    """Enumerations pass to numbers and switches: components read named values (FIFTy, OFF)."""
    if isinstance(default, bool):
        return st.type in ("bool", "int", "enum")
    if isinstance(default, (int, float)):
        return st.type in ("float", "int", "bool", "enum")
    if isinstance(default, str) and default.startswith("$"):
        return True  # bound by default: the component reads a setting, of any type
    if isinstance(default, str):
        return st.type in ("enum", "string")
    return True
