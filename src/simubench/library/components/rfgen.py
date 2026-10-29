"""RF signal generator: carrier, exact AM, Bessel FM and PM to Carson's rule, list and step sweeps,
and a timebase whose error every output frequency carries (validation-rfgen.md)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.special import jv

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError

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


@component("rf_generator_core")
class RfGeneratorCore(Component):
    """Lines of the RF output and the 10 MHz reference output.

    ``offset_ppm`` is the timebase error: hidden state (D9), readable only by measuring.
    """

    ports = {
        "rf": PortSpec("out", "signal"),
        "ref": PortSpec("out", "signal"),
        "ext": PortSpec("in", "signal", "REF_IN"),
    }
    defaults = {
        "frequency": 1e9,  # Hz of the CW carrier
        "level": -20.0,  # dBm into a matched load
        "mode": "CW",  # CW | LIST | otherwise the start/stop sweep
        "am": False,  # amplitude modulation on
        "am_depth": 0.0,  # percent modulation depth
        "am_rate": 1e3,  # Hz of the AM tone
        "fm": False,  # frequency modulation on (FM and PM are exclusive)
        "fm_dev": 0.0,  # Hz peak deviation
        "fm_rate": 1e3,  # Hz of the FM tone
        "pm": False,  # phase modulation on (FM and PM are exclusive)
        "pm_dev": 0.0,  # rad of phase deviation (the modulation index beta)
        "pm_rate": 1e3,  # Hz of the PM tone
        "pulm": False,  # pulse modulation (setting it errors: not simulated)
        "ref_out": True,  # the 10 MHz reference output on
        "ref_source": "INTernal",  # INTernal | EXTernal 10 MHz timebase
        "offset_ppm": 0.0,  # timebase error, ppm (hidden state, D9)
        "manual": 1,  # current sweep point, 1-based
        "start": 1e9,  # Hz, first frequency of the sweep
        "stop": 2e9,  # Hz, last frequency of the sweep
        "points": 2,  # points of the sweep
        "spacing": "LINear",  # LINear | LOGarithmic
    }
    units = {"offset_ppm": "ppm"}
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.list_freqs: list[float] = []
        self._ext_error: float | None = None
        self._seen = -1

    # ---- timebase -------------------------------------------------------------------------------

    def error(self) -> float:
        """Fractional frequency error of the timebase in use."""
        if self.param("ref_source") == "EXTernal" and self._ext_error is not None:
            return self._ext_error
        return float(self.param("offset_ppm")) * 1e-6

    def sync(self) -> None:
        if self._seen == self.session._signal_version:
            return
        self._seen = self.session._signal_version
        ext = None
        if self.param("ref_source") == "EXTernal":
            sig = self.input("ext").merged()
            if sig.freq.size:
                k = int(np.argmax(np.abs(sig.amp)))
                f = float(sig.freq[k])
                if abs(f / 10e6 - 1) <= 10e-6 and abs(sig.amp[k]) > 0.05:
                    ext = f / 10e6 - 1
        if ext != self._ext_error:
            self._ext_error = ext
            self.session.invalidate()

    # ---- frequency plan -------------------------------------------------------------------------

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

    # ---- modulation -----------------------------------------------------------------------------

    def _angle(self) -> tuple[float, float, float] | None:
        """(β, rate, phase of order 1) of the active angle modulation, or None."""
        if self.param("fm"):
            rate = float(self.param("fm_rate"))
            return float(self.param("fm_dev")) / rate, rate, -math.pi / 2
        if self.param("pm"):
            return float(self.param("pm_dev")), float(self.param("pm_rate")), 0.0
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
        eps = self.error()
        if port == "ref":
            if not self.param("ref_out"):
                return Signal.zero()
            return Signal.lines([10e6 * (1 + eps)], [emf_peak(REF_DBM)])
        fc = self.frequency()
        a = emf_peak(float(self.param("level")))
        # Angle modulation: J_n(β) at fc + n·fm.
        angle = self._angle()
        if angle:
            beta, rate, phi = angle
            k = carson_orders(beta)
            n = np.arange(-k, k + 1)
            freqs = fc + n * rate
            amps = a * jv(n, beta) * np.exp(1j * n * phi)
        else:
            rate = 0.0
            freqs = np.array([fc])
            amps = np.array([a], dtype=complex)
        # AM multiplies every line by (1 + m·sin ω_m t): two sidebands of m/2 each.
        if self.param("am"):
            m = float(self.param("am_depth")) / 100
            fa = float(self.param("am_rate"))
            side = m / 2 / 1j
            freqs = np.concatenate([freqs, freqs + fa, freqs - fa])
            amps = np.concatenate([amps, amps * side, -amps * side])
        keep = (freqs > 0) & (np.abs(amps) > 0)
        return Signal.lines(freqs[keep] * (1 + eps), amps[keep])

    # ---- operations -----------------------------------------------------------------------------

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
        angle = self._angle()
        return {
            "values": {
                "frequency": self.frequency(),
                "point": int(self.param("manual")) if self.param("mode") != "CW" else 0,
                "points": len(self.sweep_points()) if self.param("mode") != "CW" else 0,
                "beta": angle[0] if angle else 0.0,
                "lines": int(self.output("rf").freq.size),
                "omitted_power": omitted_power_fraction(angle[0]) if angle else 0.0,
                "reference_locked": self.param("ref_source") != "EXTernal" or self._ext_error is not None,
            }
        }
