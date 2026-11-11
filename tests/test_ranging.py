"""The shared range-selection rules (issue 634): one ranges/fitting/autorange implementation
behind the DMM conditioning, the SMU and the DAQ — with the meters' missing-function refusal as
an explicit policy."""

from __future__ import annotations

import pytest

from helpers import raises_with
from simubench.library.components import ranging

pytestmark = pytest.mark.unit


def test_a_function_the_dict_does_not_name_depends_on_the_policy():
    """``missing="empty"``: the function does not range (the SMU's reading). ``missing="error"``:
    a ranges dict that skips a selectable function is a definition error (the DMM's refusal,
    message unchanged)."""
    spec = {"VOLT": [0.1, 1.0]}
    assert ranging.ranges_of(spec, "CURR") == []
    with raises_with(ValueError, "dmm/acq: ranges have no entry for the CURR function"):
        ranging.ranges_of(spec, "CURR", missing="error", owner="dmm/acq")
    assert ranging.ranges_of(None, "VOLT") == []
    assert ranging.ranges_of([1.0, 0.1], "VOLT") == [0.1, 1.0]


def test_the_hysteresis_band_holds_and_outside_takes_the_smallest_fit():
    ranges = [0.1, 1.0, 10.0]
    assert ranging.auto_range(ranges, 1.0, 0.95, 1.2, 0.1) == 1.0  # inside the band: stays
    assert ranging.auto_range(ranges, 1.0, 2.5, 1.2, 0.1) == 10.0  # above it: up
    assert ranging.auto_range(ranges, 1.0, 0.05, 1.2, 0.1) == 0.1  # below it: down
    assert ranging.auto_range(ranges, None, 0.5, 1.2, 0.1) == 1.0  # no range yet: take the fit


def test_fitting_range_takes_the_smallest_that_fits_the_value():
    ranges = [0.1, 1.0, 10.0]
    assert ranging.fitting_range(ranges, 0.09) == 0.1
    assert ranging.fitting_range(ranges, 1.0) == 1.0  # the bound itself fits (the 1e-12 tolerance)
    assert ranging.fitting_range(ranges, 50.0) == 10.0  # nothing fits: the largest
    assert ranging.fitting_range([], 1.0) is None
