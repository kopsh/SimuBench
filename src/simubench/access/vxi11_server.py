"""VXI-11 network instruments (VXIbus VXI-11 rev 1.0 over ONC RPC/TCP, D62, architecture.md 7.4).

Every instrument of a bench is reachable through one core channel, selected by the device name of
``create_link``: ``inst<i>`` (bench order), ``inst_<instance name>``, or — for instruments whose
bench ``address:`` is a GPIB one — ``gpib<board>,<primary>[,<secondary>]``, the form a LAN/GPIB
gateway uses. Clients connect with ``TCPIP0::127.0.0.1::inst0::INSTR``.

The abort channel (``device_abort``) is a second listener whose port ``create_link`` returns. The
interrupt channel (SRQ) is a connection the server opens back to the client. Locks, REN/GTL and
the status byte are the ones HiSLIP sessions to the same instrument see (bus.py). A port mapper
(RFC 1833, GETPORT only) is optional: VISA libraries ask it for the core port, and it must listen
on port 111 for them to find it, which is a privileged port that rpcbind often holds; without it,
clients that accept ``host,port`` (pyvisa-py) still connect.

Only TCP is served, only on the loopback address, and ``device_docmd`` (GPIB bus commands through a
gateway) answers ``operation not supported``. A ``disconnect`` link fault closes the connection;
a ``timeout`` fault leaves the query unanswered, which the client sees as an I/O timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pyvisa import rname

from ..engine.faults import LinkDown
from ..engine.session import Session
from .bus import bus_of

log = logging.getLogger("simubench.vxi11")

# ---- ONC RPC (RFC 5531) ---------------------------------------------------------------------------
CALL, REPLY = 0, 1
MSG_ACCEPTED, MSG_DENIED = 0, 1
SUCCESS, PROG_UNAVAIL, PROG_MISMATCH, PROC_UNAVAIL, GARBAGE_ARGS = 0, 1, 2, 3, 4
RPC_MISMATCH = 0
LAST_FRAGMENT = 0x8000_0000
MAX_RECORD = 64 << 20  # a length beyond this is a broken record mark, not a message

PMAP_PROG, PMAP_VERS, PMAP_GETPORT = 100000, 2, 3
IPPROTO_TCP = 6

# ---- VXI-11 ---------------------------------------------------------------------------------------
DEVICE_ASYNC_PROG = 0x0607B0
DEVICE_CORE_PROG = 0x0607AF
DEVICE_INTR_PROG = 0x0607B1
VXI11_VERS = 1

CREATE_LINK, DEVICE_WRITE, DEVICE_READ, DEVICE_READSTB, DEVICE_TRIGGER = 10, 11, 12, 13, 14
DEVICE_CLEAR, DEVICE_REMOTE, DEVICE_LOCAL, DEVICE_LOCK, DEVICE_UNLOCK = 15, 16, 17, 18, 19
DEVICE_ENABLE_SRQ, DEVICE_DOCMD, DESTROY_LINK, CREATE_INTR_CHAN, DESTROY_INTR_CHAN = 20, 22, 23, 25, 26
DEVICE_ABORT, DEVICE_INTR_SRQ = 1, 30

# Device_ErrorCode (table B.2 of the specification).
OK = 0
E_SYNTAX = 1
E_NOT_ACCESSIBLE = 3
E_INVALID_LINK = 4
E_PARAMETER = 5
E_NO_CHANNEL = 6
E_NOT_SUPPORTED = 8
E_LOCKED = 11
E_NO_LOCK = 12
E_TIMEOUT = 15
E_ABORT = 23
E_CHANNEL_EXISTS = 29

FLAG_WAITLOCK, FLAG_END, FLAG_TERMCHAR = 1, 8, 128
RX_REQCNT, RX_CHR, RX_END = 1, 2, 4
MAX_RECV_SIZE = 1 << 20  # what create_link advertises: the biggest block a write may carry
RQS, MAV = 0x40, 0x10


class _Garbage(Exception):
    """The arguments of a call do not decode (GARBAGE_ARGS)."""


class _Reader:
    """XDR decoding (RFC 4506) of the few types VXI-11 uses."""

    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def _take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise _Garbage
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def uint(self) -> int:
        return struct.unpack("!I", self._take(4))[0]

    def int(self) -> int:
        return struct.unpack("!i", self._take(4))[0]

    def bool(self) -> bool:
        return self.uint() != 0

    def opaque(self) -> bytes:
        n = self.uint()
        out = self._take(n)
        self._take(-n % 4)
        return out


def _u(*values: int) -> bytes:
    return b"".join(struct.pack("!I", v & 0xFFFF_FFFF) for v in values)


def _opaque(data: bytes) -> bytes:
    return _u(len(data)) + data + b"\0" * (-len(data) % 4)


def _error(code: int) -> bytes:
    return _u(code)


Handler = Callable[["_Conn", _Reader], Awaitable[bytes]]


@dataclass(eq=False)
class _Conn:
    """One TCP connection of an RPC server: the core channel's per-client state lives here."""

    writer: asyncio.StreamWriter
    host: str
    port: int
    #: The interrupt channel the client asked for with create_intr_chan: (host, port, prog, vers).
    intr: tuple[str, int, int, int] | None = None
    intr_writer: asyncio.StreamWriter | None = None
    links: set[_Link] = field(default_factory=set)


