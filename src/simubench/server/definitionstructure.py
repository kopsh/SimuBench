"""The structure of an instrument definition as the Web's build area edits it (#1201,
instrument-authoring.md 9): components, channel components, signal wiring and panel ports — the
effective ones, with ``extends`` and the category default merged in (D59) — and the write back of
a whole new structure into the definition file.

The server owns the layering: the client sends the structure it wants to run, and the file gets
only what differs from what it inherits. A list (components, a wiring list) that equals the
inherited one is left out of the file, so it stays inherited; a different one replaces it whole
(D59: lists replace). A panel-port table is a mapping, merged key by key, so a port the parent
brings and the new structure drops is written as ``null`` (D59's deletion marker)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Literal

from ..library.loader import layered_definition_data
from ..models import api as M

StructureKey = Literal["components", "channel_components", "wiring", "ports"]
#: The top-level keys of a definition that make up its structure.
STRUCTURE_KEYS: tuple[StructureKey, ...] = ("components", "channel_components", "wiring", "ports")
_LISTS = ("components", "channel_components")
_WIRES = ("instrument", "channel")
_PORTS = ("instrument", "channel")


def _layered(data: dict[str, Any], source: Path) -> dict[str, Any]:
    try:
        return layered_definition_data(copy.deepcopy(data), source)
    except ValueError:  # a broken extends: the file alone (its diagnostics say why)
        return copy.deepcopy(data)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _components(value: Any) -> list[M.StructureComponent]:
    out = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("type"), str):
            params = item.get("params")
            out.append(M.StructureComponent(name=item["name"], type=item["type"], params=dict(params or {})))
    return out


def _wires(value: Any) -> list[tuple[str, str]]:
    out = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(x, str) for x in item):
            out.append((item[0], item[1]))
    return out


def _ports(value: Any) -> dict[str, str]:
    return {k: v for k, v in (value or {}).items() if isinstance(v, str)} if isinstance(value, dict) else {}


def _structure_of(data: dict[str, Any]) -> dict[str, Any]:
    """The four structure items of layered data, normalised to the shapes the API carries."""
    wiring = _mapping(data.get("wiring"))
    ports = _mapping(data.get("ports"))
    return {
        "components": _components(data.get("components")),
        "channel_components": _components(data.get("channel_components")),
        "wiring": M.StructureWiring(**{k: _wires(wiring.get(k)) for k in _WIRES}),
        "ports": M.StructurePorts(**{k: _ports(ports.get(k)) for k in _PORTS}),
    }


def _inherited_data(data: dict[str, Any], source: Path) -> dict[str, Any]:
    """What the definition would run without its own structure items: the layers below it."""
    rest = {k: v for k, v in data.items() if k not in STRUCTURE_KEYS}
    if "extends" not in rest:
        return {}
    return _layered(rest, source)


def structure(doc: M.DefinitionDocument, source: Path) -> M.DefinitionStructure:
    """The effective structure of a definition document read from ``source``."""
    layered = _layered(doc.data, source)
    inherited = _inherited_data(doc.data, source)
    below = _structure_of(inherited)
    empty = _structure_of({})
    from_parent: list[StructureKey] = [
        key for key in STRUCTURE_KEYS if key not in doc.data and below[key] != empty[key]
    ]
    channels = _mapping(layered.get("channels"))
    count = channels.get("count", 1)
    return M.DefinitionStructure(
        id=doc.id,
        sha256=doc.sha256,
        editable=doc.editable,
        extends=doc.data.get("extends") if isinstance(doc.data.get("extends"), str) else None,
        channels=count if isinstance(count, int) and count > 0 else 1,
        inherited=from_parent,
        ok=doc.ok,
        diagnostics=doc.diagnostics,
        **_structure_of(layered),
    )


def _component_data(items: list[M.StructureComponent]) -> list[dict[str, Any]]:
    return [
        {"name": c.name, "type": c.type, **({"params": dict(c.params)} if c.params else {})} for c in items
    ]


def _as_json(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _restored(
    items: list[M.StructureComponent], before: list[M.StructureComponent]
) -> list[M.StructureComponent]:
    """``items`` with every parameter the client sent back unchanged replaced by the value as the
    definition holds it: JSON turns a table's number keys (``{0.001: 100}``) into strings, which
    would change what the table means."""
    old = {(c.name, c.type): c.params for c in before}
    out = []
    for c in items:
        params = old.get((c.name, c.type), {})
        out.append(
            c.model_copy(
                update={
                    "params": {
                        k: params[k] if k in params and _as_json(params[k]) == _as_json(v) else v
                        for k, v in c.params.items()
                    }
                }
            )
        )
    return out


def written(data: dict[str, Any], new: M.StructureUpdate, source: Path) -> dict[str, Any]:
    """The definition file's data with ``new`` as its structure: only what differs from what it
    inherits is written (the module docstring has the rules); every other key is kept as it is."""
    out = copy.deepcopy(data)
    below = _structure_of(_inherited_data(data, source))
    now = _structure_of(_layered(data, source))
    for key in _LISTS:
        items = _restored(getattr(new, key), now[key])
        if items == below[key]:
            out.pop(key, None)
        else:
            out[key] = _component_data(items)
    wiring = dict(out.get("wiring") or {}) if isinstance(out.get("wiring"), dict) else {}
    for key in _WIRES:
        wires = getattr(new.wiring, key)
        if wires == getattr(below["wiring"], key):
            wiring.pop(key, None)
        else:
            wiring[key] = [list(w) for w in wires]
    _set_or_drop(out, "wiring", wiring)
    ports = dict(out.get("ports") or {}) if isinstance(out.get("ports"), dict) else {}
    for key in _PORTS:
        wanted = getattr(new.ports, key)
        inherited = getattr(below["ports"], key)
        table = {name: target for name, target in wanted.items() if inherited.get(name) != target}
        table.update({name: None for name in inherited if name not in wanted})
        if table:
            ports[key] = table
        else:
            ports.pop(key, None)
    _set_or_drop(out, "ports", ports)
    return out


def _set_or_drop(out: dict[str, Any], key: str, value: dict[str, Any]) -> None:
    if value:
        out[key] = value
    else:
        out.pop(key, None)
