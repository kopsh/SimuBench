"""The TUI row layer's pure functions (issue 635): the error queue's reconciliation — a slot
belongs to the newest matching event — and the error's origin from the log's pairing."""

from __future__ import annotations

from types import SimpleNamespace

from simubench.tui import rows

LANG = "en"


def ev(code, message, t_ns, seq=None):
    e = {"kind": "error", "instrument": "psu", "code": code, "message": message, "t_ns": t_ns}
    if seq is not None:
        e["seq"] = seq
    return e


def line(seq, command, source="socket:1", error_seq=None):
    return SimpleNamespace(seq=seq, command=command, source=source, error_seq=error_seq)


def test_a_queue_slot_belongs_to_the_newest_matching_event():
    """Two identical errors, one ``:SYSTem:ERRor?`` slot: the newest fills it, the older shows."""
    events = [ev(-113, "undefined header", 1_000, seq=1), ev(-113, "undefined header", 2_000, seq=2)]
    out = rows.read_away(LANG, events, [{"code": -113, "message": "undefined header"}], [], "psu")
    assert [(code, message) for code, message, _ in out] == [(-113, "undefined header")]
    assert "2.0" not in out[0][2]  # the origin is the older event's instant, not the queued one


def test_without_a_queue_slot_everything_is_still_readable():
    events = [ev(-113, "undefined header", 1_000, seq=1), ev(0, "no error", 2_000, seq=2)]
    out = rows.read_away(LANG, events, [], [], "psu")
    assert [code for code, _, _ in out] == [-113, 0]  # chronological: the history reads in order


def test_an_identical_event_only_fills_one_slot():
    """Three identical errors, two slots: the two newest fill them, one shows."""
    events = [ev(-222, "out of range", t, seq=n) for n, t in ((1, 1_000), (2, 2_000), (3, 3_000))]
    queue = [{"code": -222, "message": "out of range"}, {"code": -222, "message": "out of range"}]
    out = rows.read_away(LANG, events, queue, [], "psu")
    assert len(out) == 1


def test_the_origin_names_the_owning_command_and_its_source():
    lines = [line(seq=7, command="*IDN?", error_seq=11)]
    events = [ev(-113, "undefined header", 1_000, seq=11)]
    origin = rows.read_away(LANG, events, [], lines, "psu")[0][2]
    assert "*IDN?" in origin and "socket:1" in origin


def test_an_error_no_command_owns_names_the_fault_at_its_instant():
    lines = []
    fault = {"kind": "fault", "reason": "active", "t_ns": 1_000, "source": "socket:1", "fault": "f1"}
    origin = rows.error_origin(LANG, lines, [fault], ev(-300, "tipped", 1_000, seq=99))
    assert "f1" in origin[0]  # "Fault f1 active": named, with its source beside it


def test_another_instrument_events_are_ignored():
    events = [ev(-113, "undefined header", 1_000, seq=1), {"kind": "state", "instrument": "psu"}]
    assert rows.read_away(LANG, events, [], [], "awg") == []


def test_grouped_columns_keep_columns_within_the_width():
    state = {
        "a.one": {"path": "a.one", "label": "One", "value": 1},
        "a.two": {"path": "a.two", "label": "Two", "value": 2},
        "b.one": {"path": "b.one", "label": "Three", "value": 3},
    }
    groups = {"A": ["a.one", "a.two"], "B": ["b.one"]}
    out = rows.grouped_columns(LANG, groups, state, width=16)
    text = ["".join(cell for cell, _ in row) for row in out]
    assert any("A" in t for t in text) and any("B" in t for t in text)
    assert any("one" in t for t in text)  # the path's last segment is the fallback label


def test_a_group_the_budget_has_no_room_for_is_left_out_whole():
    state = {f"k{n}": {"path": f"k{n}", "label": f"L{n}", "value": n} for n in range(6)}
    out = rows.grouped_columns(LANG, {"G": [f"k{n}" for n in range(6)]}, state, width=40, budget=3)
    shown = [t for t in ("".join(c for c, _ in r) for r in out) if t.strip()]
    assert len(shown) <= 3  # title alone above the fold misleads (#374): no partial group


def test_read_away_finds_the_fault_origin_in_the_full_stream():
    """The origin lookup scans the whole event stream (faults included), not the filtered
    error-only list — the preset fault's queue entry still names its source (the demo SVG)."""
    stream = [
        ev(-222, "Device-specific error", 1_000, seq=11),
        {"kind": "fault", "reason": "active", "t_ns": 1_000, "source": "socket:1", "fault": "f1"},
    ]
    out = rows.read_away(LANG, stream, [], [], "psu")
    assert len(out) == 1
    assert "Fault f1" in out[0][2]
