"""RF signal generator as its real blocks (D50, D2 #188): carrier, exact AM, Bessel FM and PM to
Carson's rule, list and step sweeps, and a timebase whose error every output frequency carries
(validation-rfgen.md).

The chain is data-level except the output: ``rf_reference`` (the 10 MHz timebase, internal or
locked to REF_IN, and the REF_OUT line that carries its error), ``rf_synthesizer`` (the carrier and
the sweep/list plan), ``rf_mod_source`` (the internal low-frequency modulation source: the tone
frequencies, scaled by the same timebase, and their phase convention), ``rf_modulator`` (AM, FM
and PM with the modulation index and its line budget), ``rf_level_control`` (the level in dBm,
defined into a matched load) and ``rf_attenuator`` (the output step attenuator; 0 dB in this model,
whose level is exact over the whole range). The 50 Ω output is the generic ``output_stage``.

The split keeps the behaviour of the old monolith exactly, down to the spectral lines on RF_OUT and
REF_OUT (see ``tests/test_rfgen_migration.py``).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.special import jv

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .sources import ReferenceClock

Z0 = 50.0
LINE_BUDGET = 999
REF_DBM = 4.0  # REF_OUT: 10 MHz at +4 dBm into a matched load


def carson_orders(beta: float) -> int:
    """Sideband orders kept on each side: floor(β) + 1 (Carson's rule)."""
    return math.floor(beta) + 1 if beta > 0 else 0


def omitted_power_fraction(beta: float) -> float:
    k = carson_orders(beta)
    n = np.arange(-k, k + 1)
    return float(1.0 - np.sum(jv(n, beta) ** 2))


def emf_peak(dbm: float) -> float:
    """Open-circuit peak voltage behind 50 Ω that delivers ``dbm`` into a matched load."""
    return 2 * math.sqrt(2 * Z0 * 1e-3 * 10 ** (dbm / 10))


@component("rf_reference")
class RfReference(ReferenceClock):
    """The 10 MHz timebase (D2 #188): the generic reference clock, plus the REF_OUT output.

    ``ppm`` is the timebase error: hidden state (D9), readable only by measuring. With ``source``
    EXTernal the 10 MHz on ``ref`` locks the generator when one within ±10 ppm is there, and every
    synthesized frequency carries that error instead; REF_OUT carries the error of the timebase in
    use (10 MHz at +4 dBm into a matched load).
    """

    ports = {**ReferenceClock.ports, "out": PortSpec("out", "signal")}
    defaults = {
        **ReferenceClock.defaults,
        "ref_out": True,  # the REF_OUT output on (a setting such as $rosc.output)
        "out_level_dbm": REF_DBM,  # REF_OUT level into a matched load (a fixed model value)
    }
    units = {**ReferenceClock.units, "ppm": "ppm", "out_level_dbm": "dBm"}
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._ext_error: float | None = None
        self._seen = -1

    def sync(self) -> None:
        """Read the reference input when the signals changed and invalidate on a new lock state.

        The input is read here, not in :meth:`error`: ``error`` is called while the bench is being
        solved, and asking for the signal on ``ref`` there would solve the circuit again.
        """
        if self._seen == self.session._signal_version:
            return
        self._seen = self.session._signal_version
        ext = None
        if str(self.param("source")).upper().startswith("EXT"):
            f = self._external_hz()
            if f is not None:
                ext = f / float(self.param("nominal_hz")) - 1
        if ext != self._ext_error:
            self._ext_error = ext
            self.session.invalidate()

    def error(self) -> float:
        if str(self.param("source")).upper().startswith("EXT") and self._ext_error is not None:
            return self._ext_error
        return float(self.param("ppm")) * 1e-6

    def locked(self) -> bool:
        return self._ext_error is not None

    def output(self, port: str) -> Signal:
        if port != "out":
            raise NotImplementedError(f"{self.type_name} has no signal output {port!r}")
        if not self.param("ref_out"):
            return Signal.zero()
        reference_hz = float(self.param("nominal_hz")) * (1 + self.error())
        return Signal.lines([reference_hz], [emf_peak(float(self.param("out_level_dbm")))])

    def readings(self) -> dict[str, Any]:
        external = str(self.param("source")).upper().startswith("EXT")
        return {"values": {"locked": (not external) or self.locked()}}


@component("rf_synthesizer")
class RfSynthesizer(Component):
    """The carrier synthesizer (D2 #188): the CW frequency, or the sweep/list plan and its current
    point, advanced by ``:INITiate`` and ``:TRIGger``. The line it emits is unit amplitude at the
    set frequency times the timebase error of the reference it locks to; level and modulation come
    later in the chain."""

    ports = {"out": PortSpec("out", "signal")}
    refs = {"clock": ("reference_clock", "instrument")}
    defaults = {
        "frequency": 1e9,  # Hz of the CW carrier
        "mode": "CW",  # CW | LIST | otherwise the start/stop sweep
        "start": 1e9,  # Hz, first frequency of the sweep
        "stop": 2e9,  # Hz, last frequency of the sweep
        "points": 2,  # points of the sweep
        "spacing": "LINear",  # LINear | LOGarithmic
        "manual": 1,  # current sweep point, 1-based
        "clock": "",  # the rf_reference (empty: an exact clock)
    }
    units = {"frequency": "Hz", "start": "Hz", "stop": "Hz"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.list_freqs: list[float] = []

    def _clock(self) -> ReferenceClock:
        assert self.owner is not None
        return self.owner.components[str(self.param("clock"))]

    def error(self) -> float:
        """Fractional frequency error of the timebase in use."""
        return float(self._clock().error())

    def sweep_points(self) -> list[float]:
        if self.param("mode") == "LIST":
            return self.list_freqs
        n = int(self.param("points"))
        a, b = float(self.param("start")), float(self.param("stop"))
        if n < 2:
            return [a]
        if self.param("spacing") == "LOGarithmic" and a > 0 and b > 0:
            return [float(x) for x in np.geomspace(a, b, n)]
        return [float(x) for x in np.linspace(a, b, n)]

    def frequency(self) -> float:
        """The frequency the generator is set to (before the timebase error)."""
        if self.param("mode") == "CW":
            return float(self.param("frequency"))
        pts = self.sweep_points()
        if not pts:
            return float(self.param("frequency"))
        i = min(max(int(self.param("manual")), 1), len(pts)) - 1
        return pts[i]

    def output(self, port: str) -> Signal:
        if port != "out":
            raise NotImplementedError(f"{self.type_name} has no signal output {port!r}")
        return Signal.lines([self.frequency() * (1 + self.error())], [1.0])

    @op("list_freq")
    def op_list_freq(self, query: bool = False, values: list[bytes] | None = None, **_: Any) -> OpResult:
        """``:LIST:FREQ 1e9,2.4e9`` or ``"1e9,2.4e9"``: real instruments take both forms (D13)."""
        if query:
            return OpResult(b",".join(V.format_float(f, 12) for f in self.list_freqs))
        freqs = V.parse_float_list(tuple(values or ()), "HZ")
        if not 1 <= len(freqs) <= 1601:
            raise ScpiError(-222, "a list holds 1 to 1601 frequencies")
        if any(not 9e3 <= f <= 6e9 for f in freqs):
            raise ScpiError(-222)
        self.list_freqs = freqs
        self.session.invalidate()
        return OpResult()

    @op("list_points")
    def op_list_points(self, **_: Any) -> OpResult:
        return OpResult(V.format_int(len(self.list_freqs)))

    @op("initiate")
    def op_initiate(self, **_: Any) -> OpResult:
        assert self.owner is not None
        self.owner.set_state(self.bound_paths()["manual"], 1)
        return OpResult()

    @op("trigger")
    def op_trigger(self, **_: Any) -> OpResult:
        assert self.owner is not None
        n = len(self.sweep_points())
        self.owner.set_state(self.bound_paths()["manual"], min(int(self.param("manual")) + 1, max(n, 1)))
        return OpResult()

    def readings(self) -> dict[str, Any]:
        return {
            "values": {
                "frequency": self.frequency(),
                "point": int(self.param("manual")) if self.param("mode") != "CW" else 0,
                "points": len(self.sweep_points()) if self.param("mode") != "CW" else 0,
            }
        }


@component("rf_mod_source")
class RfModSource(Component):
    """The internal low-frequency modulation source (D2 #188): the tone of AM, FM or PM at its set
    frequency. Its frequency is synthesized from the same timebase as the carrier, so the
    reference's error scales it too; FM modulates with a phase shift of −π/2 (a sine tone), PM in
    phase with the carrier."""

    refs = {"clock": ("reference_clock", "instrument")}
    defaults = {
        "am_rate": 1e3,  # Hz of the AM tone
        "fm_rate": 1e3,  # Hz of the FM tone
        "pm_rate": 1e3,  # Hz of the PM tone
        "clock": "",  # the rf_reference (empty: an exact clock)
    }
    units = {"am_rate": "Hz", "fm_rate": "Hz", "pm_rate": "Hz"}

    def _clock(self) -> ReferenceClock:
        assert self.owner is not None
        return self.owner.components[str(self.param("clock"))]

    def rate(self, kind: str) -> float:
        """The tone's frequency as set (before the timebase error), Hz."""
        return float(self.param(f"{kind}_rate"))

    def frequency(self, kind: str) -> float:
        """The tone's frequency on the wire: the set frequency times the timebase error."""
        return self.rate(kind) * (1 + float(self._clock().error()))

    @staticmethod
    def phase(kind: str) -> float:
        """Phase of sideband 1: FM on a sine tone (−π/2), PM in phase with the carrier (0)."""
        return -math.pi / 2 if kind == "fm" else 0.0


@component("rf_modulator")
class RfModulator(Component):
    """The modulator (D2 #188): AM, and Bessel FM/PM truncated at Carson's rule, applied to the
    carrier line. FM and PM are exclusive (the later one wins with -221), AM may be added to
    either, pulse modulation is refused (-221), and a modulation index needing more than
    ``LINE_BUDGET`` lines is refused (-222) on the setting that causes it."""

    ports = {
        "in": PortSpec("in", "signal"),
        "out": PortSpec("out", "signal"),
    }
    refs = {"source": ("rf_mod_source", "instrument")}
    defaults = {
        "am": False,  # amplitude modulation on
        "am_depth": 0.0,  # percent modulation depth
        "am_rate": 1e3,  # Hz of the AM tone (the modulator forms the sidebands at it)
        "fm": False,  # frequency modulation on (FM and PM are exclusive)
        "fm_dev": 0.0,  # Hz peak deviation
        "fm_rate": 1e3,  # Hz of the FM tone (β = deviation / rate)
        "pm": False,  # phase modulation on (FM and PM are exclusive)
        "pm_dev": 0.0,  # rad of phase deviation (the modulation index beta)
        "pm_rate": 1e3,  # Hz of the PM tone
        "pulm": False,  # pulse modulation (setting it errors: not simulated)
        "source": "",  # the rf_mod_source (the modulating tone, with the timebase error)
    }
    units = {"am_rate": "Hz", "fm_dev": "Hz", "fm_rate": "Hz", "pm_rate": "Hz"}

    def _source(self) -> RfModSource:
        assert self.owner is not None
        return self.owner.components[str(self.param("source"))]

    def angle_kind(self) -> str | None:
        """Which angle modulation is active: 'fm', 'pm' or None."""
        if self.param("fm"):
            return "fm"
        if self.param("pm"):
            return "pm"
        return None

    def _angle(self) -> tuple[float, float] | None:
        """(β, phase of order 1) of the active angle modulation, or None."""
        kind = self.angle_kind()
        if kind == "fm":
            return float(self.param("fm_dev")) / float(self.param("fm_rate")), RfModSource.phase("fm")
        if kind == "pm":
            return float(self.param("pm_dev")), RfModSource.phase("pm")
        return None

    def line_count(self, fm: bool, pm: bool, am: bool, beta: float) -> int:
        k = carson_orders(beta) if (fm or pm) else 0
        return (2 * k + 1) * (3 if am else 1)

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        key = bound.get(path)
        assert self.owner is not None
        if key == "pulm" and new:
            raise ScpiError(-221, "pulse modulation is not simulated")
        if key == "pm" and new and self.param("fm"):
            self.owner.set_state(self.bound_paths()["fm"], False)
            self.owner.push_error(-221, "")
        if key == "fm" and new and self.param("pm"):
            self.owner.set_state(self.bound_paths()["pm"], False)
            self.owner.push_error(-221, "")
        angle = self._angle()
        beta = angle[0] if angle else 0.0
        if (
            self.line_count(bool(self.param("fm")), bool(self.param("pm")), bool(self.param("am")), beta)
            > LINE_BUDGET
        ):
            raise ScpiError(-222, f"modulation index {beta:g} needs more than {LINE_BUDGET} lines")

    def output(self, port: str) -> Signal:
        if port != "out":
            raise NotImplementedError(f"{self.type_name} has no signal output {port!r}")
        carrier = self.input("in").merged()
        if not carrier.freq.size:
            return Signal.zero()
        angle = self._angle()
        if angle:
            beta, phi = angle
            rate = self._source().frequency(str(self.angle_kind()))
            k = carson_orders(beta)
            n = np.arange(-k, k + 1)
            freqs = (carrier.freq[:, None] + n[None, :] * rate).reshape(-1)
            amps = (carrier.amp[:, None] * (jv(n, beta) * np.exp(1j * n * phi))[None, :]).reshape(-1)
        else:
            freqs = carrier.freq
            amps = carrier.amp
        # AM multiplies every line by (1 + m·sin ω_m t): two sidebands of m/2 each.
        if self.param("am"):
            m = float(self.param("am_depth")) / 100
            fa = self._source().frequency("am")
            side = m / 2 / 1j
            freqs = np.concatenate([freqs, freqs + fa, freqs - fa])
            amps = np.concatenate([amps, amps * side, -amps * side])
        keep = (freqs > 0) & (np.abs(amps) > 0)
        return Signal.lines(freqs[keep], amps[keep])

    def readings(self) -> dict[str, Any]:
        angle = self._angle()
        return {
            "values": {
                "beta": angle[0] if angle else 0.0,
                "lines": int(self.output("out").freq.size),
                "omitted_power": omitted_power_fraction(angle[0]) if angle else 0.0,
            }
        }


@component("rf_level_control")
class RfLevelControl(Component):
    """The level control and ALC (D2 #188): the level in dBm into a matched 50 Ω load. The model's
    level is exact over the whole range (no ALC dynamics, settling or level error,
    validation-rfgen.md), so this block is the conversion from dBm to the carrier's EMF."""

    ports = {
        "in": PortSpec("in", "signal"),
        "out": PortSpec("out", "signal"),
    }
    defaults = {"level": -20.0}  # dBm into a matched load (a setting such as $power.level)
    units = {"level": "dBm"}

    def output(self, port: str) -> Signal:
        if port != "out":
            raise NotImplementedError(f"{self.type_name} has no signal output {port!r}")
        return self.input("in").scale(emf_peak(float(self.param("level"))))


@component("rf_attenuator")
class RfAttenuator(Component):
    """The output step attenuator (D2 #188). This model has no attenuation steps — its level is
    exact over the whole range (validation-rfgen.md) — so ``attenuation`` is 0 dB unless a
    definition sets it."""

    ports = {
        "in": PortSpec("in", "signal"),
        "out": PortSpec("out", "signal"),
    }
    defaults = {"attenuation": 0.0}  # dB of output attenuation
    units = {"attenuation": "dB"}

    def output(self, port: str) -> Signal:
        if port != "out":
            raise NotImplementedError(f"{self.type_name} has no signal output {port!r}")
        return self.input("in").scale(10 ** (-float(self.param("attenuation")) / 20))
