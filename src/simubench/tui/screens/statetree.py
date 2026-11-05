"""The state tree (``t`` on an instrument screen, tui-screens.md 5.6): every state variable of
the instrument by path group, each editable where it stands, and — with ``h`` — the hidden values.

The rows are :func:`simubench.tui.tree.tree_rows`; the editing is the instrument screen's own
layer (:class:`ControlEditingScreen`), so range checks, units, the confirmation of a dangerous
switch and the read-only state of a remote-controlled instrument are not written a second time.
The tree is rebuilt only when its *shape* changes (the variables, the folded groups, ``h``);
a new data version only repaints the values, so focus, a typed number and the folds survive it.
"""

from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from .. import controls as C
from .. import i18n
from .. import tree as T
from .editing import ControlButton, ControlEditingScreen


class GroupRow(ControlButton):
    """A group's header: focusable like a field; Enter or Space folds and unfolds it."""

    def __init__(self, group: str, opened: bool, count: int) -> None:
        super().__init__(
            C.Control(kind="softkey", label=f"{'▾' if opened else '▸'} {group}  {count}"), "tree"
        )
        self.group = group
        self._text = Text(self.control.label)

    def field_text(self, info: dict[str, Any], lang: str) -> Text:
        return Text(self.control.label)  # a heading, not a ``[button]``


