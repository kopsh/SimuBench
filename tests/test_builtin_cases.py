"""The built-in models' manual cases ship with SimuBench (#1352): where the cases root is found
(the packaged ``model_cases`` first, else the checkout's ``tests/models``). The library-scale
scans — a model's own cases files, refused names against the real tree, the wheel's contents —
live in ``test_builtin_cases_scan.py``, above the unit tier (D49)."""

from __future__ import annotations

from pathlib import Path

import pytest

from simubench.library.coverage import builtin_cases_root

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "tests" / "models"


def test_the_packaged_folder_comes_before_the_checkout(tmp_path):
    library = tmp_path / "src" / "simubench" / "library"
    (library / "model_cases").mkdir(parents=True)
    (tmp_path / "tests" / "models").mkdir(parents=True)
    assert builtin_cases_root(library) == library / "model_cases"


def test_without_a_packaged_folder_the_checkout_is_the_root(tmp_path):
    library = tmp_path / "src" / "simubench" / "library"
    library.mkdir(parents=True)
    assert builtin_cases_root(library) is None  # neither exists
    (tmp_path / "tests" / "models").mkdir(parents=True)
    assert builtin_cases_root(library) == (tmp_path / "tests" / "models").resolve()


def test_the_running_checkout_finds_its_own_cases():
    assert builtin_cases_root() == MODELS
