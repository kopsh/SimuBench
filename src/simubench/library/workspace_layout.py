"""Which files of a workspace are SimuBench's, and where they live.

The judgements the write points (:class:`~simubench.server.benchfiles.BenchFiles`,
:class:`~simubench.server.definitionfiles.DefinitionFiles`), the workspace watcher, the session
manager and the storage inventory share: a bench file carries one of :data:`BENCH_SUFFIXES`; an
instrument definition is a ``.yaml`` in an ``instruments/`` folder (the workspace's own, or one
beside a bench — the bench-local library); every definition the workspace owns sits at
:data:`definition_rel_path`. :data:`~simubench.library.loader.SKIP_DIRS` is the same idea for the
directories that never hold the user's files.
"""

from __future__ import annotations

from pathlib import Path

#: The suffixes a bench file carries — the one triple, spelled once.
BENCH_SUFFIXES = (".bench.yaml", ".bench.json")

#: The folder holding instrument definition files: the workspace's own, or beside a bench.
DEFINITIONS_FOLDER = "instruments"


def is_bench_file(name: str) -> bool:
    """A file is a bench file when its name carries a bench suffix."""
    return name.endswith(BENCH_SUFFIXES)


def is_definition_file(path: Path) -> bool:
    """A file is an instrument definition when it is a ``.yaml`` in an ``instruments/`` folder,
    at any depth (the workspace's own folder, or a bench's local library beside it)."""
    return path.parent.name == DEFINITIONS_FOLDER and path.suffix == ".yaml"


def definitions_dir(workspace: Path) -> Path:
    """The workspace's own definitions folder."""
    return Path(workspace) / DEFINITIONS_FOLDER


def definition_rel_path(def_id: str) -> str:
    """A workspace definition's file, workspace-relative: ``instruments/<id>.yaml``."""
    return f"{DEFINITIONS_FOLDER}/{def_id}.yaml"
