"""The TUI knows no instrument model by name (D42: a model is a definition, not code).

A layout special-cased for one model's name in ``src/simubench/tui`` would never be noticed by any
other check. Every built-in and validation definition's ``id`` and ``model`` is looked for in the
TUI's source, comments and docstrings included; a hit fails with ``file:line`` and the name.
There is no allow-list: use an example that names no model (``awg · MODEL-A``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TUI = ROOT / "src" / "simubench" / "tui"
DEFINITION_DIRS = [
    ROOT / "src" / "simubench" / "library" / "instruments",
    ROOT / "validation" / "instruments",
]
MIN_LENGTH = 4  # shorter names ("sb", "b") would match ordinary words


def top_level_names(head: str) -> list[str]:
    """The values of ``id:`` and ``model:`` at the top level of a definition (column 0), without
    quotes or a trailing comment. Read line by line, not as a YAML parse: the definitions are large
    and this test must stay under 100 ms (D49)."""
    names = []
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if colon and key in ("id", "model"):
            value = value.partition("#")[0].strip().strip("\"'").strip()
            if value:
                names.append(value)
    return names


def model_names() -> set[str]:
    names: set[str] = set()
    for folder in DEFINITION_DIRS:
        for path in folder.glob("*.yaml"):
            if path.name.endswith(".coverage.yaml"):
                continue
            head = path.read_text(encoding="utf-8")[:4000]  # the identity block opens the file
            names.update(m for m in top_level_names(head) if len(m) >= MIN_LENGTH)
    return names


#: Collected at import time (collection, not the 100 ms call phase, D49): reading ~40 definition
#: heads and ~40 source files is the test's whole cost otherwise, and under xdist load it grazed
#: the budget (#651). The call only scans memory.
MODEL_NAMES = model_names()
TUI_FILES = sorted(TUI.rglob("*.py"))


def find_hits(names: set[str], files: list[Path]) -> list[str]:
    """``file:line  name`` for every line that holds a name, case-insensitively. A substring test
    over each file's lowered text first (fast), the line scan only for the files that hit."""
    lowered = sorted({n.lower() for n in names})
    hits: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        low = text.lower()
        found = [n for n in lowered if n in low]
        if not found:
            continue
        shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        for number, line in enumerate(low.splitlines(), 1):
            hits.extend(f"{shown}:{number}  {n}" for n in found if n in line)
    return hits


@pytest.mark.unit
def test_the_tui_source_names_no_instrument_model():
    assert len(MODEL_NAMES) > 20, "the definitions were not found: the guard would pass on nothing"
    hits = find_hits(MODEL_NAMES, TUI_FILES)
    assert not hits, "the TUI must not name a model (D42):\n" + "\n".join(hits)


@pytest.mark.unit
def test_the_guard_does_catch_a_model_name(tmp_path):
    source = tmp_path / "x.py"
    source.write_text('"""one line"""\n# special-case the Siglent-SDG2042X layout\n', encoding="utf-8")
    hits = find_hits({"siglent-sdg2042x", "SDG2042X"}, [source])
    assert hits and all(":2 " in h for h in hits)
