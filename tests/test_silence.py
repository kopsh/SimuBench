"""Why a port reads 0 V: traced upstream, not guessed (issue 22)."""

from pathlib import Path

from fastapi.testclient import TestClient

from simubench.api import open_bench
from simubench.engine.silence import why_silent
from simubench.models.bench import BenchDefinition, Fault
from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "benches" / "awg_rc_scope.bench.yaml"


def reasons(s, ref):
    return why_silent(s, s.port_net.get(ref, ""), port_ref=ref)


def test_output_off_names_the_source_and_the_path():
    s = open_bench(EXAMPLE)
    (r,) = reasons(s, "scope.CH1")
    assert r.kind == "source_off"
    assert r.source == "awg.OUT1"
    assert r.path == ["scope.CH1", "wire1.passive_probe0", "rc", "awg.OUT1"]


def test_unwired_channel():
    s = open_bench(EXAMPLE)
    (r,) = reasons(s, "scope.CH2")
    assert r.kind == "unconnected"


def test_no_reason_once_the_signal_is_there():
    s = open_bench(EXAMPLE)
    s.scpi("awg", ":OUTPut1 ON")
    assert reasons(s, "scope.CH1") == []


def test_output_on_but_set_to_zero():
    s = open_bench(EXAMPLE)
    s.scpi("awg", ":FUNCtion DC")
    s.scpi("awg", ":VOLTage:OFFSet 0")
    s.scpi("awg", ":OUTPut1 ON")
    (r,) = reasons(s, "scope.CH1")
    assert r.kind == "zero_setting" and r.source == "awg.OUT1"


def test_no_source_upstream():
    bench = BenchDefinition.model_validate(
        {
            "name": "passive",
            "instruments": {"scope": {"model": "sb-dso-4"}},
            "duts": {"rc": {"type": "rc_lowpass", "params": {"r": 1000.0, "c": 1e-7}}},
            "wires": [{"from": "rc.OUT", "to": "scope.CH1"}],
        }
    )
    s = open_bench(bench)
    (r,) = reasons(s, "scope.CH1")
    assert r.kind == "no_source"
    assert r.path == ["scope.CH1", "rc"]


def test_fault_on_the_source_is_named():
    s = open_bench(EXAMPLE)
    s.scpi("awg", ":OUTPut1 ON")
    f = s.faults.inject(Fault(kind="hidden", component="awg/ch1.out", param="enabled", value=False))
    kinds = {r.kind: r for r in reasons(s, "scope.CH1")}
    assert set(kinds) == {"source_off", "fault"}
    assert kinds["fault"].fault_id == f.id and kinds["fault"].source == "awg.OUT1"


def test_scope_screen_and_probe_carry_the_reasons():
    app = create_app(SessionManager(ROOT / "examples", scpi_base_port=None), web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        sid = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"}).json()["id"]
        screen = c.get(f"/api/sessions/{sid}/instruments/scope/screen", params={"source": "preview"}).json()
        (ch1,) = [t for t in screen["traces"] if t["channel"] == 1]
        assert [r["kind"] for r in ch1["silence"]] == ["source_off"]
        net = c.get(f"/api/sessions/{sid}").json()["nets"]
        scope_net = next(n["id"] for n in net if "scope.CH1" in n["members"])
        probe = c.get(f"/api/sessions/{sid}/nets/{scope_net}/probe").json()
        assert probe["silence"][0]["source"] == "awg.OUT1"
        c.post(f"/api/sessions/{sid}/instruments/awg/scpi", json={"command": ":OUTPut1 ON"})
        screen = c.get(f"/api/sessions/{sid}/instruments/scope/screen", params={"source": "preview"}).json()
        assert all(t["silence"] == [] for t in screen["traces"] if t["channel"] == 1)
        c.delete(f"/api/sessions/{sid}")
