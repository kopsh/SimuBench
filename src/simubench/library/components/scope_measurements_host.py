"""What the ScopeMeasurements collaborators ask of the component: the one interface they read it by."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ...engine.component import (
    Component,
    OpResult,
)

if TYPE_CHECKING:
    from ...engine.instrument import Instrument
    from .scope_cursors import Cursors
    from .scope_edges import Edges
    from .scope_kmarkers import KMarkers
    from .scope_readouts import Readouts
    from .scope_results import ResultsList
    from .scope_values import Values


class MeasurementsHost(Protocol):
    MODES: Any
    XUNITS: Any
    YUNITS: Any

    def acq(self) -> Any: ...
    def bound_path(self, key: str) -> str | None: ...
    def channel_of(self, channel: int | None, source: Any = None) -> int: ...
    def follow(self, n: int, source: Any) -> None: ...
    @staticmethod
    def key(word: Any) -> int: ...
    def number(self, value: float) -> bytes: ...

    owner: Instrument | None

    def param(self, key: str) -> Any: ...
    def peer(self, name: str, *, required: bool = False) -> Component | None: ...
    def ref(self, key: str) -> Component | None: ...
    def reply(self, value: float, used: int) -> OpResult: ...
    @staticmethod
    def source_word(n: int) -> str: ...
    def sources(self, raw: list[bytes] | None) -> tuple[list[str], list[int]]: ...
    @property
    def uid(self) -> str: ...

    results: ResultsList
    values: Values
    edges: Edges
    cursors: Cursors
    readouts: Readouts
    kmarkers: KMarkers
