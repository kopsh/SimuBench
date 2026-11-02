#!/usr/bin/env python
"""Keep the agent skills (.claude/skills/*/SKILL.md) from going stale.

- every repo path a skill names in backticks (``src/...``, ``docs/...``, ``tests/...``, …) exists;
  paths with placeholders (``<id>``, ``*``, ``{a,b}``) are checked up to the placeholder;
- every skill has front matter with ``name`` equal to its directory and a ``description``;
- AGENTS.md names every skill, and names no skill that does not exist.

Exit code 1 with one line per problem. Fast and offline: part of ``scripts/check.sh``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude" / "skills"
ROOTS = (
    "src/",
    "docs/",
    "tests/",
    "web/",
    "scripts/",
    "validation/",
    "examples/",
    "acceptance/",
    "refcalc/",
    "schemas/",
    ".claude/",
)
PREFIXES = ("engine/", "library/", "scpi/", "server/", "models/", "access/", "tui/")  # under src/simubench


def checked_part(path: str) -> str:
    """The path up to its first placeholder, without a trailing partial name."""
    cut = re.split(r"[<*{]", path, maxsplit=1)[0]
    return cut if cut == path else cut[: cut.rfind("/") + 1]


def candidates(token: str) -> list[str]:
    token = token.split()[0].rstrip(",.:;")
    token = re.sub(r":\d+$", "", token)  # file:line
    if token.startswith(ROOTS):
        return [token]
    if token.startswith(PREFIXES) and "/" in token:
        return [f"src/simubench/{token}"]
    return []


def main() -> int:
    problems: list[str] = []
    names = sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())
    for name in names:
        path = SKILLS / name / "SKILL.md"
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        front = re.match(r"---\n(.*?)\n---\n", text, re.S)
        if (
            not front
            or f"name: {name}\n" not in front.group(1) + "\n"
            or "description:" not in front.group(1)
        ):
            problems.append(f"{rel}: front matter needs name: {name} and a description")
        for token in re.findall(r"`([^`\n]+)`", text):
            for cand in candidates(token):
                part = checked_part(cand)
                if part and not (ROOT / part).exists():
                    problems.append(f"{rel}: names {cand!r}, which does not exist")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"`([a-z0-9-]+)`", agents.split("## Skills", 1)[-1].split("\n## ", 1)[0]))
    for name in names:
        if name not in listed:
            problems.append(f"AGENTS.md: the Skills section does not name the skill {name!r}")
    for name in sorted(listed - set(names)):
        problems.append(f"AGENTS.md: the Skills section names {name!r}, which is not a skill")
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
