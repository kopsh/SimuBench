"""What changed for the user, by day, from the git history; no tags needed (D58).

Only commits that change the product are listed: those touching ``PRODUCT`` below, which is what
goes into the wheel. A commit that touches only CI, tests, cases, docs or skills is left out. The
entry is the commit subject, i.e. the PR title (squash merge), without what only the developers
read: the trailing issue and PR references ``(Closes #12) (#34)`` and the ``核心:`` work label.
One heading per day, newest first, named by the day's last version (``version.py``).

    python scripts/changelog.py                         # everything
    python scripts/changelog.py --since 2026.11.4.75    # the commits after that version
    python scripts/changelog.py -o dist/CHANGELOG.md

Standard library and git only, so an internal build needs nothing extra. Needs the full history;
without it this exits with 2 and says so.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import version as versions  # scripts/version.py, beside this file

PRODUCT_DIRS = ("src/", "web/src/", "examples/")
PRODUCT_FILES = ("pyproject.toml", "web/package.json")
SEP = "\x1e"
# A trailing (…) or （…） holding only references: "(Closes #12)", "(#34)", "（Refs #5, #6）", and
# with them the internal codes of a decision or a work item: "(#23, D44)", "（G6，#137）".
_REF = r"(?:(?:closes|fixes|refs|关闭)\s*)?#\d+"
_ITEM = rf"(?:{_REF}|[A-Z]{{1,2}}\d+)"
TRAILING_REFS = re.compile(
    rf"\s*[(（]\s*(?=[^()（）]*#\d)(?:{_ITEM})(?:\s*[,，、/ ]\s*{_ITEM})*\s*[)）]\s*$", re.IGNORECASE
)
# "Refs #333" inside a note that says more: "(SR865A 前置,Refs #333)" keeps "(SR865A 前置)".
INNER_REFS = re.compile(r"\s*[,，;；]\s*(?:closes|fixes|refs|关闭)\s*#\d+", re.IGNORECASE)
WORK_LABEL = re.compile(r"^核心(?:能力)?\s*[:：]\s*")


def entry(subject: str) -> str:
    """The changelog line for a commit subject: what changed, without the developers' bookkeeping."""
    text = INNER_REFS.sub("", subject.strip())
    while (m := TRAILING_REFS.search(text)) is not None:
        text = text[: m.start()]
    return WORK_LABEL.sub("", text).strip()


@dataclass
class Commit:
    sha: str
    epoch: int
    subject: str
    paths: list[str] = field(default_factory=list)


def is_product(paths: list[str]) -> bool:
    return any(p.startswith(PRODUCT_DIRS) or p in PRODUCT_FILES for p in paths)


def render(commits: list[Commit], *, since: str | None = None) -> str:
    """Markdown for ``commits`` (first-parent, oldest first): one section per day, newest first."""
    names = versions.numbered([c.epoch for c in commits])
    floor = versions.parse(since.removeprefix("v")) if since else None
    days: list[tuple[str, int, list[str]]] = []  # (day's last version, epoch, entries)
    for c, vname in zip(commits, names, strict=True):
        day = versions.day_of(c.epoch)
        if not days or versions.day_of(days[-1][1]) != day:
            days.append((vname, c.epoch, []))
        days[-1] = (vname, c.epoch, days[-1][2])
        if is_product(c.paths) and (floor is None or versions.parse(vname) > floor):
            days[-1][2].append(entry(c.subject))
    out = ["# SimuBench 变更记录", ""]
    for vname, epoch, entries in reversed(days):
        if entries:
            d = versions.day_of(epoch)
            out += [f"## {vname}（{d[0]}-{d[1]:02d}-{d[2]:02d}）", "", *(f"- {s}" for s in entries), ""]
    return "\n".join(out)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def read_history() -> list[Commit]:
    commits: list[Commit] = []
    log = _git("log", "--first-parent", "--reverse", "--name-only", f"--format={SEP}%H %ct %s")
    for block in log.split(SEP)[1:]:
        head, *paths = block.strip("\n").split("\n")
        sha, epoch, subject = head.split(" ", 2)
        commits.append(Commit(sha, int(epoch), subject, [p for p in paths if p]))
    return commits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="only the commits after this version")
    parser.add_argument("-o", "--output", type=Path, help="write here instead of printing")
    args = parser.parse_args(argv)
    try:
        commits = read_history()
    except (OSError, subprocess.CalledProcessError):
        print("changelog: no git history here (a zip or archive): nothing to list", file=sys.stderr)
        return 2
    if not commits or _git("rev-parse", "--is-shallow-repository").strip() == "true":
        print(
            "changelog: needs the full history (git fetch --unshallow)",
            file=sys.stderr,
        )
        return 2
    try:
        text = render(commits, since=args.since)
    except ValueError as exc:  # a malformed --since
        print(f"changelog: {exc}", file=sys.stderr)
        return 2
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
