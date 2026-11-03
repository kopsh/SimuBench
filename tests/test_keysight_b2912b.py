"""Keysight B2912B (issue 330): the two-channel flow of the SCPI Command Reference, against the engine.

The manual cases (tests/models/keysight-b2912b/cases.yaml) check command by command; these tests run
the real-instrument script examples/scripts/keysight_b2912b_two_channel_iv.py on the example bench
(1 kOhm on channel 1, 10 kOhm on channel 2) and compare what it measures with Ohm's law per channel,
then check what a transcript cannot see: each channel compliance limits only its own circuit.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
import pyvisa

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_BENCH = ROOT / "examples" / "benches" / "keysight-b2912b.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_b2912b_two_channel_iv.py"


def _script():
    spec = importlib.util.spec_from_file_location("keysight_b2912b_two_channel_iv", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


iv = _script()


@pytest.fixture
def smu():
    rm = pyvisa.ResourceManager(f"{EXAMPLE_BENCH}@simubench")
    inst = rm.open_resource("smu", read_termination="\n", write_termination="\n", timeout=5000)
    yield inst
    rm.close()


def test_example_bench_runs_the_script(smu):
    assert smu.query("*IDN?").strip() == "Keysight Technologies,B2912B,MY63000101,5.0.2051.2800"
    iv.setup(smu)
    points = iv.spot_iv(smu, [0.5, 1.0, 2.0])
    for v, i1, i2 in points:
        assert i1 == pytest.approx(v / 1e3, rel=1e-6)
        assert i2 == pytest.approx(v / 1e4, rel=1e-6)
    assert iv.triggered_pair(smu) == pytest.approx([2e-3, 2e-4], rel=1e-6)
    assert smu.query(":SYST:ERR?").strip().startswith("+0,")


def test_a_compliance_limits_only_its_own_channel(smu):
    iv.setup(smu)
    smu.write(":SENS1:CURR:PROT 1E-3")  # 5 V into 1 kOhm wants 5 mA
    smu.write(":SOUR1:VOLT 5;:SOUR2:VOLT 5")
    i1, i2 = iv.values(smu.query(":MEAS:CURR? (@1,2)"))
    assert i1 == pytest.approx(1e-3, rel=1e-6)  # channel 1 in compliance (CC)
    assert i2 == pytest.approx(5e-4, rel=1e-6)  # channel 2 unaffected: 5 V / 10 kOhm
    assert float(smu.query(":MEAS:VOLT? (@1)")) == pytest.approx(1.0, rel=1e-6)
    assert smu.query(":SENS1:CURR:PROT:TRIP?").strip() == "1"
    assert smu.query(":SENS2:CURR:PROT:TRIP?").strip() == "0"
