"""Keysight M8194A: the guide's programming recommendations, run against the example bench.

The manual cases (tests/models/keysight-m8194a/cases.yaml) check command by command; this runs the
real-instrument-style script examples/scripts/keysight_m8194a_levels.py (``*RST``, levels, outputs on,
``:INIT:IMM`` last) against the bench with a UXR0334B on channels 1 and 2, and checks what a transcript
cannot see: the DC level each channel puts out at the scope's 50 ohm input.
"""

from pathlib import Path

from helpers import load_script, run_manual_script

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight-m8194a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_m8194a_levels.py"

script = load_script(SCRIPT)


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(script, ["awg", "scope"], BENCH, tmp_path, monkeypatch, capsys, rm_wrap=False)
    assert out[0] == "Keysight Technologies,M8194A,MY12345678,0.1.23.0-1"
    assert out[1].startswith("ch1: 4.0000")  # :VOLTage1 0.4, :VOLTage1:OFFSet 0.3
    assert out[2].startswith("ch2: 5.0000")  # :VOLTage2:HIGH 0.5, :LOW 0.1
    assert out[3] == "scope: 0.3 V, 0.3 V"  # the mean of a zero table: ch1 offset, ch2 (high + low) / 2
    assert out[-1] == '0,"No error"'
