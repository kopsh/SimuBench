"""Every settings group of a built-in model or validation instrument has a name in both languages (#1251).

The inspector and the acceptance page group an instrument's settings by the first segment of
their path (``power.ref_level`` → ``power``) and title the group ``inspector.group.<segment>``
(``web/src/features/workbench/model.ts`` ``groupName``); a numbered segment (``function3``) uses
``<prefix>N``. A segment without a name would show as the bare identifier.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from simubench.engine.session import Session
from simubench.library.loader import BUILTIN_DIR, DefinitionLibrary
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]


def _web_keys(lang: str) -> set[str]:
    spec = importlib.util.spec_from_file_location("check_tui_i18n", ROOT / "scripts/check_tui_i18n.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.flatten(mod.parse_ts(ROOT / f"web/src/i18n/{lang}.ts")))


def _segments() -> dict[str, str]:
    """Each group segment of the built-in models and the validation instruments, with one model that has it."""
    validation = ROOT / "validation/instruments"
    lib = DefinitionLibrary([validation])
    found: dict[str, str] = {}
    for path in sorted([*BUILTIN_DIR.glob("*.yaml"), *validation.glob("*.yaml")]):
        if "." in path.stem:  # coverage tables beside the definitions
            continue
        bench = BenchDefinition.model_validate(
            {"schema": "simubench.bench/1", "name": "g", "instruments": {"i": {"model": path.stem}}}
        )
        inst = Session(bench, lib.get, clock="stepped", seed=1).instrument("i")
        for name in inst.specs:
            if name.startswith("ch") and name[2:3].isdigit():
                name = name.split(".", 1)[1]
            if "." in name:
                found.setdefault(name.split(".", 1)[0], path.stem)
    return found


def _key(segment: str, keys: set[str]) -> str | None:
    stem = segment.rstrip("0123456789")
    if stem and stem != segment and f"inspector.group.{stem}N" in keys:
        return f"inspector.group.{stem}N"
    return f"inspector.group.{segment}" if f"inspector.group.{segment}" in keys else None


def test_every_settings_group_of_a_built_in_model_has_a_name():
    segments = _segments()
    assert "power" in segments and "function1" in segments  # the analyzer's, the scope's math
    for lang in ("zh", "en"):
        keys = _web_keys(lang)
        missing = {s: model for s, model in segments.items() if _key(s, keys) is None}
        assert not missing, f"{lang}.ts: groups without inspector.group.<segment>: {missing}"
