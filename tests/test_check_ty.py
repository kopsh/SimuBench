"""scripts/check_ty.py must not pass when ty itself is broken (#916): a crash, a bad config or an
output-format change parses to zero diagnostics, and a gate that only counts new diagnostics would
call that clean. The fake subprocess.run below plays ty with its real return-code contract: 0 = no
diagnostics, 1 = diagnostics found, anything else = tool failure."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_ty.py"

script = load_script(SCRIPT)

DIAGNOSTIC = "src/simubench/engine/edit.py:301:20: error[unsupported-operator] Operator `-` is not supported"


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """The checker with its baseline pointed at a scratch file; call with a fake ty result."""
    (tmp_path / "ty_baseline.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(script, "BASELINE", tmp_path / "ty_baseline.txt")

    def call(returncode: int, stdout: str = "", stderr: str = "", capsys=None) -> int:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr),
        )
        rc = script.main()
        if capsys is not None:
            out = capsys.readouterr()
            return rc, out.out + out.err
        return rc

    return call


@pytest.mark.unit
def test_a_ty_crash_fails_the_gate_instead_of_passing(gate, capsys):
    rc, out = gate(2, stdout="", stderr="ty: internal error", capsys=capsys)
    assert rc == 1
    assert "type check itself failed" in out and "internal error" in out


@pytest.mark.unit
def test_diagnostics_without_parseable_lines_fail_the_gate(gate, capsys):
    """Exit 1 with nothing the parser recognizes means the output format changed (#916)."""
    rc, out = gate(1, stdout="ty: something happened\n", stderr="", capsys=capsys)
    assert rc == 1
    assert "none matched the expected" in out


@pytest.mark.unit
def test_windows_backslash_paths_parse_to_the_same_keys(gate, capsys):
    """ty prints `src\\...` on Windows: the baseline keys are POSIX-shaped everywhere (#921)."""
    script.BASELINE.write_text(
        "src/simubench/access/hislip_server.py:invalid-argument-type Argument to bound method"
        " `HislipSession._send` is incorrect: Expected `StreamWriter | None`, found"
        " `StreamWriter | SecureStream | None`\n"
    )
    win_line = (
        "src\\simubench\\access\\hislip_server.py:172:24: error[invalid-argument-type]"
        " Argument to bound method `HislipSession._send` is incorrect: Expected"
        " `StreamWriter | None`, found `StreamWriter | SecureStream | None`\n"
    )
    rc, out = gate(1, stdout=win_line, capsys=capsys)
    assert rc == 0 and "none new" in out


@pytest.mark.unit
def test_a_diagnostic_outside_the_baseline_is_reported(gate, capsys):
    script.BASELINE.write_text(
        "src/simubench/engine/edit.py:unsupported-operator Operator `+` is not supported\n"
    )
    rc, out = gate(1, stdout=DIAGNOSTIC + "\n", capsys=capsys)
    assert rc == 1
    assert "NEW" in out and "unsupported-operator" in out


@pytest.mark.unit
def test_no_diagnostics_is_clean_and_only_suggests_shortening(gate, capsys):
    script.BASELINE.write_text(
        "src/simubench/engine/edit.py:unsupported-operator Operator `+` is not supported\n"
    )
    rc, out = gate(0, stdout="", capsys=capsys)
    assert rc == 0
    assert "none new" in out and "shorten" in out
