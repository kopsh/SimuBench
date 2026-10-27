"""In-process API: open a bench file as a running session (architecture.md 7.6)."""

from __future__ import annotations

from pathlib import Path

from .engine.session import Session
from .library.loader import DefinitionLibrary, load_bench
from .models.bench import BenchDefinition


def open_bench(
    bench: str | Path | BenchDefinition,
    *,
    clock: str | None = None,
    seed: int | None = None,
    record_dir: str | Path | None = None,
    record_raw: bool = False,
    definition_dirs: list[str | Path] | None = None,
) -> Session:
    """Load a bench file (YAML or JSON) and start a session.

    Instrument definitions are looked up in ``<bench folder>/instruments`` and in ``definition_dirs``
    before the built-in library.
    """
    base: Path | None = None
    if isinstance(bench, BenchDefinition):
        definition = bench
    else:
        base = Path(bench).resolve().parent
        definition = load_bench(bench)
    dirs: list[Path] = []
    if base is not None:
        dirs.append(base / "instruments")
    dirs += [Path(d) for d in definition_dirs or []]
    return Session(
        definition,
        DefinitionLibrary(dirs),
        base_dir=base,
        record_dir=Path(record_dir) if record_dir else None,
        record_raw=record_raw,
        clock=clock,
        seed=seed,
    )
