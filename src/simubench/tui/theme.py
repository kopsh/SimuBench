"""The TUI's semantic colors (tui-screens.md 1.2): Textual theme variables.

``NO_COLOR`` switches to the monochrome theme: every token is the default foreground, states
remain readable because each is also written as text.
"""

from __future__ import annotations

import os

from textual.theme import Theme

#: Token → purpose (tui-screens.md 1.2). Dark and light terminals use the same tokens; the values
#: are readable on both (the terminal's own palette softens them where needed).
COLORS: dict[str, str] = {
    "fg": "#e8e8e8",  # values, body text (values are bold)
    "muted": "#8a8a8a",  # labels, units, secondary information, read-away errors
    "focus": "#ffb000",  # focus border, highlight frames
    "bad": "#ff5555",  # error codes, trips, limited (▲), disconnected
    "ok": "#4fd68a",  # the running status dot (●)
    "sim": "#c39bff",  # simulation-only information (true values, ≈ fidelity)
    "ch1": "#f1fa8c",  # scope channels: yellow, cyan, magenta, blue
    "ch2": "#8be9fd",
    "ch3": "#ff79c6",
    "ch4": "#7aa2f7",
}

THEME = Theme(
    name="simubench",
    primary=COLORS["focus"],
    secondary=COLORS["sim"],
    variables={**COLORS, "success": COLORS["ok"], "error": COLORS["bad"]},
)

MONO_VARS = {k: "" for k in COLORS}


def mono_theme() -> Theme:
    """The ``NO_COLOR`` theme: semantic tokens carry no color of their own."""
    return Theme(
        name="simubench-mono",
        primary="#e8e8e8",
        variables={**MONO_VARS, "success": "", "error": ""},
    )


def apply_theme(app: object) -> None:
    """Register both themes and pick by ``NO_COLOR``."""
    app.register_theme(THEME)  # type: ignore[attr-defined]
    app.register_theme(mono_theme())  # type: ignore[attr-defined]
    app.theme = "simubench-mono" if os.environ.get("NO_COLOR") else "simubench"  # type: ignore[attr-defined]
