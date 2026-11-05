"""Bus-level state shared by every access path (D63): locks and remote/local control."""

from __future__ import annotations

import pytest

from simubench.access.bus import LockTable, RemoteLocal

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
