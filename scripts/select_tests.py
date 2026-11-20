"""Decide which tests a pull request runs (#1680, D49): the models it changed whole, the others smoke.

The fast checks used to run every real model's tests on every pull request, so their time grew with
the number of models. A change that touches one model's files cannot break another model, so only
that model's tests (and a smoke subset of the other models' manual cases, ``tests/model_scope.py``)
run. A change to shared code — the engine, the components, SCPI, the loader, the test helpers, the
build — or any path this script does not know runs everything, as before; so does ``main``.

    uv run python scripts/select_tests.py [--base origin/main] [--full] [--explain]

prints the pytest arguments (empty: run everything); ``--explain`` prints why instead.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTRUMENTS = Path("src/simubench/library/instruments")
#: Paths that no test depends on: a change to them selects nothing by itself.
NEUTRAL_PREFIXES = ("docs/", "web/", "manuals/", ".claude/", "schemas/")
NEUTRAL_SUFFIXES = (".md",)


@dataclass
class Selection:
    full: bool
    reason: str
    models: set[str] = field(default_factory=set)

    def args(self) -> list[str]:
        return [] if self.full else [f"--model-scope={','.join(sorted(self.models))}"]


def model_ids(root: Path = ROOT) -> set[str]:
    """The ids of the library's instrument definitions (``<id>.yaml`` beside its coverage table)."""
    return {
        p.name.removesuffix(".yaml")
        for p in (root / INSTRUMENTS).glob("*.yaml")
        if not p.name.endswith(".coverage.yaml") and not p.name.endswith(".cases.yaml")
    }


def extends_of(root: Path = ROOT) -> dict[str, set[str]]:
    """For each model, the models that ``extends`` it (directly): a base's change reaches them."""
    children: dict[str, set[str]] = {}
    for path in (root / INSTRUMENTS).glob("*.yaml"):
        if path.name.endswith(".coverage.yaml"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("extends:"):
                base = line.split(":", 1)[1].strip().strip("'\"")
                children.setdefault(base, set()).add(path.name.removesuffix(".yaml"))
                break
    return children


def marked_models(path: Path) -> set[str]:
    """The models a test file declares with ``pytestmark = pytest.mark.model(...)``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "model"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "mark"
        ):
            out.update(a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
    return out


def _model_of_path(rel: str, ids: set[str]) -> str | None:
    p = Path(rel)
    if p.parent == INSTRUMENTS:
        stem = p.name.split(".", 1)[0]
        return stem if stem in ids else None
    if rel.startswith("tests/models/") and len(p.parts) > 3 and p.parts[2] in ids:
        return p.parts[2]
    return None


def select(changed: list[str], root: Path = ROOT) -> Selection:
    """``changed``: repo-relative paths of the pull request. Anything unknown selects everything."""
    if not changed:
        return Selection(True, "no changed paths known: run everything")
    ids = model_ids(root)
    children = extends_of(root)
    models: set[str] = set()
    why: list[str] = []
    for rel in changed:
        model = _model_of_path(rel, ids)
        if model:
            models.add(model)
            continue
        if rel.startswith(("tests/test_", "acceptance/test_")) and rel.endswith(".py"):
            models |= marked_models(root / rel)  # an unmarked test file runs anyway
            continue
        if rel.startswith("tests/models/"):  # a framework model's files (acme-dm1, contract-*)
            continue
        if rel.startswith(NEUTRAL_PREFIXES) or rel.endswith(NEUTRAL_SUFFIXES):
            continue
        why.append(rel)
    if why:
        shown = ", ".join(why[:3]) + (f" (+{len(why) - 3} more)" if len(why) > 3 else "")
        return Selection(True, f"shared or unknown paths changed ({shown}): run everything")
    frontier = list(models)
    while frontier:  # a changed base reaches the models that extend it, transitively
        for child in children.get(frontier.pop(), ()):
            if child not in models:
                models.add(child)
                frontier.append(child)
    shown = ", ".join(sorted(models)) or "no model"
    return Selection(False, f"model-scoped: {shown} whole, the other models smoke only", models)


def changed_paths(base: str) -> list[str]:
    """Paths changed against the merge base with ``base``, committed or not, and new files."""

    def git(*args: str) -> list[str]:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
        return [line for line in out.stdout.splitlines() if line]

    merge_base = git("merge-base", base, "HEAD")[0]
    return sorted(
        {*git("diff", "--name-only", merge_base), *git("ls-files", "--others", "--exclude-standard")}
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--full", action="store_true", help="run everything")
    ap.add_argument("--explain", action="store_true", help="print the reason, not the arguments")
    args = ap.parse_args(argv)
    # CI: anything but a pull request (main, a manual run) runs everything (#1680).
    event = os.environ.get("GITHUB_EVENT_NAME")
    if args.full:
        sel = Selection(True, "--full: run everything")
    elif event and event != "pull_request":
        sel = Selection(True, f"{event}: run everything")
    else:
        try:
            sel = select(changed_paths(args.base))
        except (subprocess.CalledProcessError, OSError, IndexError) as exc:
            sel = Selection(True, f"the changed paths could not be read ({exc}): run everything")
    print(sel.reason if args.explain else " ".join(sel.args()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
