"""scripts/changelog.py and `simubench changelog`: product changes by version (D58)."""

from __future__ import annotations

from pathlib import Path

import pytest

import simubench.cli as cli
from helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
DAY = 1_793_750_400  # 2026-11-04 08:00 UTC+8
SCRIPT = ROOT / "scripts" / "changelog.py"

script = load_script(SCRIPT)  # dataclasses look their module up in sys.modules, load_script puts it there


def _history():
    m = script
    commits = [
        m.Commit("a", DAY, "型号 A", ["src/simubench/library/instruments/a.yaml"]),
        m.Commit("b", DAY + 60, "CI:合并机器人", [".github/workflows/ci.yml"]),
        m.Commit("c", DAY + 120, "界面:新按钮(Closes #7) (#8)", ["web/src/App.tsx", "tests/test_x.py"]),
        m.Commit("d", DAY + 86_400, "测试:补用例", ["tests/models/a/cases.yaml"]),
        m.Commit("e", DAY + 86_460, "依赖:numpy 升级", ["pyproject.toml"]),
    ]
    return m, commits


def test_lists_only_product_changes_one_section_per_day_newest_first():
    m, commits = _history()
    assert m.render(commits) == (
        "# SimuBench 变更记录\n\n"
        "## 2026.11.5.2（2026-11-05）\n\n- 依赖:numpy 升级\n\n"
        "## 2026.11.4.3（2026-11-04）\n\n- 型号 A\n- 界面:新按钮\n"
    )


def test_since_keeps_only_the_commits_after_that_version():
    m, commits = _history()
    text = m.render(commits, since="v2026.11.4.1")
    assert "型号 A" not in text and "界面:新按钮" in text and "依赖:numpy 升级" in text
    assert "## 2026.11.4.3" in text  # the day is still named by its last version


def test_since_the_last_version_is_empty():
    m, commits = _history()
    assert m.render(commits, since="2026.11.5.2") == "# SimuBench 变更记录\n"


def test_cli_prints_the_packaged_changelog(tmp_path, monkeypatch, capsys):
    (tmp_path / "CHANGELOG.md").write_text("# SimuBench 变更记录\n", encoding="utf-8")
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "cli.py"))
    assert cli.main(["changelog"]) == 0
    assert capsys.readouterr().out == "# SimuBench 变更记录\n"


def test_cli_without_a_packaged_changelog_says_where_it_comes_from(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "cli.py"))
    assert cli.main(["changelog"]) == 1
    assert "build-wheel.sh" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("subject", "line"),
    [
        ("核心:量程锁定按功能限定(Closes #589) (#591)", "量程锁定按功能限定"),
        ("5522A：量程锁定核实出处(Closes #583) (#590)", "5522A：量程锁定核实出处"),
        ("真实型号：Fluke 8588A（Closes #340）（#524）", "真实型号：Fluke 8588A"),
        ("bool 设置按键映射(运行时)(Refs #429) (#434)", "bool 设置按键映射(运行时)"),
        ("Real model: SDS2104X Plus (#11, D45)", "Real model: SDS2104X Plus"),
        ("索引参数可写号(SR865A 前置,Refs #333) (#12)", "索引参数可写号(SR865A 前置)"),
        ("核心能力：电源输出级 (#7)", "电源输出级"),
    ],
)
def test_entry_drops_issue_references_and_the_work_label(subject, line):
    assert script.entry(subject) == line


def test_entry_keeps_a_note_that_only_mentions_an_issue():
    assert script.entry("能力缺口登记 #539/#540") == "能力缺口登记 #539/#540"
