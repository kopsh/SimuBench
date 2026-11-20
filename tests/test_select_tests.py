"""scripts/select_tests.py and tests/model_scope.py: a pull request runs the models it changed whole,
the other models smoke only, and everything it cannot place runs in full (#1680, D49)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import load_script
from model_scope import SMOKE_CASES, case_model, marked_models, parse_scope, split

ROOT = Path(__file__).resolve().parents[1]
script = load_script(ROOT / "scripts" / "select_tests.py")

pytestmark = pytest.mark.unit


def test_a_change_to_one_models_files_selects_that_model_and_nothing_else():
    sel = script.select(
        [
            "src/simubench/library/instruments/bk-8614.yaml",
            "src/simubench/library/instruments/bk-8614.coverage.yaml",
            "tests/models/bk-8614/cases.yaml",
            "docs/real-models.md",
        ]
    )
    assert not sel.full
    assert sel.models == {"bk-8614"}
    assert sel.args() == ["--model-scope=bk-8614"]


def test_shared_code_runs_everything():
    for path in (
        "src/simubench/engine/session.py",
        "src/simubench/library/components/scope.py",
        "src/simubench/scpi/parse.py",
        "conftest.py",
        "pyproject.toml",
        "scripts/select_tests.py",
        ".github/workflows/ci.yml",
        "tests/helpers.py",
        "examples/benches/siglent_scope_rc.bench.yaml",
    ):
        sel = script.select(["src/simubench/library/instruments/bk-8614.yaml", path])
        assert sel.full, path
        assert sel.args() == []


def test_a_path_nobody_knows_runs_everything():
    assert script.select(["somewhere/new.txt"]).full


def test_no_known_change_runs_everything():
    assert script.select([]).full


def test_docs_and_web_alone_select_no_model():
    sel = script.select(["docs/decisions.md", "web/src/app.tsx", "README.md"])
    assert not sel.full
    assert sel.args() == ["--model-scope="]


def test_a_changed_model_test_file_selects_the_models_it_declares():
    sel = script.select(["tests/test_keysight_uxr.py"])
    assert sel.models == {"keysight-uxr1104b", "keysight-uxr0334b", "keysight-33622a"}


def test_a_changed_base_definition_reaches_the_models_extending_it(tmp_path):
    inst = tmp_path / script.INSTRUMENTS
    inst.mkdir(parents=True)
    (inst / "base.yaml").write_text("id: base\n", encoding="utf-8")
    (inst / "mid.yaml").write_text("extends: base\nid: mid\n", encoding="utf-8")
    (inst / "leaf.yaml").write_text("extends: mid\nid: leaf\n", encoding="utf-8")
    (inst / "other.yaml").write_text("id: other\n", encoding="utf-8")
    sel = script.select(["src/simubench/library/instruments/base.yaml"], root=tmp_path)
    assert sel.models == {"base", "mid", "leaf"}


def _item(models=(), case_of=None):
    marks = [SimpleNamespace(args=tuple(models))] if models else []
    callspec = None
    if case_of is not None:
        callspec = SimpleNamespace(params={"cases": SimpleNamespace(model=case_of), "case": object()})
    return SimpleNamespace(iter_markers=lambda name: marks if name == "model" else [], callspec=callspec)


def test_a_marked_test_runs_only_when_one_of_its_models_is_in_scope():
    a, b, both = _item(["m-a"]), _item(["m-b"]), _item(["m-a", "m-b"])
    kept, dropped = split([a, b, both], {"m-b"})
    assert kept == [b, both]
    assert dropped == [a]


def test_a_model_out_of_scope_keeps_only_its_first_manual_cases():
    cases = [_item(case_of="m-a") for _ in range(SMOKE_CASES + 2)]
    kept, dropped = split(cases, set())
    assert kept == cases[:SMOKE_CASES]
    assert dropped == cases[SMOKE_CASES:]


def test_a_model_in_scope_keeps_every_manual_case_and_other_tests_always_run():
    cases = [_item(case_of="m-a") for _ in range(SMOKE_CASES + 2)]
    plain = _item()
    kept, dropped = split([plain, *cases], {"m-a"})
    assert kept == [plain, *cases]
    assert dropped == []


def test_an_item_without_a_model_is_not_a_manual_case():
    assert case_model(_item()) is None
    assert marked_models(_item(["m-a"])) == {"m-a"}


def test_the_scope_argument_is_a_comma_list_and_empty_means_none():
    assert parse_scope("a, b,,") == {"a", "b"}
    assert parse_scope("") == set()
