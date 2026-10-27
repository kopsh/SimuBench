"""Reading, validating, editing and saving bench files in the workspace (architecture.md 4.3).

Saves are optimistic: a writer passes the hash of the content it started from, and a save against
content that changed in the meantime (another tab, an agent, a text editor) is refused with
:class:`Conflict` instead of silently overwriting it.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import os
import threading
from pathlib import Path
from typing import Any

import yaml

from ..engine import edit
from ..engine.component import component_types
from ..engine.validate import Report, validate_data
from ..library.loader import DefinitionLibrary
from ..models import api as M
from ..models.bench import BenchDefinition

SUFFIXES = (".bench.yaml", ".bench.json")


class Conflict(Exception):
    """The file changed since the writer read it (or exists when creating)."""

    def __init__(self, message: str, current_sha256: str | None) -> None:
        super().__init__(message)
        self.current_sha256 = current_sha256


class BenchFiles:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self._lock = threading.Lock()

    # ---- paths and libraries ------------------------------------------------------------------------

    def resolve(self, bench_path: str) -> Path:
        p = (self.workspace / bench_path).resolve()
        if self.workspace not in p.parents:
            raise PermissionError("bench path outside the workspace")
        return p

    def library(self, path: Path | None) -> DefinitionLibrary:
        dirs = [path.parent / "instruments"] if path else []
        dirs.append(self.workspace / "instruments")
        return DefinitionLibrary(dict.fromkeys(dirs))

    # ---- documents ----------------------------------------------------------------------------------

    def read(self, bench_path: str) -> M.BenchDocument:
        path = self.resolve(bench_path)
        raw = path.read_bytes()
        return self._document(bench_path, path, raw, _parse(path, raw))

    def validate(self, data: dict[str, Any], bench_path: str | None = None) -> Report:
        path = self.resolve(bench_path) if bench_path else None
        _, report = validate_data(data, self.library(path))
        return report

    def save(self, bench_path: str, base_sha256: str | None, data: dict[str, Any]) -> M.BenchDocument:
        path = self.resolve(bench_path)
        if not path.name.endswith(SUFFIXES):
            raise ValueError(f"bench files end with {' or '.join(SUFFIXES)}")
        # Schema-valid content is written in canonical form; invalid content is kept as given so
        # that work in progress is never lost.
        with contextlib.suppress(ValueError):
            data = edit.to_yaml_data(BenchDefinition.model_validate(data))
        raw = _dump(path, data)
        with self._lock:
            current = _sha(path.read_bytes()) if path.exists() else None
            if base_sha256 is None and current is not None:
                raise Conflict(f"{bench_path} already exists", current)
            if base_sha256 is not None and base_sha256 != current:
                raise Conflict(f"{bench_path} changed since it was read", current)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, path)
        return self._document(bench_path, path, raw, data)

    def edit(self, bench_path: str, base_sha256: str, ops: list[Any]) -> M.BenchDocument:
        doc = self.read(bench_path)
        if doc.sha256 != base_sha256:
            raise Conflict(f"{bench_path} changed since it was read", doc.sha256)
        try:
            bench = BenchDefinition.model_validate(doc.data)
        except ValueError:
            raise edit.EditError("文件不符合测试台格式，先修正诊断中的错误再编辑") from None
        return self.save(bench_path, base_sha256, edit.to_yaml_data(edit.apply(bench, ops)))

    def _document(self, bench_path: str, path: Path, raw: bytes, data: dict[str, Any]) -> M.BenchDocument:
        _, report = validate_data(data, self.library(path))
        return M.BenchDocument(
            path=bench_path,
            sha256=_sha(raw),
            data=data,
            ok=report.ok,
            diagnostics=diagnostics(report),
        )

    # ---- catalog ------------------------------------------------------------------------------------

    def catalog(self) -> M.Catalog:
        lib = self.library(None)
        instruments = []
        for def_id in lib.ids():
            try:
                d = lib.get(def_id)
            except Exception:  # a broken definition shows up in validation, not here
                continue
            ports = [
                p.replace("{n}", str(n)) for n in range(1, d.channels.count + 1) for p in d.ports.channel
            ] + list(d.ports.instrument)
            instruments.append(
                M.DefinitionSummary(
                    id=d.id,
                    manufacturer=d.manufacturer,
                    model=d.model,
                    instrument_class=d.instrument_class,
                    description=d.description,
                    channels=d.channels.count,
                    ports=ports,
                    template=d.display.template,
                )
            )
        components = [
            M.ComponentTypeInfo(
                type=name,
                category=cls.category,  # type: ignore[arg-type]
                label=cls.label or None,
                label_en=cls.label_en or None,
                doc=inspect.getdoc(cls),
                ports=[
                    M.PortInfo(name=p, direction=spec.direction, medium=spec.medium)
                    for p, spec in cls.ports.items()
                ],
                defaults={k: _json_number(v) for k, v in cls.defaults.items()},
                units=dict(cls.units),
            )
            for name, cls in sorted(component_types().items())
            if cls.category != "internal"
        ]
        return M.Catalog(instruments=instruments, components=components)


def diagnostics(report: Report) -> list[M.Diagnostic]:
    return [M.Diagnostic(**d.as_dict()) for d in report.diagnostics]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse(path: Path, raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a mapping at the top level")
    return data


def _dump(path: Path, data: dict[str, Any]) -> bytes:
    if path.suffix == ".json":
        return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100).encode("utf-8")


def _json_number(value: Any) -> Any:
    # JSON has no infinity; the bench files spell it "INF".
    if isinstance(value, float) and value in (float("inf"), float("-inf")):
        return "INF" if value > 0 else "-INF"
    return value
