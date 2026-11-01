"""A user's own instrument definition (D42, #172): its reference is the author's specification, and
the workspace tools check it like a built-in real model — validate, coverage, cases and MCP."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from simubench.api import open_bench
from simubench.cli import main
from simubench.library.coverage import check, coverage_path, load_cases, load_coverage
from simubench.library.loader import DefinitionLibrary
from simubench.server.manager import SessionManager

REFERENCE = {
    "title": "ACME 在研仪表 SCPI 接口规格",
    "document": "SPEC-ACME-001",
    "version": "草案 v0.3",
}

DEFINITION = {
    "schema": "simubench.instrument/1",
    "id": "acme-user-1",
    "manufacturer": "ACME",
    "model": "USER-1",
    "class": "multimeter",
    "reference": REFERENCE,
    "state": {"sense.value": {"type": "float", "unit": "V", "default": 1.5}},
    "commands": [{"header": ":MEASure[:VOLTage]", "state": "sense.value", "set": False}],
}

COVERAGE = {
    "schema": "simubench.coverage/1",
    "model": "acme-user-1",
    "sections": [
        {
            "section": "SPEC-ACME-001 §3 测量命令",
            "commands": [
                {"header": ":MEASure[:VOLTage]", "level": "implemented", "note": "返回当前设定值"},
                {"header": "*IDN", "level": "implemented"},
            ],
        }
    ],
}

CASES = {
    "model": "acme-user-1",
    "bench": "bench.yaml",
    "instrument": "dut",
    "cases": [
        {
            "name": "测量命令按规格返回设定值",
            "manual": "SPEC-ACME-001 §3.1 :MEASure?",
            "steps": [{"query": ":MEAS?", "expect": "+1.500000E+00"}],
        },
        {
            "name": "身份查询",
            "manual": "SPEC-ACME-001 §2 *IDN?",
            "steps": [{"query": "*IDN?", "expect": "ACME,USER-1,SB000001,1.0.0"}],
        },
    ],
}

BENCH = {
    "schema": "simubench.bench/2",
    "name": "user-model",
    "instruments": {"dut": {"model": "acme-user-1"}},
    "run": {"clock": "command", "idle": "off"},
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "instruments").mkdir()
    (tmp_path / "instruments" / "acme-user-1.yaml").write_text(
        yaml.safe_dump(DEFINITION, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (tmp_path / "instruments" / "acme-user-1.coverage.yaml").write_text(
        yaml.safe_dump(COVERAGE, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (tmp_path / "acme-user-1.cases.yaml").write_text(
        yaml.safe_dump(CASES, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (tmp_path / "bench.yaml").write_text(yaml.safe_dump(BENCH, allow_unicode=True), encoding="utf-8")
    return tmp_path


def test_a_user_definition_follows_the_real_model_rules(workspace: Path):
    library = DefinitionLibrary([workspace / "instruments"])
    definition = library.get("acme-user-1")
    assert definition.reference is not None and definition.reference.title == REFERENCE["title"]
    assert definition.reference.version == "草案 v0.3"
    coverage = load_coverage(coverage_path(library.path("acme-user-1")))
    cases = load_cases(workspace / "acme-user-1.cases.yaml")
    assert check(definition, coverage, cases) == []


def test_validate_coverage_and_cases_run_on_the_workspace(workspace: Path, monkeypatch, capsys):
    monkeypatch.chdir(workspace)
    assert main(["validate", "instruments/acme-user-1.yaml"]) == 0
    assert main(["coverage", "acme-user-1"]) == 0
    out = capsys.readouterr().out
    assert REFERENCE["title"] in out and "实现" in out
    assert main(["cases", "acme-user-1.cases.yaml"]) == 0
    assert "2/2 cases passed" in capsys.readouterr().out


def test_the_unknown_command_reply_does_not_blame_simubench(workspace: Path):
    session = open_bench(workspace / "bench.yaml")
    try:
        session.write("dut", b":NOPE?\n")
        error = session.instrument("dut").errors.peek_all()[-1]
        assert error.code == -113
        assert "not implemented in this definition" in (error.text or "")
        assert "SimuBench" not in (error.text or "")
    finally:
        session.close()


def test_mcp_run_cases_uses_the_workspace_definition(workspace: Path):
    pytest.importorskip("mcp")
    import asyncio

    from mcp import Client

    from simubench.mcp_server import build_server

    manager = SessionManager(workspace, scpi_base_port=None)
    server = build_server(manager, ui_url="http://127.0.0.1:1/?token=x")

    async def run() -> dict:
        async with Client(server) as client:
            result = await client.call_tool("run_cases", {"path": "acme-user-1.cases.yaml"})
            assert not result.is_error
            return result.structured_content or {}

    result = asyncio.run(run())
    assert result["passed"] == 2 and result["failed"] == 0


def test_cases_and_coverage_are_checked_from_another_folder(workspace: Path, monkeypatch, capsys):
    """The workspace's instruments/ folder is searched when the files are elsewhere (#172)."""
    (workspace / "sub").mkdir()
    shutil.copy(workspace / "acme-user-1.cases.yaml", workspace / "sub" / "acme-user-1.cases.yaml")
    shutil.copy(workspace / "bench.yaml", workspace / "sub" / "bench.yaml")
    monkeypatch.chdir(workspace)
    assert main(["cases", "sub/acme-user-1.cases.yaml"]) == 0
    assert "2/2 cases passed" in capsys.readouterr().out
    assert main(["coverage", "acme-user-1"]) == 0
    assert REFERENCE["title"] in capsys.readouterr().out
