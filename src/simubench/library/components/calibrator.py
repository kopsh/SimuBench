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

While the output is above ``standby_above`` (new setting or old), any change of the output
settings returns the instrument to standby; outputting the value needs an explicit ``OPER``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ...engine.circuit import GROUND, Circuit
from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal


def _flag(value: Any) -> bool:
    return bool(value) if not isinstance(value, str) else value.upper() in ("1", "ON", "TRUE")


@component("calibrator_output")
class CalibratorOutput(Component):
    """A calibrator's output: ``function`` (DCV | ACV), ``level`` (V; RMS for ACV), ``frequency``
    (Hz; 0 for DCV), ``operate``, ``z_out``, ``i_max``, ``settle`` (s, or a table per function),
    ``standby_above`` (V), and the hidden ``gain_error_ppm`` / ``offset`` of the output stage, the
    instrument's own errors and the truth behind a calibration (like the meter core's).

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
        "overload_error": 0,  # device error code queued on a trip (text from the definition; 0: none)
        "gain_error_ppm": 0.0,  # the output's own gain error, ppm (hidden)
        "offset": 0.0,  # the output's own DC offset error, V (hidden, DCV only)
    }
    piecewise = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tripped = False  # the last overload sent the output to standby; the next OPER clears it
        self._trans: tuple[int, int, float, float] | None = None  # (start ns, ns, from, to)
        self._settled = 0.0  # the amplitude the output rests at (0.0 in standby)

    # ---- output state ---------------------------------------------------------------------------

    def on(self) -> bool:
        """The terminal is in the circuit: the instrument is in operate."""
        return _flag(self.param("operate"))

    def _settle_s(self) -> float:
        """The settling time of the target function (a ``settle`` table is taken by function)."""
        s = self.param("settle")
        if isinstance(s, dict):
            s = s.get(str(self.param("function")).upper(), 0.0)
        try:
            v = float(s)
        except (TypeError, ValueError):
            v = 0.0
        return max(v, 0.0)

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

    def _true_level(self) -> float:
        """The amplitude the output stage actually makes: the setting through the transition and
        its own hidden errors (the truth a calibrated meter is checked against)."""
        true = self._level_now() * (1 + float(self.param("gain_error_ppm")) * 1e-6)
        if str(self.param("function")).upper() != "ACV":  # an offset is a DC concept, as on the meter
            true += float(self.param("offset"))
        return true

    def _output_signal(self) -> Signal:
        """What the source drives: the level as DC (DCV) or one line of that RMS value (ACV)."""
        level = self._true_level()
        if str(self.param("function")).upper() == "ACV":
            f = float(self.param("frequency"))
            if f <= 0:
                return Signal.constant(0.0)
            return Signal.lines([f], [complex(level * math.sqrt(2.0))])  # peak phasor of the RMS level
        return Signal.constant(level)

    # ---- circuit --------------------------------------------------------------------------------

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        if not self.on():
            return  # standby: the terminal is out of the circuit (5.1)
        z = float(self.param("z_out") or 0.0)
        if z <= 0:
            # An ideal source with a tracked branch current: the overload check reads it.
            circuit.loop_source(node("out"), GROUND, node("out"), GROUND, self.uid)
        else:
            circuit.source(node("out"), self.uid, z)

    def emf(self) -> dict[str, Signal]:
        if not self.on():
            return {}
        return {self.uid: self._output_signal()}

    def _load_current(self, dc_voltage: Callable[[str], float]) -> float:
        """The current the operating point draws from the terminal (D34)."""
        z = float(self.param("z_out") or 0.0)
        if z <= 0:
            return self.session.source_current(self.uid)
        return (self._output_signal().dc - dc_voltage("out")) / z

    def update_mode(self, dc_voltage: Callable[[str], float]) -> bool:
        if not self.on():
            return False
        i_max = float(self.param("i_max") or 0.0)
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
            code = int(self.param("overload_error") or 0)
            if code:
                self.owner.push_error(
                    code, f"load current {abs(current):.4g} A exceeds the {i_max:.4g} A capability"
                )

    # ---- settings ---------------------------------------------------------------------------------

    def _start_settling(self, target: float) -> None:
        """The amplitude moves to ``target`` through a first-order transition, done after
        ``settle``; that moment is a pending operation (*OPC? waits for it, 3.3)."""
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

    def _operate_changed(self, old: Any, new: Any) -> None:
        """Leaving or entering operate: the output starts from 0 (it was disconnected) and settles
        to the level; anything pending is dropped. OPER from a tripped standby tries again."""
        if _flag(new):
            self.tripped = False
        self._trans = None
        self._settled = 0.0
        if self.owner is not None:
            self.owner.cancel_pending(self.uid)
        if _flag(new):
            self._start_settling(float(self.param("level")))

    def _output_changed(self, key: str, old: Any, new: Any) -> None:
        """A change of the output settings while operating: above ``standby_above`` (old or new) it
        returns the instrument to standby, otherwise the new value is settled to."""
        was_on = self.on()
        sa = self.param("standby_above")
        if was_on and sa is not None:
            lo = float(old) if key == "level" else float(self.param("level"))
            ln = float(new) if key == "level" else float(self.param("level"))
            if abs(lo) > float(sa) or abs(ln) > float(sa):
                self._set_operate(False)
                return
        if self.on():
            self._start_settling(float(self.param("level")))

    def _set_operate(self, value: bool) -> None:
        """Operate through the bound state (the normal path) or the component's own attribute."""
        path = self.bound_paths().get("operate") if self.owner is not None else None
        if path is None:
            old = _flag(self.param("operate"))
            if old == value:
                return
            self.overrides["operate"] = value
            self._operate_changed(old, value)
            self.session.invalidate(electrical=True)
            return
        assert self.owner is not None
        self.owner.set_state(path, value, internal=True)

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        key = bound.get(path)
        if key == "operate":
            self._operate_changed(old, new)
        elif key in ("level", "function", "frequency"):
            self._output_changed(key, old, new)

    def reset(self) -> None:
        self.tripped = False
        self._trans = None
        self._settled = 0.0
        if self.owner is not None:
            self.owner.cancel_pending(self.uid)

    # ---- operations -------------------------------------------------------------------------------

    @op("operate")
    def op_operate(self, value: bool = True, **_: Any) -> OpResult:
        """``OPER`` (value true) or ``STBY`` (false): enter or leave operate; entering starts the
        settling of the level."""
        self._set_operate(_flag(value))
        return OpResult()

    # ---- observability ----------------------------------------------------------------------------

    def readings(self) -> dict[str, Any]:
        self.session._solve()
        values: dict[str, Any] = {
            "output": "OPER" if self.on() else "STBY",
            "function": str(self.param("function")),
            "level": float(self.param("level")),
            "frequency": float(self.param("frequency")),
            "tripped": self.tripped,
            "settling": self._trans is not None,
        }
        if self.wired("out"):
            values["voltage"] = self.pin_signal("out").mean()
        return {"values": values}
