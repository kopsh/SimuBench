"""Analog-to-digital conversion: the code rule and the generic converter.

The ADC code rule (issue 634): one volts→codes kernel behind the scope's acquisition-time
quantization (``scope_adc.quantize``, codes kept back in volts) and the waveform transfer's output
word (``WaveformTransfer.codes``, codes aligned to the wire's width).

The conversion itself is the hardware's single rule: round ``volts + offset`` scaled into codes
per division, clipped to the signed span ``-2^(bits-1) .. 2^(bits-1) - 1``. ``offset_sign`` (add |
subtract) stays a per-component declaration — it is the vendor's offset convention, and the
oscilloscope template mirrors it into both components so the acquisition and the wire see the same
codes (issue 634: removing it would fork the vendor's math into every definition instead).

``adc`` is the generic single-input converter with a fixed full scale (signal-chain.md 3.3, #955):
a spectrum analyzer's IF ADC, later a digitizer. The scope's ADC group (``scope_adc``, scope.py)
shares the code rule above and nothing else — its full scale follows the vertical setting.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine import rfcascade
from ...engine.component import Component, PortSpec, RfStage, component
from ...engine.noise import gaussian, stream_key
from ...engine.stream import FrameRequest, SampleFormat, SampleFrame


def codes(volts: np.ndarray, scale: float, offset: float, per_div: float, bits: int) -> np.ndarray:
    """The signed ADC codes of ``volts`` (float, whole by construction): the caller casts, shifts
    to the output width or re-offsets to unsigned — the ring itself is decided here."""
    half = 1 << (bits - 1)
    return np.clip(np.round((volts + offset) / scale * per_div), -half, half - 1)


def analog_chain(block: Any) -> list[Any]:
    """The analog RF stages that end in ``block``, input first: ``block`` and every ``RfStage``
    found walking the wiring upstream from its ``in`` (signal-chain.md 5.2). Whoever needs the
    chain's figures — a spectrum analyzer's trace, its ADC — combines these with
    ``engine.rfcascade.cascade``; there is one walk and one cascade."""
    blocks: list[Any] = []
    while isinstance(block, RfStage) and block not in blocks:
        blocks.append(block)
        owner = block.owner
        src = None if owner is None else owner.wiring.source(block.name, "in")
        if src is None or owner is None:
            break
        block = owner.components[src[0]]
    return blocks[::-1]


@component("adc")
class Adc(Component):
    """A sampling ADC with one input and a fixed full scale (signal-chain.md 3.3): where an analog
    signal becomes samples.

    ``in`` takes the analog signal, ``out`` gives its sample stream at the rate asked of it (its
    own ``rate`` when nobody says: a probe). A frame is made in this order:

    1. the signal is evaluated at the request's instants — at the tuning the request names, when
       it names one (a receiver's IF depends on where its oscillator is);
    2. the noise of the analog chain ahead becomes samples: the cascaded noise figure and gain of
       the ``RfStage`` blocks found walking the wiring upstream, as white noise of that density
       over the Nyquist band, kT0·F·G·(rate/2) into ``impedance``, drawn from the request's random
       stream. A chain that declares no noise figure adds none;
    3. the converter's own noise (``noise_lsb_rms``), from a stream of its own;
    4. the sum is quantized to ``bits`` over ±``full_scale`` and clipped there.

    The samples are the codes, kept in volts (D46, as the scope's): the format says how many bits
    and what one code step is worth. ``bits`` 0 does not quantize (and cannot clip). The request's
    interval is the real one — the block that starts the read holds the clock and its error."""

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "samples")}
    defaults = {
        "bits": 16,  # resolution (0: not quantized)
        "full_scale": 1.0,  # V, the peak input the codes span (±full_scale)
        "rate": 100.0e6,  # Sa/s, the converter's clock
        "noise_lsb_rms": 0.0,  # the converter's own noise, in code steps rms
        "impedance": 50.0,  # ohm, what the input voltage is across: turns the chain's noise power into volts
    }
    units = {"full_scale": "V", "rate": "Sa/s", "impedance": "Ohm"}

    def lsb(self) -> float:
        """What one code step is worth, V (0.0 when not quantized)."""
        bits = int(self.param("bits"))
        return 2.0 * float(self.param("full_scale")) / (1 << bits) if bits > 0 else 0.0

    def analog_noise_rms(self, dt_s: float, f_hz: float) -> float:
        """The analog chain's noise at this input over the Nyquist band of samples ``dt_s`` apart,
        V rms: kT0 · the chain's noise figure · its gain · 1/(2·dt), into ``impedance``."""
        assert self.owner is not None
        src = self.owner.wiring.source(self.name, "in")
        if src is None:
            return 0.0
        stages = [block.stage(f_hz) for block in analog_chain(self.owner.components[src[0]])]
        if not stages:
            return 0.0
        chain = rfcascade.cascade(stages)
        if chain.noise_figure_db is None:
            return 0.0
        dbm = rfcascade.noise_floor_dbm(chain.noise_figure_db, 0.5 / dt_s) + chain.gain_db
        return math.sqrt(10 ** (dbm / 10) * 1e-3 * float(self.param("impedance")))

    def frames(self, port: str, request: FrameRequest) -> SampleFrame:
        tune = request.tune_hz if request.tune_hz is not None else self.session.tune_hz
        with self.session.tuned(tune):
            volts = self.input("in").sample_rel(request.t0_ns, request.offsets_s())
            sigma = self.analog_noise_rms(request.dt_s, 0.0 if tune is None else tune)
        indexes = request.indexes()
        if sigma > 0:
            volts = volts + sigma * gaussian(stream_key(self.session.seed, *request.key), indexes)
        bits, lsb = int(self.param("bits")), self.lsb()
        own = float(self.param("noise_lsb_rms")) * lsb
        if own > 0:
            volts = volts + own * gaussian(
                stream_key(self.session.seed, *request.key, self.uid, "adc"), indexes
            )
        clipped = False
        if bits > 0:
            kept = codes(volts, lsb, 0.0, 1.0, bits)
            half = 1 << (bits - 1)
            clipped = bool(np.max(kept) >= half - 1 or np.min(kept) <= -half)
            volts = kept * lsb
        return SampleFrame(
            volts, request.t0_ns, request.dt_s, SampleFormat("volts", bits, lsb), clipped=clipped
        )

    def sample_interval(self, port: str) -> float:
        return 1.0 / float(self.param("rate"))
