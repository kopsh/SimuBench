"""The unit tier's time budget (D49), apart from conftest so a test can check it without re-running
conftest's set-up (#1544)."""

import os
import sys

# The unit budget (D49), 100 ms locally, counted in the process's CPU time over the call phase
# (#1544): a shared CI runner's load spike — other workflows, xdist workers, the Windows antivirus
# scanning a file on first access (#1465) — lands in wall time while the test waits for a core, and
# turned millisecond tests red (a 5 ms test read 0.85 s, #1529). CPU time counts what the test
# computes, in every thread of the worker, so a real regression (5 ms → 300 ms of work) still fails.
# The CI multipliers stay as they were (×3, ×10 on Windows, #482): nothing that passed gets tighter.
UNIT_BUDGET_S = (1.0 if sys.platform == "win32" else 0.3) if os.environ.get("CI") == "true" else 0.1
#: A unit test waits for nothing (D49), and CPU time does not see waiting: the wall clock still
#: bounds it, ten budgets wide, which a load spike does not reach and a sleep or a timeout does.
UNIT_WALL_FACTOR = 10


def unit_budget_verdict(cpu_s: float, wall_s: float, budget_s: float = UNIT_BUDGET_S) -> str | None:
    """Why a `unit` test's call phase broke the budget (D49), or None when it kept it."""
    if cpu_s > budget_s:
        return (
            f"unit test over budget: {cpu_s:.3f}s CPU > {budget_s:g}s (D49) — split it or move it up a tier"
        )
    if wall_s > UNIT_WALL_FACTOR * budget_s:
        return (
            f"unit test waited: {wall_s:.3f}s wall > {UNIT_WALL_FACTOR * budget_s:g}s with {cpu_s:.3f}s CPU (D49) — "
            "a unit test waits for nothing: inject the clock or move it up a tier"
        )
    return None
