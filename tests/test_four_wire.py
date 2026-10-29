"""Four-wire measurement and lead resistance (issue 91, metrology-authoring.md 6).

The acceptance bench: the SMU forces 1 A through two 0.5 Ω leads into a 10 Ω resistor, the sense
terminals on the resistor body. Manual calculation (the solver stamps 1e-12 S of GMIN from every
node to ground; at ampere-level currents its effect is below 1e-11 relative, far under the
assertion tolerances):

- two-wire: the loop is 0.5 + 10 + 0.5 = 11 Ω, so the terminals see 11 V and the meter reads 11 V;
  the internal LO-to-ground bond (1e-6 Ω) lifts both terminals by 1 µV, invisible here.
- four-wire: the voltage loop holds V(sense_hi) − V(sense_lo) = the setpoint, so the meter reads
  the resistor's 10 V while the force terminals sit 1 V higher (the leads drop 0.5 V each).
"""

from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.engine.validate import validate_data
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS = ROOT / "validation" / "instruments"
LIB = DefinitionLibrary([DEFINITIONS])

BENCH = {
    "schema": "simubench.bench/2",
    "name": "four-wire",
    "run": {"clock": "command"},
}


def smu_bench(*, sense: bool = True, floating: bool = False, leads: bool = True, state: dict | None = None):
    """The SMU into a 10 Ω resistor: 0.5 Ω leads on both force wires, sense on the resistor body."""
    wires = [
        {
            "from": "smu.HI",
            "to": "r1.A",
            **({"accessories": [{"type": "lead", "params": {"r": 0.5}}]} if leads else {}),
        },
        {
            "from": "r1.B",
            "to": "smu.LO",
            **({"accessories": [{"type": "lead", "params": {"r": 0.5}}]} if leads else {}),
        },
    ]
    if sense:
        wires += [{"from": "smu.SENSE_HI", "to": "r1.A"}, {"from": "smu.SENSE_LO", "to": "r1.B"}]
    if floating:
        state = {"terminal.floating": True, **(state or {})}
    return {
        **BENCH,
        "instruments": {"smu": {"model": "sb-smu-1", **({"state": state} if state else {})}},
        "parts": {"r1": {"type": "resistor", "dut": True, "params": {"r": 10.0}}},
        "wires": wires,
    }


@pytest.fixture
def open_():
    sessions = []

    def open(data, **kw):
        s = open_bench(BenchDefinition.model_validate(data), definition_dirs=[DEFINITIONS], **kw)
        sessions.append(s)
        return s

    yield open
    for s in sessions:
        s.close()


def q(s, cmd: str = ":MEAS?") -> str:
    s.write("smu", (cmd + "\n").encode())
    out = s.read("smu")
    return out.decode().strip() if out else ""


def row(s, cmd: str = ":MEAS?") -> tuple[float, ...]:
    return tuple(float(x) for x in q(s, cmd).split(","))


def force_hi(s) -> float:
    core = s.instruments["smu"].components["core"]
    return core.pin_signal("hi").mean()


# ---- the acceptance readings: 11 V two-wire, 10 V four-wire ----------------------------------------


def test_two_wire_reading_includes_the_leads(open_):
    s = open_(
        smu_bench(state={"source.function": "CURRent", "source.current.level": 1.0, "output.enabled": True})
    )
    # Manual: 1 A x (0.5 + 10 + 0.5) = 11 V at the terminals; the meter is across HI and ground.
    assert row(s) == pytest.approx((11.0, 1.0, 11.0, 5.24e-2, 0.0), abs=5e-5)
    assert q(s, ":OUTP:REG?") == "CC"
    assert force_hi(s) == pytest.approx(11.0, abs=5e-5)  # 11 V plus the 1 µV bond lift


def test_four_wire_reading_measures_the_resistor(open_):
    s = open_(
        smu_bench(
            state={
                "source.function": "CURRent",
                "source.current.level": 1.0,
                "sense.remote": True,
                "output.enabled": True,
            }
        )
    )
    # Manual: the loop holds the resistor at 1 A x 10 = 10 V; each lead drops 0.5 V, so the
    # resistor body sits 0.5 V above the bonded LO while the meter reads only the sense pair.
    assert row(s) == pytest.approx((10.0, 1.0, 10.0, 5.24e-2, 0.0), abs=5e-5)
    assert q(s, ":OUTP:REG?") == "CC"
    assert force_hi(s) == pytest.approx(11.0, abs=5e-5)  # 0.5 (low lead) + 10 + 0.5 (high lead)


