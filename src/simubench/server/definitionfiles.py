"""Instrument definition files in the workspace (instrument-authoring.md 5.4): read, create, edit
with JSON Patch under optimistic concurrency (the sha256 read, as for benches, D25), and keep every
save in the version history (D35). Definitions outside the workspace are read only."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..contracts import api as M
from ..engine import jsonpatch
from ..engine.validate import Report
from ..engine.validate_definition import validate_definition_data
from ..library.loader import dump_yaml, layered_definition_data, load_yaml, parse_error_text
from ..models.migrate import upgrade
from . import definitionstructure
from .benchfiles import BenchFiles
from .definitionchanges import definition_changes
from .errors import Forbidden, Invalid
from .history import Source

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
FOLDER = "instruments"
#: The build area's positions beside a definition (#1207).
LAYOUT_SUFFIX = ".layout.json"


class DefinitionFiles:
    def __init__(self, files: BenchFiles) -> None:
        self.files = files

    def rel_path(self, def_id: str) -> str:
        if not ID.fullmatch(def_id):
            raise Invalid(f"{def_id!r}: an id is letters, digits, '.', '_' and '-'")
        return f"{FOLDER}/{def_id}.yaml"

    def read(self, def_id: str) -> M.DefinitionDocument:
        rel = self.rel_path(def_id)
        path = self.files.workspace / rel
        if path.is_file():
            raw, editable = path.read_bytes(), True
        else:
            raw, editable, rel = self.files.library(None).path(def_id).read_bytes(), False, None  # type: ignore[assignment]
        return self._document(def_id, rel, editable, raw, _parse(raw, def_id))

    def validate(self, data: dict[str, Any], source: Path | None = None) -> Report:
        """Static checks of definition ``data``, layered over what it extends (D59): checked against
        what it will run. ``source`` is the file the definition is read from — as :meth:`source`
        names it for a saved definition, so its ``extends`` resolves where the engine's does. For
        unsaved content (a draft, bare tool input) the path it would land at decides, which
        :meth:`_would_land` takes from the data's id; a draft without an id lands at the shared
        ``instruments/draft.yaml``. The path's folder locates sibling parents and a broken
        ``extends`` diagnostic names it (D76)."""
        try:
            data = layered_definition_data(data, source if source is not None else self._would_land(data))
        except ValueError as exc:
            report = Report()
            code, pointer = getattr(exc, "code", "E-DEF-EXTENDS"), getattr(exc, "pointer", "/extends")
            report.add("error", code, pointer, str(exc), None)
            return report
        _, report = validate_definition_data(data)
        return report

    def _would_land(self, data: dict[str, Any]) -> Path:
        """The file unsaved definition ``data`` would be saved to, so its layering and its
        diagnostics speak of the place it will run at (#1634): the id's own place in the workspace
        when the data names one, else the shared draft path beside the other definitions."""
        def_id = data.get("id")
        if isinstance(def_id, str) and ID.fullmatch(def_id):
            return self.files.workspace / self.rel_path(def_id)
        return self.files.workspace / FOLDER / "draft.yaml"

    def create(self, def_id: str, data: dict[str, Any], source: Source = "agent") -> M.DefinitionDocument:
        return self.save(def_id, None, data, source)

    def copy_of(self, def_id: str, source: Source = "ui") -> M.DefinitionDocument:
        """ "Copy as my instrument": a workspace definition ``my-<id>`` (``-2``, ``-3`` … when taken)
        that ``extends`` the given one (D59). It starts as the same instrument and is changed by
        editing its YAML or with the agent's ``edit_definition``; the parent stays untouched."""
        self.read(def_id)  # it must exist (built in or in the workspace)
        base = f"my-{def_id}"
        new_id, n = base, 1
        while (self.files.workspace / self.rel_path(new_id)).exists():
            n += 1
            new_id = f"{base}-{n}"
        return self.create(new_id, {"extends": def_id}, source)

    def edit(
        self, def_id: str, base_sha256: str, ops: list[jsonpatch.PatchOp], source: Source = "agent"
    ) -> M.DefinitionDocument:
        doc = self.read(def_id)
        if not doc.editable:
            raise Forbidden(f"{def_id} is not in the workspace; create a copy under a new id to change it")
        self.files.require_unchanged(doc.path or def_id, base_sha256, doc.sha256, target="definition")
        return self.save(def_id, base_sha256, jsonpatch.apply(doc.data, ops), source)

    def save(
        self, def_id: str, base_sha256: str | None, data: dict[str, Any], source: Source = "agent"
    ) -> M.DefinitionDocument:
        """Write the definition as given (work in progress that does not pass the checks is kept)."""
        rel = self.rel_path(def_id)
        if data.get("id", def_id) != def_id:
            raise Invalid(f"the file {rel} holds the definition {def_id!r}, not {data.get('id')!r}")
        data = {"schema": "simubench.instrument/2", "id": def_id, **data}
        raw = dump_yaml(data).encode("utf-8")

        # The changes describe what this save replaces: the write transaction hands over the bytes
        # the file held while it has the lock, so the description is of exactly the version this
        # one follows — an unparseable previous records no link and reads as no changes (#1631).
        previous_data: list[dict[str, Any]] = []

        def previous_fingerprint(old: bytes) -> str:
            base = _parse(old)
            previous_data.append(base)
            return _fingerprint(base)

        self.files.write(
            rel,
            raw,
            base_sha256,
            fingerprint=_fingerprint(data),
            source=source,
            target="definition",
            previous_fingerprint=previous_fingerprint,
        )
        changes = self._changes(def_id, previous_data[0] if previous_data else {}, data)
        return self._document(def_id, rel, True, raw, data, changes=changes)

    def versions(self, def_id: str) -> list[M.DefinitionVersion]:
        """Saved versions of a workspace definition, newest first, each with what it changed since
        the one before it (T3). A definition outside the workspace has no history: an empty list."""
        rel = self.rel_path(def_id)
        saved = self.files.history.versions(rel)
        out = []
        for i, v in enumerate(saved):
            data = _parse(self.files.history.content(rel, v.seq))
            older = _parse(self.files.history.content(rel, saved[i + 1].seq)) if i + 1 < len(saved) else {}
            out.append(
                M.DefinitionVersion(
                    seq=v.seq,
                    sha256=v.sha256,
                    source=v.source,
                    saved_at=v.saved_at,
                    changes=self._changes(def_id, older, data),
                )
            )
        return out

    def restore(self, def_id: str, seq: int, base_sha256: str) -> M.DefinitionDocument:
        """Write version ``seq`` back as the newest version, recorded as a restore (the versions
        after it stay in the history), as :meth:`BenchFiles.restore` does for benches."""
        rel = self.rel_path(def_id)
        data = _parse(self.files.history.content(rel, seq))
        return self.save(def_id, base_sha256, data, "restore")

    def source(self, def_id: str) -> Path:
        """The file a definition is read from: the workspace's, else the library's."""
        path = self.files.workspace / self.rel_path(def_id)
        return path if path.is_file() else self.files.library(None).path(def_id)

    def structure(self, def_id: str) -> M.DefinitionStructure:
        """The effective structure the Web's build area edits (#1201)."""
        return definitionstructure.structure(self.read(def_id), self.source(def_id))

    def save_structure(
        self, def_id: str, new: M.StructureUpdate, source: Source = "ui"
    ) -> M.DefinitionStructure:
        """Write a whole structure into a workspace definition: one version (D35), the file keeping
        only what it does not inherit (D59). The draft is kept even when it does not pass the checks."""
        doc = self.read(def_id)
        if not doc.editable:
            raise Forbidden(f"{def_id} is not in the workspace; copy it as your instrument to change it")
        self.files.require_unchanged(doc.path or def_id, new.base_sha256, doc.sha256, target="definition")
        path = self.source(def_id)
        data = definitionstructure.written(doc.data, new, path)
        definitionstructure.check_kept(data, new, path)  # refuse rather than lose something silently
        self.save(def_id, new.base_sha256, data, source)
        return self.structure(def_id)

    def save_identity(
        self, def_id: str, new: M.IdentityUpdate, source: Source = "ui"
    ) -> M.DefinitionDocument:
        """Write a workspace definition's identity: only the fields that differ from what it runs
        now (a copy inherits the rest, D59), as one version through the usual checks."""
        doc = self.read(def_id)
        if not doc.editable:
            raise Forbidden(f"{def_id} is not in the workspace; copy it as your instrument to change it")
        self.files.require_unchanged(doc.path or def_id, new.base_sha256, doc.sha256)
        runs = definitionstructure.layered(doc.data, self.source(def_id))
        data = copy.deepcopy(doc.data)
        for key in ("manufacturer", "model"):
            value = getattr(new, key)
            if value is not None and value != runs.get(key):
                data[key] = value
        identity = dict(data.get("identity") or {})
        for key in ("serial", "firmware", "idn"):
            value = getattr(new, key)
            if value is not None and value != (runs.get("identity") or {}).get(key):
                identity[key] = value
        if identity:
            data["identity"] = identity
        return self.save(def_id, new.base_sha256, data, source)

    def layout_path(self, def_id: str) -> Path:
        return self.files.workspace / FOLDER / f"{def_id}{LAYOUT_SUFFIX}"

    def layout(self, def_id: str) -> M.DefinitionLayout:
        """The build area's positions (#1207); empty when none were saved or the file is broken
        (positions are a convenience: the canvas lays itself out without them)."""
        self.rel_path(def_id)
        path = self.layout_path(def_id)
        try:
            return M.DefinitionLayout.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            return M.DefinitionLayout()

    def save_layout(self, def_id: str, layout: M.DefinitionLayout) -> M.DefinitionLayout:
        """Write the positions beside a workspace definition (#1207); no version, no session restart.

        Through the write transaction (D90), exempted from both of its bookkeeping channels: a
        layout file holds no version — it is not the definition, and dragging a node is not a
        change to the instrument the history describes — and no ``file_changed`` event, because
        no query is re-keyed on a layout file (the watcher's scan skips them for the same reason,
        ``workspace_events.py``): the only writer is the build area, which puts the positions
        into its own cache as it saves. Overwrite without a base hash for the same reason: the
        newest positions are always the right ones, two canvases save independently."""
        if not self.read(def_id).editable:
            raise Forbidden(f"{def_id} is not in the workspace; its layout is not kept")
        self.files.write(
            f"{FOLDER}/{def_id}{LAYOUT_SUFFIX}",
            layout.model_dump_json(indent=1).encode("utf-8"),
            None,
            fingerprint="",  # nothing is recorded: the fingerprint names a version's content
            source="ui",
            record=False,
            announce=False,
            overwrite=True,
        )
        return layout

    def free_id(self, base: str) -> str:
        """``base``, or the first ``-2``, ``-3`` … variant that names no definition yet (workspace
        or built in)."""
        known = set(self.files.library(None).ids())
        new_id, n = base, 1
        while new_id in known or (self.files.workspace / self.rel_path(new_id)).exists():
            n += 1
            new_id = f"{base}-{n}"
        return new_id

    def expanded(self, def_id: str, bench_path: str | None = None) -> dict[str, Any]:
        """The definition with its whole ``extends`` chain and category default merged in, as one
        mapping without ``extends``: what the engine runs, standing on its own (#875). With
        ``bench_path``, looked up as that bench finds it (its own ``instruments/`` first)."""
        lib = self.files.library(self.files.resolve(bench_path) if bench_path else None)
        source = lib.path(def_id)
        raw, _ = upgrade(_parse(source.read_bytes()), "instrument")
        data = copy.deepcopy(layered_definition_data(raw, source))
        data.pop("extends", None)
        return data

    def changes_between(self, def_id: str, base: int, target: int) -> M.DefinitionChangeList:
        """The changes between two saved versions (``base=0`` compares against nothing: creation)."""
        rel = self.rel_path(def_id)
        older = _parse(self.files.history.content(rel, base)) if base > 0 else {}
        newer = _parse(self.files.history.content(rel, target))
        return M.DefinitionChangeList(base=base, target=target, changes=self._changes(def_id, older, newer))

    def _changes(self, def_id: str, older: dict[str, Any], newer: dict[str, Any]) -> list[M.DefinitionChange]:
        """What a version changed, in terms of the instrument it runs: both versions are compared
        after layering (``extends`` and the category default merged in, ``same_as`` resolved —
        the layering ends with it, D59, D82), so a copy's file growing from three lines to a whole
        command table reads as the one command it added, an inherited value overridden reads as
        old → new rather than as an added key, and a place that follows a spec with ``same_as``
        shows the change in the value it stands for, not in the alias. A copy's first version is
        compared with its parent: it changes nothing yet, apart from being based on it. Layering
        uses the parent as it is now; each layer's parse comes from the loader's mtime cache."""
        source = self.files.workspace / self.rel_path(def_id)

        def runs(data: dict[str, Any]) -> dict[str, Any]:
            # A file with no ``extends`` layers too: the layering ends with ``same_as`` resolved
            # (loader.py, what the engine runs), so the alias never compares as its literal (#1636).
            return {k: v for k, v in definitionstructure.layered(data, source).items() if k != "extends"}

        def base(data: dict[str, Any]) -> dict[str, Any]:
            return {"extends": data["extends"]} if "extends" in data else {}

        before = (
            {"extends": newer["extends"]} if not older and isinstance(newer.get("extends"), str) else older
        )
        return definition_changes(base(older), base(newer)) + definition_changes(runs(before), runs(newer))

    def _document(
        self,
        def_id: str,
        rel: str | None,
        editable: bool,
        raw: bytes,
        data: dict[str, Any],
        changes: list[M.DefinitionChange] | None = None,
    ) -> M.DefinitionDocument:
        report = self.validate(data, self.source(def_id))
        return M.DefinitionDocument(
            id=def_id,
            path=rel,
            editable=editable,
            sha256=hashlib.sha256(raw).hexdigest(),
            data=data,
            ok=report.ok,
            diagnostics=list(report.diagnostics),
            changes=changes or [],
        )


def _parse(raw: bytes, name: str = "") -> dict[str, Any]:
    try:
        data = load_yaml(raw.decode("utf-8"))
    except yaml.YAMLError as exc:
        # Broken file content is a domain error (#1406); the parser's line and column stay.
        raise Invalid(parse_error_text(exc, name)) from None
    if not isinstance(data, dict):
        raise Invalid("expected a mapping at the top level")
    return data


def _fingerprint(data: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
