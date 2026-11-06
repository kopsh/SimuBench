"""HiSLIP 2.0 secure connection capability: TLS upgrade of a live channel and SASL authentication.

IVI-6.1 4, 5 and 6.15/6.16. Each of the two TCP channels of a HiSLIP connection is upgraded to TLS
on its own, in the middle of the connection, by ``AsyncStartTLS`` (asynchronous channel) and
``StartTLS`` (synchronous channel), and downgraded again by ``AsyncEndTLS``/``EndTLS``.

asyncio's own ``start_tls`` cannot be used for that: a client sends its ClientHello right behind
the StartTLS message, so the stream reader may already hold handshake bytes, and those would be
lost. :class:`SecureStream` runs the TLS state machine over memory buffers instead and is fed the
bytes the reader had already taken.

Authentication mechanisms (SASL, RFC 4422): ``PLAIN`` (RFC 4616) against a table of users, or
``ANONYMOUS`` (RFC 4505) when no users are configured. ``EXTERNAL`` (client certificates),
``GSSAPI`` and the like are not offered.
"""

from __future__ import annotations

import asyncio
import hmac
import ssl
import struct
from dataclasses import dataclass, field
from pathlib import Path

TLS_VERSIONS = {ssl.TLSVersion.TLSv1_2: 0x0303, ssl.TLSVersion.TLSv1_3: 0x0304}
TLS_NAMES = {0x0303: "TLS 1.2", 0x0304: "TLS 1.3"}

DESC_TLS_VERSIONS, DESC_TLS_INFO, DESC_TLS_LAST_ERROR = 0, 1, 2


@dataclass
class SecureConfig:
    """What the secure connection capability needs: a certificate, the encryption mode, the users."""

    context: ssl.SSLContext
    #: Encryption mandatory (table 6): the connection must be secured right after initialization
    #: and cannot be unsecured. Otherwise it is optional and clients may switch it on and off.
    mandatory: bool = False
    #: With optional encryption: the client must still switch it on right after initialization (4.1.4).
    initial: bool = False
    #: PLAIN credentials, name -> password. ``None`` offers ANONYMOUS only.
    users: dict[str, str] | None = None
    handshake_timeout: float = 10.0

    @classmethod
    def from_files(
        cls,
        certfile: str | Path,
        keyfile: str | Path,
        *,
        mandatory: bool = False,
        initial: bool = False,
        users: dict[str, str] | None = None,
    ) -> SecureConfig:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile, keyfile)
        return cls(context, mandatory=mandatory, initial=initial or mandatory, users=users)

    @property
    def initial_required(self) -> bool:
        return self.mandatory or self.initial

    def mechanisms(self) -> list[str]:
        """SASL mechanisms in the server's order of preference (6.15)."""
        return ["PLAIN"] if self.users else ["ANONYMOUS"]

    def tls_versions(self) -> list[int]:
        lo, hi = self.context.minimum_version, self.context.maximum_version
        out = []
        for version, code in TLS_VERSIONS.items():
            if version == ssl.TLSVersion.TLSv1_3 and not ssl.HAS_TLSv1_3:
                continue
            if lo <= version and (hi == ssl.TLSVersion.MAXIMUM_SUPPORTED or version <= hi):
                out.append(code)
        return out

    def check_plain(self, data: bytes) -> bool | None:
        """RFC 4616: ``[authzid] NUL authcid NUL passwd``. ``None`` for data that is not that form."""
        parts = data.split(b"\0")
        if len(parts) != 3:
            return None
        try:
            authzid, authcid, passwd = (p.decode("utf-8") for p in parts)
        except UnicodeDecodeError:
            return None
        users = self.users or {}
        expected = users.get(authcid)
        # The comparison runs for unknown names too, so that its time does not say which exist.
        ok = hmac.compare_digest((expected or "\0").encode(), passwd.encode()) and expected is not None
        return ok and authzid in ("", authcid)

    def descriptors(self, last_error: str = "") -> bytes:
        """The descriptors of table 8, all at once (5)."""

        def one(kind: int, content: bytes) -> bytes:
            return struct.pack("!HB", len(content), kind) + content

        versions = self.tls_versions()
        info = ", ".join(TLS_NAMES[v] for v in versions) + "; SASL " + " ".join(self.mechanisms())
        return (
            one(DESC_TLS_VERSIONS, b"".join(struct.pack("!H", v) for v in versions))
            + one(DESC_TLS_INFO, info.encode("ascii"))
            + one(DESC_TLS_LAST_ERROR, last_error.encode("ascii", errors="replace"))
        )


