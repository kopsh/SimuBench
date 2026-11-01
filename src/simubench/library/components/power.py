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
    }
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
        "loop": "",  # this channel's output_control_loop (the reused B1 loop)
        "stage": "",  # this channel's power_stage (the envelope)
        "protection": "",  # this channel's supply_protection (the trip latch)
    }
    piecewise = True
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._closed = False  # the relay's state (what the circuit sees)
        self._relay_pending = False  # a close is scheduled

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

    def conducting(self) -> bool:
        return self._closed and not self._protection().tripped()

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
        if self._closed:
            self._closed = False
            self.session.invalidate(electrical=True)

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        key = bound.get(path)
        if key == "output":
            if new:
                if self._protection().tripped():
                    raise ScpiError(OUTPUT_DISABLED, "the output is disabled by a protection fault")
                self._open()
                assert self.owner is not None
                when = self.owner.now_ns() + round(float(self.param("relay_s")) * 1e9)
                self.owner.register_pending(self.relay_key, when, self._close)
                self._relay_pending = True
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
        if loop.mode == "V":
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
            return False
        loop = self._loop()
        v = self._metered(dc_voltage)
        current = self.session.source_current(self.uid) if loop.mode == "V" else 0.0
        changed = loop.update_mode(v, current)
        i = current if loop.mode == "V" else loop.targets()[1]
        protection = self._protection()
        if not protection.tripped() and protection.check(v, i):
            protection.trip(v, i)
            changed = True
        return changed

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
        if loop.mode == "V":
            r = max(float(self.param("r_out")), 1e-9)
            i = (self.pin_signal("a").mean() - self.pin_signal("pos").mean()) / r
        else:
            i = loop.targets()[1]
        return v, abs(i)

    def regulation(self) -> str:
        if not self.conducting():
            return "OFF"
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
        return OpResult(V.format_float(value, 7))

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
    }
    units = {"ovp": "V", "ocp": "A"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tripped = ""  # "OVP" | "OCP" | ""
        self._restore = False  # was the output on when the fault came?
        self._disabling = False

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

    def _disable_output(self) -> None:
        if self.owner is None or not self._restore:
            return
        self._disabling = True
        try:
            self.owner.set_state(self._output_path(), False)
        finally:
            self._disabling = False

    def clear(self) -> None:
        restore = self._restore
        self._tripped = ""
        self._restore = False
        if restore and self.owner is not None:
            self.owner.set_state(self._output_path(), True)

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = {v: k for k, v in self.bound_paths().items()}
        if bound.get(path) == "output" and new and self._tripped and not self._disabling:
            raise ScpiError(OUTPUT_DISABLED, "the output is disabled by a protection fault")

    def reset(self) -> None:
        self._tripped = ""
        self._restore = False

    @op("tripped")
    def op_tripped(self, query: bool = False, **_: Any) -> OpResult:
        self.session._solve()
        return OpResult(V.format_bool(self.tripped()))

    @op("kind")
    def op_kind(self, query: bool = False, **_: Any) -> OpResult:
        self.session._solve()
        return OpResult(V.format_string(self.kind()))

    @op("clear")
    def op_clear(self, **_: Any) -> OpResult:
        self.clear()
        return OpResult()

    def readings(self) -> dict[str, Any]:
        return {"values": {"tripped": self._tripped or "—"}}


@component("supply_tracking")
class SupplyTracking(Component):
    """The ±25 V outputs' combination (B2 #185): track, parallel or series (channels 2 and 3 only).

    ``mode`` is the instrument's combination state (OFF / TRAC / PAR / SER). Enabling a combination
    requires both outputs off (the manual's constraint); afterwards a change of either programmed
    voltage is mirrored to the other — track keeps ``CH3 = −|CH2|``, parallel ``CH3 = CH2`` and
    series ``CH3 = −CH2``. The physical connection is the bench wiring: this component keeps the
    programmed levels consistent, which is what the instrument does.
    """

    defaults = {
        "mode": "OFF",  # OFF | TRAC | PAR | SER (the instrument's combination state)
        "voltage2": 0.0,  # CH2's programmed voltage (watched)
        "voltage3": 0.0,  # CH3's programmed voltage (watched)
        "output2": False,  # CH2's output state (must be off to change the combination)
        "output3": False,  # CH3's output state
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mirroring = False

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
        return self._MODES.get(str(self.param("mode")).upper(), "OFF")

    def _voltage(self, channel: int) -> float:
        assert self.owner is not None
        return float(self.owner.get_state(f"ch{channel}.voltage"))

    def _target(self, mode: str, master: float) -> float:
        # Both combined modes put CH3 at the negative level: the series connection stacks the two
        # outputs, the parallel connection crosses the negative output's terminals (the two then
        # push the same polarity into the load).
        if mode == "TRAC":
            return -abs(master)
        return -master  # SER and PAR

    def _apply(self) -> None:
        mode = self.mode()
        if mode == "OFF":
            return
        if bool(self.param("output2")) or bool(self.param("output3")):
            raise ScpiError(-221, "outputs 2 and 3 must be off to change the combination mode")
        self._push(3, self._target(mode, self._voltage(2)))

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
        if key == "mode":
            self._apply()
            return
        if self._mirroring or self.mode() == "OFF":
            return
        if key == "voltage2":
            self._push(3, self._target(self.mode(), float(new)))
        elif key == "voltage3":
            master = abs(float(new)) if self.mode() == "TRAC" else -float(new)
            self._push(2, master)


__all__ = [
    "OUTPUT_DISABLED",
    "PowerOutput",
    "PowerStage",
    "PowerSupplySetpoint",
    "SupplyProtection",
    "SupplyReadback",
    "SupplyTracking",
]
