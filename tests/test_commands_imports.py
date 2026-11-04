"""Importing an engine module must not pull in the library (model) layer.

engine/commands.py is the static side of command routing, imported by the MCP service and the
coverage checker: the import chain commands → library → engine was the cycle the command-table
consolidation had to cut (issue 594). engine/instrument.py is the runtime's own module: the
coverage table it classifies -113 with is read by the library loader and rides on the definition
(issue 613), so the engine reads no files and imports no loader.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def _library_modules_after_import(module: str) -> list[str]:
    code = (
        f"import sys; import {module}; "
        "print(','.join(m for m in sys.modules if m.startswith('simubench.library')))"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    return out.stdout.strip().split(",") if out.stdout.strip() else []


@pytest.mark.parametrize("module", ["simubench.engine.commands", "simubench.engine.instrument"])
def test_engine_module_stays_free_of_the_library_layer(module: str):
    assert _library_modules_after_import(module) == []
