"""Power supply scenario (B2 #185): the ±25 V outputs combined in parallel share a 2 Ω load, and a
protection fault latches the output off until it is cleared.

The bench (``benches/ps_combo.bench.yaml``): CH1 drives 10 Ω; CH2 and CH3 are wired in parallel
(CH2+ with CH3−, CH2− with CH3+) across 2 Ω. With both current limits at 1.03 A the pair supplies
2.06 A, so the node settles at 4.12 V.
"""

from __future__ import annotations

import pytest
import pyvisa

from .conftest import BENCHES


@pytest.fixture
def ps():
    rm = pyvisa.ResourceManager(f"{BENCHES / 'ps_combo.bench.yaml'}@simubench")
    instrument = rm.open_resource("ps", read_termination="\n", write_termination="\n")
    yield instrument
    rm.close()


def values(text: str) -> list[float]:
    return [float(x) for x in text.split(",")]


def test_the_parallel_pair_shares_the_load(ps):
    ps.write("OUTP:PAIR PAR")
    ps.write("VOLT 5,(@2)")
    ps.write("CURR 1.03,(@2,3)")
    ps.write("OUTP ON,(@2,3)")
    assert ps.query("*OPC?") == "1"  # the relays are closed (D12)
    assert ps.query("OUTP:PAIR?") == "PAR"
    assert values(ps.query("VOLT? (@2,3)")) == pytest.approx([5.0, -5.0])
    assert values(ps.query("MEAS:VOLT? (@2,3)")) == pytest.approx([4.12, -4.12], rel=1e-3)
    assert values(ps.query("MEAS:CURR? (@2,3)")) == pytest.approx([1.03, 1.03], rel=1e-6)


def test_a_protection_fault_latches_until_cleared(ps):
    ps.write("VOLT 5,(@1)")
    ps.write("VOLT:PROT 4,(@1)")
    ps.write("OUTP ON,(@1)")
    assert ps.query("*OPC?") == "1"
    assert ps.query("OUTP:PROT:TRIP? (@1)") == "1"
    assert ps.query("OUTP:PROT:KIND? (@1)") == '"OVP"'
    assert ps.query("OUTP? (@1)") == "0"  # the fault switched the output off
    ps.write("OUTP:PROT:CLE (@1)")  # the fault still stands (OVP 4 V is below the output)
    assert ps.query("*OPC?") == "1"  # the restored output's relay closes
    ps.query("MEAS:VOLT? (@1)")  # the next operating point trips again
    assert ps.query("OUTP:PROT:TRIP? (@1)") == "1"
    assert ps.query("OUTP? (@1)") == "0"
    ps.write("VOLT:PROT 6,(@1)")
    ps.write("OUTP:PROT:CLE (@1)")
    ps.write("OUTP ON,(@1)")
    assert ps.query("*OPC?") == "1"
    assert float(ps.query("MEAS:VOLT? (@1)")) == pytest.approx(5.0, rel=1e-3)
