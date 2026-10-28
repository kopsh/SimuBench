"""HiSLIP server (D41): the wire protocol exercised by a minimal raw client.

pyvisa-py covers the VISA-level behaviour in acceptance/test_hislip_scenario.py; these tests
reach for the parts a VISA client cannot see: interrupted responses, lock waits, the device
clear handshake's input discipline, SRQ and the error paths of IVI-6.1.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from simubench.access.hislip_server import (
    ASYNC_DEVICE_CLEAR,
    ASYNC_DEVICE_CLEAR_ACKNOWLEDGE,
    ASYNC_INITIALIZE,
    ASYNC_INITIALIZE_RESPONSE,
    ASYNC_INTERRUPTED,
    ASYNC_LOCK,
    ASYNC_LOCK_INFO,
    ASYNC_LOCK_INFO_RESPONSE,
    ASYNC_LOCK_RESPONSE,
    ASYNC_MAXIMUM_MESSAGE_SIZE,
    ASYNC_MAXIMUM_MESSAGE_SIZE_RESPONSE,
    ASYNC_SERVICE_REQUEST,
    ASYNC_STATUS_QUERY,
    ASYNC_STATUS_RESPONSE,
    DATA,
    DATA_END,
    DEVICE_CLEAR_ACKNOWLEDGE,
    DEVICE_CLEAR_COMPLETE,
    ERROR,
    FATAL_ERROR,
    HEADER,
    INITIALIZE,
    INITIALIZE_RESPONSE,
    INTERRUPTED,
    TRIGGER,
    HislipServer,
)
from simubench.api import open_bench
from simubench.models.bench import Fault

BENCH = Path(__file__).resolve().parents[1] / "examples" / "benches" / "awg_rc_scope.bench.yaml"
VENDOR = 0x5342  # "SB"


def _read_exact(sock: socket.socket, count: int) -> bytes:
    out = bytearray()
    while len(out) < count:
        chunk = sock.recv(count - len(out))
        if not chunk:
            raise ConnectionError("peer closed")
        out.extend(chunk)
    return bytes(out)


def recv_msg(sock: socket.socket) -> tuple[int, int, int, bytes]:
    prologue, msg_type, control, parameter, length = HEADER.unpack(_read_exact(sock, HEADER.size))
    assert prologue == b"HS"
    return msg_type, control, parameter, _read_exact(sock, length)


def send_msg(sock: socket.socket, msg_type: int, control: int = 0, parameter: int = 0, payload: bytes = b""):
    sock.sendall(HEADER.pack(b"HS", msg_type, control, parameter, len(payload)) + payload)


class Raw:
    """A minimal HiSLIP client: two blocking sockets, no VISA in the way."""

    def __init__(self, port: int, sub: bytes = b"hislip1", timeout: float = 5.0) -> None:
        self.sync = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.sync.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.async_ = None
        self.mid = 0xFFFFFF00
        send_msg(self.sync, INITIALIZE, 0, (1 << 24) | VENDOR, sub)  # version 1.0, like pyvisa-py
        msg_type, control, parameter, _ = recv_msg(self.sync)
        assert (msg_type, control) == (INITIALIZE_RESPONSE, 0)  # synchronized mode only
        self.session_id = parameter & 0xFFFF
        assert parameter >> 16 == 0x0100  # the negotiated version is the lower one
        self.open_async()

    def open_async(self) -> None:
        self.async_ = socket.create_connection(("127.0.0.1", self.sync.getpeername()[1]), timeout=5.0)
        send_msg(self.async_, ASYNC_INITIALIZE, 0, self.session_id)
        assert recv_msg(self.async_)[0] == ASYNC_INITIALIZE_RESPONSE

    def send_command(self, text: str, rmt: int = 0) -> int:
        """One program message as a DataEnd; returns its message ID."""
        mid = self.mid
        self.mid += 2
        send_msg(self.sync, DATA_END, rmt, mid, text.encode())
        return mid

    def read_response(self, sock: socket.socket | None = None) -> tuple[int, bytes]:
        """Collect the Data messages of one response and its final DataEnd."""
        sock = sock or self.sync
        payload = b""
        while True:
            msg_type, _, parameter, part = recv_msg(sock)
            assert msg_type in (DATA, DATA_END)
            payload += part
            if msg_type == DATA_END:
                return parameter, payload

    def query(self, text: str) -> bytes:
        self.send_command(text)
        _, payload = self.read_response()
        return payload

    def status_query(self, rmt: int = 0) -> int:
        assert self.async_ is not None
        send_msg(self.async_, ASYNC_STATUS_QUERY, rmt, self.mid - 2)
        msg_type, control, _, _ = recv_msg(self.async_)
        assert msg_type == ASYNC_STATUS_RESPONSE
        return control

    def lock(self, *, request: bool, timeout_ms: int = 0, lock_string: bytes = b"") -> int:
        assert self.async_ is not None
        if request:
            send_msg(self.async_, ASYNC_LOCK, 1, timeout_ms, lock_string)
        else:
            send_msg(self.async_, ASYNC_LOCK, 0, self.mid - 2)
        msg_type, control, _, _ = recv_msg(self.async_)
        assert msg_type == ASYNC_LOCK_RESPONSE
        return control

    def close(self) -> None:
        self.sync.close()
        if self.async_:
            self.async_.close()


@pytest.fixture
async def rig():
    bench = open_bench(BENCH)
    server = HislipServer(bench, port=0)
    await server.start()
    yield SimpleNamespace(bench=bench, server=server, port=server.port)
    await server.stop()
    bench.close()


def client(rig, **kwargs) -> Raw:
    return Raw(rig.port, **kwargs)


# ---- sessions and sub-addresses ----------------------------------------------------------------------


async def test_addresses_list_every_instrument_and_alias(rig):
    addresses = rig.server.addresses()
    assert addresses["awg"] == f"TCPIP0::127.0.0.1::hislip0,{rig.port}::INSTR"
    assert addresses["scope"] == f"TCPIP0::127.0.0.1::hislip1,{rig.port}::INSTR"


async def test_query_over_the_wire(rig):
    def run() -> None:
        c = client(rig)
        assert c.query("*IDN?") == b"SIMUBENCH,SB-DSO-4,SBDSO000001,1.0.0\n"
        mid = c.send_command("*OPC?")
        response_mid, payload = c.read_response()
        assert (response_mid, payload) == (mid, b"1\n")  # the response carries the query's ID
        c.close()

    await asyncio.to_thread(run)


async def test_empty_subaddress_opens_the_default_instrument(rig):
    def run() -> None:
        c = client(rig, sub=b"")
        assert b"SB-AWG-2" in c.query("*IDN?")  # the first instrument of the bench
        c.close()

    await asyncio.to_thread(run)


async def test_instance_name_alias(rig):
    def run() -> None:
        c = client(rig, sub=b"hislip_awg")
        assert b"SB-AWG-2" in c.query("*IDN?")
        c.close()

    await asyncio.to_thread(run)


async def test_unknown_subaddress_is_a_fatal_error(rig):
    def run() -> None:
        sock = socket.create_connection(("127.0.0.1", rig.port), timeout=5)
        send_msg(sock, INITIALIZE, 0, 0, b"hislip9")
        msg_type, _control, _, payload = recv_msg(sock)
        assert msg_type == FATAL_ERROR
        assert b"hislip9" in payload
        with pytest.raises(ConnectionError):
            _read_exact(sock, 1)  # the server closes the connection
        sock.close()

    await asyncio.to_thread(run)


async def test_initialize_must_come_first(rig):
    def run() -> None:
        sock = socket.create_connection(("127.0.0.1", rig.port), timeout=5)
        send_msg(sock, DATA_END, 0, 0, b"*IDN?")
        msg_type, *_ = recv_msg(sock)
        assert msg_type == FATAL_ERROR
        sock.close()

    await asyncio.to_thread(run)


async def test_async_initialize_needs_a_known_session(rig):
    def run() -> None:
        sock = socket.create_connection(("127.0.0.1", rig.port), timeout=5)
        send_msg(sock, ASYNC_INITIALIZE, 0, 0xDEAD)  # an invented session ID
        assert recv_msg(sock)[0] == FATAL_ERROR
        sock.close()

    await asyncio.to_thread(run)


# ---- data transfer ------------------------------------------------------------------------------------


async def test_maximum_message_size_splits_the_response(rig):
    def run() -> None:
        c = client(rig, sub=b"hislip1")
        send_msg(c.async_, ASYNC_MAXIMUM_MESSAGE_SIZE, 0, 0, struct.pack("!Q", 1024))
        msg_type, _, _, payload = recv_msg(c.async_)
        assert msg_type == ASYNC_MAXIMUM_MESSAGE_SIZE_RESPONSE
        assert struct.unpack("!Q", payload)[0] >= 1024  # the server's own limit
        c.send_command(":DIGitize")
        mid = c.send_command(":WAVeform:DATA?")
        # 2007 bytes with payloads of at most 1024 - 16: one Data, then the final DataEnd.
        first = recv_msg(c.sync)
        assert first[0] == DATA and first[2] == mid and len(first[3]) == 1008
        second = recv_msg(c.sync)
        assert second[0] == DATA_END and second[2] == mid and len(second[3]) == 999
        whole = first[3] + second[3]
        assert len(whole) == 2007 and whole[:6] == b"#42000"
        c.close()

    await asyncio.to_thread(run)


async def test_interrupted_query(rig):
    """A query sent before the previous response was read: the old one is dropped (spec 3.1.1)."""

    def run() -> None:
        c = client(rig)
        first = c.send_command("*IDN?")
        second = c.send_command("*OPC?")
        assert second == first + 2
        msg_type, _, parameter, _ = recv_msg(c.async_)
        assert (msg_type, parameter) == (ASYNC_INTERRUPTED, second)  # AsyncInterrupted first (6.11)
        msg_type, _, parameter, payload = recv_msg(c.sync)
        assert (msg_type, parameter) == (INTERRUPTED, second)
        mid, payload = c.read_response()
        assert (mid, payload) == (second, b"1\n")  # only the second query is answered
        c.close()

    await asyncio.to_thread(run)


# ---- status, SRQ ----------------------------------------------------------------------------------------


async def test_status_query_reports_mav_until_rmt_is_delivered(rig):
    def run() -> None:
        c = client(rig)
        c.send_command("*IDN?")
        c.read_response()  # response fully read, but RMT-delivered not yet signalled
        assert c.status_query() & 0x10  # MAV: a response is available from the server's view
        assert c.status_query(1) & 0x10 == 0  # the RMT-delivered flag clears MAV (spec 6.14.1)
        c.close()

    await asyncio.to_thread(run)


async def test_service_request_when_rqs_rises(rig):
    def run() -> None:
        c = client(rig)
        c.send_command("*CLS;*ESE 1;*SRE 32")
        c.query("*OPC?")  # nothing pending; read it so the channel is clean
        c.send_command("*OPC")  # ESR bit 0 -> ESB with SRE 32 -> RQS rises
        msg_type, control, _, _ = recv_msg(c.async_)
        assert msg_type == ASYNC_SERVICE_REQUEST
        assert control == 0x60  # ESB | RQS
        assert c.status_query() == 0x70  # MAV too: the *OPC? reply was never confirmed as read
        assert c.status_query(1) == 0x60  # RMT-delivered clears MAV (spec 6.14.1)
        c.close()

    await asyncio.to_thread(run)


# ---- device clear ----------------------------------------------------------------------------------------


async def test_device_clear_handshake_and_input_discipline(rig):
    def run() -> None:
        c = client(rig)
        assert c.query("*IDN?").startswith(b"SIMUBENCH")
        send_msg(c.async_, ASYNC_DEVICE_CLEAR)
        msg_type, _, _, _ = recv_msg(c.async_)
        assert msg_type == ASYNC_DEVICE_CLEAR_ACKNOWLEDGE
        c.send_command("*IDN?")  # stray input while clearing: disregarded (spec 6.12)
        send_msg(c.sync, DEVICE_CLEAR_COMPLETE, 0)
        msg_type, _, _, _ = recv_msg(c.sync)
        assert msg_type == DEVICE_CLEAR_ACKNOWLEDGE
        assert c.status_query() & 0x10 == 0  # the cleared output queue leaves no MAV
        assert c.query("*OPC?") == b"1\n"  # normal operation resumes
        c.close()

    await asyncio.to_thread(run)


# ---- trigger, locks ----------------------------------------------------------------------------------------


async def test_trigger_is_the_common_trigger_command(rig):
    def run() -> None:
        c = client(rig, sub=b"hislip0")
        send_msg(c.sync, TRIGGER, 0, c.mid)
        c.mid += 2
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:  # the write happens off the event loop
            if any(
                e["kind"] == "scpi" and e["dir"] == "write" and e["data"] == b"*TRG"
                for e in rig.bench.history
            ):
                c.close()
                return
            time.sleep(0.02)
        c.close()
        raise AssertionError("*TRG never reached the instrument")

    await asyncio.to_thread(run)


async def test_exclusive_lock_holds_another_sessions_writes(rig):
    def run() -> None:
        a = client(rig)
        b = client(rig)
        assert a.lock(request=True, timeout_ms=0) == 1  # granted
        b.send_command("*IDN?")
        b.sync.settimeout(0.3)
        with pytest.raises(socket.timeout):
            recv_msg(b.sync)  # the held session gets no response (spec 2.6.1)
        b.sync.settimeout(5)
        started = time.monotonic()
        assert b.lock(request=True, timeout_ms=300) == 0  # failure after the timeout
        assert time.monotonic() - started >= 0.25
        assert a.lock(request=False) == 1  # released an exclusive lock
        _, payload = b.read_response()  # the held query completes once the lock is gone
        assert payload.startswith(b"SIMUBENCH")
        b.sync.settimeout(0.3)
        with pytest.raises(socket.timeout):
            recv_msg(b.sync)  # and nothing else was queued behind it
        a.close()
        b.close()

    await asyncio.to_thread(run)


async def test_shared_locks_and_exclusive_refusal(rig):
    def run() -> None:
        a, b, c = client(rig), client(rig), client(rig)
        assert a.lock(request=True, timeout_ms=0, lock_string=b"team") == 1
        assert b.lock(request=True, timeout_ms=0, lock_string=b"team") == 1  # shared locks compose
        send_msg(c.async_, ASYNC_LOCK_INFO)
        msg_type, control, holders, _ = recv_msg(c.async_)
        assert (msg_type, control, holders) == (ASYNC_LOCK_INFO_RESPONSE, 0, 2)
        assert c.lock(request=True, timeout_ms=0) == 0  # exclusive fails while shared locks exist
        assert a.lock(request=False) == 2  # released a shared lock
        a.close()
        b.close()
        c.close()

    await asyncio.to_thread(run)


async def test_locks_are_released_when_the_connection_drops(rig):
    def run() -> None:
        a = client(rig)
        assert a.lock(request=True, timeout_ms=0) == 1
        b = client(rig)
        b.send_command("*IDN?")
        a.close()  # both channels gone: the server releases the lock at once (spec 2.6)
        _, payload = b.read_response()
        assert payload.startswith(b"SIMUBENCH")
        assert b.lock(request=True, timeout_ms=0) == 1  # and the next session can take it
        b.close()

    await asyncio.to_thread(run)


# ---- the in-process PyVISA backend resolves HiSLIP addresses too -------------------------------------


def test_in_process_backend_opens_hislip_addresses(tmp_path):
    """``@simubench`` maps HiSLIP-shaped addresses onto the same in-process instruments (D41)."""
    import pyvisa
    from pyvisa import constants

    bench = tmp_path / "hislip.bench.yaml"  # a fresh file: one backend per bench path
    bench.write_bytes(BENCH.read_bytes())
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        assert "TCPIP0::127.0.0.1::hislip1,4880::INSTR" in rm.list_resources("?*::INSTR")
        scope = rm.open_resource(
            "TCPIP0::127.0.0.1::hislip1,4880::INSTR", read_termination="\n", write_termination="\n"
        )
        assert scope.query("*IDN?").startswith("SIMUBENCH,SB-DSO-4")
        by_alias = rm.open_resource(
            "TCPIP0::127.0.0.1::hislip_awg::INSTR", read_termination="\n", write_termination="\n"
        )
        assert "SB-AWG-2" in by_alias.query("*IDN?")
        assert scope.get_visa_attribute(constants.VI_ATTR_TCPIP_IS_HISLIP) == constants.VI_TRUE
        assert scope.get_visa_attribute(constants.VI_ATTR_TCPIP_ADDR) == "127.0.0.1"
        assert scope.get_visa_attribute(constants.VI_ATTR_TCPIP_DEVICE_NAME) == "hislip1,4880"
    finally:
        rm.close()


# ---- protocol errors and faults ---------------------------------------------------------------------------


async def test_unknown_message_type_is_recoverable(rig):
    def run() -> None:
        c = client(rig)
        send_msg(c.sync, 100)  # reserved for future use
        msg_type, control, _, _payload = recv_msg(c.sync)
        assert (msg_type, control) == (ERROR, 1)  # "unrecognized message type" (spec 6.3)
        assert c.query("*OPC?") == b"1\n"  # the connection survives a non-fatal error
        c.close()

    await asyncio.to_thread(run)


async def test_disconnect_fault_closes_both_channels(rig):
    def run() -> None:
        c = client(rig)
        fault = rig.bench.faults.inject(Fault(kind="disconnect", instrument="scope", duration_s=1.0))
        c.send_command("*IDN?")
        for sock in (c.sync, c.async_):
            with pytest.raises((ConnectionError, socket.timeout)):
                _read_exact(sock, 1)  # the link is cut, like a pulled cable
        rig.bench.advance(1.0)  # the fault lasts one second of virtual time
        rig.bench.faults.clear(fault.id)
        c2 = client(rig)  # a new session works again
        assert c2.query("*OPC?") == b"1\n"
        c2.close()
        c.close()

    await asyncio.to_thread(run)


async def test_timeout_fault_is_silence(rig):
    def run() -> None:
        c = client(rig)
        fault = rig.bench.faults.inject(Fault(kind="timeout", instrument="scope"))
        c.send_command("*IDN?")
        c.sync.settimeout(0.4)
        with pytest.raises(socket.timeout):
            recv_msg(c.sync)  # nothing arrives; the message never reached the instrument
        c.sync.settimeout(5)
        rig.bench.faults.clear(fault.id)
        assert c.query("*OPC?") == b"1\n"
        c.close()

    await asyncio.to_thread(run)
