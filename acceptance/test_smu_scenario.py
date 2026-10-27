"""Source-measure unit scenario (docs/validation-smu.md): force, limit, NPLC, sweeps, buffer, loads."""

from __future__ import annotations

import numpy as np
import pytest
import pyvisa
from pyvisa import constants, errors

from simubench.models.bench import Fault

from .conftest import BENCHES

ADDRESS = "TCPIP0::192.168.1.60::5025::SOCKET"
R = 1 / (1 / 1000 + 1 / 10e6)  # 1 kΩ in parallel with the DMM's 10 MΩ input: 999.9 Ω
LIMIT = 8.0  # the STAT element of a reading taken with the limit in control


def open_bench(name: str):
    rm = pyvisa.ResourceManager(f"{BENCHES / name}@simubench")
    smu = rm.open_resource(ADDRESS, read_termination="\n", write_termination="\n")  # the real bench's address
    return rm, smu


@pytest.fixture
def rig():
    rm, smu = open_bench("smu_resistor.bench.yaml")
    dmm = rm.open_resource("dmm", read_termination="\n", write_termination="\n")
    yield smu, dmm, rm.visalib.bench
    rm.close()


def table(text: str) -> np.ndarray:
    values = [float(x) for x in text.split(",")] if text else []
    return np.array(values).reshape(-1, 5)


def errors_of(inst) -> list[str]:
    out = []
    while not (e := inst.query(":SYST:ERR?")).startswith("0,"):
        out.append(e)
    return out


def sweep(smu, config: str, ilimit: float) -> np.ndarray:
    smu.write("*RST")  # the limit is back to 105 µA and the output is off
    smu.write(f":SOUR:FUNC VOLT;:SENS:CURR:PROT {ilimit}")
    smu.write(config)
    smu.write(":OUTP ON")
    try:
        return table(smu.query(":READ?"))
    finally:
        smu.write(":OUTP OFF")


# ---- 1. force and limit -------------------------------------------------------------------------


def test_force_and_limit(rig):
    smu, dmm, _ = rig

    def reading() -> tuple[float, float, str, float]:
        v, i, _r, _t, stat = table(smu.query(":MEAS?"))[0]
        return v, i, smu.query(":OUTP:REG?"), stat

    steps = [
        (None, 5.0, 5.0005e-3, "CV", 0),
        (":SENS:CURR:PROT 0.004", 3.9996, 4e-3, "CC", 8),
        (":SOUR:VOLT 8", 3.9996, 4e-3, "CC", 8),
        (":SOUR:VOLT -8", -3.9996, -4e-3, "CC", 8),
        (":SOUR:FUNC CURR;:SOUR:CURR 0.002;:SENS:VOLT:PROT 1.5", 1.5, 1.5002e-3, "CV", 8),
        (":SOUR:CURR -0.002", -1.5, -1.5002e-3, "CV", 8),
        (":OUTP OFF", 0.0, 0.0, "OFF", 0),
    ]
    for cmd, v, i, loop, stat in steps:
        if cmd:
            smu.write(cmd)
        got = reading()
        assert got[0] == pytest.approx(v, abs=5e-5), cmd
        assert got[1] == pytest.approx(i, abs=5e-8), cmd
        assert got[2:] == (loop, stat), cmd
        if cmd == ":SENS:CURR:PROT 0.004":
            assert smu.query(":SENS:CURR:PROT:TRIP?") == "1"
            assert smu.query(":STAT:MEAS:COND?") == "16384"
            assert float(dmm.query(":MEAS:VOLT?")) == pytest.approx(0.004 * R, abs=1e-6)
        if cmd and cmd.startswith(":SOUR:FUNC CURR"):
            assert smu.query(":SENS:VOLT:PROT:TRIP?") == "1" and smu.query(":SENS:CURR:PROT:TRIP?") == "0"
    assert errors_of(smu) == []  # a limit is a state, not an error


def test_limit_holds_when_the_load_drives_current_in():
    rm, smu = open_bench("smu_reverse.bench.yaml")
    v, i, _r, _t, stat = table(smu.query(":MEAS?"))[0]
    assert v == pytest.approx(11.0, abs=1e-9) and i == pytest.approx(-1e-3, abs=1e-12)
    assert stat == LIMIT and smu.query(":OUTP:REG?") == "CC"
    rm.close()


