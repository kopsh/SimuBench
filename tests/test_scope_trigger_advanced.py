"""The advanced trigger families (#1184): runt, transition, window, timeout and nedge — the same
steady-state signal the edge search looks at, each family with its own qualification. The instants
come from the search directly, so the mathematics can be asserted exactly."""

from __future__ import annotations

from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.engine.signal import Signal, sine

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "models" / "contract-float-scope" / "bench.yaml"

T0 = 0  # the search's base instant
DT = 1e-6  # 1 us record grid
PRE = 100  # samples of history before the arming instant
POINTS = 10_000
HORIZON = 1.0  # s the search may look ahead

# sine(1 kHz, peak 1 V): the rising zero at 1 ms, the 0.2 V crossings at
# asin(0.2)/2pi ms = 32.06 us and (pi - asin(0.2))/2pi ms = 467.94 us, the 0.8 V ones at
# 147.58 us and 852.42 us; the rise 0.2 -> 0.8 takes 115.52 us.
SINE = sine(1000.0, 1.0)


@pytest.fixture()
def trig():
    s = open_bench(CONTRACT)
    try:
        unit = s.instruments["scope"].components["trigger"]
        s.set_parameter("scope", "adc", "bits", 0)
        unit.overrides.clear()
        unit.reset()
        yield unit
    finally:
        s.close()


def search(trig, sig: Signal = SINE, horizon: float = HORIZON, scale: float = 1.0, pre: int = PRE):
    """One search on the record grid: the instant it triggers at, or None. ``scale`` is the
    source channel's volts per division: the hysteresis band follows it, so a small signal is
    viewed at a small volts/div, as an operator would. ``pre`` sets the arming instant (pre * dt)."""
    hit = trig.search(sig, T0, DT, pre, POINTS, None, scale, horizon)
    trig.reset()  # every search here stands alone: the memo must not answer the next one
    return None if hit is None else hit.t_s


def test_nedge_counts_edges_from_the_first(trig):
    trig.overrides.update(mode="nedge", nedge_count=1)
    assert search(trig) == pytest.approx(1.0e-3, abs=1e-7)  # the first edge the edge trigger takes
    trig.overrides["nedge_count"] = 3
    assert search(trig) == pytest.approx(3.0e-3, abs=1e-6)  # the third one, two periods on
    trig.overrides["nedge_count"] = 7
    assert search(trig) == pytest.approx(7.0e-3, abs=1e-6)  # past the two-period probe: its period


def test_nedge_honours_its_own_slope(trig):
    trig.overrides.update(mode="nedge", nedge_count=1, nedge_slope="negative")
    assert search(trig) == pytest.approx(0.5e-3, abs=1e-7)  # the first falling edge
    # the main slope stays rising: the families keep their own slope where they have one
    trig.overrides.update(mode="edge")
    assert search(trig) == pytest.approx(1.0e-3, abs=1e-7)


def test_timeout_fires_when_the_state_outlasts_its_time(trig):
    trig.overrides.update(mode="timeout", timeout_condition="high", timeout_time=200e-6)
    assert search(trig, Signal.constant(0.5)) == pytest.approx((PRE * DT) + 200e-6, abs=1e-6)
    # low on the same constant: never (the signal is high) …
    trig.overrides["timeout_condition"] = "low"
    assert search(trig, Signal.constant(0.5)) is None
    # … but on a negative one it does
    assert search(trig, Signal.constant(-0.5)) == pytest.approx((PRE * DT) + 200e-6, abs=1e-6)


def test_timeout_unchanged_on_a_wave_that_keeps_changing(trig):
    # each high or low run of the sine lasts half a period: longer than 100 us, shorter than 600
    trig.overrides.update(mode="timeout", timeout_condition="unchanged", timeout_time=100e-6)
    assert search(trig) == pytest.approx((PRE * DT) + 100e-6, abs=1e-6)
    trig.overrides["timeout_time"] = 600e-6
    assert search(trig) is None


def test_runt_fires_on_a_pulse_that_never_reaches_the_high_threshold(trig):
    trig.overrides.update(mode="runt", low_threshold=0.2, high_threshold=0.8)
    # a 0.25 V peak viewed at 0.05 V/div crosses 0.2 V and never 0.8: the hump opens at 147.58 us
    # and closes at 352.42 us (the runt's close is the trigger)
    assert search(trig, sine(1000.0, 0.25), scale=0.05) == pytest.approx(352.418e-6, abs=2e-6)
    # the full 1 V peak crosses 0.8 as well: every hump is a normal pulse
    assert search(trig, SINE, scale=0.05) is None


def test_runt_qualified_by_width(trig):
    sine_low = sine(1000.0, 0.25)
    trig.overrides.update(mode="runt", low_threshold=0.2, high_threshold=0.8, runt_qualified=True)
    trig.overrides["runt_time"] = 100e-6  # the hump lasts 204.8 us: qualified
    assert search(trig, sine_low, scale=0.05) == pytest.approx(352.418e-6, abs=2e-6)
    trig.overrides["runt_time"] = 500e-6  # wider than any hump: none qualifies
    assert search(trig, sine_low, scale=0.05) is None


