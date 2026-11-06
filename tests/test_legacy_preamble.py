"""A definition written with ``op: acq.preamble`` (before #633) still loads and answers the same
Keysight ten-field preamble: the op expands into ``xfer.header`` (issue 665)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from simubench.api import open_bench
from simubench.engine.validate_definition import validate_definition_data
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "tests" / "data" / "legacy_sb-dso-4.yaml"
BENCH = BenchDefinition.model_validate(
    {"schema": "simubench.bench/2", "name": "b", "instruments": {"dso": {"model": "sb-dso-4"}}}
)


def preamble(definition_dirs: list[Path], fmt: str) -> str:
    s = open_bench(BENCH, definition_dirs=definition_dirs)
    try:
        for c in (":TIMebase:SCALe 2E-4", f":WAVeform:FORMat {fmt}", ":DIGitize", ":WAVeform:PREamble?"):
            s.write("dso", c.encode() + b"\n")
        return s.read("dso").decode().strip()
    finally:
        s.close()


#: What commit 17881a9 (before #633) answered to the sequence above, taken by running that commit.
AT_17881A9 = {
    "BYTE": "0,0,1000,1,+2.000000E-06,-1.000000E-03,0,+3.125000E-02,+0.000000E+00,0",
    "WORD": "1,0,1000,1,+2.000000E-06,-1.000000E-03,0,+1.220703E-04,+0.000000E+00,0",
    "ASCii": "4,0,1000,1,+2.000000E-06,-1.000000E-03,0,+1.220703E-04,+0.000000E+00,0",
}
#: The one difference #633 made and the expansion cannot undo: the old ``acq.preamble`` took the Y
#: increment from the acquisition's ADC codes (8192 per division, WORD scale) whatever the format;
#: the transfer's ``header`` takes it from the transfer's own ``codes_per_div`` (32 for ASCii).
#: Every other field is as before. (Raised on #665.)
SINCE_633 = {"ASCii": "4,0,1000,1,+2.000000E-06,-1.000000E-03,0,+3.125000E-02,+0.000000E+00,0"}


def test_legacy_definition_validates_without_changes():
    data = yaml.safe_load(LEGACY.read_text("utf-8"))
    assert any(c.get("op") == "acq.preamble" for c in data["commands"])
    assert validate_definition_data(data)[1].diagnostics == []


@pytest.mark.parametrize("fmt", AT_17881A9)
def test_legacy_preamble_answers_what_it_answered_before(tmp_path, fmt):
    shutil.copy(LEGACY, tmp_path / "sb-dso-4.yaml")
    loaded = DefinitionLibrary([tmp_path, ROOT / "validation" / "instruments"]).get("sb-dso-4")
    assert Path(loaded._source_file) == tmp_path / "sb-dso-4.yaml"  # the legacy file, not the built-in
    assert preamble([tmp_path, ROOT / "validation" / "instruments"], fmt) == {**AT_17881A9, **SINCE_633}[fmt]
