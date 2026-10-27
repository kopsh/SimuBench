"""File format versions and migrations (M5): older bench and definition files keep working.

Each format names its version in ``schema`` (``simubench.bench/1``). A migration upgrades the
parsed data by one version; loaders apply the chain in memory, and ``simubench migrate --write``
rewrites the files. A file from a newer SimuBench is refused with a clear message instead of a
confusing validation error.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Data = dict[str, Any]

CURRENT = {"bench": 1, "instrument": 1}

#: (format, from_version) -> function returning the data at from_version + 1.
MIGRATIONS: dict[tuple[str, int], Callable[[Data], Data]] = {}


def migration(fmt: str, from_version: int) -> Callable[[Callable[[Data], Data]], Callable[[Data], Data]]:
    def deco(fn: Callable[[Data], Data]) -> Callable[[Data], Data]:
        MIGRATIONS[(fmt, from_version)] = fn
        return fn

    return deco


class FormatError(ValueError):
    pass


def version_of(data: Data, fmt: str) -> int:
    schema = data.get("schema")
    if schema is None:
        return 0  # written before files named their format
    prefix = f"simubench.{fmt}/"
    if not isinstance(schema, str) or not schema.startswith(prefix) or not schema[len(prefix) :].isdigit():
        raise FormatError(f"schema {schema!r} is not a SimuBench {fmt} file")
    return int(schema[len(prefix) :])


def upgrade(data: Any, fmt: str) -> tuple[Any, list[str]]:
    """Bring parsed data to the current version; return it and the steps taken (empty if current)."""
    if not isinstance(data, dict):
        return data, []
    version = version_of(data, fmt)
    current = CURRENT[fmt]
    if version > current:
        raise FormatError(
            f"this {fmt} file is format version {version}; this SimuBench reads up to {current}: upgrade SimuBench"
        )
    steps = []
    while version < current:
        step = MIGRATIONS.get((fmt, version))
        if step is None:
            raise FormatError(f"no migration from {fmt} format version {version}")
        data = step(dict(data))
        steps.append(f"{fmt} {version} → {version + 1}")
        version += 1
    return data, steps


@migration("bench", 0)
def _bench_0_to_1(data: Data) -> Data:
    """Files without a schema line: name the format (the content is the same)."""
    return {"schema": "simubench.bench/1", **data}


@migration("instrument", 0)
def _instrument_0_to_1(data: Data) -> Data:
    return {"schema": "simubench.instrument/1", **data}
