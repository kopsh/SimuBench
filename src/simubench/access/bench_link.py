"""Connected mode: a script in its own process drives the bench a service is running (D63).

``PYVISA_LIBRARY=127.0.0.1:4881@simubench`` makes ``pyvisa.ResourceManager()`` connect to the
service instead of opening a bench of its own, so the script's GPIB, USB and TCPIP addresses work
unchanged while the Web Bench or the TUI watches the same bench.

The transport is a line of JSON per request over one TCP connection (loopback only, like the SCPI
ports): any transport would do, because what a script sees is decided by the *links* of links.py,
which run in the service exactly as they do in process. This module only carries their calls:

* ``{"op": "hello"}`` answers with the bench's instruments and addresses;
* ``{"op": "open", "id": n, "name": ..., "kind": ..., "board": b}`` makes a link, one per VISA session;
* ``{"op": "call", "id": n, "method": m, "args": [...]}`` runs a link method (only the ones of
  :data:`LINK_METHODS`); a failure comes back with the VISA status in ``code``;
* closing the connection closes its links, so a script that dies drops its locks.

Bytes travel as ``{"$b": "<base64>"}``. The service counts every link as a remote client: the UI
refuses to change an instrument a script is connected to (#121).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import logging
import socket
import threading
from types import SimpleNamespace
from typing import Any

from pyvisa import constants

from ..engine.faults import LinkDown
from ..engine.session import Session
from .bus import BusError
from .links import Link, make_link

log = logging.getLogger("simubench.link")

DEFAULT_PORT = 4881
PROTOCOL = 1
MAX_LINE = 1 << 27  # a megabyte waveform block as base64 JSON fits many times over

#: The link methods a client may call: the whole surface of :class:`~simubench.access.links.Link`.
LINK_METHODS = (
    "guard",
    "lock",
    "unlock",
    "spend_timeout",
    "wait_srq",
    "consume_srq",
    "write",
    "read_message",
    "clear",
    "trigger",
    "read_stb",
    "control_ren",
    "srq_ready",
    "attribute",
    "gpib_command",
    "gpib_send_ifc",
    "gpib_control_atn",
    "gpib_pass_control",
    "usb_control",
)
KINDS = ("net", "gpib", "gpib_intfc", "usb")


def encode(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"$b": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value: Any) -> Any:
    if isinstance(value, dict) and "$b" in value:
        return base64.b64decode(value["$b"])
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


# ---- the service side -------------------------------------------------------------------------------------------


class BenchLinkServer:
    """Serves the links of one bench session to scripts in other processes."""

    def __init__(self, session: Session, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.session = session
        self.host, self.port = host, port
        #: instrument -> the addresses the service serves it at (socket, HiSLIP, VXI-11), set by the
        #: service once its servers are up: their ports are its choice, a script must find them.
        self.served: dict[str, list[str]] = {}
        self.server: asyncio.AbstractServer | None = None
        self._clients: dict[str, tuple[str | None, Link]] = {}  # label -> (instrument, link)
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, self.host, self.port, limit=MAX_LINE)
        self.port = self.server.sockets[0].getsockname()[1]
        log.info("bench link at %s:%d", self.host, self.port)

    async def stop(self) -> None:
        for writer in list(self._writers):
            writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    def address(self) -> str:
        """What goes before ``@simubench`` in ``PYVISA_LIBRARY``."""
        return f"{self.host}:{self.port}"

    def clients(self, instrument: str | None = None) -> list[str]:
        return sorted(
            label for label, (name, _) in self._clients.items() if instrument is None or name == instrument
        )

    def holds_lock(self, label: str) -> bool:
        entry = self._clients.get(label)
        return bool(entry and entry[1].lock_depth)

    def _describe(self) -> dict[str, Any]:
        instruments = [
            {
                "name": name,
                "address": inst.instance.address,
                "idn": inst.idn,
                "served": self.served.get(name, []),
            }
            for name, inst in self.session.instruments.items()
        ]
        return {"ok": True, "protocol": PROTOCOL, "instruments": instruments}

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        host, port = writer.get_extra_info("peername")[:2]
        links: dict[int, Link] = {}
        labels: dict[int, str] = {}
        self._writers.add(writer)
        try:
            while line := await reader.readline():
                try:
                    request = json.loads(line)
                except ValueError:
                    break  # not our protocol: drop the connection
                reply = await self._handle(request, links, labels, f"{host}:{port}")
                writer.write(json.dumps(reply, separators=(",", ":")).encode() + b"\n")
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        finally:
            self._writers.discard(writer)
            for link_id, link in links.items():  # a script that goes away drops its locks
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(link.close)
                self._clients.pop(labels[link_id], None)
            writer.close()

    async def _handle(
        self, request: dict[str, Any], links: dict[int, Link], labels: dict[int, str], peer: str
    ) -> dict[str, Any]:
        op = request.get("op")
        if op == "hello":
            return self._describe()
        if op == "open":
            return self._open(request, links, labels, peer)
        if op == "call":
            link = links.get(request.get("id", -1))
            method = request.get("method")
            if link is None or method not in LINK_METHODS:
                return {
                    "ok": False,
                    "code": constants.VI_ERROR_INV_OBJECT,
                    "message": "no such link or method",
                }
            args = decode(request.get("args", []))
            try:
                result = await asyncio.to_thread(getattr(link, method), *args)
            except BusError as exc:
                return {"ok": False, "code": exc.code, "message": str(exc)}
            except LinkDown as exc:
                return {"ok": False, "code": constants.VI_ERROR_CONN_LOST, "message": str(exc)}
            except Exception as exc:  # a link bug must not take the service down
                log.exception("bench link call %s failed", method)
                return {"ok": False, "code": constants.VI_ERROR_SYSTEM_ERROR, "message": str(exc)}
            return {"ok": True, "result": encode(result)}
        if op == "close":
            link_id = request.get("id", -1)
            link = links.pop(link_id, None)
            if link is not None:
                await asyncio.to_thread(link.close)
                self._clients.pop(labels.pop(link_id, ""), None)
            return {"ok": True}
        return {"ok": False, "code": constants.VI_ERROR_NSUP_OPER, "message": f"unknown op {op!r}"}

    def _open(
        self, request: dict[str, Any], links: dict[int, Link], labels: dict[int, str], peer: str
    ) -> dict[str, Any]:
        name, kind, board = request.get("name"), request.get("kind"), int(request.get("board", 0))
        link_id = request.get("id")
        if kind not in KINDS or not isinstance(link_id, int):
            return {"ok": False, "code": constants.VI_ERROR_INV_SETUP, "message": "bad open request"}
        if kind != "gpib_intfc" and name not in self.session.instruments:
            return {"ok": False, "code": constants.VI_ERROR_RSRC_NFOUND, "message": f"no instrument {name!r}"}
        label = f"bench:{peer}#{link_id:04x}"
        try:
            link = make_link(self.session, name or "", kind, board, client=label)
        except ValueError as exc:  # e.g. two devices at one GPIB address
            return {"ok": False, "code": constants.VI_ERROR_INV_SETUP, "message": str(exc)}
        links[link_id], labels[link_id] = link, label
        self._clients[label] = (None if kind == "gpib_intfc" else name, link)
        return {"ok": True}


# ---- the script's side ------------------------------------------------------------------------------------------------


class LinkConnection:
    """One TCP connection to a bench link server; requests are answered in order."""

    def __init__(self, address: str, timeout: float = 5.0) -> None:
        host, _, port = address.rpartition(":")
        self.address = address
        self.sock = socket.create_connection((host or "127.0.0.1", int(port)), timeout=timeout)
        self.sock.settimeout(None)  # a read may wait as long as the bench does
        self.file = self.sock.makefile("rb")
        self._lock = threading.Lock()

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            try:
                self.sock.sendall(json.dumps(message, separators=(",", ":")).encode() + b"\n")
                line = self.file.readline()
            except OSError as exc:
                raise BusError(constants.VI_ERROR_CONN_LOST, f"the bench link broke: {exc}") from None
        if not line:
            raise BusError(constants.VI_ERROR_CONN_LOST, "the bench link was closed")
        return json.loads(line)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.file.close()
            self.sock.close()


class RemoteLink(Link):
    """A link whose operations run in the service: the same methods, carried over the connection."""

    def __init__(self, conn: LinkConnection, link_id: int, name: str | None, kind: str) -> None:
        self._conn, self._id = conn, link_id
        self.kind = kind
        self.name, self.lock_depth = name, 0  # the Link fields; the state itself is the service's

    def _call(self, method: str, *args: Any) -> Any:
        reply = self._conn.request({"op": "call", "id": self._id, "method": method, "args": encode(args)})
        if not reply.get("ok"):
            raise BusError(
                int(reply.get("code", constants.VI_ERROR_SYSTEM_ERROR)), str(reply.get("message", ""))
            )
        return decode(reply.get("result"))

    def close(self) -> None:
        with contextlib.suppress(BusError):
            self._conn.request({"op": "close", "id": self._id})
        self._conn.close()


def _forward(method: str):
    def call(self: RemoteLink, *args: Any) -> Any:
        return self._call(method, *args)

    call.__name__ = method
    return call


for _method in LINK_METHODS:
    setattr(RemoteLink, _method, _forward(_method))


class RemoteBench:
    """What the PyVISA backend needs of a bench it does not own: who is on it, and links to them.

    Every link has a connection of its own: a wait on one resource (a 25 s ``wait_for_srq``) must not
    hold up a query to another, and the lock of a session goes when its connection does."""

    def __init__(self, address: str) -> None:
        self.address = address
        hello_conn = LinkConnection(address)
        try:
            hello = hello_conn.request({"op": "hello"})
        finally:
            hello_conn.close()
        if not hello.get("ok") or hello.get("protocol") != PROTOCOL:
            raise BusError(constants.VI_ERROR_SYSTEM_ERROR, f"{address} is not a SimuBench bench link")
        # The shape the backend reads from a session: instruments with an ``instance.address``,
        # plus the addresses the service serves them at (their ports are the service's choice).
        self.instruments = {
            i["name"]: SimpleNamespace(
                instance=SimpleNamespace(address=i["address"]), idn=i["idn"], served=i.get("served", [])
            )
            for i in hello["instruments"]
        }
        self._ids = itertools.count(1)
        self._connections: list[LinkConnection] = []

    def open_link(self, name: str | None, kind: str, board: int) -> RemoteLink:
        link_id = next(self._ids)
        conn = LinkConnection(self.address)
        reply = conn.request({"op": "open", "id": link_id, "name": name, "kind": kind, "board": board})
        if not reply.get("ok"):
            conn.close()
            raise BusError(
                int(reply.get("code", constants.VI_ERROR_RSRC_NFOUND)), str(reply.get("message", ""))
            )
        self._connections.append(conn)
        return RemoteLink(conn, link_id, name, kind)

    def close(self) -> None:
        for conn in self._connections:
            conn.close()
        self._connections.clear()
