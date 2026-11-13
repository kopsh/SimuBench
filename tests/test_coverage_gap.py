"""Why a command is not modeled (#1037): the coverage table's gap and blocked_by, their rules, the
counts, and the advisor handing them to the -113 event. No instrument, no session."""

from __future__ import annotations

from pydantic import ValidationError

from helpers import raises_with
from simubench.engine.coverage_advice import CoverageAdvisor
from simubench.library.coverage import Coverage, CoverageEntry
from simubench.library.loader import DefinitionLibrary
from simubench.scpi.parse import Pattern, parse_unit

LIB = DefinitionLibrary()


def test_gap_is_refused_on_an_implemented_command():
    with raises_with(ValidationError, "not_modeled commands only"):
        CoverageEntry(header=":FOO", level="implemented", gap="model_work")


def test_blocked_needs_the_capability_it_waits_for():
    with raises_with(ValidationError, "needs blocked_by"):
        CoverageEntry(header=":FOO", level="not_modeled", gap="blocked")


def test_blocked_by_needs_gap_blocked():
    with raises_with(ValidationError, "needs gap: blocked"):
        CoverageEntry(header=":FOO", level="not_modeled", gap="model_work", blocked_by="C5")


def test_gap_stats_count_untriaged_commands_apart():
    cov = Coverage(
        model="x",
        sections=[
            {
                "section": "s",
                "commands": [
                    {"header": ":A", "level": "implemented"},
                    {"header": ":B", "level": "not_modeled", "gap": "out_of_scope"},
                    {"header": ":C", "level": "not_modeled", "gap": "blocked", "blocked_by": "C5"},
                    {"header": ":D", "level": "not_modeled"},
                ],
            }
        ],
    )
    assert cov.gap_stats() == {"out_of_scope": 1, "blocked": 1, "model_work": 0, "untriaged": 1}


def test_the_advisor_hands_over_the_entry_with_its_gap():
    d = LIB.get("bk-8614").model_copy()
    d._coverage = Coverage(
        model="bk-8614",
        sections=[
            {
                "section": "s",
                "commands": [
                    {
                        "header": "*TST",
                        "level": "not_modeled",
                        "gap": "blocked",
                        "blocked_by": "C5",
                        "note": "why",
                    }
                ],
            }
        ],
    )
    adv = CoverageAdvisor(
        d,
        name="t",
        real=True,
        source_file=None,
        commands=lambda: ((Pattern.compile(c.header), c.header) for c in d.commands),
    )
    unit = parse_unit(b"*TST?")
    listed = adv.listed(unit, unit.tokens)
    assert listed is not None
    assert (listed.gap, listed.blocked_by, listed.note) == ("blocked", "C5", "why")
