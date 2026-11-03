"""Rigol DL3021 (issue 194): the manual's flow, the example bench and the C1 load blocks.

The manual cases (tests/models/rigol-dl3021/cases.yaml) check command by command; these tests run
the guide's measurement flow as a script written for a real DL3021
(examples/scripts/rigol_dl3021_measure.py) and the wiring of the load's settings onto the C1
components: the CC slew rate in virtual time and the V_LIMIT/ILIM protection latch.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
import pyvisa

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "rigol_dl3021.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "rigol_dl3021_measure.py"


def _script():
    spec = importlib.util.spec_from_file_location("rigol_dl3021_measure", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def load_bench() -> BenchDefinition:
    """The load behind a 10 V, 0.1 ohm source, as the manual cases' bench."""
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "dl3021",
            "instruments": {"dut": {"model": "rigol-dl3021"}},
            "parts": {"src": {"type": "dc_supply", "params": {"v": 10.0, "r": 0.1}}},
            "wires": [{"from": "src.OUT", "to": "dut.IN"}],
            "run": {"clock": "command", "seed": 3, "idle": "off"},
        }
    )


def readings(session) -> dict:
    return session.instrument("dut").components["load"].readings()["values"]


# ---- the guide's flow as a script written for a real DL3021 ----------------------------------------


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(sys, "argv", ["rigol_dl3021_measure.py", "dut", "--bench", str(bench)])
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "RIGOL TECHNOLOGIES,DL3021,DL3000A000001,00.01.00.04.05"
    assert out[-1] == '0,"No error"'
    assert out[1].startswith("CC 1 mA: ")
    assert out[2].startswith("CV 2 V:  ")
    # The SDG's 5 V behind 50 ohm: 1 mA drops 50 uV; CV 2 V leaves 3 V across the 50 ohm.
    cc = [float(x.strip(" V,AW")) for x in out[1].split(": ")[1].split(", ")]
    cv = [float(x.strip(" V,AW")) for x in out[2].split(": ")[1].split(", ")]
    assert cc[0] == pytest.approx(5.0 - 0.001 * 50, abs=1e-4)
    assert cc[1] == pytest.approx(0.001, abs=1e-9)
    assert cc[2] == pytest.approx(cc[0] * cc[1], rel=1e-6)
    assert cv[0] == pytest.approx(2.0, abs=1e-4)
    assert cv[1] == pytest.approx(3.0 / 50, abs=1e-6)
    assert cv[2] == pytest.approx(cv[0] * cv[1], rel=1e-6)


# ---- the definition on the C1 components -----------------------------------------------------------


def test_cc_slew_ramps_the_current_in_virtual_time():
    s = open_bench(load_bench())
    try:
        s.write("dut", b":SOUR:FUNC CURR;:SOUR:CURR:SLEW 0.001;:SOUR:CURR 2;:SOUR:INP 1\n")
        assert s.read("dut") is None
        assert readings(s)["current"] == pytest.approx(0.0, abs=1e-12)  # the ramp starts now
        s.advance(1e-3)  # 0.001 A/us = 1000 A/s
        assert readings(s)["current"] == pytest.approx(1.0, rel=1e-6)
        s.advance(1e-3)
        assert readings(s)["current"] == pytest.approx(2.0, rel=1e-6)
        assert readings(s)["voltage"] == pytest.approx(9.8, rel=1e-9)
    finally:
        s.close()


@pytest.mark.parametrize(("command", "kind"), [(":SOUR:CURR:VLIM 9", "OVP"), (":SOUR:CURR:ILIM 1", "OCP")])
def test_the_voltage_and_current_limits_trip_the_input(command, kind):
    """The model's choice, not a verified instrument fact (#269): the user's guide calls V_Limit and
    C_Limit the working limits of each mode and never ties them to OVP/OCP; this fixes that the
    model trips (opens the input) instead of clamping. See the coverage notes of CURRent:VLIMt."""
    s = open_bench(load_bench())
    try:
        s.write("dut", f":SOUR:FUNC CURR;:SOUR:CURR:SLEW 1;{command};:SOUR:CURR 2;:SOUR:INP 1\n".encode())
        assert s.read("dut") is None
        s.advance(1e-3)  # the CC ramp reaches 2 A (slew 1 A/us) before the limit is checked
        assert readings(s)["current"] == pytest.approx(0.0, abs=1e-12)
        assert readings(s)["voltage"] == pytest.approx(10.0, rel=1e-9)  # the input is open
        trip = s.instrument("dut").components["prot"]
        assert trip.tripped() and trip.kind() == kind
    finally:
        s.close()


def test_opp_trips_at_the_rated_power():
    s = open_bench(load_bench())
    try:
        # 30 A into 0.1 ohm leaves 7 V: 210 W, just over the 200 W rating.
        s.write("dut", b":SOUR:FUNC CURR;:SOUR:CURR:SLEW 1;:SOUR:CURR 30;:SOUR:INP 1\n")
        assert s.read("dut") is None
        s.advance(1e-3)  # 30 A at 1 A/us
        values = readings(s)
        trip = s.instrument("dut").components["prot"]
        assert trip.tripped() and trip.kind() == "OPP"
        assert values["current"] == pytest.approx(0.0, abs=1e-12)
        assert values["voltage"] == pytest.approx(10.0, rel=1e-9)
    finally:
        s.close()
