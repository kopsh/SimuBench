"""Electronic loads: the input stage, the sink, its mode loop, the meter and the protection (C1).

Since #186 the load is built from its real functional blocks (D50, component-map.md
electronic_load): ``eload_input`` (the input terminal and its switch, the piece that stamps the
circuit), ``eload_power_stage`` (the operating envelope: the constant-current knee, maximum
current and power), ``eload_mode_loop`` (CC / CV / CR / CP, Von, the CC slew rate), ``eload_meter``
(the input voltage, the sink current and the power) and ``eload_protection`` (OVP / OCP / OPP).

The mode loop is the piecewise device's brain: :meth:`EloadModeLoop.update_mode` picks the
stamped element (open, a resistor, a current sink or a voltage clamp) from the solved input
voltage, and the D34 re-solve repeats until the element and the operating point agree. A
constant-power load (I = P/V) is non-linear; it is solved by that same iteration (see D52), not
by extending the circuit solver.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...engine.circuit import GROUND, Circuit
from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal
from ...scpi import values as V

#: How close two successive constant-power currents must be for the mode loop to call it settled.
CP_TOLERANCE = 1e-9


@component("eload_power_stage")
class EloadPowerStage(Component):
    """The sink's operating envelope (#186 C1): the CC knee, the current and power capability.

    In constant current mode the load cannot go below its minimum operating resistance ``r_min``:
    below the knee voltage (``r_min`` × the set current) it behaves as that resistance instead of
    the current sink. ``i_max`` and ``p_max`` cap every mode (the constant-power envelope), so a
    CC setpoint above the capability is power-limited rather than drawn.
    """

    units = {"r_min": "OHM", "i_max": "A", "p_max": "W"}
    defaults = {
        "r_min": 0.0367,  # minimum operating resistance in CC mode (the knee), ohm
        "i_max": 30.0,  # maximum sink current, A
        "p_max": 300.0,  # maximum sink power, W (0: no power limit)
    }

    def knee(self) -> float:
        return max(float(self.param("r_min")), 1e-9)

    def clip(self, v: float, i: float) -> float:
        """The sink current within the envelope at the input voltage ``v``."""
        i = min(max(i, 0.0), float(self.param("i_max")))
        p = float(self.param("p_max"))
        if p > 0 and v > 0:
            i = min(i, p / v)
        return max(i, 0.0)


@component("eload_mode_loop")
class EloadModeLoop(Component):
    """The static function and the operating-point mode (#186 C1): CC, CV, CR or CP.

    ``function`` names the static mode; the setpoint belongs to it (``current``, ``voltage``,
    ``resistance`` or ``power``). ``von`` is the starting voltage: below it a CC (or CP) load does
    not sink. ``slew`` (A/µs) limits how fast the CC current follows a setpoint change; the ramp is
    evaluated at the current virtual instant, so a later reading sees the intermediate current.

    The mode loop is piecewise in the D34 sense: :meth:`update_mode` reads the solved input voltage
    and the present sink current and chooses ``("open"|"i"|"r"|"v", value)`` — a current sink, a
    resistance or a voltage clamp (CV mode). The CP hysteresis (I = P/V) is iterated to a
    self-consistent point; the decision D52 records the semantics and its limit.
    """

    refs = {"stage": ("eload_power_stage", "instrument")}
    units = {"current": "A", "voltage": "V", "resistance": "OHM", "power": "W", "von": "V"}
    defaults = {
        "function": "CC",  # CC | CV | CR | CP: the static operation mode
        "current": 0.0,  # A, the regulated current in CC
        "voltage": 0.0,  # V, the regulated input voltage in CV
        "resistance": 1000.0,  # ohm, the resistance in CR
        "power": 0.0,  # W, the power in CP
        "von": 0.0,  # V, starting voltage: a CC/CP load does not sink below it
        "slew": 0.0,  # A/µs, CC setpoint slew rate (0: instantly)
        "stage": "",  # the eload_power_stage carrying the envelope
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.mode = "open"  # open | i (current sink) | r (resistance) | v (voltage clamp)
        self.value = 0.0  # the current (A), resistance (ohm) or voltage (V) of the mode
        self._ramp_from = 0.0  # the CC current when the setpoint last changed
        self._ramp_target = float(self.param("current"))  # the current now asked for
        self._ramp_t0: int | None = None  # when the ramp started (None: no slew yet)
        self._cv_hold = True  # CV clamp allowed to conduct (False once it would source)

    def _stage(self) -> EloadPowerStage:
        return self.ref("stage")  # type: ignore[return-value]

    # ---- the CC slew -----------------------------------------------------------------------------

    def _level(self) -> float:
        """The CC current at the current instant, following ``slew`` from the last change."""
        if self._ramp_t0 is None:
            return self._ramp_target
        slew = float(self.param("slew"))
        if slew <= 0:
            return float(self.param("current"))
        assert self.owner is not None
        seconds = max(0, self.owner.now_ns() - self._ramp_t0) / 1e9
        step = slew * 1e6 * seconds  # A/µs -> A/s
        if self._ramp_target >= self._ramp_from:
            return min(self._ramp_target, self._ramp_from + step)
        return max(self._ramp_target, self._ramp_from - step)

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        if key == "current":
            # Anchor the ramp at the level the old setpoint had reached.
            self._ramp_from = self._level()
            self._ramp_target = float(new)
            assert self.owner is not None
            self._ramp_t0 = self.owner.now_ns()
            self.session.invalidate(electrical=True)

    # ---- the mode decision -----------------------------------------------------------------------

    def mode_value(self) -> tuple[str, float]:
        return self.mode, self.value

    def open(self) -> bool:
        """The load conducts nothing; True when that was not already the mode."""
        changed = self.mode != "open"
        self.mode, self.value = "open", 0.0
        return changed

    def _decide(self, v: float, current: float) -> tuple[str, float]:
        function = str(self.param("function"))
        stage = self._stage()
        if function == "CV":
            vset = float(self.param("voltage"))
            if self.mode == "v" and current < -1e-12:
                self._cv_hold = False  # it would have to source current: a load cannot
            elif vset > 0 and v >= vset:
                self._cv_hold = True
            if vset > 0 and self._cv_hold:
                return "v", vset
            return "open", 0.0
        if function == "CR":
            r = float(self.param("resistance"))
            if r <= 0:
                return "open", 0.0
            i = v / r
            limited = stage.clip(v, i)
            return ("i", limited) if limited < i else ("r", r)
        i = 0.0
        if function == "CP":
            p = float(self.param("power"))
            i = p / v if v > 0 else 0.0
        elif function == "CC":
            i = self._level()
        i = stage.clip(v, i)
        if i <= 0 or (function in ("CC", "CP") and v <= float(self.param("von"))):
            return "open", 0.0
        if v / stage.knee() <= i:
            return "r", stage.knee()  # below the knee: the minimum resistance holds the sink
        return "i", i

    def update_mode(self, v: float, current: float) -> bool:
        """Pick the stamped element for the solved input voltage; True when it changed."""
        mode, value = self._decide(v, current)
        changed = mode != self.mode or abs(value - self.value) > CP_TOLERANCE * max(1.0, abs(value))
        self.mode, self.value = mode, value
        return changed


@component("eload_input")
class EloadInput(Component):
    """The load's input terminal and switch (#186 C1): the element that stamps the circuit.

    It owns the ``in`` port; with the input off (or a protection trip latched) it is an open
    circuit. Otherwise it stamps what the mode loop decided: a current sink (CC/CP), a resistance
    (CR or the CC knee) or a voltage clamp (CV). ``c_in`` adds the input capacitance to ground.
    """

    refs = {
        "loop": ("eload_mode_loop", "instrument"),
        "protection": ("eload_protection", "instrument"),
    }
    ports = {"in": PortSpec("in", "electrical", "the load's input terminal")}
    units = {"c_in": "F"}
    defaults = {
        "enabled": False,  # the input switch (a setting)
        "c_in": 0.0,  # input capacitance to ground, F
        "loop": "",  # the eload_mode_loop that owns the mode
        "protection": "",  # the eload_protection whose trip opens the input
    }
    piecewise = True

    def _loop(self) -> EloadModeLoop:
        return self.ref("loop")  # type: ignore[return-value]

    def _protection(self) -> EloadProtection:
        return self.ref("protection")  # type: ignore[return-value]

    def enabled(self) -> bool:
        return bool(self.param("enabled"))

    def conducting(self) -> bool:
        return self.enabled() and not self._protection().tripped()

    # ---- the circuit -----------------------------------------------------------------------------

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        if not self.conducting():
            return
        c = float(self.param("c_in") or 0.0)
        if c > 0:
            circuit.capacitor(node("in"), GROUND, c)
        mode, value = self._loop().mode_value()
        if mode == "r":
            circuit.resistor(node("in"), GROUND, value)
        elif mode == "i":
            circuit.current(node("in"), self.uid)
        elif mode == "v":
            # A shunt regulator: the loop holds the input at the CV setpoint. Its loop current
            # reads negative while the load absorbs (the SMU's loop_source convention).
            circuit.loop_source(node("in"), GROUND, node("in"), GROUND, self.uid)

    def emf(self) -> dict[str, Signal]:
        if not self.conducting():
            return {}
        mode, value = self._loop().mode_value()
        if mode == "i":
            return {self.uid: Signal.constant(-value)}  # sinks current into the load
        if mode == "v":
            return {self.uid: Signal.constant(value)}
        return {}

    def update_mode(self, dc_voltage: Callable[[str], float]) -> bool:
        loop = self._loop()
        if not self.conducting():
            return loop.open()
        v = dc_voltage("in")
        mode, _ = loop.mode_value()
        # Current into the load: a current sink's source drives a negative EMF, a CV clamp
        # absorbs the source's current (positive "out of its node").
        raw = self.session.source_current(self.uid) if mode in ("i", "v") else 0.0
        current = -raw  # a negative EMF current sink and an absorbing clamp both read negative
        changed = loop.update_mode(v, current)
        mode, value = loop.mode_value()
        sink = self._sink_current(v, current, mode, value)
        if not self._protection().tripped() and self._protection().check(v, sink):
            self._protection().trip(v, sink)
            loop.open()
            changed = True
        return changed

    @staticmethod
    def _sink_current(v: float, current: float, mode: str, value: float) -> float:
        if mode == "i":
            return value
        if mode == "r" and value > 0:
            return v / value
        if mode == "v":
            return max(current, 0.0)
        return 0.0

    # ---- readings --------------------------------------------------------------------------------

    def voltage(self) -> float:
        return self.pin_signal("in").mean()

    def measured(self) -> tuple[float, float]:
        """(input voltage, sink current) as the load reports them."""
        v = self.voltage()
        if not self.conducting():
            return v, 0.0
        mode, value = self._loop().mode_value()
        if mode == "i":
            return v, value
        if mode == "r":
            return v, (v / value if value > 0 else 0.0)
        if mode == "v":
            return v, max(0.0, -self.session.source_current(self.uid))
        return v, 0.0

    def unregulated(self) -> bool:
        """The input is on but does not hold its set quantity (#343): the mode loop did not settle
        (D52), or the solved point misses the setpoint — the knee, the power/current envelope, Von
        or a CC ramp still on its way. The status register's UNR bit reads this."""
        if not self.conducting():
            return False
        if self.session.unsettled:
            return True
        loop = self._loop()
        mode, value = loop.mode_value()
        function = str(loop.param("function"))
        if function == "CR":
            return mode != "r" or abs(value - float(loop.param("resistance"))) > CP_TOLERANCE * value
        if function == "CV":
            return mode != "v"
        v, i = self.measured()
        target, got = (
            (float(loop.param("power")), v * i) if function == "CP" else (float(loop.param("current")), i)
        )
        return abs(got - target) > 1e-6 * max(1.0, abs(target))

    def regulating(self) -> bool:
        """The load holds its regulated quantity (not the knee/open, and the mode loop settled,
        D52/#258): for the panel's warning."""
        return self.conducting() and self._loop().mode_value()[0] == "i" and not self.session.unsettled


@component("eload_meter")
class EloadMeter(Component):
    """The load's V/I/P measurement (#186 C1). The readings keep the ``load.*`` keys the front
    panel uses; the input stage supplies the solved values."""

    refs = {"input": ("eload_input", "instrument")}
    defaults = {
        "input": "",  # the eload_input whose solved values are read
        # STATus:QUEStionable bit positions (#343).
        "ov_bit": 0,  # questionable bit set while OVP is tripped
        "oc_bit": 1,  # questionable bit set while OCP is tripped
        "op_bit": 3,  # questionable bit set while OPP is tripped
        "unr_bit": 10,  # questionable bit set while the input is on but unregulated
    }

    def _input(self) -> EloadInput:
        return self.ref("input")  # type: ignore[return-value]

    @op("measure")
    def op_measure(self, what: str = "voltage", **_: Any) -> OpResult:
        v, i = self._input().measured()
        value = {"voltage": v, "current": i, "power": v * i}[what]
        return OpResult(self.reply_number(value, 7))

    @op("questionable")
    def op_questionable(self, query: bool = False, **_: Any) -> OpResult:
        """STATus:QUEStionable:CONDition? (#343): the condition register, live — OV / OC / OP while
        that protection is tripped, UNR while the input is unregulated (:meth:`EloadInput.unregulated`).
        The bit positions are parameters, so a model maps its own manual's register."""
        self.session.solve()
        inp = self._input()
        bits = 0
        kind = inp._protection().kind()
        for name, param in (("OVP", "ov_bit"), ("OCP", "oc_bit"), ("OPP", "op_bit")):
            if kind == name:
                bits |= 1 << int(self.param(param))
        if inp.unregulated():
            bits |= 1 << int(self.param("unr_bit"))
        return OpResult(str(bits).encode())

    def readings(self) -> dict[str, Any]:
        v, i = self._input().measured()
        return {
            "values": {
                "voltage": v,
                "current": i,
                "power": v * i,
                "regulating": self._input().regulating(),
            }
        }


@component("eload_protection")
class EloadProtection(Component):
    """OVP / OCP / OPP protection (#186 C1): thresholds, the trip latch and its kind.

    A threshold of 0 disables that protection. When the operating point crosses one the trip is
    latched and the input stops conducting (the mode loop opens); :meth:`op_clear` resets it (the
    model's protection-clear command; #194 maps the DL3000's own). The thresholds are checked on
    the solved operating point, so a trip is a DC behaviour, not a transient one.
    """

    defaults = {
        "ovp": 0.0,  # over-voltage trip, V (0: off)
        "ocp": 0.0,  # over-current trip, A (0: off)
        "opp": 0.0,  # over-power trip, W (0: off)
    }
    units = {"ovp": "V", "ocp": "A", "opp": "W"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tripped = ""  # the threshold crossed ("OVP" | "OCP" | "OPP"); '' when clear

    def tripped(self) -> bool:
        return bool(self._tripped)

    def kind(self) -> str:
        return self._tripped

    def check(self, v: float, i: float) -> bool:
        """Whether the operating point crosses a threshold (the caller then trips)."""
        if self._tripped:
            return False
        ovp, ocp, opp = (float(self.param(k)) for k in ("ovp", "ocp", "opp"))
        return (ovp > 0 and abs(v) > ovp) or (ocp > 0 and abs(i) > ocp) or (opp > 0 and abs(v * i) > opp)

    def trip(self, v: float, i: float) -> None:
        ovp, ocp, opp = (float(self.param(k)) for k in ("ovp", "ocp", "opp"))
        if ovp > 0 and abs(v) > ovp:
            self._tripped = "OVP"
        elif ocp > 0 and abs(i) > ocp:
            self._tripped = "OCP"
        elif opp > 0 and abs(v * i) > opp:
            self._tripped = "OPP"
        else:
            self._tripped = "PROT"

    def clear(self) -> None:
        self._tripped = ""

    @op("tripped")
    def op_tripped(self, query: bool = False, **_: Any) -> OpResult:
        self.session.solve()  # the trip happens in the mode loop's update
        return OpResult(V.format_bool(self.tripped()))

    @op("kind")
    def op_kind(self, query: bool = False, **_: Any) -> OpResult:
        self.session.solve()
        return OpResult(V.format_string(self._tripped))

    @op("clear")
    def op_clear(self, **_: Any) -> OpResult:
        self.clear()
        return OpResult()


__all__ = [
    "EloadInput",
    "EloadMeter",
    "EloadModeLoop",
    "EloadPowerStage",
    "EloadProtection",
]
