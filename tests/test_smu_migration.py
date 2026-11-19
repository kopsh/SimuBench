"""sb-smu-1 on the generic trigger model and reading buffer (#94): replies, virtual time and the
run record of the commands the SMU tests and the acceptance scenario use are unchanged.

``tests/data/smu_gold.json`` holds the recording in the one gold step shape (#1635) — recorded on
the smu_core implementation this migration replaced, re-recordable with
``uv run python tests/record_gold.py smu``. A ``None`` reply: the command answers nothing (and may
have queued an error).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import record_steps, require_gold
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition
from simubench.replay import replay

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
GOLD_FILE = ROOT / "tests" / "data" / "smu_gold.json"
GOLD = json.loads(GOLD_FILE.read_text()) if GOLD_FILE.exists() else {}


def bench() -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "name": "smu-migration",
            "instruments": {
                "smu": {
                    "model": "sb-smu-1",
                    "state": {
                        "source.voltage.level": 5.0,
                        "compliance.current.limit": 0.01,
                        "output.enabled": True,
                        "source.delay": 0.001,
                    },
                },
                "dmm": {"model": "sb-dmm-1"},
            },
            "parts": {"r1": {"type": "load_resistor", "params": {"r": 1000.0}}},
            "wires": [
                {"from": "smu.HI", "to": "r1.A"},
                {"from": "smu.HI", "to": "dmm.IN"},
            ],
            "run": {"clock": "command", "idle": "off"},
        }
    )


SEQUENCE = (
    "*IDN?",
    "*RST",
    ":SOUR:FUNC?;:SOUR:VOLT?;:SENS:CURR:PROT?;:OUTP?",
    ":OUTP ON;:SENS:CURR:PROT 0.01;:SOUR:VOLT 5",
    ":MEAS?",
    ":SENS:CURR:PROT 0.004;:MEAS?",
    ":OUTP:REG?;:STAT:MEAS:COND?;:SENS:CURR:PROT:TRIP?",
    ":SOUR:FUNC CURR;:SOUR:CURR 0.002;:SENS:VOLT:PROT 1.5;:MEAS?",
    ":SOUR:FUNC VOLT;:SOUR:VOLT 8;:MEAS?",
    ":SYST:ERR?",
    ":SENS:CURR:NPLC 0.1;:MEAS?",
    ":SENS:CURR:NPLC 11",
    ":SYST:LFR 55",
    ":SYST:ERR?",
    ":SOUR:SWE:VOLT:LIN 0,0.8,5,0;:READ?",
    ":TRAC:POIN:ACT?;:FETC?",
    ":TRAC:CLE;:TRAC:POIN:ACT?",
    ":FETC?",
    ":SYST:ERR?",
    ":SOUR:SWE:VOLT:LOG 0.01,10,7,0;:READ?",
    ':SOUR:SWE:VOLT:LIST "-8,-2,0,2,8";:READ?',
    ":SOUR:SWE:VOLT:LIST -8,-2,0,2,8;:READ?",
    ":SOUR:VOLT 2;:MEAS?",
    ":SOUR:SWE:VOLT:LIN 0,0.8,5,0;:INIT",
    "*OPC?",
    ":FETC?",
    ":SOUR:SWE:VOLT:LIN 0,8,1,0",
    ":SOUR:SWE:VOLT:LIN 0,8,2501,0",
    ":SOUR:SWE:VOLT:LIN 0,211,9,0",
    ":SOUR:SWE:CURR:LIN 0,1.1,9,0",
    ":SOUR:SWE:VOLT:LOG -1,10,5,0",
    ":SYST:ERR?;:SYST:ERR?;:SYST:ERR?;:SYST:ERR?;:SYST:ERR?",
    ":SENS:CURR:PROT 1;:SOUR:VOLT 50;:MEAS?",
    ":SOUR:VOLT 200;:MEAS?",
)


@pytest.fixture
def s():
    session = open_bench(bench(), definition_dirs=[DEFS])
    yield session
    session.close()


def run_sequence(s) -> list[dict]:
    return record_steps(s, [("smu", cmd) for cmd in SEQUENCE])


def test_replies_and_virtual_time_are_unchanged(s):
    require_gold(run_sequence(s), GOLD.get("steps", []), GOLD_FILE.name)


def record() -> None:
    """Re-record the SMU sequence into ``tests/data/smu_gold.json`` (``tests/record_gold.py``
    calls this): walk the sequence on the bench, the way the test above compares it."""
    session = open_bench(bench(), definition_dirs=[DEFS])
    try:
        recorded = {"steps": run_sequence(session)}
    finally:
        session.close()
    GOLD_FILE.write_text(json.dumps(recorded, indent=1) + "\n")
    print(f"wrote {GOLD_FILE.relative_to(ROOT)}")


def test_the_record_replays(tmp_path):
    s = open_bench(bench(), record_dir=tmp_path)
    try:
        run_sequence(s)
    finally:
        s.close()
    [record] = tmp_path.glob("*.jsonl")
    report = replay(record)
    assert report.ok, report
