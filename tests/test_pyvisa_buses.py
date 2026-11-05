"""PyVISA backend: GPIB and USB INSTR sessions behave like their buses (D62).

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


def test_control_ren_is_recorded_in_the_event_stream(gpib):
    cal = gpib.open_resource("GPIB0::4::INSTR")
    cal.control_ren(constants.RENLineOperation.address_gtl)
    lib = gpib.visalib
    events = [e for e in lib.bench.history if e["kind"] == "bus_control"]
    assert events and events[-1]["action"] == "go to local" and events[-1]["instrument"] == "cal"


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
