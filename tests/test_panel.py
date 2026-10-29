"""Front-panel Run / Stop / Single (issue 32, D32): operations inside the instrument, not SCPI."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from simubench.api import open_bench
from simubench.engine.replay import replay
from simubench.server.manager import ServedSession, SessionManager

ROOT = Path(__file__).resolve().parents[1]
SIGLENT = ROOT / "examples" / "benches" / "siglent_scope_rc.bench.yaml"
LFCAL = ROOT / "examples" / "lowfreq-cal" / "lowfreq_cal.bench.yaml"
SB = ROOT / "validation" / "benches" / "awg_rc_scope.bench.yaml"


def bench(path=SIGLENT, **kw):
    s = open_bench(path, clock="command", seed=5, idle="off", **kw)
    if "awg" in s.instruments:
        s.write("awg", b"C1:OUTP ON\n" if path == SIGLENT else b":OUTPut1 ON\n")
    return s, ServedSession("t", s, None)


def screen(served):
    return SessionManager.scope_screen(served, "scope")


def query(s, cmd: str) -> bytes | None:
    s.write("scope", cmd.encode() + b"\n")
    return s.read("scope") if "?" in cmd else None


def test_a_running_screen_is_not_an_acquisition():
    s, served = bench()
    events: list[dict] = []
    s.subscribe(events.append)
    t0, count = s.now_ns, s.instruments["scope"].components["acq"].acq_count
    frames = [screen(served) for _ in range(3)]
    assert all(f.running and f.source == "live" for f in frames)
    a, b = (np.array(f.traces[0].volts) for f in frames[:2])
    assert not np.array_equal(a, b)  # the noise changes frame to frame
    assert np.abs(a - b).max() < 0.05  # the signal does not
    assert (s.now_ns, s.instruments["scope"].components["acq"].acq_count) == (t0, count)
    assert events == []  # nothing in the communication log or the history


def test_run_and_stop_are_one_event_each_and_no_scpi():
    s, served = bench()
    events: list[dict] = []
    s.subscribe(events.append)
    s.panel("scope", "stop")
    assert not screen(served).running
    s.panel("scope", "run")
    assert screen(served).running
    assert [(e["kind"], e["action"], e["source"]) for e in events] == [
        ("panel", "stop", "ui"),
        ("panel", "run", "ui"),
    ]


def script(s) -> list:
    out = []
    for cmd in (":TRIGger:STATus?", ":WAVeform:DATA?", ":MEASure:SIMPle:VALue? PKPK", ":WAVeform:PREamble?"):
        reply = query(s, cmd)
        out.append((cmd, hashlib.sha256(reply or b"").hexdigest(), s.now_ns))
    return out


def test_a_running_screen_does_not_change_what_a_script_sees():
    alone, _ = bench()
    expected = script(alone)
    watched, served = bench()
    seen = []
    for cmd in (":TRIGger:STATus?", ":WAVeform:DATA?", ":MEASure:SIMPle:VALue? PKPK", ":WAVeform:PREamble?"):
        screen(served)  # the UI redraws between the script's commands
        reply = query(watched, cmd)
        seen.append((cmd, hashlib.sha256(reply or b"").hexdigest(), watched.now_ns))
    assert seen == expected


def test_single_is_the_acquisition_a_script_then_reads():
    s, served = bench()
    acq = s.instruments["scope"].components["acq"]
    s.panel("scope", "single")
    assert acq.acq_count == 1 and not screen(served).running  # one acquisition, then stopped
    data = query(s, ":WAVeform:DATA?")
    xfer = s.instruments["scope"].components["xfer"]
    assert data == xfer.encode(acq.records[1]) + b"\n"  # the record the panel acquired
    assert acq.acq_count == 1  # a stopped scope hands out that one
    assert screen(served).source == "record"


def test_models_without_their_own_panel_commands():
    s, served = bench(SB)
    acq = s.instruments["scope"].components["acq"]
    s.panel("scope", "stop")
    s.panel("scope", "single")  # no single mode: one acquisition of the displayed channels, stopped or not
    assert acq.acq_count == 1 and acq.records
    query(s, ":DIGitize")  # a script's digitize too
    assert acq.acq_count == 2
    s, served = bench(LFCAL)
    s.panel("scope", "stop")
    assert s.instruments["scope"].get_state("trigger.mode") == "STOP"
    s.panel("scope", "run")  # a stop mode: run again in the first auto mode
    assert s.instruments["scope"].get_state("trigger.mode") == "AUTO" and screen(served).running


def test_a_record_with_panel_actions_replays_byte_for_byte(tmp_path):
    s, _ = bench(record_dir=tmp_path)
    s.panel("scope", "single")
    query(s, ":WAVeform:DATA?")
    s.panel("scope", "run")
    query(s, ":WAVeform:DATA?")
    path = s.recorder.path
    s.close()
    report = replay(path)
    assert report.ok, report.as_dict()
    assert report.replies == 2 and report.actions >= 4  # two panel actions among them
