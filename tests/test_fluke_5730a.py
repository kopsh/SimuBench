"""Fluke 5730A (issue 341): the calibrator's voltage output against the engine.

The manual cases (tests/models/fluke-5730a/cases.yaml) check command by command; these tests run the
real-instrument script examples/scripts/fluke_5730a_verify.py on the example bench (a Fluke 8588A
reads the points) and check what a transcript cannot see: the settling time in virtual time and the
overload trip back to standby.

Flow tests per D49/#704: one test walks one flow, each ``# --`` act preserves an original test's
body verbatim; acts that are provably state-compatible share one bench, with the bridging
commands noted.
"""

import math
import statistics
from pathlib import Path

import pytest

from helpers import load_script, run_manual_script
from simubench.engine.session import Session
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "fluke-5730a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "fluke_5730a_verify.py"
#: The readback noise the definition carries (specs.readback_noise_rms, #744/#809): the tightest
#: 1-year floor's 1/20 (DC 220 mV range, floor 0.5 uV), absolute rms on the panel's voltage readback.
READBACK_SIGMA = 2.5e-8

script = load_script(SCRIPT)


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
    out = run_manual_script(script, ["cal", "dmm"], BENCH, tmp_path, monkeypatch, capsys)
    assert out[0] == "FLUKE,5730A,5248000,1.0+B+*"
    assert out[-1] == "fault: 0"
    readings = [float(line.split("->")[1]) for line in out[1:-1]]
    # the 5730A's own errors are drawn within its 1-year specification (a few ppm)
    assert readings == pytest.approx([1.0, 10.0, -10.0, 1.0], rel=2e-5)
    assert [line.split()[1] for line in out[1:-1]] == ["V", "V", "V", "V"]


def test_the_definition_noise_distributes_and_replays():
    """The readback noise (specs.readback_noise_rms, #744/#809): the calibrator's own monitor
    reads the settled output with an independent draw of σ = 25 nV per panel frame and the same
    seed replays bit for bit; the terminal itself (what a UUT meter reads) carries none of it."""
    s, q = session(
        {"r": {"type": "load_resistor", "params": {"r": 1.0e6}}}, [{"from": "cal.OUT", "to": "r.A"}]
    )
    try:
        q("OUT 1 V;OPER")
        q("*OPC?")
        inst = s.instruments["cal"]
        out = inst.components["out"]
        true_v = inst.session.port_signal(out, "out").mean()  # the driven output, noiseless
        frames = [out.readings()["values"]["voltage"] for _ in range(200)]
        assert len(set(frames)) > 100  # every frame draws
        assert statistics.mean(frames) == pytest.approx(true_v, abs=4 * READBACK_SIGMA / math.sqrt(200))
        assert statistics.stdev(frames) == pytest.approx(READBACK_SIGMA, rel=0.2)
        first20 = frames[:20]
    finally:
        s.close()
    s, q = session(
        {"r": {"type": "load_resistor", "params": {"r": 1.0e6}}}, [{"from": "cal.OUT", "to": "r.A"}]
    )
    try:
        q("OUT 1 V;OPER")
        q("*OPC?")
        out = s.instruments["cal"].components["out"]
        again = [out.readings()["values"]["voltage"] for _ in range(20)]
        assert again == first20  # the same seed replays bit for bit
    finally:
        s.close()


# ---- the settling times: one flow -------------------------------------------------------------------


def test_the_settling_flow():
    """The output settles in 3 s of virtual time; #552: 3 s within a range, 4 s across a range
    or polarity change (chapter 1 settling time)."""
    s, q = session(
        {"r": {"type": "load_resistor", "params": {"r": 1.0e6}}}, [{"from": "cal.OUT", "to": "r.A"}]
    )
    try:
        # -- the output settles in three seconds (was test_the_output_settles_in_three_seconds_of_virtual_time)
        q("OUT 10 V")
        q("OPER")
        t0 = s.now_ns
        assert q("*OPC?") == "1"
        assert (s.now_ns - t0) / 1e9 == pytest.approx(3.0, abs=0.05)
        # -- a range or polarity change adds a second (was
        # test_a_range_or_polarity_change_adds_a_second_of_settling; the act's own OUT 1 V;OPER
        # makes the (unasserted) 11 V -> 2.2 V range change its premise starts from)
        # #552: 3 s within a range, 4 s across a range or polarity change (chapter 1 settling time).
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


def test_an_overload_trips_back_to_standby_with_fault_210():
    """10 V into 100 ohm draws 100 mA, past the 50 mA maximum load (DC voltage specifications).

    The 1100 V range's own 20 mA limit is not modeled per range (#556): i_max is 50 mA for every
    range, so 1000 V into 40 kohm (25 mA) does not trip here as it would on the instrument."""

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


