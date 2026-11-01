"""Data acquisition (E2 #191): the multiplexer card's relays and analog bus, the scan table and
the DAQ class default panel.

The validation definition ``sb-daq-1`` is a 34970A mainframe with a 34901A in slot 1 (channels
101-120, the channel-number domain of #207) and the internal DMM built from E1's measurement chain.
Two reference voltages sit on channels 101 and 102; closing a relay puts the internal DMM's input
resistance (10 MΩ) across that channel, so a source with a series resistance reads the divided
value — the relay closure is a real change in the bench circuit.
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


def bench(*, state: dict | None = None, source_r: float = 1e-6) -> BenchDefinition:
    """Channels 101 and 102 on 5 V and 3 V references; every L terminal on a 0 V reference."""
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "daq",
            "instruments": {"daq": {"model": "sb-daq-1", **({"state": state} if state else {})}},
            "parts": {
                "v1": {"type": "dc_supply", "params": {"v": 5.0, "r": source_r}},
                "v2": {"type": "dc_supply", "params": {"v": 3.0, "r": source_r}},
                "v0": {"type": "dc_supply", "params": {"v": 0.0, "r": 1e-6}},
            },
            "wires": [
                {"from": "v1.OUT", "to": "daq.H101"},
                {"from": "v0.OUT", "to": "daq.L101"},
                {"from": "v2.OUT", "to": "daq.H102"},
                {"from": "v0.OUT", "to": "daq.L102"},
            ],
            "run": {"clock": "command", "seed": 1},
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
    s.write("daq", (cmd + "\n").encode())
    out = s.read("daq")
    return out.decode().strip() if out else ""


def numbers(reply: str) -> list[float]:
    return [float(x) for x in reply.split(",")]


# ---- the definition and the channel number domain ---------------------------------------------------


def test_the_definition_is_clean():
    definition = DefinitionLibrary([DEFS]).get("sb-daq-1")
    assert validate_definition(definition).diagnostics == []


def test_a_channel_of_another_slot_or_a_channel_index_is_unknown(open_):
    s = open_()
    q(s, "ROUT:CLOS (@1)")  # the internal index, not the instrument's number (101)
    assert q(s, ":SYST:ERR?").startswith("-114")
    q(s, "ROUT:CLOS (@121)")  # beyond the 34901A's 20 channels
    assert q(s, ":SYST:ERR?").startswith("-114")


# ---- relays: closure, the closure limit and the bus ------------------------------------------------


def test_a_closed_channel_reaches_the_internal_dmm(open_):
    s = open_()
    q(s, "ROUT:CLOS (@101)")
    assert numbers(q(s, ":MEAS?"))[0] == pytest.approx(5.0, abs=1e-4)
    assert q(s, "ROUT:CLOS? (@101,102)") == "1,0"
    q(s, "ROUT:OPEN (@101)")
    assert q(s, "ROUT:CLOS? (@101,102)") == "0,0"


def test_the_card_closes_one_channel_at_a_time(open_):
    s = open_()
    q(s, "ROUT:CLOS (@101)")
    q(s, "ROUT:CLOS (@102)")
    assert q(s, ":SYST:ERR?").startswith("-221")  # the break-before-make closure limit
    # The refused command left the first channel closed, and opening it lets the next one close.
    assert q(s, "ROUT:CLOS? (@101,102)") == "1,0"
    q(s, "ROUT:OPEN (@101)")
    q(s, "ROUT:CLOS (@102)")
    assert numbers(q(s, ":MEAS?"))[0] == pytest.approx(3.0, abs=1e-4)


def test_closing_a_channel_loads_the_dut(open_):
    # 5 V behind 1 MΩ: open, nothing draws current; closed, the DMM's 10 MΩ divides it down.
    s = open_(source_r=1.0e6)
    assert abs(numbers(q(s, ":MEAS?"))[0]) < 1e-3  # bus floating, nothing connected
    q(s, "ROUT:CLOS (@101)")
    assert numbers(q(s, ":MEAS?"))[0] == pytest.approx(5.0 * 10.0 / 11.0, rel=1e-4)


# ---- the scan table ---------------------------------------------------------------------------------


def test_a_scan_reads_each_channel_in_order(open_):
    s = open_()
    q(s, "ROUT:SCAN (@101:102)")
    reply = numbers(q(s, ":READ?"))
    assert reply[0::2] == [101.0, 102.0]  # the channel field of each reading
    assert reply[1::2] == pytest.approx([5.0, 3.0], abs=1e-4)


def test_a_scan_runs_in_ascending_channel_order(open_):
    s = open_()
    q(s, "ROUT:SCAN (@102,101)")  # the 34970A scans ascending, whatever order was written
    assert numbers(q(s, ":READ?"))[0::2] == [101.0, 102.0]
    assert q(s, "ROUT:SCAN?") == "(@102,101)"  # the stored list keeps the written order


def test_the_scan_count_repeats_the_list(open_):
    s = open_()
    q(s, "ROUT:SCAN (@101:102)")
    q(s, "TRIG:COUN 2")
    reply = numbers(q(s, ":READ?"))
    assert reply[0::2] == [101.0, 102.0, 101.0, 102.0]
    assert q(s, ":DATA:POIN?") == "4"


def test_the_relays_are_open_after_a_scan(open_):
    s = open_()
    q(s, "ROUT:SCAN (@101:102)")
    q(s, ":READ?")
    assert q(s, "ROUT:CLOS? (@101,102)") == "0,0"


def test_the_scan_interval_is_a_setting(open_):
    s = open_()
    q(s, "SCAN:INT 0.5")
    assert float(q(s, "SCAN:INT?").lstrip("+")) == pytest.approx(0.5)


def test_an_empty_scan_list_is_an_error(open_):
    s = open_(state={"scan.list": []})
    assert q(s, ":READ?") == ""
    assert q(s, ":SYST:ERR?").startswith("-222")
