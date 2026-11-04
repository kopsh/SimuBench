"""The scope timebase's power-of-two rates and a set sample rate (#602: the Infiniium UXR's
128 GSa/s divided by powers of two, :ACQuire:SRATe taking precedence over the memory depth)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simubench.library.components.scope import ScopeTimebase

TOP = 128e9


def timebase(**params) -> ScopeTimebase:
    return ScopeTimebase("tb", {"scale": 100e-9, **params}, None, SimpleNamespace())  # type: ignore[arg-type]


@pytest.mark.unit
def test_pow2_rounds_the_points_rate_down_to_top_over_a_power_of_two():
    assert timebase(record_length="rate_pow2", points=128_000).sample_rate(TOP) == TOP
    assert timebase(record_length="rate_pow2", points=100_000).sample_rate(TOP) == TOP / 2  # 100 GSa/s -> 64
    assert (
        timebase(record_length="rate_pow2", points=1000).sample_rate(TOP) == TOP / 128
    )  # 1 GSa/s -> 1 GSa/s
    assert timebase(record_length="rate_pow2", points=10**9).sample_rate(TOP) == TOP  # capped at the top


@pytest.mark.unit
def test_pow2_records_are_rate_times_the_window_samples_one_period_apart():
    points, dt, pre, post = timebase(record_length="rate_pow2", points=100_000).timing(TOP)
    assert (points, pre + post) == (64_000, 64_000)
    assert dt == pytest.approx(1 / 64e9)


@pytest.mark.unit
def test_a_set_rate_wins_over_the_points_and_rounds_up_to_the_next_step():
    tb = timebase(record_length="rate_pow2", points=1000, sample_rate=20e9)
    assert tb.sample_rate(TOP) == 32e9  # the next fastest: 128 / 4
    assert tb.timing(TOP)[0] == 32_000
    assert timebase(record_length="rate_pow2", sample_rate=1e12).sample_rate(TOP) == TOP
    assert timebase(record_length="exact", points=1000, sample_rate=20e9).sample_rate(TOP) == 20e9


@pytest.mark.unit
def test_a_set_rate_is_lowered_to_fit_the_most_points():
    # 128 GSa/s over 10 us is 1.28 M points; with room for 1 M the rate steps down to 64 GSa/s
    tb = timebase(scale=1e-6, record_length="rate_pow2", sample_rate=TOP, max_points=1_000_000)
    assert tb.sample_rate(TOP) == 64e9


@pytest.mark.unit
def test_the_1_2_5_ladder_rounds_a_set_rate_up():
    assert timebase(record_length="rate_ladder", sample_rate=3e9).sample_rate(TOP) == 5e9
    assert timebase(record_length="rate_ladder", sample_rate=2e9).sample_rate(TOP) == 2e9
    assert timebase(record_length="rate_ladder", points=30_000, scale=1e-3).sample_rate(TOP) == 2e6
