"""PyVISA backend: GPIB and USB INSTR sessions behave like their buses (D63).

The bench file declares the addresses (``address:``); the backend fills the VISA attributes from
them and gives the sessions the bus semantics a script can observe: resource locks, REN/GTL/LLO,
service requests. Nothing here needs a network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pyvisa
from pyvisa import constants, errors

ROOT = Path(__file__).resolve().parent.parent
GPIB_BENCH = ROOT / "examples/benches/fluke-5522a.bench.yaml"  # cal: GPIB0::4::INSTR
USB_BENCH = ROOT / "examples/benches/bk-8614.bench.yaml"


@pytest.fixture
def gpib(tmp_path):
    bench = tmp_path / "gpib.bench.yaml"  # one backend per bench path: a fresh file per test
    bench.write_bytes(GPIB_BENCH.read_bytes())
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    yield rm
    rm.close()


@pytest.fixture
def usb(tmp_path):
    bench = tmp_path / "usb.bench.yaml"
    bench.write_bytes(USB_BENCH.read_bytes())
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    yield rm
    rm.close()


def idn(inst) -> str:
    return inst.query("*IDN?")


def test_gpib_address_opens_the_instrument_and_fills_its_attributes(gpib):
    cal = gpib.open_resource("GPIB0::4::INSTR", read_termination="\n", write_termination="\n")
    assert "5522A" in idn(cal)
    assert cal.get_visa_attribute(constants.VI_ATTR_GPIB_PRIMARY_ADDR) == 4
    assert cal.get_visa_attribute(constants.VI_ATTR_GPIB_SECONDARY_ADDR) == constants.VI_NO_SEC_ADDR
    assert cal.get_visa_attribute(constants.VI_ATTR_INTF_TYPE) == constants.InterfaceType.gpib


def test_usb_address_fills_the_ids_the_script_wrote(usb):
    load = usb.open_resource(
        "USB0::0xFFFF::0x8614::601400010000000001::INSTR", read_termination="\n", write_termination="\n"
    )
    assert load.get_visa_attribute(constants.VI_ATTR_MANF_ID) == 0xFFFF
    assert load.get_visa_attribute(constants.VI_ATTR_MODEL_CODE) == 0x8614
    assert load.get_visa_attribute(constants.VI_ATTR_USB_SERIAL_NUM) == "601400010000000001"
    assert load.get_visa_attribute(constants.VI_ATTR_USB_PROTOCOL) == 1  # USB488


def test_an_address_the_bench_does_not_declare_is_not_found(gpib):
    with pytest.raises(errors.VisaIOError) as err:
        gpib.open_resource("GPIB0::5::INSTR")
    assert err.value.error_code == constants.VI_ERROR_RSRC_NFOUND


def test_control_ren_walks_the_remote_local_states(gpib):
    cal = gpib.open_resource("GPIB0::4::INSTR")
    ren = constants.VI_ATTR_GPIB_REN_STATE
    assert cal.get_visa_attribute(ren) == constants.LineState.unasserted
    cal.control_ren(constants.RENLineOperation.asrt_address)
    assert cal.get_visa_attribute(ren) == constants.LineState.asserted
    cal.control_ren(constants.RENLineOperation.deassert_gtl)
    assert cal.get_visa_attribute(ren) == constants.LineState.unasserted


def test_control_ren_is_recorded_as_what_the_bus_did(gpib):
    cal = gpib.open_resource("GPIB0::4::INSTR")
    cal.control_ren(constants.RENLineOperation.address_gtl)
    lib = gpib.visalib
    actions = [e["action"] for e in lib.bench.history if e["kind"] == "bus_control"]
    assert actions == ["GPIB0: GTL", "GPIB0: REN address_gtl"]  # GTL went out, then the mode is noted
    assert all(e["instrument"] == "cal" for e in lib.bench.history if e["kind"] == "bus_control")


def test_an_exclusive_lock_keeps_the_other_session_out(gpib):
    a = gpib.open_resource("GPIB0::4::INSTR", read_termination="\n", write_termination="\n")
    b = gpib.open_resource("GPIB0::4::INSTR", read_termination="\n", write_termination="\n")
    a.lock_excl()
    with pytest.raises(errors.VisaIOError) as err:
        b.write("*IDN?")
    assert err.value.error_code == constants.VI_ERROR_RSRC_LOCKED
    assert "5522A" in idn(a)  # the holder is not affected
    a.unlock()
    assert "5522A" in idn(b)


def test_a_lock_attempt_on_a_locked_resource_times_out(gpib):
    a = gpib.open_resource("GPIB0::4::INSTR")
    b = gpib.open_resource("GPIB0::4::INSTR")
    a.lock_excl()
    with pytest.raises(errors.VisaIOError) as err:
        b.lock_excl(timeout=10)
    assert err.value.error_code == constants.VI_ERROR_TMO


def test_a_shared_lock_is_joined_with_its_key(gpib):
    a = gpib.open_resource("GPIB0::4::INSTR", read_termination="\n", write_termination="\n")
    b = gpib.open_resource("GPIB0::4::INSTR", read_termination="\n", write_termination="\n")
    c = gpib.open_resource("GPIB0::4::INSTR")
    key = a.lock()
    assert b.lock(requested_key=key) == key
    assert "5522A" in idn(b)
    with pytest.raises(errors.VisaIOError):
        c.write("*IDN?")
    a.unlock()
    b.unlock()
    c.write("*CLS")  # the resource is free again


def test_unlock_without_a_lock_is_an_error(gpib):
    a = gpib.open_resource("GPIB0::4::INSTR")
    with pytest.raises(errors.VisaIOError) as err:
        a.unlock()
    assert err.value.error_code == constants.VI_ERROR_SESN_NLOCKED


def test_closing_a_session_releases_its_lock(gpib):
    a = gpib.open_resource("GPIB0::4::INSTR")
    b = gpib.open_resource("GPIB0::4::INSTR")
    a.lock_excl()
    a.close()
    b.write("*CLS")


def test_nested_locks_need_as_many_unlocks(gpib):
    a = gpib.open_resource("GPIB0::4::INSTR")
    b = gpib.open_resource("GPIB0::4::INSTR")
    a.lock_excl()
    a.lock_excl()
    a.unlock()
    with pytest.raises(errors.VisaIOError):
        b.write("*CLS")
    a.unlock()
    b.write("*CLS")


def test_service_request_event_follows_the_rqs_bit(gpib):
    cal = gpib.open_resource("GPIB0::4::INSTR", read_termination="\n", write_termination="\n")
    cal.enable_event(constants.EventType.service_request, constants.EventMechanism.queue)
    cal.write("*CLS")
    cal.write("*SRE 32")  # ESB in the service request enable register
    cal.write("*ESE 1")  # operation complete sets ESB
    cal.write("*OPC")
    resp = cal.wait_on_event(constants.EventType.service_request, 100)
    assert not resp.timed_out
    cal.discard_events(constants.EventType.service_request, constants.EventMechanism.queue)


def test_waiting_for_an_event_that_was_not_enabled_is_an_error(gpib):
    cal = gpib.open_resource("GPIB0::4::INSTR")
    with pytest.raises(errors.VisaIOError) as err:
        cal.wait_on_event(constants.EventType.service_request, 10, capture_timeout=False)
    assert err.value.error_code == constants.VI_ERROR_NENABLED


def test_vxi11_address_forms_resolve_in_process(gpib):
    names = gpib.list_resources("?*::INSTR")
    assert "TCPIP0::127.0.0.1::inst0::INSTR" in names
    assert "TCPIP0::127.0.0.1::gpib0,4::INSTR" in names  # the gateway form of the declared GPIB address
    by_index = gpib.open_resource("TCPIP0::127.0.0.1::inst0::INSTR", read_termination="\n")
    by_gateway = gpib.open_resource("TCPIP0::127.0.0.1::gpib0,4::INSTR", read_termination="\n")
    by_name = gpib.open_resource("TCPIP0::127.0.0.1::inst_cal::INSTR", read_termination="\n")
    for inst in (by_index, by_gateway, by_name):
        assert "5522A" in inst.query("*IDN?")
    assert by_gateway.get_visa_attribute(constants.VI_ATTR_TCPIP_IS_HISLIP) == constants.VI_FALSE


def test_srq_wait_returns_when_rqs_rises_during_the_wait(gpib):
    """The SRQ of ``*OPC`` after a long operation appears only as virtual time passes."""
    cal = gpib.open_resource("GPIB0::4::INSTR")
    bench = gpib.visalib.bench
    start = bench.now_ns
    cal_instrument = bench.instrument("cal")
    cal_instrument.sre = 0x20  # ESB is an enabled reason
    cal_instrument.stb = lambda commit=True: (
        0x60 if bench.now_ns - start >= 250_000_000 else 0
    )  # appears at 250 ms
    cal.enable_event(constants.EventType.service_request, constants.EventMechanism.queue)
    resp = cal.wait_on_event(constants.EventType.service_request, 2000)
    assert not resp.timed_out
    spent = (bench.now_ns - start) / 1e9
    assert 0.25 <= spent < 0.5  # what the instrument needed, not the whole 2 s


def test_srq_wait_that_never_ends_times_out_after_the_timeout(gpib):
    cal = gpib.open_resource("GPIB0::4::INSTR")
    cal.enable_event(constants.EventType.service_request, constants.EventMechanism.queue)
    start = gpib.visalib.bench.now_ns
    with pytest.raises(errors.VisaIOError) as err:
        cal.wait_on_event(constants.EventType.service_request, 100, capture_timeout=False)
    assert err.value.error_code == constants.VI_ERROR_TMO
    assert (gpib.visalib.bench.now_ns - start) / 1e9 >= 0.1


@pytest.mark.parametrize(
    "call",
    [  # a device session cannot command the bus: only the board (INTFC) can
        lambda lib, s: lib.gpib_send_ifc(s),
        lambda lib, s: lib.gpib_command(s, b"\x3f"),
        lambda lib, s: lib.gpib_control_atn(s, 0),
        lambda lib, s: lib.gpib_pass_control(s, 5, 0),
        lambda lib, s: lib.usb_control_in(s, 0xC0, 0, 0, 0),
        lambda lib, s: lib.usb_control_out(s, 0x40, 0, 0, 0),
    ],
)
def test_interface_operations_are_refused_on_an_instrument_session(gpib, call):
    cal = gpib.open_resource("GPIB0::4::INSTR")
    with pytest.raises(errors.VisaIOError) as err:
        call(gpib.visalib, cal.session)
    assert err.value.error_code == constants.VI_ERROR_NSUP_OPER


def test_the_served_vxi11_form_with_its_port_resolves_in_process(gpib):
    """What ``serve`` prints when no port mapper is on 111 also opens in process; the port is random."""
    cal = gpib.open_resource("TCPIP0::127.0.0.1,51234::inst0::INSTR", read_termination="\n")
    assert "5522A" in cal.query("*IDN?")
    gateway = gpib.open_resource("TCPIP0::127.0.0.1,51234::gpib0,4::INSTR", read_termination="\n")
    assert "5522A" in gateway.query("*IDN?")


# ---- the GPIB board (GPIB0::INTFC) ------------------------------------------------------------------------------


@pytest.fixture
def board(tmp_path):
    bench = tmp_path / "board.bench.yaml"
    bench.write_bytes((ROOT / "examples/benches/fluke-5730a.bench.yaml").read_bytes())  # GPIB 6 and 7
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    yield rm
    rm.close()


def test_the_board_is_listed_and_opens_as_an_interface(board):
    assert "GPIB0::INTFC" in board.list_resources("GPIB?*INTFC")
    intfc = board.open_resource("GPIB0::INTFC")
    a = constants.ResourceAttribute
    assert intfc.get_visa_attribute(a.interface_type) == constants.InterfaceType.gpib
    assert intfc.get_visa_attribute(a.gpib_system_controller) == constants.VI_TRUE
    assert intfc.get_visa_attribute(a.gpib_cic_state) == constants.VI_TRUE
    assert intfc.get_visa_attribute(a.gpib_primary_address) == 0  # the board's own address
    with pytest.raises(errors.VisaIOError) as err:
        board.open_resource("GPIB1::INTFC")  # no device is on board 1
    assert err.value.error_code == constants.VI_ERROR_RSRC_NFOUND


def test_the_board_addresses_a_device_and_talks_to_it(board):
    intfc = board.open_resource("GPIB0::INTFC", read_termination="\n", write_termination="\n")
    with pytest.raises(errors.VisaIOError) as err:
        intfc.write("*IDN?")  # nobody is addressed to listen
    assert err.value.error_code == constants.VI_ERROR_NLISTENERS
    intfc.send_command(bytes([0x3F, 0x20 | 6]))  # UNL, listen address 6
    intfc.write("*IDN?")
    intfc.send_command(bytes([0x3F, 0x20 | 0, 0x40 | 6]))  # UNL, the board listens, device 6 talks
    assert intfc.read().startswith("FLUKE,5730A")


def test_the_board_sends_ifc_triggers_and_controls_ren(board):
    intfc = board.open_resource("GPIB0::INTFC")
    cal = board.open_resource("GPIB0::6::INSTR")
    a = constants.ResourceAttribute
    intfc.control_ren(constants.RENLineOperation.asrt)
    assert cal.get_visa_attribute(a.gpib_ren_state) == constants.LineState.asserted  # a line of the bus
    intfc.send_command(bytes([0x3F, 0x20 | 7]))
    intfc.send_ifc()
    assert intfc.get_visa_attribute(a.gpib_address_state) == constants.AddressState.unaddressed
    intfc.control_atn(constants.ATNLineOperation.asrt)
    assert intfc.get_visa_attribute(a.gpib_atn_state) == constants.LineState.asserted
    intfc.control_atn(constants.ATNLineOperation.deassert)
    intfc.send_command(bytes([0x3F, 0x20 | 7]))
    intfc.assert_trigger()  # GET to whoever listens
    triggers = [e for e in board.visalib.bench.history if e["kind"] == "scpi" and e.get("data") == b"*TRG"]
    assert [e["instrument"] for e in triggers] == ["dmm"]


def test_passing_control_fails_because_no_device_can_take_it(board):
    intfc = board.open_resource("GPIB0::INTFC")
    with pytest.raises(errors.VisaIOError) as err:
        intfc.pass_control(6, constants.VI_NO_SEC_ADDR)
    assert err.value.error_code == constants.VI_ERROR_NSUP_OPER


def test_the_srq_line_is_visible_on_the_board_and_a_poll_lowers_it(board):
    intfc = board.open_resource("GPIB0::INTFC")
    cal = board.open_resource("GPIB0::6::INSTR", read_termination="\n", write_termination="\n")
    srq = constants.ResourceAttribute.gpib_srq_state
    assert intfc.get_visa_attribute(srq) == constants.LineState.unasserted
    for command in ("*CLS", "*ESE 1", "*SRE 32", "*OPC"):
        cal.write(command)
    assert intfc.get_visa_attribute(srq) == constants.LineState.asserted
    cal.enable_event(constants.EventType.service_request, constants.EventMechanism.queue)
    cal.wait_on_event(constants.EventType.service_request, 100)  # this instrument asked
    assert cal.read_stb() == 0x60  # the serial poll: ESB and RQS
    assert intfc.get_visa_attribute(srq) == constants.LineState.unasserted  # and the line is down


def test_visa_addresses_the_device_for_every_write_and_read(board):
    from pyvisa import constants as c

    from simubench.access.gpib_bus import gpib_board

    cal = board.open_resource("GPIB0::6::INSTR", write_termination="\n")
    bus = gpib_board(board.visalib.bench, 0)
    assert bus.address_state("cal") == c.AddressState.unaddressed
    cal.write("*CLS")  # VISA addressed it to listen for the write
    assert bus.address_state("cal") == c.AddressState.listenr
    cal.write("*IDN?")
    cal.read_raw()
    assert bus.address_state("cal") == c.AddressState.talker  # and to talk for the read


def test_two_sessions_to_one_device_share_its_serial_poll(board):
    a = board.open_resource("GPIB0::6::INSTR", write_termination="\n")
    b = board.open_resource("GPIB0::6::INSTR", write_termination="\n")
    for command in ("*CLS", "*ESE 1", "*SRE 32", "*OPC"):
        a.write(command)
    assert b.read_stb() & 0x40  # whoever polls first gets RQS ...
    assert a.read_stb() & 0x40 == 0  # ... the request is gone for the other


# ---- USB: the USBTMC/USB488 interface -----------------------------------------------------------------------------

USB_ADDRESS = "USB0::0xFFFF::0x8614::601400010000000001::INSTR"


def test_usb_control_requests_are_answered_as_the_specifications_say(usb):
    load = usb.open_resource(USB_ADDRESS)
    capabilities = load.control_in(0xA1, 7, 0, 0, 24)  # GET_CAPABILITIES
    assert len(capabilities) == 24 and capabilities[0] == 0x01 and capabilities[14] == 0b111
    assert load.control_in(0xA1, 64, 0, 0, 1) == b"\x01"  # INDICATOR_PULSE
    with pytest.raises(errors.VisaIOError) as err:
        load.control_in(0xA1, 200, 0, 0, 1)  # a request the device does not define: STALL
    assert err.value.error_code == constants.VI_ERROR_IO


def test_usb_abort_and_clear_run_through_pyvisa(usb):
    load = usb.open_resource(USB_ADDRESS, write_termination="\n", read_termination="\n")
    load.write("*IDN?")
    assert load.control_in(0xA1, 5, 0, 0, 1) == b"\x01"  # INITIATE_CLEAR
    assert load.control_in(0x82, 0, 0, 0x02, 2) == b"\x01\x00"  # GET_STATUS: the Bulk-OUT endpoint is halted
    assert load.control_in(0xA1, 6, 0, 0, 2) == b"\x01\x00"  # CHECK_CLEAR_STATUS
    load.control_out(0x02, 1, 0, 0x02)  # CLEAR_FEATURE(ENDPOINT_HALT) on the Bulk-OUT endpoint
    assert load.control_in(0x82, 0, 0, 0x02, 2) == b"\x00\x00"
    assert "8614" in load.query("*IDN?")  # and the interface is usable again


def test_usb_clear_status_byte_trigger_and_remote_local(usb):
    load = usb.open_resource(USB_ADDRESS, write_termination="\n", read_termination="\n")
    load.write("*IDN?")
    load.clear()
    assert load.read_stb() & 0x10 == 0  # the cleared output queue leaves no MAV
    load.assert_trigger()
    load.control_ren(constants.RENLineOperation.asrt_address_llo)
    rl = bus_state(usb, "load")
    assert rl.state == "remote_lockout"
    load.control_ren(constants.RENLineOperation.deassert_gtl)
    assert rl.state == "local"


def bus_state(rm, name):
    from simubench.access.bus import bus_of

    return bus_of(rm.visalib.bench, name).remote_local


def test_usb_service_request_arrives_on_interrupt_in_and_is_taken_once(usb):
    load = usb.open_resource(USB_ADDRESS, write_termination="\n", read_termination="\n")
    load.enable_event(constants.EventType.service_request, constants.EventMechanism.queue)
    for command in ("*CLS", "*ESE 1", "*SRE 32", "*OPC"):
        load.write(command)
    load.wait_on_event(constants.EventType.service_request, 100)  # consumes the Interrupt-IN packet
    with pytest.raises(errors.VisaIOError) as err:
        load.wait_on_event(constants.EventType.service_request, 10, capture_timeout=False)
    assert err.value.error_code == constants.VI_ERROR_TMO  # one request, one event


def test_usb_attributes_come_from_the_address_and_the_identity(usb):
    load = usb.open_resource(USB_ADDRESS)
    a = constants.ResourceAttribute
    assert load.get_visa_attribute(a.manufacturer_name) == "B&K PRECISION"  # from *IDN?
    assert load.get_visa_attribute(a.model_name) == "8614"
    assert load.get_visa_attribute(a.usb_max_interrupt_size) == 8
    with pytest.raises(errors.VisaIOError):
        load.get_visa_attribute(a.usb_bulk_out_pipe)  # a pipe attribute: USB RAW only, as in VISA


def test_wait_for_srq_polls_like_a_script_does(board):
    """PyVISA's own wait_for_srq polls the status byte after the event, so RQS is already read."""
    cal = board.open_resource("GPIB0::6::INSTR", write_termination="\n")
    for command in ("*CLS", "*ESE 1", "*SRE 32", "*OPC"):
        cal.write(command)
    cal.wait_for_srq(100)
    assert cal.read_stb() == 0x20  # the reason is still there, the request was taken by wait_for_srq


