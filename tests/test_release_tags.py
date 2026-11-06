"""scripts/release_tags.py: the date tag of each main commit (D58)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))
SCRIPT = ROOT / "scripts" / "release_tags.py"

script = load_script(SCRIPT)


def _at(y: int, mo: int, d: int, h: int, mi: int = 0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=CST).timestamp())


def test_counts_commits_per_day_from_one():
    tags = script.date_tags(
        [("a", _at(2026, 11, 4, 9)), ("b", _at(2026, 11, 4, 10)), ("c", _at(2026, 11, 5, 9))]
    )
    assert tags == {"a": "v2026.11.4.1", "b": "v2026.11.4.2", "c": "v2026.11.5.1"}


def test_day_boundary_is_midnight_utc_plus_8():
    # 07:30 on Nov 5 in UTC+8 is still Nov 4 in UTC; the tag follows UTC+8.
    tags = script.date_tags([("a", _at(2026, 11, 4, 23, 59)), ("b", _at(2026, 11, 5, 7, 30))])
    assert tags == {"a": "v2026.11.4.1", "b": "v2026.11.5.1"}


def test_tags_only_the_commits_after_the_newest_tagged_one():
    commits = [("a", _at(2026, 11, 4, 9)), ("b", _at(2026, 11, 4, 10)), ("c", _at(2026, 11, 4, 11))]
    assert script.missing(commits, {"a"}) == [("v2026.11.4.2", "b"), ("v2026.11.4.3", "c")]


def test_without_any_tag_only_head_is_tagged_with_its_day_count():
    commits = [("a", _at(2026, 11, 4, 9)), ("b", _at(2026, 11, 4, 10))]
    assert script.missing(commits, set()) == [("v2026.11.4.2", "b")]


def test_nothing_to_tag_when_head_is_tagged():
    commits = [("a", _at(2026, 11, 4, 9)), ("b", _at(2026, 11, 4, 10))]
    assert script.missing(commits, {"b"}) == []
