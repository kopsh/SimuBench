"""Source-measure unit, electronic load and a DC supply: piecewise-linear devices (validation-smu.md).

An SMU forces voltage or current and limits the other quantity in all four quadrants. When the
limit takes over, the device changes mode (CV <-> CC); the session re-solves until every
piecewise-linear device's mode agrees with the operating point, so a limit against any load,
linear or not, is solved exactly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from ...engine.circuit import GROUND, Circuit
from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError

OPEN = 9.91e37  # SCPI "not a number" for a resistance with no current
STAT_LIMIT = 8
CONDITION_LIMIT = 1 << 14


def _sign(x: float) -> float:
    return 1.0 if x >= 0 else -1.0


@component("smu_core")
class SmuCore(Component):
    """Source with a limit and a meter on HI (LO is the bench ground).

    Not a ``driver`` for bench checks: at its limit it sinks current, so a supply on the same net
    is a valid bench (quadrant 2).
    """

    ports = {"hi": PortSpec("inout", "electrical")}
    defaults = {
        "function": "VOLTage",  # VOLTage | CURRent: what the terminal is forced to
        "v_level": 0.0,  # V, setpoint in voltage mode
        "i_level": 0.0,  # A, setpoint in current mode
        "i_limit": 105e-6,  # A, current limit in voltage mode
        "v_limit": 21.0,  # V, voltage limit in current mode
        "output": False,  # False: the terminal floats
        "nplc": 1.0,  # reading time in power line cycles
        "autozero": True,  # autozero on: an extra phase and firmware per reading
        "line_freq": 60.0,  # Hz, mains frequency nplc refers to
        "delay": 0.0,  # s, wait before each reading
        "sweep_mode": "NONE",  # NONE | LINear | LOGarithmic | LIST
        "sweep_function": "VOLTage",  # VOLTage | CURRent: what a sweep varies
        "sweep_start": 0.0,  # V or A, first level
        "sweep_stop": 1.0,  # V or A, last level
        "sweep_points": 2,  # points of a linear or log sweep
        "sweep_delay": 0.0,  # s, wait before each sweep point
        "sweep_list": "",  # comma-separated levels of a LIST sweep
    }
    piecewise = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.mode = "V"  # which quantity the terminal is held to: V (voltage loop) or I (current loop)
        self.limit_sign = 1.0
        self.buffer: list[list[float]] = []

    # ---- settings -------------------------------------------------------------------------------

    def _forcing_voltage(self) -> bool:
        return self.param("function") == "VOLTage"

    def i_limit(self) -> float:
        """The current limit, within the terminal envelope: 1.05 A to 21 V, 105 mA above (≈ 22 W)."""
        lim = abs(float(self.param("i_limit")))
        if self._forcing_voltage() and abs(float(self.param("v_level"))) > 21.0:
            lim = min(lim, 0.105)
        return lim

    def v_limit(self) -> float:
        return abs(float(self.param("v_limit")))

    def on(self) -> bool:
        return bool(self.param("output"))

    def limited(self) -> bool:
        """True when the limit, not the setpoint, holds the terminal."""
        if not self.on():
            return False
        return (self.mode == "I") if self._forcing_voltage() else (self.mode == "V")

    def _targets(self) -> tuple[float, float]:
        """(voltage EMF in voltage mode, current in current mode)."""
        if self._forcing_voltage():
            return float(self.param("v_level")), self.limit_sign * self.i_limit()
        return self.limit_sign * self.v_limit(), float(self.param("i_level"))

    # ---- circuit --------------------------------------------------------------------------------

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        if not self.on():
            return
        if self.mode == "V":
            circuit.source(node("hi"), self.uid + "/v", 0.0)  # ideal: its current is solved exactly
        else:
            circuit.current(node("hi"), self.uid + "/i")

    def emf(self) -> dict[str, Signal]:
        if not self.on():
            return {}
        v, i = self._targets()
        if self.mode == "V":
            return {self.uid + "/v": Signal.constant(v)}
        return {self.uid + "/i": Signal.constant(i)}

    def update_mode(self, dc_voltage: Callable[[str], float]) -> bool:
        if not self.on():
            return False
        v_node = dc_voltage("hi")
        old = (self.mode, self.limit_sign)
        if self._forcing_voltage():
            setpoint = float(self.param("v_level"))
            if self.mode == "V":
                current = self.session.source_current(self.uid + "/v")
                if abs(current) > self.i_limit() * (1 + 1e-9):
                    self.mode, self.limit_sign = "I", _sign(current)
            elif self.limit_sign * (setpoint - v_node) < 0:
                self.mode, self.limit_sign = "V", 1.0  # the setpoint is reachable again
        else:
            setpoint = float(self.param("i_level"))
            if self.mode == "I":
                if abs(v_node) > self.v_limit() * (1 + 1e-9):
                    self.mode, self.limit_sign = "V", _sign(v_node)
            else:
                current = self.session.source_current(self.uid + "/v")
                if self.limit_sign * (setpoint - current) < 0:
                    self.mode, self.limit_sign = "I", 1.0
        return (self.mode, self.limit_sign) != old

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        if bound.get(path) == "line_freq" and float(new) not in (50.0, 60.0):
            raise ScpiError(-222, "line frequency is 50 or 60 Hz")
        if bound.get(path) in ("function", "output"):
            # A new source function or a restarted output starts from the setpoint's own loop.
            self.mode = "V" if self._forcing_voltage() else "I"
            self.limit_sign = 1.0

    def reset(self) -> None:
        self.mode, self.limit_sign = "V", 1.0
        self.buffer = []

    # ---- measuring ------------------------------------------------------------------------------

    def reading(self) -> tuple[float, float]:
        """(terminal voltage, current out of HI) at the operating point."""
        if not self.on():
            return 0.0, 0.0
        v_node = self.pin_signal("hi").mean()
        if self.mode == "V":
            return v_node, self.session.source_current(self.uid + "/v")
        return v_node, self._targets()[1]

    def reading_time(self, delay: float = 0.0) -> float:
        """Modelled time of one reading: 50 µs + delay + phases × (NPLC / line + 185 µs) + firmware."""
        phases, firmware = (3, 1.8e-3) if self.param("autozero") else (1, 0.3e-3)
        line = float(self.param("line_freq"))
        return 50e-6 + delay + phases * (float(self.param("nplc")) / line + 185e-6) + firmware

    def _row(self, t: float) -> list[float]:
        v, i = self.reading()
        res = v / i if abs(i) > 0 else OPEN
        return [v, i, res, t, float(STAT_LIMIT if self.limited() else 0)]

    def sweep_levels(self) -> list[float]:
        mode = self.param("sweep_mode")
        start, stop = float(self.param("sweep_start")), float(self.param("sweep_stop"))
        n = int(self.param("sweep_points"))
        if mode == "LINear":
            return [float(x) for x in np.linspace(start, stop, n)]
        if mode == "LOGarithmic":
            return [float(x) for x in np.geomspace(start, stop, n)]
        if mode == "LIST":
            return [float(x) for x in str(self.param("sweep_list")).split(",") if x.strip()]
        return []

    def _single(self) -> tuple[list[list[float]], float]:
        """One reading with the source as set: its row, and the time it took (source delay included)."""
        t = self.reading_time()
        return [self._row(t)], t + float(self.param("delay"))

    def _run(self) -> tuple[list[list[float]], float]:
        """Take the configured sweep (or one reading): rows, and the time it took.

        Every point is solved against the bench as it is, so any load, nonlinear ones included,
        gives its exact operating point at each level (validation-smu.md, deviations).
        """
        levels = self.sweep_levels()
        if not levels:
            return self._single()
        function = self.param("sweep_function")
        key = "v_level" if function == "VOLTage" else "i_level"
        saved = {k: self.overrides.get(k) for k in (key, "function", "output")}
        step = self.reading_time(float(self.param("sweep_delay")))
        rows: list[list[float]] = []
        try:
            self.overrides["function"] = function
            self.overrides["output"] = True  # a sweep drives the output for its duration
            for k, level in enumerate(levels):
                self.overrides[key] = level
                self.mode, self.limit_sign = ("V" if key == "v_level" else "I"), 1.0
                self.session.invalidate(electrical=True)
                rows.append(self._row((k + 1) * step))
        finally:
            for k, v in saved.items():
                if v is None:
                    self.overrides.pop(k, None)
                else:
                    self.overrides[k] = v
            self.mode, self.limit_sign = ("V" if self._forcing_voltage() else "I"), 1.0
            self.session.invalidate(electrical=True)
        return rows, len(levels) * (step + float(self.param("delay")))

    @staticmethod
    def _format(rows: list[list[float]]) -> bytes:
        return b",".join(V.format_float(x, 7) for row in rows for x in row)

    # ---- operations -----------------------------------------------------------------------------

    @op("read")
    def op_read(self, **_: Any) -> OpResult:
        rows, duration = self._run()
        self.buffer = rows
        return OpResult(self._format(rows), duration_ns=round(duration * 1e9))

    @op("initiate")
    def op_initiate(self, **_: Any) -> OpResult:
        rows, duration = self._run()
        self.buffer = rows
        return OpResult(duration_ns=round(duration * 1e9))

    @op("measure")
    def op_measure(self, **_: Any) -> OpResult:
        """One reading with the source as configured, whatever the sweep mode."""
        self.buffer, duration = self._single()
        return OpResult(self._format(self.buffer), duration_ns=round(duration * 1e9))

    @op("fetch")
    def op_fetch(self, **_: Any) -> OpResult:
        if not self.buffer:
            raise ScpiError(-230, "the buffer is empty")
        return OpResult(self._format(self.buffer))

    @op("buffer")
    def op_buffer(self, what: str = "count", **_: Any) -> OpResult:
        if what == "clear":
            self.buffer = []
            return OpResult()
        return OpResult(V.format_int(len(self.buffer)))

    @op("sweep")
    def op_sweep(
        self,
        function: str = "VOLTage",
        kind: str = "LINear",
        start: float | None = None,
        stop: float | None = None,
        points: int | None = None,
        delay: float | None = None,
        values: list[bytes] | None = None,
        **_: Any,
    ) -> OpResult:
        assert self.owner is not None
        limit = 210.0 if function == "VOLTage" else 1.05
        if kind == "LIST":
            levels = V.parse_float_list(tuple(values or ()))  # quoted or not (D13)
            if not 1 <= len(levels) <= 2500 or any(abs(x) > limit for x in levels):
                raise ScpiError(-222)
            self.owner.set_state("sweep.list", ",".join(repr(x) for x in levels))
        else:
            assert start is not None and stop is not None and points is not None
            if not 2 <= points <= 2500 or abs(start) > limit or abs(stop) > limit:
                raise ScpiError(-222)
            if kind == "LOGarithmic" and (start == 0 or stop == 0 or (start > 0) != (stop > 0)):
                raise ScpiError(-221, "a log sweep needs non-zero levels of one sign")
            self.owner.set_state("sweep.start", start)
            self.owner.set_state("sweep.stop", stop)
            self.owner.set_state("sweep.points", points)
        self.owner.set_state("sweep.delay", delay or 0.0)
        self.owner.set_state("sweep.function", function)
        self.owner.set_state("sweep.mode", kind)
        return OpResult()

    @op("regulation")
    def op_regulation(self, **_: Any) -> OpResult:
        self.session._solve()
        if not self.on():
            return OpResult(b"OFF")
        return OpResult(b"CV" if self.mode == "V" else b"CC")

    @op("tripped")
    def op_tripped(self, kind: str = "current", **_: Any) -> OpResult:
        self.session._solve()
        tripped = self.limited() and (kind == "current") == self._forcing_voltage()
        return OpResult(V.format_bool(tripped))

    @op("condition")
    def op_condition(self, **_: Any) -> OpResult:
        self.session._solve()
        return OpResult(V.format_int(CONDITION_LIMIT if self.limited() else 0))

    def readings(self) -> dict[str, Any]:
        self.session._solve()
        v, i = self.reading()
        return {
            "values": {
                "voltage": v,
                "current": i,
                "regulation": "OFF" if not self.on() else ("CV" if self.mode == "V" else "CC"),
                "limited": self.limited(),
                "forcing": "VOLTage" if self._forcing_voltage() else "CURRent",
                "limit": self.i_limit() if self._forcing_voltage() else self.v_limit(),
                "reading_time": self.reading_time(),
                "buffer_points": len(self.buffer),
            },
            # One trace per buffer element (VOLT, CURR, RES, TIME, STAT), indexed by point.
            "traces": [
                {"name": f"buffer_{name}", "t0_s": 0.0, "dt_s": 1.0, "values": [r[k] for r in self.buffer]}
                for k, name in enumerate(("v", "i", "r", "t", "stat"))
            ],
        }


@component("electronic_load")
class ElectronicLoad(Component):
    """Constant-current or constant-resistance sink on IN.

    In CC mode it sinks its setting wherever the input voltage allows; below the knee
    (setting × ``r_min``) its minimum operating resistance limits it, so 0 V sinks nothing.
    """

    ports = {"in": PortSpec("in", "electrical")}
    defaults = {
        "function": "CC",  # CC | CR: constant-current sink or constant resistance
        "current": 0.0,  # A, set current in CC
        "resistance": 1000.0,  # ohm, set resistance in CR
        "enabled": False,  # False: the input floats
        "r_min": 0.0367,  # minimum operating resistance: below set*r_min the CC mode sinks less
    }
    units = {"r_min": "OHM"}
    piecewise = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.mode = "R"  # R: limited by r_min; I: sinking the set current

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        if not self.param("enabled"):
            return
        if self.param("function") == "CR":
            circuit.resistor(node("in"), GROUND, float(self.param("resistance")))
        elif self.mode == "R":
            circuit.resistor(node("in"), GROUND, float(self.param("r_min")))
        else:
            circuit.current(node("in"), self.uid)

    def emf(self) -> dict[str, Signal]:
        if self.param("enabled") and self.param("function") == "CC" and self.mode == "I":
            return {self.uid: Signal.constant(-float(self.param("current")))}  # sinks
        return {}

    def update_mode(self, dc_voltage: Callable[[str], float]) -> bool:
        if not self.param("enabled") or self.param("function") != "CC":
            return False
        v = dc_voltage("in")
        i_set = float(self.param("current"))
        old = self.mode
        if self.mode == "R" and v / float(self.param("r_min")) > i_set * (1 + 1e-9):
            self.mode = "I"
        elif self.mode == "I" and v < i_set * float(self.param("r_min")):
            self.mode = "R"
        return self.mode != old

    def measured(self) -> tuple[float, float]:
        if not self.param("enabled"):
            return self.pin_signal("in").mean(), 0.0
        v = self.pin_signal("in").mean()
        if self.param("function") == "CR":
            return v, v / float(self.param("resistance"))
        if self.mode == "R":
            return v, v / float(self.param("r_min"))
        return v, float(self.param("current"))

    @op("measure")
    def op_measure(self, what: str = "voltage", **_: Any) -> OpResult:
        self.session._solve()
        v, i = self.measured()
        value = {"voltage": v, "current": i, "power": v * i}[what]
        return OpResult(V.format_float(value, 7))

    def readings(self) -> dict[str, Any]:
        self.session._solve()
        v, i = self.measured()
        return {"values": {"voltage": v, "current": i, "power": v * i, "regulating": self.mode == "I"}}


@component("dc_supply")
class DcSupply(Component):
    """A fixed DC source with an output resistance (a bench supply or a battery), to ground."""

    category = "part"
    label = "直流电源"
    label_en = "DC supply"
    role = "driver"
    units = {"v": "V", "r": "OHM"}
    ports = {"OUT": PortSpec("out", "electrical")}
    defaults = {
        "v": 12.0,  # EMF
        "r": 1000.0,  # output resistance
    }

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        circuit.source(node("OUT"), self.uid, max(float(self.param("r")), 1e-6))

    def emf(self) -> dict[str, Signal]:
        return {self.uid: Signal.constant(float(self.param("v")))}


__all__ = ["OPEN", "DcSupply", "ElectronicLoad", "SmuCore"]
