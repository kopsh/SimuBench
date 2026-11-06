"""R&S SMA100B (issue 335): the generator's RF chain against the engine.

The manual cases (tests/models/rs-sma100b/cases.yaml) check command by command; these tests run the
real-instrument script examples/scripts/rs_sma100b_generate.py on the example bench and check what a
transcript cannot see: the level into a matched load, AM sidebands at the LF1 rate behind the master
switch, a frequency sweep stepping on each trigger, and a level sweep.
"""

from pathlib import Path

import numpy as np
import pytest

from helpers import load_script, run_manual_script
from simubench.engine.session import Session
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_BENCH = ROOT / "examples" / "benches" / "rs-sma100b.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "rs_sma100b_generate.py"
Z0 = 50.0

script = load_script(SCRIPT)


@pytest.fixture
def sg():
    lib = DefinitionLibrary()
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/2", "name": "sma", "instruments": {"sg": {"model": "rs-sma100b"}}}
    )
    s = Session(bench, lambda model: lib.get(model), clock="command", seed=7, idle="off")

    def q(cmd: str) -> str:
        s.write("sg", cmd.encode() + b"\n")
        out = s.read("sg")
        return out.decode().strip() if out else ""

    yield s, q
    s.close()


def lines(s):
    inst = s.instruments["sg"]
    comp, port = inst.ports["RF_OUT"]
    sig = inst.session.port_signal(inst.components[comp], port).merged()
    order = np.argsort(sig.freq)
    return sig.freq[order], np.abs(sig.amp[order])


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(
        script, ["sg", "--scope", "scope"], EXAMPLE_BENCH, tmp_path, monkeypatch, capsys, rm_wrap=False
    )
    assert out[0] == "Rohde&Schwarz,SMA100B,1419.8888K02/101234,5.40.102.48"
    assert out[1].startswith("AGILENT TECHNOLOGIES,DSO-X 2024A,")
    assert out[2].startswith("carrier: 10.00")  # the counter rides the noise (#745)
    assert out[3] == "expected for 0 dBm: 1.2649 Vpp"
    assert out[4].startswith("20 dB down: 0.1")  # the low reading includes the noise floor (#745)
    assert out[6] == '0,"No error"'


def test_the_level_is_defined_into_a_matched_load(sg):
    s, q = sg
    q("FREQ 10 MHz;:POW 0;:OUTP ON")
    freq, amp = lines(s)
    assert freq.tolist() == pytest.approx([10e6])
    # open-circuit EMF of a source that delivers 0 dBm into 50 Ω: 2 x sqrt(2 x 50 x 1 mW) peak
    assert amp[0] == pytest.approx(2 * np.sqrt(2 * Z0 * 1e-3), rel=1e-9)


def test_am_from_lf1_needs_the_master_switch(sg):
    s, q = sg
    q("FREQ 10 MHz;:POW 0;:OUTP ON;:AM 50;:LFO:FREQ 10 kHz;:AM:STAT ON")
    assert lines(s)[0].size == 1  # master switch off (*RST): the carrier alone
    q("MOD ON")
    freq, amp = lines(s)
    assert freq.tolist() == pytest.approx([9.99e6, 10e6, 10.01e6])
    assert amp[0] / amp[1] == pytest.approx(0.25, rel=1e-9)  # m/2 of the carrier


def test_a_step_sweep_moves_one_point_per_trigger(sg):
    s, q = sg
    q("OUTP ON;:FREQ:STAR 100 MHz;:FREQ:STOP 200 MHz;:SWE:POIN 11;:SWE:MODE STEP;:TRIG:FSW:SOUR SING")
    q("FREQ:MODE SWE")
    first = lines(s)[0][0]
    q("*TRG")
    second = lines(s)[0][0]
    q("*TRG")
    third = lines(s)[0][0]
    assert [first, second, third] == pytest.approx([100e6, 110e6, 120e6])


def test_a_level_sweep_steps_the_level(sg):
    s, q = sg
    q("FREQ 10 MHz;:OUTP ON;:POW:STAR -20;:POW:STOP 0;:SWE:POW:POIN 3;:SWE:POW:MODE STEP")
    q("TRIG:PSW:SOUR SING;:POW:MODE SWE")
    levels = []
    for _ in range(3):
        amp = lines(s)[1][0]
        levels.append(20 * np.log10(amp / (2 * np.sqrt(2 * Z0 * 1e-3))))
        q("*TRG")
    assert levels == pytest.approx([-20.0, -10.0, 0.0], abs=1e-9)
