"""Static lookup of program messages against an instrument definition, without running them.

Used where a command must be understood before (or instead of) executing it: the MCP service asks
for confirmation of dangerous commands, and the script scanner reports commands a model does not
support. The header rules here are the ones :class:`~simubench.engine.instrument.Instrument`
applies — the shared pieces (the built-in header table, the ``when`` gate) live in this module so
the two implementations cannot drift; tests/test_command_equivalence.py locks the equivalence.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Literal

from ..models.definition import CommandSpec, InstrumentDefinition
from ..scpi import values as V
from ..scpi.errors import ScpiError
from ..scpi.parse import Pattern, Token, Unit, parse_unit, resolve_tokens, split_units

COMMON = {"IDN", "RST", "CLS", "OPC", "WAI", "ESR", "ESE", "SRE", "STB", "TST"}
#: Which directions each common command answers, exactly as ``Instrument`` applies them:
#: (query, set). ``*OPC?`` reads the operation-complete bit, ``*OPC`` arms it, ``*WAI`` waits
#: either way; the rest answer one direction only (a wrong-direction common is an undefined
#: header, as dispatch's fall through ``_common`` raises).
COMMON_DIRECTIONS = {
    "IDN": (True, False),
    "RST": (False, True),
    "CLS": (False, True),
    "OPC": (True, True),
    "WAI": (True, True),
    "TRG": (False, True),
    "ESR": (True, False),
    "ESE": (True, True),
    "SRE": (True, True),
    "STB": (True, False),
    "TST": (True, False),
}

#: Headers every instrument answers without a definition entry (coverage tables list them too).
BUILTIN_HEADERS = (
    "*IDN",
    "*RST",
    "*CLS",
    "*OPC",
    "*WAI",
    "*TRG",
    "*ESR",
    "*ESE",
    "*SRE",
    "*STB",
    "*TST",
    ":SYSTem:ERRor[:NEXT]",
    ":SYSTem:ERRor:COUNt",
    ":SYSTem:ERRor:ALL",
    ":SYSTem:DATE",
    ":SYSTem:TIME",
)


def _header_key(header: str) -> str:
    """A header as written in a definition, compared without case, spaces or the root colon."""
    return "".join(header.split()).lstrip(":").upper()


#: The common ones a ``builtins`` list can leave out; the :SYSTem: ones are matched through their
#: patterns (they are not common commands).
COMMON_BUILTINS = frozenset(_header_key(h) for h in BUILTIN_HEADERS if h.startswith("*"))

#: The built-in :SYSTem: commands: (header, pattern, whether the query runs, whether the set
#: runs). A query answers the error queue; DATE/TIME read and set the clock, the error family
#: sets nothing.
SYST_BUILTINS = (
    (":SYSTem:ERRor[:NEXT]", Pattern.compile(":SYSTem:ERRor[:NEXT]"), True, False),
    (":SYSTem:ERRor:COUNt", Pattern.compile(":SYSTem:ERRor:COUNt"), True, False),
    (":SYSTem:ERRor:ALL", Pattern.compile(":SYSTem:ERRor:ALL"), True, False),
    (":SYSTem:DATE", Pattern.compile(":SYSTem:DATE"), True, True),
    (":SYSTem:TIME", Pattern.compile(":SYSTem:TIME"), True, True),
)


def builtin_runs(named: str, query: bool) -> bool:
    """Whether the named built-in :SYSTem: command answers in this direction; a wrong-direction
    one answers nothing and its header is undefined."""
    for header, _p, q_ok, s_ok in SYST_BUILTINS:
        if header == named:
            return not ((query and not q_ok) or (not query and not s_ok))
    return False


def answered_builtins(definition: InstrumentDefinition) -> list[str]:
    """The built-in headers the definition answers without a command of its own: its
    ``builtins`` list (all of ``BUILTIN_HEADERS`` when unset), and ``*OPT`` when its identity
    lists the installed options (``identity.options_reply``, D68)."""
    headers = list(BUILTIN_HEADERS if definition.builtins is None else definition.builtins)
    if definition.identity.options_reply is not None:
        headers.append("*OPT")
    return headers


def named_builtin(tokens: tuple[Token, ...]) -> str | None:
    """The built-in :SYSTem: header the tokens name (the common ``*`` ones are in ``COMMON``),
    or None."""
    for header in BUILTIN_HEADERS:
        if not header.startswith("*") and _compiled_builtin(header).match(tokens) is not None:
            return header
    return None


@cache
def _compiled_builtin(header: str) -> Pattern:
    return Pattern.compile(header)


def builtin_allowed(definition: InstrumentDefinition, header: str) -> bool:
    """Whether the definition's ``builtins`` list leaves this built-in command on (D42)."""
    allowed = definition.builtins
    return allowed is None or _header_key(header) in {_header_key(h) for h in allowed}


