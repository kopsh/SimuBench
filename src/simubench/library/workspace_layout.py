"""Which files of a workspace are SimuBench's, and where they live.

The judgements the write points (:class:`~simubench.server.benchfiles.BenchFiles`,
:class:`~simubench.server.definitionfiles.DefinitionFiles`), the workspace watcher, the session
manager and the storage inventory share: a bench file carries one of :data:`BENCH_SUFFIXES`; an
instrument definition is a ``.yaml`` in an ``instruments/`` folder (the workspace's own, or one
beside a bench — the bench-local library); every definition the workspace owns sits at
:data:`definition_rel_path`; SimuBench's own data lives under :data:`STORAGE_ROOT`, with a bench's
instrument files in the folder :func:`bench_files_folder_rel` names (D95).
:data:`~simubench.library.loader.SKIP_DIRS` is the same idea for the directories that never hold
the user's files.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

#: The suffixes a bench file carries — the one triple, spelled once.
BENCH_SUFFIXES = (".bench.yaml", ".bench.json")

#: The last suffix a bench file carries beyond ``.bench``: the folder where its instruments keep
#: their files (#1069) drops it — ``b.bench.yaml`` keeps files in ``files/b.bench/`` — and the
#: storage inventory adds one back when it judges a folder live (D95). Both directions spell
#: this pair once.
BENCH_FILE_TAILS = (".yaml", ".json")

#: The folder holding instrument definition files: the workspace's own, or beside a bench.
DEFINITIONS_FOLDER = "instruments"

#: Where a workspace keeps SimuBench's own data, with the subfolders by kind (D95): saved file
#: versions, conversations, instrument files, and the server's settings.
STORAGE_ROOT = ".simubench"
HISTORY_FOLDER = "history"
ASSISTANT_FOLDER = "assistant"
FILES_FOLDER = "files"
SETTINGS_NAME = "settings.json"


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


def bench_files_folder_rel(bench_rel: str) -> str:
    """A bench's ``files/`` folder, files-root-relative: its path without the last suffix (D95) —
    ``b.bench.yaml`` keeps files in ``files/b.bench/``."""
    return PurePosixPath(bench_rel).with_suffix("").as_posix()


def bench_files_candidates(folder_rel: str) -> tuple[str, ...]:
    """The bench files a ``files/`` folder can belong to: the folder name with a bench tail added
    back — the inverse of :func:`bench_files_folder_rel`."""
    return tuple(f"{folder_rel}{tail}" for tail in BENCH_FILE_TAILS)
