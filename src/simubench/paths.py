"""Where a user's own data lives: not in a workspace (#1237).

A workspace is a folder people zip, sync and send to colleagues; what belongs to one person (the
plugin trust they granted, their console history) must not travel with it. Everything of that
kind goes through :func:`user_data_dir` / :func:`user_state_dir`, never a ``Path.home()`` of its
own. These folders stay small: anything big or growing belongs to the workspace, which the user
placed on the disk they chose.

``SIMUBENCH_HOME`` overrides both (tests, CI, portable installs, a data disk other than the
system one).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

HOME_ENV = "SIMUBENCH_HOME"
APP = "simubench"


def _home(env: Mapping[str, str], home: Path | None) -> Path:
    return home if home is not None else Path.home()


def _base(kind: str, env: Mapping[str, str], platform: str, home: Path | None) -> Path:
    override = env.get(HOME_ENV)
    if override:
        return Path(override)
    h = _home(env, home)
    if platform.startswith("win"):
        return Path(env.get("LOCALAPPDATA") or h / "AppData" / "Local") / "SimuBench"
    if platform == "darwin":
        return h / "Library" / "Application Support" / "SimuBench"
    var, fallback = (
        ("XDG_STATE_HOME", ".local/state") if kind == "state" else ("XDG_DATA_HOME", ".local/share")
    )
    root = env.get(var)
    return (Path(root) if root and os.path.isabs(root) else h / fallback) / APP


def user_data_dir(
    env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> Path:
    """Small, durable per-user data: what the user granted or configured."""
    return _base("data", os.environ if env is None else env, platform or sys.platform, home)


def user_state_dir(
    env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> Path:
    """Small per-user state that may be lost without harm: histories, last-used values."""
    return _base("state", os.environ if env is None else env, platform or sys.platform, home)
