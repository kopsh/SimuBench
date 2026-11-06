"""The virtual USBTMC / USB488 interface (D63), checked against the USBTMC 1.00 and USB488 1.00
specifications: request codes, USBTMC_status values, response layouts and the tables they come from."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from pyvisa import constants

from simubench.access import usbtmc as u
from simubench.access.bus import bus_of
from simubench.api import open_bench

BENCH = Path(__file__).resolve().parents[1] / "examples" / "benches" / "bk-8614.bench.yaml"
ops = constants.RENLineOperation


@pytest.fixture
def usb():
    session = open_bench(BENCH)
    yield u.usb_interface(session, "load")
    session.close()


def req(usb, request, value=0, length=None, *, request_type=u.CLASS_INTERFACE, index=0):
    """A control request with the setup packet of the request's table."""
    return usb.control_request(request_type, request, value, index, length or u.RESPONSE_SIZE.get(request, 0))


def abort_req(usb, request, value=0, endpoint=u.BULK_OUT):
    return usb.control_request(u.CLASS_ENDPOINT, request, value, endpoint, u.RESPONSE_SIZE[request])


def raise_srq(usb):
    for command in (b"*CLS", b"*ESE 1", b"*SRE 32", b"*OPC"):
        usb.session.write("load", command, client="test")


# ---- GET_CAPABILITIES (USBTMC table 37, USB488 table 8) --------------------------------------------------


def test_get_capabilities_has_the_24_byte_layout_of_the_tables(usb):
    data = req(usb, u.GET_CAPABILITIES)
    assert len(data) == 24 and data[0] == u.STATUS_SUCCESS and data[1] == 0
    assert struct.unpack("<H", data[2:4])[0] == 0x0100  # bcdUSBTMC
    assert data[4] & 0b11 == 0  # a 488.2 interface is neither talk-only nor listen-only
    assert data[4] & 0b100  # accepts INDICATOR_PULSE
    assert data[5] & 1  # ends a Bulk-IN transfer at a TermChar
    assert data[6:12] == bytes(6)  # reserved for USBTMC
    assert struct.unpack("<H", data[12:14])[0] == 0x0100  # bcdUSB488
    assert data[14] == 0b111  # 488.2, REN/GTL/LLO, TRIGGER
    assert data[15] & 0b111 == 0b111 and not data[15] & 0b1000  # SR1, RL1, DT1; SCPI not claimed
    assert data[16:] == bytes(8)


def test_the_capability_rules_of_usb488_hold(usb):
    data = req(usb, u.GET_CAPABILITIES)
    dev, iface = data[15], data[14]
    assert not dev & 1 or iface & 1  # DT1 needs the TRIGGER capability of the interface (rule 1)
    assert not dev & 2 or iface & 2  # RL1 needs REN/GTL/LLO (rule 2)
    assert not iface & 4 or dev & 4  # a 488.2 interface is SR1 (rule 3)
    assert not dev & 8 or (dev & 4 and iface & 4)  # SCPI needs SR1 and 488.2 (rule 4)


def test_the_response_is_cut_to_wlength(usb):
    assert len(req(usb, u.GET_CAPABILITIES, length=8)) == 8


# ---- INDICATOR_PULSE ----------------------------------------------------------------------------------------


def test_indicator_pulse_succeeds_and_is_recorded(usb):
    assert req(usb, u.INDICATOR_PULSE) == bytes([u.STATUS_SUCCESS])
    assert [e["action"] for e in usb.session.history if e["kind"] == "bus_control"][
        -1
    ] == "USB: INDICATOR_PULSE"


# ---- requests that are not defined ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("request_type", "b_request", "index"),
    [
        (0xA1, 0, 0),  # reserved bRequest
        (0xA1, 8, 0),  # reserved
        (0xA1, 200, 0),  # VISA reserved range
        (0xC0, u.GET_CAPABILITIES, 0),  # not a class request
        (u.CLASS_ENDPOINT, u.GET_CAPABILITIES, 0),  # wrong recipient
        (u.CLASS_INTERFACE, u.INITIATE_ABORT_BULK_OUT, 0),  # abort goes to an endpoint
        (u.CLASS_INTERFACE, u.GET_CAPABILITIES, 1),  # no interface 1
    ],
)
def test_undefined_requests_stall(usb, request_type, b_request, index):
    with pytest.raises(u.UsbStall):
        usb.control_request(request_type, b_request, 0, index, 8)


