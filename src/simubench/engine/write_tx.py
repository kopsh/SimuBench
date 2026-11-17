"""One batch of writes, applied all-or-nothing (#1379): the door every write from outside goes through.

A write transaction is an intent -- settings and values, the write's source, and the batch's
flags -- carried out in one pass:

1. every write is validated against its setting's declaration (a read-only setting accepts the
   instrument's own ``internal`` writes only), before anything is applied;
2. the definition's constraints judge the state the whole batch leaves (D69, #1013) and may
   coerce further settings; a refusing constraint takes the batch down before anything is
   written -- judged batches only;
3. the values are applied and the components watching each written path are told
   (:meth:`Component.on_state`), in the batch's order; a watcher may adjust the value to keep
   (it returns it) or refuse the write;
4. the dynamic limits hold for the state the batch leaves: the declared bounds, narrowed by
   :meth:`Component.state_limits` (#437) -- judged batches only;
5. the components watching the written paths judge the combination
   (:meth:`Component.check_settings`, #580) -- judged batches only;
6. a ``reinforce`` batch writes its values again after the watchers ran (#436): a recalled
   setup is consistent as it stands, so what a watcher writes as a side effect does not survive.

Nothing becomes visible until the batch survives. A refused write takes back every value the
batch and its watchers wrote -- without sending events for the way back -- and the surviving
writes' ``state_changed`` go out in the order they were applied. Whether a batch is judged is
the source's declaration (D69): writes from outside the instrument -- SCPI settings, the panel
and MCP (``configure``), a recalled setup -- are judged; the instrument's own writes (a
component's, an operation's side effect), a replay and a fault are not.

The transaction needs five things of its instrument (:class:`WriteHost`) -- the specs, the
state, the watchers of a path, the notification and the dynamic limits -- plus the definition's
constraints for judged batches. #1380's host gives the same narrow interface.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Callable, NamedTuple, Protocol

from ..scpi.errors import ScpiError
from .constraints import settle
from .settings import validate_value

if TYPE_CHECKING:
    from ..models.definition import ConstraintSpec, StateSpec
    from .component import Component


class WriteEvent(NamedTuple):
    """One surviving write, as ``state_changed`` will send it."""

    path: str
    old: Any
    new: Any
    electrical: bool
    source: str


class WriteHost(Protocol):
    """What a write transaction needs of its instrument: the specs, the state, the watchers, the
    notification and the dynamic limits (specs/state/watchers/notify/limits), plus the
    definition's constraints and the batch's deferred events. #1380's host gives the same."""

    specs: Mapping[str, StateSpec]
    state: dict[str, Any]
    constraints: Iterable[ConstraintSpec]
    #: The events of the transaction in progress, or None outside one: the transaction collects
    #: its writes' events here and sends them only when the batch survives, so a refused batch
    #: leaves no visible change -- including nothing for the way back (#1379).
    write_events: list[WriteEvent] | None

    def watchers(self, path: str) -> Iterable[Component]:
        """The components told when the setting at ``path`` changes."""

    def watch_param(self, path: str, uid: str) -> str | None:
        """The parameter a change arrives through (:meth:`Component.on_state`'s key; None when it
        reaches the component through ``watches`` or a nested mapping)."""

    def limits(self, path: str) -> tuple[float | None, float | None]:
        """The setting's (min, max) now: the declared ones, narrowed by its watchers."""

    def notify_write(self, path: str, old: Any, new: Any, *, electrical: bool, source: str) -> None:
        """One surviving write leaves the instrument: the ``state_changed`` event."""


