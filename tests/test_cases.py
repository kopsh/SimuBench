"""Running cases files (instrument-authoring.md 5.5, issue 45): `simubench cases`, the runner shared
with the manual cases of real models, and the JUnit report for CI."""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from simubench.cli import main
from simubench.library.coverage import load_cases, run_case, run_cases

ROOT = Path(__file__).resolve().parents[1]
LFCAL_CASES = ROOT / "examples" / "lowfreq-cal" / "cases.yaml"
ACME = ROOT / "tests" / "models" / "acme-dm1"


def _cases(tmp_path: Path, cases: list[dict]) -> Path:
    """A cases file for the framework's made-up model, whose definition lies next to it."""
    shutil.copy(ACME / "acme-dm1.yaml", tmp_path / "acme-dm1.yaml")
    path = tmp_path / "cases.yaml"
    path.write_text(
        yaml.safe_dump({"model": "acme-dm1", "cases": cases}, allow_unicode=True), encoding="utf-8"
    )
    return path


def test_the_contract_cases_of_the_lowfreq_bench_pass(tmp_path, capsys):
    report = tmp_path / "report.xml"
    assert main(["cases", str(LFCAL_CASES), "--junit", str(report)]) == 0
    n = len(load_cases(LFCAL_CASES).cases)
    assert f"{n}/{n} cases passed" in capsys.readouterr().out
    root = ET.parse(report).getroot()
    assert (root.tag, root.get("tests"), root.get("failures")) == ("testsuites", str(n), "0")
    assert len(root.findall("./testsuite/testcase")) == n


def test_a_failure_names_the_step_the_command_and_both_replies(tmp_path, capsys):
    path = _cases(
        tmp_path,
        [
            {"name": "good", "steps": [{"query": "*IDN?", "expect_match": "ACME Instruments,DM1,.*"}]},
            {
                "name": "bad",
                "manual": "Common Commands > *IDN?",
                "steps": [{"send": "*CLS"}, {"query": "*IDN?", "expect": "ACME,DM2"}],
            },
            {"name": "not a number", "steps": [{"query": "*IDN?", "expect_value": 1.0}]},
        ],
    )
    report = tmp_path / "report.xml"
    assert main(["cases", str(path), "--junit", str(report)]) == 1
    captured = capsys.readouterr()
    assert "1/3 cases passed" in captured.out
    assert "FAIL bad: step 2 ('*IDN?'): expected 'ACME,DM2', got 'ACME Instruments,DM1," in captured.err
    [failure] = [c for c in ET.parse(report).getroot().iter("testcase") if c.get("name").startswith("bad")]
    assert failure.get("name") == "bad [Common Commands > *IDN?]"
    assert "expected 'ACME,DM2'" in failure.find("failure").get("message")


def test_every_case_starts_on_a_freshly_powered_bench(tmp_path):
    path = _cases(
        tmp_path,
        [
            {"name": "set", "steps": [{"send": ":SENS:VOLT:NPLC 1"}]},
            {
                "name": "default again",
                "steps": [{"query": ":SENS:VOLT:DC:NPLC?", "expect": "+1.00000000E+01"}],
            },
        ],
    )
    assert [r.passed for r in run_cases(path)] == [True, True]


def test_a_bench_that_does_not_open_is_a_failed_case(tmp_path):
    path = tmp_path / "cases.yaml"
    path.write_text(
        yaml.safe_dump({"model": "no-such-model", "cases": [{"name": "x", "steps": [{"send": "*CLS"}]}]})
    )
    cases = load_cases(path)
    result = run_case(path, cases, cases.cases[0])
    assert not result.passed and "does not open" in result.error


def test_a_file_that_does_not_read_exits_2(tmp_path, capsys):
    path = tmp_path / "cases.yaml"
    path.write_text("cases: [{name: x, steps: [{send: a, query: b}]}]\n")
    assert main(["cases", str(path)]) == 2
    assert "exactly one of send or query" in capsys.readouterr().err
