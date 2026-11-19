"""The command-list queries every instrument answers (#1558): ``:SYSTem:HELP:HEADers?``,
``:SYSTem:HELP:SYNTax? "<header>"`` and ``:SYSTem:HELP:SYNTax:ALL?``.

The lists are generated from the definition: its commands (those of an option the unit lacks left
out) and the built-in headers it answers, so what is listed is what executes. Real vendors write the
reply differently (Keysight Infiniium: a definite-length block, CR LF lines, ``/qonly/`` and
``/nquery/`` markers; Rohde & Schwarz: a string of lines with the same two markers), so the layout is
a preset of the definition's ``help`` block. The syntax text is generated from the parameter types, it
is an approximation of the vendor's own wording.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from functools import cache

from ..models.definition import InstrumentDefinition, ParamSpec
from ..scpi import values as V
from ..scpi.errors import ScpiError
from ..scpi.parse import Pattern, parse_unit, resolve_tokens
from .commands import COMMON_DIRECTIONS, SYST_BUILTINS, _header_key, answered_builtins


@dataclass(frozen=True)
class HelpFormat:
    """How a reply of the command-list queries is written."""

    block: bool  # the list is a definite-length block (#<n><length>…)
    separator: bytes
    qonly: str  # appended to a command that only answers a query ("" for none)
    nquery: str  # appended to a command that has no query form
    colon: bool  # headers keep their leading colon


PRESETS: dict[str, HelpFormat] = {
    "keysight": HelpFormat(True, b"\r\n", "/qonly/", "/nquery/", True),
    "keysight_plain": HelpFormat(True, b"\n", "", "", True),
    "rs": HelpFormat(False, b"\n", "/qonly/", "/nquery/", False),
    "generic": HelpFormat(False, b"\n", "/qonly/", "/nquery/", True),
}


def resolve_format(definition: InstrumentDefinition) -> HelpFormat:
    """The layout of the definition's ``help.preset``; ``auto`` goes by the manufacturer."""
    preset = definition.help.preset
    if preset == "auto":
        maker = definition.manufacturer.lower()
        if "keysight" in maker or "agilent" in maker:
            preset = "keysight_plain"
        elif "rohde" in maker or maker.startswith("r&s"):
            preset = "rs"
        else:
            preset = "generic"
    return PRESETS[preset]


@dataclass(frozen=True)
class HelpEntry:
    header: str
    query: bool
    set: bool
    params: tuple[ParamSpec, ...] = ()


def entries(definition: InstrumentDefinition, options: Collection[str]) -> list[HelpEntry]:
    """Every command this unit executes: the common ones first, then the headers in order. A header
    written twice (a ``when`` split, an option's and a plain one) is one entry that answers what
    either does."""
    merged: dict[str, HelpEntry] = {}

    def add(entry: HelpEntry) -> None:
        key = _header_key(entry.header)
        old = merged.get(key)
        if old is None:
            merged[key] = entry
        else:
            merged[key] = HelpEntry(
                old.header, old.query or entry.query, old.set or entry.set, old.params or entry.params
            )

    for c in definition.commands:
        if c.option and c.option not in options:
            continue
        add(HelpEntry(c.header, c.query, c.set, tuple(c.params)))
    for header in answered_builtins(definition):
        if header.startswith("*"):
            q_ok, s_ok = COMMON_DIRECTIONS.get(header[1:].upper(), (True, False))  # *OPT? only queries
        else:
            q_ok, s_ok = next(
                ((q, s) for h, _p, q, s in SYST_BUILTINS if _header_key(h) == _header_key(header)),
                (True, False),
            )
        add(HelpEntry(header, q_ok, s_ok))
    return sorted(merged.values(), key=lambda e: (not e.header.startswith("*"), _header_key(e.header)))


@cache
def _pattern(header: str) -> Pattern:
    return Pattern.compile(header)


def _shown(header: str, fmt: HelpFormat) -> str:
    return header if fmt.colon else header.lstrip(":")


def headers_reply(definition: InstrumentDefinition, options: Collection[str]) -> bytes:
    """The ``:SYSTem:HELP:HEADers?`` reply: one line per command, the way the preset writes it."""
    fmt = resolve_format(definition)
    lines: list[str] = []
    for e in entries(definition, options):
        line = _shown(e.header, fmt)
        if e.query and not e.set:
            line += "?" + fmt.qonly
        elif e.set and not e.query:
            line += fmt.nquery
        lines.append(line)
    payload = fmt.separator.join(line.encode() for line in lines) + fmt.separator
    return V.format_block(payload) if fmt.block else payload


#: The word for a parameter type in the syntax text (the names a vendor's manual uses).
_TYPE_WORDS = {
    "float": "numeric value",
    "int": "integer",
    "bool": "Boolean",
    "enum": "char data",
    "string": "string",
    "float_list": "list of numeric values",
    "block": "block data",
    "raw": "arbitrary data",
    "pairs": "list of pairs",
    "quantities": "numeric value",
    "channel_list": "expression",
}


def syntax_line(e: HelpEntry, fmt: HelpFormat) -> str:
    """``ROUTe:CLOSe[?] <expression>``: the header (``[?]`` when it also queries, ``?`` when it
    only does), then each parameter by its type, optional ones in brackets."""
    line = _shown(e.header, fmt)
    if e.query and e.set:
        line += "[?]"
    elif e.query:
        line += "?"
    params = [f"[<{_TYPE_WORDS[p.type]}>]" if p.optional else f"<{_TYPE_WORDS[p.type]}>" for p in e.params]
    return line + (" " + ",".join(params) if params else "")


def syntax_all_reply(definition: InstrumentDefinition, options: Collection[str]) -> bytes:
    """The ``:SYSTem:HELP:SYNTax:ALL?`` reply: the syntax line of every command."""
    fmt = resolve_format(definition)
    return fmt.separator.join(syntax_line(e, fmt).encode() for e in entries(definition, options))


def syntax_reply(definition: InstrumentDefinition, options: Collection[str], header: bytes) -> bytes:
    """The ``:SYSTem:HELP:SYNTax? "<header>"`` reply (a quoted string): the syntax line of the
    command the header names, in any short or long form; -113 when this unit has no such command."""
    fmt = resolve_format(definition)
    text = V.parse_string(header).strip()
    if not text:
        raise ScpiError(-109)
    tokens = resolve_tokens(parse_unit(text.encode()), ())
    for e in entries(definition, options):
        if _pattern(e.header).match(tokens) is not None:
            return V.format_string(syntax_line(e, fmt))
    raise ScpiError(-113, f"{text}: no such command")
