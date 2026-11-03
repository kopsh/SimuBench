"""Signal sources."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.special import jv

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import MAX_LINES, FidelityNote, Signal, pulse_train, sine, square, triangle
from ...scpi import values as V
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

    def _external_hz(self) -> float | None:
        """The REF_IN frequency when a usable external reference is there, else None."""
        if not str(self.param("source")).upper().startswith("EXT"):
            return None
        nominal = float(self.param("nominal_hz"))
        sig = self.input("ref").merged()
        if sig.freq.size:
            k = int(np.argmax(np.abs(sig.amp)))
            f = float(sig.freq[k])
            if abs(f - nominal) <= nominal * 10e-6 and abs(sig.amp[k]) > 0.05:
                return f
        return None

    def locked(self) -> bool:
        """Whether an external reference is locking the source (False on the internal one)."""
        return self._external_hz() is not None

    def error(self) -> float:
        """The fractional frequency error every synthesized frequency carries."""
        f = self._external_hz()
        if f is not None:
            return f / float(self.param("nominal_hz")) - 1
        return float(self.param("ppm")) * 1e-6


@component("waveform_synth")
class WaveformSynth(Component):
    """A standard waveform at 1 Vpp around zero, phase-continuous across frequency changes.

    ``function``: SINusoid, SQUare (with ``duty``: a pulse when it is not 50 %), RAMP (``symmetry`` %
    rising; 50 is a triangle), DC (zero: the level module adds the offset), ARBitrary and PRBS (the
    ``arb`` table or ``prbs`` source on the same channel, #426). ``duty`` is a percentage or
    a ratio as ``duty_unit`` declares; nothing is guessed from its size (0.1 is 0.1 % or 10 %, by the
    declaration). Square, pulse and ramp keep harmonics up to ``bandwidth`` (and the line limit, with a
    fidelity note, D33). ``clock`` names a reference_clock whose error scales the frequency.
    """

    ports = {"out": PortSpec("out", "signal")}
    refs = {
        "clock": ("reference_clock", "instrument"),
        "arb": ("arb_table", "channel"),
        "prbs": ("prbs_source", "channel"),
    }
    optional_refs = frozenset({"clock", "arb", "prbs"})
    defaults = {
        "function": "SINusoid",  # SINusoid | SQUare | RAMP | DC | ARBitrary | PRBS (aliases below)
        "frequency": 1000.0,  # Hz
        "phase": 0.0,  # degrees
        "duty": 50.0,  # high time of a square or pulse, in duty_unit
        "duty_unit": "percent",  # percent | ratio: what duty means
        "symmetry": 100.0,  # percent rising half of the ramp (100: sawtooth)
        "bandwidth": 100e6,  # Hz, harmonic limit for square, pulse and ramp
        "square_max": math.inf,  # Hz, highest square/ramp frequency (inf: none)
        "clock": "",  # the reference_clock component (empty: an exact clock)
        "arb": "",  # the arb_table played for ARBitrary (#426; empty: not offered)
        "prbs": "",  # the prbs_source played for PRBS (#426; empty: not offered)
    }
    units = {"frequency": "Hz", "bandwidth": "Hz", "square_max": "Hz", "phase": "deg"}
    #: Other names for the same shapes.
    ALIASES = {
        "SINE": "SINusoid",
        "SIN": "SINusoid",
        "SQUARE": "SQUare",
        "PULSE": "SQUare",
        "PULSe": "SQUare",
        "ARB": "ARBitrary",
        "ARBITRARY": "ARBitrary",
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
        if fn in ("ARBitrary", "PRBS"):
            source = self._played("arb" if fn == "ARBitrary" else "prbs")
            if source is not None:
                return source.output("out")
        raise ScpiError(-224, f"function {fn!r} not supported")

    def _played(self, key: str) -> Component | None:
        """The arbitrary-waveform or PRBS component named by ``key``, on this one's channel."""
        name = str(self.param(key) or "")
        if not name or self.owner is None:
            return None
        full = f"ch{self.channel}.{name}" if self.channel is not None else name
        return self.owner.components.get(full)


def _carson(beta: float) -> int:
    """Sideband orders kept on each side of an angle-modulated line: floor(β) + 1 (Carson)."""
    return math.floor(abs(beta)) + 1 if beta else 0


class _Lines:
    """Phasor lines being assembled (cosine convention); negative frequencies fold over."""

    def __init__(self) -> None:
        self.freq: list[float] = []
        self.amp: list[complex] = []
        self.dc = 0.0

    def add(self, f: float, a: complex) -> None:
        if abs(a) < 1e-15:
            return
        if f < 0:
            f, a = -f, a.conjugate()
        if f == 0:
            self.dc += a.real
            return
        self.freq.append(f)
        self.amp.append(a)

    def signal(self, notes: tuple[FidelityNote, ...] = ()) -> Signal:
        return Signal.lines(self.freq, self.amp, dc=self.dc, notes=notes).merged()


@component("awg_modulator")
class AwgModulator(Component):
    """A function generator's modulation (#327, component-map function_generator.modulation):
    between the waveform synthesis and the amplitude/offset, so the level settings scale the
    modulated wave as the instrument's do. The modulating wave is a sine, internal (``int_rate``)
    or the strongest line on the ``ext`` input (its depth scaled by ``ext_full_scale``).

    - AM: the waveform times (1 + m·sin), m = ``am_depth`` %: sidebands of m/2 on every line;
    - FM / PM: Bessel lines to Carson's rule, the deviation scaled by the harmonic number for the
      harmonics of a square or ramp (each harmonic moves k times as far);
    - FSK: the waveform alternates with a copy at ``fsk_hop`` Hz at ``fsk_rate`` (a 50 % square
      gate): each copy at half amplitude with the gate's odd sidebands;
    - PWM: the pulse's duty swings by ± ``pwm_dev`` % of the period (the synth's pulse, rebuilt with
      the Bessel expansion of its duty), so the edges move and the mean follows.

    ``enabled`` is the master switch (the 33600A's MOD state per type, Siglent's MOD STATE);
    ``type`` picks one modulation. Off, the waveform passes untouched.
    """

    ports = {
        "in": PortSpec("in", "signal", "the synthesized waveform (1 Vpp)"),
        "ext": PortSpec("in", "signal", "the external modulation input"),
        "out": PortSpec("out", "signal", "the modulated waveform, to the level block"),
    }
    refs = {"synth": ("waveform_synth", "channel")}
    optional_refs = frozenset({"synth"})
    defaults = {
        "enabled": False,  # modulation on (a setting such as $ch.mod.state)
        "type": "AM",  # AM | FM | PM | FSK | PWM
        "source": "INTernal",  # INTernal | EXTernal: where the modulating wave comes from
        "int_rate": 100.0,  # Hz, the internal modulating sine (AM/FM/PM/PWM)
        "ext_full_scale": 5.0,  # V peak on the ext input that gives the full depth / deviation
        "am_depth": 100.0,  # percent AM depth
        "fm_dev": 100.0,  # Hz peak frequency deviation of the fundamental
        "pm_dev": 180.0,  # degrees peak phase deviation of the fundamental
        "fsk_hop": 100.0,  # Hz, the hop frequency of the fundamental
        "fsk_rate": 10.0,  # Hz, how often it alternates (one carrier then the other per period)
        "pwm_dev": 10.0,  # percent of the period the duty swings by (peak)
        "synth": "",  # the waveform_synth whose pulse PWM rebuilds
        "line_budget": 2000,  # lines the modulated spectrum may hold before it is refused (-222)
    }
    units = {
        "int_rate": "Hz",
        "ext_full_scale": "V",
        "fm_dev": "Hz",
        "fsk_hop": "Hz",
        "fsk_rate": "Hz",
        "pm_dev": "deg",
    }
    choices = {"type": ("AM", "FM", "PM", "FSK", "PWM"), "source": ("INTernal", "EXTernal")}

    def _tone(self) -> tuple[float, float, float]:
        """The modulating sine: (rate Hz, depth scale 0..1, phase rad at t = 0)."""
        if self.choice("source") == "EXTernal":
            ext = self.input("ext").merged()
            if not ext.freq.size:
                return 0.0, 0.0, 0.0
            k = int(np.argmax(np.abs(ext.amp)))
            a = complex(ext.amp[k])
            scale = abs(a) / float(self.param("ext_full_scale"))
            return float(ext.freq[k]), min(scale, 1.0), math.atan2(a.imag, a.real) + math.pi / 2
        return float(self.param("int_rate")), 1.0, 0.0

    def output(self, port: str) -> Signal:
        carrier = self.input("in")
        if not bool(self.param("enabled")):
            return carrier
        kind = self.choice("type")
        if kind == "PWM":
            return self._pwm()
        c = carrier.merged()
        rate, scale, phi = self._tone()
        if rate <= 0 or scale <= 0:
            return carrier
        out = _Lines()
        f0 = float(c.freq.min()) if c.freq.size else 0.0
        if kind == "AM":
            m = float(self.param("am_depth")) / 100.0 * scale
            side = m / 2 / 1j * complex(math.cos(phi), math.sin(phi))
            for f, a in zip(c.freq, c.amp, strict=True):
                out.add(f, a)
                out.add(f + rate, a * side)
                out.add(f - rate, a * side.conjugate())
            out.add(0.0, c.dc)
            out.add(rate, c.dc * 2 * side)
        elif kind in ("FM", "PM"):
            if kind == "FM":
                beta1 = float(self.param("fm_dev")) * scale / rate
            else:
                beta1 = math.radians(float(self.param("pm_dev"))) * scale
            # Count Carson's lines before the expansion: legal settings reach beta ~ 1e10 and the
            # loop itself would run for hours before the budget check (issue #357).
            if sum(2 * _carson(beta1 * (f / f0 if f0 > 0 else 1.0)) + 1 for f in c.freq) > int(
                self.param("line_budget")
            ):
                raise ScpiError(-222, "the modulation needs more lines than the model keeps")
            for f, a in zip(c.freq, c.amp, strict=True):
                beta = beta1 * (f / f0 if f0 > 0 else 1.0)
                k = _carson(beta)
                for n in range(-k, k + 1):
                    out.add(f + n * rate, a * jv(n, beta) * complex(math.cos(n * phi), math.sin(n * phi)))
            out.add(0.0, c.dc)
        elif kind == "FSK":
            fsk_rate = float(self.param("fsk_rate"))
            shift = float(self.param("fsk_hop")) - f0
            harmonics = range(1, 32, 2)
            for f, a in zip(c.freq, c.amp, strict=True):
                hop = f + shift * (f / f0 if f0 > 0 else 1.0)
                for fc, sign in ((f, 1.0), (hop, -1.0)):
                    out.add(fc, a / 2)
                    for h in harmonics:
                        side = sign * a / (math.pi * h) / 1j
                        out.add(fc + h * fsk_rate, side)
                        out.add(fc - h * fsk_rate, -side)
            out.add(0.0, c.dc)
        if len(out.freq) > int(self.param("line_budget")):
            raise ScpiError(-222, "the modulation needs more lines than the model keeps")
        return out.signal(carrier.notes)

    def _pwm(self) -> Signal:
        """The synth's pulse with its duty swinging by ±``pwm_dev`` % at the modulating rate."""
        name = str(self.param("synth") or "")
        if not name or self.owner is None:
            raise ScpiError(-221, "PWM needs the pulse synthesizer (the modulator's synth)")
        synth = self.owner.components[f"ch{self.channel}.{name}" if self.channel is not None else name]
        return self._pwm_from(synth)

    def _pwm_from(self, synth: Any) -> Signal:
        if synth.function() != "SQUare":  # type: ignore[attr-defined]
            raise ScpiError(-221, "PWM modulates a pulse")
        f = synth.frequency()  # type: ignore[attr-defined]
        d0 = synth.duty_ratio()  # type: ignore[attr-defined]
        bw = float(synth.param("bandwidth"))
        rate, scale, phi = self._tone()
        delta = float(self.param("pwm_dev")) / 100.0 * scale
        budget = int(self.param("line_budget"))
        # Count Carson's lines before the expansion (issue #357): the harmonics are kept while
        # they fit the budget and cut after that (issue #388: a pulse has bw/f harmonics, so a
        # refusal would make PWM unusable at ordinary rates). The count stops at the budget, so
        # the cost does not grow with the harmonic count or the deviation.
        lines = 2  # the DC term and the modulating tone
        keep = 0
        for k in range(1, max(1, int(bw // f)) + 1):
            lines += 2 * _carson(2 * math.pi * k * delta) + 2  # the sidebands and the harmonic
            if lines > budget:
                break
            keep = k
        out = _Lines()
        out.add(0.0, d0 - 0.5)  # levels ±0.5, as the synth's pulse
        out.add(rate, delta * complex(math.cos(phi - math.pi / 2), math.sin(phi - math.pi / 2)))
        for k in range(1, keep + 1):
            out.add(k * f, 1 / (math.pi * k) * -1j)
            b = 2 * math.pi * k * delta
            psi = 2 * math.pi * k * d0
            for n in range(-_carson(b), _carson(b) + 1):
                rot = complex(math.cos(-psi - n * phi - math.pi / 2), math.sin(-psi - n * phi - math.pi / 2))
                out.add(k * f - n * rate, -jv(n, b) / (math.pi * k) * rot)
        return out.signal((FidelityNote("truncated", "PWM pulse: harmonics kept within the line budget"),))


@component("amplitude_level")
class AmplitudeLevel(Component):
    """The amplitude and offset of a 1 Vpp waveform, as the instrument defines them (D13, D48).

    ``amplitude`` is in ``unit``: VPP, VPK, VRMS (through each function's peak-to-RMS ratio: sine
    2√2, square and pulse 2, ramp 2√3 Vpp per Vrms) or DBM (the power into the load setting:
    Vrms = √(10^(dBm/10) · 1 mW · R)). The level is what a load of ``load_setting`` ohms would see
    behind ``output``'s impedance, so the EMF is raised by (R + Z) / R; ``inf``, or ``hiz_from`` ohms
    and more, is high-Z: the level is the open-circuit voltage and dBm is referred to
    ``dbm_hiz_ohms`` (50 Ω, the usual convention when the load is unknown).

    ``vpp_min`` / ``vpp_max`` bound the amplitude as volts peak-to-peak into the load setting, the
    one physical limit a source has (D48): a write in any unit is converted and checked, refusing
    with -222; a change of the waveform or the load setting that would leave the range is refused
    with -221. Switching the unit converts the value, so the output does not move (a real source's
    ``VOLT:UNIT``); there is no dBm expression of 0 Vpp, so that switch is refused with -221.
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
        "vpp_min": None,  # V, the smallest Vpp into the load setting (None: not checked)
        "vpp_max": None,  # V, the largest Vpp into the load setting (None: not checked)
        "synth": "synth",  # the waveform_synth (its function sets the VRMS and DBM conversion)
        "output": "out",  # the output_stage (its impedance raises the EMF for the load setting)
    }
    units = {
        "offset": "V",
        "load_setting": "ohm",
        "hiz_from": "ohm",
        "dbm_hiz_ohms": "ohm",
        "vpp_min": "V",
        "vpp_max": "V",
    }
    #: Peak-to-peak volts per RMS volt, by function.
    VPP_PER_VRMS = FunctionGeneratorCore.VPP_PER_VRMS

    def _sibling(self, key: str) -> Component | None:
        name = str(self.param(key) or "")
        if not name or self.owner is None:
            return None
        full = f"ch{self.channel}.{name}" if self.channel is not None else name
        return self.owner.components.get(full)

    def bound_paths(self) -> dict[str, str]:
        out = super().bound_paths()
        synth = self._sibling("synth")
        if synth is not None:  # watched so a shape change that breaks the Vpp range is refused
            path = synth.bound_paths().get("function")
            if path:
                out["function"] = path
        return out

    def _load(self) -> float | None:
        """The load setting in ohms, None for high-Z."""
        rl = float(self.param("load_setting"))
        if not math.isfinite(rl) or rl <= 0 or rl >= float(self.param("hiz_from")):
            return None
        return rl

    def _per_rms(self) -> float:
        synth = self._sibling("synth")
        fn = synth.function() if isinstance(synth, WaveformSynth) else "SINusoid"
        return self.VPP_PER_VRMS.get(fn, self.VPP_PER_VRMS["SINusoid"])

    def _dbm_ohms(self) -> float:
        return self._load() or float(self.param("dbm_hiz_ohms"))

    def _to_vpp(self, amp: float, unit: str) -> float:
        """The amplitude in ``unit`` as volts peak-to-peak into the load setting."""
        unit = unit.upper()
        if unit == "VPK":
            return 2 * amp
        if unit == "VRMS":
            return amp * self._per_rms()
        if unit == "DBM":
            return math.sqrt(10 ** (amp / 10) * 1e-3 * self._dbm_ohms()) * self._per_rms()
        return amp

    def _from_vpp(self, vpp: float, unit: str) -> float:
        """The inverse of :meth:`_to_vpp` (log of a non-positive Vpp is undefined)."""
        unit = unit.upper()
        if unit == "VPK":
            return vpp / 2
        if unit == "VRMS":
            return vpp / self._per_rms()
        if unit == "DBM":
            return 10 * math.log10((vpp / self._per_rms()) ** 2 / (1e-3 * self._dbm_ohms()))
        return vpp

    def vpp(self) -> float:
        """The amplitude as volts peak-to-peak into the load setting."""
        return self._to_vpp(float(self.param("amplitude")), str(self.param("unit")))

    def on_state(self, path: str, old: Any, new: Any) -> Any:
        if self.owner is None or old is None:
            return None
        bound = self.bound_paths()
        if path == bound.get("unit"):
            self._switch_unit(str(old), str(new))
            return None
        if path not in (bound.get("amplitude"), bound.get("load_setting"), bound.get("function")):
            return None
        vpp, lo, hi = self.vpp(), self.param("vpp_min"), self.param("vpp_max")
        if (lo is not None and vpp < float(lo) - 1e-12) or (hi is not None and vpp > float(hi) + 1e-12):
            if path == bound.get("amplitude"):
                unit = str(self.param("unit")).upper()
                edges = []
                for bound_vpp, word in ((lo, "-inf"), (hi, "inf")):
                    edges.append(
                        f"{self._from_vpp(float(bound_vpp), unit):.6g}" if bound_vpp is not None else word
                    )
                raise ScpiError(
                    -222,
                    f"amplitude {float(self.param('amplitude')):.6g} {unit} out of range "
                    f"[{edges[0]}, {edges[1]}] {unit}",
                )
            raise ScpiError(
                -221,
                f"the wave would be {vpp:.6g} Vpp into the load setting, "
                f"out of [{lo if lo is not None else '-inf'}, {hi if hi is not None else 'inf'}] Vpp",
            )
        return None

    def _switch_unit(self, old: str, new: str) -> None:
        """Convert the value to the new unit: the same wave in the new unit's terms (D48)."""
        amp_path = self.bound_paths().get("amplitude")
        if amp_path is None:
            return
        vpp = self._to_vpp(float(self.owner.get_state(amp_path)), old)
        if str(new).upper() == "DBM" and vpp <= 0:
            raise ScpiError(-221, "0 Vpp has no dBm expression")
        self.owner.set_state(amp_path, self._from_vpp(vpp, new), internal=True)

    # ---- high and low levels (#424) -----------------------------------------------------------

    def levels(self) -> tuple[float, float]:
        """The wave's (low, high) levels into the load setting: offset ∓ Vpp/2."""
        half = self.vpp() / 2
        offset = float(self.param("offset"))
        return offset - half, offset + half

    def _set_levels(self, low: float, high: float) -> None:
        """Write the bound amplitude (in its unit) and offset so the wave spans low..high."""
        if high <= low:
            raise ScpiError(-222, f"the high level {high:.6g} V must be above the low level {low:.6g} V")
        bound = self.bound_paths()
        amp_path, off_path = bound.get("amplitude"), bound.get("offset")
        if self.owner is None or amp_path is None or off_path is None:
            raise ScpiError(-221, "the amplitude and offset are not settings")
        unit = str(self.param("unit"))
        self.owner.set_state(amp_path, self._from_vpp(high - low, unit))
        self.owner.set_state(off_path, (high + low) / 2)

    def _level(self, which: int, query: bool, value: Any) -> OpResult:
        assert self.owner is not None
        levels = list(self.levels())
        if query:
            return OpResult(self.owner.format_number(levels[which]))
        if value is None:
            raise ScpiError(-109, "missing level")
        levels[which] = float(value)
        self._set_levels(*levels)
        return OpResult()

    @op("high")
    def op_high(self, query: bool = False, value: Any = None, **_: Any) -> OpResult:
        """``VOLTage:HIGH``: the high level; setting it keeps the low level (amplitude and offset change)."""
        return self._level(1, query, value)

    @op("low")
    def op_low(self, query: bool = False, value: Any = None, **_: Any) -> OpResult:
        """``VOLTage:LOW``: the low level; setting it keeps the high level."""
        return self._level(0, query, value)

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
        "table": [0.0],  # one period of the waveform, sampled (until a loaded waveform is selected)
        "rate": 16000.0,  # samples per second
        "scale": 1.0,  # volts per unit of the table
        "max_lines": 1024,  # harmonics kept
        "min_points": 1,  # the fewest points a loaded waveform may have (#426)
        "max_points": 1_000_000,  # the most points a loaded waveform may have
        "dac_full_scale": 32767,  # the DAC code of +1 for load_dac
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Waveforms loaded by name into volatile memory (#426), each normalized to -1..+1.
        self.waves: dict[str, np.ndarray] = {}
        self.selected: str | None = None

    # ---- volatile memory (#426): DATA:ARBitrary, FUNCtion:ARBitrary, DATA:VOLatile ------------------

    def _store(self, name: Any, values: list[float]) -> None:
        key = V.parse_string(name) if isinstance(name, bytes) else str(name)
        key = key.strip()
        if not key:
            raise ScpiError(-224, "a waveform needs a name")
        n = len(values)
        if not int(self.param("min_points")) <= n <= int(self.param("max_points")):
            raise ScpiError(
                -222, f"{n} points; a waveform has {self.param('min_points')}..{self.param('max_points')}"
            )
        table = np.asarray(values, dtype=float)
        if np.any(np.abs(table) > 1.0 + 1e-12):
            raise ScpiError(-222, "arbitrary waveform values are -1 to +1")
        self.waves[key] = table

    @op("load")
    def op_load(self, name: Any = None, values: list[float] | None = None, **_: Any) -> OpResult:
        """``DATA:ARBitrary <name>,<v>,…``: a waveform of -1..+1 values into volatile memory."""
        self._store(name, list(values or []))
        return OpResult()

    @op("load_dac")
    def op_load_dac(self, name: Any = None, values: list[float] | None = None, **_: Any) -> OpResult:
        """``DATA:ARBitrary:DAC <name>,<code>,…``: DAC codes, ``dac_full_scale`` being +1."""
        full = float(self.param("dac_full_scale"))
        self._store(name, [float(v) / full for v in values or []])
        return OpResult()

    def _key(self, name: Any) -> str:
        text = V.parse_string(name) if isinstance(name, bytes) else str(name)
        return text.strip()

    @op("select")
    def op_select(self, query: bool = False, name: Any = None, **_: Any) -> OpResult:
        """``FUNCtion:ARBitrary <name>``: play a loaded waveform; the query answers its quoted name."""
        if query:
            return OpResult(V.format_string(self.selected or ""))
        key = self._key(name)
        hit = next((k for k in self.waves if k.upper() == key.upper()), None)
        if hit is None:
            raise ScpiError(-224, f"no waveform {key!r} in volatile memory")
        self.selected = hit
        return OpResult()

    @op("catalog")
    def op_catalog(self, **_: Any) -> OpResult:
        """``DATA:VOLatile:CATalog?``: the loaded waveforms' quoted names."""
        return OpResult(b",".join(V.format_string(k) for k in self.waves) or b'""')

    @op("points")
    def op_points(self, **_: Any) -> OpResult:
        """``FUNCtion:ARBitrary:POINts?``: the points of the waveform played."""
        return OpResult(V.format_int(len(self._table())))

    @op("clear")
    def op_clear(self, **_: Any) -> OpResult:
        """``DATA:VOLatile:CLEar``: forget the loaded waveforms (the definition's table plays again)."""
        self.waves.clear()
        self.selected = None
        return OpResult()

    def _table(self) -> np.ndarray:
        if self.selected is not None and self.selected in self.waves:
            return self.waves[self.selected]
        return np.asarray(self.param("table"), dtype=float)

    def output(self, port: str) -> Signal:
        table = self._table() * float(self.param("scale"))
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
