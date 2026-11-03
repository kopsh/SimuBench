"""Counter time stamps as the 53230A takes them (#385): a scale factor, then the intervals between
stamped edges, through the trigger model — on sb-counter-1 fed by a sine of known frequency."""

from __future__ import annotations

import math
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
            "run": {"seed": 3, "clock": "command"},
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
