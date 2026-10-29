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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from .signal import Signal

if TYPE_CHECKING:
    from .circuit import Circuit
    from .instrument import Instrument
    from .session import Session

Direction = Literal["in", "out", "inout"]
Medium = Literal["signal", "electrical", "reference"]
#: What a component is to the bench circuit, for checks that read a bench without solving it:
#: "driver" holds the nets on its electrical outputs to a voltage (an output stage, a supply, a
#: buffer's output), "series" is a short in series for load matching (a cable).
Role = Literal["driver", "series"]


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
    #: Named parts of the reply for the command's reply template, e.g. {unit} (see ``op(fields=...)``).
    fields: dict[str, str] = field(default_factory=dict)


_REGISTRY: dict[str, type[Component]] = {}


def component(type_name: str) -> Callable[[type[Component]], type[Component]]:
    def deco(cls: type[Component]) -> type[Component]:
        cls.type_name = type_name
        _REGISTRY[type_name] = cls
        return cls

    return deco


def component_type(type_name: str) -> type[Component]:
    from ..library import components as _  # noqa: F401  (registers the built-in library)
    from ..plugins import ensure_entry_point_plugins

    ensure_entry_point_plugins()  # installed packages load by default (D3)
    try:
        return _REGISTRY[type_name]
    except KeyError:
        raise KeyError(f"unknown component type {type_name!r}") from None


def component_types() -> dict[str, type[Component]]:
    from ..library import components as _  # noqa: F401
    from ..plugins import ensure_entry_point_plugins

    ensure_entry_point_plugins()
    return dict(_REGISTRY)


def op(
    name: str | None = None, fields: tuple[str, ...] = ()
) -> Callable[[Callable[..., OpResult]], Callable[..., OpResult]]:
    """Mark a method as an operation callable from a command (``op: <component>.<name>``).

    ``fields``: names the operation's result gives a reply template besides {value}."""

    def deco(fn: Callable[..., OpResult]) -> Callable[..., OpResult]:
        fn.__op_name__ = name or fn.__name__  # type: ignore[attr-defined]
        fn.__op_fields__ = tuple(fields)  # type: ignore[attr-defined]
        return fn

    return deco


def binding_of(value: Any) -> str | None:
    """The state path a parameter value is bound to (``$ch.wave`` or ``{from: $ch.wave, ...}``), or None."""
    if isinstance(value, dict) and isinstance(value.get("from"), str):
        value = value["from"]
    if isinstance(value, str) and value.startswith("$"):
        return value[1:]
    return None


def map_setting(mapping: dict[str, Any], value: Any) -> Any:
    """A setting's value through a parameter mapping: ``map`` (else ``default``, else as is), then
    ``value * scale + offset`` for numbers."""
    table = mapping.get("map")
    if table is not None:
        if value in table:
            value = table[value]
        elif isinstance(value, str) and (hit := next((k for k in table if k.upper() == value.upper()), None)):
            value = table[hit]
        elif mapping.get("default") is not None:
            value = mapping["default"]
    scale, offset = mapping.get("scale"), mapping.get("offset")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and (scale, offset) != (None, None):
        value = value * (1.0 if scale is None else float(scale)) + (0.0 if offset is None else float(offset))
    return value


class Component:
    """Base class for every component type."""

    type_name: ClassVar[str] = "component"
    #: Where the component may be placed: "dut" (bench node), "accessory" (on a wire) or "internal"
    #: (inside instrument definitions only).
    category: ClassVar[str] = "internal"
    label: ClassVar[str] = ""
    label_en: ClassVar[str] = ""
    ports: ClassVar[dict[str, PortSpec]] = {}
    #: Ports the instrument wiring creates (a regular expression), e.g. ``in\d+`` for in1..inN.
    port_pattern: ClassVar[str | None] = None
    #: Circuit role (see :data:`Role`); input impedance comes from ``impedance_of(params)``.
    role: ClassVar[Role | None] = None
    #: False for a component written for one model's own behavior: built-in real models may not
    #: use one beyond the list in tests/test_model_components.py (metrology-authoring.md 9).
    generic: ClassVar[bool] = True
    defaults: ClassVar[dict[str, Any]] = {}
    units: ClassVar[dict[str, str]] = {}
    #: Components with state that evolves in virtual time (filters, oscillators) set this and
    #: implement :meth:`sync`; the session calls it whenever signals change and before time moves.
    dynamic: ClassVar[bool] = False
    #: Piecewise-linear devices (sources with limits, electronic loads) set this and implement
    #: :meth:`update_mode`; the session re-solves until every device's mode is consistent.
    piecewise: ClassVar[bool] = False

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
        return self._resolve(key, self._params.get(key))

    def _resolve(self, key: str, value: Any) -> Any:
        if isinstance(value, dict) and "from" in value:  # a mapping of a setting (ParamMapping)
            return map_setting(value, self._resolve(key, value["from"]))
        if isinstance(value, str):
            if value.startswith("$"):
                if self.owner is None:
                    raise ValueError(f"{self.uid}: binding {value!r} outside an instrument")
                return self.owner.get_state(self.resolve_path(value[1:]))
            default = self.defaults.get(key)
            if isinstance(default, (int, float)) and not isinstance(default, bool):
                # YAML 1.1 reads "25.0e6" (no exponent sign) as a string; numbers are meant here.
                try:
                    return float(value)
                except ValueError:
                    pass
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
            ref = binding_of(value)
            if ref is not None:
                out[key] = self.resolve_path(ref)
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

    def update_mode(self, dc_voltage: Callable[[str], float]) -> bool:
        """Piecewise-linear devices: choose the mode the DC operating point calls for.

        ``dc_voltage(port)`` is the solved DC voltage on one of the component's electrical ports.
        Return True when the mode changed (the session then solves again).
        """
        return False

    def sync(self) -> None:
        """Dynamic components: note what changed at the current instant (see :attr:`dynamic`)."""

    # ---- operations --------------------------------------------------------------------------

    def operation(self, name: str) -> Callable[..., OpResult] | None:
        for attr in dir(type(self)):
            fn = getattr(type(self), attr)
            if getattr(fn, "__op_name__", None) == name:
                return getattr(self, attr)  # type: ignore[no-any-return]
        return None

    # ---- observability -----------------------------------------------------------------------

    def readings(self) -> dict[str, Any] | None:
        """What the instrument's front panel shows, read without side effects (no SCPI, no errors).

        Return ``{"values": {...}, "traces": [{"name", "t0_s", "dt_s", "values"}]}`` or None.
        """
        return None

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

    def nominal(self) -> list[tuple[str, Any, str | None]]:
        """``(name, value, bound state path)`` for each parameter as the model defines it.

        Per-unit overrides (bench hidden state, faults) are left out: this is the model, not the unit.
        A bound parameter's value is the setting's current value.
        """
        out = []
        bound = self.bound_paths()
        for key, value in self._params.items():
            try:
                resolved = self._resolve(key, value)
            except Exception:  # a binding that cannot resolve yet
                resolved = None
            out.append((key, resolved, bound.get(key)))
        return out
