"""Connected mode (D63): a script in its own process drives the bench a service runs.

The service side is a BenchLinkServer on a bench; the script side is PyVISA with
``PYVISA_LIBRARY=<host>:<port>@simubench``. What a script sees is what the links do, so these tests
check that the same behaviour arrives through the connection, and what the connection adds: shared
locks between scripts, locks dropped with the connection, the service seeing its clients.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pyvisa
from pyvisa import constants, errors

from simubench.access.bench_link import BenchLinkServer, LinkConnection, RemoteBench
from simubench.access.bus import BusError
from simubench.api import open_bench
from simubench.models.bench import Fault

ROOT = Path(__file__).resolve().parents[1]
GPIB_BENCH = ROOT / "examples/benches/fluke-5730a.bench.yaml"  # cal GPIB0::6, dmm GPIB0::7
USB_BENCH = ROOT / "examples/benches/bk-8614.bench.yaml"
USB_ADDRESS = "USB0::0xFFFF::0x8614::601400010000000001::INSTR"


def serve(bench_file: Path):
    @pytest.fixture
    async def rig():
        bench = open_bench(bench_file)
        server = BenchLinkServer(bench, port=0)
        await server.start()
        yield SimpleNamespace(bench=bench, server=server, address=server.address())
        await server.stop()
        bench.close()

    return rig


gpib_rig = serve(GPIB_BENCH)
usb_rig = serve(USB_BENCH)


def manager(address: str) -> pyvisa.ResourceManager:
    return pyvisa.ResourceManager(f"{address}@simubench")


# ---- the protocol --------------------------------------------------------------------------------------------


async def test_hello_lists_the_instruments_and_their_addresses(gpib_rig):
    def run() -> None:
        remote = RemoteBench(gpib_rig.address)
        assert {n: i.instance.address for n, i in remote.instruments.items()} == {
            "cal": "GPIB0::6::INSTR",
            "dmm": "GPIB0::7::INSTR",
        }
        assert remote.instruments["cal"].idn.startswith("FLUKE,5730A")
        remote.close()

    await asyncio.to_thread(run)


async def test_a_link_carries_calls_and_bus_errors(gpib_rig):
    def run() -> None:
        remote = RemoteBench(gpib_rig.address)
        cal = remote.open_link("cal", "gpib", 0)
        cal.write(b"*IDN?")
        assert cal.read_message().startswith(b"FLUKE,5730A")
        assert cal.read_message() is None  # nothing left: the caller turns that into a timeout
        board = remote.open_link(None, "gpib_intfc", 0)
        with pytest.raises(BusError) as err:
            board.write(b"*IDN?")  # nobody is addressed to listen
        assert err.value.code == constants.VI_ERROR_NLISTENERS
        assert board.gpib_command(bytes([0x3F, 0x26])) == 2  # UNL, listen address 6
        remote.close()

    await asyncio.to_thread(run)


async def test_opening_what_is_not_there_is_refused(gpib_rig):
    def run() -> None:
        remote = RemoteBench(gpib_rig.address)
        with pytest.raises(BusError) as err:
            remote.open_link("nobody", "net", 0)
        assert err.value.code == constants.VI_ERROR_RSRC_NFOUND
        with pytest.raises(BusError) as err:
            remote.open_link("cal", "carrier-pigeon", 0)
        assert err.value.code == constants.VI_ERROR_INV_SETUP
        remote.close()

    await asyncio.to_thread(run)


async def test_only_the_link_methods_can_be_called(gpib_rig):
    def run() -> None:
        conn = LinkConnection(gpib_rig.address)
        conn.request({"op": "open", "id": 1, "name": "cal", "kind": "net", "board": 0})
        for method in ("__class__", "session", "_locks", "pass_time", "close"):
            reply = conn.request({"op": "call", "id": 1, "method": method, "args": []})
            assert reply["ok"] is False and reply["code"] == constants.VI_ERROR_INV_OBJECT, method
        assert conn.request({"op": "shell", "cmd": "id"})["ok"] is False
        conn.close()

    await asyncio.to_thread(run)


async def test_something_that_is_not_the_protocol_is_dropped(gpib_rig):
    def run() -> None:
        import socket

        host, port = gpib_rig.address.split(":")
        sock = socket.create_connection((host, int(port)), timeout=5)
        sock.sendall(b"GET / HTTP/1.1\r\n\r\n")
        assert sock.recv(10) == b""  # the connection is closed
        sock.close()

    await asyncio.to_thread(run)


# ---- the service sees its clients, and locks are shared between scripts ----------------------------------


async def test_the_service_lists_every_link_as_a_client(gpib_rig):
    def run() -> None:
        remote = RemoteBench(gpib_rig.address)
        remote.open_link("cal", "gpib", 0)
        remote.open_link(None, "gpib_intfc", 0)
        labels = gpib_rig.server.clients()
        assert len(labels) == 2 and all(label.startswith("bench:127.0.0.1:") for label in labels)
        assert len(gpib_rig.server.clients("cal")) == 1  # the board belongs to no instrument
        remote.close()

    await asyncio.to_thread(run)
    deadline = time.monotonic() + 3
    while gpib_rig.server.clients() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert gpib_rig.server.clients() == []  # closing the connection closed its links


async def test_a_lock_holds_another_script_out_and_goes_with_the_connection(gpib_rig):
    def run() -> None:
        a, b = RemoteBench(gpib_rig.address), RemoteBench(gpib_rig.address)
        mine, theirs = a.open_link("cal", "gpib", 0), b.open_link("cal", "gpib", 0)
        mine.lock(True, "", 0)
        holders = [label for label in gpib_rig.server.clients("cal") if gpib_rig.server.holds_lock(label)]
        assert len(holders) == 1 and len(gpib_rig.server.clients("cal")) == 2  # the service sees who holds it
        with pytest.raises(BusError) as err:
            theirs.write(b"*IDN?")
        assert err.value.code == constants.VI_ERROR_RSRC_LOCKED
        with pytest.raises(BusError) as err:
            theirs.lock(True, "", 10)
        assert err.value.code == constants.VI_ERROR_TMO
        a.close()  # the script died: its connection is gone and so is its lock
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                theirs.write(b"*CLS")
                break
            except BusError:
                time.sleep(0.02)
        else:
            raise AssertionError("the lock was not released with the connection")
        b.close()

    await asyncio.to_thread(run)


async def test_a_lock_is_shared_with_scripts_in_the_same_process_as_the_bench(gpib_rig):
    """A link in the service and an in-process link are the same holder-agnostic table."""
    from simubench.access.links import make_link

    local = make_link(gpib_rig.bench, "cal", "gpib", 0)
    local.lock(True, "", 0)

    def run() -> None:
        remote = RemoteBench(gpib_rig.address)
        with pytest.raises(BusError) as err:
            remote.open_link("cal", "gpib", 0).write(b"*IDN?")
        assert err.value.code == constants.VI_ERROR_RSRC_LOCKED
        remote.close()

    await asyncio.to_thread(run)


# ---- PyVISA through the connection ----------------------------------------------------------------------


async def test_a_gpib_script_runs_through_the_connection(gpib_rig):
    def run() -> None:
        rm = manager(gpib_rig.address)
        try:
            cal = rm.open_resource("GPIB0::6::INSTR", read_termination="\n", write_termination="\n")
            assert cal.query("*IDN?").startswith("FLUKE,5730A")
            for command in ("*CLS", "*ESE 1", "*SRE 32", "*OPC"):
                cal.write(command)
            cal.enable_event(constants.EventType.service_request, constants.EventMechanism.queue)
            cal.wait_on_event(constants.EventType.service_request, 100)
            assert cal.read_stb() == 0x60  # the serial poll clears RQS, as in process
            assert cal.read_stb() == 0x20
            assert "GPIB0::INTFC" in rm.list_resources("GPIB?*INTFC")
        finally:
            rm.close()

    await asyncio.to_thread(run)


async def test_the_board_session_commands_the_bus_through_the_connection(gpib_rig):
    def run() -> None:
        rm = manager(gpib_rig.address)
        try:
            intfc = rm.open_resource("GPIB0::INTFC", read_termination="\n", write_termination="\n")
            intfc.send_command(bytes([0x3F, 0x20 | 7, 0x14]))  # UNL, listen 7, then DCL
            intfc.send_command(bytes([0x3F, 0x20 | 6]))
            intfc.write("*IDN?")
            intfc.send_command(bytes([0x3F, 0x20 | 0, 0x40 | 6]))
            assert intfc.read().startswith("FLUKE,5730A")
            a = constants.ResourceAttribute
            intfc.control_ren(constants.RENLineOperation.asrt)
            assert intfc.get_visa_attribute(a.gpib_ren_state) == constants.LineState.asserted
            with pytest.raises(errors.VisaIOError) as err:
                intfc.pass_control(6, constants.VI_NO_SEC_ADDR)
            assert err.value.error_code == constants.VI_ERROR_NSUP_OPER
        finally:
            rm.close()

    await asyncio.to_thread(run)


async def test_a_usb_script_runs_through_the_connection(usb_rig):
    def run() -> None:
        rm = manager(usb_rig.address)
        try:
            load = rm.open_resource(USB_ADDRESS, read_termination="\n", write_termination="\n")
            assert "8614" in load.query("*IDN?")
            capabilities = load.control_in(0xA1, 7, 0, 0, 24)  # GET_CAPABILITIES
            assert len(capabilities) == 24 and capabilities[14] == 0b111
            assert load.control_in(0xA1, 5, 0, 0, 1) == b"\x01"  # INITIATE_CLEAR halts Bulk-OUT
            load.control_out(0x02, 1, 0, 0x02)  # CLEAR_FEATURE(ENDPOINT_HALT)
            assert "8614" in load.query("*IDN?")
            with pytest.raises(errors.VisaIOError) as err:
                load.control_in(0xA1, 200, 0, 0, 1)
            assert err.value.error_code == constants.VI_ERROR_IO  # STALL
        finally:
            rm.close()

    await asyncio.to_thread(run)


async def test_a_read_with_nothing_to_read_costs_the_timeout_on_the_services_clock(gpib_rig):
    start = gpib_rig.bench.now_ns

    def run() -> None:
        rm = manager(gpib_rig.address)
        try:
            cal = rm.open_resource("GPIB0::6::INSTR", read_termination="\n")
            cal.timeout = 300
            with pytest.raises(errors.VisaIOError) as err:
                cal.read()
            assert err.value.error_code == constants.VI_ERROR_TMO
        finally:
            rm.close()

    await asyncio.to_thread(run)
    assert (gpib_rig.bench.now_ns - start) / 1e9 >= 0.3  # virtual time passed in the service


async def test_a_link_fault_is_a_lost_connection_for_the_script(gpib_rig):
    def run() -> None:
        rm = manager(gpib_rig.address)
        try:
            cal = rm.open_resource("GPIB0::6::INSTR", read_termination="\n", write_termination="\n")
            assert cal.query("*OPC?") == "1"
            gpib_rig.bench.faults.inject(Fault(kind="disconnect", instrument="cal", duration_s=5.0))
            with pytest.raises(errors.VisaIOError) as err:
                cal.query("*OPC?")
            assert err.value.error_code == constants.VI_ERROR_CONN_LOST
        finally:
            rm.close()

    await asyncio.to_thread(run)


async def test_two_scripts_share_one_bench(gpib_rig):
    def run() -> None:
        one, two = manager(gpib_rig.address), manager(gpib_rig.address)
        try:
            a = one.open_resource("GPIB0::6::INSTR", write_termination="\n", read_termination="\n")
            b = two.open_resource("GPIB0::6::INSTR", write_termination="\n", read_termination="\n")
            a.write("*CLS")
            a.write("*ESE 1")
            a.write("*SRE 32")
            a.write("*OPC")
            assert b.read_stb() & 0x40  # the other script polls first and takes the request
            assert a.read_stb() & 0x40 == 0
            a.lock_excl()
            with pytest.raises(errors.VisaIOError) as err:
                b.write("*CLS")
            assert err.value.error_code == constants.VI_ERROR_RSRC_LOCKED
            a.unlock()
            b.write("*CLS")
        finally:
            one.close()
            two.close()

    await asyncio.to_thread(run)


# ---- finding the service -----------------------------------------------------------------------------------


def test_no_service_is_an_error_that_says_what_to_do():
    with pytest.raises(Exception) as err:
        pyvisa.ResourceManager("127.0.0.1:1@simubench")
    assert "no SimuBench service answers at 127.0.0.1:1" in str(err.value)


SCRIPT = """
import pyvisa
rm = pyvisa.ResourceManager()          # the library comes from PYVISA_LIBRARY, as on a lab PC
cal = rm.open_resource("GPIB0::6::INSTR", read_termination="\\n", write_termination="\\n")
print(cal.query("*IDN?"))
"""


async def test_an_unchanged_script_drives_the_running_bench(gpib_rig):
    env = {**os.environ, "PYVISA_LIBRARY": f"{gpib_rig.address}@simubench"}

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True, timeout=60
        )

    out = await asyncio.to_thread(run)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("FLUKE,5730A")
    events = [
        e for e in gpib_rig.bench.history if e["kind"] == "scpi" and e.get("client", "").startswith("bench:")
    ]
    assert events  # the service's own event stream shows who talked to the bench
