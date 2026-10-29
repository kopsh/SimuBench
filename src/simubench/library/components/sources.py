"""Signal sources."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine.component import Component, PortSpec, component
from ...engine.signal import MAX_LINES, FidelityNote, Signal, pulse_train, sine, square, triangle
from ...scpi.errors import ScpiError


@component("function_generator_core")
class FunctionGeneratorCore(Component):
    """Standard waveforms with phase-continuous frequency changes.

    Parameters: ``function`` (SINusoid, SQUare, RAMP, DC; Siglent's SINE, SQUARE are the same),
    ``frequency`` (Hz), ``amplitude`` (Vpp), ``offset`` (V), ``phase`` (degrees), ``duty`` (% high,
    square), ``symmetry`` (% rising, ramp; 100 is a sawtooth), ``bandwidth`` (Hz, harmonic limit for
    square and ramp), ``square_max`` (Hz, highest square/ramp frequency), ``amplitude_unit`` (what
    ``amplitude`` means: VPP, VPK or VRMS of the waveform).
    """

    ports = {"out": PortSpec("out", "signal")}
    defaults = {
        "function": "SINusoid",
        "frequency": 1000.0,
        "amplitude": 0.1,
        "offset": 0.0,
        "phase": 0.0,
        "bandwidth": 100e6,
        "square_max": math.inf,
        "duty": 50.0,
        "symmetry": 100.0,
        "amplitude_unit": "VPP",
    }
    #: Other instruments' names for the same shapes (Siglent SDG: C1:BSWV WVTP,SINE).
    ALIASES = {"SINE": "SINusoid", "SQUARE": "SQUare"}
    #: Peak-to-peak volts per RMS volt, by function (``amplitude_unit: VRMS``).
    VPP_PER_VRMS = {"SINusoid": 2 * math.sqrt(2), "SQUare": 2.0, "RAMP": 2 * math.sqrt(3)}

    def vpp(self, fn: str) -> float:
        """The amplitude as volts peak-to-peak."""
        amp = float(self.param("amplitude"))
        unit = str(self.param("amplitude_unit")).upper()
        if unit == "VPK":
            return 2 * amp
        if unit == "VRMS":
            return amp * self.VPP_PER_VRMS.get(fn, self.VPP_PER_VRMS["SINusoid"])
        return amp

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._t0 = 0
        self._phase0 = 0.0

    def reset(self) -> None:
        self._t0 = self.session.now_ns
        self._phase0 = 0.0

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = self.bound_paths()
        if path == bound.get("frequency") and old is not None:
            now = self.session.now_ns
            self._phase0 = (self._phase0 + 2 * math.pi * float(old) * (now - self._t0) * 1e-9) % (2 * math.pi)
            self._t0 = now
        fn = self.ALIASES.get(self.param("function"), self.param("function"))
        freq = float(self.param("frequency"))
        if fn in ("SQUare", "RAMP") and freq > float(self.param("square_max")):
            raise ScpiError(-221, f"{fn} is limited to {self.param('square_max'):g} Hz")

    def output(self, port: str) -> Signal:
        fn = self.ALIASES.get(self.param("function"), self.param("function"))
        offset = float(self.param("offset"))
        if fn == "DC":
            return Signal.constant(offset)
        freq = float(self.param("frequency"))
        peak = self.vpp(fn) / 2.0
        phase = self._phase0 + math.radians(float(self.param("phase")))
        if fn == "SINusoid":
            sig = sine(freq, peak, phase, self._t0)
        elif fn == "SQUare":
            duty = float(self.param("duty")) / 100.0
            bw = float(self.param("bandwidth"))
            sig = (
                square(freq, peak, bw, phase, self._t0)
                if duty == 0.5
                else pulse_train(freq, peak, duty, bw, phase, self._t0)
            )
        elif fn == "RAMP":
            sym = float(self.param("symmetry")) / 100.0
            sig = triangle(freq, peak, sym, float(self.param("bandwidth")), phase, self._t0)
        else:
            raise ScpiError(-224, f"function {fn!r} not supported")
        return sig + Signal.constant(offset)


@component("arb_table")
class ArbTable(Component):
    """An arbitrary waveform: a table played at ``rate`` samples per second, linearly interpolated.

    The output is periodic with fundamental ``rate / N``. Linear interpolation between samples is a
    convolution with a triangle, so harmonic k carries ``X[k mod N] / N · sinc²(k / N)``: the
    spectrum is exact, not sampled (validation-lockin.md 6). Harmonics are kept up to ``max_lines``.
    """

    ports = {"out": PortSpec("out", "signal")}
    defaults = {"table": [0.0], "rate": 16000.0, "scale": 1.0, "max_lines": 1024}

    def output(self, port: str) -> Signal:
        table = np.asarray(self.param("table"), dtype=float) * float(self.param("scale"))
        n = table.size
        rate = float(self.param("rate"))
        if n == 0 or rate <= 0:
            return Signal.zero()
        spectrum = np.fft.fft(table) / n
        dc = float(spectrum[0].real)
        k = np.arange(1, int(self.param("max_lines")) + 1)
        coef = spectrum[k % n] * np.sinc(k / n) ** 2
        keep = np.abs(coef) > 1e-15
        # v(t) = Σ 2·Re(c_k e^{i2πk f0 t}) for a real signal: peak phasor 2·c_k. Sample j sits at t = j/rate.
        sig = Signal.lines(k[keep] * rate / n, 2 * coef[keep])
        notes = (FidelityNote("truncated", f"arbitrary table: {int(keep.sum())} harmonics"),)
        return Signal(sig.freq, sig.amp, sig.t0_ns, dc, notes)


# Feedback taps of maximal-length sequences: x^n + x^k + 1.
PRBS_TAPS = {5: 3, 6: 5, 7: 6, 9: 5, 10: 7, 11: 9, 15: 14}


def prbs_sequence(order: int) -> np.ndarray:
    """One period (2^n − 1 bits) of the maximal-length sequence of ``order``, as ±1."""
    tap = PRBS_TAPS[order]
    state = (1 << order) - 1
    bits = np.empty((1 << order) - 1, dtype=float)
    for i in range(bits.size):
        bits[i] = 1.0 if state & 1 else -1.0
        fb = ((state >> (order - 1)) ^ (state >> (tap - 1))) & 1
        state = ((state << 1) | fb) & ((1 << order) - 1)
    return bits


@component("prbs_source")
class PrbsSource(Component):
    """A pseudo-random bit stream, NRZ at ``rate`` bits/s between ±``level``.

    Periodic with 2^n − 1 bits, so its spectrum is lines at k·rate/N: the DFT of the sequence times
    the rectangular pulse's sinc. Exact, not estimated by an FFT of samples (D13); lines are kept
    for ``lobes`` sinc lobes, within the line limit.
    """

    ports = {"out": PortSpec("out", "signal")}
    defaults = {"order": 7, "rate": 100e3, "level": 0.25, "lobes": 8}

    def output(self, port: str) -> Signal:
        order = int(self.param("order"))
        if order not in PRBS_TAPS:
            raise ScpiError(-224, f"PRBS order {order} not supported; one of {sorted(PRBS_TAPS)}")
        seq = prbs_sequence(order) * float(self.param("level"))
        n = seq.size
        rate = float(self.param("rate"))
        spec = np.fft.fft(seq) / n
        m_max = min(int(self.param("lobes")) * n, MAX_LINES)
        m = np.arange(1, m_max + 1)
        coef = spec[m % n] * np.sinc(m / n) * np.exp(-1j * np.pi * m / n)
        keep = np.abs(coef) > 1e-15
        sig = Signal.lines(m[keep] * rate / n, 2 * coef[keep])
        notes = (FidelityNote("truncated", f"PRBS: lines kept up to {m_max * rate / n:.6g} Hz"),)
        return Signal(sig.freq, sig.amp, sig.t0_ns, float(spec[0].real), notes)
