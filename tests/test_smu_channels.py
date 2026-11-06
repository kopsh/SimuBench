"""A multi-channel SMU from generic components (#382): each channel its own SMU chain.

``sb-smu-2`` puts the whole chain (source DAC, control loop, output stage, meter, trigger model,
reading buffer) in ``channel_components``; component references resolve within the channel first
(``Component.peer``). Two resistors on the two channels give closed forms: I = V / R per channel, a
channel list answers in its own order, and ``*TRG`` triggers every channel waiting for a bus trigger.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simubench.api import open_bench
from simubench.engine.validate_definition import validate_definition_data
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"


def _bench() -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "two_channel_smu",
            "instruments": {"smu": {"model": "sb-smu-2"}},
            "parts": {
                "r1": {"type": "resistor", "params": {"r": 1000.0}},
                "r2": {"type": "resistor", "params": {"r": 4000.0}},
            },
            "wires": [
                {"from": "smu.HI1", "to": "r1.A"},
                {"from": "r1.B", "to": "smu.LO1"},
                {"from": "smu.HI2", "to": "r2.A"},
                {"from": "r2.B", "to": "smu.LO2"},
            ],
        }
    )


@pytest.fixture
def smu():
    session = open_bench(_bench(), definition_dirs=[DEFS])

    def q(text: str) -> str:
        session.write("smu", (text + "\n").encode())
        reply = session.read("smu")
        assert reply is not None, text
        return reply.decode().strip()

    def w(text: str) -> None:
        session.write("smu", (text + "\n").encode())

    w(":SOUR1:VOLT 1;:SOUR2:VOLT 2;:SENS1:CURR:PROT 0.01;:SENS2:CURR:PROT 0.01;:OUTP1 ON;:OUTP2 ON")
    yield q, w
    session.close()


def _values(text: str) -> list[float]:
    return [float(v) for v in text.split(",")]


def test_the_two_channel_definition_validates():
    definition, report = validate_definition_data(yaml.safe_load((DEFS / "sb-smu-2.yaml").read_text("utf-8")))
    assert definition is not None
    assert report.diagnostics == []


def test_the_channel_query_flow(smu):
    """Each channel sources and measures its own circuit, and a channel list answers in the
    list's own order (#382)."""
    # -- each channel sources and measures its own circuit (was test_each_channel_sources_and_measures_its_own_circuit)
    q, _ = smu
    assert _values(q(":MEAS:CURR? (@1,2)")) == pytest.approx([1e-3, 0.5e-3], rel=1e-6)
    assert float(q(":MEAS:VOLT? (@2)")) == pytest.approx(2.0, rel=1e-6)
    # the omitted list is channel 1, and a header suffix-less :SOUR is channel 1 too
    assert float(q(":MEAS:CURR?")) == pytest.approx(1e-3, rel=1e-6)
    # -- a channel list answers in its own order (was test_a_channel_list_answers_in_its_own_order)
    assert _values(q(":MEAS:CURR? (@2,1)")) == pytest.approx([0.5e-3, 1e-3], rel=1e-6)


def test_one_channels_state_leaves_the_other_alone(smu):
    """Per-channel independence: OFF on one output leaves the other running, and the compliance
    trips on its own channel only."""
    q, w = smu
    # -- one channel output off leaves the other running (was test_one_channel_output_off_leaves_the_other_running)
    w(":OUTP1 OFF")
    assert _values(q(":MEAS:CURR? (@1,2)")) == pytest.approx([0.0, 0.5e-3], abs=1e-12)
    # -- the compliance trips on its own channel only (was test_the_compliance_trips_on_its_own_channel_only)
    w(":SENS2:CURR:PROT 1e-4")  # 2 V into 4 kOhm wants 0.5 mA
    assert float(q(":MEAS:CURR? (@2)")) == pytest.approx(1e-4, rel=1e-6)
    assert q(":SENS2:CURR:PROT:TRIP?") == "1"
    assert q(":SENS1:CURR:PROT:TRIP?") == "0"  # channel 1's output is off: it never limits


def test_the_bus_trigger_flow(smu):
    """One *TRG bus trigger: every channel waiting for a bus trigger takes it, the idle ones are
    ignored without an error, and with nothing waiting the trigger is ignored (-211)."""
    q, w = smu
    # -- *TRG triggers every channel waiting for a bus trigger (was test_trg_triggers_every_channel_waiting_for_a_bus_trigger)
    w(":TRIG1:SOUR BUS;:TRIG2:SOUR BUS")
    w(":INIT (@1,2)")
    w("*TRG")
    assert _values(q(":FETC:ARR:CURR? (@1,2)")) == pytest.approx([1e-3, 0.5e-3], rel=1e-6)
    assert (q(":TRAC1:POIN:ACT?"), q(":TRAC2:POIN:ACT?")) == ("1", "1")
    assert q(":SYST:ERR?").startswith("0,")
    # -- *TRG reaches the waiting channel and ignores the idle one
    # (was test_trg_reaches_the_waiting_channel_and_ignores_the_idle_one)
    w(":TRIG2:SOUR BUS")
    w(":INIT (@2)")
    w("*TRG")  # channel 1 is idle: no error while channel 2 takes the trigger
    assert float(q(":FETC:ARR:CURR? (@2)")) == pytest.approx(0.5e-3, rel=1e-6)
    assert q(":SYST:ERR?").startswith("0,")
    # -- *TRG with no channel waiting is ignored (was test_trg_with_no_channel_waiting_is_ignored)
    w("*TRG")
    assert q(":SYST:ERR?").startswith("-211,")


def test_the_abort_flow(smu):
    """#396: :ABOR over a channel list idles exactly those channels - both, so :INIT again is
    accepted (no -213) and *TRG then triggers both; one, and the other keeps waiting."""
    q, w = smu
    # -- :ABOR (@1,2) returns both channels to idle (was test_abort_over_a_channel_list_returns_both_channels_to_idle)
    w(":TRIG1:SOUR BUS;:TRIG2:SOUR BUS")
    w(":INIT (@1,2)")
    w(":ABOR (@1,2)")
    assert q(":SYST:ERR?").startswith("0,")
    w(":INIT (@1,2)")
    assert q(":SYST:ERR?").startswith("0,")
    w("*TRG")
    assert _values(q(":FETC:ARR:CURR? (@1,2)")) == pytest.approx([1e-3, 0.5e-3], rel=1e-6)
    # -- aborting one channel leaves the other waiting (was test_aborting_one_channel_leaves_the_other_waiting)
    w(":INIT (@1,2)")
    w(":ABOR (@1)")
    w(":INIT (@2)")  # channel 2 is still waiting for its trigger: init ignored
    assert q(":SYST:ERR?").startswith("-213,")
    w(":INIT (@1)")  # channel 1 is idle again
    assert q(":SYST:ERR?").startswith("0,")


def test_read_over_a_channel_list_answers_in_list_order(smu):
    """#396: :READ:ARR:CURR? (@2,1) with the immediate (AINT) trigger: each channel's own reading,
    in the list's order."""
    q, _ = smu
    assert _values(q(":READ:ARR:CURR? (@2,1)")) == pytest.approx([0.5e-3, 1e-3], rel=1e-6)
    assert q(":SYST:ERR?").startswith("0,")
