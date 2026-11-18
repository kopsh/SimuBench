"""Loading instrument definitions and bench files."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

import yaml

from ..errors import Invalid, Unknown
from ..models.bench import BenchDefinition
from ..models.definition import InstrumentDefinition
from .class_defaults import deep_merge, default_block, drop_deleted
from .migrate import upgrade
from .same_as import resolve_same_as

#: Built-in definitions: real models only (D42). SimuBench's own ``sb-*`` validation definitions are
#: not shipped; they live in ``validation/instruments`` of the repository and are loaded through
#: :data:`DEFINITIONS_ENV`.
BUILTIN_DIR = Path(__file__).parent / "instruments"
#: Extra definition folders, separated like ``PATH`` (``os.pathsep``), searched before the built-in ones.
DEFINITIONS_ENV = "SIMUBENCH_DEFINITIONS"
#: Coverage tables live next to their definitions (D42) and are not definitions themselves.
COVERAGE_SUFFIX = ".coverage.yaml"
#: Folders that never hold the user's files: SimuBench's own state (history versions are folders
#: named after the file) and tool folders.
SKIP_DIRS = frozenset({".simubench", ".git", ".venv", "node_modules"})

log = logging.getLogger(__name__)


def parse_error_text(exc: yaml.YAMLError, name: str = "") -> str:
    """One line naming the file and the parser's position (#1406): the seams keep the first line."""
    mark = getattr(exc, "problem_mark", None)
    text = getattr(exc, "problem", None) or str(exc)
    if mark is not None:
        text = f"{text} (line {mark.line + 1}, column {mark.column + 1})"
    return f"{name}: {text}" if name else text


class YamlLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):  # libyaml's parser when present
    """SafeLoader that reads floats the YAML 1.2 way: ``1.0e7`` and ``1e-7`` are numbers.

    YAML 1.1 (PyYAML's default) needs a dot and a signed exponent, so engineers' ``1.0e7`` would
    silently become a string.
    """


YamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
        |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
        |\.[0-9_]+(?:[eE][-+]?[0-9]+)?
        |[-+]?\.(?:inf|Inf|INF)
        |\.(?:nan|NaN|NAN))$""",
        re.X,
    ),
    list("-+0123456789."),
)


def load_yaml(text: str) -> object:
    return yaml.load(text, Loader=YamlLoader)  # a SafeLoader subclass: no arbitrary objects


#: libyaml's emitter when present: the same text as ``yaml.safe_dump``, about five times faster on a
#: large definition (#1394).
_Dumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


def dump_yaml(data: object) -> str:
    """The text SimuBench writes a bench or definition file with: keys in order, unicode as is."""
    return yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=100)


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        try:
            data = load_yaml(f.read())
        except yaml.YAMLError as exc:
            # Broken file content is a domain error (#1406); the parser's line and column stay.
            raise Invalid(parse_error_text(exc, path.name)) from None
    if not isinstance(data, dict):
        raise Invalid(f"{path}: expected a mapping at the top level")
    return data


def env_definition_dirs() -> list[Path]:
    """The folders named by :data:`DEFINITIONS_ENV`, in order."""
    return [Path(d) for d in os.environ.get(DEFINITIONS_ENV, "").split(os.pathsep) if d]


@lru_cache(maxsize=256)
def _raw_definition(path: Path, mtime: float) -> dict:
    """One file's parsed and upgraded definition data, before any layering; keyed on the file's
    own mtime, so an edit re-reads just that file."""
    # An explicit upgrade (issue 641): the loader layers the upgraded dicts (extends) and feeds
    # them to expand_macros — the model-level validator cannot hand a dict back.
    data, _ = upgrade(_read_yaml(path), "instrument")
    return data


def _extends_target(parent_id: str, source: Path) -> Path:
    """The file an ``extends`` names: the defining file's own folder first, then the folders of
    :data:`DEFINITIONS_ENV`, then the built-in ones."""
    for d in [source.parent, *env_definition_dirs(), BUILTIN_DIR]:
        p = d / f"{parent_id}.yaml"
        if p.is_file():
            return p
    known = ", ".join(str(d) for d in [source.parent, *env_definition_dirs(), BUILTIN_DIR])
    raise Invalid(f"{source}: extends names {parent_id!r}, which is no definition in {known}")


def _extends_chain(path: Path, data: dict) -> list[tuple[Path, float]]:
    """The definition's ancestor files, nearest parent first, each with its mtime now. Single
    parent per file (``extends`` is one id); a chain may run several steps, a cycle is an error."""
    chain: list[tuple[Path, float]] = []
    seen = {path}
    current, current_data = path, data
    while True:
        parent_id = current_data.get("extends")
        if parent_id is None:
            return chain
        if not isinstance(parent_id, str) or not parent_id:
            raise Invalid(f"{current}: extends must be a definition id (a string)")
        parent = _extends_target(parent_id, current)
        if parent in seen:
            ring = " → ".join([str(p) for p, _ in chain[::-1]] + [str(parent)])
            raise Invalid(f"{path}: extends 成环:{ring}")
        parent_mtime = parent.stat().st_mtime
        chain.append((parent, parent_mtime))
        seen.add(parent)
        current, current_data = parent, _raw_definition(parent, parent_mtime)


class LayerError(ValueError):
    """A definition that cannot be layered: a missing or cyclic ``extends`` (E-DEF-EXTENDS), or a
    ``command_overrides`` removal of a header the table does not hold (E-DEF-COMMAND-OVERRIDE), or a
    component override that names no single inherited component or changes its type
    (E-DEF-COMPONENT-OVERRIDE).
    ``code`` and ``pointer`` say where, for the diagnostics."""

    def __init__(self, message: str, code: str = "E-DEF-EXTENDS", pointer: str = "/extends") -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer


#: The per-command layer of a definition (D59): ``{header: command | command list | null}``.
OVERRIDES = "command_overrides"
#: The per-component layers (D59, #1425): ``{component name: fields to change | null}``, one for
#: the instrument's components and one for each channel's, keyed by the list they change.
COMPONENT_OVERRIDES = {
    "components": "component_overrides",
    "channel_components": "channel_component_overrides",
}
#: Every override layer: set aside before the merge, applied after it.
OVERRIDE_KEYS = (OVERRIDES, *COMPONENT_OVERRIDES.values())


def _ptr_part(text: str) -> str:
    return text.replace("~", "~0").replace("/", "~1")


def apply_command_overrides(commands: list, overrides: dict) -> list:
    """``commands`` with ``overrides`` applied, each entry naming one command by its header exactly
    as written: the value — one command, a list of them, or ``null`` — is what that header's group
    becomes, *whole* (no field merge — D59's reason for replacing lists). A command replaces every
    entry with that header at the first one's place, a list of commands replaces them as a group
    (the ``when``/``option`` variants of one header are one unbreakable group), ``null`` removes
    them all; what no command has is appended. A removal of a header the list does not hold raises
    :class:`LayerError`."""
    out = list(commands)
    for header, command in overrides.items():
        at = [i for i, c in enumerate(out) if isinstance(c, dict) and c.get("header") == header]
        pointer = f"/{OVERRIDES}/{_ptr_part(str(header))}"
        if command is None:
            if not at:
                raise LayerError(
                    f"继承的命令表里没有命令头 {header}，无从删除", "E-DEF-COMMAND-OVERRIDE", pointer
                )
            for i in reversed(at):
                del out[i]
            continue
        if isinstance(command, list):
            if not all(isinstance(entry, dict) for entry in command):
                raise LayerError(
                    f"{header} 的列表值应全是命令（不含 header）", "E-DEF-COMMAND-OVERRIDE", pointer
                )
            entries = command
        elif isinstance(command, dict):
            entries = [command]
        else:
            raise LayerError(
                f"{header} 的值应是一条命令、一组命令（列表，不含 header）或 null",
                "E-DEF-COMMAND-OVERRIDE",
                pointer,
            )
        group = [{"header": header, **{k: v for k, v in e.items() if k != "header"}} for e in entries]
        if at:
            out = [c for i, c in enumerate(out) if i not in set(at)]
            out[at[0]:at[0]] = group  # the first entry's place; the rest were after it
        else:
            out.extend(group)
    return out


def apply_component_overrides(components: list, overrides: dict, key: str = "component_overrides") -> list:
    """``components`` with ``overrides`` applied, each entry naming one component by its name.
    An inherited component is changed field by field: ``params`` by parameter name — each value
    replaced whole (a mapping is one value, never merged into the inherited mapping), ``null``
    removes the parameter — and every other field replaced (``null`` removes it); its ``type`` cannot
    change (remove it and add a new name instead). A name no component has is appended and must give
    its ``type``; ``null`` removes the component. Unlike ``command_overrides`` (whole commands, D59)
    the merge is per field: a component's parameters are a flat name → value table, so "change one
    parameter" has one meaning, which a command's ordered parameter list does not have. A name the
    list holds more than once, a removal of a name it does not hold, or a changed ``type`` raises
    :class:`LayerError` (E-DEF-COMPONENT-OVERRIDE)."""
    out = list(components)
    for name, change in overrides.items():
        at = [i for i, c in enumerate(out) if isinstance(c, dict) and c.get("name") == name]
        pointer = f"/{key}/{_ptr_part(str(name))}"

        def fail(message: str, pointer: str = pointer) -> LayerError:
            return LayerError(message, "E-DEF-COMPONENT-OVERRIDE", pointer)

        if len(at) > 1:
            raise fail(f"组件 {name} 在继承的组件表里出现 {len(at)} 次，按名字改不出是哪一个；改写整张组件表")
        if change is None:
            if not at:
                raise fail(f"继承的组件表里没有组件 {name}，无从删除")
            del out[at[0]]
            continue
        if not isinstance(change, dict):
            raise fail(f"{name} 的值应是要改的字段（type、label、description、params）或 null")
        params = change.get("params")
        if params is not None and not isinstance(params, dict):
            raise fail(f"{name} 的 params 应是 参数名 → 值 的映射", f"{pointer}/params")
        if not at:
            if not change.get("type"):
                raise fail(f"继承的组件表里没有组件 {name}：新增组件要写明 type")
            whole = {"name": name, **{k: v for k, v in change.items() if k != "name" and v is not None}}
            whole["params"] = {k: v for k, v in (params or {}).items() if v is not None}
            out.append(whole)
            continue
        old = out[at[0]]
        if change.get("type") not in (None, old.get("type")):
            raise fail(
                f"组件 {name} 的类型不能改（{old.get('type')} → {change.get('type')}）：删掉它再用新名字加",
                f"{pointer}/type",
            )
        merged = {k: v for k, v in old.items() if k != "params"}
        for field, value in change.items():
            if field in ("name", "params", "type"):
                continue
            if value is None:
                merged.pop(field, None)
            else:
                merged[field] = value
        new_params = dict(old.get("params") or {})
        for param, value in (params or {}).items():
            if value is None:
                new_params.pop(param, None)
            else:
                new_params[param] = value
        merged["params"] = new_params
        out[at[0]] = merged
    return out


def _layered(data: dict, ancestors: list[tuple[Path, float]]) -> dict:
    """The definition's data with every layer merged under it (instrument-authoring.md 2.9):
    类别默认 → 父 → 子 — the class's default block lowest, then the ancestors from the far end (the nearest parent over the grandparent),
    the file's own data last. ``errors`` merge by code, everything else by key; lists replace.

    ``command_overrides`` (D59) is set aside before the merge and applied afterwards to the merged
    ``commands``, layer by layer from the far end — only the layers above the nearest one that
    writes its own ``commands`` list, since that list replaces everything below it.
    ``component_overrides`` and ``channel_component_overrides`` (#1425) do the same for
    ``components`` and ``channel_components``.

    The class default serves real models (D42, a `reference`) and the definitions built on one
    (a layer of the chain has a reference, #1428): a table entry replaces the engine's "standard
    text; detail" reply, so a definition with no reference anywhere in its chain (the ``sb-*``
    validation instruments, a toy) keeps the engine's standard texts as before."""
    layers = [data] + [_raw_definition(parent, mtime) for parent, mtime in ancestors]  # child first
    overrides = [layer.get(OVERRIDES) for layer in layers]
    merged = {k: v for k, v in data.items() if k not in OVERRIDE_KEYS}
    layered = False
    for raw in layers[1:]:  # nearest parent first: each lower layer goes under
        merged = deep_merge({k: v for k, v in raw.items() if k not in OVERRIDE_KEYS}, merged)
        layered = True
    # A real model, or one built on a real model (#1428): a child that drops its parent's
    # ``reference`` (a hypothetical instrument is no real model) still runs the parent's error texts,
    # part of which the parent's file no longer lists because they moved to the class's table.
    if any(layer.get("reference") is not None for layer in layers):
        base = default_block(merged.get("class"))
        if base:
            merged = deep_merge(base, merged)
            layered = True
    # ``key: null`` in a child drops what the layers below bring (a dict key, an errors code);
    # dropped only now, so no layer can bring the key back.
    merged = drop_deleted(merged) if layered else merged
    if any(overrides):
        top = next((i for i, layer in enumerate(layers) if "commands" in layer), len(layers) - 1)
        commands = merged.get("commands") or []
        for own in reversed(overrides[: top + 1]):  # far → near → the file itself
            if own:
                if not isinstance(own, dict):
                    raise LayerError(
                        f"{OVERRIDES} 应是 命令头 → 命令或命令列表 的映射",
                        "E-DEF-COMMAND-OVERRIDE",
                        f"/{OVERRIDES}",
                    )
                commands = apply_command_overrides(commands, own)
        merged = {**merged, "commands": commands}
    for target, key in COMPONENT_OVERRIDES.items():
        own_layers = [layer.get(key) for layer in layers]
        if not any(own_layers):
            continue
        top = next((i for i, layer in enumerate(layers) if target in layer), len(layers) - 1)
        items = merged.get(target) or []
        for own in reversed(own_layers[: top + 1]):  # far → near → the file itself
            if own:
                if not isinstance(own, dict):
                    raise LayerError(
                        f"{key} 应是 组件名 → 要改的字段 的映射", "E-DEF-COMPONENT-OVERRIDE", f"/{key}"
                    )
                items = apply_component_overrides(items, own, key)
        merged = {**merged, target: items}
    # one value in one place (#1426, D82): resolved last, so a reference reaches what any layer wrote
    return resolve_same_as(merged)


def layered_definition_data(data: dict, source: Path) -> dict:
    """The definition data with its extends chain and category default merged in (D59): what the
    engine runs, and what ``validate`` checks a file or a draft against. ``source`` is the file's
    path — for an unsaved draft, the path it would take (its folder locates sibling parents).
    Raises ValueError when an ``extends`` target is missing or the chain cycles."""
    return _layered(data, _extends_chain(source, data))


@lru_cache(maxsize=256)
def _build_definition(
    path: Path,
    mtime: float,
    coverage_mtime: float | None,
    ancestors: tuple[tuple[Path, float], ...],
) -> InstrumentDefinition:
    """The layered definition validated; every input that can change it is in the cache key — a
    parent's or the file's own edit re-keys and rebuilds."""
    definition = InstrumentDefinition.model_validate(_layered(_raw_definition(path, mtime), list(ancestors)))
    definition._source_file = path  # events that classify a command name this file (D18)
    if coverage_mtime is not None:  # the table's own mtime keys the cache: an edit invalidates it
        try:
            from .coverage import load_coverage

            table = path.with_name(path.stem + COVERAGE_SUFFIX)
            definition._coverage = load_coverage(table)
            definition._coverage_file = table
        except Exception as exc:
            # A broken table: the definition alone decides (the engine treats it as absent), but the
            # author is told why the manual's coverage is missing.
            log.warning(
                "%s: coverage table ignored: %s",
                table.name,
                str(exc).splitlines()[0] if str(exc) else type(exc).__name__,
            )
    return definition


def _load_definition_file(
    path: Path, mtime: float, coverage_mtime: float | None = None
) -> InstrumentDefinition:
    data = _raw_definition(path, mtime)
    ancestors = tuple(_extends_chain(path, data))  # walked fresh: an ancestor edit re-keys below
    return _build_definition(path, mtime, coverage_mtime, ancestors)


class DefinitionLibrary:
    """Finds instrument definitions by id: first in the bench's own folders, then in the folders of
    :data:`DEFINITIONS_ENV`, then built-in."""

    def __init__(self, search_dirs: Iterable[Path] = ()) -> None:
        dirs = [Path(d) for d in search_dirs] + env_definition_dirs() + [BUILTIN_DIR]
        self.dirs = list(dict.fromkeys(dirs))

    def path(self, def_id: str) -> Path:
        for d in self.dirs:
            p = d / f"{def_id}.yaml"
            if p.is_file():
                return p
        raise Unknown(f"no instrument definition {def_id!r} in {', '.join(str(d) for d in self.dirs)}")

    def get(self, def_id: str) -> InstrumentDefinition:
        p = self.path(def_id)
        table = p.with_name(p.stem + COVERAGE_SUFFIX)  # a real model's coverage table (D42)
        coverage_mtime = table.stat().st_mtime if table.is_file() else None
        return _load_definition_file(p, p.stat().st_mtime, coverage_mtime)

    def __call__(self, def_id: str) -> InstrumentDefinition:
        return self.get(def_id)

    def ancestors(self, def_id: str) -> list[tuple[str, Path]]:
        """The definitions ``def_id`` extends, nearest first, as (id, file) — resolved the way the
        loader layers them (D59); empty without ``extends`` or when the chain is broken."""
        p = self.path(def_id)
        try:
            chain = _extends_chain(p, _raw_definition(p, p.stat().st_mtime))
        except ValueError:
            return []
        return [(parent.stem, parent) for parent, _ in chain]

    def ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for d in self.dirs:
            if d.is_dir():
                for p in sorted(d.glob("*.yaml")):
                    if not p.name.endswith(COVERAGE_SUFFIX):  # a real model's coverage table (D42)
                        seen.setdefault(p.stem, None)
        return list(seen)


def load_bench(path: str | Path) -> BenchDefinition:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) if p.suffix == ".json" else _read_yaml(p)
    except json.JSONDecodeError as exc:
        raise Invalid(f"{p.name}: {exc}") from None
    data, _ = upgrade(raw, "bench")
    return BenchDefinition.model_validate(data)
