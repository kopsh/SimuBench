"""The independent noise reference (refcalc.noise) against a direct simulation."""

from __future__ import annotations

import math

import numpy as np
import pytest
from refcalc.noise import averaged_codes_sigma, quantized_sigma


@pytest.mark.parametrize(("ratio", "level"), [(0.54, 0.0), (0.54, 0.5), (0.3, 0.0), (2.0, 0.0), (8.0, 0.25)])
def test_quantized_sigma_is_the_spread_of_the_rounded_noise(ratio, level):
    lsb, sigma = 1.0, ratio
    x = np.random.default_rng(7).normal(level, sigma, 2_000_000)
    simulated = float(np.std(np.round(x)))
    assert quantized_sigma(sigma, lsb, level=level) == pytest.approx(simulated, rel=2e-3)


def test_it_is_the_usual_sum_in_power_when_the_noise_spans_several_codes():
    assert quantized_sigma(3.0, 1.0) == pytest.approx(math.sqrt(3.0**2 + 1 / 12), rel=1e-6)
    # Near one code it is not: the case of the scope noise on a quiet division (issue 24's acceptance).
    assert quantized_sigma(0.54, 1.0) < 0.995 * math.sqrt(0.54**2 + 1 / 12)


def test_an_average_of_codes_averages_the_quantization_down_too():
    """G4 (D46): a scope's average is the mean of N quantized acquisitions, so the lsb^2 / 12 of
    the quantization averages down with the noise - unlike an analog average taken before the
    ADC, which would leave all of it in every mean."""
    x = np.random.default_rng(7).normal(0, 3.0, (4, 2_000_000))
    simulated = float(np.std(np.round(x).mean(axis=0)))
    assert averaged_codes_sigma(3.0, 1.0, 4) == pytest.approx(simulated, rel=2e-3)
    assert averaged_codes_sigma(3.0, 1.0, 4) == pytest.approx(math.sqrt((3.0**2 + 1 / 12) / 4), rel=1e-9)
