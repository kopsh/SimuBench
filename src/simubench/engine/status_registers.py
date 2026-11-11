"""The IEEE 488.2 status registers of one instrument (#939): ESR, ESE, SRE and the status byte.

They live here, on their own: no session, no command layer, no clock. The instrument tells this
class what it cannot know (how many errors are queued, whether output waits to be read) and the
status-register components it owns, so the class is tested by calling it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

MSS = 1 << 6  # master summary status / request service (SRE never enables itself)
ESB = 1 << 5
MAV = 1 << 4
EAV = 1 << 2


class StatusRegisters:
    def __init__(self, components: Callable[[], Iterable[Any]] = lambda: ()) -> None:
        #: Provider of the instrument's components; the status registers are filtered out once.
        self._components = components
        self._registers: list[Any] | None = None
        self.esr = 0
        self.ese = 0
        self.sre = 0

    def registers(self) -> list[Any]:
        """The ``status_register`` components, in component order (fixed once the instrument is built)."""
        if self._registers is None:
            self._registers = [c for c in self._components() if c.type_name == "status_register"]
        return self._registers

    def sample(self) -> None:
        """Let every status register latch the condition transitions since its last look (#575):
        each command dispatch samples, so a condition that comes and goes between two reads of
        the event register is still latched (through PTR/NTR)."""
        for comp in self.registers():
            comp.latch_conditions()

    def event(self, name: str) -> None:
        """A momentary event (a trigger, a range change): every register with a bit of that name latches it."""
        for comp in self.registers():
            comp.event(name)

    def set_opc(self) -> None:
        self.esr |= 1

    def set_bit(self, bit: int) -> None:
        self.esr |= 1 << bit

    def read_esr(self) -> int:
        """``*ESR?``: the register, which the read clears."""
        value, self.esr = self.esr, 0
        return value

    def clear(self) -> None:
        """``*CLS``: the event registers; ESE and SRE keep their enables (IEEE 488.2)."""
        self.esr = 0
        for comp in self.registers():
            comp.clear()

    def stb(self, *, errors: int, output: bool, commit: bool = True) -> int:
        """The status byte. ``commit=False`` only looks: the condition transitions the status
        registers have not latched yet stay unlatched, so an observer cannot change what a later
        ``*ESR?`` says."""
        stb = 0
        for comp in self.registers():  # status registers' summary bits (#248, #273)
            bit = comp.param("summary_bit")
            if bit is not None and comp.summary(commit):
                stb |= 1 << int(bit)
        if errors:
            stb |= EAV
        if output:
            stb |= MAV
        if self.esr & self.ese:
            stb |= ESB
        if stb & self.sre & ~MSS:
            stb |= MSS
        return stb


class Register:
    """``Instrument.esr`` / ``ese`` / ``sre``: the instrument's attribute is the collaborator's."""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, inst: Any, owner: type | None = None) -> Any:
        return self if inst is None else getattr(inst.status, self.name)

    def __set__(self, inst: Any, value: int) -> None:
        setattr(inst.status, self.name, value)
