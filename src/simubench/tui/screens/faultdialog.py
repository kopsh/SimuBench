"""The fault dialog (tui-screens.md 8): inject by kind, revoke what is in effect.

Kind groups follow the Fault model (D27): communication (timeout / disconnect / garble /
latency / busy) at the exchange entry, instrument (error code / trip) through the error queue,
component (drift / hidden) as overrides. Opened with an instrument (from the overview's ``f``)
or with a net (from the nets screen, component faults on that net's components).
"""

from __future__ import annotations

import contextlib
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static

from .. import i18n
from .states import ConfirmScreen

#: Which Fault fields each kind shows (besides instrument/component and start/duration).
FIELDS: dict[str, tuple[str, ...]] = {
    "timeout": ("count",),
    "disconnect": ("count",),
    "garble": ("garble_rate",),
    "latency": ("latency_s",),
    "busy": ("count",),
    "error": ("code", "message"),
    "trip": ("code", "message"),
    "drift": ("component", "param", "drift_per_s"),
    "hidden": ("component", "param", "value"),
}

GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("communication", ("timeout", "disconnect", "garble", "latency", "busy")),
    ("instrument", ("error", "trip")),
    ("component", ("drift", "hidden")),
]

_INT_FIELDS = ("code", "count")
_FLOAT_FIELDS = ("garble_rate", "latency_s", "drift_per_s", "value")


def fields_for_kind(kind: str) -> tuple[str, ...]:
    """The fields the dialog shows for one kind — the unit tier's interface (tui-screens.md 8)."""
    return FIELDS.get(kind, ())


def build_spec(
    kind: str, instrument: str | None, values: dict[str, Any], components: list[str] | None = None
) -> dict[str, Any]:
    """A Fault payload from the dialog's inputs; empty inputs stay unset, per the model.

    ``components`` are the candidate component names (a net's parts) for the component kinds;
    the first one is preselected when the user left the field empty.
    """
    spec: dict[str, Any] = {"kind": kind}
    if instrument:
        spec["instrument"] = instrument
    for name in fields_for_kind(kind):
        raw = str(values.get(name, "") or "").strip()
        if not raw:
            continue
        if name in _INT_FIELDS:
            spec[name] = int(float(raw))
        elif name in _FLOAT_FIELDS:
            spec[name] = float(raw)
        else:
            spec[name] = raw
    at = str(values.get("at_s", "") or "").strip()
    if at:
        spec["at_s"] = float(at)
    duration = str(values.get("duration_s", "") or "").strip()
    if duration:
        spec["duration_s"] = float(duration)
    if kind in ("drift", "hidden") and not spec.get("component") and components:
        spec["component"] = components[0]
    return spec


