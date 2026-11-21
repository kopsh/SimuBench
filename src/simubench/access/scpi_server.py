"""SCPI network instruments: one raw TCP socket per instrument (D9, architecture.md 7.4).

Any VISA implementation or plain socket client can connect with
``TCPIP0::127.0.0.1::<port>::SOCKET``. Messages end with a line feed; responses are sent as soon
as a query has produced them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ..engine.faults import LinkDown
from ..engine.session import Session
from ..scpi.parse import block_kind
from . import ClientConnection, engine_call

log = logging.getLogger("simubench.scpi")

MAX_MESSAGE = 256 * 1024 * 1024  # bytes; a longer program message is -223 and dropped


#: The usual SCPI socket port of a real instrument, when the caller does not say otherwise.
DEFAULT_SCPI_PORT = 5025


def scpi_socket_address(host: str, port: int) -> str:
    """The virtual resource address of an instrument's SCPI socket (D5): the one place this
    string is built, so the projections and the in-process backend cannot drift."""
    return f"TCPIP0::{host}::{port}::SOCKET"


class MessageReader:
    """Splits a byte stream into program messages (IEEE 488.2 7.7.6, #1448).

    A message ends at the first LF outside a definite-length block (``#<n><len><bytes>``): the
    block's bytes, LF included, belong to the message. A ``#`` inside a quoted string is not a
    block. There is no 64 KiB line limit; over ``MAX_MESSAGE`` the rest of the message is
    discarded up to its terminator and ``read`` returns it as ``overflow``."""

    def __init__(self, reader: asyncio.StreamReader, limit: int = MAX_MESSAGE) -> None:
        self.reader = reader
        self.limit = limit
        self.buf = bytearray()
        self.pos = 0  # scanned up to here
        self.quote = 0  # the open quote byte, or 0
        self.skip = 0  # block bytes still to pass
        self.dropping = False

    def _scan(self) -> int | None:
        """Index just past the terminating LF, or None when more bytes are needed."""
        buf = self.buf
        while True:
            if self.skip:
                step = min(self.skip, len(buf) - self.pos)
                self.pos += step
                self.skip -= step
                if self.skip:
                    return None
            if self.pos >= len(buf):
                return None
            if self.quote:
                i = buf.find(bytes([self.quote]), self.pos)
                j = buf.find(b"\n", self.pos)
                if j != -1 and (i == -1 or j < i):
                    self.quote = 0  # a string does not span lines
                    self.pos = j + 1
                    return self.pos
                if i == -1:
                    self.pos = len(buf)
                    return None
                self.quote = 0
                self.pos = i + 1
                continue
            while self.pos < len(buf):
                c = buf[self.pos]
                if c == 0x0A:
                    self.pos += 1
                    return self.pos
                if c in (0x22, 0x27):
                    self.quote = c
                    self.pos += 1
                    break
                if c == 0x23:  # '#'
                    kind, i2, length = block_kind(bytes(buf), self.pos)
                    if kind == "wait":
                        return None  # the bytes that would decide have not arrived yet
                    if kind == "definite":
                        self.pos = i2
                        self.skip = length
                        break
                    if kind == "indefinite":
                        # #0 runs to the end of the message: the next LF ends it — the payload is
                        # skipped, so a quote or '#' inside it does not re-trigger the framing
                        # (aligned with split_units' judge, #1766; the reader used to treat #0 as
                        # an ordinary character and the payload could corrupt the framing).
                        j = buf.find(b"\n", i2)
                        if j == -1:
                            self.pos = len(buf)
                            return None
                        self.pos = j + 1
                        return self.pos
                    # "number" (#H/#Q/#B) and "none": ordinary characters — the tail advances
                self.pos += 1

    async def read(self) -> tuple[bytes, bool]:
        """The next message (b"" at end of stream) and whether it overflowed the limit."""
        while True:
            end = self._scan()
            if end is not None:
                msg = bytes(self.buf[:end])
                del self.buf[:end]
                self.pos = 0
                over, self.dropping = self.dropping or end > self.limit, False
                return (b"", True) if over else (msg, False)
            if len(self.buf) > self.limit:
                self.dropping = True
                self.buf.clear()
                self.pos = 0
                self.skip = 0
            chunk = await self.reader.read(1 << 20)
            if not chunk:
                rest = bytes(self.buf)  # a final message without a terminator
                self.buf.clear()
                self.pos = 0
                return rest, False
            self.buf += chunk


@dataclass
class ScpiListener:
    instrument: str
    host: str
    port: int
    server: asyncio.AbstractServer | None = None
    clients: set[asyncio.StreamWriter] = field(default_factory=set)
    labels: set[str] = field(default_factory=set)  # client labels, e.g. socket:127.0.0.1:54321

    @property
    def address(self) -> str:
        return scpi_socket_address(self.host, self.port)


class ScpiServer:
    def __init__(
        self,
        session: Session,
        host: str = "127.0.0.1",
        base_port: int = DEFAULT_SCPI_PORT,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.session = session
        self.host = host
        self.base_port = base_port
        #: Fired whenever a client connects or leaves (issue #1484): the workspace event stream
        #: announces ``clients_changed``, so the UI re-reads instead of polling the port lists.
        self.on_change = on_change
        self.listeners: list[ScpiListener] = []

    async def start(self) -> list[ScpiListener]:
        for i, name in enumerate(self.session.instruments):
            port = 0 if self.base_port == 0 else self.base_port + i
            lst = ScpiListener(name, self.host, port)
            lst.server = await asyncio.start_server(
                lambda r, w, lst=lst: self._client(lst, r, w), self.host, port
            )
            sock = lst.server.sockets[0]
            lst.port = sock.getsockname()[1]
            self.listeners.append(lst)
            log.info("SCPI %s listening on %s", name, lst.address)
        return self.listeners

    async def _client(
        self, lst: ScpiListener, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        lst.clients.add(writer)
        host, port = writer.get_extra_info("peername")[:2]
        client = f"socket:{host}:{port}"
        lst.labels.add(client)
        if self.on_change is not None:
            self.on_change()
        try:
            messages = MessageReader(reader)
            while True:
                line, overflow = await messages.read()
                if overflow:
                    self.session.instrument(lst.instrument).push_error(-223)
                    continue
                if not line:
                    break
                # One action: a UI console query cannot take this message's response (#121).
                for out in await engine_call(self.session.exchange, lst.instrument, line, client):
                    writer.write(out)
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except LinkDown:
            log.info("SCPI %s: link fault, closing %s", lst.instrument, client)
        finally:
            lst.clients.discard(writer)
            lst.labels.discard(client)
            if self.on_change is not None:
                self.on_change()
            writer.close()

    async def stop(self) -> None:
        for lst in self.listeners:
            if lst.server:
                lst.server.close()
                for w in list(lst.clients):
                    w.close()
                await lst.server.wait_closed()
        self.listeners = []

    def connections(self) -> list[ClientConnection]:
        """The clients on the instruments' sockets, listener by listener. A raw socket never locks."""
        return [
            ClientConnection(client=label, instrument=lst.instrument)
            # snapshots: the engine thread asks while the event loop adds and drops clients (#1479)
            for lst in list(self.listeners)
            for label in sorted(list(lst.labels))
        ]

    def addresses(self) -> dict[str, str]:
        """Instrument -> its socket address. This server's port semantics: the instruments open
        their ports one by one (a listener each), where HiSLIP and VXI-11 share one."""
        return {lst.instrument: lst.address for lst in self.listeners}
