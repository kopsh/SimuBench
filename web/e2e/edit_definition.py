"""An agent-side edit of a workspace definition for the acceptance-page end-to-end test.

It goes through the same save path as the MCP tool ``edit_definition`` (``DefinitionFiles.edit``
with source ``agent``), so the version history and its change notes are the ones the page shows.
Usage: ``uv run python web/e2e/edit_definition.py <workspace> <definition-id>``.
"""

import sys
from pathlib import Path

from simubench.engine.jsonpatch import PatchOp
from simubench.server.definitionfiles import DefinitionFiles
from simubench.server.manager import SessionManager


def main() -> None:
    workspace, definition_id = Path(sys.argv[1]), sys.argv[2]
    files = DefinitionFiles(SessionManager(workspace, scpi_base_port=None).files)
    doc = files.read(definition_id)
    files.edit(
        definition_id,
        doc.sha256,
        [PatchOp(op="replace", path="/state/sense.range/max", value=2000.0)],
        source="agent",
    )


if __name__ == "__main__":
    main()
