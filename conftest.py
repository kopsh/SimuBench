"""Tests and acceptance scenarios load SimuBench's own ``sb-*`` validation definitions, which are not
shipped with the package (D42), through the definition search path."""

import os
from pathlib import Path

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
