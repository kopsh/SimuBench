"""Optical blocks (D71, #1093, #1103): a light source that an electrical drive modulates, a
photoreceiver that turns optical power back into volts, a fixed attenuator on a fibre; a tunable
laser that sweeps its wavelength in virtual time, an optical power meter and a variable optical
attenuator.

An optical port carries the optical power in watts as a :class:`Signal` — the intensity envelope,
DC plus lines — and, beside it, the carrier's wavelength (:meth:`Component.carrier`, one value per
fibre: a fibre has one source). Phase, polarization, linewidth and spectrum are not modelled. A
block whose response depends on the wavelength (a power meter's detector, an attenuator's
wavelength-dependent loss) reads the wavelength arriving and compares it with the wavelength its
instrument is set to, as the real one applies the calibration of the wavelength it is told.
Functional-block level only (product-plan.md 6): no fibre dispersion, no chirp, no relative
intensity noise, no optical reflections.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal, bessel4, brickwall, butterworth4, gaussian_lp
from ...scpi import values as V
from ...scpi.errors import ScpiError

#: The speed of light in vacuum, m/s (wavelength <-> optical frequency).
C_M_S = 299_792_458.0

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


def wavelength_table(table: Any, wavelength_m: float) -> float:
    """A value from a ``[[wavelength m, value], ...]`` table at ``wavelength_m``: piecewise linear,
    holding the end values beyond it; 0.0 for an empty table."""
    rows = list(table or ())
    if not rows:
        return 0.0
    rows.sort(key=lambda row: float(row[0]))
    xs = np.array([float(row[0]) for row in rows])
    ys = np.array([float(row[1]) for row in rows])
    return float(np.interp(wavelength_m, xs, ys))


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

    def carrier(self, port: str) -> float | None:
        return float(self.param("wavelength")) if bool(self.param("enabled")) else None


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


@component("tunable_laser")
class TunableLaser(Component):
    """A tunable laser source (#1103): continuous-wave light of settable power at a settable
    wavelength, a shutter, and a wavelength sweep that runs in virtual time.

    The sweep goes from ``sweep_start`` to ``sweep_stop`` (downwards when stop < start), either
    continuously at ``sweep_speed`` or in steps of ``sweep_step`` held ``sweep_dwell`` each, for
    ``sweep_cycles`` cycles with ``sweep_delay`` between them (the laser sits at the start
    wavelength during the delay). ``start`` begins it at the command's instant; the wavelength at
    any instant follows from the settings and that instant, so a power meter logging during the
    sweep sees the wavelength move. Outside a sweep the laser emits at ``wavelength`` — after one
    ends too, unless ``park`` keeps it at the sweep's start or stop. Settings changed during a
    sweep apply to the whole sweep from then on (the model keeps no history of them).

    ``wavelength_offset`` is the absolute wavelength error (emitted − set). Not modelled: mode
    hops, linewidth and coherence control, power flatness over the sweep, the power settling after
    a change, modulation, the trigger output (a logger starts by its own command).
    """

    ports = {"out": PortSpec("out", "optical")}
    units = {
        "wavelength": "M",
        "power": "W",
        "wavelength_offset": "M",
        "sweep_start": "M",
        "sweep_stop": "M",
        "sweep_speed": "M/S",
        "sweep_step": "M",
        "sweep_dwell": "S",
        "sweep_delay": "S",
    }
    choices = {"sweep_mode": ("CONTinuous", "STEPped"), "park": ("SETting", "STARt", "STOP")}
    defaults = {
        "wavelength": 1.55e-6,  # the wavelength setting outside a sweep, m
        "power": 1.0e-3,  # output power, W
        "enabled": True,  # the laser diode is on (LD current)
        "shutter_closed": False,  # the output shutter blocks the light
        "wavelength_offset": 0.0,  # absolute wavelength error, emitted minus set, m
        "sweep_mode": "CONTinuous",  # CONTinuous: at sweep_speed | STEPped: sweep_step every sweep_dwell
        "sweep_start": 1.50e-6,  # where a sweep starts, m
        "sweep_stop": 1.60e-6,  # where a sweep stops, m
        "sweep_speed": 10e-9,  # continuous sweep speed, m/s (10 nm/s)
        "sweep_step": 1e-9,  # stepped sweep step, m
        "sweep_dwell": 0.1,  # time each step is held, s
        "sweep_cycles": 1,  # sweeps per start
        "sweep_delay": 0.0,  # time between cycles, s (at the start wavelength)
        "park": "SETting",  # after a sweep: SETting (the wavelength setting) | STARt | STOP
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._sweep_t0: int | None = None  # the instant the running (or last) sweep started

    def reset(self) -> None:
        self._sweep_t0 = None

    # ---- the sweep in time ---------------------------------------------------------------------------

    def _points(self) -> int:
        span = abs(float(self.param("sweep_stop")) - float(self.param("sweep_start")))
        step = float(self.param("sweep_step"))
        return math.floor(span / step + 1e-9) + 1 if step > 0 else 1

    def cycle_time(self) -> float:
        """One sweep's duration, s (the delay after it not included)."""
        span = abs(float(self.param("sweep_stop")) - float(self.param("sweep_start")))
        if self.choice("sweep_mode") == "STEPped":
            return self._points() * float(self.param("sweep_dwell"))
        speed = float(self.param("sweep_speed"))
        return span / speed if speed > 0 else 0.0

    def sweep_time(self) -> float:
        """The whole sweep, every cycle and the delays between them, s."""
        cycles = max(1, int(self.param("sweep_cycles")))
        return cycles * self.cycle_time() + (cycles - 1) * float(self.param("sweep_delay"))

    def _elapsed(self) -> float | None:
        """Seconds into the sweep at the instant evaluated; None outside one."""
        if self._sweep_t0 is None:
            return None
        tau = (self.session.instant_ns - self._sweep_t0) * 1e-9
        return tau if 0.0 <= tau < self.sweep_time() else None

    def sweeping(self) -> bool:
        return self._elapsed() is not None

    def cycles_done(self) -> int:
        """Cycles finished since the last start."""
        if self._sweep_t0 is None:
            return 0
        tau = (self.session.instant_ns - self._sweep_t0) * 1e-9
        period = self.cycle_time() + float(self.param("sweep_delay"))
        cycles = max(1, int(self.param("sweep_cycles")))
        if tau >= self.sweep_time() or period <= 0:
            return cycles if tau >= 0 else 0
        return min(cycles, int((tau + float(self.param("sweep_delay"))) // period)) if tau >= 0 else 0

    def set_wavelength(self) -> float:
        """The wavelength the laser is set to at the instant evaluated, m (no offset)."""
        start, stop = float(self.param("sweep_start")), float(self.param("sweep_stop"))
        tau = self._elapsed()
        if tau is None:
            park = self.choice("park")
            if self._sweep_t0 is not None and self.session.instant_ns >= self._sweep_t0 and park != "SETting":
                return start if park == "STARt" else stop
            return float(self.param("wavelength"))
        sign = 1.0 if stop >= start else -1.0
        duration = self.cycle_time()
        period = duration + float(self.param("sweep_delay"))
        u = tau - (math.floor(tau / period) * period if period > 0 else 0.0)
        if u >= duration:
            return start  # between cycles
        if self.choice("sweep_mode") == "STEPped":
            k = min(int(u / float(self.param("sweep_dwell"))), self._points() - 1)
            return start + sign * k * float(self.param("sweep_step"))
        return start + sign * float(self.param("sweep_speed")) * u

    def emitting(self) -> bool:
        return bool(self.param("enabled")) and not bool(self.param("shutter_closed"))

    def output(self, port: str) -> Signal:
        return Signal.constant(float(self.param("power"))) if self.emitting() else Signal.zero()

    def carrier(self, port: str) -> float | None:
        return self.set_wavelength() + float(self.param("wavelength_offset")) if self.emitting() else None

    # ---- operations --------------------------------------------------------------------------------------

    @op("start")
    def op_start(self, **_: Any) -> OpResult:
        """Start a sweep now (a running one restarts)."""
        if self.choice("sweep_mode") == "CONTinuous" and not float(self.param("sweep_speed")) > 0:
            raise ScpiError(-221, "sweep speed must be above zero")
        if self.choice("sweep_mode") == "STEPped" and not (
            float(self.param("sweep_step")) > 0 and float(self.param("sweep_dwell")) > 0
        ):
            raise ScpiError(-221, "sweep step and dwell must be above zero")
        self._sweep_t0 = self.session.now_ns
        self.session.invalidate()
        return OpResult()

    @op("stop")
    def op_stop(self, **_: Any) -> OpResult:
        """Stop a running sweep; the laser goes back to its wavelength setting."""
        if self._sweep_t0 is not None:
            self._sweep_t0 = None
            self.session.invalidate()
        return OpResult()

    @op("sweep_state")
    def op_sweep_state(self, **_: Any) -> OpResult:
        """1 while a sweep runs, 0 otherwise."""
        return OpResult(V.format_int(1 if self.sweeping() else 0))

    @op("sweep_count")
    def op_sweep_count(self, **_: Any) -> OpResult:
        """Cycles finished since the last start."""
        return OpResult(V.format_int(self.cycles_done()))

    @op("sweep_time")
    def op_sweep_time(self, **_: Any) -> OpResult:
        """The whole sweep's duration, s."""
        return OpResult(self.reply_number(self.sweep_time(), 6))

    @op("wavelength")
    def op_wavelength(self, **_: Any) -> OpResult:
        """The wavelength being emitted now, m (the setting, or where the sweep is), offset included."""
        return OpResult(self.reply_number(self.set_wavelength() + float(self.param("wavelength_offset")), 10))

    @op("frequency")
    def op_frequency(self, query: bool = False, value: float | None = None, **_: Any) -> OpResult:
        """The wavelength setting as an optical frequency, Hz: a query answers c/λ, a value sets
        the wavelength setting to c/f."""
        if query:
            return OpResult(self.reply_number(C_M_S / float(self.param("wavelength")), 10))
        if value is None or not float(value) > 0:
            raise ScpiError(-222, "optical frequency must be above zero")
        path = self.bound_path("wavelength")
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the wavelength is not a setting")
        assert self.owner is not None
        self.owner.set_state(path, C_M_S / float(value))
        return OpResult()

    def status_flags(self) -> dict[str, bool]:
        """SWEEP while a sweep runs; SHUT while the shutter is closed."""
        return {"SWEEP": self.sweeping(), "SHUT": bool(self.param("shutter_closed"))}

    def readings(self) -> dict[str, Any]:
        return {
            "values": {
                "wavelength": self.set_wavelength() + float(self.param("wavelength_offset")),
                "frequency": C_M_S / max(self.set_wavelength(), 1e-12),
                "power": float(self.param("power")),
                "power_dbm": watts_to_dbm(float(self.param("power"))),
                "emitting": self.emitting(),
                "sweeping": self.sweeping(),
            }
        }


@component("optical_power_meter")
class OpticalPowerMeter(Component):
    """An optical power meter's detector head and its measurement (#1103): the average optical
    power on ``in``, read in W, dBm or dB against a reference, with the calibration of the
    wavelength it is set to.

    The detector's spectral response (``spectral_response``, relative responsivity against
    wavelength) is calibrated out at the set ``wavelength``: light of another wavelength reads
    off by R(λ actual)/R(λ set), as on a real meter told the wrong wavelength. ``offset_w`` is the
    dark offset until ``zero`` takes it out (zeroing with light on the detector takes that light
    too, as on the real one). Ranges (``ranges``, the top of each in dBm) are picked automatically
    or set; light above the range in use reads ``over_value``. Reading noise uses the meter
    ``noise`` parameter on the range's full scale in W at the ``averaging_time``; ``calibration_error``
    is the gain error in dB.

    It is a ``TraceSource`` (``POWer`` in W, ``DBM``): a ``curve_buffer`` logs it at a rate or per
    trigger, each point at its own instant — during a laser sweep each point sees the wavelength of
    its instant.
    """

    ports = {"in": PortSpec("in", "optical")}
    units = {
        "wavelength": "M",
        "averaging_time": "S",
        "offset_w": "W",
        "calibration_error": "DB",
        "reference": "DBM",
        "range": "DBM",
        "zero_time": "S",
    }
    defaults = {
        "wavelength": 1.55e-6,  # the wavelength the meter is set to, m (selects the calibration)
        "spectral_response": [],  # [[wavelength m, relative responsivity], ...]; empty: flat
        "averaging_time": 0.1,  # averaging time of one reading, s
        "ranges": [10.0, 0.0, -10.0, -20.0, -30.0, -40.0, -50.0, -60.0],  # top of each range, dBm
        "range_auto": True,  # pick the lowest range the light fits
        "range": 10.0,  # the range in use when not automatic (its top), dBm
        "offset_w": 0.0,  # dark offset before zeroing, W
        "calibration_error": 0.0,  # gain error, dB (reading/true)
        "reference": 0.0,  # the reference of dB readings, dBm
        "noise": None,  # meter reading noise: {ppm_of_range | table (by range W) | rms, ref_time_s}
        "zero_time": 1.0,  # time a zeroing takes, s
        "over_value": "+9.90E+37",  # what a reading above the range answers
        "invalid_value": "-9.91E+37",  # what a dBm reading of no light (≤ 0 W) answers
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._zero_w = 0.0

    def reset(self) -> None:
        self._zero_w = 0.0

    # ---- the measurement -----------------------------------------------------------------------------

    def response_factor(self) -> float:
        """Reading over true power from the wavelength calibration: R(λ arriving)/R(λ set)."""
        table = self.param("spectral_response")
        arriving = self.input_carrier("in")
        if not table or arriving is None:
            return 1.0
        set_r = wavelength_table(table, float(self.param("wavelength")))
        if not set_r > 0:
            raise ScpiError(-221, "the detector has no response at the set wavelength")
        return wavelength_table(table, arriving) / set_r

    def detected(self) -> float:
        """The power the detector reports at the instant evaluated, W, before noise and zeroing."""
        light = float(self.input("in").mean())
        gain = 10.0 ** (float(self.param("calibration_error")) / 10.0)
        return light * self.response_factor() * gain + float(self.param("offset_w"))

    def range_top(self, p_w: float) -> float:
        """The range in use for a reading of ``p_w``, its top in dBm."""
        tops = sorted(float(r) for r in self.param("ranges"))
        if not bool(self.param("range_auto")):
            return float(self.param("range"))
        p_dbm = watts_to_dbm(p_w)
        return next((top for top in tops if p_dbm <= top), tops[-1])

    def measure(self, *key: object) -> tuple[float, bool]:
        """One reading at the instant evaluated: (W after zeroing, with noise; over range)."""
        raw = self.detected()
        top = self.range_top(raw)
        over = raw > dbm_to_watts(top) * (1 + 1e-9)
        sigma = self.noise_rms(dbm_to_watts(top), float(self.param("averaging_time")))
        return raw - self._zero_w + self.noise_sample(sigma, "power", *key), over

    def express(self, p_w: float, over: bool, unit: str) -> float:
        """A reading in ``unit`` (W, DBM, DB); NaN when it has no value (over range: +inf)."""
        if over:
            return math.inf
        if unit in ("W", "WATT"):
            return p_w
        dbm = watts_to_dbm(p_w) if p_w > 0 else math.nan
        if unit == "DBM":
            return dbm
        if unit == "DB":
            return dbm - float(self.param("reference"))
        raise ScpiError(-224, f"power unit {unit!r}: W, DBM or DB")

    def _answer(self, value: float, unit: str) -> OpResult:
        fields = {"unit": unit}
        if value == math.inf:
            return OpResult(str(self.param("over_value")).encode(), fields=fields)
        if not math.isfinite(value):
            return OpResult(str(self.param("invalid_value")).encode(), fields=fields)
        return OpResult(self.reply_number(value, 6), fields=fields)

    @op("power", fields=("unit",))
    def op_power(self, unit: str = "DBM", measure: bool = False, **_: Any) -> OpResult:
        """The power in ``W``, ``DBM`` or ``DB`` (against ``reference``). ``measure``: a new
        reading that takes the averaging time; otherwise the reading at this instant."""
        word = str(unit).upper()
        p, over = self.measure(self.session.now_ns)
        result = self._answer(self.express(p, over, word), word)
        if not measure:
            return result
        return OpResult(result.response, round(float(self.param("averaging_time")) * 1e9), result.fields)

    @op("zero")
    def op_zero(self, **_: Any) -> OpResult:
        """Zero the detector: what it reads now (dark offset, and any light on it) becomes zero."""
        self._zero_w = self.detected()
        return OpResult(duration_ns=round(float(self.param("zero_time")) * 1e9))

    @op("reference_set")
    def op_reference_set(self, **_: Any) -> OpResult:
        """Take the reading now as the dB reference (the ``reference`` setting)."""
        p, over = self.measure(self.session.now_ns)
        if over or not p > 0:
            raise ScpiError(-221, "no valid reading to take as the reference")
        path = self.bound_path("reference")
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the reference is not a setting")
        assert self.owner is not None
        self.owner.set_state(path, round(watts_to_dbm(p), 6))
        return OpResult()

    @op("range_in_use")
    def op_range_in_use(self, **_: Any) -> OpResult:
        """The top of the range in use now, dBm."""
        return OpResult(self.reply_number(self.range_top(self.detected()), 2))

    def trace_value(self, quantity: str, t_ns: int) -> float:
        """``POWer`` (W) or ``DBM`` at instant ``t_ns``, for a logger (``curve_buffer``)."""
        with self.session.at_instant(t_ns):
            p, over = self.measure(int(t_ns))
        word = str(quantity).upper()
        value = self.express(p, over, "W" if word in ("POW", "POWER", "W") else word)
        return value if math.isfinite(value) else float("nan")

    def status_flags(self) -> dict[str, bool]:
        """OVER while the light is above the range in use."""
        raw = self.detected()
        return {"OVER": raw > dbm_to_watts(self.range_top(raw)) * (1 + 1e-9)}

    def readings(self) -> dict[str, Any]:
        p, over = self.measure("panel", self.panel_frame())
        return {
            "values": {
                "power": None if over else p,
                "power_dbm": None if over or not p > 0 else watts_to_dbm(p),
                "over": over,
                "wavelength": float(self.param("wavelength")),
                "range": self.range_top(self.detected()),
            }
        }


@component("variable_optical_attenuator")
class VariableOpticalAttenuator(Component):
    """A variable optical attenuator (#1103): light from ``IN`` to ``OUT`` with the insertion
    loss plus a settable attenuation, a shutter, and an optional output-power mode.

    ``spectral_loss`` is the extra loss against wavelength ([[m, dB], ...]); the attenuator is
    calibrated at its set ``wavelength``, so only the difference between the arriving and the set
    wavelength's loss shows. A change of attenuation reaches the output ``settling_time`` after
    it (a step, the old value until then). In ``POWer`` mode the attenuation follows the light in
    so that ``power_target`` leaves the output (an internal monitor tap), held within 0 …
    ``attenuation_max``; a target it cannot reach sets UNREACH. Not modelled: return loss,
    polarization-dependent loss, the settling inside power mode.
    """

    ports = {"IN": PortSpec("in", "optical"), "OUT": PortSpec("out", "optical")}
    units = {
        "attenuation": "DB",
        "attenuation_max": "DB",
        "insertion_loss": "DB",
        "wavelength": "M",
        "settling_time": "S",
        "power_target": "W",
    }
    choices = {"mode": ("ATTenuation", "POWer")}
    defaults = {
        "attenuation": 0.0,  # the attenuation setting above the insertion loss, dB
        "attenuation_max": 60.0,  # the most attenuation the device gives, dB
        "insertion_loss": 0.0,  # loss at 0 dB attenuation, dB
        "wavelength": 1.55e-6,  # the wavelength the attenuator is set to, m (selects the calibration)
        "spectral_loss": [],  # [[wavelength m, extra loss dB], ...]; empty: flat
        "shutter_closed": False,  # the shutter blocks the light
        "settling_time": 0.0,  # time an attenuation change takes to reach the output, s
        "mode": "ATTenuation",  # ATTenuation: the setting | POWer: hold power_target at the output
        "power_target": 1.0e-3,  # output power held in POWer mode, W
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._previous: float | None = None  # the attenuation before the last change, dB
        self._changed_ns = 0

    def reset(self) -> None:
        self._previous = None

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> Any:
        if key == "attenuation" and old is not None:
            # What was in effect at the change: a change still settling counts as its old value.
            settling = not self.settled() and self._previous is not None
            self._previous = self._previous if settling else float(old)
            self._changed_ns = self.session.now_ns
        return None

    def _wavelength_loss(self) -> float:
        table = self.param("spectral_loss")
        arriving = self.input_carrier("IN")
        if not table or arriving is None:
            return 0.0
        return wavelength_table(table, arriving) - wavelength_table(table, float(self.param("wavelength")))

    def _target_attenuation(self) -> tuple[float, bool]:
        """POWer mode: the attenuation that leaves ``power_target`` at the output, and whether it
        is within reach."""
        light = float(self.input("IN").mean())
        target = float(self.param("power_target"))
        fixed = float(self.param("insertion_loss")) + self._wavelength_loss()
        if not (light > 0 and target > 0):
            return 0.0, False
        need = 10.0 * math.log10(light / target) - fixed
        top = float(self.param("attenuation_max"))
        return min(max(need, 0.0), top), 0.0 <= need <= top

    def attenuation_now(self) -> float:
        """The attenuation in effect at the instant evaluated, dB (above the insertion loss)."""
        if self.choice("mode") == "POWer":
            return self._target_attenuation()[0]
        settle = round(float(self.param("settling_time")) * 1e9)
        if self._previous is not None and self.session.instant_ns < self._changed_ns + settle:
            return self._previous
        return float(self.param("attenuation"))

    def settled(self) -> bool:
        settle = round(float(self.param("settling_time")) * 1e9)
        return self._previous is None or self.session.instant_ns >= self._changed_ns + settle

    def loss_db(self) -> float:
        return float(self.param("insertion_loss")) + self.attenuation_now() + self._wavelength_loss()

    def output(self, port: str) -> Signal:
        if bool(self.param("shutter_closed")):
            return Signal.zero()
        return self.input("IN").scale(10.0 ** (-self.loss_db() / 10.0))

    def carrier(self, port: str) -> float | None:
        return None if bool(self.param("shutter_closed")) else self.input_carrier("IN")

    @op("attenuation")
    def op_attenuation(self, **_: Any) -> OpResult:
        """The attenuation in effect now, dB (in POWer mode the one the target calls for)."""
        return OpResult(self.reply_number(self.attenuation_now(), 4))

    @op("settled")
    def op_settled(self, **_: Any) -> OpResult:
        """1 once the last attenuation change has reached the output."""
        return OpResult(V.format_bool(self.settled()))

    @op("output_power", fields=("unit",))
    def op_output_power(self, unit: str = "W", **_: Any) -> OpResult:
        """The output power the monitor tap reads, W or DBM."""
        p = float(self.output("OUT").mean())
        word = str(unit).upper()
        if word == "DBM":
            return OpResult(
                self.reply_number(watts_to_dbm(p), 4) if p > 0 else b"-9.91E+37", fields={"unit": word}
            )
        if word in ("W", "WATT"):
            return OpResult(self.reply_number(p, 6), fields={"unit": word})
        raise ScpiError(-224, f"power unit {unit!r}: W or DBM")

    def status_flags(self) -> dict[str, bool]:
        """BUSY while an attenuation change settles; UNREACH when POWer mode cannot hold its target."""
        unreach = self.choice("mode") == "POWer" and not self._target_attenuation()[1]
        return {"BUSY": not self.settled(), "UNREACH": unreach}

    def readings(self) -> dict[str, Any]:
        p = float(self.output("OUT").mean())
        return {
            "values": {
                "attenuation": self.attenuation_now(),
                "output_power": p,
                "output_power_dbm": watts_to_dbm(p) if p > 0 else None,
                "shutter_closed": bool(self.param("shutter_closed")),
                "settled": self.settled(),
            }
        }
