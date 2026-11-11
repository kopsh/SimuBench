"""The pending-operation bookkeeping (#895) on its own: no instrument, no clock."""

from __future__ import annotations

from simubench.engine.pending import PendingOps


def test_finish_due_runs_callbacks_earliest_first_and_keeps_the_rest():
    ops, order = PendingOps(), []
    ops.register("b", 20, lambda: order.append("b"))
    ops.register("a", 10, lambda: order.append("a"))
    ops.register("late", 99, lambda: order.append("late"))
    ops.finish_due(25)
    assert order == ["a", "b"] and list(ops.pending) == ["late"]


def test_unwaited_operations_do_not_hold_back_the_wait():
    ops = PendingOps()
    ops.register("scan", 500, waited=False)
    assert ops.waited() == [] and ops.wait_target() is None
    ops.register("meas", 30)
    assert ops.wait_target() == 30
    ops.register("scan", 500)  # registering again as waited
    assert ops.wait_target() == 500


def test_opc_sets_the_bit_now_when_nothing_is_pending():
    ops = PendingOps()
    assert ops.arm_opc() is True and not ops.opc_armed


def test_opc_waits_for_the_pending_operations_then_fires_once():
    ops = PendingOps()
    ops.register("m", 10)
    assert ops.arm_opc() is False and ops.opc_armed
    assert ops.opc_due() is False
    ops.finish_due(10)
    assert ops.opc_due() is True
    assert ops.opc_due() is False  # once


def test_a_blocker_holds_opc_back_until_cleared():
    ops = PendingOps()
    ops.block("trig", True)
    assert ops.arm_opc() is False
    assert ops.opc_due() is False
    ops.block("trig", False)
    assert ops.opc_due() is True


def test_clear_forgets_everything():
    ops = PendingOps()
    ops.register("m", 10, waited=False)
    ops.block("t", True)
    ops.arm_opc()
    ops.clear()
    assert (ops.pending, ops.unwaited, ops.blockers, ops.opc_armed) == ({}, set(), set(), False)
