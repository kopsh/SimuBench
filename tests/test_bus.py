"""Bus-level state shared by every access path (D63): locks and remote/local control."""

from __future__ import annotations

import asyncio

import pytest

from simubench.access.bus import LockTable, RemoteLocal, ServiceRequest

pytestmark = pytest.mark.unit


def test_an_exclusive_lock_keeps_every_other_holder_out():
    locks = LockTable()
    assert locks.grant("a", exclusive=True)
    assert not locks.grant("b", exclusive=True)
    assert not locks.grant("b", exclusive=False, key=b"k")
    assert locks.accessible("a") and not locks.accessible("b")


def test_shared_locks_compose_only_with_the_same_key():
    locks = LockTable()
    assert locks.grant("a", exclusive=False, key=b"team")
    assert locks.grant("b", exclusive=False, key=b"team")
    assert not locks.grant("c", exclusive=False, key=b"other")  # one shared key at a time
    assert not locks.grant("c", exclusive=True)
    assert locks.holders() == 2
    assert not locks.accessible("c")


def test_release_reports_what_was_held():
    locks = LockTable()
    locks.grant("a", exclusive=True)
    assert locks.release("a") == 1
    locks.grant("b", exclusive=False, key=b"k")
    assert locks.release("b") == 2
    assert locks.release("b") == 3  # nothing left to release
    assert locks.accessible("anyone")


@pytest.mark.parametrize(
    ("codes", "state", "ren"),
    [
        ((1,), "local", True),  # REN asserted, not yet addressed
        ((3,), "remote", True),  # enable remote and go to remote
        ((3, 6), "local", True),  # GTL: back to local, REN stays
        ((5,), "remote_lockout", True),  # remote with local lockout
        ((5, 6), "local_lockout", True),  # GTL under LLO: the front panel stays locked out
        ((4,), "local_lockout", True),
        ((3, 0), "local", False),  # REN released
        ((5, 2), "local", False),  # REN released and GTL clears the lockout
    ],
)
def test_remote_local_follows_the_visa_ren_modes(codes, state, ren):
    rl = RemoteLocal()
    for code in codes:
        rl.control(code)
    assert (rl.state, rl.ren) == (state, ren)


def test_an_unknown_control_code_is_refused():
    with pytest.raises(KeyError):
        RemoteLocal().control(7)


# ---- the one lock book: viLock nesting and the shared wait (#1475) ------------------------------------------


def test_nested_vilocks_count_up_and_release_at_the_outermost():
    locks = LockTable()
    assert locks.acquire("a", exclusive=True)
    assert locks.acquire("a", exclusive=True)  # the same kind counts up, it does not re-grant
    assert not locks.acquire("a", exclusive=False, key=b"k")  # another kind is refused while held
    assert locks.holders() == 1 and locks.held_by("a")
    assert locks.release_one("a")
    assert locks.held_by("a") and not locks.accessible("b")  # the inner one still holds
    assert locks.release_one("a")
    assert not locks.held_by("a") and locks.accessible("b")
    assert not locks.release_one("a")  # held none: VI_ERROR_SESN_NLOCKED's word


async def test_a_shared_lock_join_is_granted_without_a_wait():
    """A session presenting the shared key joins at once (VISA): the wait does not make it wait."""
    locks = LockTable()
    locks.grant("a", exclusive=False, key=b"team")
    assert await asyncio.wait_for(locks.wait_accessible("b", 5.0, exclusive=False, key=b"team"), 0.1)
    assert locks.holders() == 2


async def test_wait_accessible_grants_at_once_when_the_lock_is_free():
    locks = LockTable()
    assert await asyncio.wait_for(locks.wait_accessible("a", 5.0, exclusive=True), 0.1)
    assert locks.held_by("a")


async def test_wait_accessible_wakes_when_the_lock_is_released():
    locks = LockTable()
    locks.grant("a", exclusive=True)
    wait = asyncio.create_task(locks.wait_accessible("b", 5.0))
    await asyncio.sleep(0)  # one turn: the waiter is inside its wait
    assert not wait.done()
    locks.release("a")
    assert await asyncio.wait_for(wait, 1.0)


async def test_wait_accessible_takes_the_lock_the_moment_it_is_free():
    """A successful wait grants (VXI-11's device_lock granted inside its poll loop): the winner
    holds the lock, a waiter that lost the race keeps waiting."""
    locks = LockTable()
    locks.grant("a", exclusive=True)
    winner = asyncio.create_task(locks.wait_accessible("b", 5.0, exclusive=True))
    loser = asyncio.create_task(locks.wait_accessible("c", 5.0, exclusive=True))
    await asyncio.sleep(0)
    locks.release("a")
    assert await asyncio.wait_for(winner, 1.0)
    assert locks.held_by("b") and not loser.done()
    loser.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loser


async def test_wait_accessible_reports_the_timeout_and_leaves_everything_alone():
    locks = LockTable()
    locks.grant("a", exclusive=True)
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert not await locks.wait_accessible("b", 0.08)
    assert loop.time() - started >= 0.05  # the whole timeout was waited, poll interval included
    assert not locks.held_by("b") and locks.held_by("a")


async def test_wait_accessible_stops_when_the_caller_gives_up():
    """A connection being torn down stops waiting instead of waiting out an unbounded timeout."""
    locks = LockTable()
    locks.grant("a", exclusive=True)
    give_ups = iter([False, True])
    assert not await locks.wait_accessible("b", give_up=lambda: next(give_ups))


# ---- the service request latch: one announcement per request, per listener ----------------------------------


def a_request(srq: ServiceRequest) -> None:
    srq.observe(0x60, 32)  # ESB with SRE 32: a new reason, RQS latches (bit 6 is not a reason)


def test_the_edge_announces_a_pending_request_once_per_listener():
    srq = ServiceRequest()
    a_request(srq)
    send, sent = srq.edge(False)
    assert send and sent  # the listener announces now, and has announced
    send, sent = srq.edge(sent)
    assert not send and sent  # the same request announces no second time


def test_a_taken_request_arms_the_next_one():
    srq = ServiceRequest()
    a_request(srq)
    _, sent = srq.edge(False)
    srq.observe(0x20, 32)  # the reason went away: the request stays latched until a poll takes it
    send, sent = srq.edge(sent)
    assert not send and sent
    srq.poll(0x20, 32)  # a poll takes the latched request, RQS cleared
    send, sent = srq.edge(sent)
    assert not send and not sent  # re-armed
    srq.observe(0, 32)  # the reason returns: a new request raises SRQ again
    a_request(srq)
    send, sent = srq.edge(sent)
    assert send and sent


def test_two_listeners_each_announce_the_same_request_once():
    srq = ServiceRequest()
    a_request(srq)
    a_sent = b_sent = False
    send_a, a_sent = srq.edge(a_sent)
    send_b, b_sent = srq.edge(b_sent)
    assert send_a and send_b  # every listener announces for itself
    send_a, _ = srq.edge(a_sent)
    send_b, _ = srq.edge(b_sent)
    assert not send_a and not send_b


def test_a_serial_poll_delivers_the_request_and_arms_the_next():
    srq = ServiceRequest()
    a_request(srq)
    assert srq.poll(0x60, 32) & 0x40  # RQS to the poll that took it
    send, sent = srq.edge(True)  # the listener's flag: the request went out by poll
    assert not send and not sent
    srq.observe(0, 32)  # the reason returns between polls: a new request announces again
    a_request(srq)
    send, _ = srq.edge(sent)
    assert send
