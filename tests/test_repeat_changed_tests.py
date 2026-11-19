"""scripts/repeat_changed_tests.py: which changed files a pull request repeats, and when it does not (#1543)."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
script = load_script(ROOT / "scripts" / "repeat_changed_tests.py")

pytestmark = pytest.mark.unit


def test_only_pytest_files_under_tests_and_acceptance_are_picked():
    changed = [
        "src/simubench/server/workspace_events.py",
        "tests/test_server.py",
        "tests/helpers.py",
        "acceptance/test_scenarios.py",
        "web/e2e/build.spec.ts",
        "tests/test_server.py",
    ]
    assert script.test_files(changed) == ["acceptance/test_scenarios.py", "tests/test_server.py"]


def test_a_broad_change_is_not_repeated():
    assert "broad" in script.plan([f"tests/test_{i}.py" for i in range(script.MAX_FILES + 1)])


def test_up_to_the_limit_is_repeated():
    assert script.plan([f"tests/test_{i}.py" for i in range(script.MAX_FILES)]) is None


def test_no_changed_test_file_repeats_nothing():
    assert "no test file" in script.plan([])


def test_a_tui_boot_file_is_selected_and_only_the_main_only_budgets_are_not():
    """A pull request runs the TUI boot tier (the ``tui`` job), so its files are repeated too; a
    selection without them left a file of tui_boot tests with nothing to run (#1554)."""
    assert "tui_boot" not in script.MARKERS
    assert "not load_sensitive" in script.MARKERS


def test_an_empty_selection_is_not_a_failed_run():
    assert not script.failed(0)
    assert not script.failed(int(pytest.ExitCode.NO_TESTS_COLLECTED))
    assert script.failed(int(pytest.ExitCode.TESTS_FAILED))
