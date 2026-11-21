"""Which files of a workspace are SimuBench's (workspace_layout.py): one home for the judgements,
every reader asking here instead of spelling suffixes and the ``instruments`` folder itself."""

from __future__ import annotations

from pathlib import Path

import pytest

from simubench.library.workspace_layout import (
    BENCH_SUFFIXES,
    DEFINITIONS_FOLDER,
    definition_rel_path,
    definitions_dir,
    is_bench_file,
    is_definition_file,
)
from simubench.server.benchfiles import BENCH_SUFFIXES as benchfiles_suffixes

pytestmark = pytest.mark.unit


def test_a_bench_file_is_judged_by_its_suffix():
    assert is_bench_file("hello.bench.yaml") and is_bench_file("a.bench.json")
    assert not is_bench_file("a.yaml") and not is_bench_file("a.bench.yaml.bak")


def test_a_definition_is_a_yaml_in_an_instruments_folder_at_any_depth():
    assert is_definition_file(Path("ws/instruments/dmm.yaml"))
    assert is_definition_file(Path("ws/sub/instruments/dmm.yaml"))  # a bench's local library
    assert not is_definition_file(Path("ws/instruments/readme.md"))
    assert not is_definition_file(Path("ws/draft.yaml"))
    assert not is_definition_file(Path("ws/instruments/dmm.yaml.layout.json"))


def test_a_workspace_definition_sits_at_instruments_id_yaml():
    assert definition_rel_path("dmm") == "instruments/dmm.yaml"
    assert definitions_dir(Path("/ws")) == Path("/ws") / DEFINITIONS_FOLDER


def test_the_suffix_triple_is_the_one_the_file_homes_use():
    """The bench write point reads the same tuple the layout homes declare, not a copy."""
    assert benchfiles_suffixes is BENCH_SUFFIXES
