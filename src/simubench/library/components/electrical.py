"""Electrical components: output stages, input ports, passives, probes, buffers."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np

from ...engine.circuit import GROUND, Circuit
from ...engine.component import Component, PortSpec, component
from ...engine.signal import Signal


def _flag(value: Any) -> bool:
    return bool(value) if not isinstance(value, str) else value.upper() in ("1", "ON", "TRUE")


@component("output_stage")
class OutputStage(Component):
    """A Thevenin source driving an electrical pin from a signal input.

    ``load_setting`` is the load the instrument assumes when it defines its amplitude (D13): the
    source EMF is scaled so that a load of that value would see the requested voltage. ``inf`` means
    the amplitude is the open-circuit voltage; so does any setting of ``hiz_from`` ohms or more, for
    instruments whose high-impedance setting is a number (a calibrator's ``LOAD 10000``).
    """

    ports = {
        "in": PortSpec("in", "signal"),
        "pin": PortSpec("out", "electrical"),
    }
    defaults = {
        "z_out": 50.0,
        "enabled": True,
        "load_setting": math.inf,
        "invert": False,
        "hiz_from": math.inf,
    }

    def _scale(self) -> float:
        """EMF per volt of setting: the load the amplitude is defined for, and the polarity."""
        z = float(self.param("z_out"))
        rl = float(self.param("load_setting"))
        sign = -1.0 if _flag(self.param("invert")) else 1.0
        if not math.isfinite(rl) or rl <= 0 or rl >= float(self.param("hiz_from")):
            return sign
        return sign * (rl + z) / rl

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        if _flag(self.param("enabled")):
            circuit.source(node("pin"), self.uid, float(self.param("z_out")))

    def emf(self) -> dict[str, Signal]:
        if not _flag(self.param("enabled")):
            return {}
        return {self.uid: self.input("in").scale(self._scale())}


@component("input_port")
class InputPort(Component):
    """An electrical input with a resistive (and optional capacitive) impedance to ground."""

    ports = {
        "pin": PortSpec("in", "electrical"),
        "out": PortSpec("out", "signal"),
    }
    defaults = {"z_in": 1e6, "c_in": 0.0}
    units = {"z_in": "OHM", "c_in": "F"}
    _NAMED = {"FIFTY": 50.0, "FIFT": 50.0, "ONEMEG": 1e6, "ONEM": 1e6}

    @classmethod
    def impedance_of(cls, params: dict[str, Any]) -> float:
        """The resistance to ground for these parameter values (also used by the bench validator)."""
        z = params["z_in"]
        if isinstance(z, str):
            named = cls._NAMED.get(z.upper())
            return named if named is not None else float(z)
        return float(z)

    def impedance(self) -> float:
        return self.impedance_of({k: self.param(k) for k in ("z_in", *self.defaults)})

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        n = node("pin")
        circuit.resistor(n, GROUND, self.impedance())
        c = float(self.param("c_in") or 0.0)
        if c > 0:
            circuit.capacitor(n, GROUND, c)

    def output(self, port: str) -> Signal:
        return self.pin_signal("pin")


@component("resistor")
class Resistor(Component):
    category = "dut"
    label = "电阻"
    label_en = "Resistor"
    units = {"r": "OHM"}
    ports = {"A": PortSpec("inout", "electrical"), "B": PortSpec("inout", "electrical")}
    defaults = {"r": 1000.0}

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        circuit.resistor(node("A"), node("B"), float(self.param("r")))


@component("load_resistor")
class LoadResistor(Component):
    """A resistor from its one terminal to the bench ground (a load on a single-ended net)."""

    category = "dut"
    label = "对地电阻"
    label_en = "Resistor to ground"
    units = {"r": "OHM"}
    ports = {"A": PortSpec("inout", "electrical")}
    defaults = {"r": 1000.0}

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        circuit.resistor(node("A"), GROUND, float(self.param("r")))


@component("rc_lowpass")
class RcLowpass(Component):
    """Series R from IN to OUT, C from OUT to ground. ``tol_r`` / ``tol_c`` are hidden tolerances."""

    category = "dut"
    label = "RC 低通"
    label_en = "RC low-pass"
    units = {"r": "OHM", "c": "F"}

    ports = {"IN": PortSpec("in", "electrical"), "OUT": PortSpec("out", "electrical")}
    defaults = {"r": 1000.0, "c": 100e-9, "tol_r": 0.0, "tol_c": 0.0}

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        r = float(self.param("r")) * (1 + float(self.param("tol_r")))
        c = float(self.param("c")) * (1 + float(self.param("tol_c")))
        circuit.resistor(node("IN"), node("OUT"), r)
        circuit.capacitor(node("OUT"), GROUND, c)


@component("passive_probe")
class PassiveProbe(Component):
    """A divider probe: series resistance from tip (IN) to the scope connector (OUT).

    With ``attenuation`` N and ``r_tip`` the total tip resistance into the nominal scope input
    ``r_scope`` (1 MΩ), the series element is ``r_tip - r_scope`` (9 MΩ for a 10x, 10 MΩ probe).
    The division then follows from the scope's actual input impedance, as on a real bench.
    Probe capacitance and compensation are not modeled (transients are out of scope, D10).
    """

    category = "accessory"
    label = "无源探头"
    label_en = "Passive probe"
    units = {"r_tip": "OHM", "r_scope": "OHM", "c_pf": "pF"}

    ports = {"IN": PortSpec("in", "electrical"), "OUT": PortSpec("out", "electrical")}
    defaults = {"attenuation": 10.0, "r_tip": 10e6, "r_scope": 1e6, "c_pf": 0.0}

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        r_series = float(self.param("r_tip")) - float(self.param("r_scope"))
        if r_series <= 0:
            circuit.resistor(node("IN"), node("OUT"), 1e-3)
        else:
            circuit.resistor(node("IN"), node("OUT"), r_series)


@component("coax_cable")
class CoaxCable(Component):
    """A short cable: a small series resistance (no delay or reflections).

    ``r_skin_1mhz`` adds the skin effect: a series impedance ``r_skin_1mhz * sqrt(f / 1 MHz) * (1 + j)``
    (resistance and internal reactance rise together with the square root of frequency), which
    makes a 50 Ω path lose a little more at higher frequencies and a square wave settle slowly
    after each edge. A lumped model, valid while the cable is short against the wavelength.
    """

    category = "accessory"
    label = "同轴线缆"
    label_en = "Coax cable"
    units = {"r": "OHM", "length_m": "m", "r_skin_1mhz": "OHM"}

    ports = {"IN": PortSpec("inout", "electrical"), "OUT": PortSpec("inout", "electrical")}
    defaults = {"r": 0.05, "length_m": 1.0, "r_skin_1mhz": 0.0}

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        r = max(float(self.param("r")), 1e-6)
        skin = float(self.param("r_skin_1mhz"))
        if skin <= 0:
            circuit.resistor(node("IN"), node("OUT"), r)
            return

        def y(f: np.ndarray, r: float = r, skin: float = skin) -> np.ndarray:
            return 1.0 / (r + skin * np.sqrt(np.abs(np.asarray(f, dtype=float)) / 1e6) * (1 + 1j))

        circuit.admittance(node("IN"), node("OUT"), y)


@component("unity_buffer")
class UnityBuffer(Component):
    """Unity-gain buffer: high-impedance input, low-impedance output."""

    category = "dut"
    label = "单位增益缓冲器"
    label_en = "Unity-gain buffer"
    units = {"z_in": "OHM", "z_out": "OHM"}

    ports = {"IN": PortSpec("in", "electrical"), "OUT": PortSpec("out", "electrical")}
    defaults = {"z_in": 1e6, "z_out": 50.0, "gain": 1.0}

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        circuit.resistor(node("IN"), GROUND, float(self.param("z_in")))
        g = float(self.param("gain"))
        circuit.controlled(
            node("OUT"),
            node("IN"),
            lambda f: np.full(np.shape(f), g, dtype=complex),
            float(self.param("z_out")),
        )
