"""The instruments' virtual file systems (#1069, #1531).

Each instrument of a bench has its own folder, ``<root>/<instrument>``. The root is the bench's
files folder, kept across runs, or — for an in-memory bench, manual cases, a replay, which start
with no files — a temporary folder removed when the session closes.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from ..errors import Unknown


class InstrumentFiles:
    def __init__(self, root: Path | None, instruments: Iterable[str]) -> None:
        #: Where the virtual file systems live; None: a temporary folder of this session.
        self.dir = root
        self._names = tuple(instruments)
        self._tmp: str | None = None

    def root(self) -> Path:
        """The folder holding every instrument's virtual file system (created on first use)."""
        if self.dir is not None:
            self.dir.mkdir(parents=True, exist_ok=True)
            return self.dir
        if self._tmp is None:
            self._tmp = tempfile.mkdtemp(prefix="simubench-files-")
        return Path(self._tmp)

    def folder(self, name: str) -> Path:
        """The folder of one instrument's virtual file system: ``<root>/<instrument>``."""
        if name not in self._names:
            raise Unknown(name)
        folder = self.root() / name
        folder.mkdir(exist_ok=True)
        return folder

    def snapshot(self) -> dict[str, dict[str, str]]:
        """The sha256 of every file each instrument holds, by its path in the instrument's folder
        (``/``-separated); instruments without files are left out. A run record's header keeps it
        (D36): a replay starts with no files and warns when the run did not."""
        out: dict[str, dict[str, str]] = {}
        root = self.dir if self.dir is not None else (Path(self._tmp) if self._tmp is not None else None)
        if root is None or not root.is_dir():
            return out
        for name in self._names:
            folder = root / name
            if not folder.is_dir():
                continue
            files = {
                f.relative_to(folder).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in sorted(folder.rglob("*"))
                if f.is_file() and not f.is_symlink()
            }
            if files:
                out[name] = files
        return out

    def close(self) -> None:
        """Remove the temporary folder, if this session made one."""
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
