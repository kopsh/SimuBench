"""Security self-check of the local service (docs/security.md): every guard, tested systematically."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "s3cret"
#: Served without the token: liveness, and the interface description (no data). Still loopback only.
PUBLIC = {"/api/health", "/api/openapi.json", "/api/docs"}


@pytest.fixture
def app():
    return create_app(
        SessionManager(ROOT / "validation", scpi_base_port=0, hislip_port=0),
        token=TOKEN,
        web_dist=Path("/nonexistent"),
    )


def api_routes(app):
    for r in app.routes:
        path = getattr(r, "path", "")
        if path.startswith("/api/") and path not in PUBLIC and getattr(r, "methods", None):
            for method in r.methods - {"HEAD", "OPTIONS"}:
                yield method, re.sub(r"\{[^}]+\}", "x", path)


def test_every_api_route_needs_the_token(app):
    routes = list(api_routes(app))
    assert len(routes) > 30
    with TestClient(app, base_url="http://127.0.0.1") as c:
        for method, path in routes:
            r = c.request(method, path, params={"path": "x", "seq": 1}, json={})
            assert r.status_code == 401, (method, path, r.status_code)
            r = c.request(
                method, path, params={"path": "x", "seq": 1}, json={}, headers={"x-simubench-token": "wrong"}
            )
            assert r.status_code == 401, (method, path)


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "evil.example"},
        {"origin": "http://evil.example"},
        {"origin": "http://127.0.0.1.evil.example"},
    ],
)
def test_foreign_host_or_origin_is_refused_even_with_the_token(app, headers):
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.get("/api/benches", headers={"x-simubench-token": TOKEN, **headers})
        assert r.status_code == 403


def test_event_socket_checks_token_and_origin(app):
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
        for url, headers in (
            (f"/api/sessions/{sid}/events?token=wrong", {"host": "127.0.0.1"}),
            (
                f"/api/sessions/{sid}/events?token={TOKEN}",
                {"host": "127.0.0.1", "origin": "http://evil.example"},
            ),
        ):
            with pytest.raises(WebSocketDisconnect), c.websocket_connect(url, headers=headers) as ws:
                ws.receive_json()
        with c.websocket_connect(
            f"/api/sessions/{sid}/events?token={TOKEN}", headers={"host": "127.0.0.1"}
        ) as ws:
            assert ws  # the right token from the app's own origin is accepted
        c.delete(f"/api/sessions/{sid}")


@pytest.mark.parametrize(
    "bad", ["../pyproject.toml", "../../etc/passwd.bench.yaml", "/etc/passwd", "benches/../../x.bench.yaml"]
)
def test_bench_paths_stay_in_the_workspace(app, bad):
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        assert c.get("/api/benches/file", params={"path": bad}).status_code == 403
        assert c.get("/api/benches/history", params={"path": bad}).status_code == 403
        assert c.get("/api/benches/version", params={"path": bad, "seq": 1}).status_code == 403
        assert (
            c.put(
                "/api/benches/file", json={"path": bad, "base_sha256": None, "data": {"name": "x"}}
            ).status_code
            == 403
        )
        assert c.post("/api/sessions", json={"bench_path": bad}).status_code in (403, 404)


def test_scpi_sockets_listen_on_loopback_only(app):
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        info = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()
        addresses = [i["address_virtual"] for i in info["instruments"]]
        assert addresses and all(a.startswith("TCPIP0::127.0.0.1::") for a in addresses)
        hislip = [i["address_hislip"] for i in info["instruments"]]
        # The shared HiSLIP port (D41) binds loopback only as well, like the sockets.
        assert hislip and all(a.startswith("TCPIP0::127.0.0.1::hislip") for a in hislip)
        c.delete(f"/api/sessions/{info['id']}")


def test_without_a_token_by_default_but_still_loopback_only():
    """D43: the token is opt-in; Host/Origin checks still apply without it."""
    app = create_app(SessionManager(ROOT / "validation", scpi_base_port=0), web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/api/benches").status_code == 200
        assert c.get("/api/benches", headers={"origin": "http://evil.example"}).status_code == 403
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
        with (
            pytest.raises(WebSocketDisconnect),
            c.websocket_connect(
                f"/api/sessions/{sid}/events", headers={"host": "127.0.0.1", "origin": "http://evil.example"}
            ) as ws,
        ):
            ws.receive_json()
        with c.websocket_connect(f"/api/sessions/{sid}/events", headers={"host": "127.0.0.1"}) as ws:
            assert ws
        c.delete(f"/api/sessions/{sid}")


def test_workspace_plugins_are_opt_in(tmp_path):
    """Workspace ``components/*.py`` is code (issue 47, D3): not imported, let alone run, unless
    the engineer allows it. If it ran, it would leave a marker behind."""
    (tmp_path / "components").mkdir()
    (tmp_path / "components" / "bomb.py").write_text(
        "from pathlib import Path\nPath(__file__).with_suffix('.imported').write_text('x')\n"
    )
    bomb = tmp_path / "components" / "bomb.imported"
    SessionManager(tmp_path, scpi_base_port=0, hislip_port=0)
    assert not bomb.exists()
    SessionManager(tmp_path, scpi_base_port=0, hislip_port=0, allow_plugins=True)
    assert bomb.exists()  # the flag is an explicit yes to running this code


@pytest.mark.parametrize(
    ("given", "require", "required", "fixed"),
    [(None, False, False, None), (None, True, True, None), ("abc", False, True, "abc")],
)
def test_cli_token_is_opt_in(given, require, required, fixed):
    from argparse import Namespace

    from simubench.cli import _token, _ui_url

    token = _token(Namespace(token=given, require_token=require))
    assert (token is not None) == required
    if fixed:
        assert token == fixed
    assert ("?token=" in _ui_url(8765, token)) == required
