"""Instrument definition files in the workspace (instrument-authoring.md 5.4): read, create, edit
with JSON Patch under optimistic concurrency (the sha256 read, as for benches, D25), and keep every
save in the version history (D35). Definitions outside the workspace are read only."""

from __future__ import annotations

import contextlib
import json
import os
import re
from typing import Any

import yaml

from ..engine import jsonpatch
from ..engine.validate import Report
from ..engine.validate_definition import validate_definition_data
from ..library.loader import load_yaml
from ..models import api as M
from .benchfiles import BenchFiles, Conflict, _sha, diagnostics
from .definitionchanges import definition_changes
from .history import Source

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
FOLDER = "instruments"


class DefinitionFiles:
    def __init__(self, files: BenchFiles) -> None:
        self.files = files

    def rel_path(self, def_id: str) -> str:
        if not ID.fullmatch(def_id):
            raise ValueError(f"{def_id!r}: an id is letters, digits, '.', '_' and '-'")
        return f"{FOLDER}/{def_id}.yaml"

    def read(self, def_id: str) -> M.DefinitionDocument:
        rel = self.rel_path(def_id)
        path = self.files.workspace / rel
        if path.is_file():
            raw, editable = path.read_bytes(), True
        else:
            raw, editable, rel = self.files.library(None).path(def_id).read_bytes(), False, None  # type: ignore[assignment]
        return self._document(def_id, rel, editable, raw, _parse(raw))

    def validate(self, data: dict[str, Any]) -> Report:
        _, report = validate_definition_data(data)
        return report

    def create(self, def_id: str, data: dict[str, Any], source: Source = "agent") -> M.DefinitionDocument:
        return self.save(def_id, None, data, source)

    def edit(
        self, def_id: str, base_sha256: str, ops: list[jsonpatch.PatchOp], source: Source = "agent"
    ) -> M.DefinitionDocument:
        doc = self.read(def_id)
        if not doc.editable:
            raise PermissionError(
                f"{def_id} is not in the workspace; create a copy under a new id to change it"
            )
        if doc.sha256 != base_sha256:
            raise Conflict(f"{doc.path} changed since it was read", doc.sha256)
        return self.save(def_id, base_sha256, jsonpatch.apply(doc.data, ops), source)

    def save(
        self, def_id: str, base_sha256: str | None, data: dict[str, Any], source: Source = "agent"
    ) -> M.DefinitionDocument:
        """Write the definition as given (work in progress that does not pass the checks is kept)."""
        rel = self.rel_path(def_id)
        if data.get("id", def_id) != def_id:
            raise ValueError(f"the file {rel} holds the definition {def_id!r}, not {data.get('id')!r}")
        data = {"schema": "simubench.instrument/1", "id": def_id, **data}
        raw = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100).encode("utf-8")
        path = self.files.workspace / rel
        with self.files._lock:
            old = path.read_bytes() if path.exists() else None
            current = _sha(old) if old is not None else None
            if base_sha256 is None and current is not None:
                raise Conflict(f"{rel} already exists", current)
            if base_sha256 is not None and base_sha256 != current:
                raise Conflict(f"{rel} changed since it was read", current)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, path)
            base_data: dict[str, Any] = {}
            previous = None
            if old is not None:
                with contextlib.suppress(ValueError, UnicodeDecodeError, yaml.YAMLError):
                    base_data = _parse(old)
                    previous = (old, current, _fingerprint(base_data))
            self.files.history.record(rel, raw, _sha(raw), _fingerprint(data), source, previous=previous)
            changes = definition_changes(base_data, data)
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
                    seq=v.seq, sha256=v.sha256, source=v.source, saved_at=v.saved_at,
                    changes=definition_changes(older, data),
                )
            )
        return out

    def changes_between(self, def_id: str, base: int, target: int) -> M.DefinitionChangeList:
        """The changes between two saved versions (``base=0`` compares against nothing: creation)."""
        rel = self.rel_path(def_id)
        older = _parse(self.files.history.content(rel, base)) if base > 0 else {}
        newer = _parse(self.files.history.content(rel, target))
        return M.DefinitionChangeList(base=base, target=target, changes=definition_changes(older, newer))

    def _document(
        self,
        def_id: str,
        rel: str | None,
        editable: bool,
        raw: bytes,
        data: dict[str, Any],
        changes: list[M.DefinitionChange] | None = None,
    ) -> M.DefinitionDocument:
        report = self.validate(data)
        return M.DefinitionDocument(
            id=def_id,
            path=rel,
            editable=editable,
            sha256=_sha(raw),
            data=data,
            ok=report.ok,
            diagnostics=diagnostics(report),
            changes=changes or [],
        )


def _parse(raw: bytes) -> dict[str, Any]:
    data = load_yaml(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected a mapping at the top level")
    return data


def _fingerprint(data: dict[str, Any]) -> str:
    return _sha(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode())
