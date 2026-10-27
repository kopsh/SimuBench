"""SCPI network instruments: one raw TCP socket per instrument (D9, architecture.md 7.4).

Any VISA implementation or plain socket client can connect with
``TCPIP0::127.0.0.1::<port>::SOCKET``. Messages end with a line feed; responses are sent as soon
as a query has produced them.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..engine.faults import LinkDown
from ..engine.session import Session

log = logging.getLogger("simubench.scpi")


@dataclass
class ScpiListener:
    instrument: str
    host: str
    port: int
    server: asyncio.AbstractServer | None = None
    clients: set[asyncio.StreamWriter] = field(default_factory=set)

    @property
    def address(self) -> str:
        return f"TCPIP0::{self.host}::{self.port}::SOCKET"


class ScpiServer:
    def __init__(self, session: Session, host: str = "127.0.0.1", base_port: int = 5025) -> None:
        self.session = session
        self.host = host
        self.base_port = base_port
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
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                await asyncio.to_thread(self.session.write, lst.instrument, line, client)
                while True:
                    out = await asyncio.to_thread(self.session.read, lst.instrument, client)
                    if out is None:
                        break
                    writer.write(out)
                    await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        except LinkDown:
            log.info("SCPI %s: link fault, closing %s", lst.instrument, client)
        finally:
            lst.clients.discard(writer)
            writer.close()

    async def stop(self) -> None:
        for lst in self.listeners:
            if lst.server:
                lst.server.close()
                for w in list(lst.clients):
                    w.close()
                await lst.server.wait_closed()
        self.listeners = []
