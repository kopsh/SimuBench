#!/usr/bin/env python
"""Keep docs/progress-log.md append-only: its section count must not shrink.

The log is written by appending self-check sections (D24). A merge-conflict
resolution that rewrote only part of the file silently dropped ~770 lines of
history once (issue #380); comparing the section count with the merge base of
``origin/main`` catches that class of mistake before a push.

On a shallow checkout without ``origin/main`` (CI without full history) the
count check is skipped with a note; ``ci.yml`` gives the fast-check job full
history so it runs there too.

Exit code 1 with one line per problem. Fast and offline: part of ``scripts/check.sh``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LOG = Path("docs/progress-log.md")


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

    base = git("merge-base", "origin/main", "HEAD")
    if base is None:
        print(
            "check_progress_log: no merge base with origin/main (shallow checkout?), "
            "skipping the section-count check"
        )
    else:
        base_text = git("show", f"{base}:{LOG.as_posix()}")
        base_count = len(sections(base_text)) if base_text is not None else 0
        if len(current) < base_count:
            lost = sorted(set(sections(base_text or "")) - set(current))
            problems.append(
                f"{LOG} lost sections: {len(current)} now vs {base_count} on the merge base"
                f" ({base[:12]}); e.g. missing: {', '.join(lost[:5])}"
            )

    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