def test_remote_sense_switches_at_runtime(open_):
    s = open_(
        smu_bench(state={"source.function": "CURRent", "source.current.level": 1.0, "output.enabled": True})
    )
    assert row(s)[0] == pytest.approx(11.0, abs=5e-5)
    assert q(s, ":SENSe:REM?") == "0"
    q(s, ":SENSe:REM ON")
    assert q(s, ":SENSe:REM?") == "1"
    assert row(s)[0] == pytest.approx(10.0, abs=5e-5)
    q(s, ":SENSe:REM OFF")
    assert row(s)[0] == pytest.approx(11.0, abs=5e-5)


def test_voltage_loop_regulates_the_sense_pair(open_):
    s = open_(
        smu_bench(
            state={
                "source.voltage.level": 10.0,
                "compliance.current.limit": 1.05,
                "sense.remote": True,
                "output.enabled": True,
            }
        )
    )
    # Manual: the loop holds the resistor at 10 V, so 1 A flows; the force terminal carries the
    # whole loop: 0.5 (low lead) + 10 + 0.5 (high lead), plus the 1 µV bond lift.
    assert row(s) == pytest.approx((10.0, 1.0, 10.0, 5.24e-2, 0.0), abs=5e-5)
    assert q(s, ":OUTP:REG?") == "CV"
    assert force_hi(s) == pytest.approx(11.0, abs=5e-5)


# ---- the limit is judged on the sense voltage (D34) -------------------------------------------------


def test_limit_switch_judges_the_sense_voltage(open_):
    state = {
        "source.function": "CURRent",
        "source.current.level": 1.0,
        "compliance.voltage.limit": 10.5,
        "compliance.current.limit": 1.05,
        "output.enabled": True,
    }
    four = open_(smu_bench(state={"sense.remote": True, **state}))
    # 10 V at the sense pair is inside the 10.5 V limit: still a current source.
    assert row(four)[0] == pytest.approx(10.0, abs=5e-5)
    assert row(four)[4] == 0.0 and q(four, ":OUTP:REG?") == "CC"

    two = open_(smu_bench(sense=False, state=state))
    # The terminals sit at 11 V: the limit takes over and clamps them at 10.5 V.
    # Manual: 10.5 V across 11.000001 Ω = 0.9545455 A.
    assert row(two) == pytest.approx((10.5, 10.5 / 11.0, 11.0, 5.24e-2, 8.0), abs=5e-5)
    assert q(two, ":OUTP:REG?") == "CV"


def test_limit_trips_on_the_sense_voltage_below_the_force_voltage(open_):
    state = {
        "source.function": "CURRent",
        "source.current.level": 1.0,
        "compliance.voltage.limit": 9.5,
        "compliance.current.limit": 1.05,
        "sense.remote": True,
        "output.enabled": True,
    }
    s = open_(smu_bench(state=state))
    # The sense pair would reach 10 V; the limit clamps it to 9.5 V. Manual: 0.95 A through the
    # loop, the resistor at 9.5 V, each lead dropping 0.475 V: the force terminal at 10.45 V.
    assert row(s) == pytest.approx((9.5, 0.95, 10.0, 5.24e-2, 8.0), abs=5e-5)
    assert q(s, ":OUTP:REG?") == "CV"
    assert force_hi(s) == pytest.approx(10.45, abs=5e-5)


# ---- floating LO ------------------------------------------------------------------------------------


def test_floating_lo_reads_across_its_own_terminals(open_):
    state = {"source.function": "CURRent", "source.current.level": 1.0, "output.enabled": True}
    s = open_(smu_bench(leads=False, floating=True, state=state))
    # Manual: 1 A x 10 = 10 V across the resistor; nothing else references ground, so the solver
    # splits it symmetrically (GMIN): +5 V and -5 V, and the meter reads their difference.
    assert row(s) == pytest.approx((10.0, 1.0, 10.0, 5.24e-2, 0.0), abs=5e-5)
    core = s.instruments["smu"].components["core"]
    assert core.pin_signal("hi").mean() == pytest.approx(5.0, abs=5e-5)
    assert core.pin_signal("lo").mean() == pytest.approx(-5.0, abs=5e-5)


def test_ground_lo_bonds_the_low_wire(open_):
    s = open_(
        smu_bench(
            leads=False,
            state={"source.function": "CURRent", "source.current.level": 1.0, "output.enabled": True},
        )
    )
    # With floating: false the LO net is bonded to ground (1e-6 ohm): the whole 10 V appears
    # against ground, the bond lifts it by 1 µV.
    assert row(s)[0] == pytest.approx(10.0, abs=5e-5)
    core = s.instruments["smu"].components["core"]
    assert core.pin_signal("lo").mean() == pytest.approx(0.0, abs=5e-5)


# ---- a sense terminal left unconnected ---------------------------------------------------------------


