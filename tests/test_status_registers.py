"""StatusRegisters: ESR/ESE/SRE and the status byte, without an instrument (#939)."""

from __future__ import annotations

from simubench.engine.status_registers import StatusRegisters


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


def test_ese_mask_sets_esb_only_when_an_enabled_esr_bit_is_set():
    s = StatusRegisters()
    s.set_opc()
    assert s.stb(errors=0, output=False) == 0
    s.ese = 1
    assert s.stb(errors=0, output=False) == 1 << 5


def test_sre_mask_sets_mss_but_never_from_itself():
    s = StatusRegisters()
    s.set_opc()
    s.ese = 1
    s.sre = 1 << 5
    assert s.stb(errors=0, output=False) == (1 << 5) | (1 << 6)
    s.sre = 0xFF ^ (1 << 5)  # bit 6 enabled does not raise MSS by itself
    assert s.stb(errors=0, output=False) == 1 << 5


def test_error_queue_and_output_set_eav_and_mav():
    s = StatusRegisters()
    assert s.stb(errors=2, output=False) == 1 << 2
    assert s.stb(errors=0, output=True) == 1 << 4


def test_status_register_summary_bit_joins_the_byte():
    s = StatusRegisters(lambda: [_Reg(0, True), _Reg(1, False), _Reg(None, True)])
    assert s.stb(errors=0, output=False) == 1


def test_clear_zeroes_esr_and_registers_but_keeps_enables():
    reg = _Reg(0)
    s = StatusRegisters(lambda: [reg])
    s.set_bit(3)
    s.ese, s.sre = 0x20, 0x30
    s.clear()
    assert s.esr == 0 and reg.cleared and (s.ese, s.sre) == (0x20, 0x30)


def test_read_esr_clears_it():
    s = StatusRegisters()
    s.set_bit(2)
    assert s.read_esr() == 4 and s.esr == 0
