"""The passive view (被动视图, #459): external code reads a running bench only through
``Session.view()`` — one consistent read that holds the lock, follows the wall clock, and
settles only what the caller asks for. The view itself advances nothing (D47 idle belongs to
actions), emits nothing, and touches no communication record (D32)."""

import threading
import time
from pathlib import Path

import yaml

from simubench.api import open_bench

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
