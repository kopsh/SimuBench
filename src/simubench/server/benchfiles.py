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
from ..library.loader import DefinitionLibrary, load_yaml
from ..library.migrate import FormatError, upgrade
from ..models import api as M
from ..models.bench import BenchDefinition
from .history import History, Source

SUFFIXES = (".bench.yaml", ".bench.json")
#: Folders that never hold the user's benches: SimuBench's own state (history versions are folders
#: named after the bench file) and tool folders.
SKIP_DIRS = frozenset({".simubench", ".git", ".venv", "node_modules"})


def scan(workspace: Path) -> list[Path]:
    """The bench files in the workspace, sorted, without descending into :data:`SKIP_DIRS`."""
    found = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found.extend(Path(root) / f for f in files if f.endswith(SUFFIXES))
    return sorted(found, key=lambda p: (p.suffix != ".yaml", p.relative_to(workspace).as_posix()))


class Conflict(Exception):
    """The file changed since the writer read it (or exists when creating)."""

    def __init__(self, message: str, current_sha256: str | None) -> None:
        super().__init__(message)
        self.current_sha256 = current_sha256


class BenchFiles:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self._lock = threading.Lock()
        self.history = History(self.workspace)

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

    def save(
        self, bench_path: str, base_sha256: str | None, data: dict[str, Any], source: Source = "api"
    ) -> M.BenchDocument:
        path = self.resolve(bench_path)
        if not path.name.endswith(SUFFIXES):
            raise ValueError(f"bench files end with {' or '.join(SUFFIXES)}")
        # Schema-valid content is written in canonical form; invalid content is kept as given so
        # that work in progress is never lost.
        with contextlib.suppress(ValueError):
            data = edit.to_yaml_data(BenchDefinition.model_validate(data))
        raw = _dump(path, data)
        with self._lock:
            old = path.read_bytes() if path.exists() else None
            current = _sha(old) if old is not None else None
            if base_sha256 is None and current is not None:
                raise Conflict(f"{bench_path} already exists", current)
            if base_sha256 is not None and base_sha256 != current:
                raise Conflict(f"{bench_path} changed since it was read", current)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, path)
            previous = None
            if old is not None:
                with contextlib.suppress(ValueError, UnicodeDecodeError, yaml.YAMLError):
                    previous = (old, current, fingerprint(_parse(path, old)))
            self.history.record(bench_path, raw, _sha(raw), fingerprint(data), source, previous=previous)
        return self._document(bench_path, path, raw, data)

    def edit(
        self, bench_path: str, base_sha256: str, ops: list[Any], source: Source = "api"
    ) -> M.BenchDocument:
        doc = self.read(bench_path)
        if doc.sha256 != base_sha256:
            raise Conflict(f"{bench_path} changed since it was read", doc.sha256)
        try:
            bench = BenchDefinition.model_validate(doc.data)
        except ValueError:
            raise edit.EditError("文件不符合测试台格式，先修正诊断中的错误再编辑") from None
        return self.save(bench_path, base_sha256, edit.to_yaml_data(edit.apply(bench, ops)), source)

    # ---- history (D35) ------------------------------------------------------------------------------

    def versions(self, bench_path: str) -> list[M.BenchVersion]:
        self.resolve(bench_path)
        return self.history.versions(bench_path)

    def version(self, bench_path: str, seq: int) -> M.BenchVersionDocument:
        path = self.resolve(bench_path)
        entry = next((v for v in self.history.versions(bench_path) if v.seq == seq), None)
        if entry is None:
            raise FileNotFoundError(f"{bench_path} has no version {seq}")
        data = _parse(path, self.history.content(bench_path, seq))
        return M.BenchVersionDocument(path=bench_path, version=entry, data=data)

    def restore(self, bench_path: str, seq: int, base_sha256: str) -> M.BenchDocument:
        """Write version ``seq`` back as the newest version (the versions after it stay in the history)."""
        old = self.version(bench_path, seq)
        return self.save(bench_path, base_sha256, old.data, "restore")

    def _document(self, bench_path: str, path: Path, raw: bytes, data: dict[str, Any]) -> M.BenchDocument:
        # Clients see the current format (an older file is migrated here, and rewritten on its next save).
        with contextlib.suppress(FormatError):
            data = upgrade(data, "bench")[0]
        _, report = validate_data(data, self.library(path))
        return M.BenchDocument(
            path=bench_path,
            sha256=_sha(raw),
            fingerprint=fingerprint(data),
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


def fingerprint(data: dict[str, Any]) -> str:
    """Hash of the content that matters to a running session; moving a node does not change it."""
    # Normalized, so that writing defaults out or leaving them implicit is the same bench.
    with contextlib.suppress(ValueError):
        data = BenchDefinition.model_validate(data).model_dump(mode="json", by_alias=True)
    relevant = _numbers({k: v for k, v in data.items() if k not in ("layout", "description")})
    return _sha(json.dumps(relevant, sort_keys=True, ensure_ascii=False, default=str).encode())


def _numbers(value: Any) -> Any:
    """1.0 and 1 are the same value (JSON clients do not keep the difference)."""
    if isinstance(value, dict):
        return {k: _numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_numbers(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse(path: Path, raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8")
    data = json.loads(text) if path.suffix == ".json" else load_yaml(text)
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
