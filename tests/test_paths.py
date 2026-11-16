"""Where a user's own data lives (#1237): one place per platform, overridable, outside workspaces."""

from pathlib import Path

import pytest

from simubench.paths import HOME_ENV, user_data_dir, user_state_dir

HOME = Path("/h/ann")


@pytest.mark.unit
def test_user_folders_follow_each_platforms_convention():
    linux = {}
    assert user_data_dir(linux, "linux", HOME) == HOME / ".local/share/simubench"
    assert user_state_dir(linux, "linux", HOME) == HOME / ".local/state/simubench"
    xdg = {"XDG_DATA_HOME": "/x/data", "XDG_STATE_HOME": "/x/state"}
    assert user_data_dir(xdg, "linux", HOME) == Path("/x/data/simubench")
    assert user_state_dir(xdg, "linux", HOME) == Path("/x/state/simubench")
    assert user_data_dir({"XDG_DATA_HOME": "relative"}, "linux", HOME) == HOME / ".local/share/simubench"
    assert user_data_dir({"LOCALAPPDATA": "D:/L"}, "win32", HOME) == Path("D:/L/SimuBench")
    assert user_data_dir({}, "win32", HOME) == HOME / "AppData" / "Local" / "SimuBench"
    assert user_state_dir({}, "darwin", HOME) == HOME / "Library/Application Support/SimuBench"


@pytest.mark.unit
def test_simubench_home_overrides_every_platform():
    env = {HOME_ENV: "/data/sb", "XDG_DATA_HOME": "/x", "LOCALAPPDATA": "C:/L"}
    for platform in ("linux", "win32", "darwin"):
        assert user_data_dir(env, platform, HOME) == Path("/data/sb")
        assert user_state_dir(env, platform, HOME) == Path("/data/sb")
