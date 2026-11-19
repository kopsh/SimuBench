"""A read that computes too long ends between two frames: its budget, a cancel, the record (D88).

The budget is real seconds, so the tests that need one to run out use ``1e-9`` — already spent
when the first checkpoint looks — instead of timing anything; the cancel test is the one that
sleeps, and says how long (D49: no ``unit`` marker, it waits for a thread)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from helpers import raises_with
from simubench.api import open_bench
from simubench.engine.budget import ComputeAborted, ComputeBudget
from simubench.engine.component import Component, PortSpec, component, registry
from simubench.engine.session import Session
from simubench.engine.stream import FrameRequest, ModelLimit, SampleFrame
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition
from simubench.replay import replay

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "acceptance" / "benches" / "awg_rc_scope.bench.yaml"
CHUNK = 8
CHUNKS = 30  # what the reducing block asks for: a record read in 40 pieces, as a deep one is
PER_CHUNK_S = 0.01  # the slow block's compute per piece: the whole read takes ~0.3 s


@pytest.fixture
def slow_chain():
    """``reduce`` reads ``CHUNKS`` pieces of ``slow``: a measurement over a deep record."""

    @component("t_slow")
    class Slow(Component):
        ports: ClassVar[dict[str, PortSpec]] = {"out": PortSpec("out", "samples")}
        computed = 0

        def frames(self, port: str, request: FrameRequest) -> SampleFrame:
            time.sleep(PER_CHUNK_S)
            self.computed += 1
            return SampleFrame(np.zeros(request.count), request.t0_ns, request.dt_s)

    @component("t_reduce")
    class Reduce(Component):
        ports: ClassVar[dict[str, PortSpec]] = {
            "in": PortSpec("in", "samples"),
            "out": PortSpec("out", "samples"),
        }

        def frames(self, port: str, request: FrameRequest) -> SampleFrame:
            for k in range(CHUNKS):  # each piece is its own request: its own cache entry
                self.input_frames("in", FrameRequest(k * CHUNK, CHUNK, 1e-6, ("chunk", k)))
            return SampleFrame(np.zeros(request.count), request.t0_ns, request.dt_s)

    yield
    registry.unregister("t_slow", "t_reduce")


def _session(budget_s: float | None) -> tuple[Session, Component, Component]:
    definition = InstrumentDefinition.model_validate(
        {
            "schema": "simubench.instrument/2",
            "id": "slowchain",
            "manufacturer": "ACME",
            "model": "SLOW",
            "class": "oscilloscope",
            "components": [{"name": "slow", "type": "t_slow"}, {"name": "reduce", "type": "t_reduce"}],
            "wiring": {"instrument": [["slow.out", "reduce.in"]]},
            "commands": [],
        }
    )
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "s", "instruments": {"x": {"model": "slowchain"}}}
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=1, compute_budget_s=budget_s)
    comps = session.instruments["x"].components
    return session, comps["reduce"], comps["slow"]


def _read(session: Session, reduce: Component, key: str = "r") -> SampleFrame:
    """One read, the way the outside makes it: in a view (a command's is the same, in an action)."""
    with session.view():
        return session.cached_frames(reduce, "out", FrameRequest(0, 4, 1e-6, (key,)))


def test_a_read_over_its_budget_ends_between_two_frames_and_the_session_carries_on(slow_chain):
    session, reduce, slow = _session(0.05)  # a sixth of the pieces' time
    with pytest.raises(ComputeAborted) as raised:
        _read(session, reduce)
    assert raised.value.abort == "budget" and raised.value.code == -221  # step: refused like a model limit
    assert isinstance(raised.value, ModelLimit)
    assert 1 <= slow.computed < CHUNKS  # step: it stopped part-way, not before the first, not after the last
    done = slow.computed
    session.budget.seconds = None  # step: the next read, unbudgeted, is served — from what was computed
    assert _read(session, reduce, "again").count == 4
    assert slow.computed == CHUNKS and done < CHUNKS


def test_no_budget_means_no_limit_and_no_checkpoint_cost(slow_chain):
    session, reduce, slow = _session(None)
    assert _read(session, reduce).count == 4 and slow.computed == CHUNKS


def test_a_cancel_from_another_thread_ends_the_read_without_the_session_lock(slow_chain):
    """~0.3 s of reading, cancelled after 0.05 s by a thread that takes no lock."""
    session, reduce, slow = _session(None)
    outcome: list[BaseException] = []

    def read() -> None:
        try:
            _read(session, reduce)
        except ComputeAborted as exc:
            outcome.append(exc)

    thread = threading.Thread(target=read)
    started = time.monotonic()
    thread.start()
    time.sleep(0.05)
    session.cancel()  # the lock is held by the reading thread's compute: this must not wait for it
    thread.join(5)
    assert (
        not thread.is_alive() and time.monotonic() - started < 0.8 * CHUNKS * PER_CHUNK_S
    )  # step: well before the end
    assert [e.abort for e in outcome] == ["cancel"] and slow.computed < CHUNKS
    assert _read(session, reduce, "later").count == 4  # step: a cancel is for the read that was running


def test_a_view_that_runs_out_of_budget_leaves_no_event(slow_chain):
    """A view emits nothing (D55); an action's abort does leave one, see the record tests below."""
    session, reduce, _ = _session(1e-9)
    with pytest.raises(ComputeAborted):
        _read(session, reduce)
    assert [e for e in session.events.history if e["kind"] == "abort"] == []


def test_a_read_outside_an_action_or_a_view_has_no_budget(slow_chain):
    """The budget counts from the begin of an action or a view; a bare call has none to run out of."""
    session, reduce, slow = _session(1e-9)
    assert session.cached_frames(reduce, "out", FrameRequest(0, 4, 1e-6, ("bare",))).count == 4
    assert slow.computed == CHUNKS


def test_the_budget_checks_what_it_is_given():
    with raises_with(ValueError, "positive"):
        ComputeBudget(0.0)
    budget = ComputeBudget(None)
    budget.abort_next("cancel")
    with raises_with(ValueError, "one of"):
        budget.abort_next("tired")
    assert (
        budget.reason(live=False) == "cancel" and budget.reason() == "cancel"
    )  # a forced one counts in a replay
    budget.disarm()
    assert budget.reason() is None


def test_a_budget_shorter_than_a_coarse_clock_tick_still_runs_out(monkeypatch):
    """#1538: on Windows time.monotonic ticks every ~15.6 ms, so a budget below one tick was never
    seen to run out. The budget times with the fine clock."""
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)  # a coarse clock that does not tick here
    monkeypatch.setattr(time, "perf_counter", lambda: clock[0])
    budget = ComputeBudget(1e-9)
    budget.arm()
    clock[0] += 1e-6
    assert budget.reason() == "budget"


