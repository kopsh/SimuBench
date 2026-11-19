"""What the ScopeAcquisition collaborators ask of the component: the one interface they read it by."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from ...engine.signal import Signal
from ...engine.stream import (
    FrameRequest,
)

if TYPE_CHECKING:
    pass

if TYPE_CHECKING:
    from ...engine.instrument import Instrument
    from ...engine.session import Session
    from ...engine.stream import FrameRequest, SampleFrame
    from .scope_capture import Capture
    from .scope_recorder import Recorder
    from .scope_run import RunControl
    from .scope_segments import Segments


class AcquisitionHost(Protocol):
    def _averages(self) -> int: ...
    def _channels(self) -> int: ...
    def _display_signal(self, n: int) -> Signal: ...
    def _is(self, key: str, value: Any = None) -> bool: ...

    _params: Any

    def _st(self, path: str) -> Any: ...
    def _timing(self) -> tuple[int, float, int, int]: ...
    def adc_unit(self) -> Any: ...  # ScopeAdc (ty reads a @component class as a variable)
    def bound_path(self, key: str) -> str | None: ...
    def decimator_unit(self) -> Any: ...

    defaults: ClassVar[Any]

    def displayed(self) -> list[int]: ...
    def horizontal(self) -> Any: ...  # ScopeTimebase (ty reads a @component class as a variable)
    def input_frames(self, port: str, request: FrameRequest) -> SampleFrame: ...

    name: Any
    owner: Instrument | None

    def param(self, key: str) -> Any: ...
    def resolve_path(self, path: str) -> str: ...
    def sample_rate(self) -> float: ...
    def scale_offset(self, n: int) -> tuple[float, float]: ...

    session: Session

    def mode_path(self) -> str | None: ...
    def set_mode(self, value: str) -> None: ...
    def trigger_level(self) -> float: ...
    def trigger_unit(self) -> Any: ...  # ScopeTrigger (ty reads a @component class as a variable)
    @property
    def uid(self) -> str: ...
    def vertical_of(self, n: int) -> Any: ...  # ScopeVertical (ty reads a @component class as a variable)

    capture: Capture
    segments: Segments
    recorder: Recorder
    runner: RunControl
