"""Source-measure unit, electronic load and a DC supply: piecewise-linear devices (validation-smu.md).

An SMU forces voltage or current and limits the other quantity in all four quadrants. When the
limit takes over, the device changes mode (CV <-> CC); the session re-solves until every
piecewise-linear device's mode agrees with the operating point, so a limit against any load,
linear or not, is solved exactly. Readings are taken by a ``trigger_model`` (the ``sampler``
protocol) and kept in a ``reading_buffer``; the sweep's points are the readings of one run.
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
from .ranging import auto_range, fitting_range, range_of_setting, ranges_of, reading_time
from .trigger import Reading, ReadingBuffer

OPEN = 9.91e37  # SCPI "not a number" for a resistance with no current
STAT_LIMIT = 8
CONDITION_LIMIT = 1 << 14


def _sign(x: float) -> float:
    return 1.0 if x >= 0 else -1.0


def _flag(value: Any) -> bool:
    return value if not isinstance(value, str) else value.upper() in ("1", "ON", "TRUE")


@component("smu_core")
class SmuCore(Component):
    """Source with a limit and a meter on HI (LO is the bench ground).

    Not a ``driver`` for bench checks: at its limit it sinks current, so a supply on the same net
    is a valid bench (quadrant 2).

    Readings go through a ``trigger_model`` (the ``sampler`` protocol, metrology-authoring.md 3.4):
    one reading takes its NPLC time plus the source delay; a sweep's points are the readings of one
    run, each point's level driving the terminal while the run lasts. The measure side ranges like
    the meter core (#87): ``measure_ranges`` (per function: the voltage and current readings), the
    manual ``measure_range_voltage`` / ``measure_range_current``, ``measure_autorange`` with the
    same hysteresis, and a reading beyond ``overrange`` × full scale reads ``overload_value``.

    The source envelope comes from ``source_ranges`` (the full-scale values per function) with
    ``range_limits``: per function, the cap the other quantity's limit gets on each range — the
    default table carries the 21 V / 105 mA boundary (210 V range: 105 mA). A manual
    ``source_range`` takes the smallest fitting range (the meter's rule) and the envelope follows
    it; ``source_autorange`` follows the level instead.
    """

    ports = {"hi": PortSpec("inout", "electrical")}
    units = {"source_range": "V", "measure_range_voltage": "V", "measure_range_current": "A"}
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
        "source_ranges": {
            "VOLTage": [2.1, 21.0, 210.0]
        },  # full-scale value of each source range, per function
        "source_range": None,  # manual source range: a value the range must be able to source
        "source_autorange": True,  # the source range follows the level
        "range_limits": {"VOLTage": {2.1: 1.05, 21.0: 1.05, 210.0: 0.105}},  # limit cap per source range
        "measure_ranges": None,  # full-scale value of each measure range, per function
        "measure_range_voltage": None,  # manual measure range of the voltage reading (None: auto)
        "measure_range_current": None,  # manual measure range of the current reading (None: auto)
        "measure_autorange": True,  # the measure range follows the reading
        "autorange_up": 1.2,  # fraction of full scale above which the range switches up
        "autorange_down": 0.1,  # fraction of full scale below which the range switches down
        "overrange": 1.2,  # a reading beyond this fraction of full scale is an overload
        "overload_value": 9.9e37,  # the reading an overload produces
        "buffer": "",  # the reading_buffer whose contents the front panel shows ('' for none)
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
        self._auto_range: dict[str, float] = {}  # each measure function's auto-chosen range
        self._saved: dict[str, Any] | None = None  # source settings a run drove (restored on run_done)
        self._point: tuple[float, float, float, float] = (0.0, 0.0, OPEN, 0.0)  # the last reading

    # ---- settings -------------------------------------------------------------------------------

    def _forcing_voltage(self) -> bool:
        return self.param("function") == "VOLTage"

    def _source_range(self) -> float | None:
        """The source range in use for the function being forced (None: the function does not
        range): the manual setting's, or the auto one, which follows the level."""
        function = self.param("function")
        ranges = ranges_of(self.param("source_ranges"), function)
        if not ranges:
            return None
        if not _flag(self.param("source_autorange")):
            return range_of_setting(ranges, self.param("source_range"))
        return fitting_range(
            ranges, abs(float(self.param("v_level" if function == "VOLTage" else "i_level")))
        )

    def i_limit(self) -> float:
        """The current limit, within the terminal envelope: the range_limits cap of the source
        range in use (the default table's last entry is the 21 V / 105 mA boundary)."""
        lim = abs(float(self.param("i_limit")))
        if self._forcing_voltage():
            cap = (self.param("range_limits") or {}).get("VOLTage", {}).get(self._source_range())
            if cap is not None:
                lim = min(lim, abs(float(cap)))
        return lim

    def v_limit(self) -> float:
        lim = abs(float(self.param("v_limit")))
        if not self._forcing_voltage():
            cap = (self.param("range_limits") or {}).get("CURRent", {}).get(self._source_range())
            if cap is not None:
                lim = min(lim, abs(float(cap)))
        return lim

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
        self._auto_range = {}
        self.run_done()

    # ---- measuring ------------------------------------------------------------------------------

    def reading(self) -> tuple[float, float]:
        """(terminal voltage, current out of HI) at the operating point."""
        if not self.on():
            return 0.0, 0.0
        v_node = self.pin_signal("hi").mean()
        if self.mode == "V":
            return v_node, self.session.source_current(self.uid + "/v")
        return v_node, self._targets()[1]

    def _measure_range(self, function: str, value: float) -> float | None:
        """The measure range a reading of ``function`` uses (None: the function does not range):
        the manual setting's, or the auto one, chosen like the meter core's (#87)."""
        ranges = ranges_of(self.param("measure_ranges"), function)
        if not ranges:
            return None
        if not _flag(self.param("measure_autorange")):
            manual = self.param("measure_range_voltage" if function == "VOLTage" else "measure_range_current")
            return range_of_setting(ranges, manual)
        r = auto_range(
            ranges,
            self._auto_range.get(function),
            value,
            float(self.param("autorange_up")),
            float(self.param("autorange_down")),
        )
        self._auto_range[function] = r
        return r

    def _measured(self) -> tuple[float, float]:
        """The two readings as the instrument reports them: the measure ranges applied (an overload
        reads ``overload_value``)."""
        v, i = self.reading()
        return self._clipped(v, i)

    def _clipped(self, v: float, i: float) -> tuple[float, float]:
        for function, ref in (("VOLTage", "v"), ("CURRent", "i")):
            value = v if ref == "v" else i
            r = self._measure_range(function, value)
            if r is not None and abs(value) > float(self.param("overrange")) * r:
                if ref == "v":
                    v = float(self.param("overload_value"))
                else:
                    i = float(self.param("overload_value"))
        return v, i

    def _row(self, t: float) -> list[float]:
        """A reading's row as the reading commands write it: VOLT, CURR, RES, TIME, STAT."""
        v_raw, i_raw = self.reading()
        v, i = self._clipped(v_raw, i_raw)
        res = v_raw / i_raw if abs(i_raw) > 0 else OPEN
        return [v, i, res, t, float(STAT_LIMIT if self.limited() else 0)]

    def _format(self, rows: list[list[float]]) -> bytes:
        return b",".join(V.format_float(x, 7) for row in rows for x in row)

    # ---- a Sampler for trigger_model (metrology-authoring.md 3.4) -----------------------------------

    def reading_time(self, sweep_delay: float | None = None) -> float:
        """Modelled time of one reading: 50 µs + delay + phases × (NPLC / line + 185 µs) + firmware
        (the rule the meter core shares, ranging.py)."""
        if sweep_delay is None:
            sweep_delay = float(self.param("sweep_delay")) if self.sweep_levels() else 0.0
        return (
            reading_time(
                float(self.param("nplc")), float(self.param("line_freq")), bool(self.param("autozero"))
            )
            + sweep_delay
        )

    def sample_time_ns(self) -> int:
        """One reading of a run: its reading time plus the source delay."""
        return round((self.reading_time() + float(self.param("delay"))) * 1e9)

    def run_samples(self) -> int | None:
        """A run is the configured sweep: as many readings as it has points (None: one reading)."""
        levels = self.sweep_levels()
        return len(levels) or None

    def run_begin(self) -> None:
        """A run starts: remember the source settings; the sweep drives them until run_done."""
        self._saved = {k: self.overrides.get(k) for k in ("v_level", "i_level", "function", "output")}

    def run_done(self) -> None:
        """The run is over: the source settings are the operator's again."""
        saved, self._saved = self._saved, None
        if saved is None:
            return
        for k, v in saved.items():
            if v is None:
                self.overrides.pop(k, None)
            else:
                self.overrides[k] = v
        self.mode, self.limit_sign = ("V" if self._forcing_voltage() else "I"), 1.0
        self.session.invalidate(electrical=True)

    def sample(self, index: int) -> float:
        """One reading of a run: a sweep's point ``index`` drives the terminal while it is taken
        (validation-smu.md: every point is solved against the bench as it is)."""
        levels = self.sweep_levels()
        if levels:
            function = str(self.param("sweep_function"))
            key = "v_level" if function == "VOLTage" else "i_level"
            self.overrides["function"] = function
            self.overrides["output"] = True  # a sweep drives the output for its duration
            self.overrides[key] = levels[min(index, len(levels) - 1)]
            self.mode, self.limit_sign = ("V" if key == "v_level" else "I"), 1.0
            self.session.invalidate(electrical=True)
        v_raw, i_raw = self.reading()
        v, i = self._clipped(v_raw, i_raw)
        res = v_raw / i_raw if abs(i_raw) > 0 else OPEN
        self._point = (v, i, res, float(STAT_LIMIT if self.limited() else 0))
        return v if self._forcing_voltage() else i

    def sample_fields(self, index: int) -> dict[str, float]:
        """The reading's own elements: TIME is the reading's place in the run (the reading times,
        not counting the source delay between them)."""
        v, i, res, stat = self._point
        return {"VOLT": v, "CURR": i, "RES": res, "TIME": (index + 1) * self.reading_time(), "STAT": stat}

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

    @staticmethod
    def _format(rows: list[list[float]]) -> bytes:
        return b",".join(V.format_float(x, 7) for row in rows for x in row)

    # ---- operations -----------------------------------------------------------------------------

    @op("measure")
    def op_measure(self, **_: Any) -> OpResult:
        """One reading with the source as configured, whatever the sweep mode; it replaces the
        reading memory (one reading is what the panel and ``FETCh?`` show until the next run)."""
        t = self.reading_time(0.0)
        row = self._row(t)
        buffer = self._reading_buffer()
        if buffer is not None and self.owner is not None:
            buffer.clear_buffer()
            buffer.append(
                Reading(
                    row[0] if self._forcing_voltage() else row[1],
                    self.owner.now_ns() + round((t + float(self.param("delay"))) * 1e9),
                    {"VOLT": row[0], "CURR": row[1], "RES": row[2], "TIME": t, "STAT": row[4]},
                )
            )
        return OpResult(self._format([row]), duration_ns=round((t + float(self.param("delay"))) * 1e9))

    @op("range")
    def op_range(self, function: str = "VOLTage", **_: Any) -> OpResult:
        """The measure range a function uses (the auto-chosen one included; reports it, the
        auto-range decision happens in a reading)."""
        ranges = ranges_of(self.param("measure_ranges"), function)
        if not ranges:
            raise ValueError(f"{self.uid}: no measure ranges for the {function} function")
        if _flag(self.param("measure_autorange")):
            r = self._auto_range.get(function)
            if r is None:
                v, i = self.reading()
                r = fitting_range(ranges, v if function == "VOLTage" else i)
        else:
            r = range_of_setting(
                ranges,
                self.param("measure_range_voltage" if function == "VOLTage" else "measure_range_current"),
            )
        if r is None:
            raise ValueError(f"{self.uid}: the {function} function has no usable measure range")
        return OpResult(V.format_float(r, 7))

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
        v, i = self._measured()
        points = self._buffer_points()
        return {
            "values": {
                "voltage": v,
                "current": i,
                "regulation": "OFF" if not self.on() else ("CV" if self.mode == "V" else "CC"),
                "limited": self.limited(),
                "forcing": "VOLTage" if self._forcing_voltage() else "CURRent",
                "limit": self.i_limit() if self._forcing_voltage() else self.v_limit(),
                "reading_time": self.reading_time(0.0),
                "buffer_points": len(points),
            },
            # One trace per reading element (VOLT, CURR, RES, TIME, STAT), indexed by point.
            "traces": [
                {"name": f"buffer_{name}", "t0_s": 0.0, "dt_s": 1.0, "values": [r.fields[k] for r in points]}
                for name, k in (("v", "VOLT"), ("i", "CURR"), ("r", "RES"), ("t", "TIME"), ("stat", "STAT"))
            ],
        }

    def _reading_buffer(self) -> ReadingBuffer | None:
        """The reading memory the definition names (None when it does not)."""
        name = str(self.param("buffer") or "")
        if not name or self.owner is None:
            return None
        buffer = self.owner.components.get(name)
        return buffer if isinstance(buffer, ReadingBuffer) else None

    def _buffer_points(self) -> list[Any]:
        """The readings the front panel shows."""
        buffer = self._reading_buffer()
        return list(buffer.buffers[buffer._default()]) if buffer is not None else []


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
