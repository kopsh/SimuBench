"""The version of a build, computed from the git history, no tags needed (D58).

``YYYY.M.D.N``: the date of the newest first-parent commit (committer date, UTC+8) and N, how many
first-parent commits that day has up to it, from 1. The same history always gives the same version;
a new commit on the same day gives a larger one, so ``pip`` upgrades. Nothing is written to the
repository: an internal build only fetches.

Used by hatchling (``[tool.hatch.version]``, expression ``version()``) and by ``changelog.py``.
Without git history (GitHub zip, ``git archive``) the date comes from ``.git_archival.txt`` and N is
0; an sdist carries the ``_version.py`` its builder wrote. Else ``0+unknown``.

    python scripts/version.py
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src" / "simubench" / "_version.py"


def day_of(epoch: int) -> tuple[int, int, int]:
    d = datetime.fromtimestamp(epoch, UTC).astimezone(TZ).date()
    return d.year, d.month, d.day


def name(day: tuple[int, int, int], n: int) -> str:
    return f"{day[0]}.{day[1]}.{day[2]}.{n}"


def numbered(epochs: list[int]) -> list[str]:
    """The version of each first-parent commit, given their committer epochs oldest first."""
    per_day: dict[tuple[int, int, int], int] = {}
    out = []
    for epoch in epochs:
        day = day_of(epoch)
        per_day[day] = per_day.get(day, 0) + 1
        out.append(name(day, per_day[day]))
    return out


def parse(version: str) -> tuple[int, ...]:
    """``2026.11.9.3`` as a tuple that sorts in version order."""
    return tuple(int(p) for p in version.split("."))


def _from_git(root: Path) -> str | None:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        if Path(top).resolve() != root.resolve():  # an sdist unpacked inside another repository
            return None
        log = subprocess.run(
            ["git", "log", "--first-parent", "--reverse", "--format=%ct"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    return numbered([int(e) for e in log])[-1] if log else None


def _from_archival(root: Path) -> str | None:
    try:
        text = (root / ".git_archival.txt").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^node-date: (\d{4}-\d\d-\d\dT\S+)$", text, re.MULTILINE)
    if not m:
        return None
    try:
        return name(day_of(int(datetime.fromisoformat(m.group(1)).timestamp())), 0)
    except ValueError:
        return None


def _from_version_file(path: Path) -> str | None:
    try:
        m = re.search(r'^__version__ = "([^"]+)"$', path.read_text(encoding="utf-8"), re.MULTILINE)
    except OSError:
        return None
    return m.group(1) if m else None


def version(root: Path = ROOT) -> str:
    return (
        _from_git(root)
        or _from_archival(root)
        or _from_version_file(root / "src" / "simubench" / "_version.py")
        or "0+unknown"
    )


if __name__ == "__main__":
    print(version())
