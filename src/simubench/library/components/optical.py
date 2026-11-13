"""Optical blocks (D71, #1093): a light source that an electrical drive modulates, a photoreceiver
that turns optical power back into volts, and a fixed attenuator on a fibre.

An optical port carries the optical power in watts as a :class:`Signal` — the intensity envelope,
DC plus lines. The carrier itself (its wavelength, phase, polarization, spectrum) is not modelled:
a receiver is told the wavelength by its instrument's setting, as a real sampling head or power
meter is told which calibration to use. Functional-block level only (product-plan.md 6): no fibre
dispersion, no chirp, no relative intensity noise, no optical reflections.
"""

from __future__ import annotations

import math
from typing import Any

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal, bessel4, brickwall, butterworth4, gaussian_lp
from ...scpi.errors import ScpiError

#: The filters a photoreceiver's response can take; "none" passes everything.
RESPONSES = {
    "bessel4": bessel4,
    "gaussian": gaussian_lp,
    "butterworth4": butterworth4,
    "brickwall": brickwall,
}


def watts_to_dbm(p_w: float) -> float:
    """Optical power in dBm; no light is minus infinity (callers answer it as unmeasurable)."""
    return 10.0 * math.log10(p_w / 1e-3) if p_w > 0 else -math.inf


def dbm_to_watts(p_dbm: float) -> float:
    return 1e-3 * 10.0 ** (p_dbm / 10.0)


def modulation_levels(avg_w: float, er_db: float) -> tuple[float, float]:
    """The one and zero levels (W) of a two-level optical signal with average power ``avg_w`` and
    extinction ratio ``er_db`` (P1/P0 in dB): P1 = 2·Pavg·ER/(ER+1), P0 = 2·Pavg/(ER+1)."""
    er = 10.0 ** (er_db / 10.0)
    return 2.0 * avg_w * er / (er + 1.0), 2.0 * avg_w / (er + 1.0)


@component("optical_source")
class OpticalSource(Component):
    """A laser and its modulator: the electrical drive on ``in`` sets the optical power on ``out``.

    Linear around the operating point: the drive's DC is the bias, and a drive of ±``swing``/2
    about it swings the power between the zero and one levels that ``power`` (average) and
    ``extinction_ratio`` give — P(t) = Pavg + OMA/2 · (v(t) − v_dc)/(swing/2), OMA = P1 − P0. A
    drive beyond ±swing/2 is not clipped (the modulator is linear here, a model limit): keep the
    drive inside the swing, as a real driver is set up. Off (``enabled`` false): no light.
    """

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "optical")}
    units = {"power": "W", "extinction_ratio": "DB", "swing": "V", "wavelength": "M"}
    defaults = {
        "power": 1.0e-3,  # average optical power, W
        "extinction_ratio": 6.0,  # P1/P0 in dB (infinite: not allowed; the zero level is then dark)
        "swing": 1.0,  # peak-to-peak drive (V) that swings the power from the zero to the one level
        "enabled": True,  # the laser is on
        "wavelength": 1.31e-6,  # emission wavelength, m (carried for display; receivers use their own setting)
    }

    def output(self, port: str) -> Signal:
        if not bool(self.param("enabled")):
            return Signal.zero()
        avg = float(self.param("power"))
        p1, p0 = modulation_levels(avg, float(self.param("extinction_ratio")))
        drive = self.input("in")
        swing = float(self.param("swing"))
        k = (p1 - p0) / swing if swing > 0 else 0.0
        ac = Signal(drive.freq, drive.amp, drive.t0_ns, 0.0, drive.notes).scale(k)
        return Signal(ac.freq, ac.amp, ac.t0_ns, avg, ac.notes)


@component("optical_receiver")
class OpticalReceiver(Component):
    """A photodetector and its amplifier: optical power on ``in`` (W) becomes volts on ``out``,
    v = P · ``responsivity`` (A/W) · ``transimpedance`` (V/A), band-limited by ``response`` at
    ``bandwidth``. A calibrated optical channel (a sampling head that shows watts) sets the
    transimpedance to 1/responsivity: one volt per watt.

    ``noise_w`` is the input-referred optical noise (W rms over the receiver's bandwidth, as data
    sheets give it); the record taker adds it with the sampler's own (``output_noise_rms``), since
    an analytic signal carries no noise (architecture.md 2). Average power (``power``) is the mean
    of the light arriving, as a power meter in the receiver's DC path reads it.
    """

    ports = {"in": PortSpec("in", "optical"), "out": PortSpec("out", "signal")}
    units = {
        "responsivity": "A/W",
        "transimpedance": "OHM",
        "bandwidth": "HZ",
        "noise_w": "W",
        "wavelength": "M",
    }
    choices = {"response": ("bessel4", "gaussian", "butterworth4", "brickwall", "none")}
    defaults = {
        "responsivity": 0.8,  # photodiode responsivity at the wavelength in use, A/W
        "transimpedance": 1.25,  # amplifier gain, V/A (1/responsivity: one volt per watt)
        "bandwidth": 50e9,  # -3 dB frequency of the response, Hz
        "response": "bessel4",  # bessel4 (the reference receiver's shape) | gaussian | butterworth4 | brickwall | none
        "noise_w": 0.0,  # input-referred optical noise, W rms over the bandwidth
        "wavelength": 1.31e-6,  # the wavelength the channel is set to, m (selects the calibration)
        "min_power": 0.0,  # W: an average below this reads as no light (the power meter's floor)
        "invalid_value": "9.91E+37",  # what a power reading without light answers (SCPI NaN)
    }

    def conversion_gain(self) -> float:
        """Volts on ``out`` per watt on ``in``."""
        return float(self.param("responsivity")) * float(self.param("transimpedance"))

    def output(self, port: str) -> Signal:
        light = self.input("in").scale(self.conversion_gain())
        kind = self.choice("response") or "none"
        if kind == "none":
            return light
        return light.transfer(RESPONSES[kind](float(self.param("bandwidth"))))

    def output_noise_rms(self) -> float:
        """The receiver's noise at ``out``, V rms."""
        return float(self.param("noise_w")) * self.conversion_gain()

    def average_power(self) -> float:
        """Mean optical power arriving, W; NaN below the power meter's floor."""
        p = float(self.input("in").mean())
        return p if p > float(self.param("min_power")) else math.nan

    @op("power", fields=("unit",))
    def op_power(self, unit: str = "W", **_: Any) -> OpResult:
        """The average optical power, in watts (``W``) or dBm (``DBM``). No light: the definition's
        invalid value (NaN's sentinel)."""
        assert self.owner is not None
        p = self.average_power()
        word = str(unit).upper()
        if word == "DBM":
            value = watts_to_dbm(p) if math.isfinite(p) else math.nan
        elif word in ("W", "WATT"):
            value = p
        else:
            raise ScpiError(-224, f"power unit {unit!r}: W or DBM")
        if not math.isfinite(value):
            return OpResult(str(self.param("invalid_value")).encode(), fields={"unit": word})
        return OpResult(self.owner.format_number(value), fields={"unit": word})


@component("optical_attenuator")
class OpticalAttenuator(Component):
    """A fixed optical attenuator on a fibre: ``attenuation`` dB of loss, flat in frequency."""

    category = "part"
    label = "光衰减器"
    label_en = "Optical attenuator"
    units = {"attenuation": "DB"}
    ports = {"IN": PortSpec("in", "optical"), "OUT": PortSpec("out", "optical")}
    defaults = {"attenuation": 3.0}  # loss, dB

    def output(self, port: str) -> Signal:
        return self.input("IN").scale(10.0 ** (-float(self.param("attenuation")) / 10.0))
