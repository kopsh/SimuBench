"""The snapshot tests' settle rule (#896): a capture waits for the screen to stop changing, not
for the first needle to appear — the eload capture once fired while its readings were still on
the way. Pure text logic, no Textual, no server (fast tier)."""

from __future__ import annotations

from helpers import StableScreen

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


def test_the_ready_patterns_gate_the_capture():
    """The #896 shape: the needle is on screen from the start, the value arrives later — the
    plain-substring and ``re:`` patterns hold the capture until then."""
    s = StableScreen("INPUT", (r"re:V\s+(?:4\.999[4-9][0-9]|5\.000[0-6][0-9]) V", "I 0.00000 A"))
    s.feed(LABEL_ONLY)
    assert not s.feed(LABEL_ONLY)  # stable and needle there, but the readings are missing
    assert not s.feed(SETTLED)  # changed: count restarts
    assert s.feed(SETTLED)
    s2 = StableScreen("", ("re:P\\s+1\\.000 W",))  # a value the screen does not show
    s2.feed(SETTLED)
    assert not s2.feed(SETTLED)
