"""What the ScopeMeasurements collaborators ask of the component: the one interface they read it by."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from ...engine.component import (
    Component,
    OpResult,
)

if TYPE_CHECKING:
    from ...models.definition import StateSpec
    from .scope_cursors import Cursors
    from .scope_edges import Edges
    from .scope_kmarkers import KMarkers
    from .scope_readouts import Readouts
    from .scope_results import ResultsList
    from .scope_values import Values


class MeasurementsHost(Protocol):
    """What the measurement family's blocks (results, values, edges, cursors, readouts, kmarkers)
    read of their component. The instrument's own state is a member here, not an ``owner`` to
    reach through (#1724): a block reads a setting by :meth:`get_state` (or the live
    :attr:`state` mapping, where a default for an undeclared path is wanted), writes one by
    :meth:`set_state`, formats a reply number by :meth:`format_number` and asks what settings
    the definition declares through :attr:`specs`."""

    MODES: Any
    XUNITS: Any
    YUNITS: Any

    def acq(self) -> Any: ...
    def bound_path(self, key: str) -> str | None: ...
    def channel_of(self, channel: int | None, source: Any = None) -> int: ...
    def follow(self, n: int, source: Any) -> None: ...
    def format_number(self, value: float) -> bytes: ...
    def get_state(self, path: str) -> Any: ...
    @staticmethod
    def key(word: Any) -> int: ...
    def number(self, value: float) -> bytes: ...

    def param(self, key: str) -> Any: ...
    def peer(self, name: str, *, required: bool = False) -> Component | None: ...
    def ref(self, key: str) -> Component | None: ...
    def reply(self, value: float, used: int) -> OpResult: ...
    def set_state(self, path: str, value: Any, *, source: str = "scpi", internal: bool = False) -> None: ...
    @staticmethod
    def source_word(n: int) -> str: ...
    def sources(self, raw: list[bytes] | None) -> tuple[list[str], list[int]]: ...
    @property
    def specs(self) -> Mapping[str, StateSpec]: ...
    @property
    def state(self) -> Mapping[str, Any]: ...
    @property
    def uid(self) -> str: ...

    results: ResultsList
    values: Values
    edges: Edges
    cursors: Cursors
    readouts: Readouts
    kmarkers: KMarkers
