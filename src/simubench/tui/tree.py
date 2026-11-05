"""The state tree's rows as data (tui-screens.md 5.6): every state variable of an instrument,
grouped by the first segment of its path, plus — on request — the hidden values.

Pure functions, so the grouping, the whole-group budget and the hidden rows are tested without
a screen. The screen draws what :func:`tree_rows` returns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import controls as C
from .i18n import t


@dataclass(frozen=True)
class Row:
    """One line of the tree: a group header, a state variable, a hidden value, or a note."""

    kind: str  # group | var | hidden | note
    group: str = ""
    path: str | None = None
    label: str = ""
    spec: Mapping[str, Any] | None = None  # var: its StateVarInfo
    text: str = ""  # hidden / note: the whole line
    opened: bool = True  # group: expanded?
    count: int = 0  # group: variables inside


def groups_of(info: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    """The instrument's state variables by first path segment, in the definition's order."""
    out: dict[str, list[Mapping[str, Any]]] = {}
    for sv in info.get("state", []):
        out.setdefault(str(sv["path"]).split(".", 1)[0], []).append(sv)
    return out


def default_open(groups: Mapping[str, Sequence[Any]], rows: int) -> set[str]:
    """Which groups start expanded: whole groups, in order, while their rows fit ``rows`` (the
    #374 rule — never half a group). A group too long for the room stays folded, the next
    smaller one may still fit; the choice depends only on the data and the height."""
    used, opened = 0, set()
    for name, items in groups.items():
        need = 1 + len(items)
        if used + need <= rows or not opened:  # the first group always opens, whatever its size
            opened.add(name)
            used += need
    return opened


def writable(sv: Mapping[str, Any]) -> bool:
    """Edited in place by a field: the settable kinds, as the palette and ``/`` read them."""
    return not sv.get("readonly") and sv.get("type") in ("bool", "enum", "float", "int")


def hidden_rows(
    instrument: str,
    info: Mapping[str, Any],
    faults: Sequence[Mapping[str, Any]],
    revealed: bool,
    lang: str,
) -> list[Row]:
    """What the bench keeps from the user (D9), read only: this unit's individual deviations
    (their values only when ``run.reveal_true_values`` is on) and the values an active
    ``hidden`` fault has overridden. Never written from here — ``hidden`` faults are how
    those change."""
    rows: list[Row] = []
    if not revealed:
        rows.append(Row("note", group="hidden", text=t("tree.notRevealed", lang)))
    for d in info.get("deviations", []):
        value = d.get("value")
        shown = "—" if value is None else _num(value)
        unit = f" {d['unit']}" if d.get("unit") else ""
        rows.append(
            Row(
                "hidden",
                group="hidden",
                path=d["param"],
                text=f"{t('tree.hiddenTag', lang)}  {d['param']}  {shown}{unit}  ±{_num(d.get('limit'))}",
            )
        )
    for f in faults:
        spec = f.get("spec") or {}
        if spec.get("kind") != "hidden" or f.get("status") != "active":
            continue
        if not str(spec.get("component", "")).startswith(f"{instrument}/"):
            continue
        rows.append(
            Row(
                "hidden",
                group="hidden",
                text=t(
                    "tree.hiddenFault",
                    lang,
                    tag=t("tree.hiddenTag", lang),
                    target=f"{spec.get('component')}.{spec.get('param')}",
                    value=_num(spec.get("value")),
                    id=f.get("id", ""),
                ),
            )
        )
    if revealed and not rows:
        rows.append(Row("note", group="hidden", text=t("tree.noHidden", lang)))
    return rows


def _num(value: Any) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def tree_rows(
    groups: Mapping[str, Sequence[Mapping[str, Any]]],
    open_groups: set[str],
    lang: str,
    hidden: Sequence[Row] | None = None,
) -> list[Row]:
    """The flattened tree: each group's header, then its variables when it is expanded; the
    hidden section last, when asked for."""
    out: list[Row] = []
    for name, items in groups.items():
        opened = name in open_groups
        out.append(Row("group", group=name, label=name, opened=opened, count=len(items)))
        if opened:
            for sv in items:
                path = str(sv["path"])
                out.append(
                    Row(
                        "var",
                        group=name,
                        path=path,
                        label=C.label_of(sv, lang, path.rsplit(".", 1)[-1]),
                        spec=sv,
                    )
                )
    if hidden is not None:
        out.append(
            Row(
                "group",
                group="hidden",
                label="hidden",
                opened=True,
                count=sum(r.kind == "hidden" for r in hidden),
            )
        )
        out.extend(hidden)
    return out
