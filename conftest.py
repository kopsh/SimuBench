"""Tests and acceptance scenarios load SimuBench's own ``sb-*`` validation definitions, which are not
shipped with the package (D42), through the definition search path."""

import os
from pathlib import Path

from simubench.library.loader import DEFINITIONS_ENV

VALIDATION_INSTRUMENTS = Path(__file__).parent / "validation" / "instruments"
os.environ[DEFINITIONS_ENV] = os.pathsep.join(
    [str(VALIDATION_INSTRUMENTS), *filter(None, os.environ.get(DEFINITIONS_ENV, "").split(os.pathsep))]
)
