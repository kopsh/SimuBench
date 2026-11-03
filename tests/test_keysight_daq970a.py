"""Keysight DAQ970A (issue 332): the Programming Guide's scan, trigger and reading-memory flow.

The manual cases (tests/models/keysight-daq970a/cases.yaml) check command by command; these tests
run the real-instrument-style script examples/scripts/keysight_daq970a_scan.py on the example bench
and check what a transcript cannot see: READ? keeps its readings in reading memory (it is INITiate
plus FETCh?, unlike the 34970A), CONFigure sets the trigger count and interval, the relay closure
loads the bench circuit, and the DAQM901A's four-wire pairing and current channels.
"""

import importlib.util
import math
import shutil
import sys
from pathlib import Path

import pyvisa

from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight-daq970a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_daq970a_scan.py"


def _script():
    spec = importlib.util.spec_from_file_location("keysight_daq970a_scan", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def q(s, command: str) -> str | None:
    s.write("daq", (command + "\n").encode())
    out = s.read("daq")
    return out.decode().strip() if out else None


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    monkeypatch.setattr(sys, "argv", ["keysight_daq970a_scan.py", "daq", "--bench", str(bench)])
    script.main()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Keysight Technologies,DAQ970A,MY12345678,A.02.04-00.16-11.29-00.02-02-01"
    assert out[1] == "scan (@101:103): 0.90909,1.818177,3.636357"
    assert out[2] == "opc: 1"
    assert out[3] == "memory: points=6 r=2 removed=3 left=1"
    assert out[-1] == '+0,"No error"'


def test_read_keeps_its_readings_in_memory_and_a_new_scan_clears_them():
    s = open_bench(BENCH, idle="off")
    try:
        q(s, "CONF:VOLT:DC 10,DEF,(@101:103)")
        first = q(s, "READ?")
        assert q(s, "DATA:POIN?") == "3"
        assert q(s, "FETC?") == first
        q(s, "ROUT:SCAN (@102)")
        assert q(s, "READ?") == first.split(",")[1]
        assert q(s, "DATA:POIN?") == "1"  # INITiate clears reading memory first
    finally:
        s.close()


def test_configure_sets_one_sweep_at_one_second_and_a_relay_loads_the_source():
    s = open_bench(BENCH, idle="off")
    try:
        assert q(s, "TRIG:TIM?") == "+1.00000000E+01"  # Factory Reset State: 10 s
        q(s, "TRIG:COUN 5")
        q(s, "CONF:VOLT:DC (@101)")
        assert float(q(s, "TRIG:COUN?")) == 1.0
        assert float(q(s, "TRIG:TIM?")) == 1.0
        q(s, "ROUT:CLOS (@101)")
        assert q(s, "ROUT:CLOS? (@101,102)") == "1,0"
        assert math.isclose(float(q(s, "READ?")), 10.0 / 11.0, rel_tol=1e-4)
    finally:
        s.close()


def test_four_wire_pairs_n_with_n_plus_ten_and_current_is_on_21_and_22():
    s = open_bench(BENCH, idle="off")
    try:
        two = float(q(s, "MEAS:RES? (@104)"))
        four = float(q(s, "MEAS:FRES? (@104)"))
        assert math.isclose(four, 1000.0, rel_tol=1e-4)
        assert two > four  # the two-wire reading includes the relay path
        assert math.isclose(float(q(s, "MEAS:CURR:DC? (@121)")), 5.0 / 1006.0, rel_tol=2e-2)
        assert q(s, "SYST:ERR?") == '+0,"No error"'
        q(s, "MEAS:CURR:DC? (@101)")  # not a current channel
        assert q(s, "SYST:ERR?").startswith("-221")
    finally:
        s.close()


def test_the_scan_list_reads_back_as_a_block_and_empties():
    s = open_bench(BENCH, idle="off")
    try:
        q(s, "ROUT:SCAN (@101:103,110)")
        reply = q(s, "ROUT:SCAN?")
        body = script.block(reply)
        assert body == "(@101:103,110)" and reply == f"#2{len(body)}{body}"
        q(s, "ROUT:SCAN (@)")
        assert q(s, "ROUT:SCAN?") == "#13(@)"
    finally:
        s.close()


def test_the_resource_manager_opens_the_example_bench(tmp_path):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        daq = rm.open_resource("daq", read_termination="\n", write_termination="\n")
        assert daq.query("*IDN?").startswith("Keysight Technologies,DAQ970A,")
        daq.close()
    finally:
        rm.close()
