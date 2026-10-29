"""Four-wire measurement and lead resistance scenario (issue 91, metrology-authoring.md 6).

The SMU forces 1 A through two 0.5 Ω leads into a 10 Ω resistor; the sense terminals sit on the
resistor body. Manual calculation (the solver's 1e-12 S GMIN is invisible at ampere-level
currents): two-wire, the meter reads 1 A x (0.5 + 10 + 0.5) Ω = 11 V; four-wire, the loop
regulates the resistor's 1 A x 10 Ω = 10 V while the force terminal sits at 11 V.
"""

from __future__ import annotations

import pytest
import pyvisa

from .conftest import BENCHES

ADDRESS = "TCPIP0::192.168.1.60::5025::SOCKET"


@pytest.fixture
def rig():
    rm = pyvisa.ResourceManager(f"{BENCHES / 'smu_four_wire.bench.yaml'}@simubench")
    smu = rm.open_resource(ADDRESS, read_termination="\n", write_termination="\n")  # the real bench's address
    yield smu, rm.visalib.bench
    rm.close()


def reading(smu: pyvisa.resources.MessageBasedResource) -> list[float]:
    return [float(x) for x in smu.query(":MEAS?").split(",")]


def test_two_wire_and_four_wire_readings(rig):
    smu, _ = rig
    smu.write("*RST")
    smu.write(":SOUR:FUNC CURR;:SOUR:CURR 1;:SENS:CURR:PROT 1.05")
    smu.write(":OUTP ON")
    v, i, r, _t, stat = reading(smu)
    assert v == pytest.approx(11.0, abs=5e-5) and i == pytest.approx(1.0, abs=5e-9)
    assert r == pytest.approx(11.0, abs=5e-5) and stat == 0
    assert smu.query(":OUTP:REG?") == "CC"

    smu.write(":SENSe:REM ON")
    v, i, r, _t, stat = reading(smu)
    assert v == pytest.approx(10.0, abs=5e-5) and i == pytest.approx(1.0, abs=5e-9)
    assert r == pytest.approx(10.0, abs=5e-5) and stat == 0
    assert smu.query(":OUTP:REG?") == "CC"


def test_voltage_loop_regulates_the_sense_pair(rig):
    smu, _ = rig
    smu.write("*RST")
    smu.write(":SOUR:FUNC VOLT;:SOUR:VOLT 10;:SENS:CURR:PROT 1.05;:SENSe:REM ON")
    smu.write(":OUTP ON")
    v, i, _r, _t, stat = reading(smu)
    # The loop holds the resistor at 10 V: 1 A flows and the force terminal carries the leads too.
    assert v == pytest.approx(10.0, abs=5e-5) and i == pytest.approx(1.0, abs=5e-9)
    assert stat == 0 and smu.query(":OUTP:REG?") == "CV"


def test_limit_switch_judges_the_sense_voltage(rig):
    smu, _ = rig
    smu.write("*RST")
    smu.write(":SOUR:FUNC CURR;:SOUR:CURR 1;:SENS:CURR:PROT 1.05;:SENS:VOLT:PROT 10.5")
    smu.write(":OUTP ON")
    # Four-wire: the sense pair sees 10 V, inside the 10.5 V limit: still a current source.
    smu.write(":SENSe:REM ON")
    assert reading(smu)[0] == pytest.approx(10.0, abs=5e-5)
    assert smu.query(":OUTP:REG?") == "CC" and smu.query(":STAT:MEAS:COND?") == "0"
    # Two-wire: the terminals sit at 11 V; the limit clamps them at 10.5 V. Manual:
    # 10.5 V across 11 Ω of leads and resistor plus the 1e-6 Ω LO-to-ground bond.
    smu.write(":SENSe:REM OFF")
    v, i, _r, _t, stat = reading(smu)
    assert v == pytest.approx(10.5, abs=5e-5) and i == pytest.approx(10.5 / 11.000001, abs=5e-9)
    assert stat == 8 and smu.query(":OUTP:REG?") == "CV"
    assert smu.query(":SENS:VOLT:PROT:TRIP?") == "1"
