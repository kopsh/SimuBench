#!/usr/bin/env python
"""Keep the progress log's history: nothing written there is lost, and new entries are files.

The log is the self-check record of each piece of work (D24). Until #1505 every pull request
appended its section to the end of ``docs/progress-log.md``, so any two pull requests in flight
conflicted at the end of that file, and the merge bot cannot resolve a conflict. Since then:

- ``docs/progress-log.md`` is the archive of the entries up to #1505. Its section count must
  stay what it is on the merge base with ``origin/main``: fewer means a conflict resolution
  dropped history (issue #380 lost ~770 lines that way), more means a new entry went into the
  archive instead of its own file.
- ``docs/progress-log/<issue>-<short-name>.md`` holds one entry each, starting with its ``## ``
  heading. Two pull requests add two different files, so they never conflict. A file on the
  merge base must not disappear.

On a shallow checkout without ``origin/main`` (CI without full history) the merge-base checks
are skipped with a note; ``ci.yml`` gives the fast-check job full history so they run there too.

Exit code 1 with one line per problem. Fast and offline: part of ``scripts/check.sh``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

LOG = Path("docs/progress-log.md")
ENTRIES = Path("docs/progress-log")
# <issue>-<short-name>.md, e.g. 1505-progress-log-per-file.md (the README explains the folder).
ENTRY_NAME = re.compile(r"^\d+-[a-z0-9][a-z0-9-]*\.md$")


def sections(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.startswith("## ")]


def git(*args: str) -> str | None:
    """Run git and return its stdout, decoded as UTF-8 on every platform.

    ``text=True`` alone decodes with the locale encoding (cp1252 on Windows), which
    breaks on the Chinese text of the log (issue #414).
    """
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.strip() if out is not None else None


def entry_problems(entries: Path) -> list[str]:
    """Each entry file is named <issue>-<short-name>.md and starts with its '## ' heading."""
    problems: list[str] = []
    for path in sorted(entries.glob("*.md")):
        if path.name == "README.md":
            continue
        if not ENTRY_NAME.match(path.name):
            problems.append(f"{path}: name it <issue>-<short-name>.md (lowercase, digits, '-')")
        if not path.read_text(encoding="utf-8").startswith("## "):
            problems.append(f"{path}: start with the entry's '## ' heading")
    return problems


def archive_problem(current: list[str], base: list[str], base_ref: str) -> str | None:
    if len(current) < len(base):
        lost = sorted(set(base) - set(current))
        return (
            f"{LOG} lost sections: {len(current)} now vs {len(base)} on the merge base"
            f" ({base_ref[:12]}); e.g. missing: {', '.join(lost[:5])}"
        )
    if len(current) > len(base):
        added = sorted(set(current) - set(base))
        return (
            f"{LOG} is the closed archive: put a new entry in its own file"
            f" {ENTRIES}/<issue>-<short-name>.md, not in the archive"
            f" (added: {', '.join(added[:5])})"
        )
    return None


def main() -> int:
    problems: list[str] = []
    text = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
    current = sections(text)
    if not current:
        problems.append(f"{LOG} is missing or has no '## ' sections")
    elif not text.lstrip().startswith("# "):
        # The file starts with its title and usage note, not straight with a section
        # (issue #380's accident lost both).
        problems.append(f"{LOG} does not start with its '# ' title")
    problems += entry_problems(ENTRIES)

    base = git("merge-base", "origin/main", "HEAD")
    if base is None:
        print(
            "check_progress_log: no merge base with origin/main (shallow checkout?), "
            "skipping the merge-base checks"
        )
    else:
        base_text = git("show", f"{base}:{LOG.as_posix()}")
        problem = archive_problem(current, sections(base_text or ""), base)
        if problem:
            problems.append(problem)
        listed = git("ls-tree", "--name-only", f"{base}:{ENTRIES.as_posix()}") or ""
        for name in listed.splitlines():
            if name.endswith(".md") and not (ENTRIES / name).exists():
                problems.append(f"{ENTRIES / name} exists on the merge base but was deleted")

    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