def apply_writes(
    host: WriteHost,
    writes: Iterable[tuple[str, Any]] | Mapping[str, Any],
    *,
    source: str,
    judged: bool = False,
    internal: bool = False,
    reinforce: bool = False,
    batch_event: str | None = None,
    name_error: Callable[[ScpiError, str, StateSpec | None], ScpiError] | None = None,
) -> None:
    """Apply one batch of writes all-or-nothing (#1379); see the module docstring for the pass.

    ``source`` names where the batch comes from (the events carry it). ``judged``: the batch is
    an external write -- the constraints, the dynamic limits and the components' combination
    checks hold for it. ``internal``: the instrument itself writes read-only settings (calibration
    coefficients, a preset's variables). ``reinforce``: the values are written again after the
    watchers ran (#436); the watchers see the whole batch's values (a setup is put back, not
    stepped through), and the batch announces itself as one ``state_changed`` at ``batch_event``
    (``*RCL``) instead of one event per write. ``name_error`` names a refused value's setting in
    the error (the SCPI layer's ``_bad_parameter``), where the caller would otherwise wrap it.
    """
    staged = dict(writes)
    specs = host.specs
    # 1. every write is checked against its setting's declaration, with the setting named in the
    # error, before any of them is applied (#550); a read-only setting takes the instrument's
    # own writes only.
    checked: dict[str, Any] = {}
    for path, value in staged.items():
        spec = specs.get(path)
        if spec is None:
            raise ScpiError(-113, f"no state {path!r}")
        try:
            checked[path] = validate_value(spec, value, allow_readonly=internal)
        except ScpiError as e:
            raise name_error(e, path, spec) if name_error else e from e
    # A write that would change nothing changes nothing: not applied, not judged, not sent --
    # like the single write that returns before its watchers (the constraints judge what a
    # command changes, not the state as it stands).
    pending = {p: v for p, v in checked.items() if host.state.get(p) != v}
    if not pending and batch_event is None:
        return
    events: list[WriteEvent] = []
    outer = host.write_events
    host.write_events = events
    try:
        # 2. the constraints judge the state the batch leaves, together (#1013): a command that
        # sets both settings is not refused for the state between them. Nothing is written yet.
        if judged:
            pending = {**pending, **settle(host.constraints, specs, host.state, pending)}
        snapshot = dict(host.state)
        try:
            if pending:
                if reinforce:
                    _apply_reinforced(host, pending)
                else:
                    _apply_watchers(host, pending, events, internal, source)
                if judged:
                    _check_limits(host, pending, name_error)
                    # The settings together (#580): the components watching any written path look
                    # at the whole batch's end, where each write alone was checked in on_state.
                    for comp in {c.uid: c for p in pending for c in host.watchers(p)}.values():
                        comp.check_settings()
        except ScpiError:
            # All-or-nothing: every value the batch and its watchers wrote goes back, without
            # events for the way back -- the refused batch leaves no visible change (#1379).
            host.state.update(snapshot)
            raise
    finally:
        host.write_events = outer
    if outer is not None:
        # A transaction inside one (a watcher's own write): its events join the batch's, sent
        # only if the batch survives.
        outer.extend(events)
        return
    for event in events:
        host.notify_write(
            event.path, event.old, event.new, electrical=event.electrical, source=event.source
        )
    if batch_event is not None:
        host.notify_write(batch_event, None, None, electrical=True, source=source)


def _apply_watchers(
    host: WriteHost,
    pending: Mapping[str, Any],
    events: list[WriteEvent],
    internal: bool,
    source: str,
) -> None:
    """Write each value and tell its watchers, in the batch's order; a watcher's own write is a
    transaction of one, nested in this batch (its events land in the same collector)."""
    specs = host.specs
    state = host.state
    for path, value in pending.items():
        spec = specs[path]
        old = state.get(path)
        if old == value:
            continue  # an earlier write's side effect already made it so
        state[path] = value
        for comp in host.watchers(path):
            # A component may take a value outside its legal range as the nearest legal one (it
            # returns that value) instead of refusing it.
            adjusted = comp.on_state(host.watch_param(path, comp.uid), path, old, value)
            if adjusted is not None and adjusted != value:
                value = validate_value(spec, adjusted, allow_readonly=internal)
                state[path] = value
        if old != value:
            events.append(WriteEvent(path, old, value, spec.electrical, source))


def _apply_reinforced(host: WriteHost, pending: Mapping[str, Any]) -> None:
    """Put a whole setup back (#436): all values in at once, then the watchers of the changed
    paths see the setup as it stands, then the values again -- so what a watcher writes as a
    side effect of one change does not survive."""
    state = host.state
    before = {p: state.get(p) for p in pending}
    state.update(pending)
    for path, value in pending.items():
        for comp in host.watchers(path):
            comp.on_state(host.watch_param(path, comp.uid), path, before[path], value)
    state.update(pending)


def _check_limits(
    host: WriteHost,
    pending: Mapping[str, Any],
    name_error: Callable[[ScpiError, str, StateSpec | None], ScpiError] | None,
) -> None:
    """The dynamic limits hold for the values the batch leaves (#437): the declared bounds,
    narrowed by the watchers. SCPI holds its numbers to them at parse time (MIN/MAX, #663, #721);
    writes that bypass the parser -- the panel, MCP, a recalled setup -- are held here, judged on
    the batch's end state like the constraints. A component's adjustment has run, so the value
    the check sees is the one the instrument keeps."""
    specs = host.specs
    state = host.state
    for path in pending:
        spec = specs[path]
        if spec.type not in ("float", "int"):
            continue
        v = state.get(path)
        if (
            not isinstance(v, int | float)
            or isinstance(v, bool)
            or not math.isfinite(v)
            or v in set((spec.special or {}).values())
        ):
            continue
        lo, hi = host.limits(path)
        try:
            if lo is not None and v < lo - abs(lo) * 1e-12 - 1e-300:
                raise ScpiError(-222)
            if hi is not None and v > hi + abs(hi) * 1e-12 + 1e-300:
                raise ScpiError(-222)
        except ScpiError as e:
            raise name_error(e, path, spec) if name_error else e from e