@dataclass(eq=False)
class _Link:
    """A VXI-11 link: one client's connection to one instrument. Hashable by identity (locks)."""

    id: int
    instrument: str
    conn: _Conn
    client: str
    inbuf: bytearray = field(default_factory=bytearray)  # a program message not yet ended
    current: bytearray = field(default_factory=bytearray)  # the response being read
    pending: list[bytes] = field(default_factory=list)  # responses not yet started
    srq_enabled: bool = False
    srq_handle: bytes = b""
    last_stb: int = 0

    def has_output(self) -> bool:
        return bool(self.current or self.pending)

    def drop_output(self) -> None:
        self.current.clear()
        self.pending.clear()


class _RpcServer:
    """ONC RPC over TCP with record marking, serving a table of programs."""

    def __init__(self, host: str, port: int, programs: dict[int, tuple[int, dict[int, Handler]]]) -> None:
        self.host, self.port = host, port
        self.programs = programs
        self.server: asyncio.AbstractServer | None = None
        self.conns: set[_Conn] = set()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, self.host, self.port)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for conn in list(self.conns):
            conn.writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        host, port = writer.get_extra_info("peername")[:2]
        conn = _Conn(writer, host, port)
        self.conns.add(conn)
        try:
            while True:
                record = await self._read_record(reader)
                if record is None:
                    break
                reply = await self._call(conn, record)
                if reply is not None:
                    self._write_record(writer, reply)
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except LinkDown:
            log.info("VXI-11: link fault, closing %s:%s", host, port)
        finally:
            self.conns.discard(conn)
            await self.on_close(conn)
            if conn.intr_writer is not None:
                conn.intr_writer.close()
            writer.close()

    async def on_close(self, conn: _Conn) -> None:
        """A connection is gone: whatever hangs on it is released (overridden by the core server)."""

    @staticmethod
    async def _read_record(reader: asyncio.StreamReader) -> bytes | None:
        chunks: list[bytes] = []
        total = 0
        while True:
            (mark,) = struct.unpack("!I", await reader.readexactly(4))
            size = mark & ~LAST_FRAGMENT
            total += size
            if total > MAX_RECORD:
                return None
            chunks.append(await reader.readexactly(size))
            if mark & LAST_FRAGMENT:
                return b"".join(chunks)

    @staticmethod
    def _write_record(writer: asyncio.StreamWriter, data: bytes) -> None:
        writer.write(struct.pack("!I", LAST_FRAGMENT | len(data)) + data)

    async def _call(self, conn: _Conn, record: bytes) -> bytes | None:
        rd = _Reader(record)
        try:
            xid, kind, rpcvers, prog, vers, proc = (rd.uint() for _ in range(6))
            for _ in range(2):  # credential and verifier: flavor and body, both ignored
                rd.uint()
                rd.opaque()
        except _Garbage:
            return None  # not even a call header: nothing to answer
        if kind != CALL:
            return None
        if rpcvers != 2:
            return _u(xid, REPLY, MSG_DENIED, RPC_MISMATCH, 2, 2)
        head = _u(xid, REPLY, MSG_ACCEPTED, 0, 0)  # AUTH_NONE verifier
        entry = self.programs.get(prog)
        if entry is None:
            return head + _u(PROG_UNAVAIL)
        version, procs = entry
        if vers != version:
            return head + _u(PROG_MISMATCH, version, version)
        handler = procs.get(proc)
        if handler is None:
            return head + _u(PROC_UNAVAIL)
        try:
            return head + _u(SUCCESS) + await handler(conn, rd)
        except _Garbage:
            return head + _u(GARBAGE_ARGS)


