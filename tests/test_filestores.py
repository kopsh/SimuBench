"""The kind-keyed file door (D94, #1729): one ``FileStores`` facade over ``BenchFiles`` and
``DefinitionFiles`` — the dispatch the assistant's turn record used to hand-write. The six call
sites live on through ``tests/test_assistant.py`` and ``tests/test_assistant_rollback.py``; these
unit tests pin the seam's own contract: the two keys, the restore order, and the unknown kind."""

from __future__ import annotations

from pathlib import Path

from helpers import raises_with
from simubench.server.benchfiles import BenchFiles
from simubench.server.definitionfiles import DefinitionFiles
from simubench.server.errors import Invalid
from simubench.server.filestores import FileStores


def make(tmp_path: Path) -> FileStores:
    files = BenchFiles(tmp_path)
    return FileStores(files, DefinitionFiles(files))


def test_an_unknown_kind_is_the_domain_error_not_a_silent_fallback(tmp_path):
    with raises_with(Invalid, "draft"):
        make(tmp_path).read("draft", "hello.bench.yaml")


def test_the_history_key_is_the_path_for_a_bench_and_rederived_for_a_definition(tmp_path):
    stores = make(tmp_path)
    assert stores.history_rel("bench", "sub/hello.bench.yaml") == "sub/hello.bench.yaml"
    assert stores.history_rel("definition", "instruments/my-x.yaml") == "instruments/my-x.yaml"
    # the definition's place comes from the id, not from wherever the record saw the file
    assert stores.history_rel("definition", "somewhere/my-x.yaml") == "instruments/my-x.yaml"


def test_the_session_key_is_the_resolved_bench_path_or_the_definition_id(tmp_path):
    stores = make(tmp_path)
    assert stores.session_key("bench", "hello.bench.yaml") == (tmp_path / "hello.bench.yaml").resolve()
    assert stores.session_key("definition", "instruments/my-x.yaml") == "my-x"


def test_restores_rank_benches_before_definitions(tmp_path):
    stores = make(tmp_path)
    assert stores.restore_order("bench") < stores.restore_order("definition")
