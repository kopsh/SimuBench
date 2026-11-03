"""Source-measure unit, electronic load and a DC supply: piecewise-linear devices (validation-smu.md).

Since #184 (B1) the source-measure unit is built from its real functional blocks (D50,
component-map.md smu), not one block: ``smu_source`` (the source DAC's setting and sweep),
``output_control_loop`` (the voltage/current loop and its limit switch, D34), ``smu_output``
(HI/LO, sense, floating and the output relay) and ``smu_meter`` (the V/I meter, its ranges and the
sampler the trigger model reads). The loop is generic — a definition binds the setpoint, limits
and output setting to it — so a power supply's output stage reuses it (B2, #185).

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
#: Resistance of the internal bond from the LO terminal to the bench ground when the LO is not
#: floating: small enough to be invisible at reading precision, real enough that two SMUs' bonded
#: LOs on one net stay solvable.
BOND_R = 1e-6


def _sign(x: float) -> float:
    return 1.0 if x >= 0 else -1.0


def _flag(value: Any) -> bool:
    return value if not isinstance(value, str) else value.upper() in ("1", "ON", "TRUE")


@component("smu_source")
class SmuSource(Component):
    """The source DAC's setting (#184 B1): source range, the sweep, and the range envelope.

    It stamps no circuit element. The control loop takes the level from its own bindings and the
    output stage turns the loop's target into a circuit source; this component owns the *ranges*:
    ``source_ranges`` (full-scale values per function) with ``range_limits`` (the cap the other
    quantity's limit gets on each range, the 21 V / 105 mA boundary by default), and the sweep
    (linear, logarithmic or list) that drives a run's levels. The control loop asks it to cap a
    limit for the level in use (:meth:`limit_cap`), so the envelope follows a sweep point too.
    """

    units = {
        "source_range": "V",  # legacy: both functions
        "source_range_voltage": "V",
        "source_range_current": "A",
    }
    defaults = {
        "source_ranges": {
            "VOLTage": [2.1, 21.0, 210.0]
        },  # full-scale value of each source range, per function
        "source_range": None,  # legacy manual range for both functions (new definitions bind per function)
        "source_range_voltage": None,  # manual voltage-source range
        "source_range_current": None,  # manual current-source range
        "source_autorange": True,  # legacy: both functions follow the level
        "source_autorange_voltage": None,  # per-function autorange; None falls back to source_autorange
        "source_autorange_current": None,  # per-function autorange; None falls back to source_autorange
        "range_limits": {"VOLTage": {2.1: 1.05, 21.0: 1.05, 210.0: 0.105}},  # limit cap per range
        "sweep_mode": "NONE",  # NONE | LINear | LOGarithmic | LIST
        "sweep_function": "VOLTage",  # VOLTage | CURRent: what a sweep varies
        "sweep_start": 0.0,  # V or A, first level
        "sweep_stop": 1.0,  # V or A, last level
        "sweep_points": 2,  # points of a linear or log sweep
        "sweep_delay": 0.0,  # s, wait before each sweep point
        "sweep_list": "",  # comma-separated levels of a LIST sweep
    }

    def _per_function(self, function: str, key: str, legacy: str) -> Any:
        """The per-function setting, or the legacy shared one when it is not bound."""
        value = self.param(f"{key}_{'voltage' if function.lower().startswith('v') else 'current'}")
        return self.param(legacy) if value is None else value

    def manual_source_range(self, function: str) -> Any:
        return self._per_function(function, "source_range", "source_range")

    def source_range(self, function: str, level: float) -> float | None:
        """The source range in use for a function at a level (None: the function does not range):
        the manual setting's, or the auto one, which follows the level. The manual range and the
        autorange are each kept per function (voltage and current do not move one another)."""
        ranges = ranges_of(self.param("source_ranges"), function)
        if not ranges:
            return None
        if not _flag(self._per_function(function, "source_autorange", "source_autorange")):
            return range_of_setting(ranges, self.manual_source_range(function))
        return fitting_range(ranges, abs(level))

    def limit_cap(self, function: str, level: float) -> float | None:
        """The cap the envelope puts on the other quantity's limit on the range in use."""
        range_used = self.source_range(function, level)
        return (self.param("range_limits") or {}).get(function, {}).get(range_used)

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


@component("output_control_loop")
class OutputControlLoop(Component):
    """The voltage/current control loop and its limit switch (D34), reusable by any output stage.

    A definition binds the setpoint (``function``, ``v_level``, ``i_level``), the limits
    (``i_limit``, ``v_limit``) and the output setting to it. The piecewise output stage calls
    :meth:`update_mode` with the metered terminal voltage and the source current; the loop picks
    CV or CC and the sign of a current-limited source, as the SMU's output stage does.

    ``envelope`` optionally names a component whose ``limit_cap(function, level)`` caps a limit by
    the range in use (the SMU's source DAC); a supply that has no such table leaves it empty. A
    channel-level loop names its channel's ``power_stage`` in ``channel_envelope`` instead (B2).
    """

    refs = {
        "envelope": ("smu_source", "instrument"),
        "channel_envelope": ("power_stage", "channel"),
    }
    optional_refs = frozenset({"envelope", "channel_envelope"})
    defaults = {
        "function": "VOLTage",  # VOLTage | CURRent: what the terminal is forced to
        "v_level": 0.0,  # V, setpoint in voltage mode
        "i_level": 0.0,  # A, setpoint in current mode
        "i_limit": 105e-6,  # A, current limit in voltage mode
        "v_limit": 21.0,  # V, voltage limit in current mode
        "output": False,  # the output stage is enabled
        "envelope": "",  # instrument-level component capping the limits by the range in use
        "channel_envelope": "",  # a channel's power_stage (B2), same interface
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.mode = "V"  # which quantity the terminal is held to: V (voltage loop) or I (current loop)
        self.limit_sign = 1.0

    # ---- the settings ----------------------------------------------------------------------------

    def forcing_voltage(self) -> bool:
        return self.param("function") == "VOLTage"

    def on(self) -> bool:
        return bool(self.param("output"))

    def _envelope(self) -> Component | None:
        if self.owner is None:
            return None
        for key in ("envelope", "channel_envelope"):
            comp = self.peer(str(self.param(key) or ""))
            if comp is not None:
                return comp
        return None

    def i_limit(self) -> float:
        """The current limit, within the terminal envelope: the range_limits cap of the source
        range in use (the default table's last entry is the 21 V / 105 mA boundary)."""
        lim = abs(float(self.param("i_limit")))
        envelope = self._envelope()
        if self.forcing_voltage() and envelope is not None:
            cap = envelope.limit_cap("VOLTage", float(self.param("v_level")))
            if cap is not None:
                lim = min(lim, abs(float(cap)))
        return lim

    def v_limit(self) -> float:
        lim = abs(float(self.param("v_limit")))
        envelope = self._envelope()
        if not self.forcing_voltage() and envelope is not None:
            cap = envelope.limit_cap("CURRent", float(self.param("i_level")))
            if cap is not None:
                lim = min(lim, abs(float(cap)))
        return lim

    def limited(self) -> bool:
        """True when the limit, not the setpoint, holds the terminal."""
        if not self.on():
            return False
        return (self.mode == "I") if self.forcing_voltage() else (self.mode == "V")

    def targets(self) -> tuple[float, float]:
        """(voltage EMF in voltage mode, current in current mode)."""
        if self.forcing_voltage():
            return float(self.param("v_level")), self.limit_sign * self.i_limit()
        return self.limit_sign * self.v_limit(), float(self.param("i_level"))

    # ---- the mode --------------------------------------------------------------------------------

    def restart(self) -> None:
        """Start again from the setpoint's own loop (a new function, a restarted output, a
        re-wired sense, or a sweep point)."""
        self.mode = "V" if self.forcing_voltage() else "I"
        self.limit_sign = 1.0

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        if bound.get(path) in ("function", "output"):
            self.restart()

    def reset(self) -> None:
        self.mode, self.limit_sign = "V", 1.0

    def update_mode(self, metered_v: float, current: float) -> bool:
        """Choose the mode the DC operating point calls for; the output stage supplies the values:
        the metered terminal voltage and the current out of the force HI terminal."""
        if not self.on():
            return False
        old = (self.mode, self.limit_sign)
        if self.forcing_voltage():
            setpoint = float(self.param("v_level"))
            if self.mode == "V":
                if abs(current) > self.i_limit() * (1 + 1e-9):
                    self.mode, self.limit_sign = "I", _sign(current)
            elif self.limit_sign * (setpoint - metered_v) < 0:
                self.mode, self.limit_sign = "V", 1.0  # the setpoint is reachable again
        else:
            setpoint = float(self.param("i_level"))
            if self.mode == "I":
                if abs(metered_v) > self.v_limit() * (1 + 1e-9):
                    self.mode, self.limit_sign = "V", _sign(metered_v)
            else:
                if self.limit_sign * (setpoint - current) < 0:
                    self.mode, self.limit_sign = "I", 1.0
        return (self.mode, self.limit_sign) != old

    # ---- operations ------------------------------------------------------------------------------

    @op("regulation")
    def op_regulation(self, **_: Any) -> OpResult:
        self.session._solve()
        if not self.on():
            return OpResult(b"OFF")
        return OpResult(b"CV" if self.mode == "V" else b"CC")

    @op("tripped")
    def op_tripped(self, kind: str = "current", **_: Any) -> OpResult:
        self.session._solve()
        tripped = self.limited() and (kind == "current") == self.forcing_voltage()
        return OpResult(V.format_bool(tripped))

    @op("condition")
    def op_condition(self, **_: Any) -> OpResult:
        self.session._solve()
        return OpResult(V.format_int(CONDITION_LIMIT if self.limited() else 0))


@component("smu_output")
class SmuOutput(Component):
    """The output stage (#184 B1): HI/LO, remote sense, floating LO and the output relay.

    The voltage loop regulates the metered points: ``V(sense_hi) − V(sense_lo)`` with remote sense
    on, otherwise the force terminals (``V(hi) − V(lo)``; with ``floating: false`` the LO is bonded
    to the bench ground, the model's original behaviour). The current always flows through the
    force terminals. A sense terminal left unconnected while remote sense is on makes the reading
    operations raise an error (the loop falls back to the force terminals); the bench validator
    warns about such a bench (metrology-authoring.md 6).

    Not a ``driver`` for bench checks: at its limit it sinks current, so a supply on the same net
    is a valid bench (quadrant 2). The control loop it drives holds the mode (D34).
    """

    ports = {
        "hi": PortSpec("inout", "electrical"),
        "lo": PortSpec("inout", "electrical"),
        "sense_hi": PortSpec("in", "electrical"),
        "sense_lo": PortSpec("in", "electrical"),
    }
    sense_ports = ("sense_hi", "sense_lo")
    sense_param = "remote_sense"
    refs = {"loop": ("output_control_loop", "instrument")}
    defaults = {
        "output": False,  # False: the terminal floats
        "remote_sense": False,  # False: sense internally connected to the force terminals
        "floating": False,  # False: LO bonded to the bench ground
        "loop": "",  # the control loop holding the mode
    }
    piecewise = True

    def _loop(self) -> OutputControlLoop:
        assert self.owner is not None
        loop = self.peer(str(self.param("loop")))
        assert isinstance(loop, OutputControlLoop)
        return loop

    def enabled(self) -> bool:
        return bool(self.param("output"))

    # ---- circuit ---------------------------------------------------------------------------------

    def remote(self) -> bool:
        """Remote sense in force: the setting is on and both sense terminals are wired, apart."""
        if not (bool(self.param("remote_sense")) and self.wired("sense_hi") and self.wired("sense_lo")):
            return False
        # Both sense terminals on one net would make the constraint unsolvable: fall back.
        return self.session.node_of(self, "sense_hi") != self.session.node_of(self, "sense_lo")

    def sense_fault(self) -> bool:
        """Remote sense is switched on but the sense terminals cannot serve it."""
        return bool(self.param("remote_sense")) and not self.remote()

    def low_node(self) -> str | None:
        """The node the meter's low terminal references: the LO net when floating, else ground."""
        return "lo" if self.param("floating") else None

    def metered_voltage(self, dc_voltage: Callable[[str], float]) -> float:
        """The DC voltage the loop regulates and the meter reads, from solved port voltages."""
        if self.remote():
            return dc_voltage("sense_hi") - dc_voltage("sense_lo")
        low = self.low_node()
        return dc_voltage("hi") - (dc_voltage(low) if low else 0.0)

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        floating = bool(self.param("floating"))
        if not floating and self.wired("lo"):
            # LO bonded to the bench ground: anything wired to LO reaches ground through it.
            circuit.resistor(node("lo"), GROUND, BOND_R)
        if not self.enabled():
            return
        if floating and self.session.node_of(self, "hi") == self.session.node_of(self, "lo"):
            floating = False  # force terminals shorted: drive them against ground, not themselves
        if self._loop().mode == "V":
            if self.remote():
                circuit.loop_source(
                    node("sense_hi"),
                    node("sense_lo"),
                    node("hi"),
                    node("lo") if floating else GROUND,
                    self.uid + "/v",
                )
            else:
                low = node("lo") if floating else GROUND
                circuit.loop_source(node("hi"), low, node("hi"), low, self.uid + "/v")
        else:
            circuit.current(node("hi"), self.uid + "/i")
            if floating:
                circuit.current(node("lo"), self.uid + "/i", sign=-1.0)

    def emf(self) -> dict[str, Signal]:
        if not self.enabled():
            return {}
        v, i = self._loop().targets()
        if self._loop().mode == "V":
            return {self.uid + "/v": Signal.constant(v)}
        return {self.uid + "/i": Signal.constant(i)}

    def update_mode(self, dc_voltage: Callable[[str], float]) -> bool:
        if not self.enabled():
            return False
        loop = self._loop()
        metered = self.metered_voltage(dc_voltage)
        current = self.session.source_current(self.uid + "/v") if loop.mode == "V" else 0.0
        return loop.update_mode(metered, current)

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        if bound.get(path) in ("remote_sense", "floating"):
            # A re-wired sense starts from the setpoint's own loop (D34).
            self._loop().restart()


@component("smu_meter")
class SmuMeter(Component):
    """The V/I meter (#184 B1): range resistors, A/D, NPLC and the reading memory handoff.

    Readings go through a ``trigger_model`` (the ``sampler`` protocol, metrology-authoring.md 3.4):
    one reading takes its NPLC time plus the source delay; a sweep's points are the readings of one
    run, each point's level driving the terminal while the run lasts. The measure side ranges like
    the meter core (#87): ``measure_ranges`` (per function: the voltage and current readings), the
    manual ``measure_range_voltage`` / ``measure_range_current``, ``measure_autorange`` with the
    same hysteresis, and a reading beyond ``overrange`` × full scale reads ``overload_value``.

    The bench front panel reads this component's values (``core.voltage`` …), so the SMU
    definitions keep naming it ``core``.
    """

    refs = {
        "source": ("smu_source", "instrument"),
        "loop": ("output_control_loop", "instrument"),
        "output": ("smu_output", "instrument"),
    }
    units = {"measure_range_voltage": "V", "measure_range_current": "A"}
    choices = {"function": ("VOLTage", "CURRent", "RESistance")}
    defaults = {
        "function": None,  # active sense function: VOLTage | CURRent | RESistance (None: the forced quantity)
        "nplc": 1.0,  # reading time in power line cycles
        "autozero": True,  # autozero on: an extra phase and firmware per reading
        "line_freq": 60.0,  # Hz, mains frequency nplc refers to
        "delay": 0.0,  # s, wait before each reading
        "measure_ranges": None,  # full-scale value of each measure range, per function
        "measure_range_voltage": None,  # manual measure range of the voltage reading (None: auto)
        "measure_range_current": None,  # manual measure range of the current reading (None: auto)
        "measure_autorange": True,  # the measure range follows the reading
        "autorange_up": 1.2,  # fraction of full scale above which the range switches up
        "autorange_down": 0.1,  # fraction of full scale below which the range switches down
        "overrange": 1.2,  # a reading beyond this fraction of full scale is an overload
        "overload_value": 9.9e37,  # the reading an overload produces
        "buffer": "",  # the reading_buffer whose contents the front panel shows ('' for none)
        "source": "",  # the source DAC (sweep levels, the function being forced)
        "loop": "",  # the control loop (mode, limits)
        "output": "",  # the output stage (metered voltage, current, sense)
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._auto_range: dict[str, float] = {}  # each measure function's auto-chosen range
        self._saved: dict[str, dict[str, Any]] | None = None  # settings a run drove (restored on run_done)
        self._point: tuple[float, float, float, float] = (0.0, 0.0, OPEN, 0.0)  # the last reading

    def _source(self) -> SmuSource:
        assert self.owner is not None
        return self.peer(str(self.param("source")))  # type: ignore[return-value]

    def _loop(self) -> OutputControlLoop:
        assert self.owner is not None
        return self.peer(str(self.param("loop")))  # type: ignore[return-value]

    def _output(self) -> SmuOutput:
        assert self.owner is not None
        return self.peer(str(self.param("output")))  # type: ignore[return-value]

    # ---- measuring -------------------------------------------------------------------------------

    def reading(self) -> tuple[float, float]:
        """(metered voltage, current out of the force HI terminal) at the operating point.

        The voltage is the metered differential: sense terminals with remote sense on, otherwise
        the force terminals (against ground unless the LO floats). With remote sense on but a sense
        terminal unconnected, the reading operations report an error instead of a number.
        """
        output, loop = self._output(), self._loop()
        if not output.enabled():
            return 0.0, 0.0
        v_node = output.metered_voltage(lambda p: output.pin_signal(p).mean())
        if loop.mode == "V":
            return v_node, self.session.source_current(output.uid + "/v")
        return v_node, loop.targets()[1]

    def _check_sense(self) -> None:
        if self._output().sense_fault():
            raise ScpiError(-221, "remote sense is on but a sense terminal is not connected")

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
        return [v, i, res, t, float(STAT_LIMIT if self._loop().limited() else 0)]

    def _format(self, rows: list[list[float]]) -> bytes:
        return b",".join(self.reply_number(x, 7) for row in rows for x in row)

    # ---- a Sampler for trigger_model (metrology-authoring.md 3.4) ---------------------------------

    def reading_time(self, sweep_delay: float | None = None) -> float:
        """Modelled time of one reading: 50 µs + delay + phases × (NPLC / line + 185 µs) + firmware
        (the rule the meter core shares, ranging.py)."""
        if sweep_delay is None:
            source = self._source()
            sweep_delay = float(source.param("sweep_delay")) if source.sweep_levels() else 0.0
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
        levels = self._source().sweep_levels()
        return len(levels) or None

    def run_begin(self) -> None:
        """A run starts: remember the settings the sweep drives, so that :meth:`run_done` can give
        the operator's own values back."""
        self._check_sense()  # the run's readings would report the fallback loop, refuse instead
        loop, output = self._loop(), self._output()
        self._saved = {
            "loop": {k: loop.overrides.get(k) for k in ("function", "v_level", "i_level", "output")},
            "output": {k: output.overrides.get(k) for k in ("output",)},
        }

    def run_done(self) -> None:
        """The run is over: the operator's settings are back."""
        saved, self._saved = self._saved, None
        if saved is None:
            return
        loop, output = self._loop(), self._output()
        for key, value in saved["loop"].items():
            if value is None:
                loop.overrides.pop(key, None)
            else:
                loop.overrides[key] = value
        for key, value in saved["output"].items():
            if value is None:
                output.overrides.pop(key, None)
            else:
                output.overrides[key] = value
        loop.restart()
        self.session.invalidate(electrical=True)

    def sample(self, index: int) -> float:
        """One reading of a run: a sweep's point ``index`` drives the terminal while it is taken
        (validation-smu.md: every point is solved against the bench as it is)."""
        source, loop, output = self._source(), self._loop(), self._output()
        levels = source.sweep_levels()
        if levels:
            function = str(source.param("sweep_function"))
            key = "v_level" if function == "VOLTage" else "i_level"
            loop.overrides["function"] = function
            loop.overrides["output"] = True  # a sweep drives the output for its duration
            loop.overrides[key] = levels[min(index, len(levels) - 1)]
            output.overrides["output"] = True
            loop.restart()
            self.session.invalidate(electrical=True)
        v_raw, i_raw = self.reading()
        v, i = self._clipped(v_raw, i_raw)
        res = v_raw / i_raw if abs(i_raw) > 0 else OPEN
        self._point = (v, i, res, float(STAT_LIMIT if loop.limited() else 0))
        function = self.choice("function")  # VOLTage | CURRent | RESistance; a bad word is -224
        if function == "VOLTage":
            return v
        if function == "CURRent":
            return i
        if function == "RESistance":
            return res
        return v if loop.forcing_voltage() else i

    def sample_fields(self, index: int) -> dict[str, float]:
        """The reading's own elements: TIME is the reading's place in the run (the reading times,
        not counting the source delay between them)."""
        v, i, res, stat = self._point
        return {
            "VOLT": v,
            "CURR": i,
            "RES": res,
            "TIME": (index + 1) * self.reading_time(),
            "STAT": stat,
            "SOUR": self._source_level(),
        }

    def _source_level(self) -> float:
        """The SOUR element (#252): the source output's setting, the level being forced (a sweep's
        point while it runs)."""
        loop = self._loop()
        return float(loop.param("v_level" if loop.forcing_voltage() else "i_level"))

    # ---- state, reset and operations -------------------------------------------------------------

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        if bound.get(path) == "line_freq" and float(new) not in (50.0, 60.0):
            raise ScpiError(-222, "line frequency is 50 or 60 Hz")

    def reset(self) -> None:
        self._auto_range = {}
        self.run_done()

    @op("measure")
    def op_measure(self, **_: Any) -> OpResult:
        """One reading with the source as configured, whatever the sweep mode; it replaces the
        reading memory (one reading is what the panel and ``FETCh?`` show until the next run)."""
        self._check_sense()
        t = self.reading_time(0.0)
        row = self._row(t)
        buffer = self._reading_buffer()
        if buffer is not None and self.owner is not None:
            buffer.clear_buffer()
            buffer.append(
                Reading(
                    row[0] if self._loop().forcing_voltage() else row[1],
                    self.owner.now_ns() + round((t + float(self.param("delay"))) * 1e9),
                    {
                        "VOLT": row[0],
                        "CURR": row[1],
                        "RES": row[2],
                        "TIME": t,
                        "STAT": row[4],
                        "SOUR": self._source_level(),
                    },
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
        return OpResult(self.reply_number(r, 7))

    def readings(self) -> dict[str, Any]:
        self.session._solve()
        v, i = self._measured()
        loop = self._loop()
        points = self._buffer_points()
        return {
            "values": {
                "voltage": v,
                "current": i,
                "regulation": "OFF" if not loop.on() else ("CV" if loop.mode == "V" else "CC"),
                "limited": loop.limited(),
                "forcing": "VOLTage" if loop.forcing_voltage() else "CURRent",
                "limit": loop.i_limit() if loop.forcing_voltage() else loop.v_limit(),
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
        buffer = self.peer(name)
        return buffer if isinstance(buffer, ReadingBuffer) else None

    def _buffer_points(self) -> list[Any]:
        """The readings the front panel shows."""
        buffer = self._reading_buffer()
        return list(buffer.buffers[buffer._default()]) if buffer is not None else []


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


__all__ = [
    "OPEN",
    "DcSupply",
    "OutputControlLoop",
    "SmuMeter",
    "SmuOutput",
    "SmuSource",
]
