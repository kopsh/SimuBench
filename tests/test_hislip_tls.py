"""HiSLIP 2.0 secure connection capability (IVI-6.1 4, 5, 6.15, 6.16, D41): TLS switched on and off
in the middle of a connection, SASL login, the encryption modes and the descriptors."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import ssl
import struct
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.test_hislip import BENCH, VENDOR, recv_msg, send_msg

from simubench.access import hislip_server as h
from simubench.access.hislip_tls import SecureConfig, SecureStream, read_users
from simubench.api import open_bench
from simubench.server.manager import hislip_options_of

DATA_DIR = Path(__file__).parent / "data" / "hislip"
CERT, KEY = DATA_DIR / "test-only-cert.pem", DATA_DIR / "test-only-key.pem"
MID0 = h.INITIAL_MESSAGE_ID


def client_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False  # the certificate is self-signed for localhost; it is the one trusted
    ctx.load_verify_locations(CERT)
    return ctx


class Client:
    """A HiSLIP 2.0 client that can switch encryption on and off, and log in."""

    def __init__(self, port: int, version: int = 0x0200) -> None:
        self.port = port
        self.sync = socket.create_connection(("127.0.0.1", port), timeout=5)
        send_msg(self.sync, h.INITIALIZE, 0, (version << 16) | VENDOR, b"hislip1")
        msg_type, self.init_control, parameter, _ = recv_msg(self.sync)
        self.reply_type = msg_type
        if msg_type != h.INITIALIZE_RESPONSE:
            return
        self.version, session_id = parameter >> 16, parameter & 0xFFFF
        self.async_ = socket.create_connection(("127.0.0.1", port), timeout=5)
        send_msg(self.async_, h.ASYNC_INITIALIZE, 0, session_id)
        msg_type, self.capabilities, _, _ = recv_msg(self.async_)
        assert msg_type == h.ASYNC_INITIALIZE_RESPONSE
        self.mid = MID0
        self.received = MID0 - 2  # the MessageID of the last response taken

    # ---- ordinary traffic ----
    def send(self, text: str, rmt: int = 0) -> int:
        mid, self.mid = self.mid, self.mid + 2
        send_msg(self.sync, h.DATA_END, rmt, mid, text.encode())
        return mid

    def query(self, text: str) -> bytes:
        self.send(text)
        msg_type, _, _, payload = recv_msg(self.sync)
        assert msg_type == h.DATA_END, msg_type
        return payload

    # ---- encryption ----
    def start_tls_request(self, rmt: int = 0, sent: int | None = None, received: int | None = None) -> int:
        sent = self.mid - 2 if sent is None else sent
        received = MID0 - 2 if received is None else received
        send_msg(self.async_, h.ASYNC_START_TLS, rmt, sent, struct.pack("!I", received))
        msg_type, control, _, _ = recv_msg(self.async_)
        assert msg_type == h.ASYNC_START_TLS_RESPONSE
        return control

    def start_tls(self, rmt: int = 0) -> None:
        assert self.start_tls_request(rmt=rmt) == h.TLS_SUCCESS
        self.async_ = client_context().wrap_socket(self.async_)  # handshake on the asynchronous channel
        send_msg(self.sync, h.START_TLS)
        self.sync = client_context().wrap_socket(self.sync)  # then on the synchronous one

    def end_tls(self) -> int:
        send_msg(self.async_, h.ASYNC_END_TLS, 0, self.mid - 2, struct.pack("!I", MID0 - 2))
        msg_type, control, _, _ = recv_msg(self.async_)
        assert msg_type == h.ASYNC_END_TLS_RESPONSE
        if control == h.TLS_SUCCESS:
            self.async_ = self.async_.unwrap()
            send_msg(self.sync, h.END_TLS)
            self.sync = self.sync.unwrap()
        return control

    # ---- login ----
    def mechanisms(self) -> list[str]:
        send_msg(self.sync, h.GET_SASL_MECHANISM_LIST)
        msg_type, _, _, payload = recv_msg(self.sync)
        assert msg_type == h.GET_SASL_MECHANISM_LIST_RESPONSE
        return payload.decode().split()

    def authenticate(self, mechanism: str, response: bytes | None) -> tuple[int, int, bytes]:
        """AuthenticationStart and the first AuthenticationExchange; returns the result's fields."""
        send_msg(self.sync, h.AUTHENTICATION_START, 0, 0, mechanism.encode())
        send_msg(self.sync, h.AUTHENTICATION_EXCHANGE, 0, 0, b"" if response is None else response)
        msg_type, control, parameter, payload = recv_msg(self.sync)
        assert msg_type == h.AUTHENTICATION_RESULT, msg_type
        return control, parameter, payload

    def login(self) -> None:
        if "PLAIN" in self.mechanisms():
            assert self.authenticate("PLAIN", b"\0alice\0s3cret")[0] == 1
        else:
            assert self.authenticate("ANONYMOUS", b"tracer")[0] == 1

    def close(self) -> None:
        for sock in (getattr(self, "sync", None), getattr(self, "async_", None)):
            if sock is not None:
                sock.close()


