"""File format versions: old files load, newer ones are refused clearly (M5)."""

import pytest
import yaml

from simubench.cli import main
from simubench.engine.validate import validate_data
from simubench.library.loader import DefinitionLibrary, load_bench
from simubench.library.migrate import FormatError, upgrade

OLD = {"name": "old", "instruments": {"dmm": {"model": "sb-dmm-1"}}}


def test_unversioned_bench_is_upgraded_in_memory(tmp_path):
    data, steps = upgrade(OLD, "bench")
    assert steps == ["bench 0 → 1", "bench 1 → 2"] and data["schema"] == "simubench.bench/2"
    path = tmp_path / "old.bench.yaml"
    path.write_text(yaml.safe_dump(OLD), encoding="utf-8")
    assert load_bench(path).instruments["dmm"].model == "sb-dmm-1"
    assert upgrade(data, "bench")[1] == []  # current files are left alone


def test_newer_files_are_refused_with_a_reason():
    with pytest.raises(FormatError, match="format version 3; this SimuBench reads up to 2"):
        upgrade({"schema": "simubench.bench/3", "name": "x"}, "bench")
    with pytest.raises(FormatError, match="not a SimuBench bench file"):
        upgrade({"schema": "something/1"}, "bench")
    _, report = validate_data({"schema": "simubench.bench/9", "name": "x"}, DefinitionLibrary([]))
    assert [d.code for d in report.diagnostics] == ["E-FORMAT"]


def test_cli_migrate(tmp_path, capsys):
    old = tmp_path / "old.bench.yaml"
    old.write_text(yaml.safe_dump(OLD), encoding="utf-8")
    new = tmp_path / "new.bench.yaml"
    new.write_text("schema: simubench.bench/7\nname: n\n", encoding="utf-8")
    assert main(["migrate", str(old), str(new)]) == 1
    out = capsys.readouterr()
    assert "bench 0 → 1, bench 1 → 2 (dry run" in out.out and "reads up to 2" in out.err
    assert "schema" not in yaml.safe_load(old.read_text())  # a dry run writes nothing
    assert main(["migrate", "--write", str(old)]) == 0
    assert yaml.safe_load(old.read_text())["schema"] == "simubench.bench/2"
    assert main(["migrate", str(old)]) == 0 and "current" in capsys.readouterr().out


def test_duts_become_parts_marked_as_duts(tmp_path):
    """bench/1 listed DUTs beside instruments; in bench/2 DUT is a role of an instrument or a part (#100)."""
    v1 = {
        "schema": "simubench.bench/1",
        "name": "v1",
        "instruments": {"dmm": {"model": "sb-dmm-1"}},
        "duts": {"rc": {"type": "rc_lowpass", "params": {"r": 1000.0}}},
    }
    data, steps = upgrade(v1, "bench")
    assert steps == ["bench 1 → 2"] and "duts" not in data
    assert data["parts"] == {"rc": {"type": "rc_lowpass", "params": {"r": 1000.0}, "dut": True}}
    path = tmp_path / "v1.bench.yaml"
    path.write_text(yaml.safe_dump(v1), encoding="utf-8")
    bench = load_bench(path)
    assert bench.duts() == ["rc"] and not bench.instruments["dmm"].dut
    empty = upgrade({"schema": "simubench.bench/1", "name": "e", "duts": {}}, "bench")[0]
    assert "parts" not in empty and "duts" not in empty
