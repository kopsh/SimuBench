"""Counter time stamps as the 53230A takes them (#385): a scale factor, then the intervals between
stamped edges, through the trigger model — on sb-counter-1 fed by a sine of known frequency."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition
from simubench.scpi import values as V
from simubench.scpi.errors import ScpiError

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"


def _defs(tmp_path: Path) -> Path:
    """sb-counter-1 with the time-stamp function and its rate (patched here, not in the validation
    instrument: its panel shows one readout per function)."""
    d = yaml.safe_load((DEFS / "sb-counter-1.yaml").read_text("utf-8"))
    d["state"]["function"]["options"].append("TSTamp")
    d["state"]["timestamp.rate"] = {
        "type": "float",
        "unit": "HZ",
        "min": 1.0e4,
        "max": 1.0e6,
        "default": 1.0e6,
    }
    for c in d["components"]:
        if c["name"] == "core":
            c["params"]["timestamp_rate"] = "$timestamp.rate"
    d["commands"].append({"header": ":TSTamp:RATE", "state": "timestamp.rate"})
    (tmp_path / "sb-counter-1.yaml").write_text(yaml.safe_dump(d, allow_unicode=True), "utf-8")
    return tmp_path


def _session(frequency: float, defs: Path):
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "stamps",
            "instruments": {
                "cnt": {"model": "sb-counter-1"},
                "awg": {
                    "model": "sb-awg-2",
                    "state": {
                        "ch1.function": "SINusoid",
                        "ch1.frequency": frequency,
                        "ch1.amplitude": 2.0,
                        "ch1.load": math.inf,
                        "ch1.output": True,
                    },
                },
            },
            "wires": [{"from": "awg.OUT1", "to": "cnt.CH1"}],
            # idle off (D47): with the default 'wall', idle real time of >= 10 ms between commands
            # moves virtual time, so a process's first, slower session stamped other edges and
            # read other single-shot noise than the sessions after it (issue #409).
            "run": {"seed": 3, "clock": "command", "idle": "off"},
        }
    )
    return open_bench(bench, definition_dirs=[defs, DEFS])


def _read(session, rate: float, count: int) -> list[float]:
    for text in (":FUNC TSTamp", f":TSTamp:RATE {rate}", f":SAMP:COUN {count + 1}"):
        session.write("cnt", (text + "\n").encode())
    session.write("cnt", b":READ?\n")
    return [float(v) for v in session.read("cnt").decode().split(",")]


@pytest.mark.parametrize(
    ("frequency", "rate", "scale"),
    [
        (2.0e5, 1.0e6, 1),  # below the rate: every edge, the input period itself
        (1.0e7, 1.0e6, 11),  # the guide's example: 10 MHz at 1 MHz gives a scale factor of 11
        (2.5e6, 1.0e6, 3),
    ],
)
def test_the_scale_factor_and_the_intervals(tmp_path, frequency, rate, scale):
    session = _session(frequency, _defs(tmp_path))
    try:
        values = _read(session, rate, 8)
        assert values[0] == scale
        assert len(values) == 9
        for v in values[1:]:  # scale x period, within the 20 ps single-shot resolution
            assert v == pytest.approx(scale / frequency, abs=1e-10)
        assert values[1] / values[0] == pytest.approx(1 / frequency, rel=1e-3)  # the guide's division
    finally:
        session.close()


def test_a_count_in_parentheses_is_a_number():
    assert V.parse_int(b"(200)") == 200
    assert V.parse_float(b"( 2.5E3 )") == 2500.0
    with pytest.raises(ScpiError) as e:  # a channel list is not a number
        V.parse_float(b"(@1)")
    assert e.value.code == -104


_FRESH_PROCESS = """
import json, sys, tempfile
from pathlib import Path
sys.path[:0] = [sys.argv[1]]
from test_counter_timestamps import _defs, _read, _session
s = _session(1.0e6, _defs(Path(tempfile.mkdtemp())))
try:
    print(json.dumps(_read(s, 1.0e6, 3)))
finally:
    s.close()
"""


def test_the_first_session_of_a_fresh_process_reads_the_same_stamps():
    """Issue #409: same seed, same commands — the first session in each of two new processes
    reads the same values, value for value (the in-process tests cannot see this: by then the
    imports are warm)."""
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _FRESH_PROCESS, str(ROOT / "tests")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
        )
        for _ in range(2)
    ]
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), [err for _, err in outs]
    first, second = (json.loads(out.strip().splitlines()[-1]) for out, _ in outs)
    assert first[0] == 2.0
    assert first == second
