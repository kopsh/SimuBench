"""The structure of an instrument definition as the Web's build area edits it (#1201,
instrument-authoring.md 9): components, channel components, signal wiring and panel ports — the
effective ones, with ``extends`` and the category default merged in (D59) — and the write back of
a whole new structure into the definition file. Since #1206 the structure also carries the
settings (``state``, ``channel_state``) and the commands, so the build area can declare a setting,
bind a parameter to it and give it a command.

The server owns the layering: the client sends the structure it wants to run, and the file gets
only what differs from what it inherits. A list (components, a wiring list) that equals the
inherited one is left out of the file, so it stays inherited; a different one replaces it whole
(D59: lists replace). A panel-port table is a mapping, merged key by key, so a port the parent
brings and the new structure drops is written as ``null`` (D59's deletion marker). Settings merge
the same way, by setting and then by field; the commands are one list."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Literal

from ..library.loader import layered_definition_data
from ..models import api as M

StructureKey = Literal[
    "components", "channel_components", "wiring", "ports", "state", "channel_state", "commands"
]
#: The top-level keys of a definition that make up its structure.
STRUCTURE_KEYS: tuple[StructureKey, ...] = (
    "components",
    "channel_components",
    "wiring",
    "ports",
    "state",
    "channel_state",
    "commands",
)
_SETTINGS = ("state", "channel_state")
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


def _settings(value: Any) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in _mapping(value).items() if isinstance(k, str) and isinstance(v, dict)}


def _commands(value: Any) -> list[dict[str, Any]]:
    return [c for c in value if isinstance(c, dict)] if isinstance(value, list) else []


def _structure_of(data: dict[str, Any]) -> dict[str, Any]:
    """The structure items of layered data, normalised to the shapes the API carries."""
    wiring = _mapping(data.get("wiring"))
    ports = _mapping(data.get("ports"))
    return {
        "state": _settings(data.get("state")),
        "channel_state": _settings(data.get("channel_state")),
        "commands": _commands(data.get("commands")),
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


def _restore(new: Any, old: Any) -> Any:
    """``new`` as the client sent it, with what it sent back unchanged taken from ``old`` as the
    definition holds it: JSON turns a table's number keys (``{0.001: 100}``) into strings, which
    would change what the table means. A key keeps its old type even where its value changed."""
    if _as_json(new) == _as_json(old):
        return copy.deepcopy(old)
    if isinstance(new, dict) and isinstance(old, dict):
        keys = {str(k): k for k in old}
        return {keys.get(k, k): _restore(v, old[keys[k]]) if k in keys else v for k, v in new.items()}
    if isinstance(new, list) and isinstance(old, list) and len(new) == len(old):
        return [_restore(a, b) for a, b in zip(new, old, strict=True)]
    return new


def _restore_items(new: list[Any], old: list[Any], key: Any) -> list[Any]:
    """Each item restored against the old item with the same ``key`` (a component's name and
    type, a command's header): items keep their identity when others are added or removed."""
    by_key = {key(o): o for o in old}
    return [_restore(item, by_key[key(item)]) if key(item) in by_key else item for item in new]


def _diff(new: dict[str, Any], below: dict[str, Any]) -> dict[str, Any]:
    """What a mapping must write over ``below`` to merge into ``new`` (D59: dicts merge key by
    key, deeper dicts recurse, anything else replaces; ``null`` drops a key ``below`` brings)."""
    out: dict[str, Any] = {}
    for k, v in new.items():
        if k not in below:
            if v not in ({}, []):
                out[k] = v
        elif _as_json(v) != _as_json(below[k]):
            out[k] = _diff(v, below[k]) if isinstance(v, dict) and isinstance(below[k], dict) else v
    out.update({k: None for k in below if k not in new})
    return out


def _component_data(items: list[M.StructureComponent]) -> list[dict[str, Any]]:
    return [
        {"name": c.name, "type": c.type, **({"params": dict(c.params)} if c.params else {})} for c in items
    ]


def written(data: dict[str, Any], new: M.StructureUpdate, source: Path) -> dict[str, Any]:
    """The definition file's data with ``new`` as its structure: only what differs from what it
    inherits is written (the module docstring has the rules); every other key is kept as it is."""
    out = copy.deepcopy(data)
    below = _inherited_data(data, source)
    now = _layered(data, source)
    for key in ("components", "channel_components"):
        items = _restore_items(
            _component_data(getattr(new, key)),
            _commands(now.get(key)),
            lambda c: (c.get("name"), c.get("type")),
        )
        _set_list(out, key, items, _commands(below.get(key)))
    commands = _restore_items(
        list(new.commands),
        _commands(now.get("commands")),
        lambda c: (c.get("header"), c.get("state"), c.get("op")),
    )
    _set_list(out, "commands", commands, _commands(below.get("commands")))
    for key in _SETTINGS:
        settings = _restore(getattr(new, key), _settings(now.get(key)))
        _set_or_drop(out, key, _diff(settings, _settings(below.get(key))))
    for key, values in (
        ("wiring", {k: [list(w) for w in getattr(new.wiring, k)] for k in _WIRES}),
        ("ports", {k: dict(getattr(new.ports, k)) for k in _PORTS}),
    ):
        own = dict(_mapping(out.get(key)))
        inherited = _mapping(below.get(key))
        for sub, value in values.items():
            change = (
                _diff(value, _mapping(inherited.get(sub)))
                if isinstance(value, dict)
                else (None if _as_json(value) == _as_json(inherited.get(sub, [])) else value)
            )
            if change:
                own[sub] = change
            else:
                own.pop(sub, None)
        _set_or_drop(out, key, own)
    return out


def _set_list(out: dict[str, Any], key: str, items: list[Any], below: list[Any]) -> None:
    if _as_json(items) == _as_json(below):
        out.pop(key, None)
    else:
        out[key] = items


def _set_or_drop(out: dict[str, Any], key: str, value: dict[str, Any]) -> None:
    if value:
        out[key] = value
    else:
        out.pop(key, None)
