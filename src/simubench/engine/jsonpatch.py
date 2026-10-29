"""JSON Patch (RFC 6902) for editing instrument definitions (instrument-authoring.md 5.4).

A definition is a plain document (no domain operations as for benches, D25), so edits are the
standard patch operations on JSON Pointer paths: ``add`` (``-`` appends to a list), ``remove``,
``replace``, ``move``, ``copy`` and ``test``. A patch applies whole or not at all.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PatchError(ValueError):
    pass


class PatchOp(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    op: Literal["add", "remove", "replace", "move", "copy", "test"]
    path: str = Field(description="JSON Pointer, e.g. /commands/3/header or /channel_state/scale/max")
    value: Any = Field(None, description="For add, replace and test")
    from_: str | None = Field(None, alias="from", description="Source pointer, for move and copy")


def _tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchError(f"{pointer!r}: a JSON Pointer starts with '/'")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def _index(container: list[Any], token: str, pointer: str, *, append: bool = False) -> int:
    if append and token == "-":
        return len(container)
    if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        raise PatchError(f"{pointer}: {token!r} is not a list index")
    i = int(token)
    if i > len(container) or (i == len(container) and not append):
        raise PatchError(f"{pointer}: index {i} is beyond the list ({len(container)} items)")
    return i


def _parent(doc: Any, pointer: str) -> tuple[Any, str]:
    tokens = _tokens(pointer)
    if not tokens:
        raise PatchError("the whole document cannot be the target; patch its members")
    node = doc
    for t in tokens[:-1]:
        if isinstance(node, dict):
            if t not in node:
                raise PatchError(f"{pointer}: no member {t!r}")
            node = node[t]
        elif isinstance(node, list):
            node = node[_index(node, t, pointer)]
        else:
            raise PatchError(f"{pointer}: {t!r} is inside a value, not an object or a list")
    return node, tokens[-1]


def _get(doc: Any, pointer: str) -> Any:
    node = doc
    for t in _tokens(pointer):
        if isinstance(node, dict) and t in node:
            node = node[t]
        elif isinstance(node, list):
            node = node[_index(node, t, pointer)]
        else:
            raise PatchError(f"{pointer}: nothing there")
    return node


def _add(doc: Any, pointer: str, value: Any) -> None:
    parent, last = _parent(doc, pointer)
    if isinstance(parent, dict):
        parent[last] = value
    elif isinstance(parent, list):
        parent.insert(_index(parent, last, pointer, append=True), value)
    else:
        raise PatchError(f"{pointer}: the parent is a value")


def _remove(doc: Any, pointer: str) -> Any:
    parent, last = _parent(doc, pointer)
    if isinstance(parent, dict):
        if last not in parent:
            raise PatchError(f"{pointer}: nothing to remove")
        return parent.pop(last)
    if isinstance(parent, list):
        return parent.pop(_index(parent, last, pointer))
    raise PatchError(f"{pointer}: the parent is a value")


def apply(doc: dict[str, Any], ops: list[PatchOp]) -> dict[str, Any]:
    """The document with every operation applied, in order; ``doc`` is not changed."""
    out = copy.deepcopy(doc)
    for k, op in enumerate(ops):
        try:
            if op.op == "add":
                _add(out, op.path, copy.deepcopy(op.value))
            elif op.op == "remove":
                _remove(out, op.path)
            elif op.op == "replace":
                _remove(out, op.path)
                _add(out, op.path, copy.deepcopy(op.value))
            elif op.op in ("move", "copy"):
                if op.from_ is None:
                    raise PatchError(f"{op.op} needs 'from'")
                if op.op == "move" and (op.path + "/").startswith(op.from_ + "/"):
                    raise PatchError(f"cannot move {op.from_} into itself")
                value = _remove(out, op.from_) if op.op == "move" else copy.deepcopy(_get(out, op.from_))
                _add(out, op.path, value)
            elif _get(out, op.path) != op.value:  # test
                raise PatchError(f"{op.path}: test failed, the value is {_get(out, op.path)!r}")
        except PatchError as exc:
            raise PatchError(f"operation {k} ({op.op} {op.path}): {exc}") from None
    return out
