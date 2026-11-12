"""scripts/check_tracked.py fails on tracked package-manager caches or dependencies (issue #991)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
script = load_script(ROOT / "scripts" / "check_tracked.py")


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [".pnpm-store/v10/files/00/abc", "web/node_modules/react/index.js", "node_modules/x/y.js"],
)
def test_cache_and_dependency_paths_are_forbidden(path):
    assert script.forbidden(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "path", ["web/src/app.tsx", "docs/node_modules_notes.md", ".gitignore", "src/simubench/pnpm-store.py"]
)
def test_ordinary_paths_pass(path):
    assert not script.forbidden(path)


@pytest.mark.unit
def test_main_reports_and_fails_on_a_tracked_cache(monkeypatch, capsys):
    monkeypatch.setattr(script, "tracked_files", lambda: ["src/a.py", ".pnpm-store/v10/x"])
    assert script.main() == 1
    assert ".pnpm-store/v10/x" in capsys.readouterr().out


@pytest.mark.unit
def test_main_passes_on_clean_tree(monkeypatch):
    monkeypatch.setattr(script, "tracked_files", lambda: ["src/a.py"])
    assert script.main() == 0


@pytest.mark.unit
def test_this_repository_tracks_none():
    out = subprocess.run(["git", "ls-files"], capture_output=True, encoding="utf-8", cwd=ROOT)
    if out.returncode:
        pytest.skip("not a git checkout")
    assert not [p for p in out.stdout.splitlines() if script.forbidden(p)]
