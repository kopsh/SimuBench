"""The coverage advisor (#895) on its own: no instrument, no session — a real definition, its
coverage table and the headers a client sent."""

from __future__ import annotations

from simubench.engine.coverage_advice import CoverageAdvisor, header_forms
from simubench.library.loader import DefinitionLibrary
from simubench.scpi.parse import Pattern, parse_unit

LIB = DefinitionLibrary()
MODEL = "bk-8614"


def _advisor(model: str = MODEL) -> CoverageAdvisor:
    d = LIB.get(model)
    return CoverageAdvisor(
        d,
        name="t",
        real=d.reference is not None,
        source_file=None,
        commands=lambda: ((Pattern.compile(c.header), c.header) for c in d.commands),
    )


def _classify(adv: CoverageAdvisor, text: str):
    unit = parse_unit(text.encode())
    return adv.classify(unit, unit.tokens)


def test_header_forms_lists_long_and_short_spellings():
    forms = {f.upper() for f in header_forms(Pattern.compile(":SYSTem:VERSion"))}
    assert forms == {"SYSTEM:VERSION", "SYSTEM:VERS", "SYST:VERSION", "SYST:VERS"}


def test_the_table_is_compiled_once_on_first_use():
    adv = _advisor()
    assert adv._coverage is None  # nothing read before the first question
    entries = adv.entries()
    assert entries and entries is adv.entries()


def test_a_header_the_table_lists_is_declared_but_not_modelled():
    reason, origin, closest = _classify(_advisor(), "*TST?")
    assert (reason, closest) == ("not_modeled", None)
    assert origin and origin.endswith("bk-8614.coverage.yaml")


def test_a_header_the_table_does_not_list_is_not_in_the_model():
    reason, _, closest = _classify(_advisor(), "ZZZQQQ:NOSUCH?")
    assert (reason, closest) == ("not_in_model", None)


def test_a_misspelt_header_gets_the_authors_spelling():
    reason, _, closest = _classify(_advisor(), "SYSTem:VERSio?")
    assert (reason, closest) == ("not_in_model", ":SYSTem:VERSion")


def test_a_definition_without_a_table_falls_back_on_its_own_commands():
    d = LIB.get(MODEL).model_copy()
    d._coverage = None
    adv = CoverageAdvisor(
        d,
        name="t",
        real=False,
        source_file="x.yaml",
        commands=lambda: ((Pattern.compile(c.header), c.header) for c in d.commands),
    )
    assert adv.entries() is None
    reason, origin, closest = _classify(adv, ":TRIGger:SOURc?")
    assert (reason, origin, closest) == ("not_in_definition", "x.yaml", ":TRIGger:SOURce")
