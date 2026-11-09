"""Build hook: write ``src/simubench/_version.py`` with the version and the commit (D58).

scripts/version.py gives the version; this hook writes it and the commit into the file. The commit comes from git, else from ``.git_archival.txt`` (a GitHub zip or ``git archive``),
else from the file an sdist already carries; unknown stays ``None``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

VERSION_FILE = "src/simubench/_version.py"


def _git_commit(root: Path) -> str | None:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    # Only this checkout's own repository: an sdist unpacked inside another one is not it.
    if len(top) == 2 and Path(top[0]).resolve() == root.resolve():
        return top[1]
    return None


def _archival_commit(root: Path) -> str | None:
    try:
        text = (root / ".git_archival.txt").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^node: ([0-9a-f]{40})$", text, re.MULTILINE)
    return m.group(1) if m else None


def _existing_commit(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^__commit_id__ = "([0-9a-f]{40})"$', text, re.MULTILINE)
    return m.group(1) if m else None


class VersionFileHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        path = root / VERSION_FILE
        commit = _git_commit(root) or _archival_commit(root) or _existing_commit(path)
        commit_literal = f'"{commit}"' if commit else "None"
        path.write_text(
            "# Written by hatch_build.py at build time; not in git (D58).\n"
            f'__version__ = "{self.metadata.version}"\n'
            f"__commit_id__ = {commit_literal}\n",
            encoding="utf-8",
        )
        build_data["artifacts"].append(f"/{VERSION_FILE}")
