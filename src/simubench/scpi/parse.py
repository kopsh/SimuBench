"""Parsing of SCPI program messages and header patterns.

A program message such as ``:CHAN1:SCAL 0.05;:TIM:SCAL 1E-3;*OPC?`` is split into program message
units; each unit has a header and parameters. Splitting respects quoted strings and IEEE 488.2
definite-length blocks, whose payload may contain any byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import ScpiError

_WS = b" \t\r\n"


@dataclass(frozen=True)
class Token:
    keyword: str  # upper case, without suffix
    suffix: int | None


@dataclass(frozen=True)
class Unit:
    """One program message unit."""

    common: bool  # header starts with '*'
    absolute: bool  # header starts with ':'
    tokens: tuple[Token, ...]
    query: bool
    params: tuple[bytes, ...]
    raw_header: str


def _scan(data: bytes, i: int, stop: bytes) -> int:
    """Return the index of the next byte in ``stop`` at nesting level zero, or len(data)."""
    n = len(data)
    while i < n:
        c = data[i : i + 1]
        if c in (b'"', b"'"):
            j = data.find(c, i + 1)
            while j != -1 and data[j + 1 : j + 2] == c:  # doubled quote inside a string
                j = data.find(c, j + 2)
            if j == -1:
                raise ScpiError(-150, "unterminated string")
            i = j + 1
            continue
        if c == b"#" and i + 1 < n and data[i + 1 : i + 2].isdigit():
            nd = int(data[i + 1 : i + 2])
            if nd == 0:  # indefinite block runs to the end of the message
                return n
            length = int(data[i + 2 : i + 2 + nd] or b"0")
            i = i + 2 + nd + length
            continue
        if c in stop:
            return i
        i += 1
    return n


def split_units(message: bytes) -> list[bytes]:
    data = message.rstrip(b"\r\n")
    units: list[bytes] = []
    i = 0
    while i <= len(data):
        j = _scan(data, i, b";")
        unit = data[i:j].strip(_WS)
        if unit:
            units.append(unit)
        if j >= len(data):
            break
        i = j + 1
    return units


def split_params(text: bytes) -> list[bytes]:
    params: list[bytes] = []
    i = 0
    text = text.strip(_WS)
    if not text:
        return params
    while True:
        j = _scan(text, i, b",")
        params.append(text[i:j].strip(_WS))
        if j >= len(text):
            break
        i = j + 1
    return params


_TOKEN = re.compile(r"^([A-Za-z_][A-Za-z_]*?)(\d*)$")


def parse_unit(unit: bytes) -> Unit:
    m = re.match(rb"^\s*([^\s]+)\s*(.*)$", unit, re.DOTALL)
    if not m:
        raise ScpiError(-102, "empty command")
    header = m.group(1).decode("ascii", errors="replace")
    rest = m.group(2)
    query = header.endswith("?")
    if query:
        header = header[:-1]
    common = header.startswith("*")
    absolute = header.startswith(":")
    body = header[1:] if (common or absolute) else header
    tokens: list[Token] = []
    for part in body.split(":"):
        if not part:
            raise ScpiError(-102, f"empty header node in {header!r}")
        t = _TOKEN.match(part)
        if not t:
            raise ScpiError(-102, f"invalid header {header!r}")
        tokens.append(Token(t.group(1).upper(), int(t.group(2)) if t.group(2) else None))
    return Unit(
        common, absolute, tuple(tokens), query, tuple(split_params(rest)), m.group(1).decode(errors="replace")
    )


# ---- header patterns ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Node:
    long: str
    short: str
    optional: bool
    suffix: str | None  # name of the captured numeric suffix, e.g. "ch"

    def matches(self, tok: Token) -> bool:
        kw = tok.keyword
        if kw not in (self.long, self.short):
            return False
        return not (tok.suffix is not None and self.suffix is None)


@dataclass
class Pattern:
    """A header pattern such as ``[:SOURce<ch>]:FREQuency[:CW]`` or ``*IDN``."""

    text: str
    common: bool
    nodes: tuple[Node, ...] = field(default=())

    @staticmethod
    def compile(text: str) -> Pattern:
        common = text.startswith("*")
        if common:
            word = text[1:]
            return Pattern(text, True, (Node(word.upper(), word.upper(), False, None),))
        nodes: list[Node] = []
        for m in re.finditer(r"\[:?[A-Za-z_]+(?:<\w+>)?\]|:?[A-Za-z_]+(?:<\w+>)?", text):
            part = m.group(0)
            optional = part.startswith("[")
            core = part.strip("[]").lstrip(":")
            sm = re.match(r"([A-Za-z_]+)(?:<(\w+)>)?$", core)
            if not sm:
                raise ValueError(f"bad pattern node {part!r} in {text!r}")
            word, suffix = sm.group(1), sm.group(2)
            short = "".join(c for c in word if c.isupper()) or word.upper()
            nodes.append(Node(word.upper(), short.upper(), optional, suffix))
        return Pattern(text, False, tuple(nodes))

    def match(self, tokens: tuple[Token, ...]) -> dict[str, int | None] | None:
        """Match tokens against the nodes; return captured suffixes, or None."""

        def rec(i: int, j: int, caps: dict[str, int | None]) -> dict[str, int | None] | None:
            if j == len(self.nodes):
                return caps if i == len(tokens) else None
            node = self.nodes[j]
            if i < len(tokens) and node.matches(tokens[i]):
                c = dict(caps)
                if node.suffix:
                    c[node.suffix] = tokens[i].suffix
                r = rec(i + 1, j + 1, c)
                if r is not None:
                    return r
            if node.optional:
                c = dict(caps)
                if node.suffix:
                    c.setdefault(node.suffix, None)
                return rec(i, j + 1, c)
            return None

        return rec(0, 0, {})
