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
        "function": "SINusoid",  # SINusoid | SQUare | RAMP | DC (SINE, SQUARE are aliases)
        "frequency": 1000.0,  # Hz
        "amplitude": 0.1,  # in amplitude_unit
        "offset": 0.0,  # V
        "phase": 0.0,  # degrees
        "bandwidth": 100e6,  # Hz, harmonic limit for square and ramp
        "square_max": math.inf,  # Hz, highest square/ramp frequency (inf: none)
        "duty": 50.0,  # percent high time of the square
        "symmetry": 100.0,  # percent rising half of the ramp (100: sawtooth)
        "amplitude_unit": "VPP",  # VPP | VPK | VRMS: what amplitude means
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


# ---- the signal source as modules (instrument-authoring.md 8, #144) ---------------------------------
#
# waveform_synth (the shape at 1 Vpp, phase-continuous) -> amplitude_level (the amplitude in its unit
# and the offset, defined for the load setting) -> output_stage (output impedance, on/off); a
# reference_clock sets the synthesizer's frequency error. function_generator_core is the same chain in
# one component, kept for the definitions written with it.


@component("reference_clock")
class ReferenceClock(Component):
    """The source's frequency reference: an internal oscillator off by ``ppm`` (hidden; a deviations
    entry), or with ``source`` EXTernal the 10 MHz on ``ref`` when one within ±10 ppm is there (the
    internal oscillator otherwise, as a real source falls back when it cannot lock)."""

    ports = {"ref": PortSpec("ref", "signal")}
    defaults = {
        "source": "INTernal",  # INTernal | EXTernal (a setting such as $clock_source)
        "ppm": 0.0,  # the internal oscillator's frequency error, ppm (hidden)
        "nominal_hz": 10e6,  # the external reference's frequency
    }

    def error(self) -> float:
        """The fractional frequency error every synthesized frequency carries."""
        if str(self.param("source")).upper().startswith("EXT"):
            nominal = float(self.param("nominal_hz"))
            sig = self.input("ref").merged()
            if sig.freq.size:
                k = int(np.argmax(np.abs(sig.amp)))
                f = float(sig.freq[k])
                if abs(f - nominal) <= nominal * 10e-6 and abs(sig.amp[k]) > 0.05:
                    return f / nominal - 1
        return float(self.param("ppm")) * 1e-6


