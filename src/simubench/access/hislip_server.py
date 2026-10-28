"""HiSLIP network instruments (IVI-6.1, D41, architecture.md 7.4).

One shared TCP port serves every instrument; the sub-address selects it (``hislip0``,
``hislip1`` … in bench order, or ``hislip_<instance name>``), so clients connect with
``TCPIP0::127.0.0.1::hislip0,<port>::INSTR``. Only the synchronized mode of the
specification is implemented; ``InitializeResponse`` always prefers it. Encrypted
connections (StartTLS) and authentication are not supported and are refused with Error.

Behaviour follows the IVI-6.1 HiSLIP 2.0 specification (2020-04-23): message types and
transactions as in its tables 3–4, interrupted handling per 3.1.1, device clear per 6.12,
locks per 6.5. A ``disconnect`` link fault closes both channels of the session, like a
cable pulled from a real instrument.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from dataclasses import dataclass, field

from ..engine.faults import LinkDown
from ..engine.session import Session

log = logging.getLogger("simubench.hislip")

HEADER = struct.Struct("!2sBBIQ")  # prologue, type, control code, parameter, payload length
DEFAULT_PORT = 4880
SERVER_VERSION = 0x0100  # the negotiated version is the lower of ours and the client's
SERVER_VENDOR = 0x5342  # "SB" in the VPP-9 two-letter style
MAX_MESSAGE_SIZE = 1 << 20  # what we advertise and accept (the VISA default, 1 MiB)
MAX_PAYLOAD_BUFFER = 64 << 20  # a length field beyond this is a broken header, not a message

# Message types (spec table 4); 26–38 are HiSLIP 2.0-only (descriptors, TLS, authentication).
INITIALIZE = 0
INITIALIZE_RESPONSE = 1
FATAL_ERROR = 2
ERROR = 3
ASYNC_LOCK = 4
ASYNC_LOCK_RESPONSE = 5
DATA = 6
DATA_END = 7
DEVICE_CLEAR_COMPLETE = 8
DEVICE_CLEAR_ACKNOWLEDGE = 9
ASYNC_REMOTE_LOCAL_CONTROL = 10
ASYNC_REMOTE_LOCAL_RESPONSE = 11
TRIGGER = 12
INTERRUPTED = 13
ASYNC_INTERRUPTED = 14
ASYNC_MAXIMUM_MESSAGE_SIZE = 15
ASYNC_MAXIMUM_MESSAGE_SIZE_RESPONSE = 16
ASYNC_INITIALIZE = 17
ASYNC_INITIALIZE_RESPONSE = 18
ASYNC_DEVICE_CLEAR = 19
ASYNC_SERVICE_REQUEST = 20
ASYNC_STATUS_QUERY = 21
ASYNC_STATUS_RESPONSE = 22
ASYNC_DEVICE_CLEAR_ACKNOWLEDGE = 23
ASYNC_LOCK_INFO = 24
ASYNC_LOCK_INFO_RESPONSE = 25

# Error codes (spec tables 14 and 16).
ERR_UNRECOGNIZED_TYPE = 1
FATAL_POORLY_FORMED = 1
FATAL_INVALID_INIT = 3

RMT = 1  # control-code bit 0: RMT-delivered / RMT-expected

REMOTE_LOCAL = {
    0: "disable remote",
    1: "enable remote",
    2: "disable remote and go to local",
    3: "enable remote and go to remote",
    4: "enable remote and lock out local",
    5: "enable remote, go to remote and lock out local",
    6: "go to local",
}


def _pack(msg_type: int, control: int = 0, parameter: int = 0, payload: bytes = b"") -> bytes:
    return HEADER.pack(b"HS", msg_type, control, parameter, len(payload)) + payload


@dataclass
class InstrumentLocks:
    """Locks of one instrument, shared by all of its HiSLIP sessions (spec 2.6, 6.5)."""

    exclusive: HislipSession | None = None
    shared: set[HislipSession] = field(default_factory=set)
    #: Fired whenever a lock is released, so waiters re-check and blocked channels resume.
    released: asyncio.Event = field(default_factory=asyncio.Event)

    def held_by(self, sess: HislipSession) -> bool:
        return sess is self.exclusive or sess in self.shared

    def accessible(self, sess: HislipSession) -> bool:
        """No locks at all, or this session is one of the holders (spec 2.6)."""
        return (self.exclusive is None and not self.shared) or self.held_by(sess)

    def grant(self, sess: HislipSession, exclusive: bool) -> bool:
        if exclusive:
            if self.exclusive is not None or self.shared:
                return False
            self.exclusive = sess
            return True
        if self.exclusive is not None:
            return False
        self.shared.add(sess)
        return True

    def release(self, sess: HislipSession) -> int:
        """Release everything this session holds; returns the AsyncLockResponse code."""
        if sess is self.exclusive:
            self.exclusive = None
            self.released.set()
            return 1  # released an exclusive lock
        if sess in self.shared:
            self.shared.discard(sess)
            self.released.set()
            return 2  # released a shared lock
        return 3  # error: this session held no lock

    def holders(self) -> int:
        return (1 if self.exclusive is not None else 0) + len(self.shared)


class HislipSession:
    """One HiSLIP connection: a synchronous and an asynchronous channel around one session ID."""

    def __init__(self, server: HislipServer, instrument: str, session_id: int) -> None:
        self.server = server
        self.instrument = instrument
        self.session_id = session_id
        self.sync_reader: asyncio.StreamReader | None = None
        self.sync_writer: asyncio.StreamWriter | None = None
        self.async_writer: asyncio.StreamWriter | None = None
        self.client = f"hislip:#session-{session_id:04x}"
        self.max_message_size = MAX_MESSAGE_SIZE  # what the client accepts from us (spec 6.10)
        self.closing = asyncio.Event()
        # Synchronized-mode bookkeeping (spec 3.1.1, 6.14.1): MAV is ours to compute, and the
        # RMT-expected flag detects a client that sends on before reading our response.
        self.mav = False
        self.rmt_expected = False
        self.clearing = False
        self.last_stb = 0

    # ---- sending -------------------------------------------------------------------------------------

    def _send(self, writer: asyncio.StreamWriter | None, message: bytes) -> None:
        if writer is not None and not writer.is_closing():
            writer.write(message)

    async def _drain(self, writer: asyncio.StreamWriter | None) -> None:
        if writer is not None and not writer.is_closing():
            with contextlib.suppress(ConnectionError, asyncio.CancelledError):
                await writer.drain()

    async def send_error(self, writer: asyncio.StreamWriter, code: int, message: bytes) -> None:
        self._send(writer, _pack(ERROR, code, 0, message))
        await self._drain(writer)

    async def send_fatal(self, code: int, message: bytes) -> None:
        """A fatal error goes out on both channels, then the connection closes (spec 6.2)."""
        for writer in (self.sync_writer, self.async_writer):
            self._send(writer, _pack(FATAL_ERROR, code, 0, message))
        for writer in (self.sync_writer, self.async_writer):
            await self._drain(writer)
        await self.close()

    async def close(self) -> None:
        """Close both channels; the locks of this session are released immediately (spec 2.6)."""
        if self.closing.is_set():
            return
        self.closing.set()
        self.server.locks(self.instrument).release(self)
        self.server.forget(self)
        for writer in (self.sync_writer, self.async_writer):
            if writer is not None:
                writer.close()

    # ---- status byte -----------------------------------------------------------------------------------

    def stb(self) -> int:
        """Status byte with MAV by the synchronized-mode rules (spec 6.14.1)."""
        stb = self.server.session.instrument(self.instrument).stb()
        return (stb & ~0x10) | (0x10 if self.mav else 0)

    def poll_srq(self) -> None:
        """Send AsyncServiceRequest when the RQS bit rises; once per new reason (spec 6.13)."""
        try:
            stb = self.server.session.instrument(self.instrument).stb()
        except Exception:  # a status read must never take the connection down
            return
        if stb & 0x40 and not self.last_stb & 0x40:
            self._send(self.async_writer, _pack(ASYNC_SERVICE_REQUEST, stb & 0xFF))
        self.last_stb = stb

    # ---- responses ---------------------------------------------------------------------------------------

    async def send_response(self, message_id: int, out: bytes) -> None:
        """One response message as Data chunks and a final DataEnd (spec 6.4, 6.10)."""
        chunk = max(self.max_message_size - HEADER.size, 1)
        parts = [out[i : i + chunk] for i in range(0, len(out), chunk)] or [b""]
        for part in parts[:-1]:
            self._send(self.sync_writer, _pack(DATA, 0, message_id, part))
        # The response carries the MessageID of the client message that ended with eom (3.1.1).
        self._send(self.sync_writer, _pack(DATA_END, 0, message_id, parts[-1]))
        self.mav = True  # set with the first Data or DataEnd of the response (6.14.1)
        self.rmt_expected = True
        await self._drain(self.sync_writer)


class HislipServer:
    """Every instrument of a bench on one shared HiSLIP port, selected by sub-address."""

    def __init__(self, session: Session, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.session = session
        self.host = host
        self.port = port
        self.server: asyncio.AbstractServer | None = None
        self._subaddresses: dict[str, str] = {}  # sub-address (lower case) -> instrument
        self._sessions: dict[int, HislipSession] = {}
        self._next_session_id = 1
        self._lock_state: dict[str, InstrumentLocks] = {}

    async def start(self) -> None:
        for i, name in enumerate(self.session.instruments):
            self._subaddresses[f"hislip{i}"] = name
            self._subaddresses[f"hislip_{name}".lower()] = name
            self._lock_state[name] = InstrumentLocks()
        self.server = await asyncio.start_server(self._client, self.host, self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        for sub, name in self._subaddresses.items():
            if not sub.startswith("hislip_"):
                log.info("HiSLIP %s at %s,%d", name, sub, self.port)

    def addresses(self) -> dict[str, str]:
        """Instrument -> HiSLIP resource address; instance-name aliases included."""
        out: dict[str, str] = {}
        for sub, name in self._subaddresses.items():
            out.setdefault(name, f"TCPIP0::{self.host}::{sub},{self.port}::INSTR")
        return out

    def locks(self, instrument: str) -> InstrumentLocks:
        return self._lock_state.setdefault(instrument, InstrumentLocks())

    def forget(self, sess: HislipSession) -> None:
        if self._sessions.get(sess.session_id) is sess:
            del self._sessions[sess.session_id]

    async def stop(self) -> None:
        for sess in list(self._sessions.values()):
            await sess.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    # ---- connections ------------------------------------------------------------------------------------

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            header = await self._read_header(reader)
            if header is None:
                return
            _, msg_type, _, parameter, length = header
            if msg_type == INITIALIZE:
                await self._sync_channel(reader, writer, parameter, length)
            elif msg_type == ASYNC_INITIALIZE:
                await self._async_channel(reader, writer, parameter)
            else:
                await _refuse(writer, FATAL_INVALID_INIT, b"the first message must be Initialize")
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    async def _sync_channel(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, parameter: int, length: int
    ) -> None:
        sub = await self._read_payload(reader, length)
        name = self._lookup(sub)
        if name is None:
            await _refuse(
                writer, FATAL_POORLY_FORMED, f"unknown sub-address {sub.decode(errors='replace')!r}".encode()
            )
            return
        host, port = writer.get_extra_info("peername")[:2]
        sess = HislipSession(self, name, self._next_session_id)
        self._next_session_id += 1
        sess.sync_reader, sess.sync_writer = reader, writer
        sess.client = f"hislip:{host}:{port}#{sess.session_id:04x}"
        self._sessions[sess.session_id] = sess
        version = min(parameter >> 16, SERVER_VERSION)  # the lower version wins (spec 6.1)
        writer.write(_pack(INITIALIZE_RESPONSE, 0, (version << 16) | sess.session_id))
        await writer.drain()
        log.info("HiSLIP %s: %s connected (synchronized mode)", name, sess.client)
        try:
            await self._sync_loop(sess)
        finally:
            await sess.close()  # the connection is gone: release its locks at once (spec 2.6)

    def _lookup(self, sub: bytes) -> str | None:
        """An empty sub-address opens the default device, the first instrument (spec 6.1)."""
        if not sub:
            return next(iter(self.session.instruments), None)
        return self._subaddresses.get(sub.decode(errors="replace").lower())

    async def _async_channel(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, session_id: int
    ) -> None:
        sess = self._sessions.get(session_id)
        if sess is None or sess.async_writer is not None:
            await _refuse(writer, FATAL_INVALID_INIT, b"unknown session")
            return
        sess.async_writer = writer
        writer.write(_pack(ASYNC_INITIALIZE_RESPONSE, 0, SERVER_VENDOR))
        await writer.drain()
        try:
            async for header, payload in self._messages(reader, sess):
                if sess.closing.is_set():
                    break
                await self._async_message(sess, writer, header, payload)
        finally:
            await sess.close()

    # ---- message plumbing -------------------------------------------------------------------------------

    async def _read_header(self, reader: asyncio.StreamReader) -> tuple | None:
        raw = await reader.readexactly(HEADER.size)
        prologue, msg_type, control, parameter, length = HEADER.unpack(raw)
        if prologue != b"HS" or length > MAX_PAYLOAD_BUFFER:
            return None
        return prologue, msg_type, control, parameter, length

    async def _read_payload(self, reader: asyncio.StreamReader, length: int) -> bytes:
        return await reader.readexactly(length) if length else b""

    async def _messages(self, reader: asyncio.StreamReader, sess: HislipSession):
        """Yield (header, payload) until the connection ends or a fatal error closes it."""
        while not sess.closing.is_set():
            header = await self._read_header(reader)
            if header is None:
                await sess.send_fatal(FATAL_POORLY_FORMED, b"malformed message header")
                return
            _, _, _, _, length = header
            payload = await self._read_payload(reader, length)
            yield header, payload

    async def _sync_loop(self, sess: HislipSession) -> None:
        reader, writer = sess.sync_reader, sess.sync_writer
        assert reader is not None and writer is not None
        buffer = bytearray()
        async for header, _payload in self._messages(reader, sess):
            _, msg_type, control, parameter, _ = header
            if sess.clearing:
                # Device clear in progress: disregard input until DeviceClearComplete (6.12).
                buffer.clear()
                if msg_type == DEVICE_CLEAR_COMPLETE:
                    sess.clearing = False
                    writer.write(_pack(DEVICE_CLEAR_ACKNOWLEDGE, control))
                    await writer.drain()
                continue
            if msg_type in (DATA, DATA_END, TRIGGER):
                if control & RMT:
                    sess.mav = False  # the client read the response (6.14.1, figure 1)
                elif sess.rmt_expected:
                    # The silent interrupted of 3.1.1-2: the client sent on without reading;
                    # what it never read is dropped. No indication is sent to the client.
                    sess.rmt_expected = False
                    await self._engine(sess, self._clear_output, sess.instrument, sess.client)
                sess.rmt_expected = False
            if msg_type == DATA:
                buffer.extend(_payload)
            elif msg_type == DATA_END:
                buffer.extend(_payload)
                message, buffer[:] = bytes(buffer), b""
                await self._await_lock(sess)
                if sess.closing.is_set():
                    return
                await self._engine(sess, self._write, sess.instrument, message, sess.client)
                sess.poll_srq()
                if not sess.clearing:
                    await self._answer(sess, parameter)
            elif msg_type == TRIGGER:
                await self._await_lock(sess)
                if sess.closing.is_set():
                    return
                await self._engine(sess, self._write, sess.instrument, b"*TRG", sess.client)
                sess.poll_srq()
            elif msg_type == DEVICE_CLEAR_COMPLETE:
                writer.write(_pack(DEVICE_CLEAR_ACKNOWLEDGE, control))
                await writer.drain()
            elif msg_type == FATAL_ERROR:
                await sess.close()
                return
            elif msg_type == ERROR:
                pass
            else:
                await sess.send_error(writer, ERR_UNRECOGNIZED_TYPE, b"unsupported message type")

    async def _answer(self, sess: HislipSession, message_id: int) -> None:
        """Take every response the program message produced and send it with its message ID."""
        while not sess.closing.is_set() and not sess.clearing:
            out = await self._engine(sess, self._read, sess.instrument, sess.client)
            if out is None:
                return
            if await self._interrupted(sess):
                return  # the response is dropped; the queued message is processed next
            await sess.send_response(message_id, out)

    async def _interrupted(self, sess: HislipSession) -> bool:
        """Synchronized-mode interrupted (spec 3.1.1-1): input is queued where a response is due.

        The response is dropped and both Interrupted messages carry the message ID of the
        Data, DataEnd or Trigger message that arrived next.
        """
        reader = sess.sync_reader
        if reader is None or len(reader._buffer) < HEADER.size:
            return False
        await asyncio.sleep(0)  # one loop turn, so data still in flight can land first
        if len(reader._buffer) < HEADER.size:
            return False
        _, msg_type, _, parameter, _ = HEADER.unpack(bytes(reader._buffer[: HEADER.size]))
        if msg_type not in (DATA, DATA_END, TRIGGER):
            return False
        log.debug("HiSLIP %s: response interrupted by message %#x", sess.instrument, parameter)
        sess.rmt_expected = False
        sess._send(sess.async_writer, _pack(ASYNC_INTERRUPTED, 0, parameter))
        sess._send(sess.sync_writer, _pack(INTERRUPTED, 0, parameter))
        await sess._drain(sess.sync_writer)
        return True

    async def _await_lock(self, sess: HislipSession) -> None:
        """A session without the lock has its synchronous messages held (spec 2.6.1)."""
        locks = self.locks(sess.instrument)
        while not locks.accessible(sess) and not sess.closing.is_set():
            locks.released.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(locks.released.wait(), timeout=0.1)

    async def _async_message(
        self, sess: HislipSession, writer: asyncio.StreamWriter, header: tuple, payload: bytes
    ) -> None:
        _, msg_type, control, parameter, _ = header
        if msg_type == ASYNC_MAXIMUM_MESSAGE_SIZE:
            size = struct.unpack("!Q", payload)[0] if len(payload) == 8 else MAX_MESSAGE_SIZE
            sess.max_message_size = max(size, HEADER.size + 1)
            writer.write(
                _pack(ASYNC_MAXIMUM_MESSAGE_SIZE_RESPONSE, 0, 0, struct.pack("!Q", MAX_MESSAGE_SIZE))
            )
            await writer.drain()
        elif msg_type == ASYNC_STATUS_QUERY:
            if control & RMT:
                sess.mav = False
            sess.last_stb = sess.stb()  # the status query delivers RMT; no new SRQ from it
            writer.write(_pack(ASYNC_STATUS_RESPONSE, sess.stb() & 0xFF))
            await writer.drain()
        elif msg_type == ASYNC_LOCK:
            await self._lock_message(sess, writer, control, parameter, payload)
        elif msg_type == ASYNC_LOCK_INFO:
            locks = self.locks(sess.instrument)
            code = 1 if locks.exclusive is not None else 0
            writer.write(_pack(ASYNC_LOCK_INFO_RESPONSE, code, locks.holders()))
            await writer.drain()
        elif msg_type == ASYNC_DEVICE_CLEAR:
            # Abandon operations, drop responses, then acknowledge (spec 6.12).
            sess.clearing = True
            sess.mav = False
            sess.rmt_expected = False
            await self._engine(sess, self._clear_output, sess.instrument, sess.client)
            writer.write(_pack(ASYNC_DEVICE_CLEAR_ACKNOWLEDGE, 0))
            await writer.drain()
        elif msg_type == ASYNC_REMOTE_LOCAL_CONTROL:
            # Acknowledged and recorded; a virtual instrument has no front panel to change.
            action = REMOTE_LOCAL.get(control, f"control code {control}")
            self.session.emit(
                {"kind": "hislip", "instrument": sess.instrument, "client": sess.client, "action": action}
            )
            writer.write(_pack(ASYNC_REMOTE_LOCAL_RESPONSE))
            await writer.drain()
        elif msg_type == FATAL_ERROR:
            await sess.close()
        elif msg_type == ERROR:
            pass
        else:
            await sess.send_error(writer, ERR_UNRECOGNIZED_TYPE, b"unsupported message type")

    async def _lock_message(
        self,
        sess: HislipSession,
        writer: asyncio.StreamWriter,
        control: int,
        parameter: int,
        payload: bytes,
    ) -> None:
        locks = self.locks(sess.instrument)
        if control == 1:
            # Request: an empty lock string asks for an exclusive lock (spec 6.5).
            exclusive = len(payload) == 0
            deadline = asyncio.get_running_loop().time() + parameter / 1000.0
            granted = locks.grant(sess, exclusive)
            while not granted and not sess.closing.is_set():
                locks.released.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(locks.released.wait(), timeout=0.05)
                if asyncio.get_running_loop().time() >= deadline:
                    break
                granted = locks.grant(sess, exclusive)
            code = 1 if granted else 0  # failure / success
        else:
            code = locks.release(sess)
        sess.poll_srq()
        writer.write(_pack(ASYNC_LOCK_RESPONSE, code))
        await writer.drain()

    # ---- engine passthrough (run blocking engine calls off the event loop) ------------------------------

    async def _engine(self, sess: HislipSession | None, op, *args):
        try:
            return await asyncio.to_thread(op, *args)
        except LinkDown:
            if sess is not None:
                log.info("HiSLIP %s: link fault, closing %s", sess.instrument, sess.client)
                await sess.close()
            raise

    def _write(self, instrument: str, data: bytes, client: str) -> None:
        self.session.write(instrument, data, client=client)

    def _read(self, instrument: str, client: str) -> bytes | None:
        return self.session.read(instrument, client=client)

    def _clear_output(self, instrument: str, client: str) -> None:
        self.session.clear_output(instrument, client=client)


async def _refuse(writer: asyncio.StreamWriter, code: int, message: bytes) -> None:
    """FatalError during initialization: reply and close (spec 6.1, 6.2)."""
    writer.write(_pack(FATAL_ERROR, code, 0, message))
    with contextlib.suppress(ConnectionError):
        await writer.drain()
    writer.close()
