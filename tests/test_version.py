"""scripts/version.py: the build version from the git history, no tags (D58)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))

script = load_script(ROOT / "scripts" / "version.py")


def _at(y: int, mo: int, d: int, h: int, mi: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=CST).timestamp())


def test_counts_commits_per_day_from_one():
    epochs = [_at(2026, 11, 4, 9), _at(2026, 11, 4, 10), _at(2026, 11, 5, 9)]
    assert script.numbered(epochs) == ["2026.11.4.1", "2026.11.4.2", "2026.11.5.1"]


def test_day_boundary_is_midnight_utc_plus_8():
    # 07:30 on Nov 5 in UTC+8 is still Nov 4 in UTC; the version follows UTC+8.
    assert script.numbered([_at(2026, 11, 4, 23, 59), _at(2026, 11, 5, 7, 30)]) == [
        "2026.11.4.1",
        "2026.11.5.1",
    ]


def test_versions_sort_numerically_not_as_text():
    assert script.parse("2026.11.9.10") > script.parse("2026.11.9.9")


def test_archive_gives_the_date_with_n_zero(tmp_path):
    (tmp_path / ".git_archival.txt").write_text(
        "node: " + "a" * 40 + "\nnode-date: 2026-11-09T01:30:00+08:00\n", encoding="utf-8"
    )
    assert script.version(tmp_path) == "2026.11.9.0"


def test_an_unexpanded_archive_file_is_unknown(tmp_path):
    (tmp_path / ".git_archival.txt").write_text("node-date: $Format:%cI$\n", encoding="utf-8")
    assert script.version(tmp_path) == "0+unknown"


def test_an_sdist_keeps_the_version_it_was_built_with(tmp_path):
    f = tmp_path / "src" / "simubench" / "_version.py"
    f.parent.mkdir(parents=True)
    f.write_text('__version__ = "2026.11.9.4"\n__commit_id__ = None\n', encoding="utf-8")
    assert script.version(tmp_path) == "2026.11.9.4"


def _git(cwd, *args, date="2026-11-09T10:00:00+08:00"):
    import os
    import subprocess

    env = {**os.environ, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    )


def _repo_with_three_commits_on_one_day(path):
    path.mkdir()
    _git(path, "init", "-q")
    for i in range(3):
        (path / "f.txt").write_text(str(i))
        _git(path, "add", "f.txt")
        _git(path, "commit", "-q", "-m", f"c{i}", date=f"2026-11-09T1{i}:00:00+08:00")
    return path


def test_a_full_clone_counts_the_day(tmp_path):
    repo = _repo_with_three_commits_on_one_day(tmp_path / "full")
    assert script.version(repo) == "2026.11.9.3"


def test_a_shallow_clone_does_not_pass_a_short_count_off_as_the_version(tmp_path, capsys):
    """#828: the history is cut, so N is unknown: the date with N 0 (as for an archive), loudly."""
    full = _repo_with_three_commits_on_one_day(tmp_path / "full")
    shallow = tmp_path / "shallow"
    import subprocess

    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{full}", str(shallow)], check=True)
    assert script.version(shallow) == "2026.11.9.0"
    assert "git fetch --unshallow" in capsys.readouterr().err
    _git(shallow, "fetch", "-q", "--unshallow")
    assert script.version(shallow) == "2026.11.9.3"
