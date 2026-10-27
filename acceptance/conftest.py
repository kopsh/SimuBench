"""Shared helpers: scripts drive SimuBench the way they drive hardware, through PyVISA (D5)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import pyvisa

BENCHES = Path(__file__).parent / "benches"


@dataclass
class Capture:
    t: np.ndarray
    v: np.ndarray
    preamble: list[float]


def open_rig(bench: str):
    rm = pyvisa.ResourceManager(f"{BENCHES / bench}@simubench")
    awg = rm.open_resource("awg", read_termination="\n", write_termination="\n")
    scope = rm.open_resource("scope", read_termination="\n", write_termination="\n")
    return rm, awg, scope


def capture(scope, probe: float = 1.0, source: int = 1, digitize: bool = True) -> Capture:
    if digitize:
        scope.write(":DIGitize")
    scope.write(f":WAVeform:SOURce CHANnel{source}")
    pre = scope.query_ascii_values(":WAVeform:PREamble?")
    codes = scope.query_binary_values(
        ":WAVeform:DATA?", datatype="h", is_big_endian=False, container=np.array
    )
    _fmt, _typ, points, _cnt, x_inc, x_org, x_ref, y_inc, y_org, y_ref = pre
    assert len(codes) == int(points)
    v = ((codes - y_ref) * y_inc + y_org) * probe
    t = (np.arange(len(codes)) - x_ref) * x_inc + x_org
    return Capture(t, v, pre)


@pytest.fixture
def rig():
    rm, awg, scope = open_rig("awg_rc_scope.bench.yaml")
    yield awg, scope
    rm.close()
