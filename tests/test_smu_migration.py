"""sb-smu-1 on the generic trigger model and reading buffer (#94): replies, virtual time and the
run record of the commands the SMU tests and the acceptance scenario use are unchanged."""

from __future__ import annotations

import pytest

from simubench.api import open_bench
from simubench.engine.replay import replay
from simubench.models.bench import BenchDefinition

# (command, reply, ns consumed by the message) — generated on the smu_core implementation this
# migration replaced; None: the command answers nothing (and may have queued an error).
EXPECTED: list[tuple[str, str | None, int]] = [
    ("*IDN?", "SIMUBENCH,SB-SMU-1,SBSMU000001,1.0.0", 0),
    ("*RST", None, 0),
    (":SOUR:FUNC?;:SOUR:VOLT?;:SENS:CURR:PROT?;:OUTP?", "VOLT;+0.000000E+00;+1.050000E-04;0", 0),
    (":OUTP ON;:SENS:CURR:PROT 0.01;:SOUR:VOLT 5", None, 0),
    (":MEAS?", "+5.0000000E+00,+5.0005000E-03,+9.9990001E+02,+5.2405000E-02,+0.0000000E+00", 52405000),
    (
        ":SENS:CURR:PROT 0.004;:MEAS?",
        "+3.9996000E+00,+4.0000000E-03,+9.9990001E+02,+5.2405000E-02,+8.0000000E+00",
        52405000,
    ),
    (":OUTP:REG?;:STAT:MEAS:COND?;:SENS:CURR:PROT:TRIP?", "CC;16384;1", 0),
    (
        ":SOUR:FUNC CURR;:SOUR:CURR 0.002;:SENS:VOLT:PROT 1.5;:MEAS?",
        "+1.5000000E+00,+1.5001500E-03,+9.9990001E+02,+5.2405000E-02,+8.0000000E+00",
        52405000,
    ),
    (
        ":SOUR:FUNC VOLT;:SOUR:VOLT 8;:MEAS?",
        "+3.9996000E+00,+4.0000000E-03,+9.9990001E+02,+5.2405000E-02,+8.0000000E+00",
        52405000,
    ),
    (":SYST:ERR?", '0,"No error"', 0),
    (
        ":SENS:CURR:NPLC 0.1;:MEAS?",
        "+3.9996000E+00,+4.0000000E-03,+9.9990001E+02,+7.4050000E-03,+8.0000000E+00",
        7405000,
    ),
    (":SENS:CURR:NPLC 11", None, 0),
    (":SYST:LFR 55", None, 0),
    (":SYST:ERR?", '-222,"Data out of range"', 0),
    (
        ":SOUR:SWE:VOLT:LIN 0,0.8,5,0;:READ?",
        "+0.0000000E+00,+0.0000000E+00,+9.9100000E+37,+7.4050000E-03,+0.0000000E+00,+2.0000000E-01,+2.0002000E-04,+9.9990001E+02,+1.4810000E-02,+0.0000000E+00,+4.0000000E-01,+4.0004000E-04,+9.9990001E+02,+2.2215000E-02,+0.0000000E+00,+6.0000000E-01,+6.0006000E-04,+9.9990001E+02,+2.9620000E-02,+0.0000000E+00,+8.0000000E-01,+8.0008000E-04,+9.9990001E+02,+3.7025000E-02,+0.0000000E+00",
        37025000,
    ),
    (
        ":TRAC:POIN:ACT?;:FETC?",
        "5;+0.0000000E+00,+0.0000000E+00,+9.9100000E+37,+7.4050000E-03,+0.0000000E+00,+2.0000000E-01,+2.0002000E-04,+9.9990001E+02,+1.4810000E-02,+0.0000000E+00,+4.0000000E-01,+4.0004000E-04,+9.9990001E+02,+2.2215000E-02,+0.0000000E+00,+6.0000000E-01,+6.0006000E-04,+9.9990001E+02,+2.9620000E-02,+0.0000000E+00,+8.0000000E-01,+8.0008000E-04,+9.9990001E+02,+3.7025000E-02,+0.0000000E+00",
        0,
    ),
    (":TRAC:CLE;:TRAC:POIN:ACT?", "0", 0),
    (":FETC?", None, 0),
    (":SYST:ERR?", '-222,"Data out of range; line frequency is 50 or 60 Hz"', 0),
    (
        ":SOUR:SWE:VOLT:LOG 0.01,10,7,0;:READ?",
        "+1.0000000E-02,+1.0001000E-05,+9.9990001E+02,+7.4050000E-03,+0.0000000E+00,+3.1622777E-02,+3.1625939E-05,+9.9990001E+02,+1.4810000E-02,+0.0000000E+00,+1.0000000E-01,+1.0001000E-04,+9.9990001E+02,+2.2215000E-02,+0.0000000E+00,+3.1622777E-01,+3.1625939E-04,+9.9990001E+02,+2.9620000E-02,+0.0000000E+00,+1.0000000E+00,+1.0001000E-03,+9.9990001E+02,+3.7025000E-02,+0.0000000E+00,+3.1622777E+00,+3.1625939E-03,+9.9990001E+02,+4.4430000E-02,+0.0000000E+00,+3.9996000E+00,+4.0000000E-03,+9.9990001E+02,+5.1835000E-02,+8.0000000E+00",
        51835000,
    ),
    (
        ':SOUR:SWE:VOLT:LIST "-8,-2,0,2,8";:READ?',
        "-3.9996000E+00,-4.0000000E-03,+9.9990001E+02,+7.4050000E-03,+8.0000000E+00,-2.0000000E+00,-2.0002000E-03,+9.9990001E+02,+1.4810000E-02,+0.0000000E+00,+0.0000000E+00,+0.0000000E+00,+9.9100000E+37,+2.2215000E-02,+0.0000000E+00,+2.0000000E+00,+2.0002000E-03,+9.9990001E+02,+2.9620000E-02,+0.0000000E+00,+3.9996000E+00,+4.0000000E-03,+9.9990001E+02,+3.7025000E-02,+8.0000000E+00",
        37025000,
    ),
    (
        ":SOUR:SWE:VOLT:LIST -8,-2,0,2,8;:READ?",
        "-3.9996000E+00,-4.0000000E-03,+9.9990001E+02,+7.4050000E-03,+8.0000000E+00,-2.0000000E+00,-2.0002000E-03,+9.9990001E+02,+1.4810000E-02,+0.0000000E+00,+0.0000000E+00,+0.0000000E+00,+9.9100000E+37,+2.2215000E-02,+0.0000000E+00,+2.0000000E+00,+2.0002000E-03,+9.9990001E+02,+2.9620000E-02,+0.0000000E+00,+3.9996000E+00,+4.0000000E-03,+9.9990001E+02,+3.7025000E-02,+8.0000000E+00",
        37025000,
    ),
    (
        ":SOUR:VOLT 2;:MEAS?",
        "+2.0000000E+00,+2.0002000E-03,+9.9990001E+02,+7.4050000E-03,+0.0000000E+00",
        7405000,
    ),
    (":SOUR:SWE:VOLT:LIN 0,0.8,5,0;:INIT", None, 0),
    ("*OPC?", "1", 37025000),
    (
        ":FETC?",
        "+0.0000000E+00,+0.0000000E+00,+9.9100000E+37,+7.4050000E-03,+0.0000000E+00,+2.0000000E-01,+2.0002000E-04,+9.9990001E+02,+1.4810000E-02,+0.0000000E+00,+4.0000000E-01,+4.0004000E-04,+9.9990001E+02,+2.2215000E-02,+0.0000000E+00,+6.0000000E-01,+6.0006000E-04,+9.9990001E+02,+2.9620000E-02,+0.0000000E+00,+8.0000000E-01,+8.0008000E-04,+9.9990001E+02,+3.7025000E-02,+0.0000000E+00",
        0,
    ),
    (":SOUR:SWE:VOLT:LIN 0,8,1,0", None, 0),
    (":SOUR:SWE:VOLT:LIN 0,8,2501,0", None, 0),
    (":SOUR:SWE:VOLT:LIN 0,211,9,0", None, 0),
    (":SOUR:SWE:CURR:LIN 0,1.1,9,0", None, 0),
    (":SOUR:SWE:VOLT:LOG -1,10,5,0", None, 0),
    (
        ":SYST:ERR?;:SYST:ERR?;:SYST:ERR?;:SYST:ERR?;:SYST:ERR?",
        '-230,"Data corrupt or stale; the buffer is empty";-222,"Data out of range";-222,"Data out of range";-222,"Data out of range";-222,"Data out of range"',
        0,
    ),
    (
        ":SENS:CURR:PROT 1;:SOUR:VOLT 50;:MEAS?",
        "+5.0000000E+01,+5.0005000E-02,+9.9990001E+02,+7.4050000E-03,+0.0000000E+00",
        7405000,
    ),
    (
        ":SOUR:VOLT 200;:MEAS?",
        "+1.0498950E+02,+1.0500000E-01,+9.9990001E+02,+7.4050000E-03,+8.0000000E+00",
        7405000,
    ),
]


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
    session = open_bench(bench())
    yield session
    session.close()


def run_sequence(s) -> list[tuple[str, str | None, int]]:
    out = []
    for cmd in SEQUENCE:
        before = s.now_ns
        s.write("smu", (cmd + "\n").encode())
        reply = s.read("smu")
        text = reply.decode().rstrip("\n") if reply is not None else None
        out.append((cmd, text, s.now_ns - before))
    return out


def test_replies_and_virtual_time_are_unchanged(s):
    got = run_sequence(s)
    if not EXPECTED:
        for row in got:
            print(repr(row))
        pytest.fail("EXPECTED is empty: paste the output above")
    assert got == EXPECTED


def test_the_record_replays(tmp_path):
    s = open_bench(bench(), record_dir=tmp_path)
    try:
        run_sequence(s)
    finally:
        s.close()
    [record] = tmp_path.glob("*.jsonl")
    report = replay(record)
    assert report.ok, report
