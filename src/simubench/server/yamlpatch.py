"""Write an edited bench back into its own YAML text (#1254, D73).

A bench file is the user's asset (D65): its comments say why a value was chosen, its number spelling
(``1.0e6``), key order and explicitly written defaults (``run.clock: command``) are the author's. An
edit from the UI, MCP or the API changes the bench's content; this module carries only that change
into the file. The file is loaded as a round-trip document (``ruamel.yaml``), the content before and
after the edit are compared in their canonical form (``edit.to_yaml_data``), and only the keys and
list items that differ are written; everything else is the original text, byte for byte. A team that
keeps benches in Git sees an added wire as a few added lines.
"""

from __future__ import annotations

import io
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

#: The order of a bench file's top-level sections, for placing a section the edit adds.
ORDER = (
    "schema",
    "name",
    "description",
    "instruments",
    "parts",
    "wires",
    "run",
    "faults",
    "assets",
    "layout",
)


def _sequence_offset(text: str) -> int:
    """How far the file indents a list's dash past its key (``wires:\\n  - …`` is 2, ``- …`` is 0)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.lstrip(" ")
        if not stripped.startswith("- "):
            continue
        for parent in reversed(lines[:i]):
            if parent.strip() and not parent.lstrip().startswith("#"):
                return max(0, (len(line) - len(stripped)) - (len(parent) - len(parent.lstrip(" "))))
    return 2


def _yaml(text: str) -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # never re-fold a line the author wrote
    offset = _sequence_offset(text)
    y.indent(mapping=2, sequence=offset + 2, offset=offset)
    return y


def patched(raw: bytes, old: dict[str, Any], new: dict[str, Any]) -> bytes | None:
    """``raw`` with the change from ``old`` to ``new`` (both canonical bench data) applied, or None
    when the text is not a YAML mapping this can patch."""
    text = raw.decode("utf-8")
    y = _yaml(text)
    doc = y.load(text)
    if not isinstance(doc, CommentedMap):
        return None
    _merge_map(doc, old, new, top=True)
    out = io.StringIO()
    y.dump(doc, out)
    return out.getvalue().encode("utf-8")


def _merge_map(node: CommentedMap, old: dict[str, Any], new: dict[str, Any], *, top: bool = False) -> None:
    _rename_in_place(node, old, new)
    for key in list(old):
        if key not in new and key in node:
            del node[key]
    for key, value in new.items():
        before = old.get(key, _MISSING)
        if before == value and key in node:
            continue  # unchanged: the author's text stays
        current = node.get(key, _MISSING)
        if isinstance(current, CommentedMap) and isinstance(value, dict) and isinstance(before, dict):
            _merge_map(current, before, value)
        elif isinstance(current, CommentedSeq) and isinstance(value, list) and isinstance(before, list):
            _merge_seq(current, before, value)
        elif key in node:
            node[key] = (
                _scalar_like(value, current) if not isinstance(value, (dict, list)) else _node(value, current)
            )
        else:
            created = _node(value, _sibling(node))
            if top and key in ORDER:
                later = [k for k in ORDER[ORDER.index(key) + 1 :] if k in node]
                position = list(node).index(later[0]) if later else len(node)
                node.insert(position, key, created)
            else:
                node[key] = created


def _rename_in_place(node: CommentedMap, old: dict[str, Any], new: dict[str, Any]) -> None:
    """A key that left and a key that came with the same value is a rename (an instrument renamed):
    the entry keeps its place, its comments and its text under the new key."""
    gone = [k for k in old if k not in new and k in node]
    came = [k for k in new if k not in old and k not in node]
    for k in came:
        match = next((g for g in gone if old[g] == new[k]), None)
        if match is None:
            continue
        gone.remove(match)
        position = list(node).index(match)
        comment = node.ca.items.pop(match, None)
        value = node.pop(match)
        node.insert(position, k, value)
        if comment is not None:
            node.ca.items[k] = comment
        old[k] = old.pop(match)  # from here on the entry is unchanged under its new key


def _merge_seq(seq: CommentedSeq, old: list[Any], new: list[Any]) -> None:
    """The new list from the old one's items where they are unchanged (their comments move with
    them), a changed mapping at the same place merged, the rest written new."""
    used: set[int] = set()
    items: list[Any] = []
    origin: list[int | None] = []
    for i, value in enumerate(new):
        same = next((j for j, o in enumerate(old) if j not in used and j < len(seq) and o == value), None)
        if same is not None:
            used.add(same)
            items.append(seq[same])
            origin.append(same)
            continue
        if (
            i < len(old)
            and i < len(seq)
            and i not in used
            and isinstance(seq[i], CommentedMap)
            and isinstance(value, dict)
            and isinstance(old[i], dict)
        ):
            used.add(i)
            _merge_map(seq[i], old[i], value)
            items.append(seq[i])
            origin.append(i)
            continue
        items.append(_node(value, seq[0] if len(seq) else None))
        origin.append(None)
    if len(items) == len(seq) and all(a is b for a, b in zip(items, seq, strict=True)):
        return
    comments = {
        new_i: seq.ca.items[o] for new_i, o in enumerate(origin) if o is not None and o in seq.ca.items
    }
    seq[:] = items
    seq.ca.items.clear()
    seq.ca.items.update(comments)


_MISSING = object()


def _sibling(node: CommentedMap) -> Any:
    """A value already in ``node`` whose style (flow or block, integer coordinates) a new one follows."""
    return next((v for v in node.values() if isinstance(v, (CommentedMap, CommentedSeq))), None)


def _scalar_like(value: Any, current: Any) -> Any:
    """A whole number written where the file had an integer stays an integer (a layout coordinate)."""
    if (
        isinstance(value, float)
        and value.is_integer()
        and isinstance(current, int)
        and not isinstance(current, bool)
    ):
        return int(value)
    return value


def _node(value: Any, like: Any) -> Any:
    """A new value as a round-trip node, in the style of ``like`` (a sibling the file already has)."""
    if isinstance(value, dict):
        out = CommentedMap()
        for k, v in value.items():
            sample = like.get(k) if isinstance(like, CommentedMap) else None
            out[k] = _node(v, sample)
        if isinstance(like, CommentedMap) and like.fa.flow_style():
            out.fa.set_flow_style()
        return out
    if isinstance(value, list):
        out_seq = CommentedSeq(
            _node(v, like[0] if isinstance(like, CommentedSeq) and len(like) else None) for v in value
        )
        if isinstance(like, CommentedSeq) and like.fa.flow_style():
            out_seq.fa.set_flow_style()
        return out_seq
    return _scalar_like(value, like)
