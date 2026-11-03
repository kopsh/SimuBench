"""Programmable power supplies: per-channel setpoint, loop, stage, output, readback and protection,
and the cross-channel tracking / parallel / series combination (B2, #185).

The blocks follow component-map.md power_supply: every channel is a setpoint DAC, the CV/CC control
loop reused from B1 (``output_control_loop``), the power stage's envelope, the output stage (relay,
remote sense, the stamped source), a readback and a protection latch. The ±25 V outputs (channels 2
and 3) can be combined: track (|CH3| = |CH2| and changes mirror), parallel (same voltage) or series
(CH3 = −CH2); the pair must be off to change the combination, and the bench wiring makes the
physical connection.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ...engine.circuit import Circuit
from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .smu import OutputControlLoop

#: Device error of E36300 series: the output cannot be enabled while a protection fault stands.
OUTPUT_DISABLED = 729
#: Device error of E36300 series: output 3 cannot be programmed while it is part of a combination.
CH3_COMBINED = 720


@component("power_supply_setpoint")
class PowerSupplySetpoint(Component):
    """A channel's setpoint DAC (B2 #185): the voltage and current levels, within the channel's
    range (``stage.v_max`` / ``stage.i_max``). The control loop programs from the same settings."""

    refs = {"stage": ("power_stage", "channel")}
    defaults = {
        "voltage": 0.0,  # V, programmed output voltage
        "current": 1.0,  # A, current limit
        "stage": "",  # the channel's power_stage (range and envelope)
    }
    units = {"voltage": "V", "current": "A"}

    def _stage(self) -> PowerStage:
        assert self.owner is not None
        name = str(self.param("stage"))
        full = f"ch{self.channel}.{name}" if self.channel is not None else name
        return self.owner.components[full]  # type: ignore[return-value]

    def voltage(self) -> float:
        return float(self.param("voltage"))

    def current(self) -> float:
        return float(self.param("current"))

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        key = bound.get(path)
        if key == "voltage" and abs(float(new)) > float(self._stage().param("v_max")) + 1e-9:
            raise ScpiError(-222, "the programmed voltage is outside the channel's range")
        if key == "current" and float(new) > float(self._stage().param("i_max")) + 1e-9:
            raise ScpiError(-222, "the current limit is above the channel's capability")


@component("power_stage")
class PowerStage(Component):
    """The channel's power stage (B2 #185): voltage/current capability and the current-limit cap.

    ``limit_cap`` is what the reused ``output_control_loop`` asks its envelope: the current limit a
    channel may program. ``v_max`` is the voltage ceiling the output stage clips the programmed
    level to, so a definition whose state allows a wider number still cannot over-drive the stage.
    """

    units = {"v_max": "V", "i_max": "A", "p_max": "W"}
    defaults = {
        "v_max": 25.75,  # V, the channel's maximum output voltage (magnitude)
        "i_max": 1.03,  # A, the channel's maximum current
        "p_max": 25.75,  # W, the channel's maximum output power
    }

    def limit_cap(self, function: str, level: float) -> float:
        return float(self.param("i_max"))


@component("power_output")
class PowerOutput(Component):
    """The output stage (B2 #185): relay, remote sense and the source the loop programs.

    The relay closes ``relay_s`` after the output is switched on (and opens at once when it is
    switched off), so the output timings count in virtual time (D12). The source is a voltage
    source regulating the sense pair (or the force terminals) in CV mode, a current source in CC
    mode — the same elements the SMU's output stage uses, driven by the reused control loop.
    """

    refs = {
        "loop": ("output_control_loop", "channel"),
        "stage": ("power_stage", "channel"),
        "protection": ("supply_protection", "channel"),
        "combination": ("supply_tracking", "instrument"),
    }
    optional_refs = frozenset({"combination"})
    ports = {
        "pos": PortSpec("inout", "electrical", "the positive output terminal"),
        "neg": PortSpec("inout", "electrical", "the negative output terminal"),
        "sense_pos": PortSpec("in", "electrical", "the positive remote-sense terminal"),
        "sense_neg": PortSpec("in", "electrical", "the negative remote-sense terminal"),
    }
    sense_ports = ("sense_pos", "sense_neg")
    sense_param = "remote_sense"
    units = {"relay_s": "S", "r_out": "OHM"}
    defaults = {
        "output": False,  # the output switch (a setting)
        "remote_sense": False,  # regulate the sense pair instead of the force terminals
        "r_out": 1.0e-3,  # output impedance of the pass stage, ohm (parallel outputs stay solvable)
        "relay_s": 0.01,  # s from switching the output on to the relay closing
        "rise_delay": 0.0,  # s the output waits before turning on, added to relay_s (#540)
        "fall_delay": 0.0,  # s the output stays on after being switched off; a trip is immediate (#540)
        "loop": "",  # this channel's output_control_loop (the reused B1 loop)
        "stage": "",  # this channel's power_stage (the envelope)
        "protection": "",  # this channel's supply_protection (the trip latch)
        "combination": "",  # the instrument's supply_tracking (an internal pair idles output 3; "" none)
    }
    piecewise = True
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._closed = False  # the relay's state (what the circuit sees)
        self._relay_pending = False  # a close is scheduled
        # Constant power (#533): the output is held to the stage's p_max as a current source of
        # this value (A) while the load asks for more than p_max; None outside the region.
        self._p_current: float | None = None
        self._p_sign = 1.0  # the output's polarity in the region (a negative output sources negative)

    def _sibling(self, name: str) -> Any:
        assert self.owner is not None
        full = f"ch{self.channel}.{name}" if self.channel is not None else name
        return self.owner.components[full]

    def _loop(self) -> OutputControlLoop:
        return self._sibling(str(self.param("loop")))

    def _stage(self) -> PowerStage:
        return self._sibling(str(self.param("stage")))

    def _protection(self) -> SupplyProtection:
        return self._sibling(str(self.param("protection")))

    def idle(self) -> bool:
        """Out of operation: output 3 while output 2 carries an internal series/parallel pair."""
        name = str(self.param("combination") or "")
        if not name:
            return False
        assert self.owner is not None
        combination: SupplyTracking = self.owner.components[name]  # type: ignore[assignment]
        return combination.idle(self.channel)

    def conducting(self) -> bool:
        return self._closed and not self._protection().tripped() and not self.idle()

    def remote(self) -> bool:
        if not (bool(self.param("remote_sense")) and self.wired("sense_pos") and self.wired("sense_neg")):
            return False
        return self.session.node_of(self, "sense_pos") != self.session.node_of(self, "sense_neg")

    @property
    def relay_key(self) -> str:
        return f"{self.uid}/relay"

    # ---- the relay -------------------------------------------------------------------------------

    def _close(self) -> None:
        self._relay_pending = False
        self._closed = True
        self.session.invalidate(electrical=True)

    def _open(self) -> None:
        assert self.owner is not None
        self.owner.cancel_pending(self.relay_key)
        self._relay_pending = False
        self._p_current = None
        if self._closed:
            self._closed = False
            self.session.invalidate(electrical=True)

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        key = bound.get(path)
        if key == "output":
            assert self.owner is not None
            if new:
                if self._protection().tripped():
                    raise ScpiError(OUTPUT_DISABLED, "the output is disabled by a protection fault")
                self._open()
                delay = float(self.param("rise_delay") or 0.0) + float(self.param("relay_s"))
                when = self.owner.now_ns() + round(delay * 1e9)
                self.owner.register_pending(self.relay_key, when, self._close)
                self._relay_pending = True
            elif float(self.param("fall_delay") or 0.0) > 0 and self._closed:
                # the output stays on until the fall delay has elapsed (#540)
                when = self.owner.now_ns() + round(float(self.param("fall_delay")) * 1e9)
                self.owner.register_pending(self.relay_key, when, self._open)
            else:
                self._open()
        elif key == "remote_sense":
            self.session.invalidate(electrical=True)

    def sync(self) -> None:
        """A bench that starts with the output on closes its relay on the first solve."""
        if (
            bool(self.param("output"))
            and not self._closed
            and not self._relay_pending
            and not self._protection().tripped()
        ):
            assert self.owner is not None
            when = self.owner.now_ns() + round(float(self.param("relay_s")) * 1e9)
            self.owner.register_pending(self.relay_key, when, self._close)
            self._relay_pending = True

    def reset(self) -> None:
        self._open()

    # ---- the circuit ------------------------------------------------------------------------------

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        if not self.conducting():
            return
        loop = self._loop()
        r = max(float(self.param("r_out")), 1e-9)
        if loop.mode == "V" and self._p_current is None:
            if self.remote():
                circuit.loop_source(node("sense_pos"), node("sense_neg"), node("a"), node("b"), self.uid)
            else:
                circuit.loop_source(node("a"), node("b"), node("a"), node("b"), self.uid)
        else:
            circuit.current(node("a"), self.uid)
            circuit.current(node("b"), self.uid, sign=-1.0)
        circuit.resistor(node("a"), node("pos"), r)
        circuit.resistor(node("b"), node("neg"), r)

    def _clipped(self, value: float, cap: float) -> float:
        return math.copysign(min(abs(value), abs(cap)), value) if value != 0 else 0.0

    def emf(self) -> dict[str, Signal]:
        if not self.conducting():
            return {}
        loop = self._loop()
        stage = self._stage()
        if self._p_current is not None:
            return {self.uid: Signal.constant(self._p_sign * self._p_current)}
        if loop.mode == "V":
            return {self.uid: Signal.constant(self._clipped(loop.targets()[0], float(stage.param("v_max"))))}
        return {self.uid: Signal.constant(self._clipped(loop.targets()[1], float(stage.param("i_max"))))}

    def _metered(self, dc_voltage: Callable[[str], float]) -> float:
        """The voltage the loop regulates: the sense pair with remote sense, else the internal
        output (before the pass stage's output impedance)."""
        if self.remote():
            return dc_voltage("sense_pos") - dc_voltage("sense_neg")
        return dc_voltage("a") - dc_voltage("b")

    def update_mode(self, dc_voltage: Callable[[str], float]) -> bool:
        if not self.conducting():
            self._protection().watch_cc(False)
            return False
        loop = self._loop()
        v = self._metered(dc_voltage)
        if self._p_current is not None:
            return self._update_power(v)
        current = self.session.source_current(self.uid) if loop.mode == "V" else 0.0
        changed = loop.update_mode(v, current)
        if changed:
            # The operating point just solved belongs to the old mode (e.g. CC at a raised limit
            # would read the load's full I·R): protection judges only a settled point (#271).
            return True
        i = current if loop.mode == "V" else loop.targets()[1]
        p_max = self._p_max()
        if p_max > 0 and abs(v * i) > p_max * (1 + 1e-9) and v != 0:
            # past the stage's power rating: hold the output to p_max (#533)
            self._p_current = p_max / abs(v)
            self._p_sign = 1.0 if v > 0 else -1.0
            self._protection().watch_cc(False)
            return True
        protection = self._protection()
        protection.watch_cc(loop.mode != "V")
        if not protection.tripped() and protection.check(v, i):
            protection.trip(v, i)
            changed = True
        elif not protection.tripped() and loop.mode != "V" and protection.cc_due_now():
            protection.trip_ocp()
            changed = True
        return changed

    def _p_max(self) -> float:
        return float(self._stage().param("p_max") or 0.0)

    def _update_power(self, v: float) -> bool:
        """The constant-power region (#533): the current that makes V·I = p_max at the load. The
        update is the geometric mean of the present current and p_max / V, which lands on the
        operating point in one step for a resistive load and converges for others. The output
        leaves the region when the voltage setpoint or the current limit holds it below p_max."""
        assert self._p_current is not None
        loop = self._loop()
        v_set, i_lim = abs(loop.targets()[0]), abs(loop.targets()[1])
        if abs(v) > v_set * (1 + 1e-9) or self._p_current > i_lim * (1 + 1e-9):
            self._p_current = None  # the setpoint or the limit holds the output again
            loop.restart()
            return True
        if v == 0:
            return False
        nxt = math.sqrt(self._p_current * self._p_max() / abs(v))
        nxt = min(nxt, i_lim)
        if abs(nxt - self._p_current) <= 1e-12 * max(nxt, 1e-12):
            return False
        self._p_current = nxt
        return True

    # ---- the readback -----------------------------------------------------------------------------

    def measured(self) -> tuple[float, float]:
        """(output voltage, output current as a magnitude) of a conducting output."""
        self.session._solve()
        if not self.conducting():
            return 0.0, 0.0
        remote = self.remote()
        if remote:
            v = self.pin_signal("sense_pos").mean() - self.pin_signal("sense_neg").mean()
        else:
            v = self.pin_signal("pos").mean() - self.pin_signal("neg").mean()
        loop = self._loop()
        if self._p_current is not None:
            i = self._p_current
        elif loop.mode == "V":
            r = max(float(self.param("r_out")), 1e-9)
            i = (self.pin_signal("a").mean() - self.pin_signal("pos").mean()) / r
        else:
            i = loop.targets()[1]
        return v, abs(i)

    def regulation(self) -> str:
        """CV, CC, CP (held to the stage's power rating, #533) or OFF."""
        if not self.conducting():
            return "OFF"
        if self._p_current is not None:
            return "CP"
        return "CV" if self._loop().mode == "V" else "CC"


@component("supply_readback")
class SupplyReadback(Component):
    """A channel's V/I/P readback (B2 #185): the voltmeter and ammeter of the output, with the
    regulation state (CV / CC / OFF) the front panel shows."""

    refs = {"output": ("power_output", "channel")}
    defaults = {"output": ""}  # the channel's power_output

    def _output(self) -> PowerOutput:
        assert self.owner is not None
        name = str(self.param("output"))
        full = f"ch{self.channel}.{name}" if self.channel is not None else name
        return self.owner.components[full]  # type: ignore[return-value]

    @op("measure")
    def op_measure(self, what: str = "voltage", **_: Any) -> OpResult:
        v, i = self._output().measured()
        value = {"voltage": v, "current": i, "power": v * i}[what]
        return OpResult(self.reply_number(value, 7))

    @op("condition")
    def op_condition(self, query: bool = False, **_: Any) -> OpResult:
        """The regulation as a status condition (#271, STAT:QUES:INST:ISUM<n>:COND?): 0 off or
        unregulated, 1 constant current, 2 constant voltage."""
        self.session._solve()
        return OpResult(V.format_int({"CC": 1, "CV": 2}.get(self._output().regulation(), 0)))

    def status_flags(self) -> dict[str, bool]:
        """CC and CV for a channel's questionable instrument summary register (#348)."""
        regulation = self._output().regulation()
        return {"CC": regulation == "CC", "CV": regulation == "CV"}

    def readings(self) -> dict[str, Any]:
        v, i = self._output().measured()
        return {
            "values": {
                "voltage": v,
                "current": i,
                "power": v * i,
                "regulation": self._output().regulation(),
            }
        }


@component("supply_protection")
class SupplyProtection(Component):
    """A channel's OVP / OCP latch (B2 #185).

    Crossing a threshold (0 disables it) latches the fault and turns the output off; enabling the
    output while the fault stands is refused with the E36300's own error 729. ``clear`` (the
    model's ``OUTP:PROT:CLE``) removes the latch and restores the output state that existed before
    the fault, as the manual describes.
    """

    defaults = {
        "ovp": 0.0,  # over-voltage threshold, V (0: off)
        "ocp": 0.0,  # over-current threshold, A (0: off)
        "output": False,  # the channel's output setting (watched)
        # OCP by time in CC (#271, the E36300's form): enabled, the output trips once it has been
        # in constant current for ocp_delay. The timer starts on entering CC; with "SCHange" a
        # settings change (voltage, current, output) while in CC starts it again, with "CCTRans"
        # only a transition into CC does.
        "ocp_enabled": False,  # OCP by CC duration on (CURR:PROT:STAT)
        "ocp_delay": 0.0,  # s in CC before the trip (CURR:PROT:DEL)
        "ocp_start": "SCHange",  # what starts the delay: SCHange | CCTRans (CURR:PROT:DEL:STAR)
        "voltage": 0.0,  # the channel's voltage setting (watched: a settings change)
        "current": 0.0,  # the channel's current setting (watched: a settings change)
    }
    units = {"ovp": "V", "ocp": "A", "ocp_delay": "S", "voltage": "V", "current": "A"}
    choices = {"ocp_start": ("SCHange", "CCTRans")}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tripped = ""  # "OVP" | "OCP" | ""
        self._restore = False  # was the output on when the fault came?
        self._disabling = False
        self._in_cc = False  # the output's last solved mode was constant current
        self._cc_since: int | None = None  # when the OCP delay started (None: not running)

    # ---- OCP by time in constant current (#271) ---------------------------------------------------

    def watch_cc(self, in_cc: bool) -> None:
        """The output's solved mode: entering CC starts the OCP delay, leaving it stops it."""
        if in_cc and not self._in_cc:
            self._start_delay()
        elif not in_cc and self._in_cc:
            self._stop_delay()
        self._in_cc = in_cc

    def _start_delay(self) -> None:
        if self.owner is None or self._tripped or not bool(self.param("ocp_enabled")):
            return
        now = self.owner.now_ns()
        self._cc_since = now
        due = now + round(float(self.param("ocp_delay")) * 1e9)
        self.owner.register_pending(f"{self.uid}/ocp", due, self._ocp_due, waited=False)

    def _stop_delay(self) -> None:
        self._cc_since = None
        if self.owner is not None:
            self.owner.cancel_pending(f"{self.uid}/ocp")

    def _ocp_due(self) -> None:
        if self._tripped or self._cc_since is None or not self._in_cc or not bool(self.param("ocp_enabled")):
            return
        self._cc_since = None
        self._restore = bool(self.param("output"))
        self._tripped = "OCP"
        self._disable_output()
        self.session.invalidate(electrical=True)

    def cc_due_now(self) -> bool:
        """A zero OCP delay trips on entering CC, inside the solve like a threshold."""
        return bool(self.param("ocp_enabled")) and float(self.param("ocp_delay")) <= 0

    def tripped(self) -> bool:
        return bool(self._tripped)

    def kind(self) -> str:
        return self._tripped

    def check(self, v: float, i: float) -> bool:
        if self._tripped:
            return False
        ovp, ocp = float(self.param("ovp")), float(self.param("ocp"))
        return (ovp > 0 and abs(v) > ovp) or (ocp > 0 and abs(i) > ocp)

    def _output_path(self) -> str:
        return f"ch{self.channel}.output" if self.channel is not None else "output"

    def trip(self, v: float, i: float) -> None:
        if self._tripped:
            return
        self._restore = bool(self.param("output"))
        ovp, ocp = float(self.param("ovp")), float(self.param("ocp"))
        self._tripped = "OVP" if ovp > 0 and abs(v) > ovp else "OCP" if ocp > 0 and abs(i) > ocp else "PROT"
        if self.owner is not None:
            # The output goes off outside the circuit solve (the state write is a pending action).
            key = f"{self.uid}/trip"
            self.owner.register_pending(key, self.owner.now_ns(), self._disable_output)

    def trip_ocp(self) -> None:
        """OCP with no delay: the output trips on entering constant current."""
        if self._tripped:
            return
        self._restore = bool(self.param("output"))
        self._tripped = "OCP"
        if self.owner is not None:
            self.owner.register_pending(f"{self.uid}/trip", self.owner.now_ns(), self._disable_output)

    def _disable_output(self) -> None:
        if self.owner is None or not self._restore:
            return
        self._disabling = True
        try:
            self.owner.set_state(self._output_path(), False)
        finally:
            self._disabling = False

    def clear(self, kind: str | None = None) -> None:
        """Remove the latch (only a ``kind`` fault when one is named: CURR:PROT:CLE clears OCP,
        VOLT:PROT:CLE OVP) and restore the output state from before the fault."""
        if kind is not None and self._tripped != kind.upper():
            return
        restore = self._restore
        self._tripped = ""
        self._restore = False
        if restore and self.owner is not None:
            self.owner.set_state(self._output_path(), True)

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        key = bound.get(path)
        if key == "output" and new and self._tripped and not self._disabling:
            raise ScpiError(OUTPUT_DISABLED, "the output is disabled by a protection fault")
        if key in ("voltage", "current", "output", "ocp_enabled") and bool(self.param("ocp_enabled")):
            # Solving is lazy: bring the CC state up to now before judging the change. The OCP
            # timer runs from when the simulator sees CC (a command or a query), not before.
            self.session._solve()
        if key in ("voltage", "current", "output") and self._in_cc and self.choice("ocp_start") == "SCHange":
            self._start_delay()  # a settings change restarts the delay while in CC
        if key == "ocp_enabled":
            if new and self._in_cc:
                self._start_delay()  # enabled while already in CC: the delay runs from now
            elif not new:
                self._stop_delay()

    def reset(self) -> None:
        self._tripped = ""
        self._restore = False
        self._in_cc = False
        self._stop_delay()

    @op("tripped")
    def op_tripped(self, query: bool = False, kind: str | None = None, **_: Any) -> OpResult:
        """Whether a fault is latched; with ``kind`` (OVP / OCP) whether that one is."""
        self.session._solve()
        if kind is not None:
            return OpResult(V.format_bool(self._tripped == kind.upper()))
        return OpResult(V.format_bool(self.tripped()))

    @op("kind")
    def op_kind(self, query: bool = False, **_: Any) -> OpResult:
        self.session._solve()
        return OpResult(V.format_string(self.kind()))

    @op("clear")
    def op_clear(self, kind: str | None = None, **_: Any) -> OpResult:
        self.clear(kind)
        return OpResult()

    def readings(self) -> dict[str, Any]:
        return {"values": {"tripped": self._tripped or "—"}}


@component("supply_tracking")
class SupplyTracking(Component):
    """The combination of outputs 2 and 3 (B2 #185, #259): track, parallel or series.

    ``mode`` is the instrument's combination state (OFF / TRAC / PAR / SER). Enabling a combination
    requires both outputs off (the manual's constraint). ``topology`` says how the model combines:

    - ``split`` (E36311A, ±25 V): output 3 is the negative supply. A change of either programmed
      voltage is mirrored to the other — track keeps ``CH3 = −|CH2|``, parallel ``CH3 = CH2`` and
      series ``CH3 = −CH2``; the physical connection is the bench wiring.
    - ``internal`` (E36312A / E36313A, two +25 V outputs, user's guide "Specifying the Operation
      Mode" and "Using the Tracking Operation"): track sets output 3 to the same voltage as
      output 2 and vice versa; series and parallel are made inside the instrument — output 2
      delivers the combination (twice the voltage, or twice the current) and output 3 no longer
      operates, so programming it is refused with error 720 (the manual's "CH3 is not allowed
      when serial/parallel/track is enabled"; tracking keeps output 3 programmable, the guide's
      "and vice versa"). Output 2's range follows (its voltage/current settings' limits and the
      ``v_max``/``i_max``/``p_max`` of its stage); leaving the combination brings the
      independent range back and clips a level above it.
    """

    defaults = {
        "mode": "OFF",  # OFF | TRAC | PAR | SER (the instrument's combination state)
        "track": False,  # a separate tracking switch (OUTPut:TRACk) for a mode without TRAC
        "topology": "split",  # split (E36311A ±25 V) | internal (E36312A: output 2 carries the pair)
        "voltage2": 0.0,  # CH2's programmed voltage (watched)
        "voltage3": 0.0,  # CH3's programmed voltage (watched)
        "current3": 0.0,  # CH3's current limit (watched: refused while output 3 idles)
        "output2": False,  # CH2's output state (must be off to change the combination)
        "output3": False,  # CH3's output state
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mirroring = False
        self._widened = ""  # the internal combination output 2's range is widened for ("SER"/"PAR")

    _MODES = {
        "OFF": "OFF",
        "TRAC": "TRAC",
        "TRACK": "TRAC",
        "PAR": "PAR",
        "PARALLEL": "PAR",
        "SER": "SER",
        "SERIES": "SER",
    }

    def mode(self) -> str:
        mode = self._MODES.get(str(self.param("mode")).upper(), "OFF")
        if mode == "OFF" and bool(self.param("track")):
            return "TRAC"
        return mode

    def internal(self) -> bool:
        return str(self.param("topology")) == "internal"

    def idle(self, channel: int | None) -> bool:
        """Output 3 no longer operates while output 2 carries an internal series/parallel pair."""
        return channel == 3 and self.internal() and self.mode() in ("SER", "PAR")

    def _voltage(self, channel: int) -> float:
        assert self.owner is not None
        return float(self.owner.get_state(f"ch{channel}.voltage"))

    def _target(self, mode: str, master: float) -> float:
        if self.internal():
            return master  # tracking: the same voltage (series/parallel do not mirror)
        # Both combined modes put CH3 at the negative level: the series connection stacks the two
        # outputs, the parallel connection crosses the negative output's terminals (the two then
        # push the same polarity into the load).
        if mode == "TRAC":
            return -abs(master)
        return -master  # SER and PAR

    def _mirrors(self, mode: str) -> bool:
        return mode != "OFF" and not (self.internal() and mode in ("SER", "PAR"))

    def _apply(self) -> None:
        mode = self.mode()
        # Series/parallel change with outputs 2 and 3 off; the internal topology's tracking is a
        # level link and is not held to that here.
        rebuild = mode in ("SER", "PAR") or (mode == "TRAC" and not self.internal())
        if rebuild and (bool(self.param("output2")) or bool(self.param("output3"))):
            raise ScpiError(-221, "outputs 2 and 3 must be off to change the combination mode")
        self._range(mode if self.internal() and mode in ("SER", "PAR") else "")
        if self._mirrors(mode):
            self._push(3, self._target(mode, self._voltage(2)))
        self.session.invalidate(electrical=True)

    # ---- output 2's range under an internal combination ----------------------------------------------

    def _range(self, combination: str) -> None:
        """Widen output 2's range for SER (2× voltage) or PAR (2× current); "" restores it."""
        if combination == self._widened:
            return
        owner = self.owner
        assert owner is not None
        self._restore_range()
        if combination:
            factor_v, factor_i = (2.0, 1.0) if combination == "SER" else (1.0, 2.0)
            v_max = float(owner.specs["ch2.v_max"].default) * factor_v
            i_max = float(owner.specs["ch2.i_max"].default) * factor_i
            p_max = float(owner.specs["ch2.p_max"].default) * 2.0
            for path, value in (("ch2.v_max", v_max), ("ch2.i_max", i_max), ("ch2.p_max", p_max)):
                owner.set_state(path, value, internal=True)
            voltage, current = owner.specs["ch2.voltage"], owner.specs["ch2.current"]
            owner.set_bounds("ch2.voltage", maximum=float(voltage.max or 0.0) * factor_v)
            owner.set_bounds("ch2.current", maximum=float(current.max or 0.0) * factor_i)
            ovp = owner.specs.get("ch2.ovp")
            if ovp is not None and ovp.max is not None and factor_v != 1.0:
                at_top = float(owner.get_state("ch2.ovp")) >= float(ovp.max) * (1 - 1e-12)
                owner.set_bounds("ch2.ovp", maximum=float(ovp.max) * factor_v)
                if at_top:  # an OVP left at its maximum follows the pair's maximum
                    owner.set_state("ch2.ovp", float(ovp.max) * factor_v, internal=True)
        self._widened = combination

    def _restore_range(self) -> None:
        owner = self.owner
        assert owner is not None
        if not self._widened:
            return
        self._widened = ""
        for path in ("ch2.voltage", "ch2.current", "ch2.ovp"):
            owner.restore_bounds(path)
        for path in ("ch2.voltage", "ch2.current", "ch2.ovp"):
            if path not in owner.specs:
                continue
            top = owner.specs[path].max
            if top is not None and float(owner.get_state(path)) > top:
                owner.set_state(path, top, internal=True)
        for path in ("ch2.v_max", "ch2.i_max", "ch2.p_max"):
            owner.set_state(path, owner.specs[path].default, internal=True)

    def reset(self) -> None:
        """*RST: the states are back at their defaults, so is output 2's range."""
        owner = self.owner
        assert owner is not None
        if self._widened:
            self._widened = ""
            for path in ("ch2.voltage", "ch2.current", "ch2.ovp"):
                owner.restore_bounds(path)

    def _push(self, channel: int, value: float) -> None:
        assert self.owner is not None
        if abs(self._voltage(channel) - value) < 1e-12:
            return
        self._mirroring = True
        try:
            self.owner.set_state(f"ch{channel}.voltage", value)
        finally:
            self._mirroring = False

    @op("track")
    def op_track(self, query: bool = False, enabled: bool | None = None, **_: Any) -> OpResult:
        """``OUTPut:TRACk``: on enters the track combination (and mirrors CH3), off leaves it."""
        mode_path = self.bound_paths().get("mode", "output.mode")
        if query:
            return OpResult(V.format_bool(self.mode() == "TRAC"))
        assert self.owner is not None
        if enabled is None:
            raise ScpiError(-109, "missing the tracking state")
        if enabled:
            self.owner.set_state(mode_path, "TRACk")
        elif self.mode() == "TRAC":
            self.owner.set_state(mode_path, "OFF")
        return OpResult()

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        key = bound.get(path)
        if key in ("mode", "track"):
            self._apply()
            return
        if not self._mirroring and self.idle(3) and key in ("voltage3", "current3", "output3"):
            raise ScpiError(CH3_COMBINED, "output 3 is part of the series/parallel pair")
        if self._mirroring or not self._mirrors(self.mode()):
            return
        if key == "voltage2":
            self._push(3, self._target(self.mode(), float(new)))
        elif key == "voltage3":
            if self.internal():
                master = float(new)
            else:
                master = abs(float(new)) if self.mode() == "TRAC" else -float(new)
            self._push(2, master)


__all__ = [
    "CH3_COMBINED",
    "OUTPUT_DISABLED",
    "PowerOutput",
    "PowerStage",
    "PowerSupplySetpoint",
    "SupplyProtection",
    "SupplyReadback",
    "SupplyTracking",
]
