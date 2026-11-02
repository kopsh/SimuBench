"""TUI text, keyed like the web i18n (nested dicts); ``t("a.b", **kw)`` formats.

The terminal console is English only (D23): the ``lang`` arguments that remain on the helpers
take any value and always read the English table.
"""

from __future__ import annotations

from typing import Any

from . import en

LANGS = {"en": en.TERMS}


def default_lang() -> str:
    """The TUI's one language (D23)."""
    return "en"


def table(lang: str = "en") -> dict:
    return en.TERMS


def t(key: str, lang: str = "en", **kwargs: Any) -> str:
    """``t("log.count", count=3)`` → the formatted text; a missing key returns the key itself."""
    node: Any = table(lang)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return key
        node = node[part]
    if isinstance(node, str) and kwargs:
        return node.format(**kwargs)
    return node if isinstance(node, str) else key


def on_off(value: Any, lang: str = "en") -> str:
    """Booleans read On / Off (tui-screens.md 1.4)."""
    if isinstance(value, str):
        return value
    return t("common.on" if value else "common.off", lang)
