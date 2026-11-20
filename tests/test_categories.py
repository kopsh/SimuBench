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
        assert row.reading_keys == {}
        assert row.hints == ()


def test_the_reading_keys_name_blocks_the_models_declare():
    """#1637: a row's reading keys read ``<block>.<name>`` and each block is a component some
    built-in or validation model of the class actually declares — the registry spells the keys
    the models produce, not a wish. (A model renaming its blocks is the declared-scope's own
    business: the screen reads the registry, and that other gap is a separate issue.)"""
    import yaml

    parts: dict[str, set[str]] = {}
    for folder in ("src/simubench/library/instruments", "validation/instruments"):
        for path in (ROOT / folder).glob("*.yaml"):
            if path.name.endswith((".coverage.yaml", ".cases.yaml")):
                continue
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            names = {
                c.get("name")
                for key in ("components", "channel_components")
                for c in (doc.get(key) or [])
                if isinstance(c, dict)
            }
            parts.setdefault(doc.get("class"), set()).update(names)
    for row in categories.rows():
        for role, key in row.reading_keys.items():
            block, _, name = key.partition(".")
            assert block and name, (row.id, role, key)
            assert block in parts.get(row.id, set()), (row.id, role, key)


def test_the_reading_keys_are_published_in_the_api_schema():
    """#1637: ``InstrumentInfo.reading_keys``' published schema derives from the registry — one
    property per role any row declares, all required, nothing extra. The generated TS type
    carries it, so the Web's screens reading a role the rows do not declare fail to compile, and
    a row losing a role the screens read does too."""
    from simubench.contracts.api import InstrumentInfo

    schema = InstrumentInfo.model_json_schema()["properties"]["reading_keys"]
    roles = sorted({role for row in categories.rows() for role in row.reading_keys})
    assert schema["additionalProperties"] is False
    assert schema["required"] == roles
    assert sorted(schema["properties"]) == roles


def test_the_tui_reads_no_reading_key_literal():
    """#1637: the TUI's reading-key consumers (cards, rows, reasons) take the keys from the
    category registry's row, not from string literals — a hand-copied map here is one a registry
    change cannot fix. The Web's screens carry the same ban as an ESLint rule."""
    for path in ("src/simubench/tui/cards.py", "src/simubench/tui/rows.py", "src/simubench/tui/reasons.py"):
        for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not node.value.startswith(("core.", "demod.", "osc.")), (path, node.value)


def test_fetches_reads_the_row_and_the_instruments_own_panel():
    """#1735: the TUI's polling predicate is derived once (categories.fetches) from the registry
    row — /readings for the classes whose screen polls them or an instrument given a panel of its
    own (#282), /screen for the rows that declare screen_poll. A new screen kind is one row edit;
    before, the app's two sites each derived the predicates and missing one meant a screen that
    never refreshed."""
    scope = categories.get("oscilloscope")
    assert categories.fetches(scope) == frozenset({"readings", "screen"})
    meter = categories.get("multimeter")  # readings yes, screen data no
    assert categories.fetches(meter) == frozenset({"readings"})
    source = categories.get("source")  # neither, but its own panel makes the readings go
    assert categories.fetches(source, panel=True) == frozenset({"readings"})
    assert categories.fetches(source) == frozenset()
    unknown = categories.get("no_such_class")
    assert categories.fetches(unknown, panel=True) == frozenset({"readings"})