@component("waveform_synth")
class WaveformSynth(Component):
    """A standard waveform at 1 Vpp around zero, phase-continuous across frequency changes.

    ``function``: SINusoid, SQUare (with ``duty``: a pulse when it is not 50 %), RAMP (``symmetry`` %
    rising; 50 is a triangle), DC (zero: the level module adds the offset). ``duty`` is a percentage or
    a ratio as ``duty_unit`` declares; nothing is guessed from its size (0.1 is 0.1 % or 10 %, by the
    declaration). Square, pulse and ramp keep harmonics up to ``bandwidth`` (and the line limit, with a
    fidelity note, D33). ``clock`` names a reference_clock whose error scales the frequency.
    """

    ports = {"out": PortSpec("out", "signal")}
    refs = {"clock": ("reference_clock", "instrument")}
    optional_refs = frozenset({"clock"})
    defaults = {
        "function": "SINusoid",  # SINusoid | SQUare | RAMP | DC (SINE, SQUARE, PULSe, TRIangle as below)
        "frequency": 1000.0,  # Hz
        "phase": 0.0,  # degrees
        "duty": 50.0,  # high time of a square or pulse, in duty_unit
        "duty_unit": "percent",  # percent | ratio: what duty means
        "symmetry": 100.0,  # percent rising half of the ramp (100: sawtooth)
        "bandwidth": 100e6,  # Hz, harmonic limit for square, pulse and ramp
        "square_max": math.inf,  # Hz, highest square/ramp frequency (inf: none)
        "clock": "",  # the reference_clock component (empty: an exact clock)
    }
    units = {"frequency": "Hz", "bandwidth": "Hz", "square_max": "Hz", "phase": "deg"}
    #: Other names for the same shapes.
    ALIASES = {
        "SINE": "SINusoid",
        "SIN": "SINusoid",
        "SQUARE": "SQUare",
        "PULSE": "SQUare",
        "PULSe": "SQUare",
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._t0 = 0
        self._phase0 = 0.0

    def reset(self) -> None:
        self._t0 = self.session.now_ns
        self._phase0 = 0.0

    def function(self) -> str:
        fn = str(self.param("function"))
        if fn.upper().startswith("TRI"):  # TRIangle: a ramp of 50 % symmetry
            return "RAMP"
        return self.ALIASES.get(fn, self.ALIASES.get(fn.upper(), fn))

    def duty_ratio(self) -> float:
        duty = float(self.param("duty"))
        return duty if str(self.param("duty_unit")).lower() == "ratio" else duty / 100.0

    def frequency(self) -> float:
        freq = float(self.param("frequency"))
        clock = str(self.param("clock") or "")
        if clock and self.owner is not None:
            freq *= 1.0 + self.owner.components[clock].error()
        return freq

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = self.bound_paths()
        if path == bound.get("frequency") and old is not None:
            now = self.session.now_ns
            self._phase0 = (self._phase0 + 2 * math.pi * float(old) * (now - self._t0) * 1e-9) % (2 * math.pi)
            self._t0 = now
        fn = self.function()
        if fn in ("SQUare", "RAMP") and float(self.param("frequency")) > float(self.param("square_max")):
            raise ScpiError(-221, f"{fn} is limited to {self.param('square_max'):g} Hz")

    def output(self, port: str) -> Signal:
        fn = self.function()
        if fn == "DC":
            return Signal.zero()
        freq = self.frequency()
        phase = self._phase0 + math.radians(float(self.param("phase")))
        bw = float(self.param("bandwidth"))
        if fn == "SINusoid":
            return sine(freq, 0.5, phase, self._t0)
        if fn == "SQUare":
            duty = self.duty_ratio()
            if duty == 0.5:
                return square(freq, 0.5, bw, phase, self._t0)
            return pulse_train(freq, 0.5, duty, bw, phase, self._t0)
        if fn == "RAMP":
            sym = (
                50.0
                if str(self.param("function")).upper().startswith("TRI")
                else float(self.param("symmetry"))
            )
            return triangle(freq, 0.5, sym / 100.0, bw, phase, self._t0)
        raise ScpiError(-224, f"function {fn!r} not supported")


@component("amplitude_level")
class AmplitudeLevel(Component):
    """The amplitude and offset of a 1 Vpp waveform, as the instrument defines them (D13).

    ``amplitude`` is in ``unit``: VPP, VPK, VRMS (through each function's peak-to-RMS ratio: sine
    2√2, square and pulse 2, ramp 2√3 Vpp per Vrms) or DBM (the power into the load setting:
    Vrms = √(10^(dBm/10) · 1 mW · R)). The level is what a load of ``load_setting`` ohms would see
    behind ``output``'s impedance, so the EMF is raised by (R + Z) / R; ``inf``, or ``hiz_from`` ohms
    and more, is high-Z: the level is the open-circuit voltage and dBm is referred to
    ``dbm_hiz_ohms`` (50 Ω, the usual convention when the load is unknown).
    """

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    refs = {"synth": ("waveform_synth", "channel"), "output": ("output_stage", "channel")}
    defaults = {
        "amplitude": 0.1,  # in unit
        "unit": "VPP",  # VPP | VPK | VRMS | DBM
        "offset": 0.0,  # V, defined for the load setting like the amplitude
        "load_setting": math.inf,  # ohm the amplitude is defined for; inf: open circuit
        "hiz_from": math.inf,  # a load setting of this many ohms or more counts as high-Z
        "dbm_hiz_ohms": 50.0,  # the resistance dBm is referred to when the load setting is high-Z
        "synth": "synth",  # the waveform_synth (its function sets the VRMS and DBM conversion)
        "output": "out",  # the output_stage (its impedance raises the EMF for the load setting)
    }
    units = {"offset": "V", "load_setting": "ohm", "hiz_from": "ohm", "dbm_hiz_ohms": "ohm"}
    #: Peak-to-peak volts per RMS volt, by function.
    VPP_PER_VRMS = FunctionGeneratorCore.VPP_PER_VRMS

    def _sibling(self, key: str) -> Component | None:
        name = str(self.param(key) or "")
        if not name or self.owner is None:
            return None
        full = f"ch{self.channel}.{name}" if self.channel is not None else name
        return self.owner.components.get(full)

    def _load(self) -> float | None:
        """The load setting in ohms, None for high-Z."""
        rl = float(self.param("load_setting"))
        if not math.isfinite(rl) or rl <= 0 or rl >= float(self.param("hiz_from")):
            return None
        return rl

    def vpp(self) -> float:
        """The amplitude as volts peak-to-peak into the load setting."""
        amp = float(self.param("amplitude"))
        unit = str(self.param("unit")).upper()
        synth = self._sibling("synth")
        fn = synth.function() if isinstance(synth, WaveformSynth) else "SINusoid"
        per_rms = self.VPP_PER_VRMS.get(fn, self.VPP_PER_VRMS["SINusoid"])
        if unit == "VPK":
            return 2 * amp
        if unit == "VRMS":
            return amp * per_rms
        if unit == "DBM":
            r = self._load() or float(self.param("dbm_hiz_ohms"))
            return math.sqrt(10 ** (amp / 10) * 1e-3 * r) * per_rms
        return amp

    def _emf_scale(self) -> float:
        rl = self._load()
        out = self._sibling("output")
        if rl is None or out is None:
            return 1.0
        z = float(out.param("z_out"))
        return (rl + z) / rl

    def output(self, port: str) -> Signal:
        wave = self.input("in").scale(self.vpp()) + Signal.constant(float(self.param("offset")))
        return wave.scale(self._emf_scale())


@component("arb_table")
class ArbTable(Component):
    """An arbitrary waveform: a table played at ``rate`` samples per second, linearly interpolated.

    The output is periodic with fundamental ``rate / N``. Linear interpolation between samples is a
    convolution with a triangle, so harmonic k carries ``X[k mod N] / N · sinc²(k / N)``: the
    spectrum is exact, not sampled (validation-lockin.md 6). Harmonics are kept up to ``max_lines``.
    """

    ports = {"out": PortSpec("out", "signal")}
    defaults = {
        "table": [0.0],  # one period of the waveform, sampled
        "rate": 16000.0,  # samples per second
        "scale": 1.0,  # volts per unit of the table
        "max_lines": 1024,  # harmonics kept
    }

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
    defaults = {
        "order": 7,  # LFSR order n: period 2^n - 1 bits (one of 5, 6, 7, 9, 10, 11, 15)
        "rate": 100e3,  # bit rate, bits/s
        "level": 0.25,  # V: the bits swing between +level and -level
        "lobes": 8,  # sinc lobes of spectrum kept
    }

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