# ---- an unchanged script -------------------------------------------------------------------------------------------


SCRIPT = """
import pyvisa
rm = pyvisa.ResourceManager()          # no library argument: PYVISA_LIBRARY decides, as on a lab PC
cal = rm.open_resource("GPIB0::4::INSTR", read_termination="\\n", write_termination="\\n")
print(cal.query("*IDN?"))
cal.write("*CLS"); cal.write("*ESE 1"); cal.write("*SRE 32"); cal.write("*OPC")
cal.wait_for_srq(1000)
print(hex(cal.read_stb()))
"""


def test_a_script_that_names_no_library_runs_on_the_bench_through_the_environment(tmp_path):
    import os
    import subprocess
    import sys

    bench = tmp_path / "env.bench.yaml"
    bench.write_bytes(GPIB_BENCH.read_bytes())
    env = {**os.environ, "PYVISA_LIBRARY": f"{bench}@simubench"}
    out = subprocess.run([sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    first, second = out.stdout.split("\n")[:2]
    assert "5522A" in first and second == "0x20"  # wait_for_srq took the request with its own poll


def test_every_kind_of_session_reads_the_status_byte_as_a_serial_poll(board):
    """GPIB, USB, VXI-11 and a named instrument: one rule, RQS is read once and cleared (IEEE 488.2 11.2)."""
    for address in ("GPIB0::6::INSTR", "TCPIP0::127.0.0.1::inst_cal::INSTR", "cal"):
        inst = board.open_resource(address, write_termination="\n")
        for command in ("*CLS", "*ESE 1", "*SRE 32", "*OPC"):
            inst.write(command)
        assert inst.read_stb() == 0x60, address
        assert inst.read_stb() == 0x20, address  # the reason is still there, the request is gone
