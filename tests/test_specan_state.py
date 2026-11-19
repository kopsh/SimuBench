"""The swept analyzer's trace state objects (#1524): the RF history and its sweep cache, the hold
state and the settings a read draws with. Pure logic — the trace engine that uses them is covered
by the analyzer tests (test_specan_*, the N9042B manual cases)."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from simubench.engine.signal import sine
from simubench.library.components.specan_state import (
    SWEEP_CACHE_SIZE,
    HoldState,
    LinesCache,
    SweepPlan,
    TraceHistory,
    TraceSettings,
    same_signal,
)

pytestmark = pytest.mark.unit

ONE, TWO, THREE = sine(1e6, 1.0), sine(2e6, 1.0), sine(3e6, 1.0)


def _history() -> TraceHistory:
    h = TraceHistory()
    h.restart_at(100, ONE)
    h.signals += [(200, TWO), (300, THREE)]
    return h


def test_the_history_says_which_signal_was_the_input_at_an_instant():
    h = _history()
    assert [h.index(t) for t in (0, 100, 199, 200, 299, 300, 10**9)] == [0, 0, 0, 1, 1, 2, 2]
    assert h.signal_at(250) is TWO and h.signal_at(5) is ONE  # step: before the first, the first
    assert h.latest() is THREE
    assert TraceHistory().latest() is None and TraceHistory().signal_at(5).freq.size == 0


def test_a_restart_starts_the_history_again_and_drops_the_sweeps_drawn_from_the_old_one():
    h = _history()
    h.keep(0, 0, np.zeros(3))
    h.restart_at(400, TWO)
    assert (h.restart_ns, h.signals, h.sweeps) == (400, [(400, TWO)], {})


def test_the_sweep_cache_starts_over_when_it_is_full():
    h = TraceHistory()
    for s in range(SWEEP_CACHE_SIZE + 1):
        h.keep(s, 0, np.zeros(1))
    assert len(h.sweeps) == SWEEP_CACHE_SIZE + 1  # step: full, but not over the bound yet
    kept = h.keep(SWEEP_CACHE_SIZE + 1, 0, np.ones(1))
    assert list(h.sweeps) == [(SWEEP_CACHE_SIZE + 1, 0)] and kept[0] == 1.0  # step: started over


def test_two_histories_do_not_share_their_lists():
    a, b = TraceHistory(), TraceHistory()
    a.signals.append((1, ONE))
    a.sweeps[(0, 0)] = np.zeros(1)
    assert b.signals == [] and b.sweeps == {}


def test_a_restart_drops_the_running_hold_but_not_a_frozen_single_sweep():
    holds = HoldState(held=np.ones(2), held_upto=7, frozen=np.zeros(2), warned=True)
    holds.clear()
    assert (holds.held, holds.held_upto, holds.warned) == (None, -1, False)
    assert holds.frozen is not None


def test_the_lines_cache_keeps_the_identity_of_what_it_is_keyed_by():
    st = TraceSettings(start=0, stop=1, points=2, rbw=1, sweep=1, att=0, detector="POSitive", nf=0)
    cache = LinesCache()
    cache.sig, cache.st = ONE, st
    assert cache.sig is ONE and cache.st is st  # step: a model copy would break "same read"


def test_settings_that_differ_in_what_a_trace_depends_on_have_different_keys():
    base = {
        "start": 0.0,
        "stop": 1e9,
        "points": 101,
        "rbw": 1e6,
        "sweep": 0.1,
        "att": 10.0,
        "detector": "POSitive",
        "nf": 8.0,
    }
    key = TraceSettings(**base).key()
    assert key == TraceSettings(**base).key()
    for field, other in (("stop", 2e9), ("points", 201), ("rbw", 3e6), ("detector", "SAMPle"), ("nf", 9.0)):
        assert TraceSettings(**{**base, field: other}).key() != key, field
    with pytest.raises(ValidationError):
        TraceSettings(**base).start = 1.0  # type: ignore[misc]  # step: one read's settings do not change


def test_a_sweep_plan_is_a_frozen_value():
    plan = SweepPlan(dt=1e-6, points=8, df=1e3, per=4, segments=2, origin=0.0, cost=16, acquisition=8e-6)
    with pytest.raises(ValidationError):
        plan.segments = 3  # type: ignore[misc]


def test_signals_are_the_same_when_their_lines_are():
    assert same_signal(ONE, sine(1e6, 1.0)) and not same_signal(ONE, TWO)
    assert not same_signal(ONE, sine(1e6, 2.0))
