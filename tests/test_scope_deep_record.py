"""Deep records (#1012): a record deeper than ``EAGER_POINTS`` is computed where it is read.

The acquisition fixes the record (rate, points, trigger, random stream) and captures the channel's
chain; reads compute only the tiles they touch. The invariants: the replies are those of the record
computed whole (byte for byte), a sample reads the same whichever read reaches it, what was acquired
does not follow later settings, and what one read holds in memory is bounded. Most tests shrink
``EAGER_POINTS`` so a 100 000-point record already takes the deep path; one acquires the UXR1104B's
2 Gpts for real.
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import numpy as np
import pytest

from helpers import raises_with, session_query
from simubench.api import open_bench
from simubench.engine import session as session_module
from simubench.engine.stream import ModelLimit
from simubench.library.components import scope as scope_module
from simubench.library.components import scope_record

ROOT = Path(__file__).resolve().parents[1]
UXR = ROOT / "examples" / "benches" / "keysight-uxr0334b.bench.yaml"
UXR_1104B = ROOT / "examples" / "benches" / "keysight-uxr1104b.bench.yaml"
DSOX = ROOT / "examples" / "benches" / "keysight-dsox2024a.bench.yaml"
SIGLENT = ROOT / "examples" / "benches" / "siglent_scope_rc.bench.yaml"

# 100 000 points of the UXR0334B: 128 GSa/s / 2^4 = 8 GSa/s over 10 x 1.25 us.
RECORD = [":ACQuire:POINts 100000", ":TIMebase:SCALe 1.25E-6", ":DIGitize", ":WAVeform:SOURce CHANnel1"]
READS = [
    ":WAVeform:PREamble?",
    ":WAVeform:FORMat WORD",
    ":WAVeform:DATA?",
    ":WAVeform:DATA? 12345,6789",
    ":WAVeform:FORMat ASCii",
    ":WAVeform:DATA? 99990,500",  # cut to the record's end
    ":MEASure:VPP? CHANnel1",
    ":MEASure:FREQuency? CHANnel1",
    ":WAVeform:SOURce CHANnel2",
    ":WAVeform:FORMat BYTE",
    ":WAVeform:DATA?",
    ":MEASure:VMAX? CHANnel2",
    ":SYSTem:ERRor?",
]


def _replies(bench: Path, commands: list[str]) -> tuple[list[bytes | None], object]:
    session = open_bench(bench, clock="command", seed=3, idle="off")
    replies = []
    for command in commands:
        session.write("scope", (command + "\n").encode())
        replies.append(session.read("scope") if "?" in command else None)
    acq = session.instruments["scope"].components["acq"]
    records = dict(acq.records)
    session.close()
    return replies, records


def test_a_deep_record_answers_byte_for_byte_as_the_record_computed_whole(monkeypatch):
    """The golden standard of #1012: the same reads of the same acquisition, once computed whole
    and once deep (tile by tile, where read), give the same bytes — normal and averaged."""
    for extra in ([], [":ACQuire:AVERage ON", ":ACQuire:COUNt 4"]):
        commands = extra + RECORD + READS
        whole, whole_records = _replies(UXR, commands)
        assert whole_records[1].deep is None and whole_records[1].points == 100_000
        monkeypatch.setattr(scope_module, "EAGER_POINTS", 1000)
        deep, deep_records = _replies(UXR, commands)
        monkeypatch.undo()
        assert deep_records[1].deep is not None and deep_records[1].points == 100_000
        assert deep == whole
        assert deep[-1] == b"0\n"  # no error
        assert (
            len(deep[READS.index(":WAVeform:DATA? 12345,6789") + len(extra) + len(RECORD)])
            == 2 + 5 + 2 * 6789 + 1
        )


def test_peak_detect_and_high_resolution_tiles_follow_the_whole_record(monkeypatch):
    """Peak detect's intervals and the high-resolution boxcar reach across tile edges: a deep
    record's samples are the whole record's (peak detect exactly; the boxcar to rounding, its
    running sum is the tile's own — such a record is deep only beyond MAX_FRAME)."""
    for mode, exact in ((":ACQuire:TYPE PEAK", True), (":ACQuire:TYPE ERES,2.0", False)):
        commands = [
            ":ACQuire:MDEPth 100k",
            ":TIMebase:SCALe 1E-3",
            mode,
            ":WAVeform:POINt 10",
            ":WAVeform:DATA?",
        ]
        _, whole = _replies(SIGLENT, commands)
        monkeypatch.setattr(scope_module, "EAGER_POINTS", 1000)
        monkeypatch.setattr(scope_module, "MAX_FRAME", 1000)
        monkeypatch.setattr(scope_record, "TILE", 1 << 10)  # many tile edges in the record
        _, deep = _replies(SIGLENT, commands)
        monkeypatch.undo()
        a, b = whole[1], deep[1]
        assert a.deep is None and b.deep is not None and b.deep.tile < b.points
        got = np.concatenate(list(b.chunks()))
        if exact:
            assert np.array_equal(got, a.volts)
        else:
            assert np.max(np.abs(got - a.volts)) < 1e-12 * a.scale


def test_a_deep_record_is_computed_where_it_is_read_and_keeps_what_was_acquired(monkeypatch):
    """:DIGitize computes nothing; a read computes its tiles only; reading a sample again, or
    through another read, gives the same value; later settings do not change the record; a read
    beyond what one read may hold is refused, in parts it is not."""
    monkeypatch.setattr(scope_module, "EAGER_POINTS", 1000)
    computed: list[int] = []
    frame = scope_record.AdcView.frame
    monkeypatch.setattr(scope_record.AdcView, "frame", lambda v, r: computed.append(r.count) or frame(v, r))
    session = open_bench(UXR, clock="command", seed=3, idle="off")
    for command in [*RECORD, ":WAVeform:FORMat WORD"]:
        session_query(session, "scope", command)
    rec = session.instruments["scope"].components["acq"].records[1]
    tile = rec.deep.tile
    assert computed == [] and rec.points == 100_000  # acquired: nothing computed yet
    session.write("scope", b":WAVeform:DATA? 50000,100\n")
    part = session.read("scope")
    assert sum(computed) <= 2 * tile  # the tiles under 100 points, not the record
    again = rec.span(50000, 50100).copy()
    session_query(session, "scope", ":CHANnel1:SCALe 0.5")  # a later setting
    session.write("awg", b"SOURce1:FREQuency 2E6\n")  # and a later signal
    session.write("scope", b":WAVeform:DATA? 50000,100\n")
    assert session.read("scope") == part
    rec.deep._tiles.clear()  # dropped tiles are computed again, from what was acquired
    assert np.array_equal(rec.take(np.arange(50000, 50100)), again)
    assert np.array_equal(rec.span(49990, 50110)[10:110], again)  # another read reaches the same samples
    monkeypatch.setattr(scope_record, "MAX_FRAME", 50_000)
    with raises_with(ModelLimit, "read the record in parts"):
        rec.span(0, rec.points)
    assert session_query(session, "scope", ":WAVeform:DATA?") is None  # the whole record at once
    assert session_query(session, "scope", ":SYSTem:ERRor?") == "-221"  # beyond the model
    session.write("scope", b":WAVeform:DATA? 0,50000\n")
    assert len(session.read("scope") or b"") == 2 + 6 + 2 * 50000 + 1  # in parts it reads
    assert session_query(session, "scope", ":WAVeform:DATA? 100000,10") is None  # past the record
    assert session_query(session, "scope", ":SYSTem:ERRor?") == "-222"
    session.close()


def test_an_average_beyond_the_fan_out_is_a_deep_record_read_in_parts(monkeypatch):
    """#986 → #1012: averages x record samples beyond ``MAX_REQUEST_FANOUT`` no longer refuse the
    acquisition: the record is deep, nothing is computed, and each read within the fan-out is
    answered; one that would ask beyond it (the whole record, a measurement on it) is refused
    before the ADC computes anything."""
    session = open_bench(DSOX, clock="command", seed=1, idle="off")
    scope = session.instruments["scope"].components
    points = scope["acq"]._timing()[0]
    for module in (session_module, scope_module, scope_record):
        monkeypatch.setattr(module, "MAX_REQUEST_FANOUT", 4 * points)
    computed: list[int] = []
    frame = scope_record.AdcView.frame
    monkeypatch.setattr(scope_record.AdcView, "frame", lambda v, r: computed.append(r.count) or frame(v, r))
    for command in (":ACQuire:TYPE AVERage", ":ACQuire:COUNt 5", ":DIGitize CHANnel1"):
        session_query(session, "scope", command)
    assert session_query(session, "scope", ":SYSTem:ERRor?") == '+0,"No error"'  # done (pending, D47)
    rec = scope["acq"].records[1]
    assert (rec.deep is not None, computed) == (True, [])
    assert session_query(session, "scope", ":MEASure:VPP? CHANnel1") is None  # needs every sample
    assert session_query(session, "scope", ":SYSTem:ERRor?") == '-221,"Settings conflict"'
    assert computed == []
    part = rec.span(0, 1000)
    assert part.size == 1000 and len(computed) == 5 * -(-1000 // rec.deep.tile)  # 5 acquisitions a tile
    session.close()


def test_the_screen_of_a_deep_record_is_an_envelope_of_draws_across_each_pixel(monkeypatch):
    """A deep record too costly to compute for the screen is not computed: per pixel, the minimum
    and maximum of samples drawn across it, the same draws every time, within the record's own
    extremes. One cheap enough is every sample's own per-pixel extremes."""
    monkeypatch.setattr(scope_module, "EAGER_POINTS", 1000)
    _, records = _replies(UXR, RECORD)
    rec = records[1]
    volts = np.concatenate(list(rec.chunks()))
    exact = rec.envelope(1000)  # 100 000 samples: within the screen's budget, computed
    assert exact[0::2] == volts.reshape(1000, 100).min(axis=1).tolist()
    assert exact[1::2] == volts.reshape(1000, 100).max(axis=1).tolist()
    monkeypatch.setattr(scope_record, "ENVELOPE_BUDGET", 50_000)  # now too costly: drawn
    envelope = rec.envelope(200, ("screen", 1))
    assert len(envelope) == 400 and envelope == rec.envelope(200, ("screen", 1))
    lows, highs = np.array(envelope[0::2]), np.array(envelope[1::2])
    pixels = volts.reshape(200, 500)  # each draw is a sample of its own pixel
    assert (
        (lows >= pixels.min(axis=1)).all() and (highs <= pixels.max(axis=1)).all() and (highs >= lows).all()
    )
    # 500 samples a pixel, 20 periods of the 10 MHz sine: 64 draws reach near its peaks
    assert np.median(highs - lows) > 0.8 * (volts.max() - volts.min())


def test_two_gigapoints_are_acquired_and_read_in_parts_within_a_bounded_memory():
    """The UXR1104B with its 2 Gpts option: the acquisition computes nothing, a 1 Mpts part of the
    1.28 Gpts record is read holding about that much in memory, the end of the record reads, and
    the whole record at once is refused (read it in parts)."""
    session = open_bench(UXR_1104B, clock="command", seed=3, idle="off")
    for command in (
        ":ACQuire:POINts 2000000000",
        ":TIMebase:SCALe 1E-3",
        ":DIGitize",
        ":WAVeform:FORMat WORD",
    ):
        session_query(session, "scope", command)
    assert session_query(session, "scope", ":WAVeform:POINts?") == "1280000000"  # 128 GSa/s x 10 ms
    tracemalloc.start()
    data = session.write("scope", b":WAVeform:DATA? 640000000,1000000\n") or session.read("scope")
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert data is not None and len(data) == 2 + 7 + 2 * 1_000_000 + 1
    assert peak < 200e6  # the part, its codes and its tiles: not the record (10 GB as float64)
    codes = np.frombuffer(data[9:-1], dtype=">i2") >> 6
    assert 50 < codes.max() - codes.min() < 1024  # the 400 mVpp sine at 100 mV/div, 128 codes/div
    session.write("scope", b":WAVeform:DATA? 1279999990,100\n")  # cut to the record's last 10 points
    assert len(session.read("scope") or b"") == 2 + 2 + 20 + 1
    assert session_query(session, "scope", ":WAVeform:DATA?") is None
    assert session_query(session, "scope", ":SYSTem:ERRor?") == "-221"  # the whole record at once
    session.close()


@pytest.mark.load_sensitive
def test_a_million_points_of_a_two_gigapoint_record_take_under_two_seconds():
    """The speed target of #1012: acquisition and a read of up to 1 Mpts within 2 s of wall time,
    whatever the memory depth."""
    import time

    session = open_bench(UXR_1104B, clock="command", seed=3, idle="off")
    session_query(session, "scope", ":ACQuire:POINts 2000000000;:TIMebase:SCALe 1E-3;:WAVeform:FORMat WORD")
    start = time.perf_counter()
    session_query(session, "scope", ":DIGitize")
    data = session.write("scope", b":WAVeform:DATA? 0,1000000\n") or session.read("scope")
    elapsed = time.perf_counter() - start
    assert data is not None and elapsed < 2.0
    session.close()
