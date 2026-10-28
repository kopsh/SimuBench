"""Net levels for the canvas: which wires carry a signal, and a thumbnail of it (D44)."""

from pathlib import Path

from fastapi.testclient import TestClient

from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]


def test_net_levels_show_which_wires_carry_a_signal():
    app = create_app(SessionManager(ROOT / "examples", scpi_base_port=None), web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
        levels = c.get(f"/api/sessions/{sid}/levels").json()["nets"]
        assert levels and not any(n["active"] for n in levels)
        c.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": ":OUTPut1 ON"})
        levels = c.get(f"/api/sessions/{sid}/levels").json()["nets"]
        assert all(n["active"] for n in levels)  # generator -> RC -> probe -> scope: all live
        ch1 = max(levels, key=lambda n: n["peak"])
        assert len(ch1["wave"]) == 48 and max(ch1["wave"]) > 0 > min(ch1["wave"])
        assert abs(ch1["window_s"] - 2e-3) < 1e-9  # two periods of 1 kHz
        c.delete(f"/api/sessions/{sid}")
