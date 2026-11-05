"""The ADC code kernel (issue 634): one volts→codes rule behind the scope's quantization and the
waveform transfer's output word."""

from __future__ import annotations

import numpy as np
import pytest

from simubench.library.components import adc

pytestmark = pytest.mark.unit


def test_codes_round_and_clip_to_the_signed_span():
    volts = np.array([-1.0, -0.95, 0.0, 0.95, 1.0])  # 1 V/div, 8 codes/div: -8 .. 7
    codes = adc.codes(volts, 1.0, 0.0, 8.0, 4)
    assert list(codes) == [-8, -8, 0, 7, 7]  # 7.6 rounds to 8, the clip to 2^(bits-1) - 1
    assert codes.dtype == np.float64  # whole by construction; the caller casts


def test_the_offset_enters_before_the_scaling():
    codes = adc.codes(np.array([0.0]), 1.0, 0.5, 8.0, 4)
    assert codes[0] == 4.0  # half a division up
