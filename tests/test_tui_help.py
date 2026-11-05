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