# ---- the record, and the replay that ends the same action the same way -----------------------------


def _record_run(directory: Path, budget_s: float | None) -> Path:
    s = open_bench(BENCH, record_dir=directory, compute_budget_s=budget_s)
    s.write("awg", b":OUTPut1:STATe ON;:SOURce1:FREQuency 1591.55\n")
    s.write("scope", b":CHANnel1:SCALe 0.1;:TIMebase:SCALe 0.0005;:DIGitize\n")
    s.write("scope", b":WAVeform:DATA?\n")
    s.read("scope")
    s.write("scope", b":SYSTem:ERRor?\n")
    s.read("scope")
    s.close()
    return next(directory.glob("run-*.jsonl"))


def _records(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_a_run_that_ran_out_of_budget_replays_identically_and_only_because_the_record_says_so(tmp_path):
    rec = _record_run(tmp_path, 1e-9)
    aborts = [e for e in _records(rec) if e["kind"] == "abort"]
    assert aborts and all(e["reason"] == "budget" for e in aborts)  # step: the run recorded what ended
    report = replay(rec)  # step: the replay has no budget and times nothing...
    assert report.ok, report.as_dict()  # ...and ends the same actions the same way
    unabridged = tmp_path / "unabridged.jsonl"
    unabridged.write_text(
        "".join(json.dumps(e) + "\n" for e in _records(rec) if e["kind"] != "abort"), encoding="utf-8"
    )
    assert not replay(unabridged).ok  # step: without the event the replay computes it all: another reply


def test_a_run_with_no_budget_records_no_abort(tmp_path):
    assert [e for e in _records(_record_run(tmp_path, None)) if e["kind"] == "abort"] == []