def test_external_sense_regulates_at_the_uut():
    """#551: 5 V through a 1 ohm lead into 200 ohm: internal sense leaves the UUT 5 x 200/201;
    external sense (SENSE HI on the UUT) holds 5 V there."""
    parts = {
        "lead": {"type": "resistor", "params": {"r": 1.0}},
        "dut": {"type": "load_resistor", "params": {"r": 200.0}},
    }
    wires = [
        {"from": "cal.OUT", "to": "lead.A"},
        {"from": "lead.B", "to": "dut.A"},
        {"from": "cal.SENSE", "to": "dut.A"},
    ]
    s, q = session(parts, wires)
    try:
        inst = s.instruments["cal"]

        def at_uut() -> float:
            return inst.session.port_signal(inst.components["out"], "sense").mean()

        q("OUT 5 V;OPER")
        q("*OPC?")
        assert at_uut() == pytest.approx(5.0 * 200 / 201, rel=1e-4)
        q("EXTSENSE ON")
        q("*OPC?")
        assert at_uut() == pytest.approx(5.0, rel=1e-6)
        assert q("FAULT?") == "0"
    finally:
        s.close()


def test_a_sense_lead_into_a_dead_end_regulates_locally():
    """#562: SENSE wired to a resistor whose far end goes nowhere has no DC path to the output
    side, so the feedback cannot run: the output falls back to local regulation (as with an
    unwired SENSE) instead of a singular matrix."""
    s, q = session(
        {
            "lead": {"type": "resistor", "params": {"r": 1.0}},
            "dut": {"type": "load_resistor", "params": {"r": 200.0}},
            "fl": {"type": "resistor", "params": {"r": 1000.0}},
        },
        [
            {"from": "cal.OUT", "to": "lead.A"},
            {"from": "lead.B", "to": "dut.A"},
            {"from": "cal.SENSE", "to": "fl.A"},
        ],
    )
    try:
        inst = s.instruments["cal"]
        q("OUT 5 V;OPER")
        q("*OPC?")
        q("EXTSENSE ON")  # accepted: the range allows it, but the sense lead serves no feedback
        q("*OPC?")
        at_out = inst.session.port_signal(inst.components["out"], "out").mean()
        assert at_out == pytest.approx(5.0, abs=1e-4)  # local regulation at the output terminal
        sense_net = inst.session.port_signal(inst.components["out"], "sense").mean()
        assert sense_net == pytest.approx(0.0, abs=1e-6)  # the dead net carries no source
        assert q("FAULT?") == "0"
    finally:
        s.close()


def test_a_current_output_trips_past_its_compliance():
    """#550: 10 mA into 10 kohm needs 100 V, past the 10 V compliance: back to standby, fault 210;
    into 100 ohm it drives 1 V and stays."""
    s, q = session(
        {"r": {"type": "load_resistor", "params": {"r": 100.0}}}, [{"from": "cal.OUT", "to": "r.A"}]
    )
    try:
        inst = s.instruments["cal"]
        q("OUT 10 MA;OPER")
        q("*OPC?")
        assert inst.session.port_signal(inst.components["out"], "out").mean() == pytest.approx(1.0, rel=1e-9)
        assert q("FAULT?") == "0"
    finally:
        s.close()
    s, q = session(
        {"r": {"type": "load_resistor", "params": {"r": 1.0e4}}}, [{"from": "cal.OUT", "to": "r.A"}]
    )
    try:
        inst = s.instruments["cal"]
        q("OUT 10 MA;OPER")
        q("*OPC?")
        assert inst.session.port_signal(inst.components["out"], "out").mean() == pytest.approx(0.0, abs=1e-9)
        assert q("FAULT?") == "210"
    finally:
        s.close()


def test_a_resistance_output_is_the_nominal_value_in_the_circuit():
    """#550: OUT 10 KOHM puts 10 kohm across the terminals: a 1 V source behind 10 kohm reads 0.5 V."""
    parts = {"src": {"type": "dc_supply", "params": {"v": 1.0, "r": 1.0e4}}}
    s, q = session(parts, [{"from": "src.OUT", "to": "cal.OUT"}])
    try:
        q("OUT 10 KOHM;OPER")
        q("*OPC?")
        inst = s.instruments["cal"]
        assert inst.session.port_signal(inst.components["out"], "out").mean() == pytest.approx(0.5, rel=1e-6)
    finally:
        s.close()


@pytest.mark.parametrize(
    ("command", "ohms", "fault"),
    [
        ("OUT 20 MA", 450.0, "0"),  # 9 V: within the DC 10 V compliance
        ("OUT 20 MA, 1 KHZ", 450.0, "210"),  # 9 V rms: past the AC 7 V rms compliance
        ("OUT 1.5 A", 1.8, "0"),  # 2.7 V: within the 2.2 A DC range's 3 V
        ("OUT 1.5 A, 1 KHZ", 1.8, "210"),  # 2.7 V rms: past the 2.2 A AC range's 1.4 V rms
    ],
)
def test_the_compliance_follows_the_manual_per_range(command, ohms, fault):
    """#570: chapter 1 DC/AC Current Secondary Performance Specifications, Compliance Limits."""
    s, q = session(
        {"r": {"type": "load_resistor", "params": {"r": ohms}}}, [{"from": "cal.OUT", "to": "r.A"}]
    )
    try:
        q(command + ";OPER")
        q("*OPC?")
        inst = s.instruments["cal"]
        volts = inst.session.port_signal(inst.components["out"], "out").rms()  # the bench looks
        assert q("FAULT?") == fault
        assert (volts == 0.0) == (fault == "210")  # a trip leaves the terminal open
    finally:
        s.close()
