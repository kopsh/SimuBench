"""The kind-keyed file door (D94, #1729): one ``FileStores`` facade over ``BenchFiles`` and
``DefinitionFiles`` — the dispatch the assistant's turn record used to hand-write. The six call
sites live on through ``tests/test_assistant.py`` and ``tests/test_assistant_rollback.py``; these
unit tests pin the seam's own contract: the two keys, the restore order, the unknown kind, and the
one caliber of a definition's change report (#1727)."""

from __future__ import annotations

import ast
from pathlib import Path

from helpers import raises_with
from simubench.engine.jsonpatch import PatchOp
from simubench.server.benchfiles import BenchFiles, parse_data
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


def test_definition_changes_come_from_the_layered_comparison_not_a_raw_one(tmp_path):
    """#1727: the definition side does not re-compare the raw data; it asks DefinitionFiles, so a
    copy's edit against its ``extends`` parent reads as what the instrument runs — an inherited
    default overridden — the same items the tool reply and the version list carry. Raw, the same
    edit says the file gained a state key."""
    files = BenchFiles(tmp_path)
    definitions = DefinitionFiles(files)
    stores = FileStores(files, definitions)
    definitions.create(
        "base-x",
        {"channel_state": {"frequency": {"type": "float", "min": 0.0, "max": 1.0e7, "default": 1000.0}}},
    )
    definitions.copy_of("base-x")
    rel = "instruments/my-base-x.yaml"
    sha = definitions.read("my-base-x").sha256
    edited = definitions.edit(
        "my-base-x",
        sha,
        [
            PatchOp(op="add", path="/channel_state", value={}),
            PatchOp(op="add", path="/channel_state/frequency", value={"default": 5.0e6}),
        ],
    )
    (item,) = edited.changes
    assert (item.kind, item.action, item.target, item.detail, item.old, item.new) == (
        "state",
        "changed",
        "ch<n>.frequency",
        "default",
        1000.0,
        5.0e6,
    )

    old = parse_data(Path(rel), files.history.content(rel, files.history.versions(rel)[-1].seq))
    new = parse_data(Path(rel), (tmp_path / rel).read_bytes())
    assert stores.changes("definition", rel, old, new) == edited.changes


def test_definition_changes_is_called_directly_only_by_definitionfiles():
    """#1727: ``definition_changes`` is the layered caliber's raw comparison and ``DefinitionFiles``
    is its only direct caller — every reporter of definition changes (the tool replies, the version
    list, ``changes_between``, the assistant's turn summaries through the facade) reads the layered
    items there, so D59's semantics cannot drift apart per reporter."""
    root = Path(__file__).resolve().parents[1] / "src" / "simubench" / "server"
    callers = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "definitionchanges.py":  # the function's own module
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = any(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "definition_changes")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "definition_changes")
            )
            for node in ast.walk(tree)
        )
        if called:
            callers.append(path.relative_to(root))
    assert callers == [Path("definitionfiles.py")]
