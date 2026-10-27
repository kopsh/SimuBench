"""Loading instrument definitions and bench files."""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

import yaml

from ..models.bench import BenchDefinition
from ..models.definition import InstrumentDefinition

BUILTIN_DIR = Path(__file__).parent / "instruments"


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


@lru_cache(maxsize=256)
def _load_definition_file(path: Path, mtime: float) -> InstrumentDefinition:
    return InstrumentDefinition.model_validate(_read_yaml(path))


class DefinitionLibrary:
    """Finds instrument definitions by id: first in the bench's own folders, then built-in."""

    def __init__(self, search_dirs: Iterable[Path] = ()) -> None:
        self.dirs = [Path(d) for d in search_dirs] + [BUILTIN_DIR]

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
                    seen.setdefault(p.stem, None)
        return list(seen)


def load_bench(path: str | Path) -> BenchDefinition:
    p = Path(path)
    if p.suffix == ".json":
        return BenchDefinition.model_validate_json(p.read_text(encoding="utf-8"))
    return BenchDefinition.model_validate(_read_yaml(p))