def test_an_abort_request_for_the_wrong_endpoint_stalls(usb):
    with pytest.raises(u.UsbStall):
        usb.control_request(u.CLASS_ENDPOINT, u.INITIATE_ABORT_BULK_OUT, 1, u.BULK_IN, 2)


# ---- READ_STATUS_BYTE and the Interrupt-IN endpoint (USB488 4.3.1, 3.4) -------------------------------


def test_read_status_byte_answers_on_control_and_interrupt_in(usb):
    reply = req(usb, u.READ_STATUS_BYTE, value=5)
    assert reply == bytes([u.STATUS_SUCCESS, 5, 0])  # the byte itself is on Interrupt-IN (table 13)
    assert list(usb.interrupt) == [bytes([0x80 | 5, 0])]  # D7 = 1, the tag of the request, the byte


@pytest.mark.parametrize("value", [0, 1, 0x80 | 5, 0x1005])
def test_read_status_byte_needs_a_tag_from_2_to_127_and_d7_clear(usb, value):
    assert req(usb, u.READ_STATUS_BYTE, value=value)[0] == u.STATUS_FAILED
    assert not usb.interrupt


def test_read_status_byte_says_when_interrupt_in_is_full(usb):
    for tag in range(2, 2 + u.INTERRUPT_FIFO):
        assert req(usb, u.READ_STATUS_BYTE, value=tag)[0] == u.STATUS_SUCCESS
    assert req(usb, u.READ_STATUS_BYTE, value=100)[0] == u.STATUS_INTERRUPT_IN_BUSY


def test_a_service_request_goes_out_on_interrupt_in_with_tag_1(usb):
    assert usb.take_srq() is None
    raise_srq(usb)
    assert usb.srq_pending()
    packet = usb.interrupt[0]
    assert packet[0] == 0x81  # D7 = 1 and bTag = 1 (table 6)
    assert usb.take_srq() == 0x60  # the status byte with RQS, as a serial poll gives it
    assert usb.take_srq() is None and not usb.srq_pending()  # RQS was cleared when it was queued


def test_visa_read_stb_waits_for_its_own_tag(usb):
    raise_srq(usb)
    usb.srq_pending()  # the SRQ packet is queued first
    stb = usb.read_stb()
    assert stb & 0x40 == 0 and stb & 0x20  # RQS already went out with the interrupt
    assert any(p[0] == 0x81 for p in usb.interrupt)  # and that packet is still there to be taken


def test_mav_is_set_while_a_response_waits(usb):
    usb.write(b"*IDN?")
    assert usb.read_stb() & 0x10  # MAV before any REQUEST_DEV_DEP_MSG_IN (USB488 4.3.1.3)
    usb.read(1000, None)
    assert usb.read_stb() & 0x10 == 0


# ---- bulk transfers ----------------------------------------------------------------------------------------------


def test_a_message_is_written_and_read_with_its_tags(usb):
    usb.write(b"*IDN?")
    assert usb.out_tag == 1
    usb.request_msg_in(1000, None)
    assert usb.in_tag == 2
    data, eom = usb.bulk_in()
    assert data.startswith(b"B&K PRECISION, 8614") and eom


def test_bulk_in_obeys_the_size_and_the_term_char(usb):
    usb.write(b"*IDN?")
    first, eom = usb.read(6, None)
    assert first == b"B&K PR" and not eom  # the response continues in the next transfer
    rest, eom = usb.read(1000, ord(","))
    assert rest.endswith(b",") and not eom  # ended at the TermChar, the message is not over
    tail, eom = usb.read(1000, None)
    assert eom and tail.endswith(b"\n")


def test_the_device_with_nothing_to_send_makes_the_host_wait(usb):
    usb.request_msg_in(100, None)
    with pytest.raises(u.UsbError) as err:
        usb.bulk_in()
    assert err.value.code == constants.VI_ERROR_TMO


def test_a_message_sent_in_parts_runs_when_its_eom_comes(usb):
    usb.bulk_out(b"*ID", eom=False)
    assert usb.out_in_progress
    usb.bulk_out(b"N?", eom=True)
    assert not usb.out_in_progress and usb.session.instrument("load").stb() & 0x10


def test_trigger_is_a_bulk_out_message(usb):
    usb.trigger()
    assert any(e.get("data") == b"*TRG" for e in usb.session.history if e["kind"] == "scpi")
    assert usb.out_tag == 1


def test_bulk_in_without_a_request_is_an_error(usb):
    with pytest.raises(u.UsbError):
        usb.bulk_in()


