"""The ADC code rule (issue 634): one volts→codes kernel behind the scope's acquisition-time
quantization (``scope_adc.quantize``, codes kept back in volts) and the waveform transfer's output
word (``WaveformTransfer.codes``, codes aligned to the wire's width).

The conversion itself is the hardware's single rule: round ``volts + offset`` scaled into codes
per division, clipped to the signed span ``-2^(bits-1) .. 2^(bits-1) - 1``. ``offset_sign`` (add |
subtract) stays a per-component declaration — it is the vendor's offset convention, and the
oscilloscope template mirrors it into both components so the acquisition and the wire see the same
codes (issue 634: removing it would fork the vendor's math into every definition instead).
"""

from __future__ import annotations

import numpy as np


def codes(volts: np.ndarray, scale: float, offset: float, per_div: float, bits: int) -> np.ndarray:
    """The signed ADC codes of ``volts`` (float, whole by construction): the caller casts, shifts
    to the output width or re-offsets to unsigned — the ring itself is decided here."""
    half = 1 << (bits - 1)
    return np.clip(np.round((volts + offset) / scale * per_div), -half, half - 1)
