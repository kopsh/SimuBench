"""VXI-11 server (D63): the wire protocol exercised by a minimal raw ONC RPC client.

pyvisa-py's own client covers the VISA level in the last tests; the raw client reaches what a VISA
client cannot see: record marking, RPC error replies, the abort channel, the port mapper, lock
waits and the interrupt channel.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from simubench.access import vxi11_server as v
from simubench.api import open_bench
from simubench.engine.faults import Fault

BENCH = Path(__file__).resolve().parents[1] / "validation" / "benches" / "awg_rc_scope.bench.yaml"
GPIB_BENCH = Path(__file__).resolve().parents[1] / "examples" / "benches" / "fluke-5522a.bench.yaml"


def read_exact(sock: socket.socket, count: int) -> bytes:
    out = bytearray()
    while len(out) < count:
        chunk = sock.recv(count - len(out))
        if not chunk:
            raise ConnectionError("peer closed")
        out.extend(chunk)
    return bytes(out)


def record(sock: socket.socket) -> bytes:
    chunks = []
    while True:
        (mark,) = struct.unpack("!I", read_exact(sock, 4))
        chunks.append(read_exact(sock, mark & 0x7FFF_FFFF))
        if mark & v.LAST_FRAGMENT:
            return b"".join(chunks)


class Rpc:
    """One RPC connection: call a procedure, get the accepted-success result (or the raw reply)."""

    def __init__(self, port: int, prog: int, vers: int = 1) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.prog, self.vers, self.xid = prog, vers, 100

    def raw(self, proc: int, args: bytes = b"", *, prog: int | None = None, vers: int | None = None) -> bytes:
        self.xid += 1
        head = v._u(self.xid, v.CALL, 2, prog or self.prog, vers or self.vers, proc, 0, 0, 0, 0)
        body = head + args
        self.sock.sendall(struct.pack("!I", v.LAST_FRAGMENT | len(body)) + body)
        return record(self.sock)

    def call(self, proc: int, args: bytes = b"") -> v._Reader:
        reply = self.raw(proc, args)
        rd = v._Reader(reply)
        assert rd.uint() == self.xid and rd.uint() == v.REPLY
        assert rd.uint() == v.MSG_ACCEPTED
        rd.uint()
        rd.opaque()  # verifier
        assert rd.uint() == v.SUCCESS
        return rd

    def close(self) -> None:
        self.sock.close()


class Link:
    """A core-channel client with one link to ``inst<i>``."""

    def __init__(self, rig, device: bytes = b"inst0", lock: bool = False, lock_timeout: int = 0) -> None:
        self.core = Rpc(rig.port, v.DEVICE_CORE_PROG)
        rd = self.core.call(v.CREATE_LINK, v._u(1, int(lock), lock_timeout) + v._opaque(device))
        self.error, self.id, self.abort_port, self.max_recv = rd.int(), rd.int(), rd.uint(), rd.uint()

    def generic(self, proc: int, flags: int = 0, lock_timeout: int = 0) -> v._Reader:
        return self.core.call(proc, v._u(self.id, flags, lock_timeout, 1000))

    def write(self, data: bytes, flags: int = v.FLAG_END, lock_timeout: int = 0) -> tuple[int, int]:
        rd = self.core.call(v.DEVICE_WRITE, v._u(self.id, 1000, lock_timeout, flags) + v._opaque(data))
        return rd.int(), rd.uint()

    def read(self, size: int = 1024, flags: int = 0, term: int = 0, lock_timeout: int = 0):
        rd = self.core.call(v.DEVICE_READ, v._u(self.id, size, 1000, lock_timeout, flags, term))
        return rd.int(), rd.int(), rd.opaque()

    def query(self, text: str) -> bytes:
        assert self.write(text.encode() + b"\n")[0] == v.OK
        error, _, data = self.read()
        assert error == v.OK
        return data

    def stb(self) -> int:
        rd = self.generic(v.DEVICE_READSTB)
        assert rd.int() == v.OK
        return rd.uint()

    def close(self) -> None:
        self.core.close()


@pytest.fixture
async def rig():
    bench = open_bench(BENCH)
    server = v.Vxi11Server(bench, port=0, portmap_port=0)
    await server.start()
    yield SimpleNamespace(bench=bench, server=server, port=server.port)
    await server.stop()
    bench.close()


# ---- links and device names ---------------------------------------------------------------------


async def test_create_link_by_index_and_by_instance_name(rig):
    def run() -> None:
        a, b = Link(rig, b"inst0"), Link(rig, b"inst_scope")
        assert a.error == b.error == v.OK and a.id != b.id
        assert a.max_recv == v.MAX_RECV_SIZE and a.abort_port == rig.server.abort.port
        assert b"SB-AWG" in a.query("*IDN?")
        assert b"SB-DSO" in b.query("*IDN?")
        a.close()
        b.close()

    await asyncio.to_thread(run)


async def test_an_unknown_device_name_is_not_accessible(rig):
    def run() -> None:
        link = Link(rig, b"inst99")
        assert link.error == v.E_NOT_ACCESSIBLE
        link.close()
        gpib = Link(rig, b"gpib0,4")  # this bench declares no GPIB address: nothing is invented
        assert gpib.error == v.E_NOT_ACCESSIBLE
        gpib.close()

    await asyncio.to_thread(run)


async def test_addresses_name_the_core_port_when_there_is_no_portmapper(rig):
    pm, rig.server.portmapper = rig.server.portmapper, None
    assert rig.server.addresses()["awg"] == f"TCPIP0::127.0.0.1,{rig.port}::inst0::INSTR"
    rig.server.portmapper = pm


async def test_bare_address_only_when_the_port_mapper_is_on_111(rig):
    assert rig.server.addresses()["awg"] == f"TCPIP0::127.0.0.1,{rig.port}::inst0::INSTR"  # mapper elsewhere
    rig.server.portmapper = SimpleNamespace(port=111)
    assert rig.server.addresses()["awg"] == "TCPIP0::127.0.0.1::inst0::INSTR"
    rig.server.portmapper = None  # the fixture's teardown stops it through the saved reference


async def test_invalid_link_ids_are_refused(rig):
    def run() -> None:
        link = Link(rig)
        rd = link.core.call(v.DEVICE_WRITE, v._u(9999, 1000, 0, v.FLAG_END) + v._opaque(b"*CLS"))
        assert rd.int() == v.E_INVALID_LINK
        assert link.core.call(v.DESTROY_LINK, v._u(link.id)).int() == v.OK
        assert link.core.call(v.DESTROY_LINK, v._u(link.id)).int() == v.E_INVALID_LINK
        link.close()

    await asyncio.to_thread(run)


# ---- write and read ---------------------------------------------------------------------------------


async def test_a_message_is_one_program_message_only_at_end(rig):
    def run() -> None:
        link = Link(rig)
        assert link.write(b"*ID", flags=0) == (v.OK, 3)  # no END: nothing is executed yet
        assert link.read()[0] == v.E_TIMEOUT
        assert link.write(b"N?\n") == (v.OK, 3)
        error, reason, data = link.read()
        assert error == v.OK and reason & v.RX_END and data.startswith(b"SIMUBENCH")
        link.close()

    await asyncio.to_thread(run)


async def test_read_reports_reqcnt_chr_and_end(rig):
    def run() -> None:
        link = Link(rig)
        link.write(b"*IDN?\n")
        total = len(link.query("*IDN?"))  # the same reply again, to know the size
        link.write(b"*IDN?\n")
        error, reason, first = link.read(size=5)
        assert (error, reason, len(first)) == (v.OK, v.RX_REQCNT, 5)  # asked for 5, more is left
        error, reason, rest = link.read(size=1024, flags=v.FLAG_TERMCHAR, term=ord(","))
        assert reason == v.RX_CHR and rest.endswith(b",")  # stopped after the termination character
        error, reason, tail = link.read(size=1024)
        assert reason == v.RX_END and len(first + rest + tail) == total
        link.close()

    await asyncio.to_thread(run)


async def test_queries_joined_by_semicolons_are_one_response_message(rig):
    def run() -> None:
        link = Link(rig)
        link.write(b"*IDN?;*OPC?\n")
        error, reason, data = link.read()
        assert error == v.OK and reason & v.RX_END and data.endswith(b";1\n")
        link.close()

    await asyncio.to_thread(run)


async def test_every_queued_response_message_is_read_in_turn(rig):
    rig.server._exchange = lambda instrument, data, client: [b"first\n", b"second\n"]

    def run() -> None:
        link = Link(rig)
        link.write(b"x\n")
        assert link.read()[1:] == (v.RX_END, b"first\n")
        assert link.stb() & v.MAV  # one response is still waiting
        assert link.read()[1:] == (v.RX_END, b"second\n")
        link.close()

    await asyncio.to_thread(run)


async def test_a_new_message_drops_an_unread_response(rig):
    def run() -> None:
        link = Link(rig)
        link.write(b"*IDN?\n")
        assert link.query("*OPC?") == b"1\n"  # the *IDN? reply was never read; it is gone
        assert link.read()[0] == v.E_TIMEOUT
        link.close()

    await asyncio.to_thread(run)


# ---- status, trigger, clear, remote/local --------------------------------------------------------------


async def test_status_byte_has_mav_while_a_response_is_waiting(rig):
    def run() -> None:
        link = Link(rig)
        link.write(b"*CLS;*ESE 1;*SRE 0\n")
        link.write(b"*OPC?\n")
        assert link.stb() & v.MAV
        link.read()
        assert not link.stb() & v.MAV
        link.close()

    await asyncio.to_thread(run)


async def test_device_clear_drops_pending_output(rig):
    def run() -> None:
        link = Link(rig)
        link.write(b"*IDN?\n")
        assert link.generic(v.DEVICE_CLEAR).int() == v.OK
        assert link.read()[0] == v.E_TIMEOUT
        link.close()

    await asyncio.to_thread(run)


async def test_trigger_is_the_common_trigger_command(rig):
    def run() -> None:
        link = Link(rig)
        assert link.generic(v.DEVICE_TRIGGER).int() == v.OK
        sent = [e for e in rig.bench.history if e["kind"] == "scpi" and e.get("data") == b"*TRG"]
        assert sent and sent[-1]["client"].startswith("vxi11:")
        link.close()

    await asyncio.to_thread(run)


async def test_remote_and_local_share_the_bus_state_with_other_interfaces(rig):
    def run() -> None:
        link = Link(rig)
        assert link.generic(v.DEVICE_REMOTE).int() == v.OK
        state = rig.server.session  # the same registry HiSLIP and PyVISA sessions use
        from simubench.access.bus import bus_of

        assert bus_of(state, "awg").remote_local.state == "remote"
        assert link.generic(v.DEVICE_LOCAL).int() == v.OK
        assert bus_of(state, "awg").remote_local.state == "local"
        events = [e for e in rig.bench.history if e["kind"] == "bus_control"]
        assert [e["action"] for e in events[-2:]] == ["enable remote and go to remote", "go to local"]
        link.close()

    await asyncio.to_thread(run)


# ---- locks --------------------------------------------------------------------------------------------


async def test_device_lock_holds_another_links_io(rig):
    def run() -> None:
        a, b = Link(rig), Link(rig)
        assert a.core.call(v.DEVICE_LOCK, v._u(a.id, 0, 0)).int() == v.OK
        assert b.write(b"*IDN?\n") == (v.E_LOCKED, 0)  # lock_timeout 0: no waiting
        started = time.monotonic()
        assert b.write(b"*IDN?\n", lock_timeout=300) == (v.E_LOCKED, 0)  # waited, still locked
        assert time.monotonic() - started >= 0.25
        assert b.core.call(v.DEVICE_LOCK, v._u(b.id, 0, 0)).int() == v.E_LOCKED
        assert a.query("*OPC?") == b"1\n"  # the holder is not affected
        assert a.core.call(v.DEVICE_UNLOCK, v._u(a.id)).int() == v.OK
        assert b.query("*OPC?") == b"1\n"
        assert b.core.call(v.DEVICE_UNLOCK, v._u(b.id)).int() == v.E_NO_LOCK
        a.close()
        b.close()

    await asyncio.to_thread(run)


async def test_a_waiting_write_completes_when_the_lock_is_released(rig):
    def run() -> None:
        a, b = Link(rig), Link(rig)
        a.core.call(v.DEVICE_LOCK, v._u(a.id, 0, 0))
        threading.Timer(0.2, lambda: a.core.call(v.DEVICE_UNLOCK, v._u(a.id))).start()
        assert b.write(b"*OPC?\n", lock_timeout=3000)[0] == v.OK
        assert b.read()[2] == b"1\n"
        a.close()
        b.close()

    await asyncio.to_thread(run)


async def test_create_link_can_take_the_lock(rig):
    def run() -> None:
        a = Link(rig, lock=True)
        assert a.error == v.OK
        b = Link(rig, lock=True, lock_timeout=0)
        assert b.error == v.E_LOCKED
        a.close()
        b.close()

    await asyncio.to_thread(run)


async def test_a_dropped_connection_releases_its_lock(rig):
    def run() -> None:
        a = Link(rig, lock=True)
        b = Link(rig)
        assert b.write(b"*IDN?\n")[0] == v.E_LOCKED
        a.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and b.write(b"*OPC?\n")[0] != v.OK:
            time.sleep(0.05)
        assert b.read()[2] == b"1\n"
        b.close()

    await asyncio.to_thread(run)


async def test_a_lock_is_shared_across_interfaces_of_the_same_instrument(rig):
    """D63: a HiSLIP lock holds a VXI-11 link out, and the other way round: one lock per instrument."""
    from tests.test_hislip import Raw

    from simubench.access.hislip_server import HislipServer

    hislip = HislipServer(rig.bench, port=0)
    await hislip.start()

    def run() -> None:
        h = Raw(hislip.port, sub=b"hislip0")  # hislip0 is the same instrument as inst0
        link = Link(rig)
        assert h.lock(request=True, timeout_ms=0) == 1  # granted
        assert link.write(b"*IDN?\n") == (v.E_LOCKED, 0)  # held out by the HiSLIP lock
        assert h.lock(request=False) == 1  # released
        assert link.write(b"*IDN?\n")[0] == v.OK
        link.read()
        assert link.core.call(v.DEVICE_LOCK, v._u(link.id, 0, 0)).int() == v.OK
        assert h.lock(request=True, timeout_ms=100) == 0  # the VXI-11 lock holds HiSLIP out
        assert link.core.call(v.DEVICE_UNLOCK, v._u(link.id)).int() == v.OK
        assert h.lock(request=True, timeout_ms=1000) == 1
        link.close()
        h.close()

    try:
        await asyncio.to_thread(run)
    finally:
        await hislip.stop()


# ---- abort channel, port mapper, protocol errors --------------------------------------------------------


async def test_abort_channel_drops_the_links_pending_output(rig):
    def run() -> None:
        link = Link(rig)
        link.write(b"*IDN?\n")
        abort = Rpc(link.abort_port, v.DEVICE_ASYNC_PROG)
        assert abort.call(v.DEVICE_ABORT, v._u(link.id)).int() == v.OK
        assert abort.call(v.DEVICE_ABORT, v._u(777)).int() == v.E_INVALID_LINK
        assert link.read()[0] == v.E_TIMEOUT
        abort.close()
        link.close()

    await asyncio.to_thread(run)


async def test_port_mapper_answers_getport(rig):
    def run() -> None:
        pm = Rpc(rig.server.portmapper.port, v.PMAP_PROG, v.PMAP_VERS)
        ask = lambda prog, proto=v.IPPROTO_TCP: pm.call(  # noqa: E731
            v.PMAP_GETPORT, v._u(prog, 1, proto, 0)
        ).uint()
        assert ask(v.DEVICE_CORE_PROG) == rig.port
        assert ask(v.DEVICE_ASYNC_PROG) == rig.server.abort.port
        assert ask(v.DEVICE_CORE_PROG, proto=17) == 0  # UDP is not served
        assert ask(123456) == 0
        pm.close()

    await asyncio.to_thread(run)


async def test_rpc_errors_are_replied_not_dropped(rig):
    def run() -> None:
        rpc = Rpc(rig.port, v.DEVICE_CORE_PROG)

        def accept_stat(reply: bytes) -> int:
            rd = v._Reader(reply)
            for _ in range(3):
                rd.uint()  # xid, REPLY, MSG_ACCEPTED
            rd.uint()
            rd.opaque()
            return rd.uint()

        assert accept_stat(rpc.raw(10, prog=424242)) == v.PROG_UNAVAIL
        assert accept_stat(rpc.raw(10, vers=7)) == v.PROG_MISMATCH
        assert accept_stat(rpc.raw(99)) == v.PROC_UNAVAIL
        assert accept_stat(rpc.raw(v.DEVICE_WRITE, b"\0\0")) == v.GARBAGE_ARGS  # truncated arguments
        assert rpc.call(v.CREATE_LINK, v._u(1, 0, 0) + v._opaque(b"inst0")).int() == v.OK  # still serving
        rpc.close()

    await asyncio.to_thread(run)


async def test_a_record_split_into_fragments_is_reassembled(rig):
    def run() -> None:
        rpc = Rpc(rig.port, v.DEVICE_CORE_PROG)
        body = v._u(5, v.CALL, 2, v.DEVICE_CORE_PROG, 1, 0, 0, 0, 0, 0)  # the null procedure
        rpc.sock.sendall(struct.pack("!I", 12) + body[:12])  # first fragment, not last
        rpc.sock.sendall(struct.pack("!I", v.LAST_FRAGMENT | len(body) - 12) + body[12:])
        assert record(rpc.sock)[:8] == v._u(5, v.REPLY)
        rpc.close()

    await asyncio.to_thread(run)


async def test_docmd_is_refused_not_faked(rig):
    def run() -> None:
        link = Link(rig)
        rd = link.core.call(v.DEVICE_DOCMD, v._u(link.id, 0, 1000, 0, 0x20001, 1, 1) + v._opaque(b"\x3f"))
        assert rd.int() == v.E_NOT_SUPPORTED
        link.close()

    await asyncio.to_thread(run)


# ---- faults --------------------------------------------------------------------------------------------


async def test_disconnect_fault_closes_the_connection(rig):
    def run() -> None:
        link = Link(rig)
        fault = rig.bench.faults.inject(Fault(kind="disconnect", instrument="awg", duration_s=1.0))
        body = (
            v._u(9, v.CALL, 2, v.DEVICE_CORE_PROG, 1, v.DEVICE_WRITE, 0, 0, 0, 0)
            + v._u(link.id, 1000, 0, v.FLAG_END)
            + v._opaque(b"*IDN?")
        )
        link.core.sock.sendall(struct.pack("!I", v.LAST_FRAGMENT | len(body)) + body)
        link.core.sock.settimeout(3)
        with pytest.raises((ConnectionError, socket.timeout)):
            read_exact(link.core.sock, 1)  # the cable is pulled
        rig.bench.advance(1.0)
        rig.bench.faults.clear(fault.id)
        again = Link(rig)
        assert again.query("*OPC?") == b"1\n"
        again.close()

    await asyncio.to_thread(run)


async def test_timeout_fault_is_an_io_timeout(rig):
    def run() -> None:
        link = Link(rig)
        fault = rig.bench.faults.inject(Fault(kind="timeout", instrument="awg"))
        assert link.write(b"*IDN?\n")[0] == v.OK
        assert link.read()[0] == v.E_TIMEOUT  # nothing was queued
        rig.bench.faults.clear(fault.id)
        assert link.query("*OPC?") == b"1\n"
        link.close()

    await asyncio.to_thread(run)


# ---- the interrupt channel (SRQ) --------------------------------------------------------------------------


async def test_srq_is_delivered_on_the_interrupt_channel(rig):
    received: list[bytes] = []
    ready = threading.Event()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve() -> None:
        conn, _ = listener.accept()
        conn.settimeout(5)
        received.append(record(conn))
        ready.set()
        conn.close()

    threading.Thread(target=serve, daemon=True).start()

    def run() -> None:
        link = Link(rig)
        host = int.from_bytes(socket.inet_aton("127.0.0.1"), "big")
        port = listener.getsockname()[1]
        no_chan = link.core.call(v.DEVICE_ENABLE_SRQ, v._u(link.id, 1) + v._opaque(b"h1"))
        assert no_chan.int() == v.E_NO_CHANNEL  # enabling needs the channel first
        spoof = int.from_bytes(socket.inet_aton("10.1.2.3"), "big")
        rd = link.core.call(v.CREATE_INTR_CHAN, v._u(spoof, port, v.DEVICE_INTR_PROG, 1, 0))
        assert rd.int() == v.E_PARAMETER  # the server only calls back the client that asked
        rd = link.core.call(v.CREATE_INTR_CHAN, v._u(host, port, v.DEVICE_INTR_PROG, 1, 0))
        assert rd.int() == v.OK
        again = link.core.call(v.CREATE_INTR_CHAN, v._u(host, port, v.DEVICE_INTR_PROG, 1, 0))
        assert again.int() == v.E_CHANNEL_EXISTS
        assert link.core.call(v.DEVICE_ENABLE_SRQ, v._u(link.id, 1) + v._opaque(b"h1")).int() == v.OK
        link.write(b"*CLS;*ESE 1;*SRE 32\n")
        link.write(b"*OPC\n")  # ESR bit 0 -> ESB with SRE 32 -> RQS rises
        assert ready.wait(5)
        call = v._Reader(received[0])
        assert [call.uint() for _ in range(6)][3:] == [v.DEVICE_INTR_PROG, 1, v.DEVICE_INTR_SRQ]
        for _ in range(4):
            call.uint()  # credential and verifier
        assert call.opaque() == b"h1"  # the handle the client chose
        assert link.core.call(v.DESTROY_INTR_CHAN).int() == v.OK
        link.close()

    await asyncio.to_thread(run)
    listener.close()


# ---- a GPIB gateway address resolves through the bench's declared GPIB address ----------------------------


async def test_gateway_gpib_device_names_follow_the_declared_address():
    bench = open_bench(GPIB_BENCH)
    server = v.Vxi11Server(bench, port=0)
    await server.start()
    try:
        rig = SimpleNamespace(port=server.port)

        def run() -> None:
            link = Link(rig, b"gpib0,4")  # fluke-5522a.bench.yaml: cal at GPIB0::4::INSTR
            assert link.error == v.OK and b"5522A" in link.query("*IDN?")
            link.close()
            assert Link(rig, b"gpib0,5").error == v.E_NOT_ACCESSIBLE

        await asyncio.to_thread(run)
    finally:
        await server.stop()
        bench.close()


# ---- a VISA client: pyvisa-py over VXI-11 -----------------------------------------------------------------


async def test_pyvisa_py_talks_to_the_server(rig):
    import pyvisa

    def run() -> None:
        rm = pyvisa.ResourceManager("@py")
        try:
            scope = rm.open_resource(
                f"TCPIP0::127.0.0.1,{rig.port}::inst1::INSTR", read_termination="\n", write_termination="\n"
            )
            assert scope.query("*IDN?").startswith("SIMUBENCH,SB-DSO-4")
            scope.write("*CLS")
            assert scope.read_stb() & 0x10 == 0
            scope.clear()
            scope.close()
        finally:
            rm.close()

    await asyncio.to_thread(run)
