"""A definition written with ``op: acq.preamble`` (before #633) still loads and answers the same
Keysight ten-field preamble: the op expands into ``xfer.header`` (issue 665)."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from simubench.api import open_bench
from simubench.engine.validate_definition import validate_definition_data
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "tests" / "data" / "legacy_sb-dso-4.yaml"
BENCH = BenchDefinition.model_validate(
    {"schema": "simubench.bench/2", "name": "b", "instruments": {"dso": {"model": "sb-dso-4"}}}
)


def preamble(definition_dirs: list[Path], fmt: str) -> list[str]:
    s = open_bench(BENCH, definition_dirs=definition_dirs)
    try:
        for c in (":TIMebase:SCALe 2E-4", f":WAVeform:FORMat {fmt}", ":DIGitize", ":WAVeform:PREamble?"):
            s.write("dso", c.encode() + b"\n")
        return s.read("dso").decode().strip().split(",")
    finally:
        s.close()


def test_legacy_definition_validates_without_changes():
    data = yaml.safe_load(LEGACY.read_text("utf-8"))
    assert any(c.get("op") == "acq.preamble" for c in data["commands"])
    assert validate_definition_data(data)[1].diagnostics == []


def test_legacy_preamble_answers_what_the_declared_header_answers(tmp_path):
    shutil.copy(LEGACY, tmp_path / "sb-dso-4.yaml")
    builtin = ROOT / "validation" / "instruments"
    for fmt, code in (("BYTE", "0"), ("WORD", "1"), ("ASCii", "4")):
        legacy = preamble([tmp_path, builtin], fmt)
        assert legacy == preamble([builtin], fmt)
        assert len(legacy) == 10 and legacy[0] == code and legacy[1] == "0" and legacy[3] == "1"
        assert legacy[4].startswith("+") and "E" in legacy[4]  # NR3, six digits
