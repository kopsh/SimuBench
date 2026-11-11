"""The oscilloscope acquisition's gold standard (#947, signal-chain.md 4.3): recorded before the
acquisition moves onto sample streams (D67, #950), which must keep every sample — its noise draw
and its quantized code — bit for bit.

Each case opens one bench and walks a fixed command sequence that reads waveforms through every
acquisition path its definition has: the processing stages (normal, average, peak detect, high
resolution), triggered and untriggered (auto, forced), one and several channels, both noise keys
(the acquisition number; the sample instants, on the SDS2104X Plus), and one frame of the live
screen. ``tests/data/scope_stream_gold.json`` holds, per step, the SHA-256 of the reply's bytes and
the virtual time the command consumed — hashes, not text: a waveform block is megabytes.

The sequence is walked once per case for the whole module (the bench opens in a module fixture,
#965); each test then compares one group of the recorded steps — the processing modes, the
trigger scenarios, the several channels — and reports every differing step of its group at once,
not just the first. The group ranges are ``GROUPS``; the screen frame (the last row) has its own
test.

Record again (only when a change to the replies is intended and reviewed):
``uv run python tests/test_scope_stream_migration.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from helpers import warm_up
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"
BENCHES = ROOT / "examples" / "benches"
GOLD_FILE = ROOT / "tests" / "data" / "scope_stream_gold.json"
GOLD: dict[str, list] = json.loads(GOLD_FILE.read_text()) if GOLD_FILE.exists() else {}


def _sb_bench(model: str) -> BenchDefinition:
    """A 1 kHz sine on CH1 and a 3 kHz square on CH2 of a validation scope."""
    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "scope-stream-gold",
            "instruments": {
                "awg": {
                    "model": "sb-awg-2",
                    "state": {
                        "ch1.frequency": 1000.0,
                        "ch1.amplitude": 2.0,
                        "ch1.output": True,
                        "ch2.function": "SQUare",
                        "ch2.frequency": 3000.0,
                        "ch2.amplitude": 1.0,
                        "ch2.output": True,
                    },
                },
                "scope": {
                    "model": model,
                    "state": {"timebase.scale": 2e-4, "ch1.scale": 0.5, "ch2.scale": 0.5},
                },
            },
            "wires": [{"from": "awg.OUT1", "to": "scope.CH1"}, {"from": "awg.OUT2", "to": "scope.CH2"}],
        }
    )


def _example(name: str) -> BenchDefinition:
    return BenchDefinition.model_validate(yaml.safe_load((BENCHES / name).read_text(encoding="utf-8")))


_SB = [
    ":DIGitize CHANnel1",
    ":WAVeform:SOURce CHANnel1",
    ":WAVeform:PREamble?",
    ":WAVeform:DATA?",
    ":WAVeform:FORMat WORD",
    ":WAVeform:DATA?",
    ":WAVeform:FORMat ASCii",
    ":WAVeform:DATA?",
    ":DIGitize CHANnel1,CHANnel2",  # several channels in one acquisition
    ":WAVeform:SOURce CHANnel2",
    ":WAVeform:FORMat BYTE",
    ":WAVeform:PREamble?",
    ":WAVeform:DATA?",
    ":MEASure:VPP? CHANnel1",
    ":MEASure:FREQuency? CHANnel2",
    ":TIMebase:SCALe 1E-3",
    ":TRIGger:LEVel 0.4",
    ":TRIGger:SLOPe NEGative",
    ":DIGitize CHANnel1",
    ":WAVeform:SOURce CHANnel1",
    ":WAVeform:DATA?",
    ":TRIGger:LEVel 5",  # never crossed: auto sweep acquires untriggered
    ":TRIGger:SWEep AUTO",
    ":DIGitize CHANnel1",
    ":WAVeform:PREamble?",
    ":WAVeform:DATA?",
    ":SYSTem:ERRor?",
]

_DSOX_READ = [":WAVeform:PREamble?", ":WAVeform:DATA?"]
_DSOX = [
    ":WAVeform:SOURce CHANnel1",
    ":WAVeform:FORMat BYTE",
    ":DIGitize CHANnel1",
    *_DSOX_READ,
    ":DIGitize CHANnel1",  # the next acquisition number: other noise
    ":WAVeform:DATA?",
    ":WAVeform:FORMat WORD",
    ":WAVeform:UNSigned 1",
    ":WAVeform:BYTeorder MSBFirst",
    ":WAVeform:POINts:MODE MAXimum",
    ":WAVeform:POINts 8000",
    ":DIGitize CHANnel1",
    *_DSOX_READ,
    ":WAVeform:POINts:MODE NORMal",
    ":WAVeform:POINts 1000",
    ":ACQuire:TYPE AVERage",
    ":ACQuire:COUNt 8",
    ":DIGitize CHANnel1",
    *_DSOX_READ,
    ":ACQuire:TYPE PEAK",
    ":TIMebase:SCALe 5E-3",
    ":DIGitize CHANnel1",
    *_DSOX_READ,
    ":ACQuire:TYPE HRESolution",
    ":DIGitize CHANnel1",
    *_DSOX_READ,
    ":ACQuire:TYPE NORMal",
    ":TIMebase:SCALe 2E-4",
    ":CHANnel2:DISPlay 1",
    ":DIGitize CHANnel1,CHANnel2",  # several channels (CH2 is open: its own noise)
    ":WAVeform:SOURce CHANnel2",
    *_DSOX_READ,
    ":WAVeform:SOURce CHANnel1",
    ":MEASure:VPP? CHANnel1",
    ":MEASure:VRMS? CHANnel1",
    ":TRIGger:EDGE:LEVel 3",  # never crossed
    ":TRIGger:SWEep AUTO",
    ":DIGitize CHANnel1",
    *_DSOX_READ,
    ":TRIGger:EDGE:LEVel 0",
    ":RUN",  # running: every read acquires again
    ":WAVeform:DATA?",
    ":WAVeform:DATA?",
    ":STOP",
    ":SYSTem:ERRor?",
]

_SDS_READ = [":WAVeform:STARt 0", ":WAVeform:PREamble?", ":WAVeform:DATA?"]
_SDS = [
    "awg> C1:OUTP ON",  # the example script reads the input's noise; the gold standard wants a signal too
    ":TRIGger:MODE SINGle",
    *_SDS_READ,
    ":CHANnel1:SCALe 0.5",
    ":WAVeform:SOURce C1",
    # This model's commands take no virtual time and its noise is keyed by the sample instants
    # (noise_key: time): the clock has to move for another acquisition to differ.
    ":TRIGger:MODE AUTO",  # running: every read acquires again
    "advance> 0.0137",
    *_SDS_READ,
    "advance> 0.0137",
    ":WAVeform:DATA?",
    ":ACQuire:TYPE AVERage,16",
    "advance> 0.0137",
    *_SDS_READ,
    ":ACQuire:TYPE PEAK",
    ":TIMebase:SCALe 1E-2",
    "advance> 0.0137",
    *_SDS_READ,
    ":ACQuire:TYPE ERES,2.0",
    "advance> 0.0137",
    *_SDS_READ,
    ":ACQuire:TYPE NORMal",
    ":TIMebase:SCALe 1E-3",
    ":ACQuire:RESolution 10Bits",
    ":WAVeform:WIDTh WORD",
    "advance> 0.0137",
    *_SDS_READ,
    ":ACQuire:RESolution 8Bits",
    ":WAVeform:WIDTh BYTE",
    "advance> 0.0137",
    ":TRIGger:MODE SINGle",
    *_SDS_READ,
    ":TRIGger:EDGE:LEVel 3",  # never crossed
    "advance> 0.0137",
    ":TRIGger:MODE FTRIG",  # a forced frame
    *_SDS_READ,
    ":TRIGger:EDGE:LEVel 0",
    ":TRIGger:STOP",
    ":SYSTem:ERRor?",
]

_UXR_READ = [":WAVeform:PREamble?", ":WAVeform:DATA?"]
_UXR = [
    ":SYSTem:HEADer OFF",
    ":WAVeform:SOURce CHANnel1",
    ":WAVeform:FORMat WORD",
    ":WAVeform:BYTeorder LSBFirst",
    ":WAVeform:STReaming ON",
    ":ACQuire:POINts 4000",
    ":DIGitize CHANnel1",
    *_UXR_READ,
    ":DIGitize CHANnel1",
    ":WAVeform:DATA?",
    ":ACQuire:AVERage ON",
    ":ACQuire:COUNt 4",
    ":DIGitize CHANnel1",
    *_UXR_READ,
    ":ACQuire:AVERage OFF",
    ":ACQuire:BANDwidth 1E9",
    ":DIGitize CHANnel1",
    *_UXR_READ,
    ":ACQuire:RESPonse GAUSsianmag",
    ":DIGitize CHANnel1",
    ":WAVeform:DATA?",
    ":WAVeform:FORMat BYTE",
    ":DIGitize CHANnel1",
    *_UXR_READ,
    ":SYSTem:ERRor?",
]

_LECROY_DATA = 'C1:INSPECT? "DATA_ARRAY_1",FLOAT'
_LECROY = [
    "TRIG_MODE SINGLE",
    "WAIT",
    'C1:INSPECT? "WAVEDESC"',
    _LECROY_DATA,
    "TRIG_MODE AUTO",  # running: a read starts a new acquisition
    _LECROY_DATA,
    "advance> 0.01",
    _LECROY_DATA,
    "C1:PARAMETER_VALUE? PKPK",
    "TIME_DIV 1E-3",
    'C1:INSPECT? "WAVEDESC"',
    _LECROY_DATA,
    "C1:TRIG_LEVEL 3",  # never crossed: the auto mode acquires untriggered
    _LECROY_DATA,
    "ARM_ACQUISITION",
    "FORCE_TRIGGER",
    _LECROY_DATA,
    "C1:TRIG_LEVEL 0",
    "TRIG_MODE STOP",
]

#: case -> (the bench, where its definitions live, the command sequence: for "scope", or for the
#: instrument named before "> "; "advance> <seconds>" moves the virtual clock).
CASES: dict[str, tuple] = {
    "sb-dso-4": (lambda: _sb_bench("sb-dso-4"), [DEFS], _SB),
    "sb-dso-2x": (lambda: _sb_bench("sb-dso-2x"), [DEFS], _SB),
    "keysight-dsox2024a": (lambda: _example("keysight-dsox2024a.bench.yaml"), None, _DSOX),
    "siglent-sds2104xplus": (lambda: _example("siglent_scope_rc.bench.yaml"), None, _SDS),
    "keysight-uxr0334b": (lambda: _example("keysight-uxr0334b.bench.yaml"), None, _UXR),
    "lecroy-wavepro804hd": (lambda: _example("lecroy-wavepro804hd.bench.yaml"), None, _LECROY),
}

#: The steps of one case split into groups (D49: one behaviour per test, #965): group name ->
#: inclusive (first step, last step) indexes into the command sequence, contiguous and covering
#: (``test_the_groups_partition_every_sequence`` guards that). The groups are ranges of one
#: recorded walk, not independent scenarios — a group's steps may rely on state an earlier group
#: left behind, and one group may carry another's setup (the SDS2104X's clock advances, the
#: WavePro's trigger mode). The live screen frame (the last row) has its own test.
#: ``sb-dso-4`` and ``sb-dso-2x`` share one sequence.
_SB_GROUPS = {
    "processing": (0, 7),  # one acquisition read in the three transfer formats
    "channels": (8, 14),  # both channels in one acquisition, CH2's read, a measurement per channel
    "trigger": (15, 26),  # timebase, level and slope, a level never crossed (auto sweep), errors
}
GROUPS: dict[str, dict[str, tuple[int, int]]] = {
    "sb-dso-4": _SB_GROUPS,
    "sb-dso-2x": _SB_GROUPS,
    "keysight-dsox2024a": {
        "processing": (0, 30),  # normal, the acquisition number's noise, WORD/unsigned/points, average, peak, high resolution
        "channels": (31, 40),  # CH2 opens, both channels acquired, its read and the measurements
        "trigger": (41, 51),  # a level never crossed (auto sweep), running reads, stop
    },
    "siglent-sds2104xplus": {
        "processing": (0, 29),  # single, running, average, peak, enhanced resolution
        "resolution": (30, 44),  # normal, 10 and 8 bits, word and byte widths (read under single mode)
        "trigger": (45, 53),  # a level never crossed, a forced frame, stop
    },
    "keysight-uxr0334b": {
        "processing": (0, 28),  # word/byte, points, average, bandwidth, both responses; no trigger or multi-channel steps
    },
    "lecroy-wavepro804hd": {
        "processing": (0, 11),  # single and running acquisitions, the wave descriptor, a measurement, the timebase
        "trigger": (12, 18),  # a level never crossed, arm and force, stop
    },
}


def _digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _round_frame(volts) -> bytes:
    """One screen frame's volts as bytes, rounded to 1 nV: an ideal converter (sb-dso-4) passes
    the analytic waveform on unquantized, and the last bits of a sine differ between platforms'
    math libraries (Windows CI, #964). ``+ 0.0`` turns a value that rounded to ``-0.0`` (its sign
    bit survives ``np.round``) into ``+0.0``, so a sample landing on a zero crossing hashes the
    same on every platform (#970)."""
    return (np.round(np.asarray(volts, dtype="<f8"), 9) + 0.0).astype("<f8").tobytes()


def walk(case: str, show: bool = False) -> list[list]:
    """[command, reply digest, ns consumed] for every step, then the live screen's frame."""
    bench, dirs, steps = CASES[case]
    s = open_bench(bench(), clock="command", seed=7, idle="off", definition_dirs=dirs)
    try:
        assert warm_up(s, "scope")[0] is not None
        rows = []
        for command in steps:
            before = s.now_ns
            instrument, _, text = command.rpartition("> ")
            reply = None
            if instrument == "advance":
                s.advance(float(text))
            else:
                s.write(instrument or "scope", (text + "\n").encode())
                reply = s.read(instrument or "scope")
            rows.append([command, _digest(reply), s.now_ns - before])
            if show:
                print(f"  {command:42} {len(reply) if reply is not None else '-':>9} {(reply or b'')[:48]!r}")
        # One frame of the live screen (the Web's scope screen): its noise has its own key.
        acq = s.instruments["scope"].components["acq"]
        with s.view():
            shown, _, triggered = acq.compute(acq.displayed(), frame=1)
        volts = b"".join(_round_frame(shown[n].volts) for n in sorted(shown))
        rows.append(["<screen frame 1>", _digest(volts), int(triggered)])
        if show:
            print(f"  screen: channels {sorted(shown)}, {len(volts) // 8} samples, triggered {triggered}")
        return rows
    finally:
        s.close()


def test_the_screen_frame_digest_does_not_care_about_zero_sign():
    """A sample on a zero crossing rounds to -0.0 or 0.0 depending on the platform's last bit; the
    digest must be identical either way (#970)."""
    assert _round_frame([-1e-12]) == _round_frame([1e-12])
    assert _round_frame([-1e-12]) == _round_frame([0.0])


@pytest.fixture(scope="module")
def recorded() -> dict[str, list[list]]:
    """Every case's recorded rows: each bench opens and its sequence walks once for the whole
    module (D49's environment reuse, #965); the tests below only compare their group of steps,
    so each call is a comparison and the walks' seconds stay in setup."""
    for case in CASES:
        assert GOLD.get(case), f"no gold standard for {case}: record it (see the module docstring)"
    return {case: walk(case) for case in CASES}


def _cases_with(group: str) -> list[str]:
    return [case for case in CASES if group in GROUPS[case]]


def _compare_group(rows: list[list], case: str, group: str) -> None:
    """Every differing step of the group at once, not just the first (#965)."""
    lo, hi = GROUPS[case][group]
    got, expected = rows[lo : hi + 1], GOLD[case][lo : hi + 1]
    differing = [
        f"step {lo + i}: got  {g}\n          want {w}"
        for i, (g, w) in enumerate(zip(got, expected))
        if g != w
    ]
    if len(got) != len(expected):
        differing.append(f"the group has {len(got)} steps, the gold standard {len(expected)}")
    assert not differing, f"{case} [{group}]: {len(differing)} differ\n" + "\n".join(differing)


def test_the_groups_partition_every_sequence():
    """The ranges cover each sequence exactly once, in order: they cannot drift when a sequence
    is edited (#965)."""
    for case, (_, _, steps) in CASES.items():
        covered = sorted(i for bounds in GROUPS[case].values() for i in range(bounds[0], bounds[1] + 1))
        assert covered == list(range(len(steps))), case


@pytest.mark.parametrize("case", _cases_with("processing"))
def test_the_processing_steps_are_reproduced(recorded, case):
    _compare_group(recorded[case], case, "processing")


@pytest.mark.parametrize("case", _cases_with("channels"))
def test_the_multi_channel_steps_are_reproduced(recorded, case):
    _compare_group(recorded[case], case, "channels")


@pytest.mark.parametrize("case", _cases_with("resolution"))
def test_the_resolution_steps_are_reproduced(recorded, case):
    _compare_group(recorded[case], case, "resolution")


@pytest.mark.parametrize("case", _cases_with("trigger"))
def test_the_trigger_steps_are_reproduced(recorded, case):
    _compare_group(recorded[case], case, "trigger")


@pytest.mark.parametrize("case", list(CASES))
def test_the_screen_frame_is_reproduced(recorded, case):
    """The live screen's one frame: its noise has its own key, its volts are rounded (#970)."""
    got, expected = recorded[case][-1], GOLD[case][-1]
    assert got == expected, f"{case} screen frame:\n  got  {got}\n  want {expected}"


if __name__ == "__main__":
    recorded = {}
    for name in CASES:
        print(name)
        recorded[name] = walk(name, show=True)
    GOLD_FILE.write_text(json.dumps(recorded, indent=1) + "\n")
