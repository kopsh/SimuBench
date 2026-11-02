"""Electronic load (C1 #186): the CC knee, CV regulation, CR, CP (D52), Von, the CC slew rate and
the OVP / OCP / OPP protection.

The validation definition ``sb-el-1`` is the load built from its blocks: the input stage stamps
the sink, the power stage holds the knee and current/power capability, the mode loop picks CC / CV /
CR / CP, the meter reports the ``load.*`` readings and the protection latches a trip.
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


def bench(source_v: float = 5.0, source_r: float = 1e-6, *, state: dict | None = None) -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "eload",
            "instruments": {"el": {"model": "sb-el-1", **({"state": state} if state else {})}},
            "parts": {"src": {"type": "dc_supply", "params": {"v": source_v, "r": source_r}}},
            "wires": [{"from": "src.OUT", "to": "el.IN"}],
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
    s.write("el", (cmd + "\n").encode())
    out = s.read("el")
    return out.decode().strip() if out else ""


def number(s, cmd: str) -> float:
    return float(q(s, cmd).lstrip("+"))


# ---- CC and CR (the behaviour the gold standard pins) ----------------------------------------------


def test_the_definition_is_clean():
    assert validate_definition(DefinitionLibrary([DEFS]).get("sb-el-1")).diagnostics == []


def test_cc_sinks_the_set_current_and_the_knee_limits_it(open_):
    s = open_()
    q(s, ":SOUR:FUNC CC;:SOUR:CURR 0.002;:INP ON")
    assert number(s, ":MEAS:VOLT?") == pytest.approx(5.0, abs=1e-6)
    assert number(s, ":MEAS:CURR?") == pytest.approx(2e-3, rel=1e-6)

    # Below the knee voltage (I × r_min) the minimum resistance holds the sink instead.
    s = open_(source_v=5e-5)
    q(s, ":SOUR:FUNC CC;:SOUR:CURR 0.002;:INP ON")
    assert number(s, ":MEAS:VOLT?") == pytest.approx(5e-5, rel=1e-3)
    assert number(s, ":MEAS:CURR?") == pytest.approx(1.362e-3, rel=1e-3)
    assert number(s, ":MEAS:CURR?") < 2e-3


def test_cr_behaves_as_the_set_resistance(open_):
    s = open_()
    q(s, ":SOUR:FUNC CR;:SOUR:RES 1000;:INP ON")
    assert number(s, ":MEAS:CURR?") == pytest.approx(5e-3, rel=1e-6)
    assert number(s, ":MEAS:POW?") == pytest.approx(0.025, rel=1e-6)


# ---- CV and CP ---------------------------------------------------------------------------------------


def test_cv_holds_the_input_and_opens_below_its_setpoint(open_):
    s = open_(source_v=10.0, source_r=10.0)
    q(s, ":SOUR:FUNC CV;:SOUR:VOLT 5;:INP ON")
    assert number(s, ":MEAS:VOLT?") == pytest.approx(5.0, abs=1e-5)
    assert number(s, ":MEAS:CURR?") == pytest.approx(0.5, rel=1e-4)
    assert number(s, ":MEAS:POW?") == pytest.approx(2.5, rel=1e-4)

    # A source below the setpoint cannot be pulled up: the load is an open circuit.
    s = open_(source_v=3.0, source_r=10.0)
    q(s, ":SOUR:FUNC CV;:SOUR:VOLT 5;:INP ON")
    assert number(s, ":MEAS:VOLT?") == pytest.approx(3.0, rel=1e-6)
    assert number(s, ":MEAS:CURR?") == pytest.approx(0.0, abs=1e-9)


def test_cp_finds_a_self_consistent_power(open_):
    # 10 V behind 1 Ω drawing 20 W: V² − 10V + 20 = 0 has 7.236 V and 2.764 V; the iteration
    # settles on the high-voltage root (D52 records this semantics).
    s = open_(source_v=10.0, source_r=1.0)
    q(s, ":SOUR:FUNC CP;:SOUR:POW 20;:INP ON")
    v, i = number(s, ":MEAS:VOLT?"), number(s, ":MEAS:CURR?")
    assert (v, i) == pytest.approx((7.236068, 2.763932), abs=1e-5)
    assert v * i == pytest.approx(20.0, rel=1e-6)


def _three_readings_agree(s, source_v: float, source_r: float) -> tuple[float, float]:
    v, i, p = number(s, ":MEAS:VOLT?"), number(s, ":MEAS:CURR?"), number(s, ":MEAS:POW?")
    assert v == pytest.approx(source_v - i * source_r, abs=1e-5)  # the source's line
    assert p == pytest.approx(v * i, rel=1e-5, abs=1e-6)
    return v, i


def test_cp_beyond_the_source_reads_one_operating_point_on_the_circuit(open_):
    # 10 V behind 1 Ω gives at most 25 W: 30 W has no solution and the loop does not settle
    # (#258). The readings come from one operating point on the source's line, and repeat.
    s = open_(source_v=10.0, source_r=1.0)
    q(s, ":SOUR:FUNC CP;:SOUR:POW 30;:INP ON")
    first = _three_readings_agree(s, 10.0, 1.0)
    assert _three_readings_agree(s, 10.0, 1.0) == first
    assert first[0] * first[1] <= 25.0 + 1e-6


def test_cp_flapping_at_von_reads_one_operating_point_on_the_circuit(open_):
    s = open_(source_v=10.0, source_r=1.0)
    q(s, ":SOUR:FUNC CP;:SOUR:POW 20;:SOUR:CURR:VON 8;:INP ON")
    first = _three_readings_agree(s, 10.0, 1.0)
    assert _three_readings_agree(s, 10.0, 1.0) == first


def test_an_unsettled_loop_is_a_run_record_event_and_not_regulating(open_):
    s = open_(source_v=10.0, source_r=1.0)
    q(s, ":SOUR:FUNC CP;:SOUR:POW 30;:INP ON")
    number(s, ":MEAS:VOLT?")
    events = [e for e in s.history if e["kind"] == "solver"]
    assert [e["reason"] for e in events] == ["unsettled"]
    assert s.unsettled
    assert s.instruments["el"].components["load"].readings()["values"]["regulating"] is False

    # A setpoint the source can give settles again.
    q(s, ":SOUR:POW 20")
    assert number(s, ":MEAS:POW?") == pytest.approx(20.0, rel=1e-6)
    assert not s.unsettled


def test_the_questionable_condition_register_carries_the_unregulated_bit(open_):
    """#343: a SCPI script sees the unsettled loop — STAT:QUES:COND? bit 10 (UNR) — and the bit
    clears once a reachable setpoint settles."""
    unr = 1 << 10
    s = open_(source_v=10.0, source_r=1.0)
    q(s, ":SOUR:FUNC CP;:SOUR:POW 20;:INP ON")
    assert q(s, ":STAT:QUES:COND?") == "0"
    q(s, ":SOUR:POW 30")  # beyond the 25 W the source can give
    assert int(q(s, ":STAT:QUES:COND?")) & unr
    q(s, ":SOUR:POW 20")
    assert q(s, ":STAT:QUES:COND?") == "0"
    q(s, ":INP OFF")
    assert q(s, ":STAT:QUES:COND?") == "0"  # an open input regulates nothing: no UNR


def test_the_unregulated_bit_follows_the_knee_and_a_trip_sets_its_own_bit(open_):
    s = open_(source_v=5e-5)  # below the CC knee: the minimum resistance holds the sink
    q(s, ":SOUR:FUNC CC;:SOUR:CURR 0.002;:INP ON")
    assert int(q(s, ":STAT:QUES:COND?")) == 1 << 10
    s = open_(source_r=0.1)
    q(s, ":SOUR:FUNC CC;:SOUR:CURR 2;:SOUR:OCP 1.5;:INP ON")
    assert int(q(s, ":STAT:QUES:COND?")) == 1 << 1  # OC; the tripped input is not unregulated
    q(s, ":SOUR:CURR 0.5;:SYST:PROT:CLE")
    assert q(s, ":STAT:QUES:COND?") == "0"


# ---- Von and the CC slew rate ------------------------------------------------------------------------


def test_von_stops_the_sink_below_the_starting_voltage(open_):
    # Sinking 1 A leaves 4.9 V: above Von 4 V, it conducts; below Von 4.95 V (source 4 V), it does not.
    s = open_(source_r=0.1)
    q(s, ":SOUR:FUNC CC;:SOUR:CURR 1;:SOUR:CURR:VON 4;:INP ON")
    assert number(s, ":MEAS:VOLT?") == pytest.approx(4.9, rel=1e-6)
    assert number(s, ":MEAS:CURR?") == pytest.approx(1.0, rel=1e-6)

    s = open_(source_v=4.0, source_r=0.1)
    q(s, ":SOUR:FUNC CC;:SOUR:CURR 1;:SOUR:CURR:VON 4.95;:INP ON")
    assert number(s, ":MEAS:VOLT?") == pytest.approx(4.0, rel=1e-6)
    assert number(s, ":MEAS:CURR?") == pytest.approx(0.0, abs=1e-9)


def test_the_cc_slew_rate_ramps_the_current_in_virtual_time(open_):
    s = open_(source_r=0.1)
    q(s, ":SOUR:FUNC CC;:SOUR:CURR 0;:SOUR:CURR:SLEW 0.5;:INP ON")
    q(s, ":SOUR:CURR 2")
    assert number(s, ":MEAS:CURR?") == pytest.approx(0.0, abs=1e-9)  # the ramp starts now
    s.advance(2e-6)  # 0.5 A/µs × 2 µs = 1 A
    assert number(s, ":MEAS:CURR?") == pytest.approx(1.0, rel=1e-4)
    assert number(s, ":MEAS:VOLT?") == pytest.approx(4.9, rel=1e-4)
    s.advance(2e-6)  # the target is reached
    assert number(s, ":MEAS:CURR?") == pytest.approx(2.0, rel=1e-4)


# ---- protection --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trip", "kind", "value"),
    [
        (":SOUR:OVP 4", "OVP", 5.0),
        (":SOUR:OCP 1.5", "OCP", 2.0),
        (":SOUR:OPP 5", "OPP", 9.6),
    ],
)
def test_a_threshold_trips_the_input(open_, trip, kind, value):
    s = open_(source_r=0.1)
    q(s, f":SOUR:FUNC CC;:SOUR:CURR 2;{trip};:INP ON")
    assert q(s, ":SYST:PROT:TRIP?") == "1"
    assert q(s, ":SYST:PROT:KIND?") == f'"{kind}"'
    assert number(s, ":MEAS:CURR?") == pytest.approx(0.0, abs=1e-9)  # the input stopped conducting
    assert number(s, ":MEAS:VOLT?") == pytest.approx(5.0, rel=1e-6)


def test_clearing_the_protection_resumes_the_loaded_value(open_):
    s = open_(source_r=0.1)
    q(s, ":SOUR:FUNC CC;:SOUR:CURR 2;:SOUR:OCP 1.5;:INP ON")
    assert q(s, ":SYST:PROT:TRIP?") == "1"
    q(s, ":SOUR:CURR 0.5")  # below the threshold once cleared
    q(s, ":SYST:PROT:CLE")
    assert q(s, ":SYST:PROT:TRIP?") == "0"
    assert q(s, ":SYST:PROT:KIND?") == '""'
    assert number(s, ":MEAS:CURR?") == pytest.approx(0.5, rel=1e-4)


def test_the_panel_readings_keep_their_keys(open_):
    s = open_(source_r=1e-6)
    q(s, ":SOUR:FUNC CC;:SOUR:CURR 0.001;:INP ON")
    values = s.instrument("el").components["load"].readings()["values"]
    assert set(values) == {"voltage", "current", "power", "regulating"}
    assert values["voltage"] == pytest.approx(5.0, abs=1e-6)
    assert values["current"] == pytest.approx(1e-3, rel=1e-6)
    assert values["regulating"] is True
