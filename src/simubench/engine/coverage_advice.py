"""Why an instrument does not execute a header, and which header the client probably meant (#895).

A definition's coverage table (D18) says which commands of the vendor's manual the model covers.
:class:`CoverageAdvisor` compiles it for runtime lookups on first use and classifies a header the
definition does not have: declared but not modelled, not in the model at all, or absent from a
design-specification definition. It holds the table, the file it came from and the suggestion index;
the instrument only asks. It needs nothing of a running instrument, so it is tested on its own.
"""

from __future__ import annotations

import difflib
import itertools
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..models.definition import InstrumentDefinition
from ..scpi.parse import Pattern, Token, Unit
from .commands import _header_key

log = logging.getLogger(__name__)


def header_forms(pattern: Pattern) -> list[str]:
    """Every spelling the header pattern accepts, long and short, for closest-match comparison."""
    choices: list[list[str | None]] = []
    for node in pattern.nodes:
        words: list[str] = [node.long] if node.short == node.long else [node.long, node.short]
        if node.suffix:
            words = [w + "1" for w in words]
        choices.append([None, *words] if node.optional else [*words])
    out = []
    for combo in itertools.islice(itertools.product(*choices), 512):
        parts = [p for p in combo if p is not None]
        if parts:
            out.append(":".join(parts))
    return out


@dataclass
class Covered:
    """One entry of the definition's coverage table, ready for runtime lookups (D18)."""

    pattern: Pattern
    header: str
    level: str
    #: Why a not_modeled command is not modeled, and what it waits for (#1037); None when untriaged.
    gap: str | None = None
    blocked_by: str | None = None
    note: str | None = None


class CoverageAdvisor:
    """The coverage table of one definition and the classification built on it.

    ``commands`` yields the definition's own ``(pattern, header)`` pairs; it is asked on first use
    of the suggestion, after the instrument has compiled them.
    """

    def __init__(
        self,
        definition: InstrumentDefinition,
        *,
        name: str,
        real: bool,
        source_file: str | None,
        commands: Callable[[], Iterable[tuple[Pattern, str]]],
    ) -> None:
        self.definition = definition
        self.name = name
        self.real = real
        self.source_file = source_file
        self._commands = commands
        #: The compiled entries and the table's file; None when the definition has no table (a broken
        #: or missing table falls back to the definition alone). The table itself is read by the
        #: library loader with the definition (cached with it); only the cheap pattern compilation
        #: waits for first use, so no file I/O lands on the session's wall clock (D47).
        self._coverage: list[Covered] | None = None
        self._coverage_file: str | None = None
        self._coverage_tried = False
        #: Every header spelling the instrument answers, built once for the -113's suggestion.
        self._header_keys: dict[str, str] | None = None
        self._header_key_list: list[str] | None = None

    def entries(self) -> list[Covered] | None:
        """The definition's coverage table, compiled for runtime lookups on first use (D18)."""
        if not self._coverage_tried:
            self._coverage_tried = True
            self._load()
        return self._coverage

    def _load(self) -> None:
        """Compile the coverage table the loader read with the definition (D18); an absent or
        unreadable table leaves the instrument running on the definition alone."""
        cov = self.definition._coverage
        if cov is None:
            return
        entries: list[Covered] = []
        for _, entry in cov.entries():
            try:
                entries.append(
                    Covered(
                        Pattern.compile(entry.header),
                        entry.header,
                        entry.level,
                        gap=entry.gap,
                        blocked_by=entry.blocked_by,
                        note=entry.note,
                    )
                )
            except ValueError:
                continue  # a header the pattern language cannot read: no runtime match for it
        self._coverage = entries
        self._coverage_file = str(self.definition._coverage_file)

    def listed(self, unit: Unit, tokens: tuple[Token, ...]) -> Covered | None:
        """The coverage table's entry for the message unit's command, or None when it has none."""
        return next((e for e in self.entries() or () if self.covered(e, unit, tokens)), None)

    def classify(self, unit: Unit, tokens: tuple[Token, ...]) -> tuple[str, str | None, str | None]:
        """(reason, origin, closest) for a header the definition does not execute."""
        coverage = self.entries()
        if coverage is not None:
            covered = self.listed(unit, tokens)
            if covered is not None:
                # A command the author listed (or one of theirs used in a form the model lacks).
                return "not_modeled", self._coverage_file, None
            return "not_in_model", self._coverage_file, self.closest(unit)
        if not self.real:
            return "not_in_definition", self.source_file, self.closest(unit)
        return "not_modeled", self.source_file, None  # no coverage table: the old classification

    @staticmethod
    def covered(entry: Covered, unit: Unit, tokens: tuple[Token, ...]) -> bool:
        """Whether the message unit uses the coverage entry's command (the coverage checker's match rule)."""
        if entry.pattern.common:
            return unit.common and unit.tokens[0].keyword == entry.pattern.nodes[0].long
        return not unit.common and entry.pattern.match(tokens) is not None

    def closest(self, unit: Unit) -> str | None:
        """The author's command closest to the one the client sent (its own long and short
        spellings), or None when none is reasonably close."""
        sent = _header_key(unit.raw_header)
        if not sent:
            return None
        if self._header_keys is None:
            keys: dict[str, str] = {}
            for entry in self.entries() or ():
                try:
                    for form in header_forms(entry.pattern):
                        keys.setdefault(_header_key(form), entry.header)
                except Exception as exc:  # a coverage entry that does not parse only loses a suggestion
                    log.debug("%s: header suggestion skipped for %r: %s", self.name, entry.header, exc)
                    continue
            for pattern, header in self._commands():
                for form in header_forms(pattern):
                    keys.setdefault(_header_key(form), header)
            self._header_keys = keys
            self._header_key_list = list(keys)
        assert self._header_key_list is not None
        hit = difflib.get_close_matches(sent, self._header_key_list, n=1, cutoff=0.6)
        return self._header_keys[hit[0]] if hit else None
