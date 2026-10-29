"""Plugin components: installed packages load by default, workspace files only when allowed.

The plugin API is the built-in one (``Component``, ``@component``, ``@op``, ``OpResult``,
``Signal``); a plugin module may declare ``plugin_api = 1`` for the version of that interface it
targets (instrument-authoring.md 4). Two sources, with different trust (D3):

* an installed package's ``simubench.components`` entry points load like any installed Python
  package - installing it is already the act of trust;
* ``components/*.py`` in a workspace may come from a git checkout or a colleague, so it is code
  that runs only when the engineer allows it: ``--allow-plugins``, or the workspace setting
  ``.simubench/settings.json`` with ``{"allow_workspace_plugins": true}``.

Every loaded plugin is listed with its origin and file hash: the run record header carries the
hashes, a replay warns when one changed (D36), and ``doctor`` / the diagnostics bundle list the
plugins (never their source code).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import sys
import threading
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLUGIN_API = 1
ENTRY_POINT_GROUP = "simubench.components"
SETTING = ".simubench/settings.json"

logger = logging.getLogger(__name__)


@dataclass
class PluginInfo:
    """One loaded (or failed) plugin, as doctor, the manifest and the record header show it."""

    name: str
    source: str  # "entry-point" | "workspace"
    origin: str  # "package 1.2.3", or the entry point value, or the file path
    sha256: str | None = None  # hash of the plugin file, for the replay check (D36)
    error: str | None = None  # set when it could not load, or targets another plugin_api


_LOCK = threading.Lock()
_ENTRY_POINTS: dict[str, PluginInfo] = {}  # by entry point value
_WORKSPACE: dict[str, PluginInfo] = {}  # by resolved file path


def _registry() -> dict[str, type]:
    from .engine.component import _REGISTRY

    return _REGISTRY


def _discover() -> list[Any]:
    """The ``simubench.components`` entry points; a seam for tests."""
    return list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))


def _first_line(exc: BaseException) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__


def _file_hash(module: types.ModuleType | None) -> str | None:
    file = getattr(module, "__file__", None)
    if not file:
        return None
    try:
        return hashlib.sha256(Path(file).read_bytes()).hexdigest()
    except OSError:
        return None


def _load_code(load: Any, name: str) -> tuple[types.ModuleType | None, str | None]:
    """Run a plugin module's code, then hold it to the declared ``plugin_api``.

    A module that targets another API is not kept: its component registrations are undone, so a
    plugin written for a future SimuBench cannot half-work here.
    """
    before = dict(_registry())
    try:
        module = load()
        if not isinstance(module, types.ModuleType):
            module = None
        if module is None:
            raise ValueError("the entry point does not point at a module")
    except Exception as exc:
        _registry().clear()
        _registry().update(before)
        return None, _first_line(exc)
    declared = getattr(module, "plugin_api", PLUGIN_API)
    if declared != PLUGIN_API:
        _registry().clear()
        _registry().update(before)
        return module, f"targets plugin_api {declared!r}, this SimuBench supports {PLUGIN_API}"
    _warn_overrides(before, name)
    return module, None


def _warn_overrides(before: dict[str, type], name: str) -> None:
    after = _registry()
    for key, cls in after.items():
        if key in before and before[key] is not cls:
            logger.warning("plugin %s overrides component type %r", name, key)


def ensure_entry_point_plugins() -> list[PluginInfo]:
    """Import every installed package's components (idempotent); the results stay listed."""
    with _LOCK:
        infos: list[PluginInfo] = []
        for ep in _discover():
            if ep.value in _ENTRY_POINTS:
                continue  # loaded once per process, whatever the outcome
            if getattr(ep, "dist", None):
                origin = f"{ep.dist.metadata['Name']} {ep.dist.version}"
            else:
                origin = ep.value
            module, error = _load_code(ep.load, ep.name)
            info = PluginInfo(
                ep.name,
                "entry-point",
                origin,
                sha256=_file_hash(module),
                error=error,
            )
            _ENTRY_POINTS[ep.value] = info
            infos.append(info)
            if error:
                logger.warning("plugin %s did not load: %s", ep.name, error)
        return infos


def load_workspace_plugins(directory: Path) -> list[PluginInfo]:
    """Import ``components/*.py`` from a workspace; the engineer has allowed it (D3).

    A file is imported once per content: run it again after it changed and the new classes replace
    the old ones in the registry.
    """
    with _LOCK:
        infos: list[PluginInfo] = []
        for path in sorted(Path(directory).glob("*.py")):
            resolved = path.resolve()
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            done = _WORKSPACE.get(str(resolved))
            if done and done.error is None and done.sha256 == sha:
                infos.append(done)
                continue
            _module, error = _load_code(
                lambda p=path, n=resolved.stem, s=sha: _exec_file(p, f"simubench_workspace.{n}.{s[:8]}"),
                path.name,
            )
            info = PluginInfo(path.name, "workspace", str(resolved), sha256=sha, error=error)
            _WORKSPACE[str(resolved)] = info
            infos.append(info)
            if error:
                logger.warning("workspace plugin %s did not load: %s", path.name, error)
        return infos


def _exec_file(path: Path, module_name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader  # a *.py path always yields a spec
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def loaded() -> list[PluginInfo]:
    """Every plugin this process has loaded, entry points first."""
    with _LOCK:
        return [*_ENTRY_POINTS.values(), *_WORKSPACE.values()]


def report() -> dict[str, dict[str, str | None]]:
    """The working plugins by name, for the run record header and the version manifest (D36)."""
    return {
        i.name: {"source": i.source, "origin": i.origin, "sha256": i.sha256}
        for i in loaded()
        if i.error is None
    }


def workspace_allows_plugins(workspace: Path) -> bool:
    """The workspace setting (``.simubench/settings.json``): ``{"allow_workspace_plugins": true}``."""
    try:
        data = json.loads((Path(workspace) / SETTING).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("allow_workspace_plugins") is True


def status_line(workspace: Path | None, allow: bool) -> str:
    """One startup-banner line: what is loaded, what failed, and what waits for consent."""
    parts = []
    for i in ensure_entry_point_plugins():
        parts.append(f"{i.name} {i.origin}" if i.error is None else f"{i.name}: {i.error}")
    if workspace is not None and (workspace / "components").is_dir():
        if allow:
            for i in load_workspace_plugins(workspace / "components"):
                parts.append(f"{i.name} (workspace)" if i.error is None else f"{i.name}: {i.error}")
        else:
            parts.append("workspace components/ present but not enabled (start with --allow-plugins)")
    return "; ".join(parts)


def _reset() -> None:
    """Test seam: forget what was loaded (the component registry itself is the caller's to clear)."""
    with _LOCK:
        _ENTRY_POINTS.clear()
        _WORKSPACE.clear()
