"""An instrument's mass storage (#1069): the drives, folders and files a script reaches with
``MMEMory`` or ``:DISK`` commands, kept as a real folder on the engineer's computer.

Each instrument of a bench has its own folder (``Session.instrument_files``: under
``.simubench/files/<bench>/<instrument>/`` in a workspace, a temporary folder otherwise); each
drive the definition declares (``INT:``, ``USB:``, ``C:``) is a sub-folder of it. A path a script
gives is resolved like the instrument resolves it — the drive, an absolute or a relative path
against the current folder, names compared without regard to case — and must stay inside the
instrument's folder: ``..``, a name the instrument forbids, a host path and a symbolic link are
refused with the definition's file-name error, before anything is read or written. The folder holds
data only; nothing in it is ever run.

The pure functions below split and check a path; the block keeps only the current folder and the
file named for a download.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError

#: Platform limits, whatever a definition declares (security.md): one instrument's files together,
#: and one file.
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
#: Characters no instrument allows in a folder or file name (the Trueform list, a superset of the
#: Windows one Infiniium uses); ``*`` and ``?`` are wildcards where a command takes them.
FORBIDDEN = set('\\/:*?"<>|') | {chr(c) for c in range(32)}
MAX_PATH = 240
_TMP = ".simubench-tmp-"
_DRIVE = re.compile(r"^([A-Za-z][A-Za-z0-9]*):(.*)$", re.DOTALL)


class FileNameError(ValueError):
    """A path the instrument refuses (a name error: the reason is the message)."""


def split_path(text: str) -> tuple[str | None, bool, list[str]]:
    """``(drive word or None, absolute, names)`` of an instrument path: ``"INT:\\a\\b.csv"`` is
    ``("INT", True, ["a", "b.csv"])``; ``"a/b"`` is ``(None, False, ["a", "b"])``. ``.`` parts are
    dropped; ``..`` is refused (a script never leaves the folder it names, #1069)."""
    text = text.strip()
    drive = None
    m = _DRIVE.match(text)
    if m:
        drive, text = m.group(1), m.group(2)
    absolute = drive is not None or text[:1] in ("\\", "/")
    names = [n for n in re.split(r"[\\/]", text) if n not in ("", ".")]
    for n in names:
        if n == "..":
            raise FileNameError("parent folder (..) not allowed")
    return drive, absolute, names


def check_name(name: str, *, wildcards: bool = False) -> None:
    """A folder or file name the instrument accepts (no separators, no forbidden characters)."""
    bad = FORBIDDEN - ({"*", "?"} if wildcards else set())
    if any(c in bad for c in name) or name.startswith(_TMP):
        raise FileNameError("invalid character in name")
    if name.strip(". ") == "":
        raise FileNameError("path name missing")


def find(folder: Path, name: str) -> Path:
    """``folder/name``, matching an existing entry without regard to case (the instruments' file
    names are not case sensitive); a new name keeps its case."""
    exact = folder / name
    if exact.exists() or exact.is_symlink():
        return exact
    if folder.is_dir():
        low = name.casefold()
        for entry in folder.iterdir():
            if entry.name.casefold() == low:
                return entry
    return exact


def inside(root: Path, path: Path) -> bool:
    """Whether ``path`` stays in ``root`` with no symbolic link on the way (#1069)."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    step = root
    for part in rel.parts:
        step = step / part
        if step.is_symlink():
            return False
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    return real == real_root or real.startswith(real_root + os.sep)


def tree_bytes(folder: Path) -> int:
    """The bytes the files under ``folder`` take."""
    if not folder.is_dir():
        return 0
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file() and not f.is_symlink())


@component("file_system")
class FileSystem(Component):
    """An instrument's drives, folders and files (MMEMory, :DISK), on the engineer's computer.

    ``drives`` lists the drive words the instrument knows (``INTernal`` answers to ``INT:`` and
    ``INTERNAL:``; the first is the default drive); ``home`` is the current folder after power-on
    (and after ``*RST`` when ``reset_home``); it exists from the start. Errors are the definition's
    codes: ``name_error`` for a refused path (escape, forbidden character, symbolic link, a folder
    not empty), ``not_found`` for a missing file, folder or drive, ``full`` for the capacity.
    Catalogs: ``mmemory`` gives ``<used>,<free>{,"<name>,<type>,<size>"}`` (Keysight MMEMory);
    ``lines`` gives the count, then one name per line. Model limits: the drives are folders of the
    engineer's computer, so the space a catalog reports is the files' bytes against ``capacity``,
    not the instrument's own bookkeeping; no hidden or system files, no file locks.
    """

    defaults = {
        "drives": ["INTernal"],  # the drive words, the first the default (INTernal, USB, C ...)
        "home": "",  # the current folder at power-on, e.g. INT:\ or C:\Users\Public\Documents\Infiniium
        "reset_home": True,  # *RST makes home the current folder again
        "separator": "\\",  # the separator a reply writes between folder names
        "capacity": 1.0e9,  # bytes a catalog reports as the size of each drive (at most 256 MiB is kept)
        "file_limit": 6.4e7,  # largest file, bytes (at most 64 MiB)
        "catalog": "mmemory",  # mmemory: used,free,"name,type,size"...; lines: count, then names
        "catalog_sign": True,  # the catalog's numbers carry a + sign (Trueform: +1000000000)
        "file_types": {},  # catalog type word by extension, e.g. {".sta": STAT, ".csv": ASC}
        "folder_type": "FOLD",  # catalog type word of a folder
        "name_error": -257,  # a path the instrument refuses
        "not_found": -256,  # a file, folder or drive that does not exist
        "full": -254,  # no room on the drive
    }
    choices = {"catalog": ("mmemory", "lines")}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cwd: tuple[str, tuple[str, ...]] | None = None  # (drive, folder names): None = home
        self.download: tuple[str, tuple[str, ...]] | None = None  # the file DOWNload:FNAMe named

    def reset(self) -> None:
        if V.flag(self.param("reset_home")):
            self.cwd = None
        self.download = None

    # ---- where things are ------------------------------------------------------------------------

    def _root(self) -> Path:
        """The instrument's folder, with the home folder in it (the instrument ships with it)."""
        assert self.owner is not None
        root = self.session.instrument_files(self.owner.name)
        drive, names = self._home()
        root.joinpath(drive, *names).mkdir(parents=True, exist_ok=True)
        return root

    def _drives(self) -> list[str]:
        drives = [str(d) for d in self.param("drives") or []]
        if not drives:
            raise ScpiError(-300, f"{self.uid}: no drives declared")
        return drives

    def _drive(self, word: str | None) -> str:
        """The declared drive a word names (its short form is the folder and the reply)."""
        if word is None:
            return self._here()[0]
        for d in self._drives():
            if V.matches_word(word, d):
                return V.short_form(d).upper()
        raise ScpiError(int(self.param("not_found")), "drive name missing or not recognized")

    def _home(self) -> tuple[str, tuple[str, ...]]:
        home = str(self.param("home") or "")
        if not home:
            return V.short_form(self._drives()[0]).upper(), ()
        drive, _absolute, names = split_path(home)
        return self._drive(drive) if drive else V.short_form(self._drives()[0]).upper(), tuple(names)

    def _here(self) -> tuple[str, tuple[str, ...]]:
        return self._home() if self.cwd is None else self.cwd

    def _err(self, kind: str, text: str) -> ScpiError:
        return ScpiError(int(self.param(kind)), text)

    def resolve(self, text: str, *, wildcards: bool = False) -> tuple[str, tuple[str, ...]]:
        """An instrument path as ``(drive, names)``: against the current folder when relative."""
        try:
            word, absolute, names = split_path(text)
            for n in names:
                check_name(n, wildcards=wildcards)
        except FileNameError as e:
            raise self._err("name_error", str(e)) from None
        if len(text) > MAX_PATH:
            raise self._err("name_error", "path too long")
        drive = self._drive(word)
        if absolute:
            return drive, tuple(names)
        here_drive, here = self._here()
        return here_drive, (*here, *names)

    def host(self, place: tuple[str, tuple[str, ...]]) -> Path:
        """The file or folder on the engineer's computer, found without regard to case; refused when
        it would leave the instrument's folder or pass a symbolic link."""
        root = self._root()
        path = root / place[0]
        path.mkdir(exist_ok=True)
        for n in place[1]:
            path = find(path, n)
        if not inside(root, path):
            raise self._err("name_error", "access denied")
        return path

    def show(self, place: tuple[str, tuple[str, ...]]) -> str:
        """A path as the instrument writes it: ``INT:\\States``."""
        sep = str(self.param("separator"))
        return f"{place[0]}:{sep}" + sep.join(place[1])

    # ---- capacity and the file events --------------------------------------------------------------

    def _limits(self) -> tuple[int, int]:
        total = min(int(float(self.param("capacity"))), MAX_TOTAL_BYTES)
        one = min(int(float(self.param("file_limit"))), MAX_FILE_BYTES)
        return total, one

    def _room(self, adding: int, replacing: Path | None = None) -> None:
        total, one = self._limits()
        if adding > one:
            raise self._err("name_error", "file too large")
        old = replacing.stat().st_size if replacing is not None and replacing.is_file() else 0
        if tree_bytes(self._root()) - old + adding > total:
            raise self._err("full", "media full")

    def _event(self, action: str, place: tuple[str, tuple[str, ...]], data: bytes | None = None) -> None:
        assert self.owner is not None
        event: dict[str, Any] = {"action": action, "path": self.show(place)}
        if data is not None:
            event["size"] = len(data)
            event["sha256"] = hashlib.sha256(data).hexdigest()
        self.session.instrument_event(self.owner, "file", event)

    def store(self, text: str, data: bytes) -> None:
        """Write ``data`` to a file (the folder must exist), replacing what it held; atomic."""
        place = self.resolve(text)
        if not place[1]:
            raise self._err("name_error", "path name missing")
        path = self.host(place)
        if path.is_dir():
            raise self._err("name_error", "path is a folder name")
        if not path.parent.is_dir():
            raise self._err("not_found", "folder not found")
        self._room(len(data), path)
        fd, tmp = tempfile.mkstemp(prefix=_TMP, dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._event("write", place, data)

    def load(self, text: str) -> bytes:
        """The bytes of a file."""
        path = self.host(self.resolve(text))
        if not path.is_file():
            raise self._err("not_found", "file not found")
        return path.read_bytes()

    # ---- operations ------------------------------------------------------------------------------

    @op("folder")
    def op_folder(self, name: str | None = None, query: bool = False, **_: Any) -> OpResult:
        """The current folder (MMEMory:CDIRectory, :DISK:CDIRectory); the query (and :DISK:PWD?)
        answers it quoted. The folder must exist."""
        if query or name is None:
            return OpResult(V.format_string(self.show(self._here())))
        place = self.resolve(name)
        if not self.host(place).is_dir():
            raise self._err("not_found", "folder not found")
        self.cwd = place
        return OpResult()

    @op("working_folder")
    def op_working_folder(self, **_: Any) -> OpResult:
        """The current folder quoted, or an empty string while it is still ``home`` (Infiniium
        :DISK:PWD?: empty until :DISK:CDIRectory changes it)."""
        return OpResult(V.format_string("" if self.cwd is None else self.show(self.cwd)))

    @op("make_folder")
    def op_make_folder(self, name: str, **_: Any) -> OpResult:
        """Make a folder (MDIRectory); its parent must exist."""
        place = self.resolve(name)
        path = self.host(place)
        if not place[1]:
            raise self._err("name_error", "path name missing")
        if path.exists():
            raise self._err("name_error", "file or folder already exists")
        if not path.parent.is_dir():
            raise self._err("not_found", "folder not found")
        path.mkdir()
        self._event("make_folder", place)
        return OpResult()

    @op("remove_folder")
    def op_remove_folder(self, name: str, **_: Any) -> OpResult:
        """Remove an empty folder (RDIRectory); not the current one, not a drive."""
        place = self.resolve(name)
        path = self.host(place)
        if not path.is_dir():
            raise self._err("not_found", "folder not found")
        if not place[1] or place == self._here():
            raise self._err("name_error", "folder is default folder")
        if any(path.iterdir()):
            raise self._err("name_error", "folder not empty")
        path.rmdir()
        self._event("remove_folder", place)
        return OpResult()

    @op("delete")
    def op_delete(self, name: str, **_: Any) -> OpResult:
        """Delete a file (DELete); ``*`` and ``?`` in the file name delete every match."""
        place = self.resolve(name, wildcards=True)
        if not place[1]:
            raise self._err("name_error", "path name missing")
        pattern = place[1][-1]
        if any(c in pattern for c in "*?"):
            folder = self.host((place[0], place[1][:-1]))
            if not folder.is_dir():
                raise self._err("not_found", "folder not found")
            hits = [
                f
                for f in sorted(folder.iterdir())
                if f.is_file()
                and not f.is_symlink()
                and not f.name.startswith(_TMP)
                and fnmatch.fnmatchcase(f.name.casefold(), pattern.casefold())
            ]
            for f in hits:
                f.unlink()
                self._event("delete", (place[0], (*place[1][:-1], f.name)))
            return OpResult()
        path = self.host(place)
        if path.is_dir():
            raise self._err("name_error", "path is a folder name")
        if not path.is_file():
            raise self._err("not_found", "file not found")
        path.unlink()
        self._event("delete", place)
        return OpResult()

    def _target(self, source: Path, name: str) -> tuple[tuple[str, tuple[str, ...]], Path]:
        """Where a copy or move goes: a folder keeps the source's name."""
        place = self.resolve(name)
        path = self.host(place)
        if path.is_dir():
            place = (place[0], (*place[1], source.name))
            path = self.host(place)
        if not place[1] or not path.parent.is_dir():
            raise self._err("not_found", "folder not found")
        return place, path

    @op("copy")
    def op_copy(self, source: str, target: str, **_: Any) -> OpResult:
        """Copy a file (COPY); to a folder it keeps its name; an existing file is replaced."""
        src = self.host(self.resolve(source))
        if not src.is_file():
            raise self._err("not_found", "file not found")
        place, dst = self._target(src, target)
        data = src.read_bytes()
        self._room(len(data), dst)
        shutil.copyfile(src, dst)
        self._event("write", place, data)
        return OpResult()

    @op("move")
    def op_move(self, source: str, target: str, **_: Any) -> OpResult:
        """Move or rename a file (MOVE); to a folder it keeps its name."""
        src_place = self.resolve(source)
        src = self.host(src_place)
        if not src.is_file():
            raise self._err("not_found", "file not found")
        place, dst = self._target(src, target)
        data = src.read_bytes()
        os.replace(src, dst)
        self._event("delete", src_place)
        self._event("write", place, data)
        return OpResult()

    @op("catalog")
    def op_catalog(
        self,
        name: str | None = None,
        extensions: list[str] | None = None,
        folders: bool = True,
        types: dict[str, str] | None = None,
        **_: Any,
    ) -> OpResult:
        """The files and folders of a folder (CATalog?, :DISK:DIRectory?), the current one when no
        name is given. The command's ``args`` narrow it: ``extensions`` keeps only those files,
        ``folders: false`` leaves the folders out, ``types`` replaces ``file_types``."""
        place = self.resolve(name) if name else self._here()
        path = self.host(place)
        if not path.is_dir():
            raise self._err("not_found", "folder not found")
        wanted = {e.casefold() for e in extensions or []}
        kinds = {str(k).casefold(): str(v) for k, v in (types or self.param("file_types") or {}).items()}
        entries: list[tuple[str, str, int]] = []
        for e in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
            if e.is_symlink() or e.name.startswith(_TMP):
                continue
            if e.is_dir():
                if folders:
                    entries.append((e.name, str(self.param("folder_type")), 0))
            elif not wanted or e.suffix.casefold() in wanted:
                entries.append((e.name, kinds.get(e.suffix.casefold(), ""), e.stat().st_size))
        if self.choice("catalog") == "lines":
            sep = str(self.param("separator"))
            lines = [n + (sep if t == str(self.param("folder_type")) else "") for n, t, _s in entries]
            return OpResult("\n".join([str(len(lines)), *lines]).encode())
        used = tree_bytes(self._root() / place[0])
        free = max(0, int(float(self.param("capacity"))) - used)
        sign = "+" if V.flag(self.param("catalog_sign")) else ""
        parts = [f"{sign}{used}", f"{sign}{free}"]
        parts += [V.format_string(f"{n},{t},{s}").decode() for n, t, s in entries]
        return OpResult(",".join(parts).encode())

    @op("read")
    def op_read(self, name: str, **_: Any) -> OpResult:
        """A file's bytes as a definite-length block (MMEMory:UPLoad?, :DISK:GETFILE?)."""
        return OpResult(V.format_block(self.load(name)))

    @op("write")
    def op_write(self, name: str, data: bytes, **_: Any) -> OpResult:
        """Write a block to a file (:DISK:PUTFILE), replacing it."""
        self.store(name, data)
        return OpResult()

    @op("download_name")
    def op_download_name(self, name: str, **_: Any) -> OpResult:
        """Name the file the next download writes (MMEMory:DOWNload:FNAMe); it is created empty
        when it does not exist."""
        place = self.resolve(name)
        path = self.host(place)
        if not path.exists():
            self.store(name, b"")
        elif path.is_dir():
            raise self._err("name_error", "path is a folder name")
        self.download = place
        return OpResult()

    @op("download")
    def op_download(self, data: bytes, **_: Any) -> OpResult:
        """Write a block to the file DOWNload:FNAMe named (MMEMory:DOWNload:DATA)."""
        if self.download is None:
            raise self._err("not_found", "no file named for the download (DOWNload:FNAMe)")
        self.store(self.show(self.download), data)
        return OpResult()
