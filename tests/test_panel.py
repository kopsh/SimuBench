"""Front-panel Run / Stop / Single (issue 32, D32): operations inside the instrument, not SCPI."""

from __future__ import annotations

import hashlib
from itertools import pairwise
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


def test_the_front_panel_flow():
    """One flow on the Siglent panel: a running screen is not an acquisition, run and stop are
    one panel event each and no SCPI, and single acquires once — the record a script then reads."""
    s, served = bench()
    # -- a running screen is not an acquisition (was test_a_running_screen_is_not_an_acquisition)
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
    # -- run and stop are one event each and no scpi
    # (was test_run_and_stop_are_one_event_each_and_no_scpi; screens emit nothing, so the new
    # subscription starts empty exactly as on the original bench)
    run_events: list[dict] = []
    s.subscribe(run_events.append)
    s.panel("scope", "stop")
    assert not screen(served).running
    s.panel("scope", "run")
    assert screen(served).running
    assert [(e["kind"], e["action"], e["source"]) for e in run_events] == [
        ("panel", "stop", "ui"),
        ("panel", "run", "ui"),
    ]
    # -- single is the acquisition a script then reads
    # (was test_single_is_the_acquisition_a_script_then_reads; nothing above acquired, so the
    # count a single adds one to is still the fresh bench's)
    acq = s.instruments["scope"].components["acq"]
    s.panel("scope", "single")
    assert acq.acq_count == 1 and not screen(served).running  # one acquisition, then stopped
    data = query(s, ":WAVeform:DATA?")
    xfer = s.instruments["scope"].components["xfer"]
    assert data == xfer.encode(acq.records[1]) + b"\n"  # the record the panel acquired
    assert acq.acq_count == 1  # a stopped scope hands out that one
    assert screen(served).source == "record"
    s.close()


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


# ---- a running screen is alive (#730) -----------------------------------------------------------


def _frames(served, n: int = 4) -> list[np.ndarray]:
    return [np.array(screen(served).traces[0].volts) for _ in range(n)]


def test_a_triggered_running_screen_is_stable_with_noise_on_it():
    """The Siglent's input noise rides each frame: no two frames are the same, but a triggered
    trace stays locked to the signal, so they differ by the noise only (a few mV on a 0.17 V trace)."""
    s, served = bench()
    frames = _frames(served)
    assert screen(served).traces[0].triggered
    diffs = [float(np.abs(b - a).max()) for a, b in pairwise(frames)]
    assert all(d > 0 for d in diffs)  # not a dead line
    assert all(d < 0.02 for d in diffs)  # but not sliding either
    s.close()


def test_an_untriggered_running_screen_slides_from_frame_to_frame():
    """A free-running scope starts each sweep at an arbitrary instant: with the trigger level beyond
    the signal the trace is not locked and the phase differs from frame to frame, by far more than noise."""
    s, served = bench()
    s.write("scope", b":TRIGger:EDGE:LEVel 0.2\n")
    frames = _frames(served, 6)
    assert not screen(served).traces[0].triggered
    diffs = [float(np.abs(b - a).max()) for a, b in pairwise(frames)]
    assert max(diffs) > 0.05, diffs  # the 0.17 V trace moved, not 5 mV of noise
    s.close()


def test_the_frames_of_a_screen_are_the_same_on_every_run():
    """Frames are a pure function of the seed and the frame number: a replay shows the same screen, and
    drawing them takes no virtual time (the scripts' clock is not the screen's)."""
    runs = []
    for _ in range(2):
        s, served = bench()
        s.write("scope", b":TRIGger:EDGE:LEVel 0.2\n")
        t0 = s.now_ns
        runs.append(_frames(served, 3))
        assert s.now_ns == t0
        s.close()
    assert all(np.array_equal(a, b) for a, b in zip(runs[0], runs[1], strict=True))


def test_a_triggered_screen_varies_by_its_noise_only():
    """The DSO-X 2024A now carries the range table's input noise (#745, specs.noise_per_division:
    10 mV rms on 0.5 V/div): a triggered trace draws anew each frame — never a dead line — but
    stays locked to the signal, so the frames differ by that noise only."""
    s = open_bench(
        ROOT / "examples" / "benches" / "keysight-dsox2024a.bench.yaml", clock="command", seed=5, idle="off"
    )
    served = ServedSession("t", s, None)
    s.write("scope", b":RUN\n")
    frames = _frames(served, 3)
    assert screen(served).traces[0].triggered
    diffs = [float(np.abs(b - a).max()) for a, b in pairwise(frames)]
    assert all(d > 0 for d in diffs)  # not a dead line
    assert all(d <= 0.1 for d in diffs)  # 100k samples: the pair's max reaches some 5 rms of the 10 mV noise
    s.close()