def read_users(path: str | Path) -> dict[str, str]:
    """A users file: one ``name:password`` per line; blank lines and ``#`` comments are skipped."""
    users: dict[str, str] = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, password = line.partition(":")
        if not sep or not name:
            raise ValueError(f"{path}:{number}: expected name:password")
        users[name] = password
    return users


class SecureStream:
    """A TLS server endpoint over an existing reader/writer pair, with the stream-reader interface
    the HiSLIP server uses (``readexactly``, ``write``, ``drain``, ``close``, ``_buffer``)."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, context: ssl.SSLContext
    ) -> None:
        self.raw_reader, self.raw_writer = reader, writer
        self._in, self._out = ssl.MemoryBIO(), ssl.MemoryBIO()
        self._ssl = context.wrap_bio(self._in, self._out, server_side=True)
        #: Decrypted bytes not yet consumed; the HiSLIP server looks at it like a reader's buffer.
        self._buffer = bytearray()

    @classmethod
    async def accept(
        cls,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        context: ssl.SSLContext,
        timeout: float = 10.0,
    ) -> SecureStream:
        """Run the server side of the TLS handshake (RFC 8446 4) on a channel already in use."""
        stream = cls(reader, writer, context)
        # What the reader took off the socket after the message that started this: the first bytes
        # of the ClientHello when the client did not wait.
        leftover = bytes(reader._buffer)
        reader._buffer.clear()
        if leftover:
            stream._in.write(leftover)
        await asyncio.wait_for(stream._handshake(), timeout)
        return stream

    async def _flush(self) -> None:
        data = self._out.read()
        if data:
            self.raw_writer.write(data)
            await self.raw_writer.drain()

    async def _feed(self) -> bool:
        data = await self.raw_reader.read(65536)
        if not data:
            self._in.write_eof()
            return False
        self._in.write(data)
        return True

    async def _handshake(self) -> None:
        while True:
            try:
                self._ssl.do_handshake()
                break
            except ssl.SSLWantReadError:
                await self._flush()
                if not await self._feed():
                    raise ConnectionError(
                        "the client closed the connection during the TLS handshake"
                    ) from None
            except ssl.SSLWantWriteError:
                await self._flush()
        await self._flush()

    async def _fill(self) -> None:
        """Decrypt more input into the buffer, reading from the socket when it has none."""
        while True:
            try:
                data = self._ssl.read(65536)
                if data:
                    self._buffer += data
                    while self._ssl.pending():
                        self._buffer += self._ssl.read(65536)
                    return
            except ssl.SSLWantReadError:
                await self._flush()  # e.g. a key update answers
                if not await self._feed():
                    raise asyncio.IncompleteReadError(bytes(self._buffer), None) from None
            except (ssl.SSLZeroReturnError, ssl.SSLEOFError):
                raise asyncio.IncompleteReadError(bytes(self._buffer), None) from None

    async def readexactly(self, count: int) -> bytes:
        while len(self._buffer) < count:
            await self._fill()
        out = bytes(self._buffer[:count])
        del self._buffer[:count]
        return out

    def write(self, data: bytes) -> None:
        self._ssl.write(data)
        encrypted = self._out.read()
        if encrypted:
            self.raw_writer.write(encrypted)

    async def drain(self) -> None:
        await self.raw_writer.drain()

    def is_closing(self) -> bool:
        return self.raw_writer.is_closing()

    def close(self) -> None:
        self.raw_writer.close()

    def get_extra_info(self, name: str, default=None):
        return self.raw_writer.get_extra_info(name, default)

    async def unwrap(self, timeout: float = 10.0) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Exchange close_notify (RFC 8446 6.1) and hand the plain channel back (6.16)."""

        async def shutdown() -> None:
            while True:
                try:
                    self._ssl.unwrap()
                    break
                except ssl.SSLWantReadError:
                    await self._flush()
                    if not await self._feed():
                        break
                except ssl.SSLWantWriteError:
                    await self._flush()
            await self._flush()

        await asyncio.wait_for(shutdown(), timeout)
        rest = self._in.read()  # plain bytes that came in behind the close_notify
        if rest:
            self.raw_reader.feed_data(rest)
        return self.raw_reader, self.raw_writer


@dataclass
class AuthState:
    """Where one connection is in the SASL exchange (6.15 steps 7-11)."""

    mechanism: str | None = None
    challenged: bool = False  # PLAIN: the empty challenge has been sent
    failures: int = 0
    authenticated: bool = False
    last_error: str = field(default="")
