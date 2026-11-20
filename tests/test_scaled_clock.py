"""The scaled clock (D44, #683): virtual time = real time × a rate that can change while it runs;
and a change of the clock's mode while the bench runs (#1660).

Real time is injected (``time.monotonic_ns`` is replaced), so no test waits."""

import pytest
import yaml

from helpers import raises_with
from simubench.api import open_bench
from simubench.engine.clock import MAX_VIRTUAL_NS

BENCH = {
    "schema": "simubench.bench/1",
    "name": "scaled",
    "instruments": {"lia": {"model": "sb-lia-1"}},
    "duts": {},
    "wires": [],
    "run": {"seed": 3, "idle": "off"},
}


class Wall:
    """A hand-wound monotonic clock."""

    def __init__(self) -> None:
        self.ns = 1_000_000_000

    def __call__(self) -> int:
        return self.ns

    def tick(self, seconds: float) -> None:
        self.ns += round(seconds * 1e9)


@pytest.fixture
def wall(monkeypatch):
    w = Wall()
    monkeypatch.setattr("time.monotonic_ns", w)
    return w


def session(tmp_path, wall, **run):
    path = tmp_path / "s.bench.yaml"
    path.write_text(yaml.safe_dump({**BENCH, "run": {**BENCH["run"], **run}}), encoding="utf-8")
    return open_bench(path)


def read_now(s) -> float:
    with s.view() as v:  # a passive read follows the live clock
        return v.now_ns / 1e9


def test_time_runs_at_the_scale_of_real_time(tmp_path, wall):
    s = session(tmp_path, wall, clock="scaled", time_scale=10)
    wall.tick(2)
    assert read_now(s) == pytest.approx(20.0)


def test_a_new_scale_counts_from_now_and_never_rewinds(tmp_path, wall):
    s = session(tmp_path, wall, clock="scaled", time_scale=10)
    wall.tick(1)
    assert read_now(s) == pytest.approx(10.0)
    s.clock.set_time_scale(2)
    assert read_now(s) == pytest.approx(10.0)  # the change itself moves nothing
    wall.tick(1)
    assert read_now(s) == pytest.approx(12.0)
    s.clock.set_time_scale(100)  # speeding up
    wall.tick(0.5)
    assert read_now(s) == pytest.approx(62.0)


def test_a_live_clock_stops_at_the_limit_of_virtual_time(tmp_path, wall):
    """#1458: a scaled clock that runs to the end of the range stays there, with no overflow."""
    s = session(tmp_path, wall, clock="scaled", time_scale=1_000_000)
    wall.tick(1e9)  # 1e15 virtual seconds, far past 2**53 ns
    assert read_now(s) == pytest.approx(MAX_VIRTUAL_NS / 1e9)
    wall.tick(1.0)
    assert read_now(s) == pytest.approx(MAX_VIRTUAL_NS / 1e9)  # and does not move on


def test_the_wall_clock_is_a_scale_of_one(tmp_path, wall):
    s = session(tmp_path, wall, clock="wall", time_scale=50)  # the scale is ignored on the wall clock
    wall.tick(3)
    assert read_now(s) == pytest.approx(3.0)
    assert s.clock.time_scale == 1.0


def test_a_scaled_clock_cannot_be_advanced_by_hand(tmp_path, wall):
    s = session(tmp_path, wall, clock="scaled", time_scale=2)
    with raises_with(RuntimeError, "scaled clock"):
        s.advance(1.0)


def test_only_a_scaled_clock_takes_a_new_scale(tmp_path, wall):
    s = session(tmp_path, wall, clock="stepped")
    with raises_with(RuntimeError, "scaled"):
        s.clock.set_time_scale(5)


@pytest.mark.parametrize("bad", [0, -1, float("nan"), 2e6])
def test_a_scale_outside_the_range_is_refused(tmp_path, wall, bad):
    s = session(tmp_path, wall, clock="scaled", time_scale=2)
    with raises_with(ValueError, "time scale"):
        s.clock.set_time_scale(bad)


def test_the_scale_can_come_from_the_caller_instead_of_the_file(tmp_path, wall):
    path = tmp_path / "s.bench.yaml"
    path.write_text(yaml.safe_dump({**BENCH, "run": {"seed": 3, "idle": "off", "clock": "scaled"}}))
    s = open_bench(path, time_scale=4)
    wall.tick(1)
    assert read_now(s) == pytest.approx(4.0)


def test_a_scaled_record_replays_on_the_stepped_clock(tmp_path, wall):
    from simubench.replay import open_record

    s = open_bench(
        _write(tmp_path, clock="scaled", time_scale=3),
        record_dir=tmp_path / "rec",
    )
    wall.tick(1)
    s.write("lia", b"*IDN?\n")
    s.close()
    record = next((tmp_path / "rec").glob("*.jsonl*"))
    _events, replayed, _warnings = open_record(record)
    assert replayed.clock.mode == "stepped"


