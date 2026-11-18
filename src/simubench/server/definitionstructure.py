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
the same way, by setting and then by field; the commands are one list.

JSON has no infinity (a setting's ``default: -.inf``, a ``special`` keyword's value, a load of
``inf``): the structure spells a non-finite number the one way the interface layer does, ``"INF"``,
``"-INF"`` or ``"NAN"`` (:mod:`simubench.jsonsafe`, D60), and the write back reads those spellings
as the numbers they are. Without that a non-finite value reached the client as ``null`` and came
back as D59's deletion marker, silently dropping it from the definition (#P0, the UXR1104B copy
lost ``display.persistence``'s default and its ``MINimum``/``INFinite`` keywords)."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Literal

from ..jsonsafe import as_number, spelling
from ..library.loader import OVERRIDES, LayerError, apply_command_overrides, layered_definition_data
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


def spelled(value: Any) -> Any:
    """``value`` with every non-finite float spelled ``"INF"``/``"-INF"``/``"NAN"`` (D60), through
    dicts and lists, so it survives JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return spelling(value)
    if isinstance(value, dict):
        return {k: spelled(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [spelled(v) for v in value]
    return value


def _unspelled(value: Any) -> Any:
    """The reverse of :func:`spelled`: ``"INF"``/``"-INF"``/``"NAN"`` back as floats."""
    if isinstance(value, str):
        return as_number(value)
    if isinstance(value, dict):
        return {k: _unspelled(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unspelled(v) for v in value]
    return value


#: The fields of a numeric setting that hold numbers (``special`` maps keywords to numbers).
_NUMBER_FIELDS = ("min", "max", "default", "step", "special")


def _numbers_read(settings: dict[str, Any]) -> dict[str, Any]:
    """Settings as the client sent them, with the spelled infinities of a numeric setting's number
    fields read back as floats; text settings keep a value that happens to read ``INF``."""
    out = {}
    for name, spec in settings.items():
        if isinstance(spec, dict) and spec.get("type", "float") in ("float", "int"):
            spec = {k: _unspelled(v) if k in _NUMBER_FIELDS else v for k, v in spec.items()}
        out[name] = spec
    return out


def layered(data: dict[str, Any], source: Path) -> dict[str, Any]:
    """``data`` as it runs: layered over what it extends (the file alone when that is broken)."""
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
            texts = {k: item[k] for k in ("label", "description") if isinstance(item.get(k), str)}
            out.append(
                M.StructureComponent(name=item["name"], type=item["type"], params=dict(params or {}), **texts)
            )
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
    rest = {k: v for k, v in data.items() if k not in STRUCTURE_KEYS and k != OVERRIDES}
    if "extends" not in rest:
        return {}
    return layered(rest, source)


def structure(doc: M.DefinitionDocument, source: Path) -> M.DefinitionStructure:
    """The effective structure of a definition document read from ``source``."""
    effective = layered(doc.data, source)
    inherited = _inherited_data(doc.data, source)
    below = _structure_of(inherited)
    empty = _structure_of({})
    from_parent: list[StructureKey] = [
        key for key in STRUCTURE_KEYS if key not in doc.data and below[key] != empty[key]
    ]
    channels = _mapping(effective.get("channels"))
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
        **_structure_of(spelled(effective)),
    )


def _as_json(value: Any) -> Any:
    """``value`` as JSON carries it, non-finite numbers spelled (D60): an infinity the client
    sends back as ``"INF"`` compares equal to the ``inf`` the definition holds."""
    return json.loads(json.dumps(spelled(value), default=str))


def _restore(new: Any, old: Any) -> Any:
    """``new`` as the client sent it, with what it sent back unchanged taken from ``old`` as the
    definition holds it: JSON turns a table's number keys (``{0.001: 100}``) into strings, which
    would change what the table means. A key keeps its old type even where its value changed, and
    a key added to a table whose keys are all numbers becomes a number too (#1214)."""
    if _as_json(new) == _as_json(old):
        return copy.deepcopy(old)
    if isinstance(new, dict) and isinstance(old, dict):
        keys = {str(k): k for k in old}
        numeric = bool(old) and all(isinstance(k, int | float) and not isinstance(k, bool) for k in old)
        return {
            keys.get(k, _number_key(k) if numeric else k): _restore(v, old[keys[k]]) if k in keys else v
            for k, v in new.items()
        }
    if isinstance(new, list) and isinstance(old, list) and len(new) == len(old):
        return [_restore(a, b) for a, b in zip(new, old, strict=True)]
    return new


def _number_key(key: Any) -> Any:
    """A table key typed in the Web as text: the number it spells, else the text."""
    if not isinstance(key, str):
        return key
    try:
        return int(key)
    except ValueError:
        try:
            return float(key)
        except ValueError:
            return key


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
        {
            "name": c.name,
            "type": c.type,
            **{k: v for k, v in (("label", c.label), ("description", c.description)) if v},
            **({"params": dict(c.params)} if c.params else {}),
        }
        for c in items
    ]


