"""What changed for the user, by version, from the git history and its date tags (D58).

Only commits that change the product are listed: those touching ``PRODUCT`` below, which is what
goes into the wheel. A commit that touches only CI, tests, cases, docs or skills is left out. The
entry is the commit subject, i.e. the PR title (squash merge). Newest version first.

    python scripts/changelog.py                         # everything
    python scripts/changelog.py --since 2026.11.4.75    # after that version
    python scripts/changelog.py --version 2026.11.5.3 -o dist/CHANGELOG.md

Commits after the newest tag go under ``--version`` (the version being built), else "未发布".
Standard library and git only, so an internal build needs nothing extra. Needs the history with
its v* tags; without it this exits with 2 and says so.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
PRODUCT_DIRS = ("src/", "web/src/", "examples/")
PRODUCT_FILES = ("pyproject.toml", "web/package.json")
SEP = "\x1e"


@dataclass
class Commit:
    sha: str
    epoch: int
    subject: str
    paths: list[str] = field(default_factory=list)


def is_product(paths: list[str]) -> bool:
    return any(p.startswith(PRODUCT_DIRS) or p in PRODUCT_FILES for p in paths)


def _date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).astimezone(TZ).strftime("%Y-%m-%d")


def render(
    commits: list[Commit], tags: dict[str, str], *, since: str | None = None, version: str | None = None
) -> str:
    """Markdown for ``commits`` (first-parent, oldest first); ``tags`` maps sha to ``vX.Y.Z.N``."""
    groups: list[tuple[str, int, list[str]]] = []
    pending: list[str] = []
    for c in commits:
        if is_product(c.paths):
            pending.append(c.subject)
        if c.sha in tags:
            groups.append((tags[c.sha].removeprefix("v"), c.epoch, pending))
            pending = []
    if commits and pending:
        groups.append((version or "未发布", commits[-1].epoch, pending))
    if since:
        names = [name for name, _, _ in groups]
        since = since.removeprefix("v")
        if since not in names:
            raise ValueError(f"no version {since} in this history")
        groups = groups[names.index(since) + 1 :]
    out = ["# SimuBench 变更记录", ""]
    for name, epoch, entries in reversed(groups):
        if entries:
            out += [f"## {name}（{_date(epoch)}）", "", *(f"- {s}" for s in entries), ""]
    return "\n".join(out)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def read_history() -> tuple[list[Commit], dict[str, str]]:
    commits: list[Commit] = []
    log = _git("log", "--first-parent", "--reverse", "--name-only", f"--format={SEP}%H %ct %s")
    for block in log.split(SEP)[1:]:
        head, *paths = block.strip("\n").split("\n")
        sha, epoch, subject = head.split(" ", 2)
        commits.append(Commit(sha, int(epoch), subject, [p for p in paths if p]))
    tags = {}
    for line in _git("tag", "--list", "v[0-9]*", "--format=%(objectname) %(refname:short)").splitlines():
        sha, tag = line.split()
        tags[sha] = tag
    return commits, tags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="only the versions after this one")
    parser.add_argument("--version", help="the name for the commits after the newest tag")
    parser.add_argument("-o", "--output", type=Path, help="write here instead of printing")
    args = parser.parse_args(argv)
    try:
        commits, tags = read_history()
    except (OSError, subprocess.CalledProcessError):
        print("changelog: no git history here (a zip or archive): nothing to list", file=sys.stderr)
        return 2
    if not tags or _git("rev-parse", "--is-shallow-repository").strip() == "true":
        print("changelog: needs the full history with its v* tags (git fetch --unshallow --tags)", file=sys.stderr)
        return 2
    try:
        text = render(commits, tags, since=args.since, version=args.version)
    except ValueError as exc:
        print(f"changelog: {exc}", file=sys.stderr)
        return 2
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
