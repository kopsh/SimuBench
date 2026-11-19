"""scripts/check_layers.py: the package layers point one way and form no cycle (D87, #1507).

Each test writes a small source tree in ``tmp_path`` (``src/simubench/<package>/…``) and checks
what the script reports for it; the real tree is checked by ``scripts/check.sh`` itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
script = load_script(ROOT / "scripts" / "check_layers.py")


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / "src" / "simubench" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "src" / "simubench" / "__init__.py").touch()
    return root


@pytest.mark.unit
def test_an_import_of_a_higher_layer_is_reported_even_inside_a_function(tmp_path):
    root = _tree(
        tmp_path,
        {
            "engine/__init__.py": "",
            "engine/component.py": "def f():\n    from ..library import components\n",
            "library/__init__.py": "",
            "library/components.py": "from ..engine import component\n",
        },
    )
    found = script.problems(root)
    assert (
        "src/simubench/engine/component.py:2: engine (engine) imports library (library), a higher layer"
        in found
    )


@pytest.mark.unit
def test_an_import_for_type_annotations_only_is_not_a_dependency(tmp_path):
    root = _tree(
        tmp_path,
        {
            "scpi/__init__.py": "",
            "scpi/values.py": (
                "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from ..models.definition import StateSpec\n"
            ),
            "models/__init__.py": "",
            "models/definition.py": "from ..scpi import values\n",
        },
    )
    assert script.problems(root) == []


@pytest.mark.unit
def test_a_cycle_inside_one_layer_is_reported(tmp_path):
    root = _tree(
        tmp_path,
        {"api.py": "def f():\n    from . import replay\n", "replay.py": "from .api import open_bench\n"},
    )
    found = script.problems(root)
    assert found == ["cycle between api, replay: src/simubench/api.py:2; src/simubench/replay.py:1"]


@pytest.mark.unit
def test_a_service_may_not_import_the_web_framework(tmp_path):
    root = _tree(
        tmp_path,
        {
            "server/__init__.py": "",
            "server/services/__init__.py": "",
            "server/services/faults.py": "from fastapi import HTTPException\n",
            "server/routes.py": "from fastapi import APIRouter\n",
        },
    )
    found = script.problems(root)
    assert found == [
        "src/simubench/server/services/faults.py:1: simubench.server.services.faults imports fastapi: "
        "frameworks stay at the seams"
    ]


@pytest.mark.unit
def test_a_package_without_a_layer_is_reported(tmp_path):
    root = _tree(tmp_path, {"gadgets.py": "x = 1\n"})
    assert script.problems(root) == [
        "gadgets: not in LAYERS (scripts/check_layers.py): give the new package its layer"
    ]


@pytest.mark.unit
def test_the_root_packages_version_belongs_to_the_base_layer(tmp_path):
    root = _tree(tmp_path, {"engine/__init__.py": "", "engine/record.py": "from .. import __version__\n"})
    assert script.problems(root) == []