def written(data: dict[str, Any], new: M.StructureUpdate, source: Path) -> dict[str, Any]:
    """The definition file's data with ``new`` as its structure: only what differs from what it
    inherits is written (the module docstring has the rules); every other key is kept as it is."""
    out = copy.deepcopy(data)
    below = _inherited_data(data, source)
    now = layered(data, source)
    for key in ("components", "channel_components"):
        items = _restore_items(
            [
                {**c, "params": _unspelled(c["params"])} if "params" in c else c
                for c in _component_data(getattr(new, key))
            ],
            _commands(now.get(key)),
            lambda c: (c.get("name"), c.get("type")),
        )
        _set_list(out, key, items, _commands(below.get(key)))
    commands = _restore_items(
        list(new.commands),
        _commands(now.get("commands")),
        lambda c: (c.get("header"), c.get("state"), c.get("op")),
    )
    _set_commands(out, commands, _commands(below.get("commands")))
    for key in _SETTINGS:
        settings = _restore(_numbers_read(getattr(new, key)), _settings(now.get(key)))
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


class StructureNotKept(ValueError):
    """The file the write back composed would not run the structure the client sent: something
    would be lost or changed on the way (a value JSON cannot carry, a layering rule). Nothing is
    saved; the message names the items (#P0)."""


def _comparable(value: Any) -> Any:
    """A structure item as JSON carries it, empty values left out on both sides: the write back
    omits an empty field (``params: {}``, ``options: []``) and the read fills it in again."""
    value = _as_json(_plain(value))
    if isinstance(value, dict):
        out = {k: _comparable(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, {}, [])}
    if isinstance(value, list):
        return [_comparable(v) for v in value]
    return value


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def _null_paths(new: M.StructureUpdate) -> list[str]:
    """Where the client's structure holds a ``null`` value: inside a setting, a component's
    parameters or a command. The structure is complete — a removed field is simply not sent, and
    the server writes D59's deletion marker itself — so a ``null`` there can only be a value the
    JSON did not carry (#P0: infinities arrived as null and were saved as deletions)."""
    out: list[str] = []

    def walk(value: Any, where: str) -> None:
        if value is None:
            out.append(where)
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{where}/{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{where}/{i}")

    for key in _SETTINGS:
        walk(getattr(new, key), key)
    for key in _LISTS:
        for c in getattr(new, key):
            walk(c.params, f"{key}/{c.name}/params")
    for i, cmd in enumerate(new.commands):
        walk(cmd, f"commands/{i}")
    return out


def check_kept(data: dict[str, Any], new: M.StructureUpdate, source: Path) -> None:
    """The write back's own invariant: ``data`` (the file about to be saved), read the way every
    reader reads it — layered over what it extends — runs exactly the structure ``new`` asked for.
    Raises :class:`StructureNotKept` naming the items that would come out different."""
    nulls = _null_paths(new)
    if nulls:
        raise StructureNotKept(
            '没有保存：结构里不能有 null（JSON 带不了的值，如无穷大，要写成 "INF"/"-INF"；'
            "删除一项就不发送它）：" + "、".join(nulls[:5])
        )
    effective = _structure_of(spelled(layered(data, source)))
    wanted = {
        key: _comparable(getattr(new, key))
        if key not in _SETTINGS
        else _comparable(_numbers_read(getattr(new, key)))
        for key in STRUCTURE_KEYS
    }
    wrong = []
    for key in STRUCTURE_KEYS:
        got, want = _comparable(effective[key]), wanted[key]
        if got != want:
            names = (
                sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))
                if isinstance(got, dict) and isinstance(want, dict)
                else []
            )
            wrong.append(f"{key}" + (f"（{', '.join(map(str, names[:5]))}）" if names else ""))
    if wrong:
        raise StructureNotKept(
            "没有保存：写进文件后，这些项与要保存的不一致，保存会改变或丢失它们：" + "；".join(wrong)
        )


def _overrides(commands: list[dict[str, Any]], below: list[dict[str, Any]]) -> dict[str, Any] | None:
    """``commands`` as ``command_overrides`` over the inherited ``below`` (D59): a changed or new
    command by its header, a dropped one as null. None when that cannot say it — a header twice
    on either side, or an order the overrides do not produce (only appending is)."""
    headers = [c.get("header") for c in commands]
    if None in headers or len(set(headers)) != len(headers):
        return None
    old = {str(c.get("header")): c for c in below}
    out: dict[str, Any] = {}
    for c in commands:
        h = c["header"]
        if h not in old or _as_json(old[h]) != _as_json(c):
            out[h] = {k: v for k, v in c.items() if k != "header"}
    kept = set(headers)
    for h in old:
        if h not in kept:
            out[h] = None
    try:
        if _as_json(apply_command_overrides(below, out)) != _as_json(commands):
            return None
    except LayerError:
        return None
    return out


def _set_commands(out: dict[str, Any], commands: list[dict[str, Any]], below: list[dict[str, Any]]) -> None:
    """The commands into the file: a file that writes its own list keeps doing so (D59: a list
    replaces); a copy that inherits its table gets only the commands it changes, as
    ``command_overrides``, so one command added in the Web is one entry and later fixes of the
    parent's other commands still reach it."""
    if "commands" not in out and below:
        overrides = _overrides(commands, below)
        if overrides is not None:
            if overrides:
                out[OVERRIDES] = overrides
            else:
                out.pop(OVERRIDES, None)
            return
    out.pop(OVERRIDES, None)
    _set_list(out, "commands", commands, below)


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
