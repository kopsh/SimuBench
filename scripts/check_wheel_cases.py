"""The wheel carries the built-in models' manual cases (#1352, #1377).

Usage: ``python3 scripts/check_wheel_cases.py dist/simubench-*.whl``

Every ``tests/models/<folder>`` whose ``cases.yaml`` names a built-in definition must be in the
wheel as ``simubench/library/model_cases/<folder>/cases.yaml``, together with the example benches
(``simubench/examples/benches``) some of them name; a test-only model's folder must not be. A
missing file fails here instead of the UI quietly showing "no built-in cases". The expected set is
read from the source tree independently of hatch_build.py, so the two cannot share one mistake.
Standard library only: the CI step runs it with the system Python.
"""

from __future__ import annotations

import functools
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = "simubench/library/model_cases/"
BENCHES = "simubench/examples/benches/"


def _model_of(cases: Path) -> str | None:
    for line in cases.read_text(encoding="utf-8").splitlines():
        if line.startswith("model:"):
            return line.split(":", 1)[1].split("#", 1)[0].strip().strip("\"'") or None
    return None


@functools.cache
def expected_folders(root: Path = ROOT) -> set[str]:
    """The ``tests/models`` folders whose cases are a built-in model's.

    Cached per process: ``problems`` rescans on every call and the unit test calls the pair five
    times, which put the test at the D49 budget's edge on a loaded machine. The tree does not
    change during a run.
    """
    builtin = root / "src" / "simubench" / "library" / "instruments"
    return {
        cases.parent.name
        for cases in (root / "tests" / "models").glob("*/cases.yaml")
        if (model := _model_of(cases)) and (builtin / f"{model}.yaml").is_file()
    }


def problems(names: list[str], root: Path = ROOT) -> list[str]:
    """What is wrong with a wheel whose members are ``names`` (empty: nothing)."""
    want = expected_folders(root)
    have = {
        n[len(CASES) :].split("/", 1)[0] for n in names if n.startswith(CASES) and n.endswith("/cases.yaml")
    }
    out = [f"missing {CASES}{f}/cases.yaml" for f in sorted(want - have)]
    out += [f"not a built-in model's cases: {CASES}{f}/cases.yaml" for f in sorted(have - want)]
    if not want:
        out.append("no built-in model has manual cases under tests/models")
    if not any(n.startswith(BENCHES) and n.endswith(".bench.yaml") for n in names):
        out.append(f"missing the example benches under {BENCHES}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    found = problems(zipfile.ZipFile(argv[1]).namelist())
    for p in found:
        print(p)
    if not found:
        print(f"{len(expected_folders())} built-in cases folders and the example benches are in the wheel")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