# ---- INITIATE_ABORT_BULK_OUT / CHECK_ABORT_BULK_OUT_STATUS (tables 18-23) -------------------------------


def test_abort_bulk_out_of_a_transfer_in_progress(usb):
    usb.bulk_out(b"*ID", eom=False)
    tag = usb.out_tag
    assert abort_req(usb, u.INITIATE_ABORT_BULK_OUT, tag) == bytes([u.STATUS_SUCCESS, tag])
    assert u.BULK_OUT in usb.halted  # the device halts the endpoint before it answers
    with pytest.raises(u.UsbError):
        usb.bulk_out(b"x")
    check = abort_req(usb, u.CHECK_ABORT_BULK_OUT_STATUS)
    assert check[0] == u.STATUS_SUCCESS and check[1:4] == bytes(3)
    assert struct.unpack("<I", check[4:8])[0] == 3  # NBYTES_RXD: what it had received
    assert not usb.out_partial  # and flushed the rest
    usb.control_request(u.STD_ENDPOINT_OUT, u.STD_CLEAR_FEATURE, u.FEATURE_ENDPOINT_HALT, u.BULK_OUT, 0)
    assert usb.bulk_out(b"*CLS") == 4  # the host cleared the halt: the endpoint works again


def test_abort_bulk_out_with_the_wrong_tag_or_no_transfer(usb):
    assert abort_req(usb, u.INITIATE_ABORT_BULK_OUT, 1) == bytes([u.STATUS_FAILED, 0])  # none, FIFO empty
    usb.bulk_out(b"*ID", eom=False)
    wrong = (usb.out_tag % 255) + 1
    reply = abort_req(usb, u.INITIATE_ABORT_BULK_OUT, wrong)
    assert reply == bytes([u.STATUS_TRANSFER_NOT_IN_PROGRESS, usb.out_tag])
    assert u.BULK_OUT not in usb.halted  # the device must not halt the endpoint then


def test_check_abort_without_an_initiate(usb):
    check = abort_req(usb, u.CHECK_ABORT_BULK_OUT_STATUS)
    assert check == bytes([u.STATUS_SPLIT_NOT_IN_PROGRESS]) + bytes(7)


# ---- INITIATE_ABORT_BULK_IN / CHECK_ABORT_BULK_IN_STATUS (tables 24-29) ----------------------------------


def test_abort_bulk_in_of_a_transfer_in_progress(usb):
    usb.write(b"*IDN?")
    usb.request_msg_in(1000, None)
    tag = usb.in_tag
    assert abort_req(usb, u.INITIATE_ABORT_BULK_IN, tag, u.BULK_IN) == bytes([u.STATUS_SUCCESS, tag])
    check = abort_req(usb, u.CHECK_ABORT_BULK_IN_STATUS, endpoint=u.BULK_IN)
    assert check[0] == u.STATUS_SUCCESS and check[1] == 0  # bmAbortBulkIn D0: the FIFO is empty
    assert struct.unpack("<I", check[4:8])[0] == 0  # NBYTES_TXD: nothing had been read yet
    assert not usb.response  # the rest of the response is gone
    with pytest.raises(u.UsbError):
        usb.bulk_in()  # the transfer is over


def test_abort_bulk_in_with_the_wrong_tag_or_no_transfer(usb):
    assert abort_req(usb, u.INITIATE_ABORT_BULK_IN, 1, u.BULK_IN)[0] == u.STATUS_FAILED
    usb.write(b"*IDN?")
    usb.request_msg_in(1000, None)
    wrong = (usb.in_tag % 255) + 1
    assert abort_req(usb, u.INITIATE_ABORT_BULK_IN, wrong, u.BULK_IN)[0] == u.STATUS_TRANSFER_NOT_IN_PROGRESS
    assert usb.in_active  # the transfer goes on


# ---- INITIATE_CLEAR / CHECK_CLEAR_STATUS (tables 30-35) ------------------------------------------------------


def test_initiate_clear_halts_bulk_out_and_empties_every_buffer(usb):
    usb.write(b"*IDN?")
    usb.bulk_out(b"*ID", eom=False)
    assert req(usb, u.INITIATE_CLEAR) == bytes([u.STATUS_SUCCESS])
    assert u.BULK_OUT in usb.halted
    assert not usb.out_partial and not usb.response
    assert usb.session.instrument("load").stb() & 0x10 == 0  # the output queue is empty
    assert req(usb, u.CHECK_CLEAR_STATUS) == bytes([u.STATUS_SUCCESS, 0])  # bmClear D0 = 0
    assert u.BULK_OUT in usb.halted  # lifted only by the host's CLEAR_FEATURE


