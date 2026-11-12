#!/usr/bin/env python
"""Keep package-manager caches and installed dependencies out of git (issue #991).

A tracked file under ``.pnpm-store/`` or ``node_modules/`` (at any depth) is a mistake: a pnpm
content-addressed cache once got merged (24011 files, 482 MB). ``.gitignore`` hides both, but a
forced or ``git add -f`` add still gets through; this check fails on it.

Exit code 1 with one line per problem. Fast and offline: part of ``scripts/check.sh``.
"""

from __future__ import annotations

import subprocess
import sys

FORBIDDEN = (".pnpm-store", "node_modules")


def forbidden(path: str) -> bool:
    """True when a path has a ``.pnpm-store`` or ``node_modules`` directory component."""
    return any(part in FORBIDDEN for part in path.split("/")[:-1])


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True, encoding="utf-8", errors="replace"
    ).stdout
    return [p for p in out.split("\0") if p]


def main() -> int:
    bad = [p for p in tracked_files() if forbidden(p)]
    for path in bad[:20]:
        print(f"tracked but must not be (package-manager cache or dependencies): {path}")
    if len(bad) > 20:
        print(f"... and {len(bad) - 20} more")
    if bad:
        print("remove them with `git rm -r --cached <dir>`; .gitignore lists both directories")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
