"""The instrument categories in one place (#1384).

Every instrument class the platform knows is one row here: the ``id`` a definition's ``class``
(or its ``display.template``, the same namespace) names, the i18n key of the class label (the
Web resolves it per language, ``acceptance.class.<id>``), whether the screen polls ``/readings``,
the row kind the TUI's card and channel rows dispatch on, the panel policy, and the variable-name
hints the script scanner uses to break ties between definitions. The registry holds data only —
the render functions stay with their UI (TUI cards and rows, Web icons and screens): a row's kind
is a string each UI maps to its own function once. ``class_defaults`` (D59) stays a separate
table, tied to the registry only by these ids, as are the panel builders in ``panels.py``.

A definition may name a class that has no row (an old workspace file): it loads and runs, and
every consumer falls back to the generic row (:func:`get`) instead of failing. A missing row is
made loud at build time instead: ``tests/test_categories.py`` requires a row for every class the
built-in and validation definitions declare and for every template's category, and requires every
row to carry a label key and a row kind. On the Web the generated TS type of
``InstrumentClass`` (a Literal of these ids, re-exported by ``models.api``) makes a class without
a ``INSTRUMENT_CLASSES`` entry a compile error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The row kind a UI dispatches on: which rows the card draws and which channel lines the
#: instrument screen shows (the TUI maps each kind to its own functions; the Web maps it to a
#: screen component). ``generic`` is the fallback for a class without rows of its own.
ScreenKind = Literal["scope", "generator", "smu", "meter", "spectrum", "lockin", "rf", "load", "generic"]

#: The panel policy: ``dedicated`` — the class draws its own screen, no panel is used for it;
#: ``default`` — the class has a default front-panel layout (the builders in panels.py);
#: ``generic`` — neither, the generic layout.
PanelPolicy = Literal["dedicated", "default", "generic"]

#: The class ids, as ``models.api`` publishes them so the generated TS type carries the same set.
InstrumentClass = Literal[
    "oscilloscope",
    "function_generator",
    "spectrum_analyzer",
    "smu",
    "lockin",
    "electronic_load",
    "rf_generator",
    "multimeter",
    "calibrator",
    "daq",
    "power_supply",
    "frequency_counter",
    "tunable_laser",
    "optical_power_meter",
    "optical_attenuator",
    "source",
    "converter",
    "sampling_oscilloscope",
]


@dataclass(frozen=True)
class Category:
    """One instrument class's knowledge, previously spread over five places (#1384)."""

    id: str
    label_key: str  # the i18n key of the class label, zh and en (web/src/i18n)
    readings: bool  # the screen polls /readings (app.py #282)
    screen: ScreenKind
    panel: PanelPolicy
    #: variable-name hints of the script scanner, tie-break only (empty for most classes)
    hints: tuple[str, ...] = ()


_ROWS: tuple[Category, ...] = (
    # The first five keep their relative order: the script scanner takes the first row whose
    # hints match the receiver name.
    Category(
        "oscilloscope",
        "acceptance.class.oscilloscope",
        readings=True,
        screen="scope",
        panel="dedicated",
        hints=("scope", "osc", "dso", "mso"),
    ),
    Category(
        "function_generator",
        "acceptance.class.function_generator",
        readings=False,
        screen="generator",
        panel="dedicated",
        hints=("awg", "gen", "fg", "afg", "src", "source", "sig"),
    ),
    Category(
        "spectrum_analyzer",
        "acceptance.class.spectrum_analyzer",
        readings=True,
        screen="spectrum",
        panel="dedicated",
        hints=("sa", "spec", "fsv", "analyzer"),
    ),
    Category(
        "smu",
        "acceptance.class.smu",
        readings=True,
        screen="smu",
        panel="dedicated",
        hints=("smu", "keithley"),
    ),
    Category(
        "lockin",
        "acceptance.class.lockin",
        readings=True,
        screen="lockin",
        panel="dedicated",
        hints=("lockin", "lia", "sr8"),
    ),
    Category("electronic_load", "acceptance.class.electronic_load", readings=True, screen="load", panel="dedicated"),
    Category("rf_generator", "acceptance.class.rf_generator", readings=True, screen="rf", panel="dedicated"),
    Category("multimeter", "acceptance.class.multimeter", readings=True, screen="meter", panel="default"),
    Category("calibrator", "acceptance.class.calibrator", readings=True, screen="meter", panel="default"),
    Category("daq", "acceptance.class.daq", readings=True, screen="generic", panel="default"),
    Category("power_supply", "acceptance.class.power_supply", readings=True, screen="generic", panel="default"),
    Category(
        "frequency_counter", "acceptance.class.frequency_counter", readings=False, screen="generic", panel="default"
    ),
    Category("tunable_laser", "acceptance.class.tunable_laser", readings=False, screen="generic", panel="default"),
    Category(
        "optical_power_meter", "acceptance.class.optical_power_meter", readings=False, screen="generic", panel="default"
    ),
    Category(
        "optical_attenuator",
        "acceptance.class.optical_attenuator",
        readings=False,
        screen="generic",
        panel="default",
    ),
    Category("source", "acceptance.class.source", readings=False, screen="generic", panel="generic"),
    Category("converter", "acceptance.class.converter", readings=False, screen="generic", panel="generic"),
    Category(
        "sampling_oscilloscope",
        "acceptance.class.sampling_oscilloscope",
        readings=False,
        screen="generic",
        panel="generic",
    ),
)

_BY_ID = {c.id: c for c in _ROWS}

#: The fallback for an id (or none) the registry does not know: no label to show, no readings of
#: its own, generic rows, no panel — what an unknown class got before there was a registry.
_GENERIC = Category(id="", label_key="", readings=False, screen="generic", panel="generic")


def rows() -> tuple[Category, ...]:
    """Every registered category, in registry order."""
    return _ROWS


def ids() -> tuple[str, ...]:
    """Every registered class id, in registry order."""
    return tuple(_BY_ID)


def find(category_id: str | None) -> Category | None:
    """The category an id names, or ``None`` when the registry does not know it."""
    return _BY_ID.get(category_id or "")


def get(category_id: str | None) -> Category:
    """The category an id names; an unknown id (or none) gets the generic fallback row, so a
    definition of a class the registry does not know still loads, runs and shows."""
    return _BY_ID.get(category_id or "") or _GENERIC
