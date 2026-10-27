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
    the amplitude is the open-circuit voltage.
    """

    ports = {
        "in": PortSpec("in", "signal"),
        "pin": PortSpec("out", "electrical"),
    }
    defaults = {"z_out": 50.0, "enabled": True, "load_setting": math.inf}

    def _scale(self) -> float:
        z = float(self.param("z_out"))
        rl = float(self.param("load_setting"))
        if not math.isfinite(rl) or rl <= 0:
            return 1.0
        return (rl + z) / rl

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
    _NAMED = {"FIFTY": 50.0, "ONEMEG": 1e6}

    def impedance(self) -> float:
        z = self.param("z_in")
        if isinstance(z, str):
            return self._NAMED[z.upper()]
        return float(z)

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
    ports = {"A": PortSpec("inout", "electrical"), "B": PortSpec("inout", "electrical")}
    defaults = {"r": 1000.0}

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        circuit.resistor(node("A"), node("B"), float(self.param("r")))


@component("rc_lowpass")
class RcLowpass(Component):
    """Series R from IN to OUT, C from OUT to ground. ``tol_r`` / ``tol_c`` are hidden tolerances."""

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
    """An ideal short cable: a small series resistance (no delay or reflections)."""

    ports = {"IN": PortSpec("inout", "electrical"), "OUT": PortSpec("inout", "electrical")}
    defaults = {"r": 0.05, "length_m": 1.0}

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        circuit.resistor(node("IN"), node("OUT"), max(float(self.param("r")), 1e-6))


@component("unity_buffer")
class UnityBuffer(Component):
    """Unity-gain buffer: high-impedance input, low-impedance output."""

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
