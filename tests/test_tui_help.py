"""The key help (``?``) and the key moves of the TUI redesign: ``?`` is the help everywhere,
``w`` is "why", the net waveform is ``v``. The help reads the real bindings, so a key added
without wording fails here instead of vanishing from the help."""

from __future__ import annotations

import pytest

from simubench.tui import i18n
from simubench.tui.app import SimuBenchApp
from simubench.tui.screens.help import action_name, bindings_of
from simubench.tui.screens.instrument import InstrumentScreen
from simubench.tui.screens.logscreen import LogScreen
from simubench.tui.screens.nets import NetsScreen
from simubench.tui.screens.overview import OverviewScreen
from simubench.tui.screens.runs import RunsScreen
from simubench.tui.screens.scope import ScopeScreen

SCREENS = [OverviewScreen, InstrumentScreen, ScopeScreen, NetsScreen, LogScreen, RunsScreen]


@pytest.mark.unit
@pytest.mark.parametrize("owner", [*SCREENS, SimuBenchApp], ids=lambda c: c.__name__)
def test_every_binding_has_help_wording(owner):
    for b in bindings_of(owner):
        name = action_name(b.action)
        assert i18n.t(f"help.actions.{name}") != f"help.actions.{name}", (owner.__name__, b.key, b.action)


@pytest.mark.unit
def test_action_names_drop_their_arguments():
    assert action_name("cursor('up')") == "cursor"
    assert action_name("step(-1)") == "step"
    assert action_name("open('log')") == "open_log"
    assert action_name("quit_app") == "quit_app"


@pytest.mark.unit
@pytest.mark.parametrize("owner", [InstrumentScreen, ScopeScreen, NetsScreen], ids=lambda c: c.__name__)
def test_w_is_why_and_question_mark_is_not(owner):
    keys = {b.key: b.action for b in bindings_of(owner)}
    assert keys["w"] == "reason"
    assert "question_mark" not in keys


@pytest.mark.unit
def test_the_net_waveform_moved_to_v():
    keys = {b.key: b.action for b in bindings_of(NetsScreen)}
    assert keys["v"] == "wave"


@pytest.mark.unit
def test_the_app_binds_the_help():
    assert {b.key: b.action for b in bindings_of(SimuBenchApp)}["question_mark"] == "help"


# ---- the HUD bars (tui-screens.md 1.1, 1.2) --------------------------------------------------------


class _Stub:
    """Just what ``style_of`` and ``is_mono`` read from the app."""

    def __init__(self, theme: str, variables: dict[str, str]) -> None:
        self.theme = theme
        self._vars = variables

    def get_css_variables(self) -> dict[str, str]:
        return self._vars


@pytest.mark.unit
def test_the_bars_are_drawn_on_the_bar_colour():
    from simubench.tui.theme import COLORS
    from simubench.tui.widgets import bar_text

    text = bar_text(
        _Stub("simubench", dict(COLORS)), [(" Esc", "bold hud"), (" back", "muted")], [("x", "")], 30
    )
    assert text.cell_len == 30
    assert all(f"on {COLORS['bar']}" in str(span.style) for span in text.spans)


@pytest.mark.unit
def test_under_no_color_the_bars_are_reverse_video_and_nothing_else():
    from simubench.tui.theme import MONO_VARS
    from simubench.tui.widgets import bar_text

    text = bar_text(
        _Stub("simubench-mono", dict(MONO_VARS)), [(" Esc", "bold hud"), (" back", "muted")], [("x", "")], 30
    )
    assert text.cell_len == 30
    assert {str(span.style) for span in text.spans} <= {"reverse", "reverse bold"}


@pytest.mark.unit
def test_every_colour_token_has_a_mono_counterpart():
    from simubench.tui.theme import COLORS, MONO_VARS

    assert set(MONO_VARS) == set(COLORS)


@pytest.mark.unit
def test_the_bottom_bar_gives_up_the_least_essential_keys_first():
    from simubench.tui.widgets import fit_keys

    keys = [("Esc", "back"), ("Tab", "move"), ("⏎", "edit"), ("␣", "switch"), ("[ ]", "next"),
            (":", "SCPI"), ("w", "reason"), ("?", "help"), ("q", "Quit")]  # fmt: skip
    assert fit_keys(keys, 200) == keys  # room enough: nothing goes
    tight = [k for k, _ in fit_keys(keys, 60)]
    assert "[ ]" not in tight and ":" not in tight  # the first to go
    assert {"Esc", "w", "?", "q"} <= set(tight)  # these stay
    assert [k for k, _ in fit_keys(keys, 1)][-3:] == ["w", "?", "q"]  # even when nothing fits
