"""The instrument categories in one place (#1384).

Every instrument class the platform knows is one row here: the ``id`` a definition's ``class``
(or its ``display.template``, the same namespace) names, the i18n key of the class label (the
Web resolves it per language, ``acceptance.class.<id>``), whether the screen polls ``/readings``,
the row kind the TUI's card and channel rows dispatch on, the panel policy, the variable-name
hints the script scanner uses to break ties between definitions, and the reading keys — the
roles the class's dedicated screen reads the readings payload by, declared once so the TUI's
row functions and the Web's screens share the same map (#1637). The registry holds data only —
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

The ids are written twice here on purpose: ``InstrumentClass`` must be a static expression for the
type checker and the generated TS union, while ``_ROWS`` is the runtime table.
``test_the_api_literal_names_the_registry_ids`` keeps the two in step; there is no third copy.
``scriptscan.draft_definition`` writes ``"other"`` when no hint names a class — likewise outside
the registry, and the documented generic fallback for a definition nobody has classified yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

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
    #: The class's front-panel reading keys, by role (#1637): the vocabulary its dedicated screen
    #: reads the readings payload with — the TUI's row and reason functions and the Web's screen
    #: take the keys from here, so the map is written once. Declared per class whose screen reads
    #: readings of its own; the roles are the consumers' shared contract (a role the screen uses
    #: that the row does not declare is a loud failure: a missing TS property fails to compile,
    #: the TUI guard test bans the literals the keys replace).
    reading_keys: Mapping[str, str] = field(default_factory=dict)
    #: variable-name hints of the script scanner, tie-break only (empty for most classes)
    hints: tuple[str, ...] = ()
    #: The class's screen data must be fetched for the TUI to show (the scope's live frame and
    #: run state, section 4.2): declared on the row, so a new screen kind teaches its one row and
    #: both TUI polling sites follow (categories.fetches, #1735).
    screen_poll: bool = False


_ROWS: tuple[Category, ...] = (
    # The first five keep their relative order: the script scanner takes the first row whose
    # hints match the receiver name.
    Category(
        "oscilloscope",
        "acceptance.class.oscilloscope",
        readings=True,
        screen="scope",
        panel="dedicated",
        screen_poll=True,
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
        # The smu_meter's readings (library/components/smu.py), by the role the SMU's screen reads:
        # the TUI's card, channel rows and reason dialog and the Web's SmuScreen look them up here.
        reading_keys={
            "regulation": "core.regulation",
            "limited": "core.limited",
            "forcing": "core.forcing",
            "voltage": "core.voltage",
            "current": "core.current",
            "limit": "core.limit",
            "reading_time": "core.reading_time",
            "buffer_v": "core.buffer_v",
            "buffer_i": "core.buffer_i",
            "buffer_r": "core.buffer_r",
            "buffer_t": "core.buffer_t",
            "buffer_stat": "core.buffer_stat",
        },
    ),
    Category(
        "lockin",
        "acceptance.class.lockin",
        readings=True,
        screen="lockin",
        panel="dedicated",
        hints=("lockin", "lia", "sr8"),
        # The lock-in's demodulator and reference-oscillator readings (library/components/lockin.py):
        # the Web's LockinScreen reads them all from here.
        reading_keys={
            "locked": "osc.locked",
            "detect_frequency": "osc.detect_frequency",
            "overload": "demod.overload",
            "x": "demod.X",
            "y": "demod.Y",
            "r": "demod.R",
            "theta": "demod.THETA",
            "enbw": "demod.enbw",
            "settling": "demod.settling",
            "noise_sigma": "demod.noise_sigma",
            "r_projected": "demod.R_projected",
        },
    ),
    Category(
        "electronic_load", "acceptance.class.electronic_load", readings=True, screen="load", panel="dedicated"
    ),
    Category("rf_generator", "acceptance.class.rf_generator", readings=True, screen="rf", panel="dedicated"),
    Category("multimeter", "acceptance.class.multimeter", readings=True, screen="meter", panel="default"),
    Category("calibrator", "acceptance.class.calibrator", readings=True, screen="meter", panel="default"),
    Category("daq", "acceptance.class.daq", readings=True, screen="generic", panel="default"),
    Category(
        "power_supply", "acceptance.class.power_supply", readings=True, screen="generic", panel="default"
    ),
    Category(
        "frequency_counter",
        "acceptance.class.frequency_counter",
        readings=False,
        screen="generic",
        panel="default",
    ),
    Category(
        "tunable_laser", "acceptance.class.tunable_laser", readings=False, screen="generic", panel="default"
    ),
    Category(
        "optical_power_meter",
        "acceptance.class.optical_power_meter",
        readings=False,
        screen="generic",
        panel="default",
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


def fetches(row: Category, *, panel: bool = False) -> frozenset[str]:
    """What the TUI fetches for an instrument of this class (#1735, the polling predicate the
    app's two sites — the initial marking and the per-event refresh — once derived each on their
    own): ``"readings"`` when the class's screen polls them or the bench file gave the instrument
    a panel of its own (#282), ``"screen"`` for the classes whose screen data must be fetched.
    Declared on the row (``readings``, ``screen_poll``), so a new screen kind is one registry
    edit and both callers follow; missing one was a screen that never refreshed."""
    want: set[str] = set()
    if row.readings or panel:
        want.add("readings")
    if row.screen_poll:
        want.add("screen")
    return frozenset(want)


def as_api_class(category_id: str) -> InstrumentClass:
    """Pass a definition's ``class`` (a plain str) into an API field. The schema names the set the
    UI is built for, the fields carrying it deliberately skip validation (``SkipValidation``), so
    a class the registry does not know still loads, runs and shows the generic layout (#1384)."""
    return cast(InstrumentClass, category_id)
