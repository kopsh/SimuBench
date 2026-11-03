"""Fluke 5730A (issue 341): the calibrator's voltage output against the engine.

The manual cases (tests/models/fluke-5730a/cases.yaml) check command by command; these tests run the
real-instrument script examples/scripts/fluke_5730a_verify.py on the example bench (a Fluke 8588A
reads the points) and check what a transcript cannot see: the settling time in virtual time and the
overload trip back to standby.
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
BENCH = ROOT / "examples" / "benches" / "fluke-5730a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "fluke_5730a_verify.py"


def _script():
    spec = importlib.util.spec_from_file_location("fluke_5730a_verify", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def session(parts: dict, wires: list):
    lib = DefinitionLibrary()
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "t",
            "instruments": {"cal": {"model": "fluke-5730a"}},
            "parts": parts,
            "wires": wires,
        }
    )
    s = Session(bench, lambda model: lib.get(model), clock="command", seed=3, idle="off")

    def q(cmd: str) -> str:
        s.write("cal", cmd.encode() + b"\n")
        out = s.read("cal")
        return out.decode().strip() if out else ""

    return s, q


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(sys, "argv", ["fluke_5730a_verify.py", "cal", "dmm", "--bench", str(bench)])
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "FLUKE,5730A,5248000,1.0+B+*"
    assert out[-1] == "fault: 0"
    readings = [float(line.split("->")[1]) for line in out[1:-1]]
    # the 5730A's own errors are drawn within its 1-year specification (a few ppm)
    assert readings == pytest.approx([1.0, 10.0, -10.0, 1.0], rel=2e-5)
    assert [line.split()[1] for line in out[1:-1]] == ["V", "V", "V", "V"]


def test_the_output_settles_in_three_seconds_of_virtual_time():
    s, q = session(
        {"r": {"type": "load_resistor", "params": {"r": 1.0e6}}}, [{"from": "cal.OUT", "to": "r.A"}]
    )
    try:
        q("OUT 10 V")
        q("OPER")
        t0 = s.now_ns
        assert q("*OPC?") == "1"
        assert (s.now_ns - t0) / 1e9 == pytest.approx(3.0, abs=0.05)
    finally:
        s.close()


def test_an_overload_trips_back_to_standby_with_fault_210():
    """10 V into 100 ohm draws 100 mA, past the 50 mA maximum load (DC voltage specifications)."""
    s, q = session(
        {"r": {"type": "load_resistor", "params": {"r": 100.0}}}, [{"from": "cal.OUT", "to": "r.A"}]
    )
    try:
        q("OUT 10 V;OPER")
        q("*OPC?")
        inst = s.instruments["cal"]
        assert inst.session.port_signal(inst.components["out"], "out").mean() == pytest.approx(0.0, abs=1e-9)
        assert q("FAULT?") == "210"
        assert q("FAULT?") == "0"
    finally:
        s.close()


def test_a_range_or_polarity_change_adds_a_second_of_settling():
    """#552: 3 s within a range, 4 s across a range or polarity change (chapter 1 settling time)."""
    s, q = session(
        {"r": {"type": "load_resistor", "params": {"r": 1.0e6}}}, [{"from": "cal.OUT", "to": "r.A"}]
    )
    try:
        q("OUT 1 V;OPER")
        q("*OPC?")

        def settle(cmd: str) -> float:
            t0 = s.now_ns
            q(cmd)
            q("*OPC?")
            return (s.now_ns - t0) / 1e9

        assert settle("OUT 1.5 V") == pytest.approx(3.0, abs=0.05)  # same 2.2 V range
        assert settle("OUT -1.5 V") == pytest.approx(4.0, abs=0.05)  # polarity
        assert settle("OUT -5 V") == pytest.approx(4.0, abs=0.05)  # 2.2 V -> 11 V range
    finally:
        s.close()
