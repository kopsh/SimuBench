"""An instrument's mass storage over the API (#1069): list the files, download one.

Reads only, never through the instrument (no SCPI, no clock): the files are the folder
``Session.instrument_files`` gives. A path is the listing's (``INT/States/a.sta``); one that would
leave the instrument's folder or pass a symbolic link is refused (``PermissionError``), a missing
instrument or file is a ``KeyError``.
"""

from __future__ import annotations

import hashlib

from ...engine.session import Session
from ...library.components.files import inside
from ...models import api as M


def listing(session: Session, name: str) -> M.InstrumentFiles:
    root = session.instrument_files(name)
    files = [
        M.InstrumentFile(
            path=f.relative_to(root).as_posix(),
            size=f.stat().st_size,
            sha256=hashlib.sha256(f.read_bytes()).hexdigest(),
            modified=f.stat().st_mtime,
        )
        for f in sorted(root.rglob("*"))
        if f.is_file() and inside(root, f) and not f.name.startswith(".simubench-tmp-")
    ]
    return M.InstrumentFiles(instrument=name, files=files)


def read(session: Session, name: str, path: str) -> bytes:
    root = session.instrument_files(name)
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts or ":" in path:
        raise PermissionError(f"{path!r} is not a file of {name}")
    target = root.joinpath(*parts)
    if not inside(root, target):
        raise PermissionError(f"{path!r} is not a file of {name}")
    if not target.is_file():
        raise KeyError(f"{name} has no file {path!r}")
    return target.read_bytes()
