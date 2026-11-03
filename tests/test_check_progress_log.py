"""scripts/check_progress_log.py reads git output as UTF-8 on every platform (issue #414).

On Windows ``text=True`` alone decodes with the locale code page (cp1252), which cannot decode
the Chinese text of ``docs/progress-log.md``. The fake ``subprocess.run`` below decodes the way
the real one does: with ``encoding`` when given, else with cp1252 when ``text=True``, so the
tests run on any platform.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOG_TEXT = "# 进度日志\n\n## 自检:#414\n"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_progress_log", ROOT / "scripts" / "check_progress_log.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_run(raw: bytes | None):
    def run(args, **kwargs):
        if raw is None:
            return SimpleNamespace(stdout=None)
        encoding = kwargs.get("encoding") or ("cp1252" if kwargs.get("text") else None)
        if encoding is None:
            return SimpleNamespace(stdout=raw)
        return SimpleNamespace(stdout=raw.decode(encoding, kwargs.get("errors", "strict")))

    return run


@pytest.mark.unit
def test_git_output_is_decoded_as_utf8_whatever_the_locale(monkeypatch):
    script = _script()
    monkeypatch.setattr(subprocess, "run", _fake_run(LOG_TEXT.encode("utf-8")))
    assert script.git("show", "HEAD:docs/progress-log.md") == LOG_TEXT.strip()


@pytest.mark.unit
def test_git_without_stdout_gives_none_instead_of_raising(monkeypatch):
    script = _script()
    monkeypatch.setattr(subprocess, "run", _fake_run(None))
    assert script.git("show", "HEAD:docs/progress-log.md") is None
