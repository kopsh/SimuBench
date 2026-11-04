"""engine/commands.py is the static side of command routing, imported by the MCP service and the
coverage checker. It must stay free of the library (model) layer: the import chain
commands → library → engine was the cycle the command-table consolidation had to cut (issue 594)."""

from __future__ import annotations

import subprocess
import sys


def test_commands_module_stays_free_of_the_library_layer():
    code = (
        "import sys; import simubench.engine.commands; "
        "print(','.join(m for m in sys.modules if m.startswith('simubench.library')))"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