# ---- 2. NPLC and reading time -------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, nplc, ms",
    [
        (60, 0.01, 2.905),
        (60, 1, 52.405),
        (60, 10, 502.405),
        (50, 0.01, 3.005),
        (50, 1, 62.405),
        (50, 10, 602.405),
    ],
)
def test_nplc_sets_the_reading_time(rig, line, nplc, ms):
    smu, _, bench = rig
    smu.write(f":SOUR:VOLT 1;:SYST:LFR {line};:SENS:CURR:NPLC {nplc}")
    before = bench.now_ns
    _v, i, _r, t, _s = table(smu.query(":MEAS?"))[0]
    assert i == pytest.approx(1 / R, abs=5e-9) and t == pytest.approx(ms * 1e-3, abs=1e-9)
    # D12: the reading takes its time on the virtual clock too (plus the bench's 1 ms source delay).
    assert bench.now_ns - before >= round((ms + 1.0) * 1e6)
    assert float(smu.query(":SENS:VOLT:NPLC?")) == nplc == float(smu.query(":SENS:RES:NPLC?"))


def test_nplc_and_line_frequency_ranges(rig):
    smu, _, _ = rig
    smu.write(":SENS:CURR:NPLC 11")
    smu.write(":SYST:LFR 55")
    assert errors_of(smu) == [
        '-222,"Data out of range"',
        '-222,"Data out of range; line frequency is 50 or 60 Hz"',
    ]
    assert float(smu.query(":SENS:CURR:NPLC?")) == 1 and float(smu.query(":SYST:LFR?")) == 60


# ---- 3. sweeps and the buffer -------------------------------------------------------------------


def test_five_point_sweep_and_buffer(rig):
    smu, _, _ = rig
    rows = sweep(smu, ":SOUR:SWE:VOLT:LIN 0,0.8,5,0", ilimit=450e-6)
    want = [
        (0.0, 0.0, 9.91e37, 52.405e-3, 0),
        (0.2, 0.2 / R, R, 104.810e-3, 0),
        (0.4, 0.4 / R, R, 157.215e-3, 0),
        (450e-6 * R, 450e-6, R, 209.620e-3, 8),
        (450e-6 * R, 450e-6, R, 262.025e-3, 8),
    ]
    assert rows == pytest.approx(np.array(want), rel=1e-6, abs=1e-12)
    assert smu.query(":TRAC:POIN:ACT?") == "5"
    assert np.array_equal(table(smu.query(":FETC?")), rows)
    smu.write(":TRAC:CLE")
    assert smu.query(":TRAC:POIN:ACT?") == "0"
    smu.write(":FETC?")  # an empty buffer has nothing to fetch: no reply, an error
    assert errors_of(smu) == ['-230,"Data corrupt or stale; the buffer is empty"']


def test_linear_log_and_list_sweeps(rig):
    smu, _, _ = rig
    rows = sweep(smu, ":SOURce:SWEep:VOLTage:LINear 0,0.8,41,0", ilimit=450e-6)
    clamped = rows[:, 4] == LIMIT
    assert clamped.sum() == 18 and clamped.argmax() == 23 and rows[23, 0] < 0.46
    assert np.allclose(rows[~clamped, 1], rows[~clamped, 0] / R, rtol=1e-9)
    assert np.allclose(rows[clamped, 1], 450e-6)

    rows = sweep(smu, ":SOUR:SWE:VOLT:LOG 0.01,10,7,0", ilimit=4e-3)
    assert np.allclose(rows[1:6, 0] / rows[:5, 0], 10**0.5)
    assert list(rows[:, 4]) == [0] * 6 + [LIMIT]
    assert rows[6, :2] == pytest.approx([4e-3 * R, 4e-3])

    for config in (':SOUR:SWE:VOLT:LIST "-8,-2,0,2,8"', ":SOUR:SWE:VOLT:LIST -8,-2,0,2,8"):  # D13: both forms
        rows = sweep(smu, config, ilimit=4e-3)
        assert list(rows[:, 4]) == [LIMIT, 0, 0, 0, LIMIT]
        assert rows[:, 0] == pytest.approx([-4e-3 * R, -2, 0, 2, 4e-3 * R], abs=1e-6)  # 8 digits
        assert rows[:, 1] == pytest.approx([-4e-3, -2 / R, 0, 2 / R, 4e-3], abs=1e-12)
    assert errors_of(smu) == []


