"""The passive view (被动视图, #459): external code reads a running bench only through
``Session.view()`` — one consistent read that holds the lock, follows the wall clock, and
settles only what the caller asks for. The view itself advances nothing (D47 idle belongs to
actions), emits nothing, and touches no communication record (D32)."""

import threading
import time
from pathlib import Path

import yaml

from simubench.api import open_bench
from simubench.models.bench import Fault
from simubench.server.views import fault_info

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "examples" / "benches" / "keysight-dsox2024a.bench.yaml"

BENCH = {
    "schema": "simubench.bench/1",
    "name": "view",
    "instruments": {
        "lia": {
            "model": "sb-lia-1",
            "state": {"filter.time_constant": 0.03, "filter.slope": "24", "reference.frequency": 1591.55},
        }
    },
    "duts": {"rc": {"type": "rc_lowpass", "params": {"r": 1000.0, "c": 1.0e-7}}},
    "wires": [{"from": "lia.SINE_OUT", "to": "rc.IN"}, {"from": "rc.OUT", "to": "lia.SIG_IN"}],
    "run": {"clock": "command", "seed": 3, "idle": "off"},
}


def open_view(tmp_path, name="view", **run):
    path = tmp_path / f"{name}.bench.yaml"
    path.write_text(yaml.safe_dump({**BENCH, "run": {**BENCH["run"], **run}}), encoding="utf-8")
    return open_bench(path)


def test_passive_view_does_not_advance_the_command_clock(tmp_path):
    s = open_view(tmp_path)
    with s.view():
        assert s.now_ns == 0
        s.net_signal("net-0")  # solving reads the present; it must not move time or emit
        assert s.now_ns == 0
        assert not s.history


def test_passive_view_follows_the_wall_clock(tmp_path):
    s = open_view(tmp_path, clock="wall")
    time.sleep(0.03)
    with s.view() as v:
        assert v.now_ns >= 20_000_000  # 30 ms slept, 10 ms slack for construction and scheduling


def test_view_settles_only_the_instruments_it_is_given(tmp_path):
    s = open_bench(SCOPE)
    inst = s.instrument("scope")
    s.scpi("scope", ":DIGitize")  # waits in virtual time; the done entry stays pending…
    assert inst.pending  # …due by now, but no command has arrived to settle it
    with s.view():  # a passive read without settle leaves it alone
        assert inst.pending
    with s.view(settle=("scope",)):
        pass
    assert inst.pending == {}


def test_a_view_excludes_actions_until_it_is_done(tmp_path):
    s = open_view(tmp_path)
    with s.view():
        t = threading.Thread(target=s.write, args=("lia", b"*IDN?\n"), kwargs={"client": "t"})
        t.start()
        t.join(0.05)
        assert t.is_alive()  # the action waits for the view to end
        assert s.seq == 0
    t.join(5)
    assert not t.is_alive()
    assert s.seq == 1


def test_a_view_inside_an_action_is_reentrant(tmp_path):
    s = open_view(tmp_path)
    seen = []
    done = threading.Event()

    def listener(ev: dict) -> None:
        if ev.get("kind") != "state" or done.is_set():
            return
        done.set()
        with s.view():  # same thread as the running action; the RLock allows it
            seen.append(s.now_ns)

    s.subscribe(listener)
    s.configure("lia", "reference.frequency", 2000)
    assert seen and seen[0] == 0


def test_fault_dto_conversion_stays_inside_the_view(tmp_path, monkeypatch):
    """#480: the fault endpoints assemble their DTO from the live FaultState objects; a tick
    landing between two field reads could tear the answer (status from before a transition,
    started_ns from after it). The conversion happens inside the view: the lock is held from
    the snapshot to the last field read."""
    s = open_view(tmp_path)
    s.faults.inject(Fault(kind="latency", instrument="lia", latency_s=0.1, at_s=0.05), source="t")
    seen = {"snapshot": False, "read": False}
    violations: list[int] = []
    real = threading.RLock()
    depth = {"n": 0}

    class Tracked:
        def __enter__(self):
            real.acquire()
            depth["n"] += 1
            return self

        def __exit__(self, *exc):
            depth["n"] -= 1
            real.release()
            if seen["snapshot"] and not seen["read"] and depth["n"] == 0:
                violations.append(depth["n"])
            return False

    real_snapshot = s.faults.snapshot

    def snapshot():
        seen["snapshot"] = True  # the read begins: the lock must hold until the DTO is done
        return real_snapshot()

    monkeypatch.setattr(s.faults, "snapshot", snapshot)
    monkeypatch.setattr(s, "_lock", Tracked())
    with s.view():  # the list_faults shape (app.py, mcp_server.py)
        infos = [fault_info(f) for f in s.faults.snapshot()]
        seen["read"] = True
    assert len(infos) == 1 and infos[0].status == "pending" and infos[0].started_ns is None
    assert violations == []


def test_a_fault_read_racing_ticks_stays_consistent(tmp_path):
    """#480: advance() ticks the faults from another thread while the endpoints read them —
    every DTO must be the state of one instant, never half of two."""
    s = open_view(tmp_path)
    s.faults.inject(
        Fault(kind="latency", instrument="lia", latency_s=0.1, at_s=0.05, duration_s=0.2), source="t"
    )
    stop = threading.Event()

    def ticks():
        # a tiny yield between ticks: the race stays real, but the ticker does not spin the GIL
        # away from the reads (the unthrottled loop cost ~7 s of wall time, #651)
        while not stop.is_set():
            s.advance(0.01)
            time.sleep(0.0005)

    t = threading.Thread(target=ticks)
    t.start()
    try:
        for _ in range(20):
            with s.view():  # the list_faults shape: convert inside the view
                for info in (fault_info(f) for f in s.faults.snapshot()):
                    if info.status == "pending":
                        assert info.started_ns is None
                    else:
                        assert info.started_ns is not None
    finally:
        stop.set()
        t.join(5)
    assert not t.is_alive()
