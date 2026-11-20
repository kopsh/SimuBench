"""A taken SCPI port (#1195): the bench does not start, the page is told why, nothing stays open;
an unforeseen server error reaches the page with its type and message, not a bare 500."""

from __future__ import annotations

import shutil
import socket
from pathlib import Path

from fastapi.testclient import TestClient

from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
BENCH = "awg_rc_scope.bench.yaml"  # two instruments: two SCPI ports, base and base+1


def _two_free_ports() -> int:
    for _ in range(50):
        with socket.socket() as a:
            a.bind(("127.0.0.1", 0))
            base = a.getsockname()[1]
        try:
            with socket.socket() as b, socket.socket() as c:
                b.bind(("127.0.0.1", base))
                c.bind(("127.0.0.1", base + 1))
            return base
        except OSError:
            continue
    raise RuntimeError("no two consecutive free ports")


def test_taken_scpi_port_is_a_409_that_says_why_and_closes_the_rest(tmp_path):
    shutil.copy(ROOT / "validation" / "benches" / BENCH, tmp_path / BENCH)
    base = _two_free_ports()
    holder = socket.socket()
    holder.bind(("127.0.0.1", base + 1))  # the second instrument's port: the first one opens first
    holder.listen()
    try:
        manager = SessionManager(tmp_path, scpi_base_port=base, hislip_port=0)
        with TestClient(create_app(manager), base_url="http://127.0.0.1") as c:
            r = c.post("/api/sessions", json={"bench_path": BENCH})
            # step: a conflict, not a server error
            assert r.status_code == 409
            detail = r.json()["detail"]
            # step: which port, and the way out
            assert str(base + 1) in detail
            assert "--scpi-base-port" in detail
            # step: no session left behind
            assert c.get("/api/sessions").json() == []
        # step: the port that did open (the first instrument's) is closed again
        with socket.socket() as again:
            again.bind(("127.0.0.1", base))
    finally:
        holder.close()


def test_taken_default_port_falls_back_to_a_free_one_and_says_so(tmp_path):
    shutil.copy(ROOT / "validation" / "benches" / BENCH, tmp_path / BENCH)
    base = _two_free_ports()
    holder = socket.socket()
    holder.bind(("127.0.0.1", base + 1))
    holder.listen()
    try:
        # the double-click start (#1197): the SCPI port was not asked for, so a taken one is replaced
        manager = SessionManager(
            tmp_path, scpi_base_port=base, hislip_port=0, port_fallback=frozenset({"SCPI"})
        )
        with TestClient(create_app(manager), base_url="http://127.0.0.1") as c:
            r = c.post("/api/sessions", json={"bench_path": BENCH})
            # step: the bench starts
            assert r.status_code == 200
            info = r.json()
            # step: the page is told, with the way to pin a port
            (notice,) = info["port_notices"]
            assert str(base + 1) in notice
            assert "--scpi-base-port" in notice
            # step: every instrument answers on a port, none on the taken one
            ports = [lst.port for lst in manager.get(info["id"]).scpi.listeners]
            assert len(ports) == 2
            assert base + 1 not in ports
            c.delete(f"/api/sessions/{info['id']}")
    finally:
        holder.close()


def test_unforeseen_error_reaches_the_page_with_its_text(tmp_path, monkeypatch):
    manager = SessionManager(tmp_path, scpi_base_port=None)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom in start")

    monkeypatch.setattr(manager, "start", boom)
    with TestClient(create_app(manager), base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        r = c.post("/api/sessions", json={"bench_path": "x.bench.yaml"})
    assert r.status_code == 500
    assert r.json() == {"detail": "RuntimeError: boom in start"}
