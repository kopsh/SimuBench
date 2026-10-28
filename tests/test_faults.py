"""Faults seen the way a script sees them: through PyVISA, on the virtual clock."""

import pytest
import pyvisa
import yaml
from pyvisa import constants, errors

from simubench.engine.validate import validate_data
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import Fault

BENCH = {
    "schema": "simubench.bench/1",
    "name": "faults",
    "instruments": {
        "awg": {"model": "sb-awg-2", "state": {"ch1.amplitude": 2.0, "ch1.load": "INF"}},
        "scope": {"model": "sb-dso-4"},
    },
    "duts": {"rc": {"type": "rc_lowpass", "params": {"r": 1000, "c": 1e-7}}},
    "wires": [{"from": "awg.OUT1", "to": "rc.IN"}, {"from": "rc.OUT", "to": "scope.CH1"}],
    "run": {"clock": "command", "seed": 3},
}


def open_bench(tmp_path, name="f", **extra):
    # PyVISA keeps one backend per bench path, so separate runs need separate files.
    path = tmp_path / f"{name}.bench.yaml"
    path.write_text(yaml.safe_dump({**BENCH, **extra}), encoding="utf-8")
    rm = pyvisa.ResourceManager(f"{path}@simubench")
    awg = rm.open_resource("awg", read_termination="\n", write_termination="\n", timeout=500)
    return rm, awg, rm.visalib.bench


def out_amplitude(session) -> float:
    net = session.port_net["rc.OUT"]
    return float(abs(session.net_signal(net).merged().amp).max())


def test_timeout_drops_messages_until_cleared(tmp_path):
    rm, awg, s = open_bench(tmp_path)
    f = s.faults.inject(Fault(kind="timeout", instrument="awg"))
    with pytest.raises(errors.VisaIOError) as exc:
        awg.query("*IDN?")
    assert exc.value.error_code == constants.VI_ERROR_TMO
    s.faults.clear(f.id)
    assert "SB-AWG-2" in awg.query("*IDN?")
    kinds = [(e["kind"], e.get("reason")) for e in s.history if e["kind"] == "fault"]
    assert kinds == [("fault", "injected"), ("fault", "active"), ("fault", "cleared")]
    rm.close()


def test_disconnect_is_a_lost_connection(tmp_path):
    rm, awg, s = open_bench(tmp_path)
    s.faults.inject(Fault(kind="disconnect", instrument="awg", duration_s=1.0))
    with pytest.raises(errors.VisaIOError) as exc:
        awg.write("*CLS")
    assert exc.value.error_code == constants.VI_ERROR_CONN_LOST
    s.advance(1.0)  # the fault lasts one second of virtual time
    assert "SB-AWG-2" in awg.query("*IDN?")
    rm.close()


def test_garble_corrupts_a_counted_number_of_responses_reproducibly(tmp_path):
    answers = []
    for run in range(2):
        rm, awg, s = open_bench(tmp_path, name=f"run{run}")
        s.faults.inject(Fault(kind="garble", instrument="awg", count=1, garble_rate=0.5))
        answers.append(awg.query("*IDN?"))
        assert awg.query("*IDN?") == "SIMUBENCH,SB-AWG-2,SBAWG000001,1.0.0"  # count used up
        rm.close()
    assert answers[0] != "SIMUBENCH,SB-AWG-2,SBAWG000001,1.0.0"
    assert answers[0] == answers[1]  # same seed, same corruption


def test_latency_costs_virtual_time(tmp_path):
    rm, awg, s = open_bench(tmp_path)
    t0 = s.now_ns
    s.faults.inject(Fault(kind="latency", instrument="awg", latency_s=0.25))
    awg.query("*IDN?")  # one write and one read
    assert s.now_ns - t0 >= 500_000_000
    rm.close()


def test_busy_holds_the_answer_until_it_ends(tmp_path):
    rm, awg, s = open_bench(tmp_path)
    s.faults.inject(Fault(kind="busy", instrument="awg", duration_s=0.4))
    awg.write("*IDN?")
    with pytest.raises(errors.VisaIOError):
        awg.read()  # the 0.5 s timeout runs past the busy window
    assert "SB-AWG-2" in awg.read()
    rm.close()


def test_error_and_trip(tmp_path):
    rm, awg, s = open_bench(tmp_path)
    awg.write(":OUTPut1 ON")
    s.faults.inject(Fault(kind="error", instrument="awg", code=-313))
    assert awg.query(":SYSTem:ERRor?") == '-313,"Calibration memory lost"'
    s.faults.inject(Fault(kind="trip", instrument="awg"))
    assert awg.query(":OUTPut1?") in ("0", "OFF")
    assert awg.query(":SYSTem:ERRor?").startswith("-300,")
    rm.close()


def test_hidden_and_drift_change_the_circuit_and_are_undone(tmp_path):
    rm, awg, s = open_bench(tmp_path)
    awg.write(":SOURce1:FREQuency 1600")
    awg.write(":OUTPut1 ON")
    base = out_amplitude(s)
    f = s.faults.inject(Fault(kind="hidden", component="rc", param="c", value=2e-7))
    assert out_amplitude(s) < base * 0.8  # corner frequency halved
    s.faults.clear(f.id)
    assert out_amplitude(s) == pytest.approx(base)

    s.advance(0.25)  # time before the fault does not count towards its drift
    d = s.faults.inject(Fault(kind="drift", component="rc", param="r", drift_per_s=0.5))
    s.advance(1.0)  # r has grown by 50 %
    assert out_amplitude(s) < base
    assert s.components["rc"].param("r") == pytest.approx(1500)
    s.faults.clear(d.id)
    assert s.components["rc"].param("r") == pytest.approx(1000)
    rm.close()


def test_preset_fault_starts_at_its_time(tmp_path):
    rm, awg, s = open_bench(tmp_path, faults=[{"kind": "timeout", "instrument": "awg", "at_s": 2.0}])
    assert "SB-AWG-2" in awg.query("*IDN?")
    s.advance(2.0)
    with pytest.raises(errors.VisaIOError):
        awg.query("*IDN?")
    rm.close()


def test_fault_targets_are_checked(tmp_path):
    rm, _awg, s = open_bench(tmp_path)
    with pytest.raises(KeyError):
        s.faults.inject(Fault(kind="timeout", instrument="nope"))
    with pytest.raises(ValueError, match="no parameter"):
        s.faults.inject(Fault(kind="hidden", component="rc", param="zz", value=1))
    rm.close()
    _, report = validate_data(
        {
            **BENCH,
            "faults": [
                {"kind": "error", "instrument": "aw"},
                {"kind": "drift", "component": "rx", "param": "r"},
            ],
        },
        DefinitionLibrary(),
    )
    assert [d.code for d in report.diagnostics] == ["E-FAULT-TARGET", "E-FAULT-TARGET"]
