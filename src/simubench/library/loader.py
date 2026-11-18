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

from ..models.bench import BenchDefinition
from ..models.definition import InstrumentDefinition
from .class_defaults import deep_merge, default_block, drop_deleted
from .migrate import upgrade

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


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = load_yaml(f.read())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
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
    raise ValueError(f"{source}: extends names {parent_id!r}, which is no definition in {known}")


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
            raise ValueError(f"{current}: extends must be a definition id (a string)")
        parent = _extends_target(parent_id, current)
        if parent in seen:
            ring = " → ".join([str(p) for p, _ in chain[::-1]] + [str(parent)])
            raise ValueError(f"{path}: extends 成环:{ring}")
        parent_mtime = parent.stat().st_mtime
        chain.append((parent, parent_mtime))
        seen.add(parent)
        current, current_data = parent, _raw_definition(parent, parent_mtime)


class LayerError(ValueError):
    """A definition that cannot be layered: a missing or cyclic ``extends`` (E-DEF-EXTENDS), or a
    ``command_overrides`` entry that names no single inherited command (E-DEF-COMMAND-OVERRIDE).
    ``code`` and ``pointer`` say where, for the diagnostics."""

    def __init__(self, message: str, code: str = "E-DEF-EXTENDS", pointer: str = "/extends") -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer


#: The per-command layer of a definition (D59): ``{header: whole command | null}``.
OVERRIDES = "command_overrides"


def _ptr_part(text: str) -> str:
    return text.replace("~", "~0").replace("/", "~1")


def apply_command_overrides(commands: list, overrides: dict) -> list:
    """``commands`` with ``overrides`` applied, each entry naming one command by its header exactly
    as written: a command replaces the command with that header *whole* (no field merge — D59's
    reason for replacing lists), one no command has is appended, ``null`` removes it. A header the
    list holds more than once, or a removal of a header it does not hold, raises :class:`LayerError`."""
    out = list(commands)
    for header, command in overrides.items():
        at = [i for i, c in enumerate(out) if isinstance(c, dict) and c.get("header") == header]
        pointer = f"/{OVERRIDES}/{_ptr_part(str(header))}"
        if len(at) > 1:
            raise LayerError(
                f"命令头 {header} 在继承的命令表里出现 {len(at)} 次，按命令头改不出是哪一条；改写整张 commands",
                "E-DEF-COMMAND-OVERRIDE",
                pointer,
            )
        if command is None:
            if not at:
                raise LayerError(
                    f"继承的命令表里没有命令头 {header}，无从删除", "E-DEF-COMMAND-OVERRIDE", pointer
                )
            del out[at[0]]
            continue
        if not isinstance(command, dict):
            raise LayerError(
                f"{header} 的值应是一条命令（不含 header）或 null", "E-DEF-COMMAND-OVERRIDE", pointer
            )
        whole = {"header": header, **{k: v for k, v in command.items() if k != "header"}}
        if at:
            out[at[0]] = whole
        else:
            out.append(whole)
    return out


def _layered(data: dict, ancestors: list[tuple[Path, float]]) -> dict:
    """The definition's data with every layer merged under it (instrument-authoring.md 2.9):
    类别默认 → 父 → 子 — the class's default block lowest, then the ancestors from the far end (the nearest parent over the grandparent),
    the file's own data last. ``errors`` merge by code, everything else by key; lists replace.

    ``command_overrides`` (D59) is set aside before the merge and applied afterwards to the merged
    ``commands``, layer by layer from the far end — only the layers above the nearest one that
    writes its own ``commands`` list, since that list replaces everything below it.

    The class default serves real models (D42, a `reference`): a table entry replaces the
    engine's "standard text; detail" reply, so a definition without a reference (the ``sb-*``
    validation instruments, a toy) keeps the engine's standard texts as before."""
    layers = [data] + [_raw_definition(parent, mtime) for parent, mtime in ancestors]  # child first
    overrides = [layer.get(OVERRIDES) for layer in layers]
    merged = {k: v for k, v in data.items() if k != OVERRIDES}
    layered = False
    for raw in layers[1:]:  # nearest parent first: each lower layer goes under
        merged = deep_merge({k: v for k, v in raw.items() if k != OVERRIDES}, merged)
        layered = True
    if merged.get("reference") is not None:
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
                        f"{OVERRIDES} 应是 命令头 → 命令 的映射", "E-DEF-COMMAND-OVERRIDE", f"/{OVERRIDES}"
                    )
                commands = apply_command_overrides(commands, own)
        merged = {**merged, "commands": commands}
    return merged


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
        raise KeyError(f"no instrument definition {def_id!r} in {', '.join(str(d) for d in self.dirs)}")

    def get(self, def_id: str) -> InstrumentDefinition:
        p = self.path(def_id)
        table = p.with_name(p.stem + COVERAGE_SUFFIX)  # a real model's coverage table (D42)
        coverage_mtime = table.stat().st_mtime if table.is_file() else None
        return _load_definition_file(p, p.stat().st_mtime, coverage_mtime)

    def __call__(self, def_id: str) -> InstrumentDefinition:
        return self.get(def_id)

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
    raw = json.loads(p.read_text(encoding="utf-8")) if p.suffix == ".json" else _read_yaml(p)
    data, _ = upgrade(raw, "bench")
    return BenchDefinition.model_validate(data)