def test_forgotten_limit_after_reset(rig):
    smu, _, _ = rig
    smu.write("*RST")
    assert float(smu.query(":SENS:CURR:PROT?")) == 105e-6 and smu.query(":OUTP?") == "0"
    smu.write(":SOUR:SWE:VOLT:LIN 0,0.8,41,0;:OUTP ON")
    rows = table(smu.query(":READ?"))
    clamped = rows[:, 4] == LIMIT
    assert clamped.sum() == 35 and clamped.argmax() == 6
    assert errors_of(smu) == []


def test_sweep_refusals(rig):
    smu, _, _ = rig
    for cmd in (
        ":SOUR:SWE:VOLT:LIN 0,8,1,0",
        ":SOUR:SWE:VOLT:LIN 0,8,2501,0",
        ":SOUR:SWE:VOLT:LIN 0,211,9,0",
        ":SOUR:SWE:CURR:LIN 0,1.1,9,0",
    ):
        smu.write(cmd)
        assert errors_of(smu) == ['-222,"Data out of range"'], cmd
    smu.write(":SOUR:SWE:VOLT:LOG -1,10,5,0")
    assert errors_of(smu)[0].startswith("-221,")


def test_envelope_above_21_volts(rig):
    smu, _, _ = rig
    smu.write(":SENS:CURR:PROT 1;:SOUR:VOLT 50")  # 50 mA wanted, inside 105 mA
    _v, i, _r, _t, stat = table(smu.query(":MEAS?"))[0]
    assert i == pytest.approx(50 / R) and stat == 0
    smu.write(":SOUR:VOLT 200")  # 200 mA wanted: the 105 mA range above 21 V limits it
    v, i, _r, _t, stat = table(smu.query(":MEAS?"))[0]
    assert i == pytest.approx(0.105) and v == pytest.approx(0.105 * R) and stat == LIMIT


# ---- 4. nonlinear load --------------------------------------------------------------------------


def test_nonlinear_load():
    rm, smu = open_bench("smu_eload.bench.yaml")
    smu.write(":SOUR:FUNC VOLT;:SENS:CURR:PROT 0.01")
    smu.write(":SOUR:VOLT 0;:OUTP ON")
    assert table(smu.query(":MEAS?"))[0, 1] == pytest.approx(0.0, abs=1e-12)  # below the knee: nothing
    smu.write(":SOUR:VOLT 1e-4")  # above about 73 µV the load regulates at 2 mA
    assert table(smu.query(":MEAS?"))[0, 1] == pytest.approx(2e-3)

    # Every point is solved exactly (deviation from the reference, which refuses this sweep).
    smu.write(":SOUR:SWE:VOLT:LIN 0,8,9,0")
    rows = table(smu.query(":READ?"))
    assert rows[:, 1] == pytest.approx([0.0] + [2e-3] * 8, abs=1e-12)
    assert (rows[:, 4] == 0).all() and errors_of(smu) == []

    stepped = []
    for level in np.linspace(0, 8, 9):
        smu.write(f":SOUR:VOLT {level:.6g}")
        stepped.append(table(smu.query(":MEAS?"))[0])
    assert np.allclose(np.array(stepped)[:, :2], rows[:, :2])
    eload = rm.open_resource("eload", read_termination="\n", write_termination="\n")
    assert float(eload.query(":MEAS:CURR?")) == pytest.approx(2e-3)
    assert float(eload.query(":MEAS:VOLT?")) == pytest.approx(8.0)
    rm.close()


# ---- 5. connection loss -------------------------------------------------------------------------


def test_a_dropped_link_leaves_the_output_on(rig):
    smu, _, bench = rig
    smu.write(":SOUR:FUNC VOLT;:SENS:CURR:PROT 0.001;:OUTP ON")
    with pytest.raises(errors.VisaIOError) as raised:
        try:
            for k, level in enumerate(np.linspace(0, 0.8, 41)):
                smu.write(f":SOUR:VOLT {level:.6g}")
                smu.query(":MEAS?")
                if k == 4:
                    bench.faults.inject(Fault(kind="disconnect", instrument="smu"))
        finally:
            with pytest.raises(errors.VisaIOError):
                smu.write(":OUTP OFF")  # the guard cannot reach the instrument either
    assert raised.value.error_code == constants.VI_ERROR_CONN_LOST
    assert bench.instruments["smu"].state["output.enabled"] is True
    for f in list(bench.faults.faults):
        bench.faults.clear(f)
    smu.write(":OUTP OFF")  # the next session's first command
    assert bench.instruments["smu"].state["output.enabled"] is False
