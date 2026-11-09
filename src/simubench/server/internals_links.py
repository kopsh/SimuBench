"""Which commands drive a component and which readings it influences (#739), for the internal view.

Pure functions over the loaded definition (macros are already expanded there) and the live
component graph; nothing here changes the session.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..models import api as M
from ..models.definition import CommandSpec

if TYPE_CHECKING:
    from ..engine.instrument import Instrument

_CH = re.compile(r"^ch\d+\.")
_BRACES = re.compile(r"\{[^}]*\}")


def norm(name: str) -> str:
    """A channel component or setting name without its channel number: ``ch2.front`` -> ``ch.front``
    (a definition writes commands and bindings against ``ch.``)."""
    return _CH.sub("ch.", name)


def _setting_matcher(path: str) -> re.Pattern[str]:
    """A command's setting path as a pattern: ``measure.p{slot}.kind`` matches any slot."""
    parts = _BRACES.split(norm(path))
    return re.compile(".+".join(re.escape(p) for p in parts) + "$")


def command_settings(cmd: CommandSpec) -> list[str]:
    """Every setting path a command writes or reads, in the order it names them."""
    out: list[str] = []
    if cmd.state:
        out.append(cmd.state)
    for p in cmd.params:
        if p.state:
            out.append(p.state)
        out += [v for v in (p.keys or {}).values() if v]
    out += list(cmd.writes)
    return out


def _op_component(op: str) -> str:
    return norm(op.rpartition(".")[0])


def drivers(commands: Iterable[CommandSpec], comp_name: str, settings: list[str]) -> list[M.InternalCommand]:
    """The commands that write one of ``settings`` (the component's bound settings) or run an
    operation of ``comp_name``."""
    wanted = {norm(s) for s in settings}
    own = norm(comp_name)
    out: list[M.InternalCommand] = []
    for cmd in commands:
        via: str | None = None
        if cmd.op and _op_component(cmd.op) == own:
            via = f"op:{cmd.op}"
        else:
            for path in command_settings(cmd):
                if norm(path) in wanted or any(_setting_matcher(path).match(w) for w in wanted):
                    via = f"setting:{path}"
                    break
        if via is not None:
            out.append(M.InternalCommand(header=cmd.header, via=via, set=cmd.set, query=cmd.query))
    return out


def downstream(inst: Instrument, wires: list[tuple[str, str]], start: str) -> dict[str, str]:
    """Components ``start`` influences -> how the influence reaches them ('wire' / 'ref' / 'select').

    Edges: a signal wire (output component -> input component), a ``refs`` link (the used component
    feeds the one that uses it), and a selection: a component with a bound ``source`` parameter
    picks which channel it reads with a setting (waveform and measurement sources), so every
    channel component reaches it although no wire or ref says so.
    """
    edges: dict[str, list[tuple[str, str]]] = {}

    def add(a: str, b: str, how: str) -> None:
        if a != b:
            edges.setdefault(a, []).append((b, how))

    for a, b in wires:
        add(a.rpartition(".")[0], b.rpartition(".")[0], "wire")
    for name, comp in inst.components.items():
        for _key, used in comp.ref_links():
            add(used, name, "ref")
        if comp.channel is None and comp.bound_path("source") is not None:
            for other, oc in inst.components.items():
                if oc.channel is not None:
                    add(other, name, "select")
    seen: dict[str, str] = {}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nxt, how in edges.get(cur, []):
            if nxt != start and nxt not in seen:
                seen[nxt] = how
                queue.append(nxt)
    return seen


def readings(
    commands: Iterable[CommandSpec], comp_name: str, reach: dict[str, str]
) -> list[M.InternalReading]:
    """The query commands that read the component itself or something ``reach`` lists."""
    by_comp: dict[str, tuple[str, str]] = {norm(comp_name): (comp_name, "self")}
    for name, how in reach.items():
        by_comp.setdefault(norm(name), (norm(name), how))
    out: list[M.InternalReading] = []
    seen: set[tuple[str, str]] = set()
    for cmd in commands:
        if not cmd.query or not cmd.op:
            continue
        hit = by_comp.get(_op_component(cmd.op))
        if hit is None or (cmd.header, hit[0]) in seen:
            continue
        seen.add((cmd.header, hit[0]))
        out.append(M.InternalReading(header=cmd.header, provider=hit[0], via=hit[1]))  # type: ignore[arg-type]
    return out
