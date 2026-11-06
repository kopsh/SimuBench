"""The virtual GPIB bus (D63): addressing, the 488.1 commands, REN/IFC/ATN, SRQ and serial poll.

The devices are the two instruments of the 5730A bench: ``cal`` at GPIB0::6 and ``dmm`` at GPIB0::7.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pyvisa import constants

from simubench.access.bus import bus_of
from simubench.access.gpib_bus import (
    CONTROLLER,
    DCL,
    GET,
    GTL,
    LLO,
    MLA,
    MTA,
    SDC,
    SPD,
    SPE,
    UNL,
    UNT,
    GpibAddress,
    GpibBus,
    GpibError,
    boards_of,
    gpib_board,
)
from simubench.api import open_bench

BENCH = Path(__file__).resolve().parents[1] / "examples" / "benches" / "fluke-5730a.bench.yaml"
ops = constants.RENLineOperation


@pytest.fixture
def bus():
    session = open_bench(BENCH)
    yield gpib_board(session, 0)
    session.close()


def queue_reply(bus: GpibBus, name: str) -> None:
    bus.session.write(name, b"*IDN?", client="test")


def has_reply(bus: GpibBus, name: str) -> bool:
    return bus.session.instrument(name).stb() & 0x10 != 0  # MAV


# ---- who is on the bus ------------------------------------------------------------------------------


def test_the_devices_are_the_instruments_with_gpib_addresses(bus):
    assert bus.devices == {GpibAddress(6): "cal", GpibAddress(7): "dmm"}
    assert bus.controller_pad == 0
    assert boards_of(bus.session) == [0]
    assert gpib_board(bus.session, 0) is bus  # every access path of a session shares the board


def test_two_devices_cannot_share_an_address(tmp_path):
    text = BENCH.read_text(encoding="utf-8").replace("GPIB0::7::INSTR", "GPIB0::6::INSTR")
    twin = tmp_path / "twin.bench.yaml"
    twin.write_text(text, encoding="utf-8")
    session = open_bench(twin)
    try:
        with pytest.raises(ValueError, match="cannot share a GPIB address"):
            gpib_board(session, 0)
    finally:
        session.close()


def test_the_board_moves_aside_for_a_device_at_address_0(tmp_path):
    text = BENCH.read_text(encoding="utf-8").replace("GPIB0::6::INSTR", "GPIB0::0::INSTR")
    other = tmp_path / "zero.bench.yaml"
    other.write_text(text, encoding="utf-8")
    session = open_bench(other)
    try:
        assert gpib_board(session, 0).controller_pad == 30
    finally:
        session.close()


# ---- addressing ------------------------------------------------------------------------------------------


def test_listen_and_talk_addresses_set_who_is_addressed(bus):
    bus.command(bytes([UNL, MLA | 6, MLA | 7, MTA | 0]))
    assert bus.listeners == ["cal", "dmm"] and bus.talker == CONTROLLER  # several listeners at once
    assert bus.address_state("cal") == constants.AddressState.listenr
    bus.command(bytes([UNL, MTA | 7]))
    assert bus.listeners == [] and bus.talker == "dmm"
    assert bus.address_state("dmm") == constants.AddressState.talker
    bus.command(bytes([UNT]))
    assert bus.talker is None and bus.address_state("dmm") == constants.AddressState.unaddressed


def test_a_new_talker_unaddresses_the_old_one(bus):
    bus.command(bytes([MTA | 6]))
    bus.command(bytes([MTA | 7]))
    assert bus.talker == "dmm"


def test_an_address_nobody_has_is_not_an_error(bus):
    bus.command(bytes([UNL, MLA | 12]))
    assert bus.listeners == []


def test_secondary_addresses_are_needed_for_a_device_that_has_one(tmp_path):
    text = BENCH.read_text(encoding="utf-8").replace("GPIB0::7::INSTR", "GPIB0::7::3::INSTR")
    f = tmp_path / "sad.bench.yaml"
    f.write_text(text, encoding="utf-8")
    session = open_bench(f)
    try:
        b = gpib_board(session, 0)
        assert b.devices[GpibAddress(7, 3)] == "dmm"
        b.command(bytes([UNL, MLA | 7]))
        assert b.listeners == []  # the primary address alone is not the device with a secondary
        b.command(bytes([UNL, MLA | 7, 0x60 | 3]))
        assert b.listeners == ["dmm"]
        b.command(bytes([UNL, MLA | 7, 0x60 | 4]))  # another secondary: nobody
        assert b.listeners == []
    finally:
        session.close()


# ---- data ------------------------------------------------------------------------------------------------------


def test_data_goes_to_every_listener_and_none_is_an_error(bus):
    with pytest.raises(GpibError) as err:
        bus.deliver(b"*IDN?")
    assert err.value.code == constants.VI_ERROR_NLISTENERS
    bus.command(bytes([UNL, MLA | 6, MLA | 7]))
    assert bus.deliver(b"*SRE 8") == ["cal", "dmm"]
    assert bus.session.instrument("cal").sre == bus.session.instrument("dmm").sre == 8


def test_the_talker_is_read_from_and_nobody_talking_is_a_timeout(bus):
    with pytest.raises(GpibError) as err:
        bus.take(None)
    assert err.value.code == constants.VI_ERROR_TMO
    bus.write("cal", b"*IDN?")
    bus.address_for_read("cal")
    assert bus.talker == "cal" and bus.listeners == [CONTROLLER]
    assert bus.take(None).startswith(b"FLUKE,5730A")


# ---- the commands ------------------------------------------------------------------------------------------


def test_sdc_clears_only_the_addressed_listeners(bus):
    queue_reply(bus, "cal")
    queue_reply(bus, "dmm")
    bus.command(bytes([UNL, MLA | 6, SDC]))
    assert not has_reply(bus, "cal") and has_reply(bus, "dmm")


def test_dcl_clears_every_device(bus):
    queue_reply(bus, "cal")
    queue_reply(bus, "dmm")
    bus.command(bytes([DCL]))
    assert not has_reply(bus, "cal") and not has_reply(bus, "dmm")


def test_get_triggers_the_addressed_devices_only(bus):
    bus.command(bytes([UNL, MLA | 7, GET]))
    triggers = [e for e in bus.session.history if e["kind"] == "scpi" and e.get("data") == b"*TRG"]
    assert [e["instrument"] for e in triggers] == ["dmm"]


def test_llo_and_gtl_follow_the_remote_local_states(bus):
    cal, dmm = (bus_of(bus.session, n).remote_local for n in ("cal", "dmm"))
    bus.set_ren(ops.asrt)
    bus.command(bytes([UNL, MLA | 6]))  # addressed to listen with REN: remote
    assert cal.state == "remote" and dmm.state == "local"
    bus.command(bytes([LLO]))
    assert cal.state == "remote_lockout" and dmm.state == "local_lockout"  # universal: everyone
    bus.command(bytes([UNL, MLA | 6, GTL]))
    assert cal.state == "local_lockout" and dmm.state == "local_lockout"  # a lockout stays after GTL
    bus.set_ren(ops.deassert)
    assert cal.state == dmm.state == "local"  # REN false returns every device to local, lockout gone


def test_remote_needs_ren(bus):
    bus.command(bytes([UNL, MLA | 6]))
    assert bus_of(bus.session, "cal").remote_local.state == "local"  # addressed, but REN is false


def test_control_ren_modes_from_a_device_session(bus):
    cal = bus_of(bus.session, "cal").remote_local
    bus.control_ren("cal", ops.asrt_address)
    assert bus.ren and cal.state == "remote"
    bus.control_ren("cal", ops.address_gtl)
    assert cal.state == "local" and bus.ren  # GTL leaves REN alone
    bus.control_ren("cal", ops.asrt_address_llo)
    assert cal.state == "remote_lockout"
    bus.control_ren("cal", ops.deassert_gtl)
    assert cal.state == "local" and not bus.ren


def test_ifc_unaddresses_the_bus_but_leaves_the_devices_alone(bus):
    queue_reply(bus, "cal")
    bus.command(bytes([UNL, MLA | 6, MTA | 7, SPE]))
    bus.send_ifc()
    assert bus.listeners == [] and bus.talker is None and not bus.spe
    assert has_reply(bus, "cal")  # interface clear is not a device clear
    assert [e["action"] for e in bus.session.history if e["kind"] == "bus_control"][-1] == "GPIB0: IFC"


def test_atn_is_a_line_the_controller_sets(bus):
    assert not bus.atn
    bus.set_atn(constants.ATNLineOperation.asrt)
    assert bus.atn
    bus.set_atn(constants.ATNLineOperation.deassert)
    assert not bus.atn


# ---- instrument sessions as VISA does them --------------------------------------------------------------


def test_visa_clear_is_sdc_and_trigger_is_get(bus):
    queue_reply(bus, "cal")
    bus.device_clear("cal")
    assert not has_reply(bus, "cal")
    bus.trigger("dmm")
    assert any(
        e.get("data") == b"*TRG" and e["instrument"] == "dmm"
        for e in bus.session.history
        if e["kind"] == "scpi"
    )


# ---- SRQ and serial poll ---------------------------------------------------------------------------------


def raise_request(bus: GpibBus, name: str) -> None:
    for command in (b"*CLS", b"*ESE 1", b"*SRE 32", b"*OPC"):
        bus.session.write(name, command, client="test")
    bus.session.read(name, client="test")  # nothing queued: *OPC (no question mark) only sets the bit


def test_a_service_request_is_one_new_reason_cleared_by_the_serial_poll(bus):
    assert not bus.srq
    raise_request(bus, "cal")
    assert bus.srq  # the SRQ line is the OR of the devices' latches
    first = bus.read_stb("cal")
    assert first == 0x60  # ESB and RQS: the status byte as a serial poll returns it
    assert not bus.srq  # the poll cleared RQS
    again = bus.read_stb("cal")
    assert again == 0x20  # the reason (ESB) is still there, the request is not
    assert not bus.srq


def test_a_new_reason_raises_the_line_again(bus):
    raise_request(bus, "cal")
    bus.read_stb("cal")
    assert not bus.srq
    bus.session.write("cal", b"*ESR?", client="test")  # reading ESR clears the reason ...
    bus.session.read("cal", client="test")
    bus.session.write("cal", b"*OPC", client="test")  # ... and OPC is a new one
    assert bus.srq


def test_the_srq_line_is_shared_by_the_devices(bus):
    raise_request(bus, "dmm")
    assert bus.srq
    assert bus.read_stb("cal") & 0x40 == 0  # polling the other device does not clear this one
    assert bus.srq
    assert bus.read_stb("dmm") & 0x40
    assert not bus.srq


def test_a_serial_poll_leaves_the_bus_idle(bus):
    raise_request(bus, "cal")
    bus.read_stb("cal")
    assert not bus.spe and bus.talker is None  # SPD and UNT closed it
    assert bytes([SPE]) and bytes([SPD])  # the codes used are the 488.1 ones
