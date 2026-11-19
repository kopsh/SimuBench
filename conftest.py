"""Tests and acceptance scenarios load SimuBench's own ``sb-*`` validation definitions, which are not
shipped with the package (D42), through the definition search path."""

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
    """The CPU time of the call phase, for the unit budget below."""
    started = time.process_time()
    yield
    item.stash[_CPU_S] = time.process_time() - started


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