class FaultDialog(ModalScreen[dict | None]):
    """A modal dialog the overview's ``f`` and the nets screen open."""

    DEFAULT_CSS = """
    FaultDialog {
        align: center middle;
        background: $background 60%;
        #fd-box { width: 64; max-width: 94%; height: auto; max-height: 90%;
                 border: thick $focus; padding: 1 2; background: $background; }
        .fd-title { color: $focus; margin-bottom: 1; }
        .fd-group { color: $muted; margin-top: 1; }
        RadioSet { margin-left: 2; }
        .fd-fields { height: auto; margin-top: 1; }
        .fd-fields Horizontal { height: auto; }
        .fd-fields Label { width: 14; color: $muted; }
        Input { width: 1fr; }
        .fd-effect { color: $muted; margin-top: 1; }
        .fd-buttons { height: auto; margin-top: 1; }
        .fd-buttons Button { margin-right: 2; min-width: 12; }
        #fd-active { display: none; }
        #fd-error { color: $bad; display: none; }
    }
    """

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", show=False),
        Binding("delete", "revoke", show=False),
    ]

    def __init__(self, instrument: str | None = None, net: str | None = None) -> None:
        super().__init__()
        self.instrument = instrument
        self.net = net
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="fd-box"):
            yield from self._compose_box()

    def _compose_box(self) -> ComposeResult:
        lang = self.app.lang
        subject = self.instrument or self.net or ""
        yield Static(i18n.t("fault.title", lang) + (f" · {subject}" if subject else ""), classes="fd-title")
        yield Static("", id="fd-active")  # this instrument's faults in effect, at the top (8)

        for group, kinds in GROUPS:
            yield Static(i18n.t(f"fault.group_{group}", lang), classes="fd-group")
            yield RadioSet(
                *[RadioButton(i18n.t(f"fault.kinds.{k}", lang), name=k) for k in kinds],
                id=f"fd-group-{group}",
            )
        yield Static("", id="fd-fields", classes="fd-fields")
        yield Static("", classes="fd-effect")
        yield Static("", id="fd-error")
        with Horizontal(classes="fd-buttons"):
            yield Button(i18n.t("fault.inject", lang), variant="primary", id="fd-inject")
            yield Button(i18n.t("common.cancel", lang), id="fd-cancel")

    def on_mount(self) -> None:
        self._refresh_fields()
        self._refresh_active()
        self.query_one("#fd-group-communication", RadioSet).focus()

    # ---- fields per kind ------------------------------------------------------------------------------------

    def _kind(self) -> str:
        for group, _kinds in GROUPS:
            radios = self.query_one(f"#fd-group-{group}", RadioSet)
            pressed = radios.pressed_button
            if pressed is not None:
                return pressed.name or "timeout"
        return "timeout"

    def _refresh_fields(self) -> None:
        lang = self.app.lang
        box = self.query_one("#fd-fields", Static)
        box.remove_children()
        for name in fields_for_kind(self._kind()):
            box.mount(Horizontal(Label(i18n.t(f"fault.{name}", lang)), Input(id=f"fd-f-{name}")))
        box.mount(Horizontal(Label(i18n.t("fault.at", lang)), Input(id="fd-f-at_s")))
        box.mount(Horizontal(Label(i18n.t("fault.duration", lang)), Input(id="fd-f-duration_s")))
        effect = i18n.t(f"fault.kindHints.{self._kind()}", lang)
        self.query_one(".fd-effect", Static).update(effect + "\n" + i18n.t("fault.durationHint", lang))

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self._refresh_fields()

    # ---- inject / revoke --------------------------------------------------------------------------------------

    def _collect(self) -> dict[str, Any]:
        return {(inp.id or "").removeprefix("fd-f-"): inp.value for inp in self.query(Input)}

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "fd-cancel":
            self.action_cancel()
        elif event.button.id == "fd-inject":
            self._inject()

    def _inject(self) -> None:
        if not self._busy:
            self.run_worker(self._do_inject(), exclusive=True)

    async def _do_inject(self) -> None:
        app: Any = self.app
        self._busy = True
        error = self.query_one("#fd-error", Static)
        try:
            info = await app.client.inject_fault(
                app.sid, build_spec(self._kind(), self.instrument, self._collect())
            )
        except Exception as exc:
            error.update(str(exc))
            error.display = True
            return
        finally:
            self._busy = False
        app.faults = [*list(app.faults or []), info]
        self.dismiss(info)

    def _refresh_active(self) -> None:
        app: Any = self.app
        mine = [f for f in app.faults or [] if f.get("status") in ("active", "pending")]
        box = self.query_one("#fd-active", Static)
        if not mine:
            box.display = False
            return
        lang = app.lang
        lines = [i18n.t("fault.activeCount", lang, count=len(mine))]
        lines += [
            f"  {f['id']}  {f.get('description') or ''}  {i18n.t('fault.' + f.get('status', 'active'), lang)}"
            for f in mine
        ]
        box.update("\n".join(lines))
        box.display = True

    def action_revoke(self) -> None:
        app: Any = self.app
        mine = [f for f in app.faults or [] if f.get("status") in ("active", "pending")]
        if not mine:
            return

        def on_done(ok: bool) -> None:
            if ok:
                self.run_worker(self._do_revoke(mine[-1]["id"]), exclusive=True)

        app.push_screen(
            ConfirmScreen(
                i18n.t("fault.clear", app.lang),
                [i18n.t("fault.clearBody", app.lang, fault_id=mine[-1]["id"])],
                i18n.t("common.confirm", app.lang),
                i18n.t("common.cancel", app.lang),
            ),
            on_done,
        )

    async def _do_revoke(self, fault_id: str) -> None:
        app: Any = self.app
        with contextlib.suppress(Exception):
            await app.client.revoke_fault(app.sid, fault_id)
        app.faults = [f for f in app.faults or [] if f["id"] != fault_id]
        self._refresh_active()

    def action_cancel(self) -> None:
        self.dismiss(None)
