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
import logging
import os
import threading
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml.error import YAMLError as RuamelError

from ..engine import edit
from ..engine.component import component_types
from ..engine.settings import channel_ports
from ..engine.validate import Report, drives_net, validate_data
from ..library import categories
from ..library.loader import SKIP_DIRS, DefinitionLibrary, dump_yaml, load_yaml, parse_error_text
from ..library.migrate import FormatError, upgrade
from ..models import api as M
from ..models.bench import BenchDefinition
from . import yamlpatch
from .errors import Conflict, Forbidden, Invalid, NotFound
from .history import History, Source

SUFFIXES = (".bench.yaml", ".bench.json")

log = logging.getLogger(__name__)


def scan(workspace: Path) -> list[Path]:
    """The bench files in the workspace, sorted, without descending into :data:`SKIP_DIRS`."""
    found = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found.extend(Path(root) / f for f in files if f.endswith(SUFFIXES))
    return sorted(found, key=lambda p: (p.suffix != ".yaml", p.relative_to(workspace).as_posix()))


#: The Hello World bench shipped with the package: the first bench a new workspace opens (one
#: function generator into a scope and a spectrum analyzer, real models only, D42).
HELLO = Path(__file__).resolve().parents[1] / "library" / "examples" / "hello.bench.yaml"


