"""What a workspace's ``.simubench/`` holds, how much, and which of it has lost its file (#1238).

Three kinds of data are tied to a file of the workspace and stay behind when that file is deleted
or moved outside SimuBench (a file manager, a git checkout):

* ``history/<path>/``: the saved versions of ``<path>`` (a bench or ``instruments/<id>.yaml``);
* ``server/assistant/<bench path>.json``: the conversation of a bench;
* ``files/<bench path without suffix>/``: the files its instruments keep between runs (#1069).

An entry is an *orphan* when the file it belongs to no longer exists. Nothing here deletes without
being asked; version history is kept by default, because it is how a deleted file comes back.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Kind = Literal["history", "assistant", "files"]
KINDS: tuple[Kind, ...] = ("history", "assistant", "files")
BENCH_SUFFIXES = (".bench.yaml", ".bench.json")
ROOT = ".simubench"


@dataclass(frozen=True)
class Entry:
    kind: Kind
    name: str  # the workspace file it belongs to, as a posix path (files: the bench without suffix)
    path: Path
    bytes: int
    orphan: bool


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _history(workspace: Path) -> list[Entry]:
    root = workspace / ROOT / "history"
    if not root.is_dir():
        return []
    out = []
    for index in sorted(root.rglob("index.jsonl")):
        unit = index.parent
        name = _rel(unit, root)
        out.append(Entry("history", name, unit, _size(unit), not (workspace / name).is_file()))
    return out


def _assistant(workspace: Path) -> list[Entry]:
    root = workspace / ROOT / "assistant"
    if not root.is_dir():
        return []
    out = []
    for f in sorted(root.rglob("*.json")):
        name = _rel(f, root).removesuffix(".json")
        out.append(Entry("assistant", name, f, _size(f), not (workspace / name).is_file()))
    return out


def _live(workspace: Path, files_root: Path, folder: Path) -> bool:
    stem = workspace / _rel(folder, files_root)
    return any(Path(f"{stem}{s}").is_file() for s in BENCH_SUFFIXES)


def _orphan_folders(workspace: Path, files_root: Path, folder: Path) -> tuple[list[Path], bool]:
    """The orphan folders under ``folder`` (a folder whose subfolders are all orphans counts once),
    and whether a live bench's folder is inside or is ``folder`` itself."""
    if folder != files_root and _live(workspace, files_root, folder):
        return [], True
    children = sorted(c for c in folder.iterdir() if c.is_dir() and not c.is_symlink())
    found: list[Path] = []
    any_live = False
    for child in children:
        sub, live = _orphan_folders(workspace, files_root, child)
        found += sub
        any_live = any_live or live
    if any_live or folder == files_root:
        return found, any_live
    return ([folder] if children or any(folder.iterdir()) else []), False


def _files(workspace: Path) -> list[Entry]:
    root = workspace / ROOT / "files"
    if not root.is_dir():
        return []
    orphans, _ = _orphan_folders(workspace, root, root)
    out = [Entry("files", _rel(p, root), p, _size(p), True) for p in orphans]
    live: list[Path] = []
    for d in sorted(p for p in root.rglob("*") if p.is_dir() and not p.is_symlink()):
        if any(o == d or o in d.parents for o in orphans):
            continue
        if _live(workspace, root, d):
            live.append(d)
    out += [Entry("files", _rel(d, root), d, _size(d), False) for d in live]
    return sorted(out, key=lambda e: e.name)


def inventory(workspace: Path) -> list[Entry]:
    """Every entry of the workspace's ``.simubench/`` tied to a file, with its size."""
    workspace = Path(workspace)
    return [*_history(workspace), *_assistant(workspace), *_files(workspace)]


def clean_orphans(workspace: Path, kinds: tuple[Kind, ...] = ("assistant", "files")) -> list[Entry]:
    """Delete the orphans of ``kinds`` and return what went. History is not among the defaults."""
    removed = []
    for e in inventory(workspace):
        if e.orphan and e.kind in kinds:
            if e.path.is_dir():
                shutil.rmtree(e.path, ignore_errors=True)
            else:
                e.path.unlink(missing_ok=True)
            removed.append(e)
    return removed


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")
