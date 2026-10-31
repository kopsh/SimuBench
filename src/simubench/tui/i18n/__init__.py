"""TUI text, keyed like the web i18n (nested dicts); ``t("a.b", **kw)`` formats.

The language is ``--lang`` when given, else follows ``LANG`` (zh when unset or not en, D23).
"""

from __future__ import annotations

import os
from typing import Any

from . import en, zh

LANGS = {"zh": zh.TERMS, "en": en.TERMS}


def default_lang() -> str:
    """``--lang`` wins; the environment decides otherwise (zh is the fallback, D23)."""
    lang = (os.environ.get("SIMUBENCH_LANG") or os.environ.get("LANG") or "zh").split(".")[0]
    return "en" if lang.startswith("en") else "zh"


def table(lang: str) -> dict:
    return LANGS.get(lang) or zh.TERMS


def t(key: str, lang: str = "zh", **kwargs: Any) -> str:
    """``t("log.count", count=3)`` → the formatted text; a missing key returns the key itself."""
    node: Any = table(lang)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return key
        node = node[part]
    if isinstance(node, str) and kwargs:
        return node.format(**kwargs)
    return node if isinstance(node, str) else key


def on_off(value: Any, lang: str = "zh") -> str:
    """Booleans read 开 / On (tui-screens.md 1.4)."""
    if isinstance(value, str):
        return value
    return t("common.on" if value else "common.off", lang)
