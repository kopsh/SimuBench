"""The built-in models' cases at library scale (#1352): which cases files a model lists, which
names are refused against the real tree, and what the wheel must carry (#1377).

These tests resolve through the whole definition library or scan ``tests/models`` — a few hundred
milliseconds under load, so they sit above the unit tier (D49): no ``unit`` mark, same fast suite.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from helpers import load_script, raises_with
from simubench.errors import Invalid
from simubench.library.coverage import builtin_cases_file, builtin_cases_for_model

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "tests" / "models"
wheel_check = load_script(ROOT / "scripts" / "check_wheel_cases.py")
#: Three real cases folders (two of them one model's): enough to index, quick enough for the tier.
FOLDERS = ("bk-8614", "keysight-34465a", "keysight-34465a-impedance")


@pytest.fixture(scope="module")
def cases_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("models")
    for f in FOLDERS:
        (root / f).mkdir()
        (root / f / "cases.yaml").write_bytes((MODELS / f / "cases.yaml").read_bytes())
    return root


def _hatch_build():
    """hatch_build.py without hatchling installed: its base class is all it imports from there."""
    names = ["hatchling", "hatchling.builders", "hatchling.builders.hooks", "hatchling.builders.hooks.plugin"]
    fake = {n: types.ModuleType(n) for n in names}
    interface = types.ModuleType("hatchling.builders.hooks.plugin.interface")
    interface.BuildHookInterface = object  # type: ignore[attr-defined]
    fake[interface.__name__] = interface
    saved = {n: sys.modules.get(n) for n in fake}
    sys.modules.update(fake)
    try:
        return load_script(ROOT / "hatch_build.py")
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


@pytest.mark.parametrize(
    ("model", "folders"),
    [("bk-8614", ["bk-8614"]), ("keysight-34465a", ["keysight-34465a", "keysight-34465a-impedance"])],
)
def test_a_model_lists_its_own_cases_files(cases_root, model, folders):
    found = builtin_cases_for_model(model, cases_root)
    assert [name for name, _n in found] == [f"builtin:{f}/cases.yaml" for f in folders]
    assert all(n > 0 for _name, n in found)
    for f in folders:
        assert (
            builtin_cases_file(f"builtin:{f}/cases.yaml", model, cases_root) == cases_root / f / "cases.yaml"
        )


@pytest.mark.parametrize(
    "name",
    [
        "builtin:keysight-34465a\\x/cases.yaml",
        "builtin:./cases.yaml",
        "builtin:../cases.yaml",
        "builtin:/cases.yaml",
        "keysight-34465a/cases.yaml",  # not a builtin: name
    ],
)
def test_a_name_that_is_not_the_models_own_is_refused(cases_root, name):
    with raises_with(Invalid, "not a built-in cases file"):
        builtin_cases_file(name, "keysight-34465a", cases_root)


def test_another_models_cases_file_is_refused(cases_root):
    with raises_with(Invalid, "is not a cases file of keysight-34465a"):
        builtin_cases_file("builtin:bk-8614/cases.yaml", "keysight-34465a", cases_root)


def test_the_wheel_maps_exactly_the_builtin_models_cases_and_the_example_benches():
    mapping = _hatch_build()._model_cases(ROOT)
    cases = {
        Path(src).name for src, dst in mapping.items() if dst.startswith("simubench/library/model_cases/")
    }
    assert cases == wheel_check.expected_folders(ROOT)
    assert mapping[str(ROOT / "examples" / "benches")] == "simubench/examples/benches"
    assert all(
        dst == f"simubench/library/model_cases/{Path(src).name}"
        for src, dst in mapping.items()
        if src in cases
    )


def test_the_wheel_check_names_what_is_missing():
    folders = sorted(wheel_check.expected_folders(ROOT))
    good = [f"simubench/library/model_cases/{f}/cases.yaml" for f in folders]
    good.append("simubench/examples/benches/keysight-34465a.bench.yaml")
    assert wheel_check.problems(good, ROOT) == []
    assert wheel_check.problems(good[1:], ROOT) == [
        f"missing simubench/library/model_cases/{folders[0]}/cases.yaml"
    ]
    assert wheel_check.problems(good[:-1], ROOT) == [
        "missing the example benches under simubench/examples/benches/"
    ]
    extra = [*good, "simubench/library/model_cases/sb-dmm-1/cases.yaml"]
    assert wheel_check.problems(extra, ROOT) == [
        "not a built-in model's cases: simubench/library/model_cases/sb-dmm-1/cases.yaml"
    ]
