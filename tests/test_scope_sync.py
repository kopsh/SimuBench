"""Scope acquisitions that finish in virtual time (instrument-authoring.md 8.3 F1, 8.4; issue 134).

With ``completion: pending`` a single acquisition, a forced frame or a digitize is an operation that
finishes when the record after the trigger is full: the trigger status goes waiting, triggered,
stopped; a read meanwhile gets the previous record (or an error); ``*OPC?`` waits for it (unless
the model says it does not) and, armed for a trigger that does not come, does not answer.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import pyvisa
import yaml

from simubench.engine.session import Session
from simubench.library.loader import DefinitionLibrary, load_bench, load_yaml
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition

ROOT = Path(__file__).resolve().parents[1]
SIGLENT = ROOT / "examples" / "benches" / "siglent_scope_rc.bench.yaml"
SDS = load_yaml(
    (ROOT / "src" / "simubench" / "library" / "instruments" / "siglent-sds2104xplus.yaml").read_text(
        encoding="utf-8"
    )
)
DSO4 = load_yaml((ROOT / "validation" / "instruments" / "sb-dso-4.yaml").read_text(encoding="utf-8"))


def _sds(**acq: object) -> Session:
    """The Siglent example bench (SDG2042X into the SDS2104X Plus) with acquisition parameters changed."""
    data = copy.deepcopy(SDS)
    next(c for c in data["components"] if c["name"] == "acq")["params"].update(acq)
    definition = InstrumentDefinition.model_validate(data)
    lib = DefinitionLibrary([SIGLENT.parent])
    session = Session(
        load_bench(SIGLENT),
        lambda def_id: definition if def_id == "siglent-sds2104xplus" else lib.get(def_id),
        base_dir=SIGLENT.parent,
        clock="command",
        seed=1,
        idle="off",
    )
    session.write("awg", b"C1:OUTP ON\n")
    session.write("scope", b":TIMebase:SCALe 1E-3;:CHANnel1:SCALe 0.5\n")
    return session


def _query(session: Session, cmd: str) -> str:
    session.write("scope", cmd.encode() + b"\n")
    out = session.read("scope") if "?" in cmd else None
    return out.decode(errors="replace").strip() if out is not None else ""


def test_a_read_right_after_single_gets_the_previous_frame():
    session = _sds()
    acq = session.instruments["scope"].components["acq"]
    _query(session, ":TRIGger:MODE SINGle")
    kept = acq.records[1].volts.copy()  # the running scope's last frame
    assert _query(session, ":ACQuire:NUMAcq?") == "1"
    session.write("scope", b":WAVeform:SOURce C1;:WAVeform:DATA?\n")
    assert session.read("scope")  # a read now gets that frame, and does not wait
    assert (acq.records[1].volts == kept).all() and _query(session, ":ACQuire:NUMAcq?") == "1"
    assert _query(session, "*OPC?") == "1"
    _query(session, ":TRIGger:STATus?")  # any command finishes what is due
    assert acq.acq_count == 2 and acq.records[1].t_start_ns > 0
    session.close()


def test_the_status_follows_virtual_time():
    session = _sds()
    acq = session.instruments["scope"].components["acq"]
    _query(session, ":TRIGger:MODE SINGle")
    assert _query(session, ":TRIGger:STATus?") == "Ready"
    assert acq.trigger_ns is not None and acq.due_ns is not None
    assert session.now_ns < acq.trigger_ns < acq.due_ns
    session.advance((acq.trigger_ns - session.now_ns) / 1e9 + 1e-6)  # past the trigger
    assert _query(session, ":TRIGger:STATus?") == "Trig'd"  # the record after it is filling
    session.advance((acq.due_ns - session.now_ns) / 1e9)
    assert _query(session, ":TRIGger:STATus?") == "Stop"
    session.close()


def test_opc_does_not_wait_when_the_model_says_so():
    session = _sds(opc_waits=False)
    _query(session, ":TRIGger:MODE SINGle")
    assert _query(session, "*OPC?") == "1"
    assert _query(session, ":TRIGger:STATus?") == "Ready"  # answered, the acquisition still running
    session.close()


def test_without_a_trigger_opc_returns_when_the_model_says_so():
    session = _sds(no_trigger="return")
    _query(session, ":TRIGger:EDGE:LEVel 0.15;:TRIGger:MODE SINGle")  # above the signal
    assert _query(session, "*OPC?") == "1"
    assert _query(session, ":TRIGger:STATus?") == "Ready"
    session.close()


def test_a_read_while_waiting_may_be_an_error():
    session = _sds(while_waiting="error")
    _query(session, ":TRIGger:MODE SINGle")
    session.write("scope", b":WAVeform:SOURce C1;:WAVeform:DATA?\n")
    assert session.read("scope") is None  # no data, an error instead
    assert session.instruments["scope"].errors.peek_all()[-1].code == -221
    session.close()


def test_stop_ends_the_wait():
    session = _sds()
    _query(session, ":TRIGger:EDGE:LEVel 0.15;:TRIGger:MODE SINGle")
    assert _query(session, "*OPC?") == ""  # waiting for a trigger that does not come
    _query(session, ":TRIGger:STOP")
    assert _query(session, "*OPC?") == "1"
    assert _query(session, ":TRIGger:STATus?") == "Stop"
    session.close()


def _dso(**acq: object) -> tuple[Session, InstrumentDefinition]:
    """sb-dso-4 alone with acquisition parameters changed, *RST taking 0.5 s and a system component."""
    data = copy.deepcopy(DSO4)
    data["id"] = "dso-sync"
    data["components"][0]["params"].update(acq)
    data["components"].append({"name": "sys", "type": "system"})
    data["commands"].append({"header": "*RST", "op": "sys.reset", "query": False, "duration": 0.5})
    definition = InstrumentDefinition.model_validate(data)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/2", "name": "sync", "instruments": {"scope": {"model": "dso-sync"}}}
    )
    session = Session(bench, {"dso-sync": definition}.__getitem__, clock="command", seed=1, idle="off")
    return session, definition


def test_a_digitize_returns_once_its_record_is_full():
    session, _ = _dso(completion="pending")
    acq = session.instruments["scope"].components["acq"]
    session.write("scope", b":TIMebase:SCALe 1E-3;:TRIGger:SWEep AUTO;:CHANnel1:DISPlay 1\n")
    _, takes, _ = acq.compute([1])  # what this acquisition takes (no signal: the whole search, auto)
    before = session.now_ns
    session.write("scope", b":DIGitize CHANnel1\n")
    assert session.now_ns - before == takes and takes > 10 * 1e-3 * 1e9
    assert 1 not in acq.records  # done at that instant: the next command finishes it
    assert _query(session, "*OPC?") == "1" and 1 in acq.records
    session.close()


def test_a_command_takes_the_time_its_definition_declares():
    session, _ = _dso()
    before = session.now_ns
    session.write("scope", b"*RST\n")
    assert session.now_ns - before == 500_000_000
    session.close()


def test_pyvisa_times_out_on_opc_while_no_trigger_comes(tmp_path):
    data = copy.deepcopy(DSO4)
    data["id"] = "dso-blocking"
    data["components"][0]["params"].update(completion="pending", no_trigger="wait")
    (tmp_path / "instruments").mkdir()
    (tmp_path / "instruments" / "dso-blocking.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )
    bench = tmp_path / "blocking.bench.yaml"
    bench.write_text(
        "schema: simubench.bench/2\nname: blocking\ninstruments:\n  scope: {model: dso-blocking}\n",
        encoding="utf-8",
    )
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        scope = rm.open_resource("scope", read_termination="\n", write_termination="\n", timeout=200)
        scope.write(":TRIGger:SWEep NORMal;:TRIGger:LEVel 1;:CHANnel1:DISPlay 1")  # no signal at all
        scope.write(":DIGitize CHANnel1")
        with pytest.raises(pyvisa.errors.VisaIOError):
            scope.query("*OPC?")
    finally:
        rm.close()
