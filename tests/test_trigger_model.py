"""trigger_model and reading_buffer, *OPC / *WAI on pending operations, *TRG (#88,
metrology-authoring.md 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.engine.replay import replay
from simubench.models.bench import BenchDefinition

DEFS = Path(__file__).parent / "definitions"
NPLC_S = 0.02  # one reading at 1 PLC in the test meter


def bench() -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "name": "trigger",
            "instruments": {
                "src": {
                    "model": "sb-smu-1",
                    "state": {
                        "source.voltage.level": 2.5,
                        "compliance.current.limit": 0.01,
                        "output.enabled": True,
                    },
                },
                "dmm": {"model": "test-trigger-meter"},
            },
            "wires": [{"from": "src.HI", "to": "dmm.IN"}],
            "run": {"clock": "command", "idle": "off"},
        }
    )


@pytest.fixture
def s(tmp_path):
    session = open_bench(bench(), definition_dirs=[DEFS], record_dir=tmp_path)
    yield session
    session.close()


def q(s, cmd: str) -> str | None:
    s.write("dmm", (cmd + "\n").encode())
    out = s.read("dmm")
    return out.decode().strip() if out else None


def err(s) -> str:
    return q(s, ":SYST:ERR?") or ""


def values(reply: str | None) -> list[float]:
    assert reply, reply
    return [float(x) for x in reply.split(",")]


def test_counts_times_readings_take_their_virtual_time(s):
    q(s, ":TRIG:COUN 3;:SAMP:COUN 2;:TRIG:DEL 0.01")
    t0 = s.now_ns
    q(s, ":INIT")
    assert s.now_ns == t0  # INITiate returns at once
    readings = values(q(s, ":FETC?"))
    assert readings == pytest.approx([2.5] * 6)
    assert s.now_ns - t0 == round((6 * NPLC_S + 3 * 0.01) * 1e9)
    assert values(q(s, ":FETC?")) == readings  # fetching again gives the same run, no more time
    assert s.now_ns - t0 == round((6 * NPLC_S + 3 * 0.01) * 1e9)
    assert err(s).startswith("0,")


def test_opc_query_and_wai_wait_for_the_run(s):
    q(s, ":SAMP:COUN 5")
    t0 = s.now_ns
    assert q(s, ":INIT;*OPC?") == "1"
    assert s.now_ns - t0 == round(5 * NPLC_S * 1e9)
    t1 = s.now_ns
    q(s, ":INIT;*WAI;:TRIG:STAT?")  # *WAI: the commands after it see the run finished
    assert s.now_ns - t1 == round(5 * NPLC_S * 1e9)
    assert q(s, ":TRIG:STAT?") == "IDLE"


def test_opc_sets_the_event_bit_once_the_run_is_done(s):
    q(s, ":SAMP:COUN 10")
    q(s, ":INIT;*OPC")
    assert q(s, "*ESR?") == "0"  # still measuring
    s.advance(10 * NPLC_S)
    assert q(s, "*ESR?") == "1"
    q(s, ":INIT;*OPC;*CLS")  # *CLS cancels the operation-complete wait
    s.advance(10 * NPLC_S)
    assert q(s, "*ESR?") == "0"


def test_bus_trigger(s):
    q(s, ":TRIG:SOUR BUS;:TRIG:COUN 2;:SAMP:COUN 3")
    q(s, ":INIT")
    assert q(s, ":FETC?") is None  # nothing triggered yet: no reply (the script times out) and an error
    assert err(s).startswith("-230,")
    assert q(s, ":TRIG:STAT?") == "WAITING"
    t0 = s.now_ns
    q(s, "*TRG")
    assert len(values(q(s, ":FETC?"))) == 3 and s.now_ns - t0 == round(3 * NPLC_S * 1e9)
    assert q(s, ":TRIG:STAT?") == "WAITING"  # one more trigger in this run
    q(s, "*TRG")
    assert len(values(q(s, ":FETC?"))) == 6 and q(s, ":TRIG:STAT?") == "IDLE"
    q(s, "*TRG")
    assert err(s).startswith("-211,")  # not waiting for a trigger
    assert q(s, ":READ?") is None and err(s).startswith("-214,")  # READ? would wait forever


def test_init_while_measuring_is_ignored_and_abort_stops(s):
    q(s, ":SAMP:COUN 100")
    q(s, ":INIT")
    q(s, ":INIT")
    assert err(s).startswith("-213,")
    q(s, ":ABOR")
    assert q(s, ":TRIG:STAT?") == "IDLE"
    assert q(s, "*OPC?") == "1"  # nothing pending after the abort


def test_read_is_initiate_and_fetch(s):
    q(s, ":SAMP:COUN 2")
    t0 = s.now_ns
    assert values(q(s, ":READ?")) == pytest.approx([2.5, 2.5])
    assert s.now_ns - t0 == round(2 * NPLC_S * 1e9)


def test_reading_memory(s):
    q(s, ":SAMP:COUN 5")
    q(s, ":INIT;*WAI")
    assert q(s, ":DATA:POIN?") == "5"
    assert values(q(s, ":DATA:REM? 2")) == pytest.approx([2.5, 2.5])
    assert q(s, ":DATA:POIN?") == "3"
    q(s, ":DATA:REM? 9")
    assert err(s).startswith("-222,")
    s.write("dmm", b"R?\n")
    block = s.read("dmm")
    assert block is not None and block.startswith(b"#")
    digits = int(block[1:2])
    length = int(block[2 : 2 + digits])
    payload = block[2 + digits : 2 + digits + length]
    assert [float(x) for x in payload.split(b",")] == pytest.approx([2.5] * 3)
    assert q(s, ":DATA:POIN?") == "0"  # R? takes the readings out


def test_named_buffers_and_elements(s):
    q(s, ':TRAC:MAKE "mybuf",10')
    q(s, ':TRAC:MAKE "mybuf",10')
    assert err(s).startswith("-222,")  # it exists already
    q(s, ":SAMP:COUN 3")
    q(s, ":INIT;*WAI")
    rel = values(q(s, ':TRAC:DATA? 1,3,"defbuffer1",READ,REL'))
    assert rel[0::2] == pytest.approx([2.5] * 3)
    assert rel[1::2] == pytest.approx([0.0, NPLC_S, 2 * NPLC_S])  # seconds since the first reading
    q(s, ':TRAC:DATA? 1,3,"mybuf",READ')
    assert err(s).startswith("-230,")  # the trigger model fills the default buffer


def test_trg_without_a_bus_trigger_is_not_modeled(tmp_path):
    b = BenchDefinition.model_validate({"name": "x", "instruments": {"dmm": {"model": "sb-dmm-1"}}})
    s = open_bench(b)
    try:
        s.write("dmm", b"*TRG\n")
        s.write("dmm", b":SYST:ERR?\n")
        assert s.read("dmm").decode().startswith("-113,")
    finally:
        s.close()


def test_a_run_replays_identically(s, tmp_path):
    q(s, ":TRIG:SOUR BUS;:SAMP:COUN 4")
    q(s, ":INIT")
    q(s, "*TRG")
    q(s, ":FETC?")
    q(s, ":INIT;*OPC")
    s.close()
    [record] = tmp_path.glob("*.jsonl")
    report = replay(record, definition_dirs=[DEFS])
    assert report.ok, report
