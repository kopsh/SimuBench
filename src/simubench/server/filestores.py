"""The bench and definition file stores behind one kind-keyed door (D94, #1729).

``BenchFiles`` and ``DefinitionFiles`` are the real seams, each speaking its own key — a bench by
its workspace-relative path, a definition by its id (the file's stem) — and its own wording for
changes. A caller that holds a file whose kind it only knows from data (the assistant's turn
record, #1729) used to hand-dispatch on ``kind`` at every step: the read, the change function, the
restore order, the session impact, the rel↔stem mapping, restore and save. This facade takes the
kind and the path once and dispatches; the path is always the workspace-relative one the record
keeps, and the id mapping lives here. A kind the door does not know is the domain error
:class:`Invalid`, never a silent fallback. ``resolve`` and the version history stay shared — they
are keyed by the relative path for both kinds — with only the definition's history key re-derived
here (:meth:`FileStores.history_rel`).

A changed definition carries one extra step its caller names at construction
(``on_definition_changed`` — the assistant closes its scratch sessions for the id): it fires after
a definition restore or save through :meth:`FileStores.restore`, and after a removal through
:meth:`FileStores.remove_created`, where the service used to call it inline.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..contracts import api as M
from ..library.workspace_layout import definition_rel_path
from .benchchanges import bench_changes
from .benchfiles import BenchFiles
from .definitionchanges import definition_changes
from .definitionfiles import DefinitionFiles
from .errors import Invalid


def _nothing_changed(def_id: str) -> None:
    return None


class _Side(Protocol):
    """What each kind's store answers: the relative path in, the kind's own key and vocabulary
    kept inside."""

    #: Restores land benches first, then definitions: the kind's rank in the rollback sort.
    order: int

    def read(self, rel: str) -> M.BenchDocument | M.DefinitionDocument: ...

    def changes(self, old: dict[str, Any], new: dict[str, Any]) -> list[M.DefinitionChange]: ...

    def history_rel(self, rel: str) -> str: ...

    def session_key(self, rel: str) -> Path | str: ...

    def restore(self, rel: str, seq: int | None, current: str | None, data: dict[str, Any]) -> None: ...

    def remove_created(self, rel: str, expected_sha256: str | None) -> None: ...


class _BenchSide:
    """The bench half: the key is the path itself."""

    order = 0

    def __init__(self, files: BenchFiles) -> None:
        self._files = files

    def read(self, rel: str) -> M.BenchDocument:
        return self._files.read(rel)

    def changes(self, old: dict[str, Any], new: dict[str, Any]) -> list[M.DefinitionChange]:
        return bench_changes(old, new)

    def history_rel(self, rel: str) -> str:
        return rel

    def session_key(self, rel: str) -> Path | str:
        return self._files.resolve(rel)

    def restore(self, rel: str, seq: int | None, current: str | None, data: dict[str, Any]) -> None:
        if seq is not None and current is not None:
            self._files.restore(rel, seq, current)
        else:
            self._files.save(rel, current, data, "restore")

    def remove_created(self, rel: str, expected_sha256: str | None) -> None:
        if expected_sha256 is not None:
            self._files.remove_created(rel, expected_sha256)


class _DefinitionSide:
    """The definition half: the store wants the id (the stem) where the caller holds the path, and
    a changed definition also fires ``on_changed``."""

    order = 1

    def __init__(
        self, files: BenchFiles, definitions: DefinitionFiles, on_changed: Callable[[str], None]
    ) -> None:
        self._files = files
        self._definitions = definitions
        self._on_changed = on_changed

    @staticmethod
    def _id(rel: str) -> str:
        return Path(rel).stem

    def read(self, rel: str) -> M.DefinitionDocument:
        return self._definitions.read(self._id(rel))

    def changes(self, old: dict[str, Any], new: dict[str, Any]) -> list[M.DefinitionChange]:
        return definition_changes(old, new)

    def history_rel(self, rel: str) -> str:
        return definition_rel_path(self._id(rel))

    def session_key(self, rel: str) -> Path | str:
        return self._id(rel)

    def restore(self, rel: str, seq: int | None, current: str | None, data: dict[str, Any]) -> None:
        def_id = self._id(rel)
        if seq is not None and current is not None:
            self._definitions.restore(def_id, seq, current)
        else:
            self._definitions.save(def_id, current, data, "restore")
        self._on_changed(def_id)

    def remove_created(self, rel: str, expected_sha256: str | None) -> None:
        if expected_sha256 is not None:
            self._files.remove_created(rel, expected_sha256)
        self._on_changed(self._id(rel))


class FileStores:
    """The kind-keyed door over :class:`BenchFiles` and :class:`DefinitionFiles` (D94): every
    method takes the kind and the workspace-relative path, and the two stores' key and vocabulary
    differences stay inside. Built by the assistant service, which hands in what a changed
    definition needs done (its scratch sessions closed)."""

    def __init__(
        self,
        files: BenchFiles,
        definitions: DefinitionFiles,
        on_definition_changed: Callable[[str], None] | None = None,
    ) -> None:
        self._sides: dict[str, _Side] = {
            "bench": _BenchSide(files),
            "definition": _DefinitionSide(
                files,
                definitions,
                on_definition_changed if on_definition_changed is not None else _nothing_changed,
            ),
        }

    def read(self, kind: str, rel: str) -> M.BenchDocument | M.DefinitionDocument:
        """The file's stored content with its diagnostics, through its kind's store."""
        return self._side(kind).read(rel)

    def changes(
        self, kind: str, old: dict[str, Any] | None, new: dict[str, Any] | None
    ) -> list[M.DefinitionChange]:
        """What turned ``old`` into ``new``, in the kind's own vocabulary."""
        return self._side(kind).changes(old or {}, new or {})

    def restore_order(self, kind: str) -> int:
        """The kind's rank in a rollback's restore order: benches first, then definitions."""
        return self._side(kind).order

    def history_rel(self, kind: str, rel: str) -> str:
        """The version history's key of a file: the path itself for a bench, the definition's
        canonical place re-derived from the id."""
        return self._side(kind).history_rel(rel)

    def session_key(self, kind: str, rel: str) -> Path | str:
        """The identity a running session is matched by: a bench's resolved path, a definition's id."""
        return self._side(kind).session_key(rel)

    def restore(
        self, kind: str, rel: str, seq: int | None, current: str | None, data: dict[str, Any]
    ) -> None:
        """Put the version before the turn back: as a recorded restore when the history has it and
        the file still holds what the turn left, else as a save of the recorded text. A definition
        counts as changed either way."""
        self._side(kind).restore(rel, seq, current, data)

    def remove_created(self, kind: str, rel: str, expected_sha256: str | None) -> None:
        """Delete a file the turn created, only while it still holds the content the caller checked
        (``None``: already gone, nothing to remove). A definition counts as changed either way."""
        self._side(kind).remove_created(rel, expected_sha256)

    def _side(self, kind: str) -> _Side:
        try:
            return self._sides[kind]
        except KeyError:
            raise Invalid(f"未知文件种类 {kind!r}：bench 或 definition") from None
