"""Static lookup of program messages against an instrument definition, without running them.

Used where a command must be understood before (or instead of) executing it: the MCP service asks
for confirmation of dangerous commands, and the script scanner reports commands a model does not
support. The header rules are the ones :class:`~simubench.engine.instrument.Instrument` applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..models.definition import CommandSpec, InstrumentDefinition
from ..scpi.errors import ScpiError
from ..scpi.parse import Pattern, Token, parse_unit, resolve_tokens, split_units

COMMON = {"IDN", "RST", "CLS", "OPC", "WAI", "ESR", "ESE", "SRE", "STB", "TST"}
_BUILTIN = [
    Pattern.compile(":SYSTem:ERRor[:NEXT]"),
    Pattern.compile(":SYSTem:ERRor:COUNt"),
    Pattern.compile(":SYSTem:ERRor:ALL"),
]

Kind = Literal["command", "common", "builtin", "unsupported", "invalid"]


@dataclass(frozen=True)
class UnitMatch:
    text: str
    kind: Kind
    query: bool
    header: str | None = None  # the definition's header pattern, for kind "command"
    spec: CommandSpec | None = None
    reason: str | None = None

    @property
    def supported(self) -> bool:
        return self.kind in ("command", "common", "builtin")

    @property
    def dangerous(self) -> bool:
        return bool(self.spec and self.spec.dangerous and not self.query)


_CACHE: dict[int, tuple[InstrumentDefinition, list[tuple[Pattern, CommandSpec]]]] = {}


def _compiled(definition: InstrumentDefinition) -> list[tuple[Pattern, CommandSpec]]:
    # Keyed by identity (definitions are not hashable); the entry keeps the definition alive so the
    # id cannot be reused while cached. Definitions are loaded once per file version.
    hit = _CACHE.get(id(definition))
    if hit is None or hit[0] is not definition:
        if len(_CACHE) > 256:
            _CACHE.clear()
        hit = _CACHE[id(definition)] = (
            definition,
            [(Pattern.compile(c.header), c) for c in definition.commands],
        )
    return hit[1]


def classify(definition: InstrumentDefinition, message: str) -> list[UnitMatch]:
    """Classify each unit of a program message (``;``-separated, with SCPI path rules)."""
    try:
        units = split_units(message.encode())
    except ScpiError as e:
        return [UnitMatch(message, "invalid", False, reason=e.message)]
    out: list[UnitMatch] = []
    path: tuple[Token, ...] = ()
    for raw in units:
        text = raw.decode(errors="replace").strip()
        try:
            unit = parse_unit(raw)
        except ScpiError as e:
            out.append(UnitMatch(text, "invalid", False, reason=e.message))
            continue
        tokens = resolve_tokens(unit, path)
        if not unit.common:
            path = tokens[:-1]
        hit = next(
            (
                (p, c)
                for p, c in _compiled(definition)
                if p.common == unit.common and p.match(tokens) is not None
            ),
            None,
        )
        if unit.common and hit is None:
            word = tokens[0].keyword
            kind: Kind = "common" if word in COMMON else "unsupported"
            out.append(
                UnitMatch(text, kind, unit.query, reason=None if kind == "common" else "未实现的公共命令")
            )
            continue
        if hit is not None:
            spec = hit[1]
            if (unit.query and not spec.query) or (not unit.query and not spec.set):
                out.append(
                    UnitMatch(
                        text,
                        "unsupported",
                        unit.query,
                        spec.header,
                        spec,
                        "该命令只能设置" if unit.query else "该命令只能查询",
                    )
                )
            else:
                out.append(UnitMatch(text, "command", unit.query, spec.header, spec))
        elif unit.query and any(p.match(tokens) is not None for p in _BUILTIN):
            out.append(UnitMatch(text, "builtin", True))
        else:
            out.append(UnitMatch(text, "unsupported", unit.query, reason="仪表模型中没有该命令"))
    return out
