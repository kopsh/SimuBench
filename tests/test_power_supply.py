"""Programmable power supply (B2 #185): each channel's setpoint, CV/CC control loop (reused from
B1), power stage, output relay/sense, readback and OVP/OCP latch; the ±25 V outputs' track,
parallel and series combination.

The validation definition ``sb-ps-1`` is a three-output supply like the E36312A: CH1 +6 V/5 A,
CH2 +25 V/1 A, CH3 −25 V/1 A. The combination is settings-level (the programmed levels stay
consistent) plus the bench wiring that represents the physical connection: parallel crosses the
negative output's terminals, series ties the two negative terminals, track mirrors the absolute
voltage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.engine.validate_definition import validate_definition
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"

CH1 = [
    {"from": "ps.OUT1+", "to": "r1.A"},
    {"from": "r1.B", "to": "ps.OUT1-"},
]
CH3 = [
    {"from": "ps.OUT3+", "to": "r1.A"},
    {"from": "r1.B", "to": "ps.OUT3-"},
]
#: Parallel: CH2+ joins CH3− and CH2− joins CH3+ (the negative output is crossed).
PARALLEL = [
    {"from": "ps.OUT2+", "to": "r1.A"},
    {"from": "ps.OUT3-", "to": "r1.A"},
    {"from": "r1.B", "to": "ps.OUT2-"},
    {"from": "r1.B", "to": "ps.OUT3+"},
]
#: Series: the two negative terminals are tied and the load spans the two positive ones.
SERIES = [
    {"from": "ps.OUT2+", "to": "r1.A"},
    {"from": "ps.OUT2-", "to": "ps.OUT3-"},
    {"from": "r1.B", "to": "ps.OUT3+"},
]


def bench(wires: list[dict] | None = None, r: float = 10.0) -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "ps",
            "instruments": {"ps": {"model": "sb-ps-1"}},
            "parts": {"r1": {"type": "resistor", "params": {"r": r}}},
            "wires": CH1 if wires is None else wires,
            "run": {"clock": "command", "seed": 1, "idle": "off"},
        }
    )


@pytest.fixture
def open_():
    sessions = []

    def open(**kw):
        session = open_bench(bench(**kw), definition_dirs=[DEFS])
        sessions.append(session)
        return session

    yield open
    for session in sessions:
        session.close()


def q(s, cmd: str) -> str:
    s.write("ps", (cmd + "\n").encode())
    out = s.read("ps")
    return out.decode().strip() if out else ""


def numbers(text: str) -> list[float]:
    return [float(x) for x in text.split(",")]


def relay(s, seconds: float = 0.011) -> None:
    s.advance(seconds)


# ---- the definition and the single output -----------------------------------------------------------


def test_the_definition_is_clean():
    assert validate_definition(DefinitionLibrary([DEFS]).get("sb-ps-1")).diagnostics == []


def test_cv_regulates_and_the_relay_delays_the_output(open_):
    s = open_()
    q(s, "VOLT 5,(@1)")
    q(s, "OUTP ON,(@1)")
    # The relay closes after relay_s: nothing conducts until virtual time passes (D12).
    assert numbers(q(s, "MEAS:VOLT? (@1)"))[0] == pytest.approx(0.0, abs=1e-9)
    relay(s)
    assert numbers(q(s, "MEAS:VOLT? (@1)"))[0] == pytest.approx(5.0, rel=1e-3)
    assert numbers(q(s, "MEAS:CURR? (@1)"))[0] == pytest.approx(0.5, rel=1e-3)
    assert float(q(s, "MEAS:POW? (@1)")) == pytest.approx(2.5, rel=1e-3)


def test_the_current_limit_turns_the_output_into_cc(open_):
    s = open_()
    q(s, "VOLT 5,(@1)")
    q(s, "CURR 0.1,(@1)")
    q(s, "OUTP ON,(@1)")
    relay(s)
    assert numbers(q(s, "MEAS:VOLT? (@1)"))[0] == pytest.approx(1.0, abs=1e-3)
    assert numbers(q(s, "MEAS:CURR? (@1)"))[0] == pytest.approx(0.1, rel=1e-6)
    values = s.instrument("ps").components["ch1.read"].readings()["values"]
    assert values["regulation"] == "CC"


def test_the_power_rating_holds_the_output_to_p_max(open_):
    """#533: CH1 is 6.18 V / 5.15 A but its stage is rated 26 W. 6 V into 1.2 ohm asks for 30 W:
    the output runs at constant power (I = sqrt(26 / 1.2)) and reports CP; a lower setpoint brings
    CV back."""
    s = open_(r=1.2)
    q(s, "VOLT 6,(@1)")
    q(s, "CURR 5.15,(@1)")
    q(s, "OUTP ON,(@1)")
    relay(s)
    i = numbers(q(s, "MEAS:CURR? (@1)"))[0]
    v = numbers(q(s, "MEAS:VOLT? (@1)"))[0]
    assert i == pytest.approx((26.0 / 1.2) ** 0.5, rel=2e-3)
    assert v * i == pytest.approx(26.0, rel=2e-3)
    assert s.instrument("ps").components["ch1.read"].readings()["values"]["regulation"] == "CP"
    q(s, "VOLT 4,(@1)")  # 13.3 W: inside the rating again
    assert numbers(q(s, "MEAS:VOLT? (@1)"))[0] == pytest.approx(4.0, rel=2e-3)
    assert s.instrument("ps").components["ch1.read"].readings()["values"]["regulation"] == "CV"


def test_a_negative_output_regulates(open_):
    s = open_(wires=CH3)
    q(s, "VOLT -5,(@3)")
    q(s, "OUTP ON,(@3)")
    relay(s)
    assert numbers(q(s, "MEAS:VOLT? (@3)"))[0] == pytest.approx(-5.0, rel=1e-3)
    assert numbers(q(s, "MEAS:CURR? (@3)"))[0] == pytest.approx(0.5, rel=1e-3)  # reported as magnitude


# ---- protection --------------------------------------------------------------------------------------


def test_ovp_trips_and_clear_restores_the_output(open_):
    s = open_()
    q(s, "VOLT 5,(@1)")
    q(s, "VOLT:PROT 4,(@1)")
    q(s, "OUTP ON,(@1)")
    relay(s)
    assert q(s, "OUTP:PROT:TRIP? (@1)") == "1"
    assert q(s, "OUTP:PROT:KIND? (@1)") == '"OVP"'
    assert q(s, "OUTP? (@1)") == "0"  # the fault switched the output off
    q(s, "OUTP ON,(@1)")  # enabling while the fault stands is refused with the series' error 729
    assert q(s, ":SYST:ERR?").startswith("729")
    q(s, "VOLT:PROT 6,(@1)")
    q(s, "OUTP:PROT:CLE (@1)")
    assert q(s, "OUTP? (@1)") == "1"  # the pre-fault output state is restored
    relay(s)
    assert numbers(q(s, "MEAS:VOLT? (@1)"))[0] == pytest.approx(5.0, rel=1e-3)


def test_ocp_trips_when_the_current_limit_is_crossed(open_):
    s = open_(wires=CH1, r=2.0)
    q(s, "VOLT 5,(@1)")
    q(s, "CURR 1.0,(@1)")
    q(s, "CURR:PROT 0.5,(@1)")
    q(s, "OUTP ON,(@1)")
    relay(s)
    assert q(s, "OUTP:PROT:TRIP? (@1)") == "1"
    assert q(s, "OUTP:PROT:KIND? (@1)") == '"OCP"'
    assert numbers(q(s, "MEAS:CURR? (@1)"))[0] == pytest.approx(0.0, abs=1e-9)


# ---- the ±25 V combination ---------------------------------------------------------------------------


def test_track_mirrors_the_two_voltages(open_):
    s = open_()
    q(s, "VOLT 5,(@2)")
    q(s, "OUTP:TRAC ON")
    assert q(s, "OUTP:PAIR?") == "TRAC"
    assert numbers(q(s, "VOLT? (@3)"))[0] == pytest.approx(-5.0)
    q(s, "VOLT 3,(@2)")
    assert numbers(q(s, "VOLT? (@3)"))[0] == pytest.approx(-3.0)
    q(s, "VOLT -2,(@3)")  # a change on the negative output mirrors back
    assert numbers(q(s, "VOLT? (@2)"))[0] == pytest.approx(2.0)
    q(s, "OUTP:TRAC OFF")
    assert q(s, "OUTP:PAIR?") == "OFF"


def test_the_combination_needs_the_outputs_off(open_):
    s = open_()
    q(s, "OUTP ON,(@2)")
    q(s, "OUTP:PAIR SER")
    assert q(s, ":SYST:ERR?").startswith("-221")
    assert q(s, "OUTP:PAIR?") == "OFF"


def test_parallel_outputs_share_the_load(open_):
    s = open_(wires=PARALLEL, r=2.0)
    q(s, "OUTP:PAIR PAR")
    q(s, "VOLT 5,(@2)")
    q(s, "CURR 1.03,(@2,3)")
    q(s, "OUTP ON,(@2,3)")
    relay(s)
    # Each output is current-limited at 1.03 A into 2 Ω: 2.06 A total, 4.12 V.
    assert numbers(q(s, "MEAS:VOLT? (@2,3)")) == pytest.approx([4.12, -4.12], rel=1e-3)
    assert numbers(q(s, "MEAS:CURR? (@2,3)")) == pytest.approx([1.03, 1.03], rel=1e-6)


def test_series_outputs_add_their_voltages(open_):
    s = open_(wires=SERIES, r=100.0)
    q(s, "OUTP:PAIR SER")
    q(s, "VOLT 5,(@2)")
    q(s, "OUTP ON,(@2,3)")
    relay(s)
    # The two positives are the outer rails: 5 V and −5 V give a 10 V load; 0.1 A flows.
    assert numbers(q(s, "MEAS:VOLT? (@2,3)")) == pytest.approx([5.0, -5.0], rel=1e-3)
    assert numbers(q(s, "MEAS:CURR? (@2,3)")) == pytest.approx([0.1, 0.1], rel=1e-3)


def test_the_panel_readings_keep_their_keys(open_):
    s = open_()
    q(s, "VOLT 2,(@1)")
    q(s, "OUTP ON,(@1)")
    relay(s)
    q(s, "OUTP? (@1)")  # lets the due relay operation run before the panel reads
    values = s.instrument("ps").components["ch1.read"].readings()["values"]
    assert set(values) == {"voltage", "current", "power", "regulation"}
    assert values["voltage"] == pytest.approx(2.0, rel=1e-3)


# ---- the transient system (#541) --------------------------------------------------------------------


def test_the_transient_step_writes_the_triggered_levels():
    """supply_transient on its own: STEP writes the triggered level into the bound setting on a
    trigger; FIXed leaves it; a trigger while not armed reports the configured error."""
    from simubench.engine.session import Session
    from simubench.library.components.power import SupplyTransient

    lib = DefinitionLibrary()
    bench_ = BenchDefinition.model_validate(
        {"schema": "simubench.bench/2", "name": "t", "instruments": {"psu": {"model": "keysight-n6705c"}}}
    )
    s = Session(bench_, lambda model: lib.get(model), clock="command", seed=1, idle="off")
    try:
        inst = s.instruments["psu"]
        tran = inst.components["ch2.tran"]
        assert isinstance(tran, SupplyTransient)
        inst.set_state("ch2.v_triggered", 7.0)
        inst.set_state("ch2.i_triggered", 0.3)
        inst.set_state("ch2.v_mode", "STEP")
        tran.overrides["ready_delay"] = (
            0.0  # ready at once (the N6705C's 5 ms is tested with the model, #548)
        )
        tran.op_initiate()
        assert tran.status_flags() == {"WTG_TRAN": True, "TRAN_ACTIVE": True}
        tran.bus_trigger()
        assert inst.get_state("ch2.voltage") == 7.0
        assert inst.get_state("ch2.current") == 0.0  # FIXed: untouched
        assert not tran.armed
        tran.overrides["trigger_ignored_error"] = -211
        with pytest.raises(Exception) as e:
            tran.op_trigger()
        assert getattr(e.value, "code", None) == -211
    finally:
        s.close()