def _write(tmp_path, **run):
    path = tmp_path / "r.bench.yaml"
    path.write_text(yaml.safe_dump({**BENCH, "run": {**BENCH["run"], **run}}), encoding="utf-8")
    return path


# ---- a change of mode while the bench runs (#1660) ------------------------------------------------


def test_going_live_and_back_neither_jumps_nor_rewinds(tmp_path, wall):
    s = session(tmp_path, wall, clock="command")
    s.advance(5.0)
    wall.tick(30)  # idle real time on a command clock with idle off: none counts
    assert s.set_clock_mode("wall") is True
    assert read_now(s) == pytest.approx(5.0)  # the switch itself moves nothing
    wall.tick(2)
    assert read_now(s) == pytest.approx(7.0)  # live: real time from the switch on
    assert s.set_clock_mode("command") is True
    wall.tick(10)
    assert read_now(s) == pytest.approx(7.0)  # paused: back on the command clock
    s.advance(1.0)
    assert read_now(s) == pytest.approx(8.0)


def test_a_scaled_switch_takes_its_rate_and_a_same_switch_changes_nothing(tmp_path, wall):
    s = session(tmp_path, wall, clock="stepped")
    seen: list[dict] = []
    s.events.subscribe(lambda e: seen.append(e) if e.get("kind") == "clock" else None)
    assert s.set_clock_mode("scaled", 10) is True
    wall.tick(1)
    assert read_now(s) == pytest.approx(10.0)
    assert s.set_clock_mode("scaled", 10) is False
    assert s.set_clock_mode("scaled", 100) is True  # a new rate counts from now
    wall.tick(1)
    assert read_now(s) == pytest.approx(110.0)
    assert [(e["old"], e["new"], e["scale"]) for e in seen] == [
        ("stepped", "scaled", 10.0),
        ("scaled", "scaled", 100.0),
    ]


def test_an_unknown_mode_or_rate_is_refused_and_changes_nothing(tmp_path, wall):
    s = session(tmp_path, wall, clock="command")
    with raises_with(ValueError, "clock mode"):
        s.set_clock_mode("fast")
    with raises_with(ValueError, "time scale"):
        s.set_clock_mode("scaled", 0)
    assert s.clock.mode == "command"


def test_the_clients_guard_is_asked_for_a_change_of_mode(tmp_path, wall):
    s = session(tmp_path, wall, clock="command")
    asked: list[bool] = []

    def refuse(force: bool) -> None:
        asked.append(force)
        if not force:
            raise RuntimeError("scripts are connected")

    s.clock.confirm_mode = refuse
    with raises_with(RuntimeError, "connected"):
        s.set_clock_mode("wall")
    assert s.clock.mode == "command"
    assert s.set_clock_mode("wall", force=True) is True
    assert asked == [False, True]


def test_the_sleep_patch_follows_the_command_clock(tmp_path, wall):
    from simubench.engine.sleep_mode import real_sleep_is_active

    s = session(tmp_path, wall, clock="command", idle="sleep")
    try:
        assert not real_sleep_is_active()
        s.set_clock_mode("wall")
        assert real_sleep_is_active()  # a live clock waits for real
        s.set_clock_mode("command")
        assert not real_sleep_is_active()
    finally:
        s.close()
    assert real_sleep_is_active()


def test_a_record_with_a_live_stretch_replays_at_the_recorded_times(tmp_path, wall):
    from simubench.engine.events import record_time
    from simubench.replay import open_record, resend

    s = open_bench(_write(tmp_path, clock="command"), record_dir=tmp_path / "rec")
    s.write("lia", b"*IDN?\n")
    s.advance(1.0)
    s.set_clock_mode("wall")
    wall.tick(2.5)
    s.write("lia", b"*IDN?\n")
    s.set_clock_mode("command")
    s.advance(0.5)
    s.write("lia", b"*IDN?\n")
    s.close()
    record = next((tmp_path / "rec").glob("*.jsonl*"))
    events, replayed, _warnings = open_record(record)
    writes: list[tuple[int, int, str]] = []
    for ev, _got in resend(replayed, events, {}):
        if ev.get("kind") == "scpi" and ev.get("dir") == "write":
            writes.append((record_time(ev), replayed.clock.now_ns, replayed.clock.mode))
    assert [w[0] for w in writes] == [w[1] for w in writes]  # each write at its recorded time
    assert [w[2] for w in writes] == ["command", "stepped", "command"]
    assert writes[-1][1] == pytest.approx(4.0e9)
