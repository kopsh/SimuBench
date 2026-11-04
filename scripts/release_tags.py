"""Date tags for main: every first-parent commit gets ``vYYYY.M.D.N`` (D58).

The date is the commit's committer date in UTC+8; N counts that day's first-parent commits on
main, from 1. The same commit always gets the same tag, wherever and whenever this runs, so the
version a wheel carries (hatch-vcs reads the tag) names exactly one commit.

    python scripts/release_tags.py            # print the tags main is missing
    python scripts/release_tags.py --push     # create them and push them to origin

Only commits after the newest tagged one are tagged (a cancelled CI run on main leaves gaps that
the next run fills). With no tag yet, only HEAD is tagged: history before the scheme stays untagged.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def date_tags(commits: list[tuple[str, int]]) -> dict[str, str]:
    """``{sha: tag}`` for first-parent commits given oldest first as ``(sha, committer epoch)``."""
    tags: dict[str, str] = {}
    per_day: dict[tuple[int, int, int], int] = {}
    for sha, epoch in commits:
        d = datetime.fromtimestamp(epoch, UTC).astimezone(TZ).date()
        key = (d.year, d.month, d.day)
        per_day[key] = per_day.get(key, 0) + 1
        tags[sha] = f"v{d.year}.{d.month}.{d.day}.{per_day[key]}"
    return tags


def missing(commits: list[tuple[str, int]], tagged: set[str]) -> list[tuple[str, str]]:
    """``(tag, sha)`` for the commits after the newest one in ``tagged``, oldest first."""
    if not commits:
        return []
    tags = date_tags(commits)
    shas = [sha for sha, _ in commits]
    last = max((i for i, sha in enumerate(shas) if sha in tagged), default=None)
    todo = shas[-1:] if last is None else shas[last + 1 :]
    return [(tags[sha], sha) for sha in todo]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--push", action="store_true", help="create the tags and push them to origin")
    args = parser.parse_args(argv)
    commits = [
        (sha, int(epoch))
        for sha, epoch in (
            line.split()
            for line in _git("log", "--first-parent", "--reverse", "--format=%H %ct").splitlines()
        )
    ]
    tagged = set(_git("tag", "--list", "v[0-9]*", "--format=%(objectname)").split())
    todo = missing(commits, tagged)
    for tag, sha in todo:
        print(tag, sha)
        if args.push:
            _git("tag", tag, sha)
    if args.push and todo:
        _git("push", "origin", *(f"refs/tags/{tag}" for tag, _ in todo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
