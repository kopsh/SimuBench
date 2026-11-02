"""Mock dependencies for the TUI's unit tests: no server, no network, milliseconds per test.

``FakeClient`` answers from payloads dumped once out of a real session (``tests/data/tui/unit_*.json``),
so the shapes stay honest. The app under test is the real ``SimuBenchApp`` with its connection loop
replaced: it becomes ``connected`` immediately and events are injected with :func:`feed` — the WS
stream is exactly what the e2e tests (test_tui_t2_e2e.py) cover for real.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from simubench.tui.app import SimuBenchApp

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests/data/tui"

CATALOG: dict[str, dict[str, Any]] = {
    k: v for k, v in json.loads((DATA / "unit_catalog.json").read_text(encoding="utf-8")).items()
}


def load(name: str) -> dict[str, Any]:
    import json

    return json.loads((DATA / name).read_text(encoding="utf-8"))


class FakeClient:
    """The ApiClient's interface over canned payloads; writes are recorded for assertions."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self._fixture = fixture
        self.sid = fixture["session"]["id"]
        self.writes: list[tuple[str, str]] = []  # (instrument, command) via scpi
        self.screen_requests: list[tuple[str, int | None]] = []  # (instrument, points)
        self.advances: list[tuple[float, bool]] = []  # (seconds, force)
        self.configures: list[tuple[str, str, Any]] = []  # (instrument, path, value)
        self.panels: list[tuple[str, str]] = []  # (instrument, action)
        self.locals: list[str] = []
        self.fault_list: list[dict[str, Any]] = list(fixture.get("faults", []))
        self.next_fault_id = 1
        self.scpi_reply: dict[str, Any] = {}  # instrument -> canned response for the next write

    # ---- reading --------------------------------------------------------------------------------------

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    http = None  # _screen goes through client.screen since #176

    async def catalog(self) -> list[dict[str, Any]]:
        return list(CATALOG.values())

    async def sessions(self) -> list[dict[str, Any]]:
        return [self._fixture["session"]]

    async def session(self, sid: str) -> dict[str, Any]:
        return self._fixture["session"]

    async def instrument(self, sid: str, name: str) -> dict[str, Any]:
        return self._fixture["instruments"][name]

    async def readings(self, sid: str, name: str) -> dict[str, Any]:
        r = self._fixture["readings"].get(name)
        return r or {"values": {}}

    async def levels(self, sid: str) -> dict[str, Any]:
        return self._fixture["levels"]

    async def clock(self, sid: str) -> dict[str, Any]:
        return self._fixture["clock"]

    async def faults(self, sid: str) -> list[dict[str, Any]]:
        return list(self.fault_list)

    async def screen(self, sid: str, name: str, points: int | None = None) -> dict[str, Any]:
        self.screen_requests.append((name, points))
        key = f"screen_{name}_{points}" if points else f"screen_{name}"
        return self._fixture[key]

    async def probe(self, sid: str, net: str) -> dict[str, Any]:
        """The dumped probe of one net; the session-wide advance moves the clock only."""
        probe = self._fixture.get(f"probe_{net}")
        if probe is None:
            raise KeyError(net)
        return probe

    async def net_waveform(self, sid: str, net: str, window_s: float, points: int) -> dict[str, Any]:
        return (await self.probe(sid, net))["waveform"]

    # ---- writing --------------------------------------------------------------------------------------

    async def scpi(self, sid: str, name: str, command: str) -> dict[str, Any]:
        self.writes.append((name, command))
        canned = self.scpi_reply.get(name, {"response": None, "errors": []})
        return dict(canned, now_ns=0)

    async def configure(self, sid: str, name: str, path: str, value: Any) -> dict[str, Any]:
        self.configures.append((name, path, value))
        # The server would update the state; reflect it so the UI shows the new value.
        for sv in self._fixture["instruments"][name]["state"]:
            if sv["path"] == path:
                sv["value"] = value
        return {"ok": True, "path": path, "value": value, "now_ns": 0}

    async def panel(self, sid: str, name: str, action: str) -> dict[str, Any]:
        self.panels.append((name, action))
        return {"ok": True, "action": action}

    async def local(self, sid: str, name: str) -> dict[str, Any]:
        self.locals.append(name)
        info = self._fixture["instruments"][name]
        info["local"] = True
        info["remote"] = []
        return info

    async def inject_fault(self, sid: str, spec: dict[str, Any]) -> dict[str, Any]:
        fid = f"f-{self.next_fault_id}"
        self.next_fault_id += 1
        info = {
            "id": fid,
            "status": "active",
            "description": spec.get("message") or spec["kind"],
            "source": "tui",
            "spec": spec,
        }
        self.fault_list.append(info)
        return info

    async def revoke_fault(self, sid: str, fault_id: str) -> dict[str, Any]:
        self.fault_list = [f for f in self.fault_list if f["id"] != fault_id]
        return {"id": fault_id, "status": "ended"}

    async def runs(self, sid: str | None = None) -> list[dict[str, Any]]:
        return list(self._fixture.get("runs", []))

    async def run_detail(self, run_id: str) -> dict[str, Any]:
        return self._fixture.get("run_detail", {"summary": {"run_id": run_id}})

    async def replay(self, run_id: str) -> dict[str, Any]:
        return self._fixture.get(
            "replay",
            {"run_id": run_id, "ok": True, "actions": 0, "replies": 0, "mismatches": [], "warnings": []},
        )

    async def advance(self, sid: str, seconds: float, force: bool = False) -> dict[str, Any]:
        self.advances.append((seconds, force))
        self._fixture["clock"]["now_ns"] += int(seconds * 1e9)
        return self._fixture["clock"]


def unit_app(fixture: dict[str, Any], lang: str = "en") -> tuple[SimuBenchApp, FakeClient]:
    """The real app over a FakeClient: connected immediately, no sockets.

    Events reach the app the same way the WS stream delivers them: ``await feed(app, *events)``
    (which is ``_on_event``). Tests drive the screens with Textual's Pilot.
    """
    fake = FakeClient(fixture)
    tui = SimuBenchApp("http://unit.test", sid=fake.sid)
    tui.client = fake  # type: ignore[assignment]

    async def connected_loop() -> None:
        tui.conn = "connected"
        with contextlib.suppress(Exception):
            await tui._full_refresh()
        await tui._stop.wait()

    tui._connection_loop = connected_loop  # type: ignore[method-assign]
    return tui, fake


async def feed(tui: SimuBenchApp, *events: dict[str, Any]) -> None:
    """Deliver events exactly as the WS stream would (each already carries seq and t_ns)."""
    for ev in events:
        tui._on_event(ev)
    await asyncio.sleep(0)


def ev(seq: int, t_ns: int, kind: str, **fields: Any) -> dict[str, Any]:
    """One event as the server's ``to_event`` produces it."""
    out = {"seq": seq, "t_ns": t_ns, "kind": kind}
    out.update(fields)
    return out
