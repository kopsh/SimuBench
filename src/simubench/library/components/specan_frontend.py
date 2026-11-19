"""The analog front end of the swept spectrum analyzer as its blocks (D50, signal-chain.md 5.2, #952): the step attenuator and the mixer-overload check (``specan_input``), the local oscillator (``specan_lo``), the conversion to the IF (``specan_mixer``) and the Gaussian resolution filter with the IF gain (``specan_if``). Each has an ``out`` an internal probe can read; the trace (``specan.py``) walks them."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine import rfcascade
from ...engine.component import (
    Component,
    OpResult,
    PortSpec,
    RfStageSpec,
    component,
    op,
)
from ...engine.signal import FidelityNote, Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError

#: Order of the IF filter's maximally flat (Butterworth) passband.
IF_ORDER = 5


#: The part of the IF bandwidth a sampled evaluation uses: the response is within 0.03 dB there.
IF_FLAT = 0.6


@component("specan_input")
class SpecanInput(Component):
    """The input attenuator and overload check (D1 #187): the RF input port is the generic
    ``input_port`` (50 Ω to ground, the only circuit-layer part of the analyzer). The step
    attenuator is automatic (reference level kept at the mixer limit, 2 dB steps) or manual; the
    receiver overloads when the total input power minus the attenuation passes the maximum mixer
    level. ``out`` is the input attenuated (and amplified by the preamplifier, 0 dB unless
    declared)."""

    label = "输入衰减器"
    label_en = "Input attenuator"
    about = "频谱仪的输入步进衰减器和过载检查。"
    about_en = "The analyzer's input step attenuator and overload check."

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    defaults = {
        "z_in": 50.0,  # input impedance, ohm
        "max_mixer_level": -10.0,  # dBm at the mixer: total input minus attenuation above it overloads
        "att": 10.0,  # dB, manual attenuation (a setting such as $power.att)
        "att_auto": True,  # the attenuation is automatic (a setting)
        "ref_level": 0.0,  # dBm, the reference level the automatic attenuation holds (a setting)
        "preamp_gain": 0.0,  # dB, the preamplifier's gain (0: none, or off)
        # The mixer level counts the preamplifier's gain: the automatic attenuation keeps the
        # reference level plus the gain at the maximum mixer level, and the overload check adds
        # it (the X-Series rule Atten = RefLevel + PreAmpGain − MaxMixerLevel, #1020). False: the
        # gain is left out of both, as before.
        "preamp_in_mixer_level": False,
        # The preamplifier's own figures at its input, from the data sheet (signal-chain.md 5.2,
        # #954); None: ideal. The block's stage refers them to the analyzer's input through the
        # attenuator ahead of the preamplifier.
        "preamp_noise_figure": None,  # dB
        "iip2_dbm": None,  # dBm
        "iip3_dbm": None,  # dBm
        "p1db_dbm": None,  # dBm
        # The frequency response's deviation from its calibration, dB (signal-chain.md 5.2,
        # #954): piecewise linear over [[frequency Hz, dB], ...], 0 beyond the table; empty: flat.
        "flatness": [],
    }
    units = {
        "max_mixer_level": "dBm",
        "preamp_gain": "dB",
        "preamp_noise_figure": "dB",
        "iip2_dbm": "dBm",
        "iip3_dbm": "dBm",
        "p1db_dbm": "dBm",
    }
    curves = frozenset({"flatness"})

    def signal(self) -> Signal:
        return self.input("in")

    def gain_db(self) -> float:
        return float(self.param("preamp_gain")) - self.attenuation()

    def flatness_db(self, f_hz: float | np.ndarray) -> float | np.ndarray:
        """The response's deviation at ``f_hz``, dB: piecewise linear over the flatness table, 0
        beyond it (#954: the table is a patch on the calibration, not an extrapolation); 0.0
        without a table."""
        table = self.param("flatness") or ()
        if not table:
            return 0.0
        edges = np.array([float(row[0]) for row in table])
        deviations = np.array([float(row[1]) for row in table])
        return np.interp(f_hz, edges, deviations, left=0.0, right=0.0)

    def output(self, port: str) -> Signal:
        sig = self.signal().scale(10 ** (self.gain_db() / 20))
        if self.param("flatness"):
            sig = sig.transfer(lambda f: 10 ** (np.asarray(self.flatness_db(f)) / 20))
        return sig

    def stage(self, f_hz: float) -> RfStageSpec:
        """The block as one stage: the attenuator folded into the figures (referred ahead of it,
        its loss adds the same dB to the noise figure and to every intercept)."""
        att = self.attenuation()
        nf = self.param("preamp_noise_figure")
        ip2, ip3, p1 = (self.param(k) for k in ("iip2_dbm", "iip3_dbm", "p1db_dbm"))
        return RfStageSpec(
            gain_db=self.gain_db(),
            noise_figure_db=None if nf is None else float(nf) + att,
            iip2_dbm=math.inf if ip2 is None else float(ip2) + att,
            iip3_dbm=math.inf if ip3 is None else float(ip3) + att,
            p1db_dbm=math.inf if p1 is None else float(p1) + att,
        )

    def attenuation(self) -> float:
        if bool(self.param("att_auto")):
            # Keep the reference level at the mixer limit, in 2 dB steps.
            need = float(self.param("ref_level")) + self._mixer_gain() - float(self.param("max_mixer_level"))
            return float(min(70.0, max(0.0, 2 * math.ceil(need / 2 - 1e-9))))
        return float(self.param("att"))

    def overload(self) -> bool:
        sig = self.signal().merged()
        total_mw = float(np.sum(np.abs(sig.amp) ** 2)) / (2 * float(self.param("z_in"))) * 1e3
        total_mw += sig.dc**2 / float(self.param("z_in")) * 1e3
        if total_mw <= 0:
            return False
        level = 10 * math.log10(total_mw) - self.attenuation() + self._mixer_gain()
        return level > float(self.param("max_mixer_level"))

    def _mixer_gain(self) -> float:
        """The preamplifier's gain where the mixer level counts it (``preamp_in_mixer_level``), else 0."""
        return float(self.param("preamp_gain")) if bool(self.param("preamp_in_mixer_level")) else 0.0

    @op("coupled_att")
    def op_coupled_att(self, query: bool = False, value: float | None = None, **_: Any) -> OpResult:
        """The attenuation: a manual value turns the automatic coupling off."""
        if query:
            return OpResult(self.reply_number(self.attenuation(), 10))
        assert value is not None
        if not 0 <= value <= 70:
            raise ScpiError(-222)
        assert self.owner is not None
        self.owner.set_state("power.att", 2 * round(value / 2))
        self.owner.set_state("power.att_auto", False)
        return OpResult()

    @op("overload")
    def op_overload(self, query: bool = False, **_: Any) -> OpResult:
        return OpResult(V.format_bool(self.overload()))


@component("specan_lo")
class SpecanLo(Component):
    """The local oscillator (signal-chain.md 5.1): where the receiver is tuned, the reference its
    frequency runs off, and how fast it sweeps.

    The tuning point is the frequency whose signal the mixer puts on the IF: the one of the read
    in progress (``Session.tuned``, set by the trace or an internal probe), else the center
    frequency. The 10 MHz reference is a ``reference_clock``: its fractional error ε scales the
    oscillator, so a true f displays at f / (1 + ε), and the ``:ROSCillator`` status says whether
    the external reference is in use. Switching to EXTernal without a usable 10 MHz sine keeps the
    internal oscillator and reports -221. Its SSB phase noise (``phase_noise``) rides the display
    as noise around every line the mixer converts (the trace, #954); without the table there is
    none."""

    label = "本振"
    label_en = "Local oscillator"
    about = "本振：接收机调谐到哪里、频率参考和扫描速度。"
    about_en = "The local oscillator: where the receiver is tuned, its reference and sweep speed."

    refs = {"clock": ("reference_clock", "instrument"), "if_filter": ("specan_if", "instrument")}
    defaults = {
        "clock": "",  # the reference_clock on REF_IN
        "source": "INTernal",  # watched: $rosc.source (INTernal | EXTernal)
        "if_filter": "",  # the specan_if whose intermediate frequency the oscillator tunes to
        "center": 0.0,  # Hz, where the receiver is tuned outside a sweep (a setting such as $freq.center)
        "lo_rate": 0.0,  # Hz/s, the fastest sweep (0: no limit); sets a floor on the auto sweep time
        "sweep_overhead": 0.0,  # s added to the sweep-rate floor (retrace and settling)
        # SSB phase noise, dBc/Hz (signal-chain.md 5.2, #954): [[offset Hz, dBc/Hz], ...],
        # log-log interpolation, the end offsets held beyond the table; empty: not modeled.
        "phase_noise": [],
    }
    units = {"center": "Hz", "lo_rate": "Hz/s", "sweep_overhead": "S"}

    def phase_noise_density(self, offset_hz: float | np.ndarray) -> np.ndarray | None:
        """The SSB phase noise at ``offset_hz`` from the carrier, linear (1/Hz): log-log
        interpolation over the table, the end offsets held beyond it. None: not modeled."""
        table = self.param("phase_noise") or ()
        if not table:
            return None
        offsets = np.log10([float(row[0]) for row in table])
        levels = np.array([float(row[1]) for row in table])
        asked = np.log10(np.maximum(np.abs(offset_hz), 10 ** offsets[0]))  # below the table, the first holds
        return 10 ** (np.interp(asked, offsets, levels) / 10)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._error = 0.0  # fractional frequency error of the reference in use
        self._locked = False

    def tuned(self) -> float:
        """The frequency the receiver is tuned to, on its own frequency scale, Hz."""
        tune = self.session.tune_hz
        return float(self.param("center")) if tune is None else tune

    def tuning(self) -> tuple[float, float | None, float | None] | None:
        """The center frequency, and the range of the setting it follows (the analyzer's
        frequency range) — where an internal probe can be tuned."""
        path = self.bound_path("center")
        spec = self.owner.specs.get(path) if path and self.owner else None
        lo = None if spec is None or spec.min is None else float(spec.min)
        hi = None if spec is None or spec.max is None else float(spec.max)
        return float(self.param("center")), lo, hi

    def frequency(self) -> float:
        """The oscillator's true frequency at the tuning point: the intermediate frequency below
        the tuned frequency, scaled by the reference's error."""
        if_filter: Any = self.ref("if_filter")
        if_hz = float(if_filter.param("if_hz"))
        return (self.tuned() - if_hz) * (1 + self._error)

    def sweep_floor(self, span: float) -> float:
        """The shortest time the oscillator sweeps ``span`` in, s (0 when it has no rate limit)."""
        rate = float(self.param("lo_rate"))
        return span / rate + float(self.param("sweep_overhead")) if rate > 0 else 0.0

    def refresh(self) -> None:
        """Read the reference's error and lock state (called when the receiver syncs)."""
        clock: Any = self.ref("clock")
        self._error = float(clock.error())
        self._locked = bool(clock.locked())

    def error(self) -> float:
        return self._error

    def locked(self) -> bool:
        return self._locked

    def in_use(self) -> str:
        return "EXT" if self._locked else "INT"

    def display(self, f: Any) -> Any:
        return f / (1 + self._error)

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        if key == "source" and new == "EXTernal":
            self.session.sync()
            self.refresh()
            if not self._locked:
                assert self.owner is not None
                self.owner.push_error(
                    -221,
                    "external reference unusable: REF_IN needs a 10 MHz sine within 10 ppm, "
                    "the analyzer stays on its internal reference",
                )

    @op("status")
    def op_status(self, what: str = "locked", **_: Any) -> OpResult:
        self.session.sync()
        if what == "locked":
            return OpResult(V.format_bool(self.locked()))
        return OpResult(self.in_use().encode())


@component("specan_mixer")
class SpecanMixer(Component):
    """The mixer (signal-chain.md 5.2): ``out`` is the input converted to the IF against the local
    oscillator, f_if = f_rf − f_lo, less the conversion loss. What lies below the oscillator (the
    image side) is rejected by ``image_rejection`` — completely without it, as before; the
    oscillator's leak (``lo_feedthrough``) sits at DC in the IF frame; the declared ``spurs``
    (m·f_rf ± n·f_lo responses) and the distortion products of the mixer's own intercepts appear
    as further lines; the ``residuals`` are there with no input at all. The output depends on
    where the oscillator is tuned.

    ``noise_figure`` is the whole receiver's (the earlier way to declare it): with no other stage
    declaring one, the chain's cascaded figure is exactly this."""

    label = "混频器"
    label_en = "Mixer"
    about = "把输入与本振混频变到中频，含变频损耗和镜像抑制。"
    about_en = "Converts the input to the IF against the LO, with conversion loss and image rejection."

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    tuned_ports = frozenset({"out"})
    refs = {"lo": ("specan_lo", "instrument")}
    defaults = {
        # receiver noise figure over kT0, dB; None: noiseless, for a path whose figures another
        # stage carries as a whole (a preamplifier path of a data sheet, D70)
        "noise_figure": 24.0,
        "lo": "",  # the specan_lo it converts against
        "conversion_loss": 0.0,  # dB lost from the RF line to its IF line
        # The mixer's own figures at its input, from the data sheet (signal-chain.md 5.2, #954).
        "iip2_dbm": None,  # dBm (None: ideal)
        "iip3_dbm": None,  # dBm
        "p1db_dbm": None,  # dBm
        "image_rejection": None,  # dB, the image side's rejection (None: complete)
        "lo_feedthrough": None,  # dBm, the oscillator's leak reaching the IF (None: none)
        "spurs": [],  # [[m, n, dBc], ...]: the m·f_rf − n·f_lo and m·f_rf + n·f_lo responses
        "residuals": [],  # [[frequency Hz, dBm], ...]: responses with no input
    }
    units = {
        "noise_figure": "dB",
        "conversion_loss": "dB",
        "iip2_dbm": "dBm",
        "iip3_dbm": "dBm",
        "p1db_dbm": "dBm",
        "image_rejection": "dB",
        "lo_feedthrough": "dBm",
    }

    def _z_in_ohm(self) -> float:
        """The impedance the input node ahead of the mixer runs on: the ``z_in`` of the input block
        upstream of it, 50 Ω bare. The power ↔ voltage conversions of the derived lines run on it,
        the same impedance the display calibrates against."""
        assert self.owner is not None
        block: Any = self
        seen: set[str] = set()
        while block is not None and block.name not in seen:
            seen.add(block.name)
            z = block.param("z_in")
            if z is not None:
                return float(z)
            src = self.owner.wiring.source(block.name, "in")
            block = self.owner.components[src[0]] if src is not None else None
        return 50.0

    def output(self, port: str) -> Signal:
        sig = self.input("in").merged()
        lo: Any = self.ref("lo")
        f_lo = float(lo.frequency())
        k = 10 ** (-float(self.param("conversion_loss")) / 20)
        # DC is a line at 0 Hz, so the image fold treats it like every other one.
        freq = np.concatenate([[0.0], sig.freq]) if sig.dc else sig.freq
        amp = np.concatenate([[sig.dc], sig.amp]) if sig.dc else sig.amp
        notes = sig.notes
        spec = self.stage(float(lo.tuned()))
        z = self._z_in_ohm()
        f_rf, power_mw = freq, np.abs(amp) ** 2 / (2 * z) * 1e3
        if math.isfinite(spec.p1db_dbm) and freq.size:
            # Gain compression at the mixer's own P1dB (#954): the third-order model's multi-tone
            # drive — each line's gain from every line's power, every other line counted twice.
            # The drive arrives flatness-scaled, like the display's.
            total = float(power_mw.sum())
            over = 10 * np.log10(np.maximum(2.0 * total - power_mw, 1e-300)) - spec.p1db_dbm
            amp = amp * np.asarray(rfcascade.compress_ratio(over))
        f_if = freq - f_lo
        keep = f_if > 0
        out_f, out_a = [f_if[keep]], [amp[keep] * k]
        rejection = self.param("image_rejection")
        if rejection is not None:
            # The image side folds to its mirror about the oscillator, by the rejection ratio.
            folded = ~keep
            out_f.insert(0, -f_if[folded])
            out_a.insert(0, amp[folded] * k * 10 ** (-float(rejection) / 20))
        if freq.size:
            # The declared spur responses: m·f_rf ± n·f_lo, dBc of the line. The difference
            # product lands at its absolute value — a product below the oscillator lands at its
            # mirror, like the image fold — which is what makes |m·f_rf − n·f_lo| = f_if have two
            # solutions on the display (#954); the sum product never reaches the IF.
            for row in self.param("spurs") or ():
                m_, n_, dbc = float(row[0]), float(row[1]), float(row[2])
                ks = k * 10 ** (dbc / 20)
                for f_spur in (np.abs(m_ * f_rf - n_ * f_lo), m_ * f_rf + n_ * f_lo):
                    out_f.append(f_spur)
                    out_a.append(amp * ks)
            # The distortion products the mixer's own intercepts make of the input lines
            # (#954): equivalent lines at the mixer's input, times the mixer's gain at its output.
            # A product below the oscillator has no response to ride (the receiver shows it only
            # at its own frequency), so it drops; like every line, a product rides the mixer's
            # compressed gain at its parents' drive.
            if math.isfinite(spec.iip2_dbm) or math.isfinite(spec.iip3_dbm):
                products = rfcascade.products(f_rf, 10 * np.log10(np.maximum(power_mw, 1e-300)), spec)
                if products.freq_hz.size:
                    amp_prod = np.sqrt(2 * z * 10 ** (products.power_dbm / 10) * 1e-3)
                    if math.isfinite(spec.p1db_dbm):
                        over = 10 * np.log10(np.maximum(products.drive_mw, 1e-300)) - spec.p1db_dbm
                        amp_prod = amp_prod * np.asarray(rfcascade.compress_ratio(over))
                    keep_p = products.freq_hz - f_lo > 0
                    out_f.append(products.freq_hz[keep_p] - f_lo)
                    out_a.append(amp_prod[keep_p].astype(complex) * k)
                if products.dropped:
                    notes = (
                        *notes,
                        FidelityNote(
                            "approximate",
                            "only the strongest 64 lines take part in the mixer's distortion products",
                        ),
                    )
        feedthrough = self.param("lo_feedthrough")
        dc = 0.0
        if feedthrough is not None:
            dc = math.sqrt(10 ** (float(feedthrough) / 10) * 1e-3 * z)  # DC power is V²/R
        freq_out = np.concatenate(out_f)
        return Signal(freq_out, np.concatenate(out_a), np.zeros(freq_out.shape, dtype=np.int64), dc, notes)

    def stage(self, f_hz: float) -> RfStageSpec:
        ip2, ip3, p1 = (self.param(k) for k in ("iip2_dbm", "iip3_dbm", "p1db_dbm"))
        nf = self.param("noise_figure")
        return RfStageSpec(
            gain_db=-float(self.param("conversion_loss")),
            noise_figure_db=None if nf is None else float(nf),
            iip2_dbm=math.inf if ip2 is None else float(ip2),
            iip3_dbm=math.inf if ip3 is None else float(ip3),
            p1db_dbm=math.inf if p1 is None else float(p1),
        )


@component("specan_if")
class SpecanIf(Component):
    """The IF filter and amplifier (signal-chain.md 5.1): the analog passband ahead of the ADC —
    what keeps the converter from aliasing — centered on the intermediate frequency ``if_hz``,
    ``bandwidth`` wide at -3 dB, and the IF gain. ``out`` is the mixer's output through it.

    The passband is maximally flat (a Butterworth magnitude of order 5, no phase): within 0.03 dB
    over the middle 60 % of the bandwidth, 30 dB down at twice the bandwidth. The resolution
    bandwidth is not here: it is the digital filter behind the ADC (``specan_rbw``)."""

    label = "中频滤波"
    label_en = "IF filter"
    about = "ADC 前的中频滤波与放大，防止混叠。"
    about_en = "The IF filter and amplifier ahead of the ADC, which keeps it from aliasing."

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    tuned_ports = frozenset({"out"})
    defaults = {
        "if_hz": 21.4e6,  # Hz, the intermediate frequency the filter is centered on
        "bandwidth": 10.0e6,  # Hz, the passband's width at -3 dB (the anti-alias filter)
        "gain": 0.0,  # dB, the IF amplifier's gain
        # The IF amplifier's own figures at its input, from the data sheet (#954); None: ideal.
        "iip2_dbm": None,  # dBm
        "iip3_dbm": None,  # dBm
        "p1db_dbm": None,  # dBm
    }
    units = {
        "if_hz": "Hz",
        "bandwidth": "Hz",
        "gain": "dB",
        "iip2_dbm": "dBm",
        "iip3_dbm": "dBm",
        "p1db_dbm": "dBm",
    }

    def passband(self, f_if: np.ndarray | float) -> np.ndarray:
        """The filter's voltage response at the IF frequency ``f_if`` (1 at its center)."""
        x = (
            2.0
            * (np.asarray(f_if, dtype=float) - float(self.param("if_hz")))
            / float(self.param("bandwidth"))
        )
        return 1.0 / np.sqrt(1.0 + x ** (2 * IF_ORDER))

    def flat_width(self) -> float:
        """The width around the IF a sampled evaluation takes from one tuning, Hz."""
        return IF_FLAT * float(self.param("bandwidth"))

    def output(self, port: str) -> Signal:
        gain = 10 ** (float(self.param("gain")) / 20)
        return self.input("in").transfer(lambda f: gain * self.passband(f))

    def stage(self, f_hz: float) -> RfStageSpec:
        ip2, ip3, p1 = (self.param(k) for k in ("iip2_dbm", "iip3_dbm", "p1db_dbm"))
        return RfStageSpec(
            gain_db=float(self.param("gain")),
            iip2_dbm=math.inf if ip2 is None else float(ip2),
            iip3_dbm=math.inf if ip3 is None else float(ip3),
            p1db_dbm=math.inf if p1 is None else float(p1),
        )
