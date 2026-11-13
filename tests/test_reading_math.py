"""The reading math block (#1041) on its own: each step against its closed form, no instrument."""

from __future__ import annotations

import math

import numpy as np
import pytest

from helpers import raises_with
from simubench.library.components.reading_math import ReadingMath, apply_math, apply_units, outside
from simubench.scpi.errors import ScpiError


def block(**params) -> ReadingMath:
    return ReadingMath("math", params, None, None)  # type: ignore[arg-type]


def test_every_step_off_passes_the_reading_bit_for_bit():
    x = 1.2345678901234567
    assert block().process(x) == x


def test_math_is_m_x_minus_c_over_z():
    assert apply_math(2.0, m=3.0, c=1.0, z=4.0) == (3.0 * 2.0 - 1.0) / 4.0


def test_a_factor_that_is_off_is_left_out():
    assert apply_math(2.0, m=None, c=0.5, z=None) == 1.5


def test_a_zero_divisor_is_refused():
    with raises_with(ScpiError, "divisor z is 0"):
        apply_math(1.0, m=None, c=None, z=0.0)


def test_percent_db_and_dbm_units():
    assert apply_units(0.25, "PERCent") == 25.0
    assert apply_units(10.0, "DB") == pytest.approx(20.0)
    # 0.2236 V into 50 Ω is 1 mW: 0 dBm
    assert apply_units(math.sqrt(1e-3 * 50.0), "DB50") == pytest.approx(0.0, abs=1e-12)
    assert apply_units(math.sqrt(1e-3 * 600.0), "DB600") == pytest.approx(0.0, abs=1e-12)


def test_limits_test_only_the_enabled_side():
    assert outside(5.0, lower=None, upper=4.0)
    assert not outside(-5.0, lower=None, upper=4.0)
    assert outside(-5.0, lower=-4.0, upper=None)


def test_the_math_steps_apply_in_order_through_the_block():
    b = block(math=True, m=2.0, m_on=True, c=1.0, c_on=True, units="PERCent", units_on=True)
    assert b.process(0.75) == pytest.approx(100.0 * (2.0 * 0.75 - 1.0))


def test_the_moving_average_is_the_mean_of_the_last_n_readings():
    b = block(average=True, average_count=3)
    out = [b.process(x) for x in (1.0, 2.0, 3.0, 4.0)]
    assert out == [pytest.approx(1.0), pytest.approx(1.5), pytest.approx(2.0), pytest.approx(3.0)]


def test_a_reading_outside_a_limit_latches_the_fail_flag_until_cleared():
    b = block(limits=True, upper=1.0, upper_on=True)
    b.process(2.0)
    b.process(0.5)
    assert b.failed
    b.op_limit_clear()
    assert not b.failed


def test_statistics_of_the_reported_readings():
    b = block(statistics=True, math=True, c=1.0, c_on=True)
    xs = [1.0, 2.0, 4.0, 7.0]
    for x in xs:
        b.process(x)
    ys = np.array(xs) - 1.0
    stats = b.statistics()
    assert stats["count"] == 4
    assert stats["mean"] == pytest.approx(float(ys.mean()))
    assert stats["sdev"] == pytest.approx(float(ys.std(ddof=1)))
    assert (stats["minimum"], stats["maximum"], stats["span"]) == (0.0, 6.0, 6.0)


def test_an_overload_passes_unchanged_and_stays_out_of_the_statistics():
    b = block(statistics=True, math=True, c=1.0, c_on=True, overload_value=9.9e37)
    assert b.process(9.9e37) == 9.9e37
    assert b.statistics()["count"] == 0