def test_runt_polarity_negative(trig):
    # below the -0.2 V high threshold the wave dips to -0.25 V and never reaches the -0.8 V low:
    # the dip closes when it rises back through -0.2 V at 852.42 us
    trig.overrides.update(mode="runt", low_threshold=-0.8, high_threshold=-0.2, runt_polarity="negative")
    assert search(trig, sine(1000.0, 0.25), scale=0.05) == pytest.approx(852.418e-6, abs=2e-6)


def test_transition_times_the_edge_between_the_thresholds(trig):
    trig.overrides.update(
        mode="transition",
        transition_type="risetime",
        transition_mode="gthan",
        transition_time=50e-6,
        low_threshold=0.2,
        high_threshold=0.8,
    )
    # the rise 0.2 -> 0.8 takes 115.5 us: a greater-than-50-us violation, at the 0.8 crossing
    assert search(trig) == pytest.approx(147.583e-6 + 1.0e-3, abs=2e-6)
    trig.overrides["transition_mode"] = "lthan"
    trig.overrides["transition_time"] = 200e-6  # 115.5 us is indeed under 200
    assert search(trig) == pytest.approx(147.583e-6 + 1.0e-3, abs=2e-6)
    trig.overrides["transition_mode"] = "gthan"
    trig.overrides["transition_time"] = 200e-6  # no edge of a 1 kHz sine is that slow
    assert search(trig) is None


def test_transition_range(trig):
    trig.overrides.update(
        mode="transition",
        transition_type="risetime",
        transition_mode="range",
        transition_range_low=100e-6,
        transition_range_high=130e-6,
        low_threshold=0.2,
        high_threshold=0.8,
    )
    assert search(trig) == pytest.approx(147.583e-6 + 1.0e-3, abs=2e-6)  # 115.5 us is inside
    trig.overrides["transition_mode"] = "orange"
    assert search(trig) is None  # and outside is not true of it


def test_transition_fall_time(trig):
    trig.overrides.update(
        mode="transition",
        transition_type="falltime",
        transition_mode="gthan",
        transition_time=50e-6,
        low_threshold=0.2,
        high_threshold=0.8,
    )
    # the fall crosses 0.8 V at 352.42 us and 0.2 V at 467.94 us: 115.5 us by the rise-time's
    # symmetry, a violation confirmed at the 0.2 crossing
    assert search(trig) == pytest.approx(467.940e-6, abs=2e-6)


def test_window_enters_and_leaves_at_the_boundary(trig):
    trig.overrides.update(mode="window", low_threshold=-0.2, high_threshold=0.2)
    # armed at 0.1 ms the sine is outside (0.59 V); it enters from above at 467.94 us
    assert search(trig) == pytest.approx(467.940e-6, abs=2e-6)
    trig.overrides["window_condition"] = "exit"  # and leaves downward at 532.06 us
    assert search(trig) == pytest.approx(532.060e-6, abs=2e-6)


def test_window_at_the_timeout_point(trig):
    trig.overrides.update(
        mode="window",
        low_threshold=-0.2,
        high_threshold=0.2,
        window_condition="exit",
        window_tpoint="timeout",
        window_time=100e-6,
    )
    # armed at 500 us the signal is inside the band (v = 0); it leaves at 532.06 us and the
    # outside state holds: the trigger comes 100 us into it
    assert search(trig, pre=500) == pytest.approx(632.060e-6, abs=2e-6)


def test_window_inside_outside_with_duration_qualifiers(trig):
    trig.overrides.update(mode="window", low_threshold=-0.2, high_threshold=0.2)
    trig.overrides.update(window_condition="outside", window_qualifier="gthan", window_time=50e-6)
    assert search(trig) == pytest.approx((PRE * DT) + 50e-6, abs=2e-6)  # outside since the arming
    trig.overrides.update(window_condition="inside", window_qualifier="gthan")
    assert search(trig) == pytest.approx(467.940e-6 + 50e-6, abs=2e-6)  # 50 us into the inside stay
    trig.overrides.update(window_condition="inside", window_qualifier="lthan", window_time=100e-6)
    assert search(trig) == pytest.approx(532.060e-6, abs=2e-6)  # the inside stay lasts 64 us: left before 100


def test_unset_thresholds_leave_the_pulse_families_silent(trig):
    trig.overrides.update(mode="runt")
    assert search(trig) is None
    trig.overrides.update(mode="window")
    assert search(trig) is None
    trig.overrides.update(mode="transition")
    assert search(trig) is None


def test_the_family_source_stays_the_main_one_until_set(trig):
    assert trig.family_source() == str(trig.param("source"))
    trig.overrides["runt_source"] = "CHANnel1"
    trig.overrides["mode"] = "runt"
    assert trig.family_source() == "CHANnel1"
    trig.overrides["mode"] = "edge"
    assert trig.family_source() == str(trig.param("source"))  # the edge family ignores it


def test_hysteresis_setting_selects_the_band(trig):
    scale = 1.0
    trig.overrides.update(hysteresis_setting="normal")
    assert trig._hysteresis(scale) == pytest.approx(0.16 * scale)  # the component default
    trig.overrides.update(hysteresis_setting="nreject")
    assert trig._hysteresis(scale) == pytest.approx(0.5 * scale)  # the noise-reject band
    trig.overrides.update(hysteresis_setting="hsensitivity")
    assert trig._hysteresis(scale) == pytest.approx(0.08 * scale)  # half the normal band
    trig.overrides["hsensitivity_divs"] = 0.02
    assert trig._hysteresis(scale) == pytest.approx(0.02 * scale)  # or its own band
