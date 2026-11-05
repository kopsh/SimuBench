"""The command palette (``Ctrl+K``) and the settings search (``/``), tui-design.md 4.

Both are lists of *commands* built from what the TUI really has, never a second copy:

* the app's key bindings (the same ones the help lists, worded by ``help.actions``);
* one "open" command per instrument of the bench;
* one "set" command per writable state variable of every instrument, plus the direct form
  ``smu voltage 5`` (instrument, setting, value) typed into the same box.

A setting is never written from here: the command opens the instrument screen and edits the
field there, so the range check, the unit parsing and the confirmation of a dangerous switch are
the ones the field already has (tui-screens.md 5.3, 10.1).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from typing import Any

from textual.command import CommandPalette, DiscoveryHit, Hit, Hits, Provider

from . import controls as C
from . import i18n
from .screens.help import KEY_TEXT, action_name, bindings_of

#: Actions the palette leaves out: it is itself one, and "back" has nothing to go back from.
_SKIP = {"palette", "help"}


@dataclass(frozen=True)
class Command:
    """One palette line: what it does, a line about it, and what runs."""

    title: str
    help: str
    run: Callable[[], Any]
    match: str = ""  # what the search reads; the title when empty (the value is not searched)


def key_text(keys: str) -> str:
    return " / ".join(KEY_TEXT.get(k.strip(), k.strip()) for k in keys.split(","))


def key_commands(app: Any) -> list[Command]:
    """A command per app binding, worded as the help is; the key is shown beside it."""
    out: list[Command] = []
    seen: set[str] = set()
    for b in bindings_of(app):
        name = action_name(b.action)
        if name in seen or name in _SKIP:
            continue
        seen.add(name)
        what = i18n.t(f"help.actions.{name}", app.lang)
        out.append(Command(what, key_text(b.key), partial(app.run_action, b.action)))
    return out


def instrument_commands(app: Any) -> list[Command]:
    lang = app.lang
    out: list[Command] = []
    for info in (app.session_info or {}).get("instruments", []):
        name = info.get("name", "")
        out.append(
            Command(
                i18n.t("palette.open", lang, instrument=name),
                " · ".join(x for x in (info.get("model"), info.get("kind")) if x),
                partial(app.open_instrument, name),
            )
        )
    return out


def settable(info: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The state variables an instrument screen can edit: the generic template's rule."""
    for sv in info.get("state", []):
        if not sv.get("readonly") and sv.get("type") in ("bool", "enum", "float", "int"):
            yield sv


def value_text(sv: dict[str, Any], lang: str) -> str:
    return C.summary_text(sv.get("value"), sv, lang)


def setting_commands(app: Any) -> list[Command]:
    lang = app.lang
    out: list[Command] = []
    for info in (app.session_info or {}).get("instruments", []):
        name = info.get("name", "")
        for sv in settable(info):
            label = C.label_of(sv, lang)
            out.append(
                Command(
                    i18n.t("palette.set", lang, instrument=name, label=label),
                    i18n.t("palette.now", lang, path=sv["path"], value=value_text(sv, lang)),
                    partial(app.jump_to_field, name, sv["path"], None),
                    f"{name} {label} {sv['path']}",
                )
            )
    return out


def all_commands(app: Any) -> list[Command]:
    return [*key_commands(app), *instrument_commands(app), *setting_commands(app)]


def direct_sets(app: Any, query: str) -> list[tuple[str, Command]]:
    """``smu voltage 5`` → the commands "set smu · Voltage = 5" for the settings it can mean.

    Returns ``(text to fuzzy-match, command)``; the value is checked here only for being a number
    where the setting is one, so a nonsense value yields no line instead of a flash later."""
    lang = app.lang
    head, _, value = query.strip().rpartition(" ")
    if not head or not value:
        return []
    out: list[tuple[str, Command]] = []
    for info in (app.session_info or {}).get("instruments", []):
        name = info.get("name", "")
        for sv in settable(info):
            if sv.get("type") in ("float", "int"):
                try:
                    C.parse_input(value, sv)
                except ValueError as exc:
                    if str(exc) != "range":  # out of range still shows: the field says by how much
                        continue
            label = C.label_of(sv, lang)
            out.append(
                (
                    f"{name} {label} {sv['path']}",
                    Command(
                        i18n.t("palette.setTo", lang, instrument=name, label=label, value=value),
                        i18n.t("palette.now", lang, path=sv["path"], value=value_text(sv, lang)),
                        partial(app.jump_to_field, name, sv["path"], value),
                    ),
                )
            )
    return out


class CommandProvider(Provider):
    """The palette's one source: every command above, fuzzy-matched on title and help."""

    async def discover(self) -> Hits:
        for c in all_commands(self.app):
            yield DiscoveryHit(c.title, c.run, help=c.help)

    async def search(self, query: str) -> Hits:
        app: Any = self.app
        head, _, _value = query.strip().rpartition(" ")
        if head:
            matcher = self.matcher(head)
            for text, c in direct_sets(app, query):
                score = matcher.match(text)
                if score > 0:
                    yield Hit(score + 1, c.title, c.run, help=c.help)
        matcher = self.matcher(query)
        for c in all_commands(app):
            score = matcher.match(c.match or c.title)
            if score > 0:
                yield Hit(score, matcher.highlight(c.title), c.run, help=c.help)


class SettingsProvider(Provider):
    """``/`` on an instrument screen: that screen's own fields, by label and path."""

    async def discover(self) -> Hits:
        for c in self._commands():
            yield DiscoveryHit(c.title, c.run, help=c.help)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for c in self._commands():
            score = matcher.match(c.match or c.title)
            if score > 0:
                yield Hit(score, matcher.highlight(c.title), c.run, help=c.help)

    def _commands(self) -> list[Command]:
        screen: Any = self.screen
        fields = getattr(screen, "field_index", None)
        return [] if fields is None else fields()


def open_palette(app: Any) -> None:
    app.push_screen(
        CommandPalette(providers=[CommandProvider], placeholder=i18n.t("palette.placeholder", app.lang))
    )


def open_settings_search(app: Any) -> None:
    app.push_screen(
        CommandPalette(
            providers=[SettingsProvider], placeholder=i18n.t("palette.searchPlaceholder", app.lang)
        )
    )
