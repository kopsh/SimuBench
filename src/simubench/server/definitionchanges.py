"""What changed between two versions of an instrument definition, in the instrument's language (T3,
instrument-authoring.md 7.2): commands added, removed or changed (binding, parameters, reply
format); settings (state) added or removed, or with a different range, default or options; ranges
and component parameters ("DC ranges gain 1000 V", "input impedance 10 MΩ → 1 GΩ"); identity,
specs, deviation limits and template inputs. Everything else (ports, wiring, display, the
description, ...) is described the same way, by its top-level key.

Each item names the category, the object and the old and new values; the labels are the interface's
(i18n). The comparison runs over the *parsed* data of two versions, so rewriting comments or
reordering keys yields no items. ``DefinitionFiles`` passes both versions layered (``extends`` and the
category default merged in, D59), so the items describe the instrument each version runs. A scalar list (ranges) compares as a multiset, so reordering it is
no change either, while gained and lost values become their own items.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from ..models import api as M

#: Sections compared with their own, instrument-shaped logic; every other top-level key (ports,
#: wiring, display, description, ...) goes through the generic one.
_OWN = frozenset(
    {
        "commands",
        "deviations",
        "state",
        "channel_state",
        "components",
        "channel_components",
        "identity",
        "specs",
        "template",
        "channels",
    }
)
#: Never described: the file's wrapper.
_WRAPPER = frozenset({"schema", "id"})


def definition_changes(old: dict[str, Any], new: dict[str, Any]) -> list[M.DefinitionChange]:
    """The changes from version ``old`` to version ``new`` (both parsed definition data)."""
    out: list[M.DefinitionChange] = []
    for kind, section, key_field in (
        ("command", "commands", "header"),
        ("deviation", "deviations", "param"),
    ):
        a, b = _by_key(old.get(section), key_field), _by_key(new.get(section), key_field)
        for key in sorted(b.keys() - a.keys()):
            out.append(_item(kind, "added", key))
        for key in sorted(a.keys() - b.keys()):
            out.append(_item(kind, "removed", key))
        for key in sorted(a.keys() & b.keys()):
            out += _value(kind, key, a[key], b[key])
    for section in ("state", "channel_state"):
        a, b = old.get(section) or {}, new.get(section) or {}
        for name in sorted(b.keys() - a.keys()):
            out.append(_item("state", "added", _state_target(section, name)))
        for name in sorted(a.keys() - b.keys()):
            out.append(_item("state", "removed", _state_target(section, name)))
        for name in sorted(a.keys() & b.keys()):
            out += _value("state", _state_target(section, name), a[name], b[name])
    for kind, section in (("component", "components"), ("channel_component", "channel_components")):
        a, b = _by_key(old.get(section), "name"), _by_key(new.get(section), "name")
        for name in sorted(b.keys() - a.keys()):
            out.append(_item(kind, "added", name))
        for name in sorted(a.keys() - b.keys()):
            out.append(_item(kind, "removed", name))
        for name in sorted(a.keys() & b.keys()):
            if a[name].get("type") != b[name].get("type"):
                out.append(_item(kind, "changed", name, "type", a[name].get("type"), b[name].get("type")))
            pa, pb = a[name].get("params") or {}, b[name].get("params") or {}
            for param in sorted(pa.keys() | pb.keys()):
                out += _value(kind, f"{name}.{param}", pa.get(param), pb.get(param))
    for kind, section in (
        ("identity", "identity"),
        ("specs", "specs"),
        ("template", "template"),
        ("channels", "channels"),
    ):
        out += _value(kind, section, old.get(section), new.get(section))
    for key in sorted((set(old) | set(new)) - _WRAPPER - _OWN):  # ports, wiring, display, description, ...
        out += _value(key, key, old.get(key), new.get(key))
    return [_with_still_old(c, new) for c in out]


#: How many places a specs change lists at most.
_STILL_OLD_MAX = 8


def _numbers(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [float(value)]
    if isinstance(value, list | tuple):
        return [n for v in value for n in _numbers(v)]
    return []


def _same(a: float, b: float) -> bool:
    if not (math.isfinite(a) and math.isfinite(b)):
        return a == b
    return a == b or abs(a - b) <= 1e-9 * max(abs(a), abs(b))


def _with_still_old(change: M.DefinitionChange, new: dict[str, Any]) -> M.DefinitionChange:
    """A specs change with the places that still hold its old number: the specs are a record (D3)
    and the simulation reads the settings and the component parameters, so a data-sheet value
    edited alone changes nothing — this names what would have to change with it. A place that
    refers to the spec with ``same_as`` (D82) already follows it (``new`` is layered and resolved),
    so it shows as its own change, not here."""
    if change.kind != "specs" or change.action == "added":
        return change
    gone = [n for n in _numbers(change.old) if not any(_same(n, m) for m in _numbers(change.new))]
    if not gone:
        return change
    found = [where for where, value in _behaviour_numbers(new) if any(_same(value, n) for n in gone)]
    return change.model_copy(update={"still_old": found[:_STILL_OLD_MAX]}) if found else change


#: Component parameters that record the condition a table was measured under, not a figure that
#: should follow the data sheet: ``noise_bandwidth`` keeps the bandwidth the noise table was taken
#: at, so a wider bandwidth scales the noise (#1427). Listing it as "still old" would ask the author
#: to undo exactly that.
_MEASURED_AT = frozenset({"noise_bandwidth"})


def _behaviour_numbers(data: dict[str, Any]) -> list[tuple[str, float]]:
    """The numbers the simulation runs on, with their JSON pointers: settings' limits, defaults and
    special values, and component parameters (nested tables included) other than the
    measurement conditions (``_MEASURED_AT``)."""
    out: list[tuple[str, float]] = []
    for section in ("state", "channel_state"):
        for name, spec in (data.get(section) or {}).items():
            if not isinstance(spec, dict):
                continue
            for field in ("min", "max", "default", "rst"):
                out += [(f"/{section}/{_esc(name)}/{field}", n) for n in _numbers(spec.get(field))]
            for word, value in (spec.get("special") or {}).items():
                out += [(f"/{section}/{_esc(name)}/special/{_esc(word)}", n) for n in _numbers(value)]
    for section in ("components", "channel_components"):
        for item in data.get(section) or []:
            if isinstance(item, dict) and isinstance(item.get("params"), dict):
                params = {k: v for k, v in item["params"].items() if k not in _MEASURED_AT}
                out += _walk(params, f"/{section}/{_esc(str(item.get('name')))}/params")
    return out


def _walk(value: Any, where: str) -> list[tuple[str, float]]:
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _walk(v, f"{where}/{_esc(str(k))}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _walk(v, f"{where}/{i}")]
    return [(where, n) for n in _numbers(value)]


def _esc(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def _item(
    kind: str, action: str, target: str, detail: str | None = None, old: Any = None, new: Any = None
) -> M.DefinitionChange:
    return M.DefinitionChange(kind=kind, action=action, target=target, detail=detail, old=old, new=new)


def _value(kind: str, target: str, a: Any, b: Any, detail: str | None = None) -> list[M.DefinitionChange]:
    """The changes behind one value: nested mappings per key, scalar lists as multisets (reordered
    is no change, gained and lost values are their own items), anything else as a whole. Nothing on
    one side is an add or a removal, not a change to ``null``."""
    if a == b:
        return []
    if a is None:
        return [_item(kind, "added", target, detail, None, b)]
    if b is None:
        return [_item(kind, "removed", target, detail, a, None)]
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[M.DefinitionChange] = []
        for key in sorted(a.keys() | b.keys(), key=_sort_key):
            out += _value(
                kind, target, a.get(key), b.get(key), str(key) if detail is None else f"{detail}.{key}"
            )
        return out
    if isinstance(a, list) and isinstance(b, list) and all(_scalar(v) for v in [*a, *b]):
        out = [
            _item(kind, "added", target, detail, None, v)
            for v in sorted((Counter(b) - Counter(a)).elements(), key=_sort_key)
        ]
        out += [
            _item(kind, "removed", target, detail, v, None)
            for v in sorted((Counter(a) - Counter(b)).elements(), key=_sort_key)
        ]
        return out
    return [_item(kind, "changed", target, detail, a, b)]


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (bool, int, float, str))


def _sort_key(v: Any) -> str:
    return f"{type(v).__name__}:{v}"


def _state_target(section: str, name: str) -> str:
    return name if section == "state" else f"ch<n>.{name}"


def _by_key(entries: Any, key_field: str) -> dict[str, Any]:
    return {e[key_field]: e for e in entries or [] if isinstance(e, dict) and e.get(key_field)}