class BenchFiles:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self._lock = threading.Lock()
        self.history = History(self.workspace)

    # ---- paths and libraries ------------------------------------------------------------------------

    def resolve(self, bench_path: str) -> Path:
        p = (self.workspace / bench_path).resolve()
        if self.workspace not in p.parents:
            raise Forbidden("bench path outside the workspace")
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
            raise Invalid(f"bench files end with {' or '.join(SUFFIXES)}")
        # Schema-valid content is written in canonical form; invalid content is kept as given so
        # that work in progress is never lost.
        canonical = False
        with contextlib.suppress(ValueError):
            data = edit.to_yaml_data(BenchDefinition.model_validate(data))
            canonical = True
        raw = self._patched(path, data) if canonical else None
        if raw is not None:
            return self._write(bench_path, path, raw, _parse(path, raw), base_sha256, source)
        return self._write(bench_path, path, _dump(path, data), data, base_sha256, source)

    def _patched(self, path: Path, data: dict[str, Any]) -> bytes | None:
        """The existing file's text with only the change to ``data`` applied (#1254): its comments,
        number spelling, key order and explicit defaults stay. None when there is no such text to
        keep (a new file, JSON, a file that is not a valid bench) or the patch would not read back
        as ``data`` — the canonical form is written then."""
        if path.suffix == ".json" or not path.exists():
            return None
        try:
            old_raw = path.read_bytes()
            old = edit.to_yaml_data(BenchDefinition.model_validate(_parse(path, old_raw)))
            raw = yamlpatch.patched(old_raw, old, data)
            if raw is None or edit.to_yaml_data(BenchDefinition.model_validate(_parse(path, raw))) != data:
                return None
            return raw
        except (ValueError, UnicodeDecodeError, yaml.YAMLError, RuamelError):
            return None

    def _write(
        self,
        bench_path: str,
        path: Path,
        raw: bytes,
        data: dict[str, Any],
        base_sha256: str | None,
        source: Source,
    ) -> M.BenchDocument:
        """Write ``raw`` (whose content is ``data``) to the file, refusing to overwrite a file
        the caller did not read (``base_sha256``), and record the version."""
        with self._lock:
            old = path.read_bytes() if path.exists() else None
            current = _sha(old) if old is not None else None
            if base_sha256 is None and current is not None:
                raise Conflict(f"{bench_path} already exists", current, reason="exists", target="bench")
            if base_sha256 is not None and base_sha256 != current:
                raise Conflict(f"{bench_path} changed since it was read", current, target="bench")
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

    def create_hello(self, source: Source = "ui") -> M.BenchDocument:
        """Copy the packaged Hello World bench into the workspace as ``hello.bench.yaml`` — its text
        as shipped, comments included — and return it; an existing file is returned unchanged,
        never overwritten (it is the user's now)."""
        bench_path = HELLO.name
        path = self.resolve(bench_path)
        with self._lock:
            if not path.exists():
                raw = HELLO.read_bytes()
                tmp = path.with_name(f".{path.name}.tmp")
                tmp.write_bytes(raw)
                os.replace(tmp, path)
                self.history.record(bench_path, raw, _sha(raw), fingerprint(_parse(path, raw)), source)
        return self.read(bench_path)

    def copy(
        self, bench_path: str, new_path: str | None = None, name: str | None = None, source: Source = "ui"
    ) -> M.BenchDocument:
        """Copy a whole bench — instruments, parts, wires, settings, layout — into a new file and
        return it (#874). ``new_path`` defaults to ``<stem>-copy.bench.yaml`` beside the original
        (``-copy-2`` … when taken), so the copy finds the same local ``instruments/`` folder;
        ``name`` defaults to the original's name with “（副本）”. An existing file is never
        overwritten (409)."""
        doc = self.read(bench_path)
        if new_path is None:
            new_path = self.free_path(_with_stem(bench_path, "-copy"))
        data = dict(doc.data)
        data["name"] = name or f"{data.get('name') or Path(bench_path).name}（副本）"
        # The copy is the original's text — its comments, which explain why values were chosen,
        # included (#902) — with only the name line changed and the resource string a script
        # connects with (`<file>@simubench`, e.g. in the description) pointing at the copy.
        path = self.resolve(new_path)
        source_path = self.resolve(bench_path)
        if path.name.endswith(SUFFIXES) and ".json" not in (path.suffix, source_path.suffix):
            raw = _renamed_text(source_path.read_bytes(), data["name"], source_path.name, path.name)
            if raw is not None:
                copied = _parse(path, raw)
                if copied.get("name") == data["name"]:
                    return self._write(new_path, path, raw, copied, None, source)
        return self.save(new_path, None, data, source)

    def free_path(self, bench_path: str) -> str:
        """``bench_path``, or the first ``-2``, ``-3`` … variant of its stem that is not taken."""
        candidate, n = bench_path, 1
        while self.resolve(candidate).exists():
            n += 1
            candidate = _with_stem(bench_path, f"-{n}")
        return candidate

    def remove_created(self, rel_path: str, expected_sha256: str) -> None:
        """Delete a file a conversation turn created — the user's "restore to before the last
        turn" (#876), never an agent tool. Only a bench or a workspace definition, and only while
        it still holds the content the caller checked (``expected_sha256``), so a file changed in
        the meantime is not lost unseen. Its saved versions stay in the history."""
        path = self.resolve(rel_path)
        is_definition = path.parent == self.workspace / "instruments" and path.suffix == ".yaml"
        if not (path.name.endswith(SUFFIXES) or is_definition):
            raise Forbidden("only bench files and workspace definitions can be removed")
        with self._lock:
            if not path.exists():
                return
            current = _sha(path.read_bytes())
            if current != expected_sha256:
                raise Conflict(
                    f"{rel_path} changed since it was checked",
                    current,
                    target="definition" if is_definition else "bench",
                )
            path.unlink()

    def edit(
        self, bench_path: str, base_sha256: str, ops: list[Any], source: Source = "api"
    ) -> M.BenchDocument:
        doc = self.read(bench_path)
        if doc.sha256 != base_sha256:
            raise Conflict(f"{bench_path} changed since it was read", doc.sha256, target="bench")
        try:
            bench = BenchDefinition.model_validate(doc.data)
        except ValueError:
            raise edit.EditError("文件不符合测试台格式，先修正诊断中的错误再编辑") from None
        library = self.library(self.resolve(bench_path))
        data = edit.to_yaml_data(edit.apply(bench, ops, library))
        _refuse_new_drivers(doc, data, ops, library)
        return self.save(bench_path, base_sha256, data, source)

    # ---- history (D35) ------------------------------------------------------------------------------

    def versions(self, bench_path: str) -> list[M.BenchVersion]:
        self.resolve(bench_path)
        return self.history.versions(bench_path)

    def version(self, bench_path: str, seq: int) -> M.BenchVersionDocument:
        path = self.resolve(bench_path)
        entry = next((v for v in self.history.versions(bench_path) if v.seq == seq), None)
        if entry is None:
            raise NotFound(f"{bench_path} has no version {seq}")
        data = _parse(path, self.history.content(bench_path, seq))
        return M.BenchVersionDocument(path=bench_path, version=entry, data=data)

    def restore(self, bench_path: str, seq: int, base_sha256: str) -> M.BenchDocument:
        """Write version ``seq`` back as the newest version (the versions after it stay in the history)."""
        old = self.version(bench_path, seq)
        return self.save(bench_path, base_sha256, old.data, "restore")

    def _document(self, bench_path: str, path: Path, raw: bytes, data: dict[str, Any]) -> M.BenchDocument:
        # Clients see the current format (an older file is migrated here, and rewritten on its next save).
        # Bench files, not instrument definitions — issue 641's model-level self-migration does not apply here.
        with contextlib.suppress(FormatError):
            data = upgrade(data, "bench")[0]
        _, report = validate_data(data, self.library(path))
        return M.BenchDocument(
            path=bench_path,
            sha256=_sha(raw),
            fingerprint=fingerprint(data),
            data=data,
            ok=report.ok,
            diagnostics=list(report.diagnostics),
        )

    # ---- catalog ------------------------------------------------------------------------------------

    def catalog(self) -> M.Catalog:
        lib = self.library(None)
        instruments = []
        for def_id in lib.ids():
            try:
                d = lib.get(def_id)
            except Exception as exc:  # a broken definition shows up in validation, not here
                log.warning(
                    "catalog: definition %r skipped: %s",
                    def_id,
                    str(exc).splitlines()[0] if str(exc) else type(exc).__name__,
                )
                continue
            ports = list(channel_ports(d, d.channels.count)) + list(d.ports.instrument)
            instruments.append(
                M.DefinitionSummary(
                    id=d.id,
                    manufacturer=d.manufacturer,
                    model=d.model,
                    instrument_class=categories.as_api_class(d.instrument_class),
                    description=d.description,
                    channels=d.channels.count,
                    ports=ports,
                    template=d.display.template,
                    workspace=self.workspace in lib.path(def_id).resolve().parents,
                    extends=d.extends,
                )
            )
        components = [
            M.ComponentTypeInfo(
                type=name,
                category=cls.category,  # type: ignore[arg-type]
                label=cls.label or None,
                label_en=cls.label_en or None,
                about=cls.about or None,
                about_en=cls.about_en or None,
                doc=inspect.getdoc(cls),
                ports=[
                    M.PortInfo(
                        name=p, direction=spec.direction, medium=spec.medium, drives=drives_net(cls, spec)
                    )
                    for p, spec in cls.ports.items()
                ],
                defaults={k: _json_number(v) for k, v in cls.defaults.items()},
                units=dict(cls.units),
            )
            for name, cls in sorted(component_types().items())
            if cls.category != "internal"
        ]
        return M.Catalog(instruments=instruments, components=components)


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


