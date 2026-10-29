"""``simubench coverage <id> --draft``: a coverage table from a command list (metrology-authoring.md 9)."""

from __future__ import annotations

import shutil
from pathlib import Path

from simubench.cli import main
from simubench.library.coverage import (
    DRAFT_NOTE,
    check,
    draft_coverage,
    load_coverage,
    read_command_list,
)
from simubench.library.loader import DefinitionLibrary

ACME = Path(__file__).parent / "models" / "acme-dm1"

LIST = """\
:MEASure:VOLTage[:DC]?

# Command Reference > SENSe Subsystem
[:SENSe]:VOLTage[:DC]:NPLC
[:SENSe]:VOLTage[:DC]:RANGe

#   Command Reference > CALibration Subsystem
:CALibration:STORe
"""


def test_the_list_has_sections_and_ignores_blank_lines():
    assert read_command_list(LIST) == [
        ("Commands", [":MEASure:VOLTage[:DC]?"]),
        (
            "Command Reference > SENSe Subsystem",
            ["[:SENSe]:VOLTage[:DC]:NPLC", "[:SENSe]:VOLTage[:DC]:RANGe"],
        ),
        ("Command Reference > CALibration Subsystem", [":CALibration:STORe"]),
    ]
    assert read_command_list("# empty section\n\n") == []


def test_a_new_table_is_all_not_modeled(tmp_path, monkeypatch, capsys):
    (tmp_path / "list.txt").write_text(LIST, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["coverage", "acme-new", "--draft", "list.txt"]) == 0
    out = tmp_path / "instruments" / "acme-new.coverage.yaml"
    cov = load_coverage(out)
    assert cov.model == "acme-new"
    assert [s.section for s in cov.sections] == [name for name, _ in read_command_list(LIST)]
    assert {c.level for _, c in cov.entries()} == {"not_modeled"}
    assert "4 of 4 listed commands added" in capsys.readouterr().out


def test_an_existing_table_keeps_what_was_written_by_hand(tmp_path, capsys):
    shutil.copytree(ACME, tmp_path / "acme")
    table = tmp_path / "acme" / "acme-dm1.coverage.yaml"
    table.write_text("# Commands of the DM1 manual; levels by hand.\n" + table.read_text("utf-8"), "utf-8")
    before = {c.header: c for _, c in load_coverage(table).entries()}
    (tmp_path / "list.txt").write_text(LIST, encoding="utf-8")
    args = [
        "coverage",
        "acme-dm1",
        "--definitions",
        str(tmp_path / "acme"),
        "--draft",
        str(tmp_path / "list.txt"),
    ]
    assert main(args) == 0
    assert "3 of 4 listed commands added" in capsys.readouterr().out  # NPLC was in the table

    assert table.read_text("utf-8").startswith("# Commands of the DM1 manual; levels by hand.\n")
    after = load_coverage(table)
    entries = {c.header: (s, c) for s, c in after.entries()}
    for header, entry in before.items():
        assert entries[header][1] == entry  # level and note as written
    sense = next(s for s in after.sections if s.section == "Command Reference > SENSe Subsystem")
    assert sense.commands[-1].header == "[:SENSe]:VOLTage[:DC]:RANGe"  # joins its section
    assert after.sections[-1].section == "Command Reference > CALibration Subsystem"  # new at the end


def test_commands_the_definition_has_are_drafted_as_implemented():
    definition = DefinitionLibrary([ACME]).get("acme-dm1")
    commands = [("Commands", [c.header for c in definition.commands])]
    cov, added = draft_coverage("acme-dm1", commands, None, definition)
    assert added == len(definition.commands)
    assert {(c.level, c.note) for _, c in cov.entries()} == {("implemented", DRAFT_NOTE)}
    assert check(definition, cov, None) == []  # consistent until cases are required