def serve(secure: dict | None = None, **server_options):
    """A fixture for a server with the secure connection capability configured as given."""

    @pytest.fixture
    async def rig():
        bench = open_bench(BENCH)
        config = SecureConfig.from_files(CERT, KEY, **(secure or {}))
        server = h.HislipServer(bench, port=0, secure=config, **server_options)
        await server.start()
        yield SimpleNamespace(bench=bench, server=server, port=server.port)
        await server.stop()
        bench.close()

    return rig


optional_rig = serve()
mandatory_rig = serve(secure={"mandatory": True})
initial_rig = serve(secure={"initial": True})
plain_rig = serve(secure={"users": {"alice": "s3cret", "bob": "hunter2"}})


# ---- what the server announces ------------------------------------------------------------------------


async def test_optional_encryption_is_announced_as_a_capability_only(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        assert c.init_control == 0 and c.capabilities & 1  # bits 1 and 2 clear; capability bit set
        assert c.version == 0x0200
        c.close()

    await asyncio.to_thread(run)


async def test_mandatory_and_initial_encryption_bits(mandatory_rig, initial_rig):
    def run() -> None:
        m, i = Client(mandatory_rig.port), Client(initial_rig.port)
        assert m.init_control == 0b110  # mandatory implies the initial encrypted connection
        assert i.init_control == 0b100  # optional, but the client must switch it on first
        m.close()
        i.close()

    await asyncio.to_thread(run)


async def test_a_1_0_client_cannot_use_a_server_that_needs_encryption_first(mandatory_rig):
    def run() -> None:
        c = Client(mandatory_rig.port, version=0x0100)
        assert c.reply_type == h.FATAL_ERROR and c.init_control == h.FATAL_SECURE
        c.close()

    await asyncio.to_thread(run)


async def test_a_1_0_client_is_served_in_the_clear_when_encryption_is_optional(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port, version=0x0100)
        assert c.version == 0x0100 and c.capabilities == 0  # no capability below 2.0
        assert c.query("*IDN?").startswith(b"SIMUBENCH")
        c.close()

    await asyncio.to_thread(run)


async def test_without_the_capability_start_tls_is_an_error():
    bench = open_bench(BENCH)
    server = h.HislipServer(bench, port=0)
    await server.start()

    def run() -> None:
        c = Client(server.port)
        assert c.capabilities == 0
        assert c.start_tls_request() == h.TLS_ERROR
        c.close()

    try:
        await asyncio.to_thread(run)
    finally:
        await server.stop()
        bench.close()


# ---- required encryption comes first -------------------------------------------------------------------


async def test_anything_but_the_tls_setup_is_fatal_when_encryption_is_required(mandatory_rig):
    def run() -> None:
        c = Client(mandatory_rig.port)
        c.send("*IDN?")
        msg_type, control, _, _ = recv_msg(c.sync)
        assert (msg_type, control) == (h.FATAL_ERROR, h.FATAL_SECURE)
        c.close()

    await asyncio.to_thread(run)


async def test_maximum_message_size_is_allowed_before_the_secure_connection(initial_rig):
    def run() -> None:
        c = Client(initial_rig.port)
        send_msg(c.async_, h.ASYNC_MAXIMUM_MESSAGE_SIZE, 0, 0, struct.pack("!Q", 65536))
        assert recv_msg(c.async_)[0] == h.ASYNC_MAXIMUM_MESSAGE_SIZE_RESPONSE
        c.start_tls()
        c.close()

    await asyncio.to_thread(run)


# ---- the transaction -----------------------------------------------------------------------------------------


async def test_a_query_over_both_encrypted_channels(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        c.start_tls()
        assert isinstance(c.sync, ssl.SSLSocket) and isinstance(c.async_, ssl.SSLSocket)
        c.login()
        assert c.query("*IDN?").startswith(b"SIMUBENCH")
        send_msg(c.async_, h.ASYNC_STATUS_QUERY, 1, c.mid - 2)  # the asynchronous channel works too
        msg_type, control, _, _ = recv_msg(c.async_)
        assert msg_type == h.ASYNC_STATUS_RESPONSE and control & 0x10 == 0
        c.close()

    await asyncio.to_thread(run)


async def test_a_service_request_arrives_over_the_encrypted_asynchronous_channel(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        c.start_tls()
        c.login()
        c.send("*CLS;*ESE 1;*SRE 32")
        c.query("*OPC?")
        c.send("*OPC")
        msg_type, control, _, _ = recv_msg(c.async_)
        assert msg_type == h.ASYNC_SERVICE_REQUEST and control & 0x60 == 0x60
        c.close()

    await asyncio.to_thread(run)


async def test_start_tls_is_busy_while_a_response_is_unread(optional_rig):
    """6.15 step 1: the buffers must be empty; RMT-delivered says the response was taken."""

    def run() -> None:
        c = Client(optional_rig.port)
        mid = c.send("*IDN?")
        msg_type, _, _, _ = recv_msg(c.sync)  # the response is here, but not yet delivered to the app
        assert msg_type == h.DATA_END
        assert c.start_tls_request(rmt=0, sent=mid) == h.TLS_BUSY
        assert c.start_tls_request(rmt=1, sent=mid) == h.TLS_SUCCESS  # now it was delivered
        c.async_ = client_context().wrap_socket(c.async_)
        c.close()

    await asyncio.to_thread(run)


async def test_start_tls_twice_is_an_error(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        c.start_tls()
        assert c.start_tls_request() == h.TLS_ERROR  # encryption is already on
        c.close()

    await asyncio.to_thread(run)


async def test_start_tls_on_the_sync_channel_alone_is_refused(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        send_msg(c.sync, h.START_TLS)
        msg_type, _, _, _ = recv_msg(c.sync)
        assert msg_type == h.ERROR  # it must follow a successful AsyncStartTLS
        c.close()

    await asyncio.to_thread(run)


async def test_optional_encryption_can_be_switched_off_again(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        c.start_tls()
        c.login()
        assert c.end_tls() == h.TLS_SUCCESS
        assert c.sync.cipher() is None and c.async_.cipher() is None  # unwrapped: no TLS on either channel
        assert c.query("*IDN?").startswith(b"SIMUBENCH")  # in the clear, as before
        c.start_tls(rmt=1)  # and on again (the reply above was taken: RMT-delivered); the login is gone
        send_msg(c.sync, h.DATA_END, 0, c.mid, b"*IDN?")
        msg_type, control, _, _ = recv_msg(c.sync)
        assert (msg_type, control) == (h.ERROR, h.ERR_AUTH)
        c.close()

    await asyncio.to_thread(run)


async def test_mandatory_encryption_cannot_be_switched_off(mandatory_rig):
    def run() -> None:
        c = Client(mandatory_rig.port)
        c.start_tls()
        c.login()
        assert c.end_tls() == h.TLS_ERROR
        assert c.query("*IDN?").startswith(b"SIMUBENCH")  # still encrypted and working
        c.close()

    await asyncio.to_thread(run)


async def test_a_handshake_that_fails_closes_the_connection(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        assert c.start_tls_request() == h.TLS_SUCCESS
        c.async_.sendall(b"this is not a TLS ClientHello" * 4)
        c.async_.settimeout(5)
        with pytest.raises((ConnectionError, socket.timeout)):
            while c.async_.recv(100):
                pass
            raise ConnectionError("closed")
        c.close()

    await asyncio.to_thread(run)
    assert optional_rig.server._sessions == {}


# ---- authentication --------------------------------------------------------------------------------------------


async def test_anonymous_is_offered_when_there_are_no_users(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        c.start_tls()
        assert c.mechanisms() == ["ANONYMOUS"]
        assert c.authenticate("ANONYMOUS", b"tracer") == (1, 0, b"")
        c.close()

    await asyncio.to_thread(run)


async def test_messages_before_the_login_are_refused_after_it_they_work(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        c.start_tls()
        mid = c.send("*IDN?")
        msg_type, control, _, _ = recv_msg(c.sync)
        assert (msg_type, control) == (h.ERROR, h.ERR_AUTH)  # non-fatal: the connection stays
        c.login()
        assert mid is not None and c.query("*IDN?").startswith(b"SIMUBENCH")
        c.close()

    await asyncio.to_thread(run)


async def test_plain_accepts_the_right_password_and_refuses_the_wrong_one(plain_rig):
    def run() -> None:
        c = Client(plain_rig.port)
        c.start_tls()
        assert c.mechanisms() == ["PLAIN"]
        control, code, text = c.authenticate("PLAIN", b"\0alice\0wrong")
        assert (control, code) == (0, 1) and b"failed" in text
        assert c.authenticate("PLAIN", b"\0nobody\0s3cret")[0] == 0  # an unknown name looks the same
        assert c.authenticate("PLAIN", b"\0alice\0s3cret") == (1, 0, b"")  # and the client may retry
        assert c.query("*IDN?").startswith(b"SIMUBENCH")
        c.close()

    await asyncio.to_thread(run)


async def test_repeated_failures_are_slowed_down(plain_rig):
    def run() -> None:
        c = Client(plain_rig.port)
        c.start_tls()
        started = time.monotonic()
        for _ in range(3):
            assert c.authenticate("PLAIN", b"\0alice\0no")[0] == 0
        assert time.monotonic() - started >= 0.3  # 0.05 + 0.10 + 0.15 s
        c.close()

    await asyncio.to_thread(run)


async def test_plain_without_an_initial_response_is_challenged_with_an_empty_one(plain_rig):
    def run() -> None:
        c = Client(plain_rig.port)
        c.start_tls()
        send_msg(c.sync, h.AUTHENTICATION_START, 0, 0, b"PLAIN")
        send_msg(c.sync, h.AUTHENTICATION_EXCHANGE)  # no data: the client waits for the challenge
        msg_type, _, _, payload = recv_msg(c.sync)
        assert (msg_type, payload) == (h.AUTHENTICATION_EXCHANGE, b"")
        send_msg(c.sync, h.AUTHENTICATION_EXCHANGE, 0, 0, b"bob\0bob\0hunter2")
        msg_type, control, _, _ = recv_msg(c.sync)
        assert (msg_type, control) == (h.AUTHENTICATION_RESULT, 1)
        c.close()

    await asyncio.to_thread(run)


async def test_an_unsupported_mechanism_is_a_non_fatal_error(plain_rig):
    def run() -> None:
        c = Client(plain_rig.port)
        c.start_tls()
        send_msg(c.sync, h.AUTHENTICATION_START, 0, 0, b"EXTERNAL")
        msg_type, control, _, _ = recv_msg(c.sync)
        assert (msg_type, control) == (h.ERROR, h.ERR_AUTH)
        assert c.authenticate("PLAIN", b"\0alice\0s3cret")[0] == 1  # the connection is still usable
        c.close()

    await asyncio.to_thread(run)


async def test_invalid_authentication_data_is_a_fatal_error(plain_rig):
    def run() -> None:
        c = Client(plain_rig.port)
        c.start_tls()
        send_msg(c.sync, h.AUTHENTICATION_START, 0, 0, b"PLAIN")
        send_msg(c.sync, h.AUTHENTICATION_EXCHANGE, 0, 0, b"no separators at all")
        msg_type, control, _, _ = recv_msg(c.sync)
        assert (msg_type, control) == (h.FATAL_ERROR, h.FATAL_SECURE)
        c.close()

    await asyncio.to_thread(run)


async def test_authentication_needs_the_encrypted_connection(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        send_msg(c.sync, h.GET_SASL_MECHANISM_LIST)
        msg_type, _, _, _ = recv_msg(c.sync)
        assert msg_type == h.ERROR  # SASL credentials are never sent in the clear
        c.close()

    await asyncio.to_thread(run)


# ---- descriptors ----------------------------------------------------------------------------------------------


def parse_descriptors(payload: bytes) -> dict[int, bytes]:
    out, pos = {}, 0
    while pos < len(payload):
        length, kind = struct.unpack_from("!HB", payload, pos)
        out[kind] = payload[pos + 3 : pos + 3 + length]
        pos += 3 + length
    return out


async def test_descriptors_describe_tls_and_the_last_error(plain_rig):
    def run() -> None:
        c = Client(plain_rig.port)
        send_msg(c.sync, h.GET_DESCRIPTORS)
        msg_type, _, _, payload = recv_msg(c.sync)
        assert msg_type == h.GET_DESCRIPTORS_RESPONSE
        d = parse_descriptors(payload)
        versions = struct.unpack(f"!{len(d[0]) // 2}H", d[0])
        assert 0x0303 in versions and versions == tuple(sorted(versions))  # TLS 1.2 is the floor
        assert d[1].startswith(b"TLS 1.2") and d[1].endswith(b"SASL PLAIN")
        assert d[2] == b""  # nothing has gone wrong yet
        assert c.start_tls_request() == h.TLS_SUCCESS
        c.close()

    await asyncio.to_thread(run)


async def test_the_last_error_descriptor_reports_a_refused_switch(optional_rig):
    def run() -> None:
        c = Client(optional_rig.port)
        c.start_tls()
        assert c.start_tls_request() == h.TLS_ERROR
        send_msg(c.async_, h.GET_DESCRIPTORS)
        _, _, _, payload = recv_msg(c.async_)
        assert b"cannot be switched on" in parse_descriptors(payload)[2]
        c.close()

    await asyncio.to_thread(run)


# ---- the secure stream itself ----------------------------------------------------------------------------------


async def test_a_client_hello_that_arrives_with_the_message_before_it_is_not_lost():
    """What asyncio's own start_tls would drop: the ClientHello already sits in the reader's buffer."""
    server_ctx = SecureConfig.from_files(CERT, KEY).context
    client_ctx = client_context()
    done: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    async def on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readexactly(4)  # the message that asks for TLS; the hello is right behind it
        stream = await SecureStream.accept(reader, writer, server_ctx, 5.0)
        done.set_result(await stream.readexactly(5))
        stream.write(b"world")
        await stream.drain()

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    def client() -> bytes:
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        tls = client_ctx.wrap_bio(incoming, outgoing, server_hostname="localhost")
        with contextlib.suppress(ssl.SSLWantReadError):
            tls.do_handshake()  # produces the ClientHello and then wants the server's answer
        sock.sendall(b"ASKS" + outgoing.read())  # one send: the request and the ClientHello together
        while True:
            try:
                tls.do_handshake()
                break
            except ssl.SSLWantReadError:
                incoming.write(sock.recv(65536))
        sock.sendall(_sealed(tls, outgoing, b"hello"))
        reply = b""
        while not reply:
            try:
                reply = tls.read(100)
            except ssl.SSLWantReadError:
                incoming.write(sock.recv(65536))
        sock.close()
        return reply

    def _sealed(tls: ssl.SSLObject, outgoing: ssl.MemoryBIO, data: bytes) -> bytes:
        tls.write(data)
        return outgoing.read()

    try:
        reply = await asyncio.to_thread(client)
        assert await asyncio.wait_for(done, 5) == b"hello" and reply == b"world"
    finally:
        server.close()
        await server.wait_closed()


def test_users_file_reads_name_and_password_lines(tmp_path):
    f = tmp_path / "users.txt"
    f.write_text("# who may log in\nalice:s3cret\n\nbob:pass:with:colons\n", encoding="utf-8")
    assert read_users(f) == {"alice": "s3cret", "bob": "pass:with:colons"}
    f.write_text("nonsense\n", encoding="utf-8")
    with pytest.raises(ValueError, match="name:password"):
        read_users(f)


# ---- the command line -------------------------------------------------------------------------------------------


def args(**given):
    base = {
        "hislip_overlapped": False,
        "hislip_cert": None,
        "hislip_key": None,
        "hislip_tls": "optional",
        "hislip_users": None,
    }
    return SimpleNamespace(**{**base, **given})


@pytest.mark.unit
def test_the_default_command_line_serves_hislip_without_the_capability():
    assert hislip_options_of(args()) == {"hislip_overlapped": False}
    assert hislip_options_of(args(hislip_overlapped=True)) == {"hislip_overlapped": True}


def test_certificate_and_key_switch_the_capability_on(tmp_path):
    users = tmp_path / "users.txt"
    users.write_text("alice:s3cret\n", encoding="utf-8")
    options = hislip_options_of(
        args(hislip_cert=str(CERT), hislip_key=str(KEY), hislip_tls="mandatory", hislip_users=str(users))
    )
    secure = options["hislip_secure"]
    assert secure.mandatory and secure.initial_required and secure.mechanisms() == ["PLAIN"]
    initial = hislip_options_of(args(hislip_cert=str(CERT), hislip_key=str(KEY), hislip_tls="initial"))
    assert initial["hislip_secure"].initial_required and not initial["hislip_secure"].mandatory
    assert initial["hislip_secure"].mechanisms() == ["ANONYMOUS"]


@pytest.mark.parametrize(
    "given",
    [
        {"hislip_cert": str(CERT)},  # a certificate without its key
        {"hislip_key": str(KEY)},
        {"hislip_tls": "mandatory"},  # a mode with nothing to encrypt with
        {"hislip_users": "users.txt"},
        # Users with optional encryption would look like protection and be none: a client that
        # never asks for TLS needs no login.
        {"hislip_cert": str(CERT), "hislip_key": str(KEY), "hislip_users": "users.txt"},
    ],
)
def test_options_that_cannot_work_alone_are_refused(given):
    with pytest.raises(SystemExit):
        hislip_options_of(args(**given))


@pytest.mark.unit
def test_a_service_request_waits_while_the_channel_is_switching():
    """poll_srq runs on the sync task; during a switch a plaintext message would corrupt the TLS stream."""
    sent: list[bytes] = []
    rising = SimpleNamespace(stb=lambda: 0x60)
    server = SimpleNamespace(session=SimpleNamespace(instrument=lambda name: rising), overlapped=False)
    sess = h.HislipSession(server, "scope", 1)
    sess.async_writer = SimpleNamespace(is_closing=lambda: False, write=sent.append)
    sess.switching = True
    sess.poll_srq()
    assert sent == [] and sess.last_stb == 0  # nothing written, and the edge is not used up
    sess.switching = False
    sess.poll_srq()
    assert len(sent) == 1 and sent[0][2] == h.ASYNC_SERVICE_REQUEST  # found again once it is over
