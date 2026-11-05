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
    "fg": "#d3e0ea",  # values, body text (values are bold)
    "muted": "#5f7889",  # labels, units, secondary information, read-away errors
    "focus": "#ffb000",  # focus border, highlight frames, the title brackets (HUD amber)
    "bad": "#ff4d57",  # error codes, trips, limited (▲), disconnected
    "ok": "#39e58c",  # the running status dot (●)
    "sim": "#b99aff",  # simulation-only information (true values, ≈ fidelity)
    "ch1": "#ffd23f",  # scope channels: yellow, cyan, magenta, blue
    "ch2": "#35e0ff",
    "ch3": "#ff5bd1",
    "ch4": "#5b8cff",
    "hud": "#35e0ff",  # the HUD's cyan: box titles, the screen name in the top bar, keys
    "line": "#2f6a85",  # box lines
    "bar": "#0b141c",  # the top and bottom bars' background
}

THEME = Theme(
    name="simubench",
    primary=COLORS["focus"],
    secondary=COLORS["sim"],
    background="#05080c",
    surface="#0a1118",
    foreground=COLORS["fg"],
    dark=True,
    variables={**COLORS, "success": COLORS["ok"], "error": COLORS["bad"]},
)

#: `ansi_default` is the terminal's own foreground: no colour of ours, and still a valid
#: Textual colour (an empty string fails the stylesheet parse, taking the whole TUI with it).
MONO_VARS = {k: "ansi_default" for k in COLORS}


def mono_theme() -> Theme:
    """The ``NO_COLOR`` theme: semantic tokens carry no color of their own."""
    return Theme(
        name="simubench-mono",
        primary="#e8e8e8",
        variables={**MONO_VARS, "success": "ansi_default", "error": "ansi_default"},
    )


def apply_theme(app: object) -> None:
    """Register both themes and pick by ``NO_COLOR``."""
    app.register_theme(THEME)  # type: ignore[attr-defined]
    app.register_theme(mono_theme())  # type: ignore[attr-defined]
    mono = bool(os.environ.get("NO_COLOR"))
    app.theme = "simubench-mono" if mono else "simubench"  # type: ignore[attr-defined]
    app.set_class(mono, "-mono")  # type: ignore[attr-defined]  # CSS that must differ without colour
