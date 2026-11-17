"""StatusRegisters: ESR/ESE/SRE and the status byte, without an instrument (#939).

The registers' state is private to the collaborator (#1380): the tests read and write it the way
the instrument does, through the ``Register`` descriptors the façade bridges with.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from simubench.engine.status_registers import Register, StatusRegisters


class _Reg:
    type_name = "status_register"

    def __init__(self, summary_bit: int | None, on: bool = False) -> None:
        self.bit, self.on, self.cleared = summary_bit, on, False

    def param(self, key: str):
        return self.bit

    def summary(self, commit: bool) -> bool:
        return self.on

    def clear(self) -> None:
        self.cleared = True


class _Host:
    """The façade bridge, without the instrument: ``esr``/``ese``/``sre`` answer through it."""

    esr = Register()
    ese = Register()
    sre = Register()

    def __init__(self, components: Iterable[Any] = ()) -> None:
        self.status = StatusRegisters(lambda: components)


def _host(components: Iterable[Any] = ()) -> _Host:
    return _Host(components)


def test_ese_mask_sets_esb_only_when_an_enabled_esr_bit_is_set():
    h = _host()
    h.status.set_opc()
    assert h.status.stb(errors=0, output=False) == 0
    h.ese = 1
    assert h.status.stb(errors=0, output=False) == 1 << 5


def test_sre_mask_sets_mss_but_never_from_itself():
    h = _host()
    h.status.set_opc()
    h.ese = 1
    h.sre = 1 << 5
    assert h.status.stb(errors=0, output=False) == (1 << 5) | (1 << 6)
    h.sre = 0xFF ^ (1 << 5)  # bit 6 enabled does not raise MSS by itself
    assert h.status.stb(errors=0, output=False) == 1 << 5


def test_error_queue_and_output_set_eav_and_mav():
    h = _host()
    assert h.status.stb(errors=2, output=False) == 1 << 2
    assert h.status.stb(errors=0, output=True) == 1 << 4


def test_status_register_summary_bit_joins_the_byte():
    h = _host([_Reg(0, True), _Reg(1, False), _Reg(None, True)])
    assert h.status.stb(errors=0, output=False) == 1


def test_clear_zeroes_esr_and_registers_but_keeps_enables():
    reg = _Reg(0)
    h = _host([reg])
    h.status.set_bit(3)
    h.ese, h.sre = 0x20, 0x30
    h.status.clear()
    assert h.esr == 0 and reg.cleared and (h.ese, h.sre) == (0x20, 0x30)


def test_read_esr_clears_it():
    h = _host()
    h.status.set_bit(2)
    assert h.status.read_esr() == 4 and h.esr == 0
