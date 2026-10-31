"""Loading instrument definitions and bench files."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

import yaml

from ..models.bench import BenchDefinition
from ..models.definition import InstrumentDefinition
from .migrate import upgrade

#: Built-in definitions: real models only (D42). SimuBench's own ``sb-*`` validation definitions are
#: not shipped; they live in ``validation/instruments`` of the repository and are loaded through
#: :data:`DEFINITIONS_ENV`.
BUILTIN_DIR = Path(__file__).parent / "instruments"
#: Extra definition folders, separated like ``PATH`` (``os.pathsep``), searched before the built-in ones.
DEFINITIONS_ENV = "SIMUBENCH_DEFINITIONS"
#: Coverage tables live next to their definitions (D42) and are not definitions themselves.
COVERAGE_SUFFIX = ".coverage.yaml"


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
def _load_definition_file(path: Path, mtime: float) -> InstrumentDefinition:
    data, _ = upgrade(_read_yaml(path), "instrument")
    definition = InstrumentDefinition.model_validate(data)
    definition._source_file = path  # events that classify a command name this file (D18)
    return definition


@lru_cache(maxsize=256)
def _load_coverage_file(path: Path, mtime: float) -> object | None:
    """The coverage table next to a definition, for the runtime classification (D18); None when it
    cannot be read (an instrument must still run when its table is broken)."""
    try:
        from .coverage import load_coverage

        return load_coverage(path)
    except Exception:
        return None


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
        return _load_definition_file(p, p.stat().st_mtime)

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