def test_check_clear_status_without_an_initiate(usb):
    assert req(usb, u.CHECK_CLEAR_STATUS) == bytes([u.STATUS_SPLIT_NOT_IN_PROGRESS, 0])


def test_another_class_request_during_an_initiate_is_split_in_progress(usb):
    req(usb, u.INITIATE_CLEAR)
    reply = req(usb, u.GET_CAPABILITIES)
    assert reply == bytes([u.STATUS_SPLIT_IN_PROGRESS]) + bytes(23)  # padded to the response length
    assert req(usb, u.CHECK_CLEAR_STATUS)[0] == u.STATUS_SUCCESS  # the INITIATE went on
    assert req(usb, u.GET_CAPABILITIES)[0] == u.STATUS_SUCCESS


def test_visa_clear_runs_the_whole_transaction(usb):
    usb.write(b"*IDN?")
    usb.clear()
    assert u.BULK_OUT not in usb.halted and usb.split is None
    assert usb.write(b"*CLS") == 4  # usable again straight away


# ---- standard requests on endpoints ----------------------------------------------------------------------------


def test_get_status_of_a_halted_endpoint(usb):
    usb.halted.add(u.BULK_IN)
    assert usb.control_request(u.STD_ENDPOINT_IN, u.STD_GET_STATUS, 0, u.BULK_IN, 2) == b"\x01\x00"
    assert usb.control_request(u.STD_ENDPOINT_IN, u.STD_GET_STATUS, 0, u.BULK_OUT, 2) == b"\x00\x00"
    with pytest.raises(u.UsbStall):
        usb.control_request(u.STD_ENDPOINT_IN, u.STD_GET_STATUS, 0, 0x7F, 2)  # no such endpoint


def test_a_halted_bulk_in_endpoint_stalls_reads(usb):
    usb.write(b"*IDN?")
    usb.request_msg_in(100, None)
    usb.halted.add(u.BULK_IN)
    with pytest.raises(u.UsbError):
        usb.bulk_in()


# ---- REN_CONTROL, GO_TO_LOCAL, LOCAL_LOCKOUT (USB488 4.3.2-4.3.4) ------------------------------------------


def test_the_remote_local_state_machine(usb):
    rl = bus_of(usb.session, "load").remote_local
    assert rl.state == "local"
    assert req(usb, u.REN_CONTROL, 1) == bytes([u.STATUS_SUCCESS])
    assert rl.state == "local" and rl.ren  # REN alone does not make it remote ...
    usb.write(b"*CLS")
    assert rl.state == "remote"  # ... a message does (DEV_DEP_MSG_OUT)
    req(usb, u.LOCAL_LOCKOUT)
    assert rl.state == "remote_lockout"
    req(usb, u.GO_TO_LOCAL)
    assert rl.state == "local_lockout"
    req(usb, u.REN_CONTROL, 0)
    assert rl.state == "local" and not rl.ren  # REN false clears the lockout too


def test_ren_control_needs_0_or_1(usb):
    assert req(usb, u.REN_CONTROL, 2)[0] == u.STATUS_FAILED


def test_trigger_makes_a_device_with_ren_remote(usb):
    req(usb, u.REN_CONTROL, 1)
    usb.trigger()
    assert bus_of(usb.session, "load").remote_local.state == "remote"


def test_visa_control_ren_maps_onto_the_usb488_requests(usb):
    rl = bus_of(usb.session, "load").remote_local
    usb.control_ren(ops.asrt_address_llo)
    assert rl.state == "remote_lockout"
    usb.control_ren(ops.address_gtl)
    assert rl.state == "local_lockout"
    usb.control_ren(ops.deassert_gtl)
    assert rl.state == "local" and not rl.ren
    usb.control_ren(ops.asrt_address)
    assert rl.state == "remote"
    with pytest.raises(u.UsbError):
        usb.control_ren(99)


def test_every_control_action_is_in_the_event_stream(usb):
    req(usb, u.REN_CONTROL, 1)
    req(usb, u.LOCAL_LOCKOUT)
    req(usb, u.GO_TO_LOCAL)
    actions = [e["action"] for e in usb.session.history if e["kind"] == "bus_control"]
    assert actions == ["USB: REN_CONTROL 1", "USB: LOCAL_LOCKOUT", "USB: GO_TO_LOCAL"]


def test_the_interface_is_shared_by_every_access_path(usb):
    assert u.usb_interface(usb.session, "load") is usb
