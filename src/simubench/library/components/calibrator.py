"""The output stage of a calibrator: operate and standby, settling, overload, high-voltage
confirmation (metrology-authoring.md 5.1, issue 90).

In standby the terminal is out of the bench circuit (disconnected); in operate the component is a
voltage source with ``z_out`` behind it — an ideal one with a tracked current when ``z_out`` is 0,
a Thevenin source otherwise. DCV drives the (settled) level as DC; ACV drives one spectral line
whose RMS value is the level. A load that draws more than ``i_max`` trips the output back to
standby and queues the definition's error, judged on the DC operating point like every
piecewise-linear device (D34).

A change of the output settings (or entering operate) does not reach the new value at once: the
amplitude follows a first-order transition that is done after ``settle`` seconds (time constant
``settle`` / 5), and the moment it is done is registered as a pending operation, so ``*OPC?`` and
``*WAI`` wait for it. A model without settling gives ``settle: 0``.

Whether a change of the output settings returns the instrument to standby is the model's
``standby_rule``: ``either`` (the default) trips when the old or the new magnitude is above
``standby_above``; ``crossing`` (the 5730A and 5522A manuals' wording, #553) trips only when the
new magnitude is at or above it while the old one was below. With ``standby_on_function`` a change
of the output function trips too: ``true`` any change, ``above`` (the 5522A, #566) a change between
DC and AC voltage only while the magnitude is at or above ``standby_above`` (any other change of
function still trips). Outputting the value again needs an explicit ``OPER``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ...engine.circuit import GROUND, Circuit
from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal
from ...scpi.errors import ScpiError


def _flag(value: Any) -> bool:
    return bool(value) if not isinstance(value, str) else value.upper() in ("1", "ON", "TRUE")


class _Setpoint:
    """The setpoint and range generation (DAC, range amplifier): function, level, frequency, the
    settling transition and the stage's hidden errors. Mixed into ``calibrator_setpoint`` and the
    combined ``calibrator_output``; ``_control()`` is the output control that operates it."""

    owner: Any
    session: Any
    uid: str

    def _control(self) -> Any:  # the output control block (self in the combined form)
        raise NotImplementedError

    def _settle_s(self) -> float:
        """The settling time of the target function (a ``settle`` table is taken by function)."""
        s = self.param("settle")  # type: ignore[attr-defined]
        if isinstance(s, dict):
            s = s.get(str(self.param("function")).upper(), 0.0)  # type: ignore[attr-defined]
        try:
            v = float(s)
        except (TypeError, ValueError):
            v = 0.0
        # a range or polarity change adds its own settling (#552), once
        extra, self._extra_settle = getattr(self, "_extra_settle", 0.0), 0.0
        return max(v, 0.0) + extra

    def _level_now(self) -> float:
        """The raw amplitude at this instant: the settling transition followed (5.1)."""
        return self._level_at(self.session.now_ns)

    def _level_at(self, ns: int) -> float:
        """The raw amplitude at ``ns`` (the value a new transition starts from)."""
        if self._trans is None:
            return self._settled
        start, dur, v0, v1 = self._trans
        elapsed = ns - start
        if elapsed >= dur:
            return v1
        return v1 + (v0 - v1) * math.exp(-elapsed / (dur / 5.0))

    def kind(self) -> str:
        """What the output is: V (DCV, ACV: a voltage source), I (DCI, ACI: a current source,
        #550) or R (OHM: a resistance across the terminals)."""
        fn = str(self.param("function")).upper()  # type: ignore[attr-defined]
        return "I" if fn in ("DCI", "ACI") else "R" if fn == "OHM" else "V"

    def resistance(self) -> float:
        """OHM: the resistance the terminals present, the nominal value through the hidden gain error."""
        return float(self.param("level")) * (1 + float(self.param("gain_error_ppm")) * 1e-6)  # type: ignore[attr-defined]

    def _true_level(self) -> float:
        """The amplitude the output stage actually makes: the setting through the transition and
        its own hidden errors (the truth a calibrated meter is checked against)."""
        true = self._level_now() * (1 + float(self.param("gain_error_ppm")) * 1e-6)  # type: ignore[attr-defined]
        if str(self.param("function")).upper() == "DCV":  # type: ignore[attr-defined]
            true += float(self.param("offset"))  # type: ignore[attr-defined]
        return true

    def setpoint_signal(self) -> Signal:
        """What the source drives: the level as DC (DCV, DCI) or one line of that RMS value (ACV,
        ACI); nothing for OHM (a passive resistance)."""
        if self.kind() == "R":
            return Signal.constant(0.0)
        level = self._true_level()
        if str(self.param("function")).upper() in ("ACV", "ACI"):  # type: ignore[attr-defined]
            f = float(self.param("frequency"))  # type: ignore[attr-defined]
            if f <= 0:
                return Signal.constant(0.0)
            return Signal.lines([f], [complex(level * math.sqrt(2.0))])  # peak phasor of the RMS level
        return Signal.constant(level)

    def start_settling(self, target: float | None = None) -> None:
        """The amplitude moves to ``target`` (the level) through a first-order transition, done
        after ``settle``; that moment is a pending operation (*OPC? waits for it, 3.3)."""
        if target is None:
            target = float(self.param("level"))  # type: ignore[attr-defined]
        settle_ns = round(self._settle_s() * 1e9)
        now = self.session.now_ns
        v0 = self._level_at(now)  # before _settled moves: where the output is right now
        self._settled = target
        if settle_ns <= 0:
            self._trans = None
            return
        self._trans = (now, settle_ns, v0, target)
        if self.owner is not None:
            self.owner.register_pending(self.uid, now + settle_ns)

    def drop(self) -> None:
        """The output was disconnected: the amplitude starts again from 0, nothing pending."""
        self._trans = None
        self._settled = 0.0
        if self.owner is not None:
            self.owner.cancel_pending(self.uid)

    def settling(self) -> bool:
        return self._trans is not None

    def setpoint_changed(self, key: str, old: Any, new: Any) -> None:
        """A setting of the setpoint moved: the output control decides (standby above its limit,
        else settle to the new value)."""
        self._control().output_changed(key, old, new, self)


class _Control:
    """The output control: operate and standby, the terminal (``z_out``), the overload trip and the
    high-voltage standby rule. Mixed into ``calibrator_control`` and the combined form;
    ``_setpoint()`` is the setpoint block it drives."""

    owner: Any
    session: Any
    uid: str

    def _setpoint(self) -> Any:
        raise NotImplementedError

    def _drive(self) -> Signal:
        raise NotImplementedError

    def on(self) -> bool:
        """The terminal is in the circuit: the instrument is in operate."""
        return _flag(self.param("operate"))  # type: ignore[attr-defined]

    def _kind(self) -> str:
        kind = getattr(self._setpoint(), "kind", None)
        return kind() if kind is not None else "V"

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        if not self.on():
            return  # standby: the terminal is out of the circuit (5.1)
        kind = self._kind()
        if kind == "I":
            circuit.current(node("out"), self.uid)  # the current output, returning through LO (#550)
            return
        if kind == "R":
            circuit.resistor(node("out"), GROUND, max(self._setpoint().resistance(), 1e-6))
            return
        z = float(self.param("z_out") or 0.0)  # type: ignore[attr-defined]
        if z <= 0:
            # An ideal source with a tracked branch current: the overload check reads it.
            circuit.loop_source(node("out"), GROUND, node("out"), GROUND, self.uid)
        else:
            circuit.source(node("out"), self.uid, z)

    def emf(self) -> dict[str, Signal]:
        if not self.on() or self._kind() == "R":
            return {}
        return {self.uid: self._drive()}

    def _compliance(self) -> float:
        """The voltage a current output can drive: one number, or a table by setpoint range with
        "*" for the rest (#550)."""
        spec = self.param("compliance")  # type: ignore[attr-defined]
        if isinstance(spec, dict):
            sp = self._setpoint()
            name = sp.range_name() if hasattr(sp, "range_name") else None
            value = next(
                (v for k, v in spec.items() if name is not None and str(k).upper() == name.upper()), None
            )
            return float(value if value is not None else spec.get("*", 0.0))
        return float(spec or 0.0)

    def _load_current(self, dc_voltage: Callable[[str], float]) -> float:
        """The current the operating point draws from the terminal (D34)."""
        z = float(self.param("z_out") or 0.0)  # type: ignore[attr-defined]
        if z <= 0:
            return self.session.source_current(self.uid)
        return (self._drive().dc - dc_voltage("out")) / z

    def update_mode(self, dc_voltage: Callable[[str], float]) -> bool:
        if not self.on():
            return False
        kind = self._kind()
        if kind == "R":
            return False
        if kind == "I":
            # past its compliance voltage a current output trips to standby (#550)
            limit = self._compliance()
            v = dc_voltage("out")
            if limit > 0 and abs(v) > limit * (1 + 1e-9):
                self._set_operate(False)
                self.tripped = True
                code = int(self.param("overload_error") or 0)  # type: ignore[attr-defined]
                if code and self.owner is not None:
                    self.owner.push_error(code, f"compliance {abs(v):.4g} V exceeds {limit:.4g} V")
                return True
            return False
        i_max = float(self.param("i_max") or 0.0)  # type: ignore[attr-defined]
        if i_max <= 0:
            return False
        current = self._load_current(dc_voltage)
        if abs(current) <= i_max * (1 + 1e-9):
            return False
        self._trip(i_max, current)
        return True  # the session re-solves: the output is out of the circuit now

    def _trip(self, i_max: float, current: float) -> None:
        """The load drew more than ``i_max``: the output returns to standby (the operate setting
        follows, so the next OPER re-enables it) and the definition's error is queued (5.1, D34)."""
        self._set_operate(False)
        self.tripped = True
        if self.owner is not None:
            code = int(self.param("overload_error") or 0)  # type: ignore[attr-defined]
            if code:
                self.owner.push_error(
                    code, f"load current {abs(current):.4g} A exceeds the {i_max:.4g} A capability"
                )

    def _operate_changed(self, old: Any, new: Any) -> None:
        """Leaving or entering operate: the output starts from 0 (it was disconnected) and settles
        to the level; anything pending is dropped. OPER from a tripped standby tries again."""
        if _flag(new):
            self.tripped = False
        sp = self._setpoint()
        sp.drop()
        if _flag(new):
            sp.start_settling()

    def output_changed(self, key: str, old: Any, new: Any, sp: Any) -> None:
        """A change of the output settings while operating returns the instrument to standby when
        the model's rule says so, otherwise the new value is settled to. ``standby_rule`` either
        (5522A: old or new above ``standby_above``) or crossing (5730A: the new output at or above
        it while the old one was below, #553); ``standby_on_function``: any change of function
        (``true``), or DCV <-> ACV only at or above ``standby_above`` (``above``, #566)."""
        was_on = self.on()
        sa = self.param("standby_above")  # type: ignore[attr-defined]
        level = float(sp.param("level"))
        if was_on and key == "function" and old != new and self._function_trips(old, new, level, sa):
            self._set_operate(False)
            return
        if was_on and sa is not None:
            lo = abs(float(old) if key == "level" else level)
            ln = abs(float(new) if key == "level" else level)
            if str(self.param("standby_rule") or "either").lower() == "crossing":  # type: ignore[attr-defined]
                to_standby = ln >= float(sa) > lo
            else:
                to_standby = lo > float(sa) or ln > float(sa)
            if to_standby:
                self._set_operate(False)
                return
        if self.on():
            sp.start_settling()

    def _function_trips(self, old: Any, new: Any, level: float, standby_above: Any) -> bool:
        """Whether this change of function while operating returns to standby (``standby_on_function``)."""
        mode = self.param("standby_on_function")  # type: ignore[attr-defined]
        if isinstance(mode, str) and mode.lower() == "above":
            volts = {"DCV", "ACV"}
            if str(old).upper() in volts and str(new).upper() in volts:
                return standby_above is not None and abs(level) >= float(standby_above)
            return True
        return _flag(mode)

    def _set_operate(self, value: bool) -> None:
        """Operate through the bound state (the normal path) or the component's own attribute."""
        path = self.bound_paths().get("operate") if self.owner is not None else None  # type: ignore[attr-defined]
        if path is None:
            old = _flag(self.param("operate"))  # type: ignore[attr-defined]
            if old == value:
                return
            self.overrides["operate"] = value  # type: ignore[attr-defined]
            self._operate_changed(old, value)
            self.session.invalidate(electrical=True)
            return
        assert self.owner is not None
        self.owner.set_state(path, value, internal=True)

    def control_readings(self) -> dict[str, Any]:
        self.session._solve()
        sp = self._setpoint()
        values: dict[str, Any] = {
            "output": "OPER" if self.on() else "STBY",
            "function": str(sp.param("function")),
            "level": float(sp.param("level")),
            "frequency": float(sp.param("frequency")),
            "tripped": self.tripped,
            "settling": sp.settling(),
        }
        if self.wired("out"):  # type: ignore[attr-defined]
            values["voltage"] = self.pin_signal("out").mean()  # type: ignore[attr-defined]
        return {"values": values}


@component("calibrator_setpoint")
class CalibratorSetpoint(_Setpoint, Component):
    """The calibrator's setpoint and range generation (#329): ``function`` (DCV | ACV), ``level``
    (V; RMS for ACV), ``frequency``, ``settle`` (s, or a table per function) and the hidden
    ``gain_error_ppm`` / ``offset``. Its ``out`` is the signal the output control drives onto the
    terminal; it settles in virtual time when the control operates it."""

    ports = {"out": PortSpec("out", "signal", "the generated setpoint, to the output control")}
    refs = {"control": ("calibrator_control", "instrument")}
    units = {"level": "V", "offset": "V"}
    defaults = {
        "function": "DCV",  # DCV | ACV | DCI | ACI (current outputs, #550) | OHM (a resistance, #550)
        "level": 0.0,  # the set amplitude: V (RMS for ACV), A (RMS for ACI) or ohm
        "frequency": 0.0,  # Hz, the AC line (0 with DCV)
        "settle": 1.0,  # s until a new value is reached, or a table per function
        "gain_error_ppm": 0.0,  # the output's own gain error, ppm (hidden)
        "offset": 0.0,  # the output's own DC offset error, V (hidden, DCV only)
        "control": "",  # the calibrator_control that operates this setpoint
        "limit_error": -222,  # the error a level beyond the function's largest range reports (with ranges)
        "frequency_selects_ac": False,  # a frequency alone switches DC <-> AC within V or I (the 5730A's OUT 100 HZ)
        # Ranges (#552): per function a list of [name, full scale] or [name, full scale, minimum],
        # e.g. {DCV: [[DC220MV, 0.22], [DC2_2V, 2.2], [DC1100V, 1100, 100]]}; the output takes the
        # smallest range whose full scale is above the level ({}: no ranges)
        "ranges": {},
        "range_lock": False,  # the present range is locked (a setting): levels outside it are refused
        "over_range_error": -222,  # the error a level above the locked range's full scale reports
        "under_range_error": -222,  # the error a level below the locked range's minimum reports
        "range_settle": 0.0,  # s of settling added when the range or the polarity changes
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._trans: tuple[int, int, float, float] | None = None  # (start ns, ns, from, to)
        self._settled = 0.0  # the amplitude the output rests at (0.0 while disconnected)
        self._locked: str | None = None  # the locked range's name
        self._extra_settle = 0.0

    # ---- ranges (#552) ----------------------------------------------------------------------------

    def _table(self, function: str | None = None) -> list[tuple[str, float, float]]:
        spec = dict(self.param("ranges") or {})
        fn = str(function or self.param("function")).upper()
        rows = next((v for k, v in spec.items() if str(k).upper() == fn), None) or []
        out = []
        for row in rows:
            name, full = str(row[0]), float(row[1])
            out.append((name, full, float(row[2]) if len(row) > 2 else 0.0))
        return sorted(out, key=lambda r: r[1])

    def auto_range(self, level: float, function: str | None = None) -> str | None:
        """The range an unlocked output takes for ``level``: the smallest whose full scale is
        above it (the largest when none is)."""
        table = self._table(function)
        if not table:
            return None
        if str(function or self.param("function")).upper() == "OHM":
            nominal = self._nominal_ohms(level)  # each resistance value is its own range
            return next((name for name, full, _ in table if full == nominal), table[-1][0])
        for name, full, _ in table:
            if abs(level) < full * (1 - 1e-12):
                return name
        return table[-1][0]

    def range_name(self) -> str | None:
        """The range in use: the locked one, or the one the level selects."""
        return self._locked or self.auto_range(float(self.param("level")))

    def _nominal_ohms(self, value: float) -> float:
        """OHM: the nearest of the nominal values (the range table's full-scale values), on a log
        scale; 0 (a short) for values below the smallest non-zero one's half."""
        values = sorted({r[1] for r in self._table("OHM")})
        nonzero = [v for v in values if v > 0]
        if not nonzero:
            return value
        if value <= 0 or (0.0 in values and value < nonzero[0] / 2):
            return 0.0 if 0.0 in values else nonzero[0]
        return min(nonzero, key=lambda v: abs(math.log10(v) - math.log10(value)))

    @op("range")
    def op_range(self, **_: Any) -> OpResult:
        """``RANGE?``: the present range's name."""
        name = self.range_name()
        if name is None:
            raise ScpiError(-221, "this output has no range table")
        return OpResult(name.encode())

    def _set_lock(self, on: bool) -> None:
        path = self.bound_paths().get("range_lock")
        if path is not None and self.owner is not None:
            self.owner.set_state(path, on, source="scpi", internal=True)

    def _check_locked(self, level: float) -> None:
        row = next((r for r in self._table() if r[0] == self._locked), None)
        if row is None:
            return
        _, full, minimum = row
        if abs(level) >= full * (1 - 1e-12):
            raise ScpiError(
                int(self.param("over_range_error")), f"{level:g} is over the locked range {row[0]}"
            )
        if abs(level) < minimum:
            raise ScpiError(
                int(self.param("under_range_error")), f"{level:g} is under the locked range {row[0]}"
            )

    def _control(self) -> Any:
        assert self.owner is not None
        return self.owner.components[str(self.param("control"))]

    def output(self, port: str) -> Signal:
        return self.setpoint_signal()

    def on_state(self, path: str, old: Any, new: Any) -> None:
        key = {v: k for k, v in self.bound_paths().items()}.get(path)
        if key == "range_lock":
            self._locked = self.auto_range(float(self.param("level"))) if _flag(new) else None
            return
        if key == "function" and self._locked is not None and old != new:
            self._locked = None  # a new function unlocks the range (the 5730A manual)
            self._set_lock(False)
        check = getattr(self._control(), "check_sense", None)
        if check is not None and key == "level":
            check(float(new))
        elif check is not None and key == "function":
            check(float(self.param("level")), str(new))
        if key == "frequency" and _flag(self.param("frequency_selects_ac")):
            fn = str(self.param("function")).upper()
            family = {"DCV": "V", "ACV": "V", "DCI": "I", "ACI": "I"}.get(fn)
            target = None if family is None else ("AC" if float(new) > 0 else "DC") + family
            path = self.bound_paths().get("function")
            if target is not None and target != fn and path is not None and self.owner is not None:
                self.setpoint_changed(key, old, new)
                self.owner.set_state(path, target, source="scpi", internal=True)
                return
        if key == "level":
            # (a function change is not checked: the command that changes it writes the level next)
            level = float(new)
            table = self._table()
            if table and abs(level) >= table[-1][1] * (1 + 1e-9):
                raise ScpiError(int(self.param("limit_error")), f"{level:g} is beyond the largest range")
            if str(self.param("function")).upper() == "OHM" and table:
                nominal = self._nominal_ohms(level)
                if nominal != level and self.owner is not None:
                    # the resistance outputs are a fixed set of values: the nearest one (#550)
                    path = self.bound_paths().get("level")
                    if path is not None:
                        self.owner.set_state(path, nominal, source="scpi", internal=True)
                        return
        if key == "level":
            if self._locked is not None:
                self._check_locked(float(new))
            before = self._locked or self.auto_range(float(old))
            after = self._locked or self.auto_range(float(new))
            flipped = float(old) * float(new) < 0
            # only a change while operating moves the output across ranges (in standby nothing settles)
            moved = (before != after or flipped) and self._control().on()
            self._extra_settle = float(self.param("range_settle") or 0.0) if moved else 0.0
        if key in ("level", "function", "frequency"):
            self.setpoint_changed(key, old, new)

    def reset(self) -> None:
        self.drop()
        self._locked = None


@component("calibrator_control")
class CalibratorControl(_Control, Component):
    """The calibrator's output control (#329): ``operate`` (standby disconnects the terminal),
    ``z_out``, ``i_max`` (the overload trip back to standby with ``overload_error``) and
    ``standby_above`` (a change of the output to or from above it returns to standby). It drives
    the setpoint block's signal onto the electrical terminal."""

    role = "driver"
    ports = {
        "in": PortSpec("in", "signal", "the setpoint, from the setpoint block"),
        "out": PortSpec("out", "electrical", "the output terminal"),
        "sense": PortSpec("in", "electrical", "the SENSE HI terminal (external sense, #551)"),
    }
    refs = {"setpoint": ("calibrator_setpoint", "instrument")}
    units = {"standby_above": "V", "z_out": "OHM"}
    defaults = {
        "operate": False,  # False: standby, the terminal is disconnected
        "z_out": 0.0,  # ohm, output impedance (0: an ideal source)
        "i_max": 0.05,  # A, beyond this load current the output trips to standby (<= 0: never)
        "standby_above": None,  # V, a change of the output settings to or from above it -> standby
        "standby_rule": "either",  # either: old or new above standby_above | crossing: from below to above (#553)
        "standby_on_function": False,  # function change while operating -> standby: true any; "above" DCV<->ACV only >= standby_above (#553, #566)
        "overload_error": 0,  # device error code queued on a trip (text from the definition; 0: none)
        "setpoint": "",  # the calibrator_setpoint this control operates
        "remote_sense": False,  # external sense: regulate the SENSE terminal instead of the output (#551)
        "sense_ranges": [],  # the setpoint ranges where external sense is allowed ([]: every range)
        "sense_error": -221,  # the error external sense on a range outside sense_ranges reports
        "compliance": 0.0,  # V a current output can drive: a number or {range name: V, "*": V} (0: no limit, #550)
    }
    piecewise = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tripped = False  # the last overload sent the output to standby; the next OPER clears it

    def _setpoint(self) -> Any:
        assert self.owner is not None
        return self.owner.components[str(self.param("setpoint"))]

    def _drive(self) -> Signal:
        return self.input("in")

    # ---- external sense (#551) ----------------------------------------------------------------------

    def remote(self) -> bool:
        """External sense in force: the setting is on, the SENSE terminal is wired, and the sense
        net has a DC path to the output side (else the output regulates itself, the internal
        SENSE-to-OUTPUT connection of the manual; a sense lead into a dead end cannot carry the
        feedback, #562)."""
        return _flag(self.param("remote_sense")) and self.wired("sense") and self._sense_served()

    def _sense_served(self) -> bool:
        """The SENSE net reaches the output terminal's net or ground through the bench's conducting
        parts and wire accessories (two-terminal conductors between their wired ports, one-terminal
        ones to ground). Pure wire topology — the nets exist before any stamping — so the answer is
        the same however the stamp order runs; instruments are not conductors, so SENSE on nothing
        but a high-impedance input counts as unserved."""
        start = self.session.node_of(self, "sense")
        goal = self.session.node_of(self, "out")
        seen = {start}
        queue = [start]
        while queue:
            net = queue.pop()
            for comp in self.session.components.values():
                declared = [p for p, spec in type(comp).ports.items() if spec.medium == "electrical"]
                nets = [self.session.port_net.get(f"{comp.name}.{p}") for p in declared]
                wired = [n for n in nets if n is not None]
                if len(declared) == 1 and net in wired:
                    return True  # a one-terminal conductor (load_resistor) ties this net to ground
                if net in wired:
                    for n in wired:
                        if n == goal:
                            return True
                        if n not in seen:
                            seen.add(n)
                            queue.append(n)
        return False

    def _sense_allowed(self, range_name: str | None) -> bool:
        allowed = [str(r).upper() for r in self.param("sense_ranges") or []]
        return not allowed or (range_name is not None and range_name.upper() in allowed)

    def check_sense(self, level: float, function: str | None = None) -> None:
        """A new level or function while external sense is on must stay on a range that allows it."""
        if not _flag(self.param("remote_sense")):
            return
        sp = self._setpoint()
        name = getattr(sp, "_locked", None) if function is None else None
        name = name or sp.auto_range(level, function)
        if not self._sense_allowed(name):
            raise ScpiError(int(self.param("sense_error")), f"external sense is not available on {name}")

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        if not (self.on() and self.remote()):
            super().stamp(circuit, node)
            return
        z = float(self.param("z_out") or 0.0)
        if z <= 0:
            circuit.loop_source(node("sense"), GROUND, node("out"), GROUND, self.uid)
        else:
            circuit.loop_source(node("sense"), GROUND, node("a"), GROUND, self.uid)
            circuit.resistor(node("a"), node("out"), z)

    def _load_current(self, dc_voltage: Callable[[str], float]) -> float:
        if self.remote():
            return self.session.source_current(self.uid)
        return super()._load_current(dc_voltage)

    def on_state(self, path: str, old: Any, new: Any) -> None:
        key = {v: k for k, v in self.bound_paths().items()}.get(path)
        if key == "operate":
            self._operate_changed(old, new)
        elif key == "remote_sense":
            if _flag(new):
                sp = self._setpoint()
                if not self._sense_allowed(sp.range_name()):
                    raise ScpiError(
                        int(self.param("sense_error")),
                        f"external sense is not available on {sp.range_name()}",
                    )
            self.session.invalidate(electrical=True)

    def reset(self) -> None:
        self.tripped = False

    @op("operate")
    def op_operate(self, value: bool = True, **_: Any) -> OpResult:
        """``OPER`` (value true) or ``STBY`` (false): enter or leave operate; entering starts the
        settling of the level."""
        self._set_operate(_flag(value))
        return OpResult()

    def readings(self) -> dict[str, Any]:
        return self.control_readings()


@component("calibrator_output")
class CalibratorOutput(_Setpoint, _Control, Component):
    """A calibrator's setpoint and output control in one block: ``function`` (DCV | ACV),
    ``level`` (V; RMS for ACV), ``frequency`` (Hz; 0 for DCV), ``operate``, ``z_out``, ``i_max``,
    ``settle`` (s, or a table per function), ``standby_above`` (V), and the hidden
    ``gain_error_ppm`` / ``offset`` of the output stage. The same behaviour as
    ``calibrator_setpoint`` + ``calibrator_control`` (#329), for definitions written before the
    split.

    The settings are bound to instrument state (``function: $out.function`` ...); the commands
    write the state and the component follows. ``settle`` is taken from the table by the target
    function when it is a dict (``{DCV: 1.0, ACV: 2.0}``)."""

    role = "driver"
    ports = {"out": PortSpec("out", "electrical", "the output terminal")}
    units = {"level": "V", "offset": "V", "standby_above": "V", "z_out": "OHM"}
    defaults = {
        "function": "DCV",  # DCV | ACV
        "level": 0.0,  # V, the set amplitude (RMS volts for ACV)
        "frequency": 0.0,  # Hz, the AC line (0 with DCV)
        "operate": False,  # False: standby, the terminal is disconnected
        "z_out": 0.0,  # ohm, output impedance (0: an ideal source)
        "i_max": 0.05,  # A, beyond this load current the output trips to standby (<= 0: never)
        "settle": 1.0,  # s until a new value is reached, or a table per function
        "standby_above": None,  # V, a change of the output settings to or from above it -> standby
        "standby_rule": "either",  # either: old or new above standby_above | crossing: from below to above (#553)
        "standby_on_function": False,  # function change while operating -> standby: true any; "above" DCV<->ACV only >= standby_above (#553, #566)
        "overload_error": 0,  # device error code queued on a trip (text from the definition; 0: none)
        "gain_error_ppm": 0.0,  # the output's own gain error, ppm (hidden)
        "offset": 0.0,  # the output's own DC offset error, V (hidden, DCV only)
    }
    piecewise = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tripped = False
        self._trans: tuple[int, int, float, float] | None = None
        self._settled = 0.0

    def _control(self) -> Any:
        return self

    def _setpoint(self) -> Any:
        return self

    def _drive(self) -> Signal:
        return self.setpoint_signal()

    def _output_signal(self) -> Signal:  # the name earlier code and tests use
        return self.setpoint_signal()

    def on_state(self, path: str, old: Any, new: Any) -> None:
        key = {v: k for k, v in self.bound_paths().items()}.get(path)
        if key == "operate":
            self._operate_changed(old, new)
        elif key in ("level", "function", "frequency"):
            self.setpoint_changed(key, old, new)

    def reset(self) -> None:
        self.tripped = False
        self.drop()

    @op("operate")
    def op_operate(self, value: bool = True, **_: Any) -> OpResult:
        """``OPER`` (value true) or ``STBY`` (false): enter or leave operate; entering starts the
        settling of the level."""
        self._set_operate(_flag(value))
        return OpResult()

    def readings(self) -> dict[str, Any]:
        return self.control_readings()