def test_unconnected_sense_errors_instead_of_reading(open_):
    s = open_(
        smu_bench(
            sense=False,
            state={
                "source.function": "CURRent",
                "source.current.level": 1.0,
                "sense.remote": True,
                "output.enabled": True,
            },
        )
    )
    assert q(s, ":MEAS?") == ""  # no reply
    assert (
        q(s, ":SYST:ERR?")
        == '-221,"Settings conflict; remote sense is on but a sense terminal is not connected"'
    )
    # The loop fell back to the force terminals: the bench still solves, the output still drives.
    assert q(s, ":OUTP:REG?") == "CC"
    assert force_hi(s) == pytest.approx(11.0, abs=5e-5)
    q(s, ":SENSe:REM OFF")
    assert row(s)[0] == pytest.approx(11.0, abs=5e-5)  # remote sense off: readings work again
    assert q(s, ":SYST:ERR?").startswith("0,")


def test_unconnected_sense_does_not_disturb_the_panel(open_):
    s = open_(
        smu_bench(
            sense=False,
            state={
                "source.function": "CURRent",
                "source.current.level": 1.0,
                "sense.remote": True,
                "output.enabled": True,
            },
        )
    )
    values = s.instruments["smu"].components["core"].readings()["values"]
    assert values["voltage"] == pytest.approx(11.0, abs=5e-5)  # the fallback loop, no exception


# ---- validation diagnostics -------------------------------------------------------------------------


def test_validator_warns_when_remote_sense_is_left_unconnected():
    _, report = validate_data(smu_bench(sense=False, state={"sense.remote": True}), LIB)
    codes = [(d.code, d.pointer, d.subject) for d in report.diagnostics]
    assert codes == [("W-SENSE-UNCONNECTED", "/instruments/smu/state/sense.remote", "smu")]


def test_validator_passes_a_wired_four_wire_bench():
    _, report = validate_data(smu_bench(state={"sense.remote": True}), LIB)
    # Only the shared-port notes: the sense wires land on the same DUT terminals as the force
    # wires, the normal Kelvin connection.
    assert [(d.code, d.pointer) for d in report.diagnostics] == [
        ("W-PORT-SHARED", "/wires/2/to"),
        ("W-PORT-SHARED", "/wires/3/to"),
    ]


def test_validator_rejects_sense_terminals_on_one_net():
    raw = smu_bench(state={"sense.remote": True})
    raw["wires"] = [
        {"from": "smu.HI", "to": "r1.A"},
        {"from": "r1.B", "to": "smu.LO"},
        {"from": "smu.SENSE_HI", "to": "r1.A"},
        {"from": "smu.SENSE_LO", "to": "r1.A"},
    ]
    _, report = validate_data(raw, LIB)
    assert [(d.code, d.severity) for d in report.diagnostics] == [
        ("W-PORT-SHARED", "warning"),
        ("W-PORT-SHARED", "warning"),
        ("E-SENSE-SHORT", "error"),
    ]


# ---- the lead is a real series element ---------------------------------------------------------------


def test_a_lead_is_not_looked_through_by_the_termination_check():
    """A 50 Ω source terminated through a cable is terminated; through a lead it is not: the lead
    is a real resistance (``role`` is None, so the net check does not merge its two sides)."""

    def gen_wire(acc: dict) -> dict:
        return {
            "schema": "simubench.bench/2",
            "name": "t",
            "instruments": {"awg": {"model": "sb-awg-2"}, "sa": {"model": "sb-sa-1"}},
            "wires": [{"from": "awg.OUT1", "to": "sa.RF_IN", "accessories": [acc]}],
            "run": {"clock": "command"},
        }

    _, coax = validate_data(gen_wire({"type": "coax_cable", "params": {"r": 0.05}}), LIB)
    assert coax.diagnostics == []  # the 50 Ω input terminates through a series accessory
    _, lead = validate_data(gen_wire({"type": "lead", "params": {"r": 0.5}}), LIB)
    assert [(d.code, d.pointer) for d in lead.diagnostics] == [
        ("W-IMPEDANCE-MISMATCH", "/instruments/awg/state/ch1.load")
    ]  # behind a real lead the source's net has no 50 Ω termination


def test_a_zero_ohm_lead_still_solves(open_):
    raw = smu_bench(state={"source.function": "CURRent", "source.current.level": 1.0, "output.enabled": True})
    for w in raw["wires"][:2]:
        w["accessories"] = [{"type": "lead", "params": {"r": 0.0}}]
    s = open_(raw)
    assert row(s)[0] == pytest.approx(10.0, abs=5e-5)  # clamped to 1e-6 ohm: no drop, no crash
