"""Keysight M8194A: the guide's programming recommendations, run against the example bench.

The manual cases (tests/models/keysight-m8194a/cases.yaml) check command by command; this runs the
real-instrument-style script examples/scripts/keysight_m8194a_levels.py (``*RST``, levels, outputs on,
``:INIT:IMM`` last) against the bench with a UXR0334B on channels 1 and 2, and checks what a transcript
cannot see: the DC level each channel puts out at the scope's 50 ohm input.
"""

from pathlib import Path

import numpy as np
import pytest

from helpers import load_script, run_manual_script, session_query
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight-m8194a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_m8194a_levels.py"

script = load_script(SCRIPT)


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(script, ["awg", "scope"], BENCH, tmp_path, monkeypatch, capsys, rm_wrap=False)
    assert out[0] == "Keysight Technologies,M8194A,MY12345678,0.1.23.0-1"
    assert out[1] == "catalog: 1,128"  # the segment the script loaded
    assert out[2].startswith("ch1: 4.0000")  # :VOLTage1 0.4, :VOLTage1:OFFSet 0.3
    assert out[3].startswith("ch2: 5.0000")  # :VOLTage2:HIGH 0.5, :LOW 0.1
    assert out[4] == "scope: 0.3 V, 0.3 V"  # the mean of a zero table: ch1 offset, ch2 (high + low) / 2
    assert out[-1] == '0,"No error"'


def test_a_loaded_segment_plays_at_the_sample_frequency(tmp_path):
    """#879: a segment of DAC codes loops at the sample frequency — the fundamental is rate / length,
    the zero-order-hold DAC weights it by sinc, the codes are 8 bits (127 is full scale) and a
    deleted segment leaves the offset alone."""
    s = open_bench(BENCH, idle="off")
    try:

        def q(text):
            return session_query(s, "awg", text, default="")

        synth = s.instruments["awg"].components["ch1.synth"]
        # -- no segment: no signal
        assert synth.output("out").amp.size == 0
        # -- a square of 128 samples, half at +127 and half at -127, at 120 GSa/s
        q(":TRAC1:DEF 1,128,0")
        block = bytes(np.array([127] * 64 + [-127] * 64, dtype=np.int8))
        s.write("awg", b":TRAC1:DATA 1,0,#3128" + block + b"\n")
        assert q(":SYST:ERR?") == '0,"No error"'
        sig = synth.output("out")
        assert sig.freq[0] == pytest.approx(120e9 / 128)
        # a square of peak 0.5 V (scale 0.5): the fundamental is 4/pi x 0.5, times the DAC's sinc roll-off
        assert abs(sig.amp[0]) == pytest.approx(4 / np.pi * 0.5 * np.sinc(1 / 128), rel=0.02)
        assert sig.amp.size == 4096  # only the odd harmonics of a square, up to max_lines (8192)
        # -- a new sample frequency moves the line; the segment stays
        q(":FREQ:RAST 1.15E11")
        assert synth.output("out").freq[0] == pytest.approx(115e9 / 128)
        # -- deleting the segment leaves nothing to play
        q(":TRAC1:DEL:ALL")
        assert synth.output("out").amp.size == 0
    finally:
        s.close()
