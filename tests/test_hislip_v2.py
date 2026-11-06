"""HiSLIP 2.0 (IVI-6.1, D41): version negotiation, overlapped mode, device-clear feature negotiation,
GetDescriptors. The secure connection capability is in test_hislip_tls.py."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest
from tests.test_hislip import BENCH, VENDOR, recv_msg, send_msg

from simubench.access import hislip_server as h
from simubench.api import open_bench

MID0 = h.INITIAL_MESSAGE_ID


class Client:
    """A HiSLIP client of a chosen protocol version; keeps the MessageIDs the way a client does."""

    def __init__(self, port: int, version: int = 0x0200, sub: bytes = b"hislip1") -> None:
        self.sync = socket.create_connection(("127.0.0.1", port), timeout=5)
        send_msg(self.sync, h.INITIALIZE, 0, (version << 16) | VENDOR, sub)
        msg_type, self.init_control, parameter, _ = recv_msg(self.sync)
        assert msg_type == h.INITIALIZE_RESPONSE
        self.version, self.session_id = parameter >> 16, parameter & 0xFFFF
        self.async_ = socket.create_connection(("127.0.0.1", port), timeout=5)
        send_msg(self.async_, h.ASYNC_INITIALIZE, 0, self.session_id)
        msg_type, self.capabilities, _, _ = recv_msg(self.async_)
        assert msg_type == h.ASYNC_INITIALIZE_RESPONSE
        self.mid = MID0

    def send(self, text: str, rmt: int = 0) -> int:
        mid, self.mid = self.mid, (self.mid + 2) & 0xFFFF_FFFF
        send_msg(self.sync, h.DATA_END, rmt, mid, text.encode())
        return mid

    def read(self) -> tuple[int, bytes]:
        msg_type, _, parameter, payload = recv_msg(self.sync)
        assert msg_type == h.DATA_END, msg_type
        return parameter, payload

    def status(self, delivered: int, rmt: int = 0) -> int:
        send_msg(self.async_, h.ASYNC_STATUS_QUERY, rmt, delivered)
        msg_type, control, _, _ = recv_msg(self.async_)
        assert msg_type == h.ASYNC_STATUS_RESPONSE
        return control

    def device_clear(self, request_features: int) -> tuple[int, int]:
        """Both halves of the transaction; returns (the server's preference, the agreed features)."""
        send_msg(self.async_, h.ASYNC_DEVICE_CLEAR)
        msg_type, preferred, _, _ = recv_msg(self.async_)
        assert msg_type == h.ASYNC_DEVICE_CLEAR_ACKNOWLEDGE
        send_msg(self.sync, h.DEVICE_CLEAR_COMPLETE, request_features)
        msg_type, agreed, _, _ = recv_msg(self.sync)
        assert msg_type == h.DEVICE_CLEAR_ACKNOWLEDGE
        self.mid = MID0
        return preferred, agreed

    def close(self) -> None:
        self.sync.close()
        self.async_.close()


def serve(overlapped: bool):
    @pytest.fixture
    async def rig():
        bench = open_bench(BENCH)
        server = h.HislipServer(bench, port=0, overlapped=overlapped)
        await server.start()
        yield SimpleNamespace(bench=bench, server=server, port=server.port)
        await server.stop()
        bench.close()

    return rig


sync_rig = serve(False)
overlap_rig = serve(True)


# ---- version and mode announced at initialization -----------------------------------------------------


async def test_a_2_0_client_gets_2_0_and_a_1_0_client_gets_1_0(sync_rig):
    def run() -> None:
        new, old = Client(sync_rig.port, 0x0200), Client(sync_rig.port, 0x0100)
        assert (new.version, old.version) == (0x0200, 0x0100)  # the lower of the two versions
        assert new.init_control == 0 and new.capabilities == 0  # synchronized; no secure connection
        new.close()
        old.close()

    await asyncio.to_thread(run)


async def test_an_overlapped_server_says_so_in_initialize_response(overlap_rig):
    def run() -> None:
        c = Client(overlap_rig.port)
        assert c.init_control & 1  # bit 0: the mode the server starts in
        c.close()

    await asyncio.to_thread(run)


# ---- overlapped mode ---------------------------------------------------------------------------------


async def test_overlapped_queries_are_all_answered_in_order(overlap_rig):
    """3: input and output are buffered; nothing is interrupted, nothing is dropped."""

    def run() -> None:
        c = Client(overlap_rig.port)
        c.send("*IDN?")
        c.send("*OPC?")
        c.send("SYST:ERR?")  # three queries in a row, none read yet
        ids, replies = [], []
        for _ in range(3):
            mid, payload = c.read()
            ids.append(mid)
            replies.append(payload)
        assert replies[0].startswith(b"SIMUBENCH") and replies[1] == b"1\n" and replies[2].startswith(b"0,")
        assert ids == [MID0, MID0 + 2, MID0 + 4]  # the server's own counter, two more per message (3.2.1)
        c.close()

    await asyncio.to_thread(run)


async def test_overlapped_mav_follows_the_message_id_the_client_reports(overlap_rig):
    """6.14.2: MAV is set while a message the client has not delivered yet exists."""

    def run() -> None:
        c = Client(overlap_rig.port)
        nothing = MID0 - 2  # what a client reports before it has delivered anything
        assert c.status(nothing) & 0x10 == 0
        c.send("*IDN?")
        c.read()
        assert c.status(nothing) & 0x10  # the reply exists and the client says it has taken nothing
        assert c.status(MID0) & 0x10 == 0  # it has taken message 0xffffff00: nothing is left
        c.close()

    await asyncio.to_thread(run)


async def test_overlapped_mode_ignores_rmt_flags(overlap_rig):
    def run() -> None:
        c = Client(overlap_rig.port)
        c.send("*IDN?", rmt=0)
        c.send("*OPC?", rmt=0)  # no RMT-delivered in between: synchronized mode would call it interrupted
        assert c.read()[1].startswith(b"SIMUBENCH") and c.read()[1] == b"1\n"
        c.close()

    await asyncio.to_thread(run)


# ---- device clear negotiates the mode (6.12.1) ----------------------------------------------------------


async def test_device_clear_proposes_the_servers_mode_and_accepts_the_clients(sync_rig):
    def run() -> None:
        c = Client(sync_rig.port)
        preferred, agreed = c.device_clear(request_features=1)  # the client asks for overlapped
        assert (preferred, agreed) == (0, 1)
        c.send("*IDN?")
        c.send("*OPC?")  # two queries in a row: only overlapped mode answers both
        assert c.read()[1].startswith(b"SIMUBENCH") and c.read()[1] == b"1\n"
        c.close()

    await asyncio.to_thread(run)


async def test_after_a_switch_to_overlapped_the_counter_starts_over_and_back_again(sync_rig):
    def run() -> None:
        c = Client(sync_rig.port)
        assert c.device_clear(1)[1] == 1
        c.send("*IDN?")
        c.send("*OPC?")
        assert [c.read()[0], c.read()[0]] == [MID0, MID0 + 2]  # both answered, counting from the start
        assert c.device_clear(0)[1] == 0  # and back to synchronized
        c.send("*OPC?")
        mid, payload = c.read()
        assert (mid, payload) == (MID0, b"1\n")  # the response carries the client's own MessageID
        c.close()

    await asyncio.to_thread(run)


async def test_device_clear_proposes_overlap_when_the_server_starts_there(overlap_rig):
    def run() -> None:
        c = Client(overlap_rig.port)
        c.send("*IDN?")
        c.read()
        preferred, agreed = c.device_clear(request_features=1)
        assert (preferred, agreed) == (1, 1)
        c.send("*OPC?")
        assert c.read() == (MID0, b"1\n")  # the counter was reset by the clear (3.2.1)
        c.close()

    await asyncio.to_thread(run)


# ---- GetDescriptors ----------------------------------------------------------------------------------------


async def test_get_descriptors_answers_on_both_channels(sync_rig):
    def run() -> None:
        c = Client(sync_rig.port)
        for sock in (c.sync, c.async_):
            send_msg(sock, h.GET_DESCRIPTORS)
            msg_type, _, _, payload = recv_msg(sock)
            assert msg_type == h.GET_DESCRIPTORS_RESPONSE
            assert payload == b""  # no capability has a descriptor to give without TLS
        c.close()

    await asyncio.to_thread(run)


async def test_get_descriptors_does_not_exist_before_2_0(sync_rig):
    def run() -> None:
        c = Client(sync_rig.port, 0x0100)
        send_msg(c.sync, h.GET_DESCRIPTORS)
        msg_type, control, _, _ = recv_msg(c.sync)
        assert (msg_type, control) == (h.ERROR, h.ERR_UNRECOGNIZED_TYPE)
        c.close()

    await asyncio.to_thread(run)
