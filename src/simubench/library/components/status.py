"""A status register group (the cross-category block the component map tracks; #248, #273, #343).

One ``status_register`` is one register group of a real instrument's status system: named bits
with a condition (what is true now) and an event latch (what has happened since the last read),
an enable mask and an optional summary bit in the status byte (``*STB?``). The bits are fed two
ways, so a model only writes YAML:

- **conditions**: any component of the instrument may report level flags by name through
  :meth:`Component.status_flags` (an analyzer's ``UNCAL``, a load's ``UNR``, an acquisition's
  ``RUN``); a bit whose flag is true is set in the condition register and latched in the event
  register when read;
- **events**: a component pushes a momentary event by name with
  :meth:`Instrument.status_event` (a trigger occurred, a time constant changed); every register
  that names it latches it.

The event register latches pushed events and the rising edges of the conditions; reading it
clears it (the whole register, or with ``bit`` only that bit, the SRS form). The condition
register is not latched. ``enable`` with two numbers sets one bit of the
mask (``LIAE 2,1``), with one the whole mask.
"""

from __future__ import annotations

from typing import Any

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError


@component("status_register")
class StatusRegister(Component):
    """A status register group: named condition/event bits, an enable mask, a summary bit."""

    defaults = {
        "bits": {},  # flag name -> bit number, e.g. {TRG: 0, RUN: 3}
        "enable": 0,  # the enable mask (a setting such as $status.lia_enable)
        "summary_bit": None,  # the status-byte bit set while (event & enable) != 0 (None: none)
        "width": 16,  # bits in the register (8 for an SRS byte)
        # Settings whose change is an event of this register (#273), flag -> $setting, e.g.
        # {TC: $filter.time_constant, RANGE: $input.sensitivity}.
        "changes": {},
        # "esr": this register is the engine's standard event register (the bits *OPC and command
        # errors set, the *ESE mask), so a model can give it its own forms (*ESE i,j; *ESR? i).
        "mirror": "",
        "request_bit": None,  # a serial-poll register: this bit is set while (condition & enable) != 0
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._events = 0
        self._seen = 0  # the condition at the last look: events latch its rising edges
        self._busy = False  # computing a summary: other registers read none from this one

    def bound_paths(self) -> dict[str, str]:
        out = super().bound_paths()
        for flag, ref in dict(self._params.get("changes") or {}).items():
            if isinstance(ref, str) and ref.startswith("$"):
                out[f"change:{str(flag).upper()}"] = self.resolve_path(ref[1:])
        return out

    def on_state(self, path: str, old: Any, new: Any) -> None:
        for key, bound in self.bound_paths().items():
            if key.startswith("change:") and bound == path:
                self.event(key.split(":", 1)[1])

    def status_flags(self) -> dict[str, bool]:
        """This register's summary as a flag named after it (a serial-poll register's LIA bit); a
        channel's register is named with its channel, e.g. CH1.ISUM (#348)."""
        if self._busy or not self.name:
            return {}
        return {self.name.upper(): self.summary()}

    def _mirror(self) -> bool:
        return str(self.param("mirror")).lower() == "esr"

    def _bits(self) -> dict[str, int]:
        return {str(k).upper(): int(v) for k, v in dict(self.param("bits") or {}).items()}

    def condition(self) -> int:
        """The condition register: every bit whose flag a component reports true now, with the
        instrument's own MAV (output waiting), ESB (enabled standard events) and IFC (idle)."""
        assert self.owner is not None
        self._busy = True
        try:
            flags: dict[str, bool] = {
                "MAV": bool(self.owner._output),
                "ESB": bool(self.owner.esr & self.owner.ese),
                "IFC": True,
            }
            self.session._solve()
            for comp in self.owner.components.values():
                # A register of one channel (a channel component, e.g. a supply's ISUMmary<n>)
                # sees only that channel's flags (#348).
                if self.channel is not None and comp.channel != self.channel:
                    continue
                if comp is not self:
                    for name, on in comp.status_flags().items():
                        flags[name.upper()] = flags.get(name.upper(), False) or bool(on)
        finally:
            self._busy = False
        value = 0
        for name, bit in self._bits().items():
            if flags.get(name):
                value |= 1 << bit
        request = self.param("request_bit")
        if request is not None and value & int(self.param("enable") or 0) & ~(1 << int(request)):
            value |= 1 << int(request)
        return value

    def event(self, name: str) -> bool:
        """Latch a pushed event; True when this register has a bit for it."""
        bit = self._bits().get(name.upper())
        if bit is None:
            return False
        self._events |= 1 << bit
        return True

    def events(self) -> int:
        """The event register: pushed events and every condition bit that went true since the
        last look (a condition that stays true does not set it again once read)."""
        if self._mirror():
            assert self.owner is not None
            return int(self.owner.esr)
        now = self.condition()
        self._events |= now & ~self._seen
        self._seen = now
        return self._events

    def _enable(self) -> int:
        if self._mirror():
            assert self.owner is not None
            return int(self.owner.ese)
        return int(self.param("enable") or 0)

    def summary(self) -> bool:
        self._busy = True
        try:
            return bool(self.events() & self._enable())
        finally:
            self._busy = False

    def _clear_events(self, mask: int) -> None:
        if self._mirror():
            assert self.owner is not None
            self.owner.esr &= ~mask
        else:
            self._events &= ~mask

    def reset(self) -> None:
        self._events = 0
        self._seen = 0

    def clear(self) -> None:
        """``*CLS``: the event register empties (the enable mask stays)."""
        self._events = 0

    @staticmethod
    def _one(value: Any) -> Any:
        """A parameter as the op receives it: a raw parameter comes as a list of tokens."""
        if isinstance(value, list):
            return value[0] if value else None
        return value

    def _bit(self, bit: Any) -> int:
        b = int(V.parse_int(bit) if isinstance(bit, bytes) else bit)
        if not 0 <= b < int(self.param("width")):
            raise ScpiError(-222, f"bit {b} is outside the register")
        return b

    @op("event")
    def op_event(self, query: bool = False, bit: Any = None, **_: Any) -> OpResult:
        """The event register, read and cleared (with ``bit``: that bit, and only it cleared)."""
        bit = self._one(bit)
        value = self.events()
        if bit is None:
            self._clear_events(-1)
            return OpResult(V.format_int(value))
        b = self._bit(bit)
        self._clear_events(1 << b)
        return OpResult(V.format_int((value >> b) & 1))

    @op("condition")
    def op_condition(self, query: bool = False, bit: Any = None, **_: Any) -> OpResult:
        bit = self._one(bit)
        value = self.condition()
        if bit is None:
            return OpResult(V.format_int(value))
        return OpResult(V.format_int((value >> self._bit(bit)) & 1))

    @op("enable")
    def op_enable(self, query: bool = False, bit: Any = None, value: Any = None, **_: Any) -> OpResult:
        """The enable mask: ``n`` sets it, ``i,j`` sets bit i to j; a query reads it, ``? i`` bit i."""
        assert self.owner is not None
        bit, value = self._one(bit), self._one(value)
        path = self.bound_paths().get("enable")
        mask = self._enable()
        if query:
            if bit is None:
                return OpResult(V.format_int(mask))
            return OpResult(V.format_int((mask >> self._bit(bit)) & 1))
        if bit is None:
            raise ScpiError(-109, "missing the mask")
        if value is None:
            new = int(V.parse_int(bit) if isinstance(bit, bytes) else bit)
        else:
            b = self._bit(bit)
            on = int(V.parse_int(value) if isinstance(value, bytes) else value)
            new = mask | (1 << b) if on else mask & ~(1 << b)
        if self._mirror():
            self.owner.ese = new & ((1 << int(self.param("width"))) - 1)
            return OpResult()
        if path is None:
            raise ScpiError(-221, "the enable mask is not a setting of this definition")
        self.owner.set_state(path, new)
        return OpResult()

    @op("preset")
    def op_preset(self, **_: Any) -> OpResult:
        """``STATus:PRESet``: the enable mask of every status register of the instrument (each
        channel's too) back to 0; the events stay (SCPI PRESet, #348)."""
        assert self.owner is not None
        for comp in self.owner.components.values():
            if comp.type_name != "status_register" or comp._params.get("mirror"):
                continue
            path = comp.bound_paths().get("enable")
            if path is not None:
                self.owner.set_state(path, 0)
        return OpResult()


__all__ = ["StatusRegister"]
