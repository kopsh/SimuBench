"""Tests and acceptance scenarios load SimuBench's own ``sb-*`` validation definitions, which are not
shipped with the package (D42), through the definition search path."""

import os
from pathlib import Path

import pytest

from simubench.library.loader import DEFINITIONS_ENV

VALIDATION_INSTRUMENTS = Path(__file__).parent / "validation" / "instruments"
os.environ[DEFINITIONS_ENV] = os.pathsep.join(
    [str(VALIDATION_INSTRUMENTS), *filter(None, os.environ.get(DEFINITIONS_ENV, "").split(os.pathsep))]
)

# One BLAS thread per worker process, set before numpy/scipy load anywhere: the suite runs under
# pytest-xdist (issue 209) and the circuits are tiny, so the BLAS pools would only oversubscribe
# the machine — burning CPU in spinlocks that lands in the solver's CPU-time budgets.
for _blas in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_blas, "1")


# The unit budget (D49), 100 ms locally. A CI runner is shared — several workflows and xdist
# workers on two cores — and a load spike lands in a call phase that measures in single-digit
# milliseconds locally (#482), so there the budget is tripled: still wide enough to catch a real
# regression (5 ms → 300 ms), deaf to the noise.
UNIT_BUDGET_S = 0.3 if os.environ.get("CI") == "true" else 0.1


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    """The unit tier's time budget (D49): a `unit`-marked test's call phase stays under budget."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.passed and item.get_closest_marker("unit"):
        if call.duration > UNIT_BUDGET_S:
            report.longrepr = (
                f"unit test over budget: {call.duration:.3f}s > {UNIT_BUDGET_S:g}s (D49) — "
                "split it or move it up a tier"
            )
            report.outcome = "failed"
