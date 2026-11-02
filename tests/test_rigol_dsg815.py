"""Rigol DSG815 (issue 197): the generator's RF chain against the engine.

The manual cases (tests/models/rigol-dsg815/cases.yaml) check command by command; these tests run
the real-instrument script examples/scripts/rigol_dsg815_generate.py on the example bench and then
check the parts a SCPI transcript cannot see: the level into a matched load, the AM sidebands, the
sweep advancing point by point on the trigger, and a hidden timebase error moving the carrier.
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
import pyvisa

from simubench.engine.session import Session
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_BENCH = ROOT / "examples" / "benches" / "rigol_dsg815.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "rigol_dsg815_generate.py"
Z0 = 50.0


def _script():
    spec = importlib.util.spec_from_file_location("rigol_dsg815_generate", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def _bench(state: dict | None = None, hidden: dict | None = None) -> BenchDefinition:
    instrument: dict = {"model": "rigol-dsg815"}
    if state:
        instrument["state"] = state
    if hidden:
        instrument["hidden"] = hidden
    return BenchDefinition.model_validate(
        {"schema": "simubench.bench/2", "name": "dsg815", "instruments": {"sg": instrument}}
    )


@pytest.fixture
def session():
    def open_it(**kwargs):
        lib = DefinitionLibrary()
        return Session(_bench(**kwargs), lambda model: lib.get(model), clock="stepped", seed=7)

    opened = []

    def factory(**kwargs):
        s = open_it(**kwargs)
        opened.append(s)
        return s

    yield factory
    for s in opened:
        s.close()


def carrier(inst):
    """The signal on RF_OUT: (frequencies, peak amplitudes)."""
    comp, port = inst.ports["RF_OUT"]
    sig = inst.session.port_signal(inst.components[comp], port)
    return sig.freq, np.abs(sig.amp)


def peaks(dbm: float) -> float:
    """Open-circuit peak voltage of a source whose level is defined into a matched load."""
    return 2 * np.sqrt(2 * Z0 * 1e-3 * 10 ** (dbm / 10))


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    bench.write_text(EXAMPLE_BENCH.read_text("utf-8"), "utf-8")
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(
            sys,
            "argv",
            ["rigol_dsg815_generate.py", "sg", "--scope", "scope", "--bench", str(bench)],
        )
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Rigol Technologies,DSG815,DSG8A170200001,00.01.07."
    assert out[1].startswith("AGILENT TECHNOLOGIES,DSO-X 2024A,")
    assert out[2].startswith("carrier: 10.000000 MHz, 1.2")
    assert out[3] == "expected for 0 dBm: 1.2649 Vpp"
    assert out[4].startswith("20 dB down: 0.12")
    assert out[5].startswith("AM 50 %: ")
    assert out[6] == '0,"No error"'
    assert out[7].startswith("+0,")


def test_the_level_is_defined_into_a_matched_load(session):
    s = session(state={"power.level": 0.0, "output.enabled": True, "freq.cw": 10e6})
    sg = s.instruments["sg"]
    freq, amp = carrier(sg)
    assert freq[0] == pytest.approx(10e6, rel=1e-12)
    # The level control makes the open-circuit EMF of a source that would deliver 0 dBm into 50 Ω;
    # a matched load would see half of it (0.316 V peak, 0.633 Vpp).
    assert amp[0] == pytest.approx(peaks(0.0), rel=1e-9)


def test_the_output_switch_removes_the_signal(session):
    s = session(state={"power.level": 0.0, "output.enabled": False, "freq.cw": 10e6})
    sg = s.instruments["sg"]
    _freq, amp = carrier(sg)
    assert amp.size == 0 or np.allclose(amp, 0.0)


def test_am_puts_sidebands_at_the_modulation_rate(session):
    s = session(
        state={
            "power.level": 0.0,
            "output.enabled": True,
            "freq.cw": 10e6,
            "mod.state": True,
            "am.state": True,
            "am.depth": 50.0,
            "am.rate": 10e3,
        }
    )
    sg = s.instruments["sg"]
    freq, amp = carrier(sg)
    order = np.argsort(freq)
    freq, amp = freq[order], amp[order]
    assert list(freq) == pytest.approx([9.99e6, 10e6, 10.01e6])
    # AM 50 %: the sidebands are m/2 = 25 % of the carrier (the guide's 20 lg(m/2) relation).
    assert amp[0] / amp[1] == pytest.approx(0.25, rel=1e-9)
    assert amp[2] / amp[1] == pytest.approx(0.25, rel=1e-9)


def test_fm_keeps_the_envelope_and_spreads_the_lines(session):
    s = session(
        state={
            "power.level": 0.0,
            "output.enabled": True,
            "freq.cw": 10e6,
            "mod.state": True,
            "fm.state": True,
            "fm.dev": 20e3,
            "fm.rate": 10e3,
        }
    )
    sg = s.instruments["sg"]
    freq, amp = carrier(sg)
    # beta = 2: Carson keeps |n| <= 3, seven lines around the carrier; the truncation drops the
    # power outside them (validation-rfgen.md 3).
    from simubench.library.components.rfgen import omitted_power_fraction

    assert freq.size == 7
    kept = peaks(0.0) * np.sqrt(1 - omitted_power_fraction(2.0))
    assert np.sqrt(np.sum(amp**2)) == pytest.approx(kept, rel=1e-9)


def test_the_sweep_advances_on_the_trigger(session):
    s = session(
        state={
            "power.level": 0.0,
            "output.enabled": True,
            "sweep.start": 100e6,
            "sweep.stop": 200e6,
            "sweep.points": 3,
            "sweep.point_trigger": "BUS",  # points advance on :TRIGger / *TRG (AUTO: by dwell)
        }
    )
    sg = s.instruments["sg"]

    def at(commands: str) -> float:
        for command in commands.split(";"):
            s.write("sg", (command + "\n").encode())
        freq, _ = carrier(sg)
        return float(freq[0])

    assert at(":SWEep:STATe FREQuency") == pytest.approx(100e6)
    assert at(":TRIGger") == pytest.approx(150e6)
    assert at("*TRG") == pytest.approx(200e6)
    assert at(":TRIGger") == pytest.approx(200e6)  # the last point is where it stops
    assert at(":SWEep:EXECute") == pytest.approx(100e6)
    assert at(":SWEep:RESet") == pytest.approx(100e6)
    # Turning the sweep off returns to the set CW carrier.
    s.write("sg", b":FREQuency 10MHz\n")
    assert at(":SWEep:STATe OFF") == pytest.approx(10e6)


def test_a_hidden_timebase_error_moves_the_carrier(session):
    s = session(
        state={"power.level": 0.0, "output.enabled": True, "freq.cw": 10e6},
        hidden={"reference": {"ppm": 2.0}},
    )
    sg = s.instruments["sg"]
    freq, _ = carrier(sg)
    # No command reads the 2 ppm error back: only the RF output carries it.
    assert freq[0] == pytest.approx(10e6 * (1 + 2e-6), rel=1e-12)
    assert float(sg.state["freq.cw"]) == 10e6


def test_the_modulation_master_switch_gates_every_modulation(session):
    """#290: Mod/on (:MODulation:STATe, factory Off) — with it off the carrier is bare and the AM
    settings stay; on, the sidebands appear."""
    s = session(state={"power.level": 0.0, "output.enabled": True, "freq.cw": 10e6})
    sg = s.instruments["sg"]

    def q(cmd: str) -> str:
        s.write("sg", cmd.encode() + b"\n")
        out = s.read("sg")
        return out.decode().strip() if out else ""

    q(":AM:DEPT 50;:AM:FREQ 10kHz;:AM:STAT ON")
    assert q(":MOD:STAT?") == "0"
    assert carrier(sg)[0].size == 1  # the carrier alone
    q(":MOD:STAT ON")
    assert q(":MOD:STAT?") == "1"
    assert sorted(carrier(sg)[0]) == pytest.approx([9.99e6, 10e6, 10.01e6])
    q(":MOD:STAT OFF")
    assert carrier(sg)[0].size == 1 and q(":AM:STAT?") == "1"  # the AM switch keeps its setting
    assert q(":SYST:ERR?") == '0,"No error"'


def _level_dbm(sg) -> float:
    _freq, amp = carrier(sg)
    from simubench.library.components.rfgen import emf_peak

    return 20 * np.log10(float(amp[0]) / emf_peak(0.0))


def test_a_level_and_frequency_sweep_steps_by_the_dwell_in_virtual_time(session):
    """#291, the manual's chapter 3 sweep: LEV,FREQ together, AUTO point trigger, 500 ms dwell."""
    s = session(state={"power.level": -50.0, "output.enabled": True})
    sg = s.instruments["sg"]
    for command in (
        ":SWE:STEP:STAR:FREQ 1GHz;:SWE:STEP:STOP:FREQ 1.4GHz",
        ":SWE:STEP:STAR:LEV -20;:SWE:STEP:STOP:LEV 0",
        ":SWE:STEP:POIN 5;:SWE:STEP:DWEL 500ms",
        ":SWE:STAT LEV,FREQ",
    ):
        s.write("sg", (command + "\n").encode())
    assert float(carrier(sg)[0][0]) == pytest.approx(1.0e9, rel=1e-9)
    assert _level_dbm(sg) == pytest.approx(-20.0, abs=1e-9)
    s.advance(1.2)  # the third point (0.5 s per point)
    assert float(carrier(sg)[0][0]) == pytest.approx(1.2e9, rel=1e-9)
    assert _level_dbm(sg) == pytest.approx(-10.0, abs=1e-9)
    s.advance(1.5)  # continuous: past the fifth point the pass repeats
    assert float(carrier(sg)[0][0]) == pytest.approx(1.0e9, rel=1e-9)


def test_a_single_sweep_runs_once_from_execute_and_holds_the_last_point(session):
    s = session(state={"power.level": -50.0, "output.enabled": True})
    sg = s.instruments["sg"]
    s.write("sg", b":SWE:STEP:STAR:LEV -30;:SWE:STEP:STOP:LEV -10;:SWE:STEP:POIN 3;:SWE:STEP:DWEL 100ms\n")
    s.write("sg", b":SWE:MODE SING;:SWE:STAT LEV\n")
    assert _level_dbm(sg) == pytest.approx(-30.0, abs=1e-9)  # not started: the first point
    s.write("sg", b":SWE:EXEC\n")
    s.advance(0.15)
    assert _level_dbm(sg) == pytest.approx(-20.0, abs=1e-9)
    s.advance(1.0)
    assert _level_dbm(sg) == pytest.approx(-10.0, abs=1e-9)  # held at the last point
    s.write("sg", b":SWE:STAT OFF\n")
    assert _level_dbm(sg) == pytest.approx(-50.0, abs=1e-9)  # back to the set level
