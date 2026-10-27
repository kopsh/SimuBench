"""Components: the building blocks of instruments, DUTs and accessories (architecture.md section 1).

A component type declares ports, parameters and behavior. Parameters are literal values or bindings
to instrument state (``$ch.frequency``); bench-level hidden state overrides parameters. Behavior is
expressed by overriding:

* :meth:`Component.output` - signal transformation for a signal output port (pulled lazily);
* :meth:`Component.stamp` / :meth:`Component.emf` - electrical contribution to the bench circuit;
* operations registered with :func:`op` - actions and queries used by commands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from .signal import Signal

if TYPE_CHECKING:
    from .circuit import Circuit
    from .instrument import Instrument
    from .session import Session

Direction = Literal["in", "out", "inout"]
Medium = Literal["signal", "electrical", "reference"]


@dataclass(frozen=True)
class PortSpec:
    direction: Direction
    medium: Medium
    doc: str = ""


@dataclass
class OpResult:
    """Outcome of an operation: an optional response and the virtual time it consumed."""

    response: bytes | None = None
    duration_ns: int = 0


_REGISTRY: dict[str, type[Component]] = {}


def component(type_name: str) -> Callable[[type[Component]], type[Component]]:
    def deco(cls: type[Component]) -> type[Component]:
        cls.type_name = type_name
        _REGISTRY[type_name] = cls
        return cls

    return deco


def component_type(type_name: str) -> type[Component]:
    from ..library import components as _  # noqa: F401  (registers the built-in library)

    try:
        return _REGISTRY[type_name]
    except KeyError:
        raise KeyError(f"unknown component type {type_name!r}") from None


def component_types() -> dict[str, type[Component]]:
    from ..library import components as _  # noqa: F401

    return dict(_REGISTRY)


def op(name: str | None = None) -> Callable[[Callable[..., OpResult]], Callable[..., OpResult]]:
    """Mark a method as an operation callable from a command (``op: <component>.<name>``)."""

    def deco(fn: Callable[..., OpResult]) -> Callable[..., OpResult]:
        fn.__op_name__ = name or fn.__name__  # type: ignore[attr-defined]
        return fn

    return deco


class Component:
    """Base class for every component type."""

    type_name: ClassVar[str] = "component"
    ports: ClassVar[dict[str, PortSpec]] = {}
    defaults: ClassVar[dict[str, Any]] = {}

    def __init__(
        self,
        name: str,
        params: dict[str, Any],
        owner: Instrument | None,
        session: Session,
        channel: int | None = None,
    ) -> None:
        self.name = name  # unique within its owner, e.g. "ch1.gen" or "rc"
        self.owner = owner
        self.session = session
        self.channel = channel
        self._params = {**self.defaults, **params}
        self.overrides: dict[str, Any] = {}

    # ---- identity ----------------------------------------------------------------------------

    @property
    def uid(self) -> str:
        """Globally unique id within the bench: '<instrument>/<component>' or '<dut>'."""
        return f"{self.owner.name}/{self.name}" if self.owner else self.name

    # ---- parameters --------------------------------------------------------------------------

    def param(self, key: str) -> Any:
        if key in self.overrides:
            return self.overrides[key]
        value = self._params.get(key)
        if isinstance(value, str) and value.startswith("$"):
            if self.owner is None:
                raise ValueError(f"{self.uid}: binding {value!r} outside an instrument")
            return self.owner.get_state(self.resolve_path(value[1:]))
        return value

    def resolve_path(self, path: str) -> str:
        """Expand channel-relative state paths: 'ch.scale' -> 'ch2.scale' for channel 2."""
        if path.startswith("ch.") and self.channel is not None:
            return f"ch{self.channel}.{path[3:]}"
        return path

    def bound_paths(self) -> dict[str, str]:
        """Parameter name -> resolved state path for every bound parameter."""
        out = {}
        for key, value in self._params.items():
            if isinstance(value, str) and value.startswith("$"):
                out[key] = self.resolve_path(value[1:])
        return out

    # ---- signal flow -------------------------------------------------------------------------

    def input(self, port: str) -> Signal:
        """Signal arriving at a signal input port (zero when nothing is wired)."""
        if self.owner is None:
            return Signal.zero()
        return self.owner.upstream_signal(self, port)

    def output(self, port: str) -> Signal:
        """Signal leaving a signal output port. Override in components with signal outputs."""
        raise NotImplementedError(f"{self.type_name} has no signal output {port!r}")

    def pin_signal(self, port: str) -> Signal:
        """Voltage on the net an electrical port is connected to."""
        return self.session.port_signal(self, port)

    # ---- electrical --------------------------------------------------------------------------

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        """Add this component's elements to the bench circuit. ``node(port)`` gives the node name."""

    def emf(self) -> dict[str, Signal]:
        """EMF of each independent source this component stamped, keyed by source id."""
        return {}

    # ---- lifecycle ---------------------------------------------------------------------------

    def on_state(self, path: str, old: Any, new: Any) -> None:
        """Called after a bound state variable changed; raise ScpiError to refuse the change."""

    def reset(self) -> None:
        """Return runtime state to power-on (``*RST``)."""

    # ---- operations --------------------------------------------------------------------------

    def operation(self, name: str) -> Callable[..., OpResult] | None:
        for attr in dir(type(self)):
            fn = getattr(type(self), attr)
            if getattr(fn, "__op_name__", None) == name:
                return getattr(self, attr)  # type: ignore[no-any-return]
        return None

    # ---- observability -----------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Parameters as currently resolved, for inspectors and explanations."""
        out: dict[str, Any] = {}
        for key in self._params:
            try:
                out[key] = self.param(key)
            except Exception:  # a binding that cannot resolve yet
                out[key] = None
        out.update(self.overrides)
        return out
