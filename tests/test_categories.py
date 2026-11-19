"""The category registry (models/categories.py, #1384): every class has its one row, complete.

A missing row used to be silent: the definition still loaded and the UI quietly fell back to the
generic card with no readings and no screen. These tests make that loud at build time; the Web's
``classes.test.ts`` and the generated TS type (a class without an ``INSTRUMENT_CLASSES`` entry is
a compile error) cover the Web side of the same registry.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

from simubench.contracts.api import InstrumentClass
from simubench.library.class_defaults import CLASS_ERRORS
from simubench.models import categories
from simubench.models.panels import _DEFAULTS, DEDICATED

ROOT = Path(__file__).resolve().parents[1]


def _declared_classes() -> set[str]:
    """The ``class:`` every built-in and validation definition declares (the same line scan
    ``classes.test.ts`` does on the Web side)."""
    out: set[str] = set()
    for folder in ("src/simubench/library/instruments", "validation/instruments"):
        for path in (ROOT / folder).glob("*.yaml"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("class: "):
                    out.add(line[len("class: ") :].strip())
    return out


def _template_categories() -> set[str]:
    """The ``category=`` every template registers, from the modules' source (importing the
    template modules costs about a second, over the unit budget)."""
    found: set[str] = set()
    for path in (ROOT / "src/simubench/library/templates").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                found |= {
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "category" and isinstance(kw.value, ast.Constant)
                }
    return found


def test_every_class_a_definition_declares_has_a_row():
    declared = _declared_classes()
    assert declared
    unknown = sorted(declared - set(categories.ids()))
    assert not unknown, f"definitions declare classes without a registry row: {unknown}"


def test_every_template_category_has_a_row():
    unknown = sorted(_template_categories() - set(categories.ids()))
    assert not unknown, f"templates generate classes without a registry row: {unknown}"


def test_every_row_has_a_label_key_and_a_row_kind():
    for row in categories.rows():
        assert row.label_key == f"acceptance.class.{row.id}", row.id
        assert row.screen in get_args(categories.ScreenKind), row.id
        assert row.panel in get_args(categories.PanelPolicy), row.id
        assert all(isinstance(w, str) and w for w in row.hints), row.id


def test_the_panel_policies_match_the_builders():
    """A default row has a builder in panels.py, a dedicated row has none; DEDICATED is derived
    from the registry."""
    assert {c.id for c in categories.rows() if c.panel == "default"} == set(_DEFAULTS)
    assert {c.id for c in categories.rows() if c.panel == "dedicated"} == set(DEDICATED)


def test_class_defaults_reference_registry_ids():
    """The class-default tables (D59) stay their own table, tied to the registry only by id."""
    assert set(CLASS_ERRORS) <= set(categories.ids())


def test_the_api_literal_names_the_registry_ids():
    """``models.api`` re-exports the registry's Literal: the schema (and with it the generated
    TS union) carries exactly the registered ids."""
    assert set(get_args(InstrumentClass)) == set(categories.ids())


def test_an_unknown_class_gets_the_generic_row():
    """#1410: a workspace definition may name a class the registry does not know, and every
    consumer reads the generic row for it instead of failing."""
    for unknown in ("no_such_class", None):
        assert categories.find(unknown) is None
        row = categories.get(unknown)
        assert (row.id, row.label_key, row.readings) == ("", "", False)
        assert (row.screen, row.panel) == ("generic", "generic")
        assert row.hints == ()
