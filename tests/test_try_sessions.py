"""TrySessions: reuse, idle reaping, MAX retirement and exclusive batches (issue 671)."""

from __future__ import annotations

import threading
import time

from simubench.server.benchfiles import BenchFiles
from simubench.server.manager import TrySessions


def pool(tmp_path, **kw) -> TrySessions:
    return TrySessions(BenchFiles(tmp_path), **kw)


def test_using_reuses_the_session_of_a_definition(tmp_path):
    tries = pool(tmp_path)
    with tries.using("sb-dmm-1") as a:
        pass
    with tries.using("sb-dmm-1") as b:
        assert b.served.id == a.served.id
    assert len(tries.all()) == 1
    tries.close(a.served.id)


def test_open_replaces_and_using_follows_the_new_session(tmp_path):
    tries = pool(tmp_path)
    with tries.using("sb-dmm-1") as a:
        pass
    fresh = tries.open("sb-dmm-1")
    assert fresh.served.id != a.served.id and a.closed
    with tries.using("sb-dmm-1") as b:
        assert b.served.id == fresh.served.id
    tries.close(fresh.served.id)


def test_idle_session_is_reaped_and_replaced(tmp_path):
    tries = pool(tmp_path, idle_s=0.0)
    with tries.using("sb-dmm-1") as a:
        pass
    with tries.using("sb-dmm-1") as b:
        assert b.served.id != a.served.id
    tries.close(b.served.id)


def test_max_retires_the_least_recently_used(tmp_path):
    tries = pool(tmp_path)
    ids = ["sb-dmm-1", "sb-dso-4", "sb-awg-2", "sb-sg-1", "sb-ps-1"]
    assert len(ids) == TrySessions.MAX + 1
    seen = []
    for d in ids:
        with tries.using(d) as e:
            seen.append(e)
    assert len(tries.all()) == TrySessions.MAX
    assert seen[0].closed and not any(e.closed for e in seen[1:])
    for e in tries.all():
        tries.close(e.served.id)


def test_replacing_open_waits_for_a_running_batch(tmp_path):
    tries = pool(tmp_path)
    inside = threading.Event()
    release = threading.Event()
    states: list[bool] = []

    def batch():
        with tries.using("sb-dmm-1") as e:
            inside.set()
            release.wait(5)
            states.append(e.closed)  # still open while this batch runs

    t = threading.Thread(target=batch)
    t.start()
    assert inside.wait(5)
    opener = threading.Thread(target=lambda: tries.open("sb-dmm-1"))
    opener.start()
    time.sleep(0.2)
    assert opener.is_alive()  # waiting for the batch, not closing under it
    release.set()
    t.join(5)
    opener.join(5)
    assert states == [False]
    for e in tries.all():
        tries.close(e.served.id)


def test_two_batches_of_one_definition_run_one_after_the_other_on_the_same_session(tmp_path):
    tries = pool(tmp_path)
    running = 0
    overlap: list[int] = []
    ids: list[str] = []
    gate = threading.Barrier(2)

    def batch():
        nonlocal running
        gate.wait(5)
        with tries.using("sb-dmm-1") as e:
            running += 1
            overlap.append(running)
            time.sleep(0.15)
            running -= 1
            ids.append(e.served.id)

    threads = [threading.Thread(target=batch) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert overlap == [1, 1]  # never two batches on the session at once
    assert len(set(ids)) == 1 and len(tries.all()) == 1  # and it was one session, not two
    for e in tries.all():
        tries.close(e.served.id)