class Vxi11Server(_RpcServer):
    """Every instrument of a bench on one VXI-11 core channel, plus abort channel and port mapper."""

    def __init__(
        self,
        session: Session,
        host: str = "127.0.0.1",
        port: int = 0,
        portmap_port: int | None = None,
    ) -> None:
        super().__init__(
            host,
            port,
            {
                DEVICE_CORE_PROG: (
                    VXI11_VERS,
                    {
                        0: self._null,
                        CREATE_LINK: self._create_link,
                        DEVICE_WRITE: self._device_write,
                        DEVICE_READ: self._device_read,
                        DEVICE_READSTB: self._device_readstb,
                        DEVICE_TRIGGER: self._device_trigger,
                        DEVICE_CLEAR: self._device_clear,
                        DEVICE_REMOTE: self._device_remote,
                        DEVICE_LOCAL: self._device_local,
                        DEVICE_LOCK: self._device_lock,
                        DEVICE_UNLOCK: self._device_unlock,
                        DEVICE_ENABLE_SRQ: self._device_enable_srq,
                        DEVICE_DOCMD: self._device_docmd,
                        DESTROY_LINK: self._destroy_link,
                        CREATE_INTR_CHAN: self._create_intr_chan,
                        DESTROY_INTR_CHAN: self._destroy_intr_chan,
                    },
                )
            },
        )
        self.session = session
        self.abort = _RpcServer(
            host, 0, {DEVICE_ASYNC_PROG: (VXI11_VERS, {0: self._null, DEVICE_ABORT: self._abort})}
        )
        self.portmapper: _RpcServer | None = (
            _RpcServer(
                host, portmap_port, {PMAP_PROG: (PMAP_VERS, {0: self._null, PMAP_GETPORT: self._getport})}
            )
            if portmap_port is not None
            else None
        )
        self._subaddresses: dict[str, str] = {}
        self._gpib: dict[tuple[int, int, int | None], str] = {}
        self._links: dict[int, _Link] = {}
        self._next_link = 1
        self._tasks: set[asyncio.Task] = set()

    # ---- lifecycle ----------------------------------------------------------------------------------

    async def start(self) -> None:
        for i, (name, inst) in enumerate(self.session.instruments.items()):
            self._subaddresses[f"inst{i}"] = name
            self._subaddresses[f"inst_{name}".lower()] = name
            with contextlib.suppress(rname.InvalidResourceName):
                parsed = rname.parse_resource_name(inst.instance.address or "")
                if isinstance(parsed, rname.GPIBInstr):
                    sad = int(parsed.secondary_address) if parsed.secondary_address is not None else None
                    self._gpib[(int(parsed.board), int(parsed.primary_address), sad)] = name
        await super().start()
        await self.abort.start()
        if self.portmapper is not None:
            try:
                await self.portmapper.start()
            except OSError as exc:  # port 111 is privileged and often held by rpcbind
                log.warning(
                    "VXI-11 port mapper not started (port %s: %s); connect with TCPIP0::%s,%d::inst0::INSTR",
                    self.portmapper.port,
                    exc,
                    self.host,
                    self.port,
                )
                self.portmapper = None
        for sub, name in self._subaddresses.items():
            if not sub.startswith("inst_"):
                log.info("VXI-11 %s at %s on port %d", name, sub, self.port)

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self.portmapper is not None:
            await self.portmapper.stop()
        await self.abort.stop()
        await super().stop()

    def addresses(self) -> dict[str, str]:
        """Instrument -> VXI-11 resource address. Without a port mapper the core port is spelled out."""
        where = self.host if self.portmapper is not None else f"{self.host},{self.port}"
        out: dict[str, str] = {}
        for sub, name in self._subaddresses.items():
            out.setdefault(name, f"TCPIP0::{where}::{sub}::INSTR")
        return out

    def locks(self, instrument: str):
        return bus_of(self.session, instrument).locks

    def clients(self, instrument: str | None = None) -> list[str]:
        return sorted(
            link.client
            for link in self._links.values()
            if instrument is None or link.instrument == instrument
        )

    def holds_lock(self, client: str) -> bool:
        return any(
            link.client == client and self.locks(link.instrument).held_by(link)
            for link in self._links.values()
        )

    async def on_close(self, conn: _Conn) -> None:
        for link in list(conn.links):
            self._drop(link)

    def _drop(self, link: _Link) -> None:
        self.locks(link.instrument).release(link)
        self._links.pop(link.id, None)
        link.conn.links.discard(link)

    def _find(self, link_id: int) -> _Link | None:
        return self._links.get(link_id)

    def _lookup(self, device: str) -> str | None:
        name = device.strip().lower()
        if name in self._subaddresses:
            return self._subaddresses[name]
        m = re.fullmatch(r"gpib(\d*),(\d+)(?:,(\d+))?", name)
        if m:
            key = (int(m.group(1) or 0), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
            return self._gpib.get(key)
        return None

    # ---- engine passthrough (blocking engine calls run off the event loop) -------------------------

    async def _engine(self, op: Callable, *args):
        return await asyncio.to_thread(op, *args)

    def _exchange(self, instrument: str, data: bytes, client: str) -> list[bytes]:
        return self.session.exchange(instrument, data, client=client)

    def _write(self, instrument: str, data: bytes, client: str) -> None:
        self.session.write(instrument, data, client=client)

    def _clear_output(self, instrument: str, client: str) -> None:
        self.session.clear_output(instrument, client=client)

    # ---- helpers ---------------------------------------------------------------------------------------

    async def _null(self, conn: _Conn, rd: _Reader) -> bytes:
        return b""

    async def _wait_lock(self, link: _Link, lock_timeout_ms: int) -> bool:
        """Wait up to ``lock_timeout`` for the lock holders to let go (specification 2.3)."""
        locks = self.locks(link.instrument)
        deadline = asyncio.get_running_loop().time() + lock_timeout_ms / 1000
        while not locks.accessible(link):
            if asyncio.get_running_loop().time() >= deadline:
                return False
            locks.released.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(locks.released.wait(), timeout=0.02)
        return True

    def _stb(self, link: _Link) -> int:
        stb = self.session.instrument(link.instrument).stb()
        return (stb & ~MAV) | (MAV if link.has_output() else 0)

    def _poll_srq(self, instrument: str) -> None:
        """The RQS bit rose: tell every link that enabled SRQ (device_intr_srq), once per new reason."""
        try:
            stb = self.session.instrument(instrument).stb()
        except Exception:  # a status read must never take the connection down
            return
        for link in list(self._links.values()):
            if link.instrument != instrument:
                continue
            if link.srq_enabled and stb & RQS and not link.last_stb & RQS:
                task = asyncio.create_task(self._send_srq(link))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            link.last_stb = stb

    async def _send_srq(self, link: _Link) -> None:
        conn = link.conn
        if conn.intr is None:
            return
        host, port, prog, vers = conn.intr
        try:
            if conn.intr_writer is None or conn.intr_writer.is_closing():
                _, conn.intr_writer = await asyncio.wait_for(asyncio.open_connection(host, port), 2.0)
            call = _u(0x5342_0000 + link.id, CALL, 2, prog, vers, DEVICE_INTR_SRQ, 0, 0, 0, 0)
            self._write_record(conn.intr_writer, call + _opaque(link.srq_handle))
            await conn.intr_writer.drain()
        except (OSError, TimeoutError):
            log.info("VXI-11: SRQ for %s could not be delivered to %s:%s", link.client, host, port)

    async def _guard(self, link: _Link, lock_timeout: int) -> bool:
        return await self._wait_lock(link, lock_timeout)

    # ---- core channel ----------------------------------------------------------------------------------

    async def _create_link(self, conn: _Conn, rd: _Reader) -> bytes:
        client_id, lock_device, lock_timeout, device = rd.int(), rd.bool(), rd.uint(), rd.opaque()
        name = self._lookup(device.decode("ascii", errors="replace"))
        if name is None:
            return _u(E_NOT_ACCESSIBLE, 0, self.abort.port, MAX_RECV_SIZE)
        link = _Link(self._next_link, name, conn, f"vxi11:{conn.host}:{conn.port}#{self._next_link:04x}")
        self._next_link += 1
        self._links[link.id] = link
        conn.links.add(link)
        if (
            lock_device
            and not self.locks(name).grant(link, True)
            and (not await self._wait_lock(link, lock_timeout) or not self.locks(name).grant(link, True))
        ):
            self._drop(link)
            return _u(E_LOCKED, 0, self.abort.port, MAX_RECV_SIZE)
        log.info("VXI-11 %s: %s linked (client id %d)", name, link.client, client_id)
        return _u(OK, link.id, self.abort.port, MAX_RECV_SIZE)

    async def _device_write(self, conn: _Conn, rd: _Reader) -> bytes:
        link_id, _io_timeout, lock_timeout, flags, data = (
            rd.int(),
            rd.uint(),
            rd.uint(),
            rd.int(),
            rd.opaque(),
        )
        link = self._find(link_id)
        if link is None:
            return _u(E_INVALID_LINK, 0)
        if not await self._guard(link, lock_timeout):
            return _u(E_LOCKED, 0)
        if not link.inbuf:
            link.drop_output()  # a new message abandons a response nobody read (as HiSLIP's interrupted)
        link.inbuf.extend(data)
        if flags & FLAG_END:
            message, link.inbuf = bytes(link.inbuf), bytearray()
            outs = await self._engine(self._exchange, link.instrument, message, link.client)
            link.pending.extend(outs)
            self._poll_srq(link.instrument)
        return _u(OK, len(data))

    async def _device_read(self, conn: _Conn, rd: _Reader) -> bytes:
        link_id, request_size, _io_timeout, lock_timeout, flags, term_char = (
            rd.int(),
            rd.uint(),
            rd.uint(),
            rd.uint(),
            rd.int(),
            rd.int(),
        )
        link = self._find(link_id)
        if link is None:
            return _u(E_INVALID_LINK, 0) + _opaque(b"")
        if not await self._guard(link, lock_timeout):
            return _u(E_LOCKED, 0) + _opaque(b"")
        if not link.current and link.pending:
            link.current.extend(link.pending.pop(0))
        if not link.current:
            return _u(E_TIMEOUT, 0) + _opaque(b"")  # nothing queued: the client's I/O wait runs out
        end = min(request_size, len(link.current))
        reason = 0
        if flags & FLAG_TERMCHAR:
            stop = link.current.find(bytes([term_char & 0xFF]), 0, end)
            if stop != -1:
                end, reason = stop + 1, RX_CHR
        out = bytes(link.current[:end])
        del link.current[:end]
        if not link.current:
            reason |= RX_END
        elif end == request_size and not reason:
            reason = RX_REQCNT
        return _u(OK, reason) + _opaque(out)

    async def _device_readstb(self, conn: _Conn, rd: _Reader) -> bytes:
        link_id, _flags, lock_timeout, _io_timeout = rd.int(), rd.int(), rd.uint(), rd.uint()
        link = self._find(link_id)
        if link is None:
            return _u(E_INVALID_LINK, 0)
        if not await self._guard(link, lock_timeout):
            return _u(E_LOCKED, 0)
        return _u(OK, self._stb(link) & 0xFF)

    async def _device_trigger(self, conn: _Conn, rd: _Reader) -> bytes:
        link_id, _flags, lock_timeout, _io_timeout = rd.int(), rd.int(), rd.uint(), rd.uint()
        link = self._find(link_id)
        if link is None:
            return _error(E_INVALID_LINK)
        if not await self._guard(link, lock_timeout):
            return _error(E_LOCKED)
        await self._engine(self._write, link.instrument, b"*TRG", link.client)
        self._poll_srq(link.instrument)
        return _error(OK)

    async def _device_clear(self, conn: _Conn, rd: _Reader) -> bytes:
        link_id, _flags, lock_timeout, _io_timeout = rd.int(), rd.int(), rd.uint(), rd.uint()
        link = self._find(link_id)
        if link is None:
            return _error(E_INVALID_LINK)
        if not await self._guard(link, lock_timeout):
            return _error(E_LOCKED)
        link.inbuf.clear()
        link.drop_output()
        await self._engine(self._clear_output, link.instrument, link.client)
        return _error(OK)

    async def _remote_local(self, rd: _Reader, code: int) -> bytes:
        link_id, _flags, lock_timeout, _io_timeout = rd.int(), rd.int(), rd.uint(), rd.uint()
        link = self._find(link_id)
        if link is None:
            return _error(E_INVALID_LINK)
        if not await self._guard(link, lock_timeout):
            return _error(E_LOCKED)
        action = bus_of(self.session, link.instrument).remote_local.control(code)
        self.session.emit(
            {"kind": "bus_control", "instrument": link.instrument, "client": link.client, "action": action}
        )
        return _error(OK)

    async def _device_remote(self, conn: _Conn, rd: _Reader) -> bytes:
        return await self._remote_local(rd, 3)  # REN asserted and the device addressed to listen

    async def _device_local(self, conn: _Conn, rd: _Reader) -> bytes:
        return await self._remote_local(rd, 6)  # GTL

    async def _device_lock(self, conn: _Conn, rd: _Reader) -> bytes:
        link_id, flags, lock_timeout = rd.int(), rd.int(), rd.uint()
        link = self._find(link_id)
        if link is None:
            return _error(E_INVALID_LINK)
        locks = self.locks(link.instrument)
        if locks.grant(link, True):
            return _error(OK)
        if flags & FLAG_WAITLOCK:
            deadline = asyncio.get_running_loop().time() + lock_timeout / 1000
            while asyncio.get_running_loop().time() < deadline:
                locks.released.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(locks.released.wait(), timeout=0.02)
                if locks.grant(link, True):
                    return _error(OK)
        return _error(E_LOCKED)

    async def _device_unlock(self, conn: _Conn, rd: _Reader) -> bytes:
        link = self._find(rd.int())
        if link is None:
            return _error(E_INVALID_LINK)
        locks = self.locks(link.instrument)
        if locks.exclusive is not link:
            return _error(E_NO_LOCK)
        locks.release(link)
        return _error(OK)

    async def _device_enable_srq(self, conn: _Conn, rd: _Reader) -> bytes:
        link_id, enable, handle = rd.int(), rd.bool(), rd.opaque()
        link = self._find(link_id)
        if link is None:
            return _error(E_INVALID_LINK)
        if enable and conn.intr is None:
            return _error(E_NO_CHANNEL)
        if len(handle) > 40:
            return _error(E_PARAMETER)
        link.srq_enabled, link.srq_handle = enable, handle
        link.last_stb = self.session.instrument(link.instrument).stb()  # only a new reason raises SRQ
        return _error(OK)

    async def _device_docmd(self, conn: _Conn, rd: _Reader) -> bytes:
        link = self._find(rd.int())
        if link is None:
            return _error(E_INVALID_LINK) + _opaque(b"")
        # GPIB bus commands through a gateway have no virtual bus behind them: refused, not faked.
        return _error(E_NOT_SUPPORTED) + _opaque(b"")

    async def _destroy_link(self, conn: _Conn, rd: _Reader) -> bytes:
        link = self._find(rd.int())
        if link is None:
            return _error(E_INVALID_LINK)
        self._drop(link)
        return _error(OK)

    async def _create_intr_chan(self, conn: _Conn, rd: _Reader) -> bytes:
        host_addr, host_port, prog, vers, family = rd.uint(), rd.uint(), rd.uint(), rd.uint(), rd.int()
        if conn.intr is not None:
            return _error(E_CHANNEL_EXISTS)
        if family != 0:  # 0 is TCP; UDP is not served
            return _error(E_NOT_SUPPORTED)
        host = ".".join(str(b) for b in host_addr.to_bytes(4, "big"))
        if host != conn.host or not 0 < host_port < 65536:
            return _error(E_PARAMETER)  # the server calls back the client that asked, nobody else
        conn.intr = (host, host_port, prog, vers)
        return _error(OK)

    async def _destroy_intr_chan(self, conn: _Conn, rd: _Reader) -> bytes:
        if conn.intr is None:
            return _error(E_NO_CHANNEL)
        conn.intr = None
        if conn.intr_writer is not None:
            conn.intr_writer.close()
            conn.intr_writer = None
        for link in conn.links:
            link.srq_enabled = False
        return _error(OK)

    # ---- abort channel and port mapper -------------------------------------------------------------------

    async def _abort(self, conn: _Conn, rd: _Reader) -> bytes:
        link = self._find(rd.int())
        if link is None:
            return _error(E_INVALID_LINK)
        link.inbuf.clear()  # whatever the link was doing is dropped; the engine's calls are atomic
        link.drop_output()
        return _error(OK)

    async def _getport(self, conn: _Conn, rd: _Reader) -> bytes:
        prog, vers, proto, _port = rd.uint(), rd.uint(), rd.uint(), rd.uint()
        if proto == IPPROTO_TCP and vers == VXI11_VERS:
            if prog == DEVICE_CORE_PROG:
                return _u(self.port)
            if prog == DEVICE_ASYNC_PROG:
                return _u(self.abort.port)
        return _u(0)  # 0: not registered
