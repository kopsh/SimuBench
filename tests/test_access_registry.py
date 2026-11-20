"""The session registry is one table (#1475): a new access server is one row plus its own module.

The issue pins two seams here. LOCAL is judged from the connections themselves — the client labels
the port servers spell (``socket:``, ``hislip:``, ``vxi11:``, ``bench:``) are display names, and a
guard test keeps every layer from decoding them again. And a fifth access protocol registers into
``ServedSession.servers`` with zero per-protocol edits: clients, the remote judgement and stopping
all read the one table, which a fake server proves by never touching anything else.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from simubench.access import AccessServer, ClientConnection
from simubench.server.manager import ServedSession

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
#: The client label prefixes the four servers spell. They name a client for humans; nothing may
#: read a protocol out of them (that was ``is_remote``'s secret cipher, #1475).
LABEL_PREFIXES = ("socket:", "hislip:", "vxi11:", "bench:")


class FakeServer(AccessServer):
    """A fifth access protocol as the registry sees it: the AccessServer surface, nothing else."""

    def __init__(self, *connections: ClientConnection) -> None:
        self.open = list(connections)
        self.stopped = False

    def connections(self) -> list[ClientConnection]:
        return list(self.open)

    def addresses(self) -> dict[str, str]:
        return {}

    async def stop(self) -> None:
        self.stopped = True


def served_with(*rows: tuple[str, FakeServer]) -> tuple[ServedSession, list[dict]]:
    """A served session over a stand-in engine session, with the given servers registered."""
    events: list[dict] = []
    session = SimpleNamespace(
        events=SimpleNamespace(subscribe=lambda f: None, emit=events.append),
        clock=SimpleNamespace(confirm_advance=None, confirm_mode=None, mode="command"),
        instrument=lambda name: None,
        instruments={},
        cancel=lambda: None,
        close=lambda: None,
    )
    served = ServedSession("t1", session, None)
    for name, server in rows:
        served.servers[name] = server
    return served, events


def test_a_fifth_protocol_needs_only_its_registry_row():
    """Clients and the remote judgement come from the one table: no per-protocol edit anywhere."""
    fifth = FakeServer(ClientConnection(client="fifth:127.0.0.1:1", instrument="psu"))
    served, _ = served_with(("Fifth", fifth))
    assert served.remote_clients() == ["fifth:127.0.0.1:1"]
    assert served.remote_clients("psu") == ["fifth:127.0.0.1:1"]
    assert served.remote_clients("dmm") == []
    assert served.is_remote("fifth:127.0.0.1:1")
    assert not served.is_remote("ui") and not served.is_remote("pyvisa") and not served.is_remote(None)


async def test_a_registry_row_stops_with_the_session(tmp_path):
    from simubench.server.manager import SessionManager

    fifth = FakeServer()
    manager = SessionManager(tmp_path, scpi_base_port=None)
    served, _ = served_with(("Fifth", fifth))
    manager.sessions[served.id] = served
    await manager.stop(served.id)
    assert fifth.stopped and served.id not in manager.sessions


async def test_stopping_a_session_cancels_a_read_still_computing_before_it_closes(tmp_path):
    """#1510: closing waits for the session lock; a long read holds it, so the stop cancels first."""
    from simubench.server.manager import SessionManager

    manager = SessionManager(tmp_path, scpi_base_port=None)
    served, _ = served_with()
    order: list[str] = []
    served.session.cancel = lambda: order.append("cancel")
    served.session.close = lambda: order.append("close")
    manager.sessions[served.id] = served
    await manager.stop(served.id)
    assert order == ["cancel", "close"]


def test_local_ends_on_a_port_clients_command_and_survives_the_uis():
    """The LOCAL guard is the connections' word (#1475): any server's client counts, the UI's
    write does not — without anybody naming a label prefix."""
    fifth = FakeServer(ClientConnection(client="fifth:127.0.0.1:1", instrument="psu"))
    served, events = served_with(("Fifth", fifth))
    served.go_local("psu")
    assert [e["action"] for e in events] == ["local"]
    served._follow_remote(
        {"kind": "scpi", "dir": "write", "instrument": "psu", "client": "fifth:127.0.0.1:1"}
    )
    assert "psu" not in served.local
    assert [e["action"] for e in events] == ["local", "remote"]
    served.go_local("psu")
    served._follow_remote({"kind": "scpi", "dir": "write", "instrument": "psu", "client": "ui"})
    assert "psu" in served.local and len(events) == 3


def test_no_layer_sniffs_a_client_label_prefix():
    """The port servers' label prefixes are display names: no quoted one anywhere in the access or
    server layers — a new protocol that spells labels differently must not be able to silently
    lose the LOCAL guard (the failure mode the old prefix table had). The quoted form is the
    sniff's shape (``startswith(("socket:", …))``): the labels themselves only occur inside
    f-strings, where a closing quote never follows the colon."""
    offenders: list[str] = []
    for layer in ("access", "server"):
        for path in sorted((ROOT / "src/simubench" / layer).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            offenders += [
                f"{path.name}: {prefix}"
                for prefix in LABEL_PREFIXES
                if f'"{prefix}"' in text or f"'{prefix}'" in text
            ]
    assert offenders == []


def _churning_bench_link():
    """A bench link whose client table changes while connections() walks it — a link opening on
    the event loop just as the engine thread asks is_remote (#1479)."""
    from simubench.access.bench_link import BenchLinkServer

    server = BenchLinkServer(None)  # type: ignore[arg-type]
    server._clients = {"bench:1": ("dut", None), "bench:2": ("dut", None)}  # type: ignore[dict-item]

    def holds_lock(label: str) -> bool:
        server._clients[f"bench:{len(server._clients) + 1}"] = ("dut", None)  # type: ignore[assignment]
        return False

    server.holds_lock = holds_lock  # type: ignore[method-assign]
    return server


def _churning_vxi11():
    from simubench.access.vxi11_server import Vxi11Server

    server = Vxi11Server(None)  # type: ignore[arg-type]
    link = SimpleNamespace(client="vxi11:1", instrument="dut")
    server._links = {1: link, 2: SimpleNamespace(client="vxi11:2", instrument="dut")}  # type: ignore[dict-item]

    def locks(_name):
        server._links[len(server._links) + 1] = link  # type: ignore[index]
        return SimpleNamespace(held_by=lambda _link: False)

    server.locks = locks  # type: ignore[method-assign]
    return server


@pytest.mark.parametrize("make", [_churning_bench_link, _churning_vxi11])
def test_connections_survive_a_client_table_changing_underneath(make):
    """connections() walks a snapshot, so the engine thread's is_remote never meets
    ``RuntimeError: dictionary changed size during iteration`` (#1479)."""
    server = make()
    served = ServedSession.__new__(ServedSession)
    served.servers = {"x": server}
    assert served.is_remote(server.connections()[0].client)
