"""One value in one place (#1426, D82): ``{same_as: <path>}`` anywhere in a definition stands for
the value at ``path`` in the same definition, resolved when the definition loads.

A data-sheet figure such as the bandwidth or the top sample rate is needed in several places — a
component parameter, a setting's ``max``/``default``/``special``, a command's ``MAX`` keyword, the
identity a ``DEF`` keyword restores. Written as literals they drift apart: a definition that
``extends`` a model and changes the figure in one place keeps the parent's number in the others,
and nothing reports it. Written as ``{same_as: specs.bandwidth_hz}`` they all follow the one value,
including the value a child definition sets.

The path is the keys from the top of the definition joined by dots: ``specs.sample_rate``,
``model``, ``state.acquire.bandwidth.max``. A key that itself has dots (a setting name) is matched
whole — at each level the longest run of the remaining parts that is a key wins — and a list item
is its index (``specs.memory_depth_options_pts.1``) or, in a list of named items such as the
components, its ``name`` (``components.adc.params.codes_per_div``: it stays right when a layer
inserts or removes a component before it, #1441). The value may itself be a ``same_as``; a ring
of them is refused. Resolution runs on the layered data (after ``extends``, the class default and
the override layers), so a reference reaches what the child wrote. The ``template`` record is left
as written. Only the runtime ``$setting`` references stay references: ``same_as`` is gone once
the definition is loaded."""

from __future__ import annotations

from typing import Any

SAME_AS = "same_as"

#: Top-level keys whose content is a record, not definition data: left as written.
_LEFT_AS_WRITTEN = ("template",)


class SameAsError(ValueError):
    """A ``same_as`` that names nothing, names a ring, or is not alone in its mapping
    (E-DEF-SAME-AS). ``code`` and ``pointer`` say where, as the loader's layering errors do."""

    def __init__(self, message: str, pointer: str) -> None:
        super().__init__(message)
        self.code = "E-DEF-SAME-AS"
        self.pointer = pointer


def _ptr(parts: list[str]) -> str:
    return "".join("/" + p.replace("~", "~0").replace("/", "~1") for p in parts)


def _lookup(data: Any, path: str) -> tuple[Any, list[str]]:
    """The value at ``path`` and the keys that reach it; KeyError when nothing is there."""
    parts = path.split(".") if path else []
    keys: list[str] = []
    node = data
    i = 0
    while i < len(parts):
        if isinstance(node, dict):
            for j in range(len(parts), i, -1):  # the longest run that is a key: "acquire.srate"
                key = ".".join(parts[i:j])
                if key in node:
                    node, i = node[key], j
                    keys.append(key)
                    break
            else:
                raise KeyError(path)
        elif isinstance(node, list) and parts[i].isdigit() and int(parts[i]) < len(node):
            node = node[int(parts[i])]
            keys.append(parts[i])
            i += 1
        elif isinstance(node, list):  # a component by its name: "components.adc" (#1441)
            for j in range(len(parts), i, -1):
                name = ".".join(parts[i:j])
                at = [k for k, item in enumerate(node) if isinstance(item, dict) and item.get("name") == name]
                if len(at) == 1:
                    node, i = node[at[0]], j
                    keys.append(str(at[0]))
                    break
            else:
                raise KeyError(path)
        else:
            raise KeyError(path)
    if not keys:
        raise KeyError(path)
    return node, keys


def resolve_same_as(data: dict) -> dict:
    """``data`` with every ``{same_as: path}`` replaced by the value it names (a copy where anything
    changed; ``data`` itself is not changed). Raises :class:`SameAsError`."""

    def target(path: Any, where: list[str], ring: tuple[str, ...]) -> Any:
        if not isinstance(path, str) or not path:
            raise SameAsError("same_as 的值应是定义里的路径，如 specs.bandwidth_hz", _ptr(where))
        if path in ring:
            raise SameAsError(f"same_as 成环：{' → '.join((*ring, path))}", _ptr(where))
        try:
            value, keys = _lookup(data, path)
        except KeyError:
            raise SameAsError(f"same_as 指向的 {path} 在定义里不存在", _ptr(where)) from None
        return walk(value, keys, (*ring, path))

    def walk(value: Any, where: list[str], ring: tuple[str, ...] = ()) -> Any:
        if isinstance(value, dict):
            if SAME_AS in value:
                if len(value) != 1:
                    others = ", ".join(sorted(k for k in value if k != SAME_AS))
                    raise SameAsError(
                        f"same_as 要单独写，不能与其他键（{others}）同在一个映射里", _ptr(where)
                    )
                return target(value[SAME_AS], where, ring)
            out = {k: walk(v, [*where, str(k)], ring) for k, v in value.items()}
            return value if all(out[k] is value[k] for k in value) else out
        if isinstance(value, list):
            items = [walk(v, [*where, str(i)], ring) for i, v in enumerate(value)]
            return value if all(a is b for a, b in zip(items, value, strict=True)) else items
        return value

    out = {k: v if k in _LEFT_AS_WRITTEN else walk(v, [k]) for k, v in data.items()}
    return data if all(out[k] is data[k] for k in data) else out
