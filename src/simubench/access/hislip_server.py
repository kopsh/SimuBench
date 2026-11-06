"""HiSLIP network instruments (IVI-6.1, D41, architecture.md 7.4).

One shared TCP port serves every instrument; the sub-address selects it (``hislip0``,
``hislip1`` … in bench order, or ``hislip_<instance name>``), so clients connect with
``TCPIP0::127.0.0.1::hislip0,<port>::INSTR``.

Both modes of the specification are served: synchronized (3.1) and overlapped (3.2). The server
starts in the one ``overlapped=`` names and a client may ask for the other at device clear
(6.12.1). The negotiated version is the lower of the client's and 2.0, which adds GetDescriptors
and, when a ``SecureConfig`` is given, the secure connection capability: TLS switched on and off
in the middle of a connection and SASL login (hislip_tls.py).

Behaviour follows the IVI-6.1 HiSLIP 2.0 specification (2020-04-23): message types and
transactions as in its tables 3–4, interrupted handling per 3.1.1, device clear per 6.12,
locks per 6.5. A ``disconnect`` link fault closes both channels of the session, like a
cable pulled from a real instrument.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import struct

from ..engine.faults import LinkDown
from ..engine.session import Session
from .bus import LockTable, bus_of, observe, serial_poll
from .hislip_tls import AuthState, SecureConfig, SecureStream

log = logging.getLogger("simubench.hislip")

HEADER = struct.Struct("!2sBBIQ")  # prologue, type, control code, parameter, payload length
DEFAULT_PORT = 4880
SERVER_VERSION = 0x0200  # HiSLIP 2.0; the negotiated version is the lower of ours and the client's
INITIAL_MESSAGE_ID = 0xFFFF_FF00  # where both sides count from after initialization and device clear
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
GET_DESCRIPTORS = 26  # HiSLIP 2.0 and later (table 4)
GET_DESCRIPTORS_RESPONSE = 27
START_TLS = 28  # the secure connection capability (table 4)
ASYNC_START_TLS = 29
ASYNC_START_TLS_RESPONSE = 30
END_TLS = 31
ASYNC_END_TLS = 32
ASYNC_END_TLS_RESPONSE = 33
GET_SASL_MECHANISM_LIST = 34
GET_SASL_MECHANISM_LIST_RESPONSE = 35
AUTHENTICATION_START = 36
AUTHENTICATION_EXCHANGE = 37
AUTHENTICATION_RESULT = 38

# Error codes (spec tables 14 and 16).
ERR_UNRECOGNIZED_TYPE = 1
ERR_UNRECOGNIZED_CONTROL = 2
VERSION_2 = 0x0200
TLS_BUSY, TLS_SUCCESS, TLS_ERROR = 0, 1, 3  # AsyncStartTLSResponse / AsyncEndTLSResponse control codes
#: Messages a connection may use whatever its state, and the ones that set encryption and login up.
ALWAYS_ALLOWED = {ASYNC_MAXIMUM_MESSAGE_SIZE, GET_DESCRIPTORS, FATAL_ERROR, ERROR}
TLS_SETUP = {ASYNC_START_TLS, START_TLS}
AUTH_SETUP = {GET_SASL_MECHANISM_LIST, AUTHENTICATION_START, AUTHENTICATION_EXCHANGE, ASYNC_END_TLS, END_TLS}
FATAL_POORLY_FORMED = 1
FATAL_INVALID_INIT = 3
FATAL_SECURE = 5  # secure connection failed
ERR_AUTH = 5  # authentication failed (non-fatal)

RMT = 1  # control-code bit 0: RMT-delivered / RMT-expected


def _pack(msg_type: int, control: int = 0, parameter: int = 0, payload: bytes = b"") -> bytes:
    return HEADER.pack(b"HS", msg_type, control, parameter, len(payload)) + payload


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
        self.srq_sent = False  # AsyncServiceRequest went out for the request that is pending
        self.version = 0x0100  # negotiated protocol version
        self.async_reader: asyncio.StreamReader | SecureStream | None = None
        self.secure_required = False  # encryption mandatory, or required right after initialization
        self.tls_pending = False  # AsyncStartTLS succeeded: the synchronous channel is next
        self.tls_active = False  # both channels are encrypted
        self.tls_ending = False  # AsyncEndTLS succeeded: the synchronous channel is next
        self.tls_last_error = ""
        #: An asynchronous-channel encryption switch is under way: nothing else may write to it.
        self.switching = False
        self.auth = AuthState()
        self.overlapped = server.overlapped
        self.rx_id = INITIAL_MESSAGE_ID - 2  # MessageID of the last Data, DataEND or Trigger received
        self.tx_id = INITIAL_MESSAGE_ID  # overlapped mode: the MessageID of the next Data/DataEND sent

    def reset_ids(self) -> None:
        """After initialization and device clear both sides count from 0xffffff00 again (3.1.2, 3.2)."""
        self.rx_id = INITIAL_MESSAGE_ID - 2
        self.tx_id = INITIAL_MESSAGE_ID

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

    def stb(self, delivered: int | None = None) -> int:
        """Status byte with MAV by the rules of the current mode (spec 6.14).

        Synchronized: MAV is ours to track (6.14.1). Overlapped: the client says which response it
        has taken (``delivered``, the MessageID of the last Data/DataEND handed to its application)
        and anything the server sent after it is still available (6.14.2)."""
        # A status query is a serial poll: it returns RQS and clears it, which allows the next
        # AsyncServiceRequest (spec 6.13).
        stb = serial_poll(self.server.session, self.instrument)
        self.srq_sent = False
        if self.overlapped:
            mav = delivered is not None and delivered != (self.tx_id - 2) & 0xFFFF_FFFF
        else:
            mav = self.mav
        return (stb & ~0x10) | (0x10 if mav else 0)

    def poll_srq(self) -> None:
        """Send AsyncServiceRequest when a request for service is pending; once per request (spec 6.13)."""
        if self.switching:
            return  # a plaintext message now would corrupt the TLS stream; it is found again after
        session = self.server.session
        try:
            observe(session, self.instrument)
            pending = bus_of(session, self.instrument).service_request.latch
            stb = session.instrument(self.instrument).stb(commit=False)
        except Exception:  # a status read must never take the connection down
            return
        if not pending:
            self.srq_sent = False
        elif not self.srq_sent:
            self.srq_sent = True
            self._send(self.async_writer, _pack(ASYNC_SERVICE_REQUEST, (stb | 0x40) & 0xFF))

    # ---- responses ---------------------------------------------------------------------------------------

    def _response_id(self, message_id: int) -> int:
        if not self.overlapped:
            return message_id
        mid, self.tx_id = self.tx_id, (self.tx_id + 2) & 0xFFFF_FFFF
        return mid

    async def send_response(self, message_id: int, out: bytes) -> None:
        """One response message as Data chunks and a final DataEnd (spec 6.4, 6.10)."""
        chunk = max(self.max_message_size - HEADER.size, 1)
        parts = [out[i : i + chunk] for i in range(0, len(out), chunk)] or [b""]
        for part in parts[:-1]:
            self._send(self.sync_writer, _pack(DATA, 0, self._response_id(message_id), part))
        # Synchronized: the response carries the MessageID of the client message that ended with
        # eom (3.1.1). Overlapped: the server's own counter, two more per message (3.2.1).
        self._send(self.sync_writer, _pack(DATA_END, 0, self._response_id(message_id), parts[-1]))
        if not self.overlapped:
            self.mav = True  # set with the first Data or DataEnd of the response (6.14.1)
            self.rmt_expected = True
        await self._drain(self.sync_writer)


def _peek_header(reader: asyncio.StreamReader, size: int) -> bytes | None:
    """The bytes already buffered in ``reader``, without consuming them — enough of a message
    header to decide whether the synchronized mode was interrupted (3.1.1-1). ``StreamReader``
    has no public peek, so this reads its buffer directly (CPython's implementation, stable for
    years but not an API) and answers None if that internal ever moves: the interrupt decision
    may then miss a turn, but never loses data."""
    buffer = getattr(reader, "_buffer", None)
    if isinstance(buffer, (bytes, bytearray, memoryview)):
        return bytes(buffer[:size])
    # Not a silent miss (issue 691): the interrupt decision is off until someone looks.
    log.warning("HiSLIP peek: StreamReader has no _buffer; synchronized-interrupt detection is off")
    return None


class HislipServer:
    """Every instrument of a bench on one shared HiSLIP port, selected by sub-address."""

    def __init__(
        self,
        session: Session,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        overlapped: bool = False,
        secure: SecureConfig | None = None,
    ) -> None:
        self.session = session
        #: The secure connection capability (4): ``None`` serves HiSLIP without it.
        self.secure = secure
        #: The mode InitializeResponse announces and device clear proposes (IVI-6.1 3, 6.12.1). Both
        #: modes are served: a client may ask for the other one in DeviceClearComplete.
        self.overlapped = overlapped
        self.host = host
        self.port = port
        self.server: asyncio.AbstractServer | None = None
        self._subaddresses: dict[str, str] = {}  # sub-address (lower case) -> instrument
        self._sessions: dict[int, HislipSession] = {}
        self._next_session_id = 1

    def sessions(self) -> list[HislipSession]:
        """The open HiSLIP connections, for the client lists the manager reports (issue 641)."""
        return list(self._sessions.values())

    async def start(self) -> None:
        for i, name in enumerate(self.session.instruments):
            self._subaddresses[f"hislip{i}"] = name
            self._subaddresses[f"hislip_{name}".lower()] = name
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

    def locks(self, instrument: str) -> LockTable:
        return bus_of(self.session, instrument).locks

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
        version = min(parameter >> 16, SERVER_VERSION)  # the lower version wins (spec 6.1)
        if self.secure is not None and self.secure.initial_required and version < VERSION_2:
            # Encryption that must come first needs HiSLIP 2.0 (table 6): an older client cannot do it.
            await _refuse(writer, FATAL_SECURE, b"a secure connection needs HiSLIP 2.0")
            return
        host, port = writer.get_extra_info("peername")[:2]
        sess = HislipSession(self, name, self._next_session_id)
        self._next_session_id += 1
        sess.sync_reader, sess.sync_writer = reader, writer
        sess.client = f"hislip:{host}:{port}#{sess.session_id:04x}"
        sess.version = version
        sess.secure_required = (
            self.secure is not None and version >= VERSION_2 and self.secure.initial_required
        )
        self._sessions[sess.session_id] = sess
        # Control: bit 0 is the mode the server starts in, bits 1 and 2 the encryption mode (6.1).
        control = int(sess.overlapped) | self._feature_bits(sess)
        writer.write(_pack(INITIALIZE_RESPONSE, control, (version << 16) | sess.session_id))
        await writer.drain()
        log.info(
            "HiSLIP %s: %s connected (%s mode, version %d.%d)",
            name,
            sess.client,
            "overlapped" if sess.overlapped else "synchronized",
            version >> 8,
            version & 0xFF,
        )
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
        sess.async_reader, sess.async_writer = reader, writer
        capabilities = int(self.secure is not None and sess.version >= VERSION_2)  # bit 0: secure connection
        writer.write(_pack(ASYNC_INITIALIZE_RESPONSE, capabilities, SERVER_VENDOR))
        await writer.drain()
        try:
            async for header, payload in self._messages(sess, "async"):
                if sess.closing.is_set():
                    break
                # The channel's writer is read afresh: AsyncStartTLS replaces it with a secure one.
                await self._async_message(sess, sess.async_writer, header, payload)
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

    async def _messages(self, sess: HislipSession, channel: str):
        """Yield (header, payload) until the connection ends or a fatal error closes it.

        The reader is looked up for every message: StartTLS and EndTLS swap a channel's stream."""
        while not sess.closing.is_set():
            reader = sess.sync_reader if channel == "sync" else sess.async_reader
            header = await self._read_header(reader)
            if header is None:
                await sess.send_fatal(FATAL_POORLY_FORMED, b"malformed message header")
                return
            _, _, _, _, length = header
            payload = await self._read_payload(reader, length)
            yield header, payload

    async def _sync_loop(self, sess: HislipSession) -> None:
        buffer = bytearray()
        async for header, _payload in self._messages(sess, "sync"):
            writer = sess.sync_writer  # read afresh: StartTLS replaces it with a secure one
            assert writer is not None
            _, msg_type, control, parameter, _ = header
            if not await self._gate(sess, msg_type, writer):
                if sess.closing.is_set():
                    return
                continue
            if sess.clearing:
                # Device clear in progress: disregard input until DeviceClearComplete (6.12).
                buffer.clear()
                if msg_type == DEVICE_CLEAR_COMPLETE:
                    sess.clearing = False
                    writer.write(_pack(DEVICE_CLEAR_ACKNOWLEDGE, self._agree_features(sess, control)))
                    await writer.drain()
                continue
            if msg_type in (DATA, DATA_END, TRIGGER):
                sess.rx_id = parameter
            if msg_type in (DATA, DATA_END, TRIGGER) and sess.overlapped:
                pass  # overlapped: messages are only buffered; the MessageID matters for locks (3.2)
            elif msg_type in (DATA, DATA_END, TRIGGER):
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
                # Write and take its responses in one engine action (Session.exchange, #121): two
                # calls would let another session's read take this one's response in between.
                outs = await self._engine(sess, self._exchange, sess.instrument, message, sess.client)
                sess.poll_srq()
                if not sess.clearing:
                    await self._answer(sess, parameter, outs)
            elif msg_type == TRIGGER:
                await self._await_lock(sess)
                if sess.closing.is_set():
                    return
                await self._engine(sess, self._write, sess.instrument, b"*TRG", sess.client)
                sess.poll_srq()
            elif msg_type == DEVICE_CLEAR_COMPLETE:
                writer.write(_pack(DEVICE_CLEAR_ACKNOWLEDGE, self._agree_features(sess, control)))
                await writer.drain()
            elif msg_type == GET_DESCRIPTORS:
                await self._descriptors(sess, writer)
            elif msg_type == START_TLS:
                await self._start_tls_sync(sess)
            elif msg_type == END_TLS:
                await self._end_tls_sync(sess)
            elif msg_type in (GET_SASL_MECHANISM_LIST, AUTHENTICATION_START, AUTHENTICATION_EXCHANGE):
                await self._authentication(sess, writer, msg_type, _payload)
            elif msg_type == FATAL_ERROR:
                await sess.close()
                return
            elif msg_type == ERROR:
                pass
            else:
                await sess.send_error(writer, ERR_UNRECOGNIZED_TYPE, b"unsupported message type")

    async def _answer(self, sess: HislipSession, message_id: int, outs: list[bytes]) -> None:
        """Send every response the program message produced, with its message ID."""
        for out in outs:
            if sess.closing.is_set() or sess.clearing:
                return
            if not sess.overlapped and await self._interrupted(sess):
                return  # the response is dropped; the queued message is processed next
            await sess.send_response(message_id, out)

    async def _interrupted(self, sess: HislipSession) -> bool:
        """Synchronized-mode interrupted (spec 3.1.1-1): input is queued where a response is due.

        The response is dropped and both Interrupted messages carry the message ID of the
        Data, DataEnd or Trigger message that arrived next.
        """
        reader = sess.sync_reader
        if reader is None:
            return False
        peeked = _peek_header(reader, HEADER.size)
        if peeked is None or len(peeked) < HEADER.size:
            return False
        await asyncio.sleep(0)  # one loop turn, so data still in flight can land first
        peeked = _peek_header(reader, HEADER.size)
        if peeked is None or len(peeked) < HEADER.size:
            return False
        _, msg_type, _, parameter, _ = HEADER.unpack(peeked)
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
        if not await self._gate(sess, msg_type, writer):
            return
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
            writer.write(_pack(ASYNC_STATUS_RESPONSE, sess.stb(parameter) & 0xFF))  # one poll
            await writer.drain()
        elif msg_type == ASYNC_LOCK:
            await self._lock_message(sess, writer, control, parameter, payload)
        elif msg_type == GET_DESCRIPTORS:
            await self._descriptors(sess, writer)
        elif msg_type == ASYNC_START_TLS:
            await self._async_start_tls(sess, writer, control, parameter, payload)
        elif msg_type == ASYNC_END_TLS:
            await self._async_end_tls(sess, writer, control, parameter, payload)
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
            writer.write(_pack(ASYNC_DEVICE_CLEAR_ACKNOWLEDGE, self._preferred_features(sess)))
            await writer.drain()
        elif msg_type == ASYNC_REMOTE_LOCAL_CONTROL:
            # The bus state (REN/GTL/LLO) is shared with every other interface of the instrument
            # (bus.py); a virtual instrument has no front panel to change, so it is recorded.
            bus = bus_of(self.session, sess.instrument)
            try:
                action = bus.remote_local.control(control)
            except KeyError:
                await sess.send_error(writer, ERR_UNRECOGNIZED_CONTROL, b"unrecognized control code")
                return
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

    # ---- secure connection capability (IVI-6.1 4, 6.15, 6.16) ----------------------------------------------

    async def _gate(self, sess: HislipSession, msg_type: int, writer) -> bool:
        """Whether a message may be handled in the state the connection is in (4.1.3, 4.1.4).

        Encryption that is required comes first: anything else is a fatal error 5. Once a channel
        is encrypted, only the login messages are answered until the client is authenticated."""
        if self.secure is None or msg_type in ALWAYS_ALLOWED:
            return True
        if not sess.tls_active:
            if sess.secure_required and msg_type not in TLS_SETUP:
                await sess.send_fatal(FATAL_SECURE, b"a secure connection is required")
                return False
            return True
        if not sess.auth.authenticated and msg_type not in AUTH_SETUP | TLS_SETUP:
            await sess.send_error(writer, ERR_AUTH, b"authentication required")
            return False
        return True

    def _tls_buffers_empty(self, sess: HislipSession, control: int, sent: int, received: int) -> bool:
        """Both sides' buffers are empty, so the encryption mode may change (6.15 step 1)."""
        if control & RMT:
            sess.mav = False
            sess.rmt_expected = False
        if sent != sess.rx_id:
            return False
        if sess.overlapped:
            return received == (sess.tx_id - 2) & 0xFFFF_FFFF
        return not sess.mav

    def _tls_failed(self, sess: HislipSession, what: str, exc: BaseException) -> None:
        sess.tls_last_error = f"{what}: {type(exc).__name__} {exc}"[:200]
        log.info("HiSLIP %s: %s failed for %s (%s)", sess.instrument, what, sess.client, exc)

    async def _async_start_tls(self, sess, writer, control: int, parameter: int, payload: bytes) -> None:
        """AsyncStartTLS: answer busy, success or error, then run the handshake on this channel."""
        secure = self.secure
        if (
            secure is None
            or sess.version < VERSION_2
            or sess.tls_active
            or sess.tls_pending
            or len(payload) != 4
        ):
            sess.tls_last_error = "encryption cannot be switched on now"
            writer.write(_pack(ASYNC_START_TLS_RESPONSE, TLS_ERROR))
            await writer.drain()
            return
        received = struct.unpack("!I", payload)[0]
        if not self._tls_buffers_empty(sess, control, parameter, received):
            writer.write(_pack(ASYNC_START_TLS_RESPONSE, TLS_BUSY))
            await writer.drain()
            return
        # No await between the answer and the handshake: the ClientHello follows it at once. Until
        # the handshake is over nothing else may be written to this channel (``switching``).
        sess.switching = True
        writer.write(_pack(ASYNC_START_TLS_RESPONSE, TLS_SUCCESS))
        try:
            stream = await SecureStream.accept(
                sess.async_reader, writer, secure.context, secure.handshake_timeout
            )
        except (ssl.SSLError, OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            self._tls_failed(sess, "TLS handshake (asynchronous channel)", exc)
            await sess.close()
            return
        finally:
            sess.switching = False
        sess.async_reader = sess.async_writer = stream
        sess.tls_pending = True
        sess.poll_srq()  # a service request raised during the switch is delivered now, encrypted

    async def _start_tls_sync(self, sess: HislipSession) -> None:
        """StartTLS: the handshake on the synchronous channel, after the asynchronous one (6.15 step 4)."""
        secure = self.secure
        if secure is None or not sess.tls_pending:
            await sess.send_error(sess.sync_writer, 0, b"StartTLS must follow a successful AsyncStartTLS")
            return
        try:
            stream = await SecureStream.accept(
                sess.sync_reader, sess.sync_writer, secure.context, secure.handshake_timeout
            )
        except (ssl.SSLError, OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            self._tls_failed(sess, "TLS handshake (synchronous channel)", exc)
            await sess.close()
            return
        sess.sync_reader = sess.sync_writer = stream
        sess.tls_pending = False
        sess.tls_active = True
        sess.auth = AuthState()  # authentication is valid for this encrypted connection only (table 6)

    async def _async_end_tls(self, sess, writer, control: int, parameter: int, payload: bytes) -> None:
        """AsyncEndTLS: possible only with optional encryption, on an encrypted connection (6.16)."""
        secure = self.secure
        if (
            secure is None
            or sess.version < VERSION_2
            or not sess.tls_active
            or sess.tls_ending
            or secure.mandatory
            or len(payload) != 4
        ):
            sess.tls_last_error = "encryption cannot be switched off now"
            writer.write(_pack(ASYNC_END_TLS_RESPONSE, TLS_ERROR))
            await writer.drain()
            return
        received = struct.unpack("!I", payload)[0]
        if not self._tls_buffers_empty(sess, control, parameter, received):
            writer.write(_pack(ASYNC_END_TLS_RESPONSE, TLS_BUSY))
            await writer.drain()
            return
        sess.switching = True
        writer.write(_pack(ASYNC_END_TLS_RESPONSE, TLS_SUCCESS))
        await writer.drain()
        try:
            sess.async_reader, sess.async_writer = await writer.unwrap(secure.handshake_timeout)
        except (ssl.SSLError, OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            self._tls_failed(sess, "TLS shutdown (asynchronous channel)", exc)
            await sess.close()
            return
        finally:
            sess.switching = False
        sess.tls_ending = True
        sess.poll_srq()

    async def _end_tls_sync(self, sess: HislipSession) -> None:
        secure = self.secure
        if secure is None or not sess.tls_ending:
            await sess.send_error(sess.sync_writer, 0, b"EndTLS must follow a successful AsyncEndTLS")
            return
        try:
            sess.sync_reader, sess.sync_writer = await sess.sync_writer.unwrap(secure.handshake_timeout)
        except (ssl.SSLError, OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            self._tls_failed(sess, "TLS shutdown (synchronous channel)", exc)
            await sess.close()
            return
        sess.tls_ending = False
        sess.tls_active = False
        sess.auth = AuthState()  # authentication does not outlive the encryption (table 6)

    async def _authentication(self, sess: HislipSession, writer, msg_type: int, payload: bytes) -> None:
        """GetSaslMechanismList, AuthenticationStart and AuthenticationExchange (6.15 steps 5-11)."""
        secure = self.secure
        if secure is None or not sess.tls_active:
            await sess.send_error(writer, 0, b"authentication needs an encrypted connection")
            return
        auth = sess.auth
        if msg_type == GET_SASL_MECHANISM_LIST:
            writer.write(
                _pack(GET_SASL_MECHANISM_LIST_RESPONSE, 0, 0, " ".join(secure.mechanisms()).encode())
            )
            await writer.drain()
        elif msg_type == AUTHENTICATION_START:
            mechanism = payload.decode("ascii", errors="replace").upper()
            if mechanism not in secure.mechanisms():
                await sess.send_error(writer, ERR_AUTH, b"unsupported authentication mechanism")
                return
            auth.mechanism, auth.challenged = mechanism, False
        else:
            await self._authentication_exchange(sess, writer, payload)

    async def _authentication_exchange(self, sess: HislipSession, writer, payload: bytes) -> None:
        secure, auth = self.secure, sess.auth
        assert secure is not None
        if auth.mechanism is None:
            await sess.send_error(writer, 0, b"AuthenticationStart comes first")
            return
        if auth.mechanism == "PLAIN" and not payload and not auth.challenged:
            # No initial response: the server's challenge is empty and the client answers it (RFC 4616 2).
            auth.challenged = True
            writer.write(_pack(AUTHENTICATION_EXCHANGE))
            await writer.drain()
            return
        if auth.mechanism == "ANONYMOUS":  # the payload is a trace token (RFC 4505): anything printable
            ok = len(payload) <= 255 and payload.isascii()
        else:
            ok = secure.check_plain(payload)
        if ok is None or (auth.mechanism == "ANONYMOUS" and not ok):
            # Data that is not even of the mechanism's form: fatal error 5 (6.15).
            await sess.send_fatal(FATAL_SECURE, b"invalid authentication data")
            return
        auth.mechanism, auth.challenged = None, False
        if ok:
            auth.authenticated, auth.failures = True, 0
            writer.write(_pack(AUTHENTICATION_RESULT, 1, 0))
        else:
            # A growing delay against guessing (6.15 recommends one).
            auth.failures += 1
            await asyncio.sleep(min(0.05 * auth.failures, 1.0))
            writer.write(_pack(AUTHENTICATION_RESULT, 0, 1, b"authentication failed"))
        await writer.drain()

    # ---- features and descriptors (HiSLIP 2.0) ----------------------------------------------------------

    def _feature_bits(self, sess: HislipSession) -> int:
        """The features the server fixes for this connection: encryption mode bits (table 31)."""
        if self.secure is None or sess.version < VERSION_2:
            return 0
        return (2 if self.secure.mandatory else 0) | (4 if self.secure.initial_required else 0)

    def _preferred_features(self, sess: HislipSession) -> int:
        """AsyncDeviceClearAcknowledge: what the server prefers (6.12.1, step 1)."""
        return int(self.overlapped) | self._feature_bits(sess)

    def _agree_features(self, sess: HislipSession, requested: int) -> int:
        """DeviceClearAcknowledge: both modes are served, so the client's choice stands (step 3).

        Device clear also empties the buffers: counting starts from 0xffffff00 again."""
        sess.overlapped = bool(requested & 1)
        sess.reset_ids()
        sess.mav = False
        sess.rmt_expected = False
        return int(sess.overlapped) | self._feature_bits(sess)

    def _descriptor_payload(self, sess: HislipSession) -> bytes:
        """All descriptors, one after the other (5): those of the secure connection capability."""
        return self.secure.descriptors(sess.tls_last_error) if self.secure is not None else b""

    async def _descriptors(self, sess: HislipSession, writer: asyncio.StreamWriter) -> None:
        if sess.version < VERSION_2:  # the message does not exist before 2.0
            await sess.send_error(writer, ERR_UNRECOGNIZED_TYPE, b"GetDescriptors needs HiSLIP 2.0")
            return
        writer.write(_pack(GET_DESCRIPTORS_RESPONSE, 0, 0, self._descriptor_payload(sess)))
        await writer.drain()

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
            granted = locks.grant(sess, exclusive, payload)
            while not granted and not sess.closing.is_set():
                locks.released.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(locks.released.wait(), timeout=0.05)
                if asyncio.get_running_loop().time() >= deadline:
                    break
                granted = locks.grant(sess, exclusive, payload)
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

    def _exchange(self, instrument: str, data: bytes, client: str) -> list[bytes]:
        return self.session.exchange(instrument, data, client=client)

    def _clear_output(self, instrument: str, client: str) -> None:
        self.session.clear_output(instrument, client=client)


async def _refuse(writer: asyncio.StreamWriter, code: int, message: bytes) -> None:
    """FatalError during initialization: reply and close (spec 6.1, 6.2)."""
    writer.write(_pack(FATAL_ERROR, code, 0, message))
    with contextlib.suppress(ConnectionError):
        await writer.drain()
    writer.close()
