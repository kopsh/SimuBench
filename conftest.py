"""Tests and acceptance scenarios load SimuBench's own ``sb-*`` validation definitions, which are not
shipped with the package (D42), through the definition search path."""

import gc
import os
import tempfile
import time
from pathlib import Path

import pytest

# The unit budget (D49): CPU time decides, the wall clock bounds waiting (#1544).
from tests.unit_budget import unit_budget_verdict

from simubench.library.loader import DEFINITIONS_ENV

VALIDATION_INSTRUMENTS = Path(__file__).parent / "validation" / "instruments"
os.environ[DEFINITIONS_ENV] = os.pathsep.join(
    [str(VALIDATION_INSTRUMENTS), *filter(None, os.environ.get(DEFINITIONS_ENV, "").split(os.pathsep))]
)

# A test never reads or writes the developer's real user folder (paths.py, #1237).
os.environ.setdefault("SIMUBENCH_HOME", tempfile.mkdtemp(prefix="simubench-home-"))


@pytest.fixture(autouse=True)
def _own_user_folder(tmp_path_factory, monkeypatch):
    """Each test gets its own user folder, so a grant or a saved key never leaks into the next one."""
    monkeypatch.setenv("SIMUBENCH_HOME", str(tmp_path_factory.mktemp("simubench-home")))


# One BLAS thread per worker process, set before numpy/scipy load anywhere: the suite runs under
# pytest-xdist (issue 209) and the circuits are tiny, so the BLAS pools would only oversubscribe
# the machine — burning CPU in spinlocks that lands in the solver's CPU-time budgets.
for _blas in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_blas, "1")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item):
    """The CPU time of the call phase, for the unit budget below.

    The cyclic garbage collector stays off during the call: a full collection walks every object the
    worker holds (the parsed component sources, loaded definitions, thousands of earlier tests'
    leftovers — 0.1–0.6 s on a CI worker), and it fires in whichever test allocates next, so a 1 ms
    test read 0.627 s CPU. The collection still happens, in teardown, outside the measured window.
    """
    was_enabled = gc.isenabled()
    gc.disable()
    started = time.process_time()
    try:
        yield
    finally:
        item.stash[_CPU_S] = time.process_time() - started
        if was_enabled:
            gc.enable()


_CPU_S = pytest.StashKey[float]()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    """The unit tier's time budget (D49): a `unit`-marked test's call phase stays under budget."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.passed and item.get_closest_marker("unit"):
        verdict = unit_budget_verdict(item.stash.get(_CPU_S, call.duration), call.duration)
        if verdict is not None:
            report.longrepr = verdict
            report.outcome = "failed"
