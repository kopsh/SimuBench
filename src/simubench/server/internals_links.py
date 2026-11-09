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

Edges = dict[str, list[tuple[str, str]]]


def norm(name: str) -> str:
    """A channel component or setting name without its channel number: ``ch2.front`` -> ``ch.front``
    (a definition writes commands and bindings against ``ch.``)."""
    return _CH.sub("ch.", name)


def concrete(header: str, channel: int | None) -> str:
    """A command header as it reaches one channel: ``:CHANnel<ch>:SCALe`` -> ``:CHANnel1:SCALe``.
    A whole-instrument component keeps the pattern (the command names the channel itself)."""
    return header if channel is None else header.replace("<ch>", str(channel))


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


def drivers(
    commands: Iterable[CommandSpec], comp_name: str, channel: int | None, settings: list[str]
) -> list[M.InternalCommand]:
    """The commands that write one of ``settings`` (the component's bound settings, as resolved for
    its channel) or run an operation of ``comp_name``."""
    own = norm(comp_name)
    out: list[M.InternalCommand] = []
    for cmd in commands:
        setting: str | None = None
        op: str | None = None
        if cmd.op and _op_component(cmd.op) == own:
            op = cmd.op.rpartition(".")[2]
        else:
            for path in command_settings(cmd):
                match = _setting_matcher(path)
                setting = next((s for s in settings if norm(s) == norm(path) or match.match(norm(s))), None)
                if setting is not None:
                    break
        if setting is not None or op is not None:
            out.append(
                M.InternalCommand(
                    header=concrete(cmd.header, channel), setting=setting, op=op, set=cmd.set, query=cmd.query
                )
            )
    return out


def selections(inst: Instrument) -> list[M.InternalSelect]:
    """Whole-instrument components that pick the channel they read with a setting (a ``source``
    parameter bound to one: waveform transfer, automatic measurements) — no wire or ref says so."""
    out: list[M.InternalSelect] = []
    for name, comp in inst.components.items():
        path = comp.bound_path("source") if comp.channel is None else None
        if path is not None:
            out.append(M.InternalSelect(component=name, setting=path))
    return out


def influence_edges(inst: Instrument, wires: list[tuple[str, str]]) -> Edges:
    """Component -> the components it feeds, and how ('wire' / 'ref' / 'select').

    Edges: a signal wire (output component -> input component), a ``refs`` link (the used component
    feeds the one that uses it), and a selection (:func:`selections`): every channel component
    reaches a component that picks its channel with a setting.
    """
    edges: Edges = {}

    def add(a: str, b: str, how: str) -> None:
        if a != b and (b, how) not in edges.get(a, []):
            edges.setdefault(a, []).append((b, how))

    for a, b in wires:
        add(a.rpartition(".")[0], b.rpartition(".")[0], "wire")
    for name, comp in inst.components.items():
        for _key, used in comp.ref_links():
            add(used, name, "ref")
    for sel in selections(inst):
        for other, oc in inst.components.items():
            if oc.channel is not None:
                add(other, sel.component, "select")
    return edges


def _reach(edges: Edges, start: str) -> dict[str, tuple[str | None, str]]:
    """Every component ``start`` reaches -> (the component it is reached from, how), breadth first
    (the shortest path); ``start`` itself maps to (None, 'self')."""
    seen: dict[str, tuple[str | None, str]] = {start: (None, "self")}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nxt, how in edges.get(cur, []):
            if nxt not in seen:
                seen[nxt] = (cur, how)
                queue.append(nxt)
    return seen


def readings(
    commands: Iterable[CommandSpec], inst: Instrument, edges: Edges, start: str
) -> list[M.InternalReading]:
    """The query commands that read ``start`` itself or a component it reaches, nearest first, each
    with the path its influence takes."""
    cmds = [c for c in commands if c.query and c.op]
    reach = _reach(edges, start)
    out: list[M.InternalReading] = []
    seen: set[tuple[str, str]] = set()
    for provider, (_parent, how) in reach.items():
        path = [provider]
        while (parent := reach[path[0]][0]) is not None:
            path.insert(0, parent)
        channel = inst.components[provider].channel
        for cmd in cmds:
            if _op_component(cmd.op or "") != norm(provider):
                continue
            header = concrete(cmd.header, channel)
            if (header, provider) in seen:
                continue
            seen.add((header, provider))
            out.append(
                M.InternalReading(
                    header=header,
                    provider=provider,
                    via=how,  # type: ignore[arg-type]
                    path=path,
                    terminal=not edges.get(provider),
                )
            )
    return out