def _with_stem(bench_path: str, extra: str) -> str:
    """``a/b.bench.yaml`` with ``extra`` after the stem: ``a/b-copy.bench.yaml``."""
    for suffix in SUFFIXES:
        if bench_path.endswith(suffix):
            return bench_path[: -len(suffix)] + extra + suffix
    return bench_path + extra


def _renamed_text(raw: bytes, name: str, old_file: str, new_file: str) -> bytes | None:
    """A bench file's YAML text with its top-level ``name:`` line set to ``name`` and every
    ``<old_file>@simubench`` (the resource string a script opens) turned into the new file's;
    the rest — comments, order, spelling — as written. None when the file has no single
    one-line top-level name to replace (the caller then writes the canonical form)."""
    lines = raw.decode("utf-8").splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.startswith("name:")]
    if len(hits) != 1:
        return None
    i = hits[0]
    following = lines[i + 1] if i + 1 < len(lines) else ""
    if following[:1] in (" ", "\t") and following.strip():  # a block scalar or a nested value
        return None
    ending = "\n" if lines[i].endswith("\n") else ""
    lines[i] = f"name: {json.dumps(name, ensure_ascii=False)}{ending}"
    text = "".join(lines).replace(f"{old_file}@simubench", f"{new_file}@simubench")
    return text.encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse(path: Path, raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8")
    try:
        data = json.loads(text) if path.suffix == ".json" else load_yaml(text)
    except json.JSONDecodeError as exc:
        # A file whose content does not parse is the caller's to fix, and the error keeps the
        # line and column it reported (#1406): a domain error, not the 500 a bug gets.
        raise Invalid(f"{path.name}: {exc}") from None
    except yaml.YAMLError as exc:
        raise Invalid(parse_error_text(exc, path.name)) from None
    if not isinstance(data, dict):
        raise Invalid(f"{path.name}: expected a mapping at the top level")
    return data


def _dump(path: Path, data: dict[str, Any]) -> bytes:
    if path.suffix == ".json":
        return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return dump_yaml(data).encode("utf-8")


def _json_number(value: Any) -> Any:
    # JSON has no infinity; the bench files spell it "INF".
    if isinstance(value, float) and value in (float("inf"), float("-inf")):
        return "INF" if value > 0 else "-INF"
    return value


def _refuse_new_drivers(doc: M.BenchDocument, data: dict[str, Any], ops: list[Any], library: Any) -> None:
    """A connect that puts a second driver on a net (two outputs wired together) is refused, and
    the file is not written (#1269): the checks would otherwise flag the net, and the canvas leave
    out a wire of it. Errors the file already had do not count."""
    if not any(isinstance(op, edit.Connect) for op in ops):
        return
    before = {(d.code, d.message) for d in doc.diagnostics if d.severity == "error"}
    _, report = validate_data(data, library)
    new = [
        d
        for d in report.diagnostics
        if d.severity == "error" and d.code == "E-NET-MULTIDRIVER" and (d.code, d.message) not in before
    ]
    if new:
        first = new[0]
        raise edit.EditError(f"{first.message}；{first.suggestion}" if first.suggestion else first.message)
