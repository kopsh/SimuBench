"""Fluke 8588A (issue 340): the reference multimeter's measurement chain against the engine.

The manual cases (tests/models/fluke-8588a/cases.yaml) check command by command; these tests run the
real-instrument script examples/scripts/fluke_8588a_verify.py on the example bench and check what a
transcript cannot see: the DCV input impedance loading a source resistance, the resistance
measurement currents of the Product Specifications (normal, low current, HV), four-wire sensing
past the lead resistance, the current shunts in the circuit and the aperture in virtual time.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
import pyvisa

from simubench.engine.session import Session
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "fluke-8588a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "fluke_8588a_verify.py"
GMIN = 1e-12  # engine/circuit.py stamps 1e-12 S from every node to ground


def _script():
    spec = importlib.util.spec_from_file_location("fluke_8588a_verify", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def session(parts: dict, wires: list, with_cal: bool = True):
    lib = DefinitionLibrary()
    instruments = {"dmm": {"model": "fluke-8588a"}}
    if with_cal:
        instruments["cal"] = {"model": "fluke-5522a"}
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "t",
            "instruments": instruments,
            "parts": parts,
            "wires": wires,
        }
    )
    s = Session(bench, lambda model: lib.get(model), clock="command", seed=7, idle="off")

    def q(inst: str, cmd: str) -> str:
        s.write(inst, cmd.encode() + b"\n")
        out = s.read(inst)
        return out.decode().strip() if out else ""

    return s, q


def resistor(r: float) -> dict:
    return {"type": "resistor", "params": {"r": r}}


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(sys, "argv", ["fluke_8588a_verify.py", "dmm", "cal", "--bench", str(bench)])
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "FLUKE,8588A,4523000101,1.28"
    assert out[-1] == '+0,"No error"'
    readings = [float(line.split()[-3]) for line in out[1:-1]]
    # the 5522A's own errors are drawn within its specifications: within 0.01 % of the points
    assert readings == pytest.approx([1.0, 10.0, 100.0, 1.0], rel=1e-4)


def test_input_impedance_loads_a_source_resistance():
    """10 V behind 1 Mohm: Auto is >1 Tohm on the 10 V range (no visible loading), 10M halves
    nothing but takes 1/11, 1M (1.01 Mohm) about half."""
    s, q = session(
        {"rs": resistor(1.0e6)}, [{"from": "cal.OUT", "to": "rs.A"}, {"from": "rs.B", "to": "dmm.IN"}]
    )
    try:
        q("cal", "OUT 10 V;OPER")
        q("cal", "*OPC?")
        q("dmm", ":CONF:VOLT:DC 10")

        def divided(z_in: float) -> float:
            g = 1 / z_in + GMIN
            return 10.0 * (1 / 1.0e6) / (1 / 1.0e6 + g)

        auto = float(q("dmm", ":READ?"))
        assert auto == pytest.approx(divided(1.0e12), rel=2e-6)
        q("dmm", ":VOLT:IMP 10M")
        assert float(q("dmm", ":READ?")) == pytest.approx(divided(1.0e7), rel=2e-6)
        q("dmm", ":VOLT:IMP 1M")
        assert float(q("dmm", ":READ?")) == pytest.approx(divided(1.01e6), rel=2e-6)
        # on the 100 V range Auto is 10 Mohm (Product Specifications, Input Impedance)
        q("dmm", ":VOLT:IMP AUTO;:VOLT:RANG 100")
        assert float(q("dmm", ":READ?")) == pytest.approx(divided(1.0e7), rel=2e-6)
    finally:
        s.close()


@pytest.mark.parametrize(
    ("setup", "r", "range_", "current"),
    [
        (":RES:MODE NORM;:RES:LOWI OFF", 1000.0, 1000.0, 1.0e-3),
        (":RES:MODE NORM;:RES:LOWI ON", 1000.0, 1000.0, 1.0e-4),
        (":RES:MODE NORM;:RES:LOWI OFF", 1.0e5, 1.0e5, 1.0e-4),
        (":RES:MODE HIV", 1.0e7, 1.0e7, 1.0e-5),
    ],
)
def test_two_wire_resistance_drives_the_specified_current(setup, r, range_, current):
    """The reading is V/I of the circuit; the voltage on the DUT is the measurement current of the
    specification table times R (seen by a second meter would be the same node voltage)."""
    s, q = session(
        {"dut": {"type": "load_resistor", "params": {"r": r}}},
        [{"from": "dmm.IN", "to": "dut.A"}],
        with_cal=False,
    )
    try:
        q("dmm", f":CONF:RES {range_}")
        q("dmm", setup)
        # the source sees the DUT beside the stage's 1e12 ohm and the solver's GMIN
        seen = 1 / (1 / r + 1e-12 + GMIN)
        assert float(q("dmm", ":READ?")) == pytest.approx(seen, rel=1e-9)
        inst = s.instruments["dmm"]
        node = inst.session.port_signal(inst.components["input"], "pin").mean()
        assert node == pytest.approx(current * seen, rel=1e-9)
    finally:
        s.close()


def test_open_input_and_a_too_small_range_overload():
    s, q = session(
        {"dut": {"type": "load_resistor", "params": {"r": 5000.0}}},
        [{"from": "dmm.IN", "to": "dut.A"}],
        with_cal=False,
    )
    try:
        q("dmm", ":CONF:RES 1000")  # 5 kohm on the 1 kohm range (2.02 kohm full scale)
        assert q("dmm", ":READ?") == "+9.910000000E+37"
        # READ? settles the auto range like MEASure? (each range has its own measurement current, #522)
        q("dmm", ":RES:RANG:AUTO ON")
        assert float(q("dmm", ":READ?")) == pytest.approx(5000.0, rel=1e-6)
        q("dmm", ":INIT")
        assert float(q("dmm", ":FETC?")) == pytest.approx(5000.0, rel=1e-6)
        assert float(q("dmm", ":MEAS:RES?")) == pytest.approx(5000.0, rel=1e-6)
        # CONFigure without a resolution leaves it unchanged (Query Commands: MEAS:VOLT:DC? 10)
        assert q("dmm", ":CONF?") == '"RES +1.00000000E+04,+1.00000000E-04"'
    finally:
        s.close()
    s, q = session({}, [], with_cal=False)
    try:
        assert q("dmm", ":MEAS:RES? 1000") == "+9.910000000E+37"  # nothing connected
    finally:
        s.close()


def test_four_wire_reads_past_the_lead_resistance():
    parts = {"lead": resistor(2.0), "dut": {"type": "load_resistor", "params": {"r": 100.0}}}
    wires = [
        {"from": "dmm.IN", "to": "lead.A"},
        {"from": "lead.B", "to": "dut.A"},
        {"from": "dmm.SENSE", "to": "dut.A"},
    ]
    s, q = session(parts, wires, with_cal=False)
    try:
        assert float(q("dmm", ":MEAS:FRES? 100")) == pytest.approx(100.0, rel=1e-6)
        assert float(q("dmm", ":MEAS:RES? 100")) == pytest.approx(102.0, rel=1e-6)
    finally:
        s.close()


def test_current_flows_through_the_range_shunt():
    """1 V behind 1 kohm into the current input: the 1 mA range puts 10.5 ohm in the loop, the
    10 mA range 1.5 ohm (Product Specifications, DC Current Input Impedance, front)."""
    s, q = session(
        {"rs": resistor(1000.0)}, [{"from": "cal.OUT", "to": "rs.A"}, {"from": "rs.B", "to": "dmm.IN"}]
    )
    try:
        q("cal", "OUT 1 V;OPER")
        q("cal", "*OPC?")
        set_v = float(q("cal", "OUT?").split(",")[0])
        assert float(q("dmm", ":MEAS:CURR? 0.001")) == pytest.approx(set_v / 1010.5, rel=1e-5)
        assert float(q("dmm", ":MEAS:CURR? 0.01")) == pytest.approx(set_v / 1001.5, rel=1e-5)
        q("dmm", ":CURR:RANG 1E-4")
        assert q("dmm", ":READ?") == "+9.910000000E+37"  # 1 mA past the 100 uA range
    finally:
        s.close()


def test_the_aperture_sets_the_reading_time():
    """Auto: 1 s per reading at the default 7 digits; manual: the aperture itself (virtual time)."""
    s, q = session({}, [], with_cal=False)
    try:
        q("dmm", ":CONF:VOLT:DC 10;:TRIG:COUN 3")
        t0 = s.now_ns
        q("dmm", ":READ?")
        assert s.now_ns - t0 == pytest.approx(3.0e9, rel=1e-6)
        q("dmm", ":VOLT:APER:MODE MAN;:VOLT:NPLC 5")  # 0.1 s
        t0 = s.now_ns
        assert len(q("dmm", ":READ?").split(",")) == 3
        assert s.now_ns - t0 == pytest.approx(0.3e9, rel=1e-6)
        q("dmm", ":VOLT:APER:MODE FAST")
        t0 = s.now_ns
        q("dmm", ":READ?")
        assert s.now_ns - t0 == pytest.approx(0.6e9, rel=1e-6)
    finally:
        s.close()
