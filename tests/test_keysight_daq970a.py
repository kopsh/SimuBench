"""Keysight DAQ970A (issue 332): the Programming Guide's scan, trigger and reading-memory flow.

The manual cases (tests/models/keysight-daq970a/cases.yaml) check command by command; these tests
run the real-instrument-style script examples/scripts/keysight_daq970a_scan.py on the example bench
and check what a transcript cannot see: READ? keeps its readings in reading memory (it is INITiate
plus FETCh?, unlike the 34970A), CONFigure sets the trigger count and interval, the relay closure
loads the bench circuit, and the DAQM901A's four-wire pairing and current channels.

Flow tests per D49/#704: one test walks one flow, each ``# --`` act preserves an original test's
body verbatim; acts that are provably state-compatible share one bench, with the bridging
commands noted.
"""

import math
import shutil
import statistics
from pathlib import Path

import pytest
import pyvisa

from helpers import load_script, run_manual_script, session_query
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight-daq970a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_daq970a_scan.py"

script = load_script(SCRIPT)


def q(s, command: str) -> str | None:
    return session_query(s, "daq", command)


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(script, ["daq"], BENCH, tmp_path, monkeypatch, capsys, rm_wrap=False)
    assert out[0] == "Keysight Technologies,DAQ970A,MY12345678,A.02.04-00.16-11.29-00.02-02-01"
    scanned = [float(x) for x in out[1].removeprefix("scan (@101:103): ").split(",")]
    assert out[1].startswith("scan (@101:103): ")
    # the readings ride the noise (#806): 0.25 ppm of the 10 V range, 4σ ≈ 1e-5
    assert scanned == pytest.approx([0.90909, 1.818177, 3.636357], abs=1e-5)
    assert out[2] == "opc: 1"
    assert out[3] == "memory: points=6 r=2 removed=3 left=1"
    assert out[-1] == '+0,"No error"'


# ---- the scan, the memory, the relay load and the scan-list block: one flow -------------------------


def test_the_scan_and_memory_flow():
    """READ? keeps its readings in memory and a new scan clears them; CONFigure sets one sweep at
    one second and the relay closure loads the source; the four-wire pairing and the current
    channels follow the module; the scan list reads back as a definite-length block and empties."""
    s = open_bench(BENCH, idle="off")
    try:
        # -- READ? keeps its readings in memory, a new scan clears them (was
        # test_read_keeps_its_readings_in_memory_and_a_new_scan_clears_them)
        q(s, "CONF:VOLT:DC 10,DEF,(@101:103)")
        first = q(s, "READ?")
        assert q(s, "DATA:POIN?") == "3"
        assert q(s, "FETC?") == first
        q(s, "ROUT:SCAN (@102)")
        assert q(s, "READ?") == first.split(",")[1]
        assert q(s, "DATA:POIN?") == "1"  # INITiate clears reading memory first
        # -- bridge: the CONFigure act's premise is the Factory Reset State (10 s interval), which
        # the act above's CONFigure replaced with 1 s
        q(s, "*RST")
        # -- CONFigure sets one sweep at one second, the relay loads the source (was
        # test_configure_sets_one_sweep_at_one_second_and_a_relay_loads_the_source)
        assert q(s, "TRIG:TIM?") == "+1.00000000E+01"  # Factory Reset State: 10 s
        q(s, "TRIG:COUN 5")
        q(s, "CONF:VOLT:DC (@101)")
        assert float(q(s, "TRIG:COUN?")) == 1.0
        assert float(q(s, "TRIG:TIM?")) == 1.0
        q(s, "ROUT:CLOS (@101)")
        assert q(s, "ROUT:CLOS? (@101,102)") == "1,0"
        assert math.isclose(float(q(s, "READ?")), 10.0 / 11.0, rel_tol=1e-4)
        # -- the four-wire pairing and the current channels (was
        # test_four_wire_pairs_n_with_n_plus_ten_and_current_is_on_21_and_22; each MEASure? form
        # configures its own channels, and every error below is read out as it comes)
        two = float(q(s, "MEAS:RES? (@104)"))
        four = float(q(s, "MEAS:FRES? (@104)"))
        assert math.isclose(four, 1000.0, rel_tol=1e-4)
        assert two > four  # the two-wire reading includes the relay path
        assert math.isclose(float(q(s, "MEAS:CURR:DC? (@121)")), 5.0 / 1006.0, rel_tol=2e-2)
        assert q(s, "SYST:ERR?") == '+0,"No error"'
        q(s, "MEAS:CURR:DC? (@101)")  # not a current channel
        assert q(s, "SYST:ERR?").startswith("-221")
        # -- the scan list reads back as a block and empties (was
        # test_the_scan_list_reads_back_as_a_block_and_empties)
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


def test_the_definition_noise_distributes_replays_and_keeps_rms_non_negative():
    """The reading noise (specs.noise_ppm_of_range, #744/#806): MEAS readings are independent draws
    around the solved value with σ = 0.25 ppm of the range at the reading time (the resolution
    quantization, larger than σ here, is switched off to see it), the same seed replays bit for
    bit, and an RMS reading of zero is never negative (#803)."""
    sigma = 0.25e-6 * 10.0 * math.sqrt(0.02 / 0.021)  # the 10 V range at 1 PLC

    def run(n: int, noisy: bool = True) -> tuple[list[float], list[float]]:
        s = open_bench(BENCH, idle="off")
        try:
            s.instruments["daq"].components["meter"].overrides["resolution"] = 0.0
            if not noisy:
                s.instruments["daq"].components["meter"].overrides["noise"] = None
            volts = [float(q(s, "MEAS:VOLT:DC? 10,DEF,(@101)")) for _ in range(n)]
            ac = [float(q(s, "MEAS:VOLT:AC? (@101)")) for _ in range(n)]
            return volts, ac
        finally:
            s.close()

    true = run(1, noisy=False)[0][0]  # the solved value (the relay's and the source's loading)
    volts, ac = run(200)
    assert len(set(volts)) > 100
    assert statistics.mean(volts) == pytest.approx(true, abs=4 * sigma / math.sqrt(200))
    assert statistics.stdev(volts) == pytest.approx(sigma, rel=0.25)
    assert min(ac) >= 0.0 and len(set(ac)) > 20  # |noise| on 0 V AC: spread, never negative
    assert run(20)[0] == volts[:20]  # the same seed replays bit for bit
