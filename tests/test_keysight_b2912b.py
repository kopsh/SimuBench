"""Keysight B2912B (issue 330): the two-channel flow of the SCPI Command Reference, against the engine.

The manual cases (tests/models/keysight-b2912b/cases.yaml) check command by command; these tests run
the real-instrument script examples/scripts/keysight_b2912b_two_channel_iv.py on the example bench
(1 kOhm on channel 1, 10 kOhm on channel 2) and compare what it measures with Ohm's law per channel,
then check what a transcript cannot see: each channel compliance limits only its own circuit.
"""

import math
import statistics
from pathlib import Path

import pytest
import pyvisa

from helpers import load_script, session_query
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_BENCH = ROOT / "examples" / "benches" / "keysight-b2912b.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_b2912b_two_channel_iv.py"
READING_TIME = 0.008405  # NPLC 0.1 at 50 Hz with autozero: reading_time(0.1, 50, True)

iv = load_script(SCRIPT)


def noise_sigma(range_: float) -> float:
    """The reading noise rms on ``range_`` (specs.noise_ppm_of_range: 7.5 ppm referred to a
    0.02 s one-PLC integration; readings scale with the square root of the integration time)."""
    return 7.5e-6 * range_ * math.sqrt(0.02 / READING_TIME)


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
        # autorange puts 0.5/1/2 mA on the 1/1/10 mA ranges and 50/100/200 µA on the 100 µA/1 mA
        # ones — the widest range's 4σ covers each reading (specs.noise_ppm_of_range, #744)
        assert i1 == pytest.approx(v / 1e3, abs=4 * noise_sigma(1.0e-2))
        assert i2 == pytest.approx(v / 1e4, abs=4 * noise_sigma(1.0e-3))
    assert iv.triggered_pair(smu) == pytest.approx([2e-3, 2e-4], abs=4 * noise_sigma(1.0e-2))
    assert smu.query(":SYST:ERR?").strip().startswith("+0,")


def test_a_compliance_limits_only_its_own_channel(smu):
    iv.setup(smu)
    smu.write(":SENS1:CURR:PROT 1E-3")  # 5 V into 1 kOhm wants 5 mA
    smu.write(":SOUR1:VOLT 5;:SOUR2:VOLT 5")
    i1, i2 = iv.values(smu.query(":MEAS:CURR? (@1,2)"))
    assert i1 == pytest.approx(1e-3, abs=4 * noise_sigma(1.0e-3))  # channel 1 in compliance (CC)
    assert i2 == pytest.approx(5e-4, abs=4 * noise_sigma(1.0e-3))  # channel 2 unaffected: 5 V / 10 kOhm
    assert float(smu.query(":MEAS:VOLT? (@1)")) == pytest.approx(1.0, abs=4 * noise_sigma(2.0))
    assert smu.query(":SENS1:CURR:PROT:TRIP?").strip() == "1"
    assert smu.query(":SENS2:CURR:PROT:TRIP?").strip() == "0"


def test_the_definition_noise_distributes_and_replays(smu):
    """The built-in reading noise (specs.noise_ppm_of_range, #744): channel readings are
    independent draws around Ohm's law, and a fresh bench on the same seed replays bit for bit."""
    smu.write("*RST")
    smu.write(":SOUR1:VOLT 1;:SENS1:CURR:PROT 0.01;:OUTP1 ON")
    ones = [float(smu.query(":MEAS:CURR?")) for _ in range(200)]  # 1 mA on the 1 mA range
    assert len(set(ones)) > 4
    assert statistics.mean(ones) == pytest.approx(1e-3, abs=4 * noise_sigma(1.0e-3) / math.sqrt(200))
    assert statistics.stdev(ones) == pytest.approx(noise_sigma(1.0e-3), rel=0.2)
    # The replay uses open_bench: the PyVISA ResourceManager namespaces each in-process bench
    # instance's component uids separately, so cross-instance draws differ even on the same seed.
    s = open_bench(EXAMPLE_BENCH)
    try:
        assert session_query(s, "smu", "*RST") is None
        assert session_query(s, "smu", ":SOUR1:VOLT 1;:SENS1:CURR:PROT 0.01;:OUTP1 ON") is None
        again = [float(session_query(s, "smu", ":MEAS:CURR?")) for _ in range(20)]
    finally:
        s.close()
    assert again == ones[:20]
