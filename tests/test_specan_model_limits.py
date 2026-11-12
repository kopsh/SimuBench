"""The spectrum-analyzer blocks' model limits (#616): filter steps and ranges, the Normal and
negative-peak detectors and the automatic detector choice. Defaults keep the N9320B / sb-sa-1
behaviour (``tests/test_specan_migration.py`` holds its golden replies); the X-Series values are
the ones the N9042B definition declares (#336)."""

from __future__ import annotations

import numpy as np
import pytest

from simubench.library.components.specan import SpecanDetector
from simubench.library.components.specan_backend import (
    RBW_LADDER,
    SpecanRbw,
    filter_ladder,
    line_response,
    snap_filter,
    snap_rbw,
)


def _rbw(**params) -> SpecanRbw:
    """The resolution filter: the RBW steps and limits are its own since #955."""
    return SpecanRbw("rbw", params, None, None)  # type: ignore[arg-type]


def _det(**params) -> SpecanDetector:
    return SpecanDetector("detector", params, None, None)  # type: ignore[arg-type]


def test_default_ladder_is_the_1_3_10_ladder():
    assert filter_ladder("1-3-10", 1.0, 3e6) == RBW_LADDER
    for value in (0.4, 2.0, 2.5e3, 7e5, 9e9):
        assert snap_filter(value, "1-3-10", 1.0, 3e6) == snap_rbw(value)


def test_ten_percent_steps_reach_8_mhz():
    ladder = filter_ladder("10%", 1.0, 8e6)
    assert ladder[:4] == [1.0, 1.1, 1.2, 1.3]
    assert ladder[-5:] == [3e6, 4e6, 5e6, 6e6, 8e6]
    assert len([r for r in ladder if 1e3 <= r < 1e4]) == 24  # E24: 24 steps a decade


def test_ten_percent_snap_is_linear_and_rounds_up_on_a_tie():
    assert snap_filter(4.5e6, "10%", 1.0, 8e6) == 5e6  # tie between 4 and 5 MHz goes up
    assert snap_filter(1.04e3, "10%", 1.0, 8e6) == 1.0e3
    assert snap_filter(7.1e6, "10%", 1.0, 8e6) == 8e6


def test_wide_open_setting_above_the_ladder():
    ladder = filter_ladder("10%", 1.0, 50e6)
    assert ladder[-2:] == [8e6, 50e6]
    assert snap_filter(40e6, "10%", 1.0, 50e6) == 50e6


def test_auto_rbw_uses_the_ratio_and_stays_below_auto_max():
    x = _rbw(steps="10%", rbw_max=8e6, auto_ratio=106.0, auto_max=3e6, span=26.5e9)
    assert x.rbw() == 3e6  # full span: capped at 3 MHz (the X-Series preset)
    x = _rbw(steps="10%", rbw_max=8e6, auto_ratio=106.0, auto_max=3e6, span=1e6)
    assert x.rbw() == pytest.approx(9.1e3)  # 1 MHz / 106 = 9.43 kHz -> nearest step 9.1 kHz


def test_default_auto_rbw_unchanged():
    assert _rbw(span=2e6).rbw() == 3e4
    assert _rbw(span=7.5e9).rbw() == 3e6


def test_negative_peak_detector_takes_the_cell_minimum():
    power = np.array([[1.0, 4.0, 2.0], [3.0, 3.0, 5.0]])
    out = _det(detector="NEGative").detect(np.sqrt(power), power)
    assert out == pytest.approx(10 * np.log10([1.0, 3.0]))


def test_normal_detector_alternates_on_noise_and_peaks_on_a_line():
    noise = np.array([[1.0, 10.0], [1.0, 10.0], [1.0, 10.0], [1.0, 10.0]])  # 10 dB spread
    out = _det(detector="NORMal").detect(np.sqrt(noise), noise)
    assert out == pytest.approx([10.0, 0.0, 10.0, 0.0])  # even max, odd min
    line = np.array([[100.0, 110.0], [100.0, 110.0]])  # 0.4 dB spread: CW-like
    out = _det(detector="NORMal").detect(np.sqrt(line), line)
    assert out == pytest.approx(10 * np.log10([110.0, 110.0]))


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("WRITe", "NORMal"), ("AVERage", "NORMal"), ("MAXHold", "POSitive"), ("MINHold", "NEGative")],
)
def test_automatic_detector_follows_the_trace_mode(mode, expected):
    assert _det(detector="SAMPle", auto=True, trace_mode=mode).in_use() == expected


def test_manual_detector_ignores_the_trace_mode():
    assert _det(detector="SAMPle", auto=False, trace_mode="MAXHold").in_use() == "SAMPle"


def test_negative_peak_line_response_is_the_far_edge_of_the_bucket():
    d = np.array([0.0])
    k = 4 * np.log(2) / 1e3**2
    assert line_response(d, 1e3, 2e3, "NEGative") == pytest.approx(np.exp(-k * 1e3**2))
    assert line_response(d, 1e3, 2e3, "NORMal") == pytest.approx(1.0)
