"""High and low levels on ``amplitude_level`` (#424): the Trueform's VOLTage:HIGH/LOW. Setting one
level keeps the other, so the amplitude and offset both change; queries give offset ± Vpp/2.
``sb-awg-2`` is patched here with the two commands (not a new validation instrument)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"


@pytest.fixture
def awg(tmp_path):
    d = yaml.safe_load((DEFS / "sb-awg-2.yaml").read_text("utf-8"))
    d["channel_state"]["unit"] = {"type": "enum", "options": ["VPP", "VRMS"], "default": "VPP"}
    for c in d["channel_components"]:
        if c["name"] == "level":
            c["params"]["unit"] = "$ch.unit"
    d["commands"] += [
        {"header": "[:SOURce<ch>]:VOLTage:UNIT", "state": "ch.unit"},
        {
            "header": "[:SOURce<ch>]:VOLTage:HIGH",
            "op": "ch.level.high",
            "params": [{"name": "value", "type": "float", "unit": "V", "optional": True}],
        },
        {
            "header": "[:SOURce<ch>]:VOLTage:LOW",
            "op": "ch.level.low",
            "params": [{"name": "value", "type": "float", "unit": "V", "optional": True}],
        },
    ]
    (tmp_path / "sb-awg-2.yaml").write_text(yaml.safe_dump(d, allow_unicode=True), "utf-8")
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/2", "name": "b", "instruments": {"awg": {"model": "sb-awg-2"}}}
    )
    session = open_bench(bench, definition_dirs=[tmp_path, DEFS])

    def q(text: str) -> str:
        session.write("awg", (text + "\n").encode())
        reply = session.read("awg")
        return reply.decode().strip() if reply else ""

    yield q
    session.close()


def test_levels_follow_amplitude_and_offset(awg):
    awg("VOLT 2;:VOLT:OFFS 0.5")
    assert float(awg("VOLT:HIGH?")) == pytest.approx(1.5)
    assert float(awg("VOLT:LOW?")) == pytest.approx(-0.5)


def test_setting_one_level_keeps_the_other(awg):
    awg("VOLT 2;:VOLT:OFFS 0")
    awg("VOLT:HIGH 3")  # low stays -1: 4 Vpp around +1 V
    assert float(awg("VOLT?")) == pytest.approx(4.0)
    assert float(awg("VOLT:OFFS?")) == pytest.approx(1.0)
    awg("VOLT:LOW 0")  # high stays 3
    assert float(awg("VOLT?")) == pytest.approx(3.0)
    assert float(awg("VOLT:OFFS?")) == pytest.approx(1.5)
    assert awg("SYST:ERR?").startswith("0") or awg("SYST:ERR?").startswith("+0")


def test_a_high_level_not_above_the_low_one_is_refused(awg):
    awg("VOLT 2;:VOLT:OFFS 0")
    awg("VOLT:HIGH -2")
    assert awg("SYST:ERR?").startswith("-222")
    assert float(awg("VOLT?")) == pytest.approx(2.0)


def test_the_amplitude_is_written_in_its_unit(awg):
    awg("VOLT:UNIT VRMS")
    awg("VOLT:HIGH 1;:VOLT:LOW -1")  # 2 Vpp sine = 1/sqrt(2) Vrms
    assert float(awg("VOLT?")) == pytest.approx(2 / (2 * math.sqrt(2)), rel=1e-6)
    assert float(awg("VOLT:HIGH?")) == pytest.approx(1.0)
