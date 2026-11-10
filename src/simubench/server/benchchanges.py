"""What changed between two versions of a bench file, in the bench's language (#874): instruments
and parts added, removed or given another model; wires connected and disconnected; initial
settings, part parameters, addresses, identities and the run settings. Node positions and the
description are left out (they do not change what runs). The items have the same shape as a
definition's changes (:mod:`definitionchanges`), so one interface renders both.
"""

from __future__ import annotations

from typing import Any

from ..models import api as M


def _item(kind: str, action: str, target: str, detail: str | None = None, old: Any = None, new: Any = None):
    return M.DefinitionChange(kind=kind, action=action, target=target, detail=detail, old=old, new=new)  # type: ignore[arg-type]


def _wire_key(w: dict[str, Any]) -> str:
    ends = sorted([str(w.get("from", "")), str(w.get("to", ""))])
    return f"{ends[0]} ↔ {ends[1]}"


def bench_changes(old: dict[str, Any], new: dict[str, Any]) -> list[M.DefinitionChange]:
    out: list[M.DefinitionChange] = []
    for kind, section, model_key in (("instrument", "instruments", "model"), ("part", "parts", "type")):
        a, b = old.get(section) or {}, new.get(section) or {}
        for name in sorted(b.keys() - a.keys()):
            out.append(_item(kind, "added", name, model_key, None, (b[name] or {}).get(model_key)))
        for name in sorted(a.keys() - b.keys()):
            out.append(_item(kind, "removed", name, model_key, (a[name] or {}).get(model_key), None))
        for name in sorted(a.keys() & b.keys()):
            x, y = a[name] or {}, b[name] or {}
            if x.get(model_key) != y.get(model_key):
                out.append(_item(kind, "changed", name, model_key, x.get(model_key), y.get(model_key)))
            for field in ("address", "label", "idn", "serial", "firmware", "dut", "deviation"):
                if x.get(field) != y.get(field):
                    out.append(_item(kind, "changed", name, field, x.get(field), y.get(field)))
            for field, sub_kind in (("state", "state"), ("params", "params")):
                xs, ys = x.get(field) or {}, y.get(field) or {}
                for path in sorted(xs.keys() | ys.keys()):
                    if xs.get(path) != ys.get(path):
                        action = "added" if path not in xs else "removed" if path not in ys else "changed"
                        out.append(
                            _item(sub_kind, action, f"{name}.{path}", None, xs.get(path), ys.get(path))
                        )
    a_wires = {_wire_key(w): w for w in old.get("wires") or []}
    b_wires = {_wire_key(w): w for w in new.get("wires") or []}
    for key in sorted(b_wires.keys() - a_wires.keys()):
        out.append(_item("wire", "added", key))
    for key in sorted(a_wires.keys() - b_wires.keys()):
        out.append(_item("wire", "removed", key))
    for key in sorted(a_wires.keys() & b_wires.keys()):
        if (a_wires[key].get("accessories") or []) != (b_wires[key].get("accessories") or []):
            out.append(_item("wire", "changed", key, "accessories"))
    if old.get("name") != new.get("name") and old:
        out.append(_item("bench", "changed", "name", None, old.get("name"), new.get("name")))
    ra, rb = old.get("run") or {}, new.get("run") or {}
    for key in sorted(ra.keys() | rb.keys()):
        if ra.get(key) != rb.get(key):
            out.append(_item("run", "changed", key, None, ra.get(key), rb.get(key)))
    return out
