"""Run the test files a pull request changed several times over, so a race that fails one run in a
few shows on the pull request instead of on main (#1543).

The fast checks run each test once. A test of new concurrent code (a watcher, an event stream, a
session that reopens) can pass that once and fail on main a few merges later: #1526 and #1547 were
three such races in one test. Repeating only what the pull request changed keeps the cost small;
the job runs beside the browser tests, which take longer, so a pull request waits no longer.

Bounded, so it never becomes the slowest job: more than MAX_FILES changed files (a broad refactor)
are not repeated, and no repeat starts after BUDGET_S seconds. A race rarer than one run in
REPEATS is not caught here: those are caught by tests that force the interleaving (dev-workflow).

    uv run python scripts/repeat_changed_tests.py [--base origin/main]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPEATS = 5
MAX_FILES = 10
BUDGET_S = 240.0
#: The fast tier's selection (D49): what the pull request's own pytest step runs.
MARKERS = "not tui_boot and not load_sensitive"


def test_files(changed: list[str]) -> list[str]:
    """The pytest files among the changed paths: tests/ and acceptance/, named test_*.py."""
    out = []
    for rel in changed:
        p = Path(rel)
        if (
            p.parts
            and p.parts[0] in ("tests", "acceptance")
            and p.name.startswith("test_")
            and p.suffix == ".py"
        ):
            out.append(p.as_posix())
    return sorted(set(out))


def plan(files: list[str]) -> str | None:
    """Why nothing is repeated, or None to repeat ``files``."""
    if not files:
        return "no test file changed"
    if len(files) > MAX_FILES:
        return f"{len(files)} test files changed (> {MAX_FILES}): a broad change, not repeated"
    return None


def changed_since(base: str) -> list[str]:
    merge_base = subprocess.run(
        ["git", "merge-base", base, "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AMR", merge_base, "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    files = [f for f in test_files(changed_since(args.base)) if (ROOT / f).exists()]
    skip = plan(files)
    if skip:
        print(f"repeat_changed_tests: {skip}")
        return 0
    print(f"repeat_changed_tests: {REPEATS} runs of {', '.join(files)}")
    started = time.monotonic()
    for n in range(1, REPEATS + 1):
        left = BUDGET_S - (time.monotonic() - started)
        if left <= 0:
            print(f"repeat_changed_tests: time cap ({BUDGET_S:g} s) reached after {n - 1} runs; stopping")
            return 0
        cmd = [sys.executable, "-m", "pytest", "-q", "-n", "4", "--dist", "loadscope", "-m", MARKERS, *files]
        try:
            result = subprocess.run(cmd, cwd=ROOT, timeout=left)
        except subprocess.TimeoutExpired:
            print(f"repeat_changed_tests: time cap ({BUDGET_S:g} s) reached during run {n}; stopping")
            return 0
        if result.returncode != 0:
            print(
                f"repeat_changed_tests: run {n} of {REPEATS} failed — a test that does not pass every time is a race"
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
