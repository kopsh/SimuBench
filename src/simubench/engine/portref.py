"""Port references: dotted references of the form ``<owner>.<port>`` (glossary 端口引用).

The syntax lives here, once: the last segment is the port name, everything before it is the
owner's name (an instrument, a part, or a component inside a definition). Internal references can
carry a ``ch<n>.`` channel prefix, so the owner segment may itself contain dots. Existence checks
and error wording stay at the call sites on purpose — authoring diagnostics, edit errors and
runtime guards are different layers presenting the same grammar.
"""

from __future__ import annotations

from typing import NamedTuple


class PortRef(NamedTuple):
    """An ``<owner>.<port>`` reference; either half may be empty when the reference is malformed."""

    owner: str
    port: str

    def __str__(self) -> str:
        return f"{self.owner}.{self.port}"


def split(ref: str) -> PortRef:
    """Tolerant split: always returns a :class:`PortRef`, never raises. A malformed reference
    keeps the halves it has, so callers can fold the syntax check into their own lookup and
    message (an empty ``owner`` means "没有名为 …" with the whole reference echoed)."""
    owner, _, port = ref.rpartition(".")
    return PortRef(owner, port)


def parse(ref: str) -> PortRef | None:
    """Strict split: ``None`` unless both halves are non-empty (an authoring-level syntax check;
    the caller supplies the wording)."""
    r = split(ref)
    return r if r.owner and r.port else None
