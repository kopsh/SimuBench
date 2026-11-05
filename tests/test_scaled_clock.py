"""The scaled clock (D44, #683): virtual time = real time × a rate that can change while it runs.

Real time is injected (``time.monotonic_ns`` is replaced), so no test waits."""

import pytest
import yaml

from simubench.api import open_bench

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
    s.set_time_scale(2)
    assert read_now(s) == pytest.approx(10.0)  # the change itself moves nothing
    wall.tick(1)
    assert read_now(s) == pytest.approx(12.0)
    s.set_time_scale(100)  # speeding up
    wall.tick(0.5)
    assert read_now(s) == pytest.approx(62.0)


def test_the_wall_clock_is_a_scale_of_one(tmp_path, wall):
    s = session(tmp_path, wall, clock="wall", time_scale=50)  # the scale is ignored on the wall clock
    wall.tick(3)
    assert read_now(s) == pytest.approx(3.0)
    assert s.time_scale == 1.0


def test_a_scaled_clock_cannot_be_advanced_by_hand(tmp_path, wall):
    s = session(tmp_path, wall, clock="scaled", time_scale=2)
    with pytest.raises(RuntimeError, match="scaled clock"):
        s.advance(1.0)


def test_only_a_scaled_clock_takes_a_new_scale(tmp_path, wall):
    s = session(tmp_path, wall, clock="stepped")
    with pytest.raises(RuntimeError, match="scaled"):
        s.set_time_scale(5)


@pytest.mark.parametrize("bad", [0, -1, float("nan"), 2e6])
def test_a_scale_outside_the_range_is_refused(tmp_path, wall, bad):
    s = session(tmp_path, wall, clock="scaled", time_scale=2)
    with pytest.raises(ValueError, match="time scale"):
        s.set_time_scale(bad)


def test_the_scale_can_come_from_the_caller_instead_of_the_file(tmp_path, wall):
    path = tmp_path / "s.bench.yaml"
    path.write_text(yaml.safe_dump({**BENCH, "run": {"seed": 3, "idle": "off", "clock": "scaled"}}))
    s = open_bench(path, time_scale=4)
    wall.tick(1)
    assert read_now(s) == pytest.approx(4.0)


def test_a_scaled_record_replays_on_the_stepped_clock(tmp_path, wall):
    from simubench.engine.replay import open_record

    s = open_bench(
        _write(tmp_path, clock="scaled", time_scale=3),
        record_dir=tmp_path / "rec",
    )
    wall.tick(1)
    s.write("lia", b"*IDN?\n")
    s.close()
    record = next((tmp_path / "rec").glob("*.jsonl*"))
    _events, replayed, _warnings = open_record(record)
    assert replayed.clock_mode == "stepped"


def _write(tmp_path, **run):
    path = tmp_path / "r.bench.yaml"
    path.write_text(yaml.safe_dump({**BENCH, "run": {**BENCH["run"], **run}}), encoding="utf-8")
    return path
