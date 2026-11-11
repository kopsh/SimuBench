"""The snapshot tests' settle rule (#896): a capture waits for the screen to stop changing, not
for the first needle to appear — the eload capture once fired while its readings were still on
the way. Pure text logic, no Textual, no server (fast tier)."""

from __future__ import annotations

from helpers import Near, StableScreen, reading_of

LABEL_ONLY = "INPUT\nV —\nI —"  # the label is there, the readings have not landed yet
SETTLED = "INPUT\nV 5.00002 V\nI 0.00000 A\nP 0.000 W"


def test_two_identical_nonempty_renders_with_the_needle_settle():
    s = StableScreen("INPUT")
    assert not s.feed(SETTLED)  # one render proves nothing
    assert s.feed(SETTLED)


def test_a_changed_render_restarts_the_count():
    s = StableScreen("INPUT")
    assert not s.feed(LABEL_ONLY)
    assert s.feed(LABEL_ONLY)  # stable and needle there; the readings are gated by `ready`
    assert not s.feed(SETTLED)  # the readings land: the screen changed, the count starts over
    assert s.feed(SETTLED)  # two identical renders again


def test_the_needle_must_be_there():
    s = StableScreen("INPUT")
    other = SETTLED.replace("INPUT", "OUTPUT")
    s.feed(other)
    assert not s.feed(other)  # two identical renders of the wrong screen


def test_blank_or_empty_renders_never_settle():
    s = StableScreen("")
    assert not s.feed("")
    assert not s.feed("")  # two identical blanks
    assert not s.feed("   \n  ")
    assert not s.feed("   \n  ")


def test_the_ready_checks_gate_the_capture():
    """The #896 shape: the needle is on screen from the start, the value arrives later — the
    plain-substring and reading checks hold the capture until then."""
    s = StableScreen("INPUT", (Near("V", "V", 5.0, 6e-4), "I 0.00000 A"))
    s.feed(LABEL_ONLY)
    assert not s.feed(LABEL_ONLY)  # stable and needle there, but the readings are missing
    assert not s.feed(SETTLED)  # changed: count restarts
    assert s.feed(SETTLED)
    s2 = StableScreen("", (Near("P", "W", 1.0),))  # a value the screen does not show
    s2.feed(SETTLED)
    assert not s2.feed(SETTLED)


def test_a_reading_is_read_back_through_its_prefix():
    """#926: the number after the label, scaled by its engineering prefix, between panel borders."""
    screen = "│CH1 voltage    -24.18046 µV││OUT1+ →│\n│CH1 current    -359.0399 µA│"
    assert reading_of(screen, "CH1 voltage", "V") == -24.18046e-6
    assert reading_of(screen, "CH1 current", "A") == -359.0399e-6
    assert reading_of("│Voltage    1.0000 V│\n│V  5.00002 V│", "V", "V") == 5.00002  # whole words
    assert reading_of("│V —│", "V", "V") is None  # not there yet
    assert reading_of(screen, "CH2 voltage", "V") is None


def test_a_near_check_judges_the_value_against_its_band():
    near = Near("V", "V", 5.0, 6e-4)
    assert near("V 4.99942 V") and near("V 5.00059 V")
    assert not near("V 5.00062 V") and not near("V 4.999 V")
    assert not near("V 500.0000 mV")  # #926: a wrong magnitude never passes
    assert Near("I", "A", 0.0)("I 0.00000 A") and not Near("I", "A", 0.0)("I 1.00000 µA")