class StateTreeScreen(ControlEditingScreen):
    """状态树: all state variables, grouped; ``h`` adds the hidden values."""

    screen_key = "states"
    AUTO_FOCUS = "#simubench-entry-no-focus"

    BINDINGS: ClassVar = [
        Binding("tab", "focus_next", show=False),
        Binding("shift+tab", "focus_previous", show=False),
        Binding("up", "cursor('up')", show=False),
        Binding("down", "cursor('down')", show=False),
        Binding("enter", "edit", show=False),
        Binding("e", "edit", show=False),
        Binding("space", "toggle", show=False),
        Binding("plus", "step(1)", show=False),
        Binding("minus", "step(-1)", show=False),
        Binding("slash", "search", show=False),
        Binding("h", "hidden", show=False),
    ]

    DEFAULT_CSS = """
    #tree-scroll { height: 1fr; padding: 0 1; scrollbar-size-vertical: 0; }
    #tree-flash { color: $bad; height: auto; padding: 0 1; }
    .tree-var { height: 1; padding: 0 0 0 2; text-wrap: nowrap; text-overflow: ellipsis; }
    .tree-hidden { height: 1; padding: 0 0 0 2; color: $sim; text-wrap: nowrap; text-overflow: ellipsis; }
    .tree-note { height: 1; padding: 0 0 0 2; color: $muted; text-wrap: nowrap; text-overflow: ellipsis; }
    GroupRow { color: $hud; text-style: bold; }
    """

    def __init__(self, name: str) -> None:
        super().__init__()
        self.instrument_name = name
        self._shape: tuple[Any, ...] | None = None
        self._last_version = -1
        self._statics: dict[str, Static] = {}
        self._pending_focus: tuple[str, str] | None = None  # ("path" | "group", key)

    # ---- layout ----------------------------------------------------------------------------------

    def screen_title(self) -> str:
        return i18n.t("tree.title", self.app.lang, instrument=self.instrument_name)  # type: ignore[attr-defined]

    def body(self) -> ComposeResult:
        self._scroll = VerticalScroll(id="tree-scroll")
        self._scroll.can_focus = False
        yield self._scroll
        self._flash_static = Static(id="tree-flash")
        self._flash_static.display = False
        self._flash_static.can_focus = False
        yield self._flash_static

    def keys(self) -> list[tuple[str, str]]:
        lang = self.app.lang  # type: ignore[attr-defined]
        return [
            ("Esc", i18n.t("keys.back", lang)),
            ("↑↓", i18n.t("keys.move", lang)),
            ("⏎", i18n.t("keys.edit", lang)),
            ("/", i18n.t("keys.search", lang)),
            ("h", i18n.t("keys.hidden", lang)),
            ("?", i18n.t("keys.help", lang)),
            ("q", i18n.t("common.quit", lang)),
        ]

    def _flash_target(self, button: ControlButton | None) -> Static | None:
        return self._flash_static

    # ---- state kept on the app, so a leave and return finds the tree as it was ---------------------

    def _open_groups(self, groups: dict[str, list[Any]]) -> set[str]:
        app: Any = self.app
        stored = app.tree_open.get(self.instrument_name)
        if stored is None:
            budget = max(4, (self._scroll.size.height or app.size.height - 4))
            stored = T.default_open(groups, budget)
            app.tree_open[self.instrument_name] = stored
        return stored  # type: ignore[no-any-return]

    def _toggle_group(self, group: str) -> None:
        opened = self._open_groups(T.groups_of(self.info))
        opened.symmetric_difference_update({group})
        self._pending_focus = ("group", group)
        self._rebuild(force=True)

    def action_hidden(self) -> None:
        """``h``: show or hide the hidden values (individual deviations, hidden-fault overrides)."""
        app: Any = self.app
        app.tree_hidden = not app.tree_hidden
        self._rebuild(force=True)

    def action_edit(self) -> None:
        button = self._focused_button()
        if isinstance(button, GroupRow):  # folding is not a write: allowed while remote-controlled
            self._toggle_group(button.group)
            return
        super().action_edit()

    def action_toggle(self) -> None:
        button = self._focused_button()
        if isinstance(button, GroupRow):
            self._toggle_group(button.group)
            return
        super().action_toggle()

    # ---- drawing ---------------------------------------------------------------------------------

    def refresh_content(self) -> None:
        super().refresh_content()
        app: Any = self.app
        if not self.info or not self.is_mounted:
            return
        if app.data_version == self._last_version and self._shape is not None:
            return
        self._last_version = app.data_version
        self._rebuild()

    def _hidden_rows(self) -> list[T.Row] | None:
        app: Any = self.app
        if not app.tree_hidden:
            return None
        revealed = bool((app.session_info or {}).get("reveal_true_values"))
        return T.hidden_rows(self.instrument_name, self.info, app.faults, revealed, app.lang)

    def _rebuild(self, force: bool = False) -> None:
        app: Any = self.app
        lang = app.lang
        groups = T.groups_of(self.info)
        opened = self._open_groups(groups)
        hidden = self._hidden_rows()
        rows = T.tree_rows(groups, opened, lang, hidden)
        shape = (
            tuple((r.kind, r.group, r.path, r.opened, r.text) for r in rows),
            self.remote_controlled,
        )
        if shape == self._shape and not force:
            self._repaint_values(rows)
            return
        self._shape = shape
        focused = self._focused_button()
        if self._pending_focus is None and focused is not None:
            if isinstance(focused, GroupRow):
                self._pending_focus = ("group", focused.group)
            elif focused.control.path:
                self._pending_focus = ("path", focused.control.path)
        scroll = self._scroll
        scroll.remove_children()
        self._statics = {}
        widgets: list[Any] = []
        info = self.info
        for r in rows:
            if r.kind == "group":
                widgets.append(GroupRow(r.group, r.opened, r.count))
            elif r.kind == "var":
                assert r.spec is not None and r.path is not None
                if T.writable(r.spec):
                    button = ControlButton(C._control_for(r.path, r.spec, lang), "tree")
                    button.render_button(info, self.remote_controlled, lang)
                    button.add_class("tree-var")
                    widgets.append(button)
                else:
                    line = Static(self._var_text(r), classes="tree-var")
                    self._statics[r.path] = line
                    widgets.append(line)
            else:
                widgets.append(
                    Static(Text(r.text), classes="tree-hidden" if r.kind == "hidden" else "tree-note")
                )
        scroll.mount(*widgets)
        self.call_after_refresh(self._restore_focus)
        self.call_after_refresh(self.take_pending_field)

    def _var_text(self, r: T.Row) -> Text:
        """A variable that cannot be edited here: ``label value unit  RO  path``."""
        app: Any = self.app
        spec = r.spec or {}
        value = C.summary_text(C.value_of(self.info, r.path), spec, app.lang)
        return Text.assemble(
            (r.label, "dim"),
            " ",
            (value, "bold"),
            "  ",
            (i18n.t("tree.readOnly", app.lang), "dim"),
            "  ",
            (r.path or "", "dim"),
        )

    def _repaint_values(self, rows: list[T.Row]) -> None:
        """Same shape, new numbers: repaint in place, leaving focus and any edit alone."""
        info, lang = self.info, self.app.lang  # type: ignore[attr-defined]
        for button in self.query(ControlButton):
            if button.edit is None and button.control.path:
                button.render_button(info, self.remote_controlled, lang)
        by_path = {r.path: r for r in rows if r.kind == "var" and r.path}
        for path, static in self._statics.items():
            if path in by_path:
                static.update(self._var_text(by_path[path]))

    def _restore_focus(self) -> None:
        pending, self._pending_focus = self._pending_focus, None
        if pending is None:
            return
        kind, key = pending
        for button in self.query(ControlButton):
            hit = (kind == "group" and isinstance(button, GroupRow) and button.group == key) or (
                kind == "path" and button.control.path == key
            )
            if hit:
                button.focus()
                button.scroll_visible(animate=False)
                return

    def _refresh_buttons(self) -> None:
        self._repaint_values([])
