"""The idle modes of the command clock (D47, issue #17): a slow machine must read the same
numbers as a fast one."""

import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from simubench.api import open_bench
from simubench.engine import sleep_mode
from simubench.engine.replay import read_record, replay
from simubench.models.bench import Fault
from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
LOCKIN = ROOT / "acceptance" / "benches" / "lockin_rc.bench.yaml"
TOKEN = "test-token"

BENCH = {
    "schema": "simubench.bench/1",
    "name": "idle",
    "instruments": {
        "lia": {
            "model": "sb-lia-1",
            "state": {"filter.time_constant": 0.03, "filter.slope": "24", "reference.frequency": 1591.55},
        }
    },
    "duts": {"rc": {"type": "rc_lowpass", "params": {"r": 1000.0, "c": 1.0e-7}}},
    "wires": [{"from": "lia.SINE_OUT", "to": "rc.IN"}, {"from": "rc.OUT", "to": "lia.SIG_IN"}],
    "run": {"clock": "command", "seed": 3},
}


def open_idle(tmp_path, idle, name="b", clock=None, record_dir=None, **run):
    bench_run = {**BENCH["run"], "idle": idle, **run}
    if clock:
        bench_run["clock"] = clock
    path = tmp_path / f"{name}.bench.yaml"
    path.write_text(yaml.safe_dump({**BENCH, "run": bench_run}), encoding="utf-8")
    return open_bench(path, record_dir=record_dir)


def stall(seconds: float) -> None:
    """Occupy the machine without time.sleep: what a slow runner looks like between commands."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass


# ---- reproducibility: a slow machine reads the same numbers -------------------------------------


def test_reproduce_the_drift_of_issue_4(tmp_path):
    """The Windows failure: real idle time between commands reached a drift; 1508 instead of 1500."""

    def drifted(idle, name, gap):
        s = open_idle(tmp_path, idle, name=name)
        try:
            s.write("lia", b"*IDN?\n")
            s.read("lia")
            s.faults.inject(Fault(kind="drift", component="rc", param="r", drift_per_s=0.5))
            s.advance(1.0)  # one second of drift, as the test always meant it
            if gap:
                stall(gap)
            s.query("lia", b"*IDN?")  # the next command; wall idle ahead of it reaches the drift
            return s.components["rc"].param("r")
        finally:
            s.close()

    for idle in ("sleep", "off"):
        without = drifted(idle, f"{idle}-fast", None)
        with_gap = drifted(idle, f"{idle}-slow", 0.05)
        assert without == pytest.approx(1500.0, rel=1e-9)
        assert with_gap == without  # a busy 50 ms between commands changes nothing
    wall_fast = drifted("wall", "wall-fast", None)
    wall_slow = drifted("wall", "wall-slow", 0.05)
    # wall counts real idle time by design: without a deliberate gap a slow runner may still idle
    # over 10 ms (the 1508 of issue 4), so only its direction is certain (#106).
    assert wall_fast >= 1500.0 * (1 - 1e-9)
    assert wall_slow > 1520  # the historical 1508-class failure, reproduced on purpose


# ---- sleep semantics ----------------------------------------------------------------------------


def test_sleep_advances_virtual_time_without_waiting(tmp_path):
    s = open_idle(tmp_path, "sleep")
    try:
        t0 = time.perf_counter()
        time.sleep(2.0)
        assert time.perf_counter() - t0 < 0.1
        assert s.now_ns == 2_000_000_000
    finally:
        s.close()
    assert sleep_mode.real_sleep_is_active()


def test_two_sleep_benches_advance_together_others_do_not(tmp_path):
    a = open_idle(tmp_path, "sleep", name="a")
    b = open_idle(tmp_path, "sleep", name="b")
    wall = open_idle(tmp_path, "wall", name="c")
    try:
        a.write("lia", b"*IDN?\n")
        wall.write("lia", b"*IDN?\n")
        wall_before = wall.now_ns
        time.sleep(1.0)
        assert a.now_ns == b.now_ns == 1_000_000_000
        assert wall.now_ns == wall_before  # a wall-idle bench does not follow a fake sleep
        a.close()
        time.sleep(0.5)
        assert b.now_ns == 1_500_000_000
    finally:
        b.close()
        wall.close()
    assert sleep_mode.real_sleep_is_active()
    t0 = time.perf_counter()
    time.sleep(0.1)
    assert time.perf_counter() - t0 > 0.09  # the real sleep is back


def test_idle_needs_the_command_clock(tmp_path):
    """Only the command clock reads run.idle: wall and stepped clocks are unaffected (D47)."""
    t0 = time.perf_counter()
    s = open_idle(tmp_path, "sleep", clock="wall")
    try:
        assert sleep_mode.real_sleep_is_active()
        time.sleep(0.1)
        assert time.perf_counter() - t0 > 0.09
    finally:
        s.close()


def test_unknown_idle_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="idle"):
        open_idle(tmp_path, "faster")


# ---- the serve downgrade and the record ---------------------------------------------------------


def test_serve_treats_sleep_as_off(tmp_path):
    """Sockets and HiSLIP serve other processes, whose sleep cannot be intercepted (D47)."""
    path = tmp_path / "b.bench.yaml"
    path.write_text(yaml.safe_dump({**BENCH, "run": {**BENCH["run"], "idle": "sleep"}}), encoding="utf-8")
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        sid = c.post("/api/sessions", json={"bench_path": "b.bench.yaml"}).json()["id"]
        assert manager.sessions[sid].session.idle_mode == "off"
        c.delete(f"/api/sessions/{sid}")


def test_sleep_record_names_idle_and_replays(tmp_path):
    record_dir = tmp_path / "runs"
    s = open_idle(tmp_path, "sleep", name="rec", record_dir=str(record_dir))
    s.write("lia", b":SOURce:VOLTage 0.5\n")
    s.read("lia")
    time.sleep(1.0)
    s.close()
    (record,) = record_dir.rglob("run-*.jsonl")
    events = list(read_record(record))
    assert events[0]["idle"] == "sleep"
    assert any(
        e["kind"] == "advance" and e["reason"] == "sleep" and e["dt_ns"] == 1_000_000_000 for e in events
    )
    assert replay(record).ok