def when_matches(spec: CommandSpec, unit: Unit) -> bool:
    """Whether the unit's parameters have the values the command's ``when`` names."""
    names = [p.name for p in spec.params]
    for key, want in (spec.when or {}).items():
        i = names.index(key)
        if i >= len(unit.params):
            return False
        text = V.parse_string(unit.params[i]).strip().upper()
        # the word in any SCPI form: COUN for COUNt, STR for STRing
        if text != str(want).upper() and not V.matches_word(text, str(want)):
            return False
    return True


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


def told_apart(a: CommandSpec, b: CommandSpec) -> bool:
    """Whether the later command ``b`` on a header the earlier ``a`` also matches still runs: ``a``
    has a ``when`` and ``b`` has none (``b`` is the fallback for the other values and for a
    parameter left out, #602: ``:SYSTem:ERRor? [STRing]``), or both have one and some parameter
    value differs. A command of an option is told apart from a later one that is not of that
    option: the later one answers on a unit without it."""
    if a.option and a.option != b.option:
        return True  # b answers on the units without a's option (#1004)
    if not a.when:
        return False
    if not b.when:
        return True
    return any(k in b.when and str(v).upper() != str(b.when[k]).upper() for k, v in a.when.items())


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
        refused: CommandSpec | None = None  # a header that matched, with other parameter values
        hit: tuple[Pattern, CommandSpec] | None = None
        for p, c in _compiled(definition):
            if p.common != unit.common or p.match(tokens) is None:
                continue
            if c.when and not when_matches(c, unit):
                refused = c  # dispatch tries the next matching command, and refuses with -224
                continue
            hit = (p, c)
            break
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
            continue
        if unit.common:
            word = tokens[0].keyword
            if word == "OPT" and definition.identity.options_reply is not None:
                # the built-in *OPT? of a definition that lists the unit's options (D68)
                if unit.query:
                    out.append(UnitMatch(text, "common", True))
                else:
                    out.append(UnitMatch(text, "unsupported", False, reason="公共命令 *OPT 不响应设置"))
                continue
            if word in COMMON:
                q_ok, s_ok = COMMON_DIRECTIONS.get(word, (True, True))
                if (unit.query and not q_ok) or (not unit.query and not s_ok):
                    out.append(
                        UnitMatch(
                            text,
                            "unsupported",
                            unit.query,
                            reason=f"公共命令 *{word} 不响应{'查询' if unit.query else '设置'}",
                        )
                    )
                    continue
                if not builtin_allowed(definition, f"*{word}"):
                    out.append(
                        UnitMatch(text, "unsupported", unit.query, reason="模型的 builtins 未包含该公共命令")
                    )
                    continue
                out.append(UnitMatch(text, "common", unit.query))
            else:
                out.append(UnitMatch(text, "unsupported", unit.query, reason="未实现的公共命令"))
            continue
        if refused is not None:
            out.append(
                UnitMatch(text, "unsupported", unit.query, refused.header, refused, "该参数组合未实现")
            )
            continue
        named = named_builtin(tokens)
        if named is not None:
            if not builtin_allowed(definition, named):
                out.append(
                    UnitMatch(text, "unsupported", unit.query, reason="模型的 builtins 未包含该内置命令")
                )
                continue
            q_ok, s_ok = next((q, s) for _h, p, q, s in SYST_BUILTINS if p.match(tokens) is not None)
            if (unit.query and not q_ok) or (not unit.query and not s_ok):
                out.append(UnitMatch(text, "unsupported", unit.query, reason="该内置命令不响应这个方向"))
            else:
                out.append(UnitMatch(text, "builtin", unit.query))
            continue
        out.append(UnitMatch(text, "unsupported", unit.query, reason="仪表模型中没有该命令"))
    return out
