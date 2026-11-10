"""Components: the building blocks of instruments, DUTs and accessories (architecture.md section 1).

A component type declares ports, parameters and behavior. Parameters are literal values or bindings
to instrument state (``$ch.frequency``); bench-level hidden state overrides parameters. Behavior is
expressed by overriding:

* :meth:`Component.output` - signal transformation for a signal output port (pulled lazily);
* :meth:`Component.stamp` / :meth:`Component.emf` - electrical contribution to the bench circuit;
* operations registered with :func:`op` - actions and queries used by commands.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, runtime_checkable

import numpy as np

from ..scpi.errors import ScpiError
from .noise import gaussian, stream_key
from .signal import Signal

if TYPE_CHECKING:
    from ..library.components.scope import Record
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


@runtime_checkable
class Sampler(Protocol):
    """What a trigger model (``trigger_model``) reads from: a measurement path that takes one reading
    at a time (a meter's core, a source-measure unit). metrology-authoring.md 3.1.

    A sampler may implement three optional hooks the trigger model uses when present:
    ``run_samples()`` — how many readings one of its runs takes (an SMU's sweep; replaces the
    configured counts), ``sample_fields(index)`` — additional fields of one reading (an SMU's VOLT,
    CURR, RES, STAT; the reading buffer selects them by name), and ``run_begin()`` / ``run_done()``
    — around a run, for settings the sampler drives while it runs."""

    def sample_time_ns(self) -> int:
        """Virtual time one reading takes (integration, autozero, overhead)."""
        ...

    def sample(self, index: int) -> float:
        """One reading now; ``index`` counts the readings of this initiate (for noise streams)."""
        ...


@runtime_checkable
class ArbSource(Protocol):
    """What a waveform synthesis plays for its arbitrary-waveform function: a table of named waveforms
    (``arb_table``) or a waveform memory of segments (``arb_segments``, #879). The synthesis reads the
    component's ``output`` signal; ``points`` is the length of the waveform it loops (0: none)."""

    def points(self) -> int:
        """The samples of the waveform played."""
        ...


@runtime_checkable
class Acquisition(Protocol):
    """What a waveform transfer (``waveform_transfer``) reads records from: an instrument's
    acquisition block (instrument-authoring.md 3.1). The transfer's ``record`` ref names one; the
    definition's refs check validates the protocol when the definition loads, and the transfer's
    accessor checks it again when a command runs (as ``trigger_model`` checks ``Sampler``).

    An acquisition may also implement :class:`AcquisitionInput` — what the transfer's preamble
    figures need besides the record (probe ratio, ADC, codes per division); without it the
    transfer falls back to its own parameters."""

    def read_record(self, channel: int, start: bool = True) -> tuple[Record | None, int]:
        """The record a read uses, and the virtual time getting it took (``reacquire``,
        ``missing``). ``start``: the read starts a transfer."""
        ...

    def transfer_record(self, channel: int, first: int) -> tuple[Record | None, int]:
        """The record a waveform transfer reads, and the virtual time getting it took; ``first``
        is the transfer's first point."""
        ...

    def timebase_scale(self) -> float:
        """The seconds per division of the record's timebase (a preamble figure)."""
        ...

    acq_count: int
    """Acquisitions stored so far (a preamble figure, e.g. LeCroy's ``NACQ``)."""


@runtime_checkable
class AcquisitionInput(Protocol):
    """The input chain an :class:`Acquisition` may expose: how input volts became the record's
    codes, as the transfer's preamble figures describe it — the probe ratio, the ADC and the ADC's
    codes per division. An acquisition without these leaves the transfer's own parameters
    (``adc_bits``, ``codes_per_div``) in charge."""

    def probe_ratio(self, channel: int) -> float:
        """The probe's ratio on a channel (1.0 when it has none)."""
        ...

    def adc_unit(self) -> Component:
        """The ADC block; the transfer reads its ``bits`` parameter."""
        ...

    def adc_codes_per_div(self) -> float:
        """ADC codes per division of the record's vertical scale."""
        ...


@runtime_checkable
class TraceSource(Protocol):
    """What a ``curve_buffer`` trace or a lock-in's display reads a quantity from (a trace spec or
    a readings table's ``<component>.<quantity>``): a block whose value at an instant a trace
    stores or a display shows."""

    def trace_value(self, quantity: str, t_ns: int) -> float:
        """One quantity's value at instant ``t_ns``; ``quantity`` names the block's own set
        (X, R, FREQUENCY, AUX1, ...)."""
        ...


class ComponentRegistry:
    """The component types a session can build, by type name. One process-wide instance
    (:data:`registry`): the built-in library and plugins register into it at import time."""

    def __init__(self) -> None:
        self._types: dict[str, type[Component]] = {}

    def register(self, type_name: str, cls: type[Component]) -> None:
        self._types[type_name] = cls

    def get(self, type_name: str) -> type[Component] | None:
        return self._types.get(type_name)

    def snapshot(self) -> dict[str, type[Component]]:
        """A copy of the table, to :meth:`restore` (a plugin that fails to load is undone) or to read."""
        return dict(self._types)

    def restore(self, snapshot: dict[str, type[Component]]) -> None:
        self._types.clear()
        self._types.update(snapshot)

    def unregister(self, *type_names: str) -> None:
        for name in type_names:
            self._types.pop(name, None)


registry = ComponentRegistry()


def component(type_name: str) -> Callable[[type[Component]], type[Component]]:
    def deco(cls: type[Component]) -> type[Component]:
        cls.type_name = type_name
        registry.register(type_name, cls)
        return cls

    return deco


def component_type(type_name: str) -> type[Component]:
    from ..library import components as _  # noqa: F401  (registers the built-in library)
    from ..plugins import ensure_entry_point_plugins

    ensure_entry_point_plugins()  # installed packages load by default (D3)
    cls = registry.get(type_name)
    if cls is None:
        raise KeyError(f"unknown component type {type_name!r}")
    return cls


def component_types() -> dict[str, type[Component]]:
    from ..library import components as _  # noqa: F401
    from ..plugins import ensure_entry_point_plugins

    ensure_entry_point_plugins()
    return registry.snapshot()


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


def nested_bindings(value: Any) -> list[str]:
    """Every state path a parameter value reads: its own binding and those inside a nested mapping
    (a mapped value or default that is itself ``$path`` or another mapping, #383), so a change of
    any of them reaches the component."""
    found: list[str] = []
    if isinstance(value, str) and value.startswith("$"):
        found.append(value[1:])
    elif isinstance(value, dict) and "from" in value:
        found.extend(nested_bindings(value["from"]))
        for inner in dict(value.get("map") or {}).values():
            found.extend(nested_bindings(inner))
        found.extend(nested_bindings(value.get("default")))
    return found


def map_setting(mapping: dict[str, Any], value: Any) -> Any:
    """A setting's value through a parameter mapping: ``map`` (else ``default``, else as is), then
    a numeric ``table`` (the largest entry not above the value), then ``value * scale + offset``
    for numbers."""
    table = mapping.get("map")
    if table is not None:
        if value in table:
            value = table[value]
        elif isinstance(value, str) and (hit := next((k for k in table if k.upper() == value.upper()), None)):
            value = table[hit]
        elif isinstance(value, bool) and (
            hit := next((k for k in (str(value), str(int(value))) if k in table), None)
        ):
            value = table[hit]  # a bool setting against "True"/"False" or "1"/"0" keys (#429)
        elif isinstance(value, int) and not isinstance(value, bool) and str(value) in table:
            value = table[str(value)]  # an int setting against the map's (string) keys
        elif isinstance(value, float) and value.is_integer() and str(int(value)) in table:
            value = table[str(int(value))]  # an int setting written through a word (#333: 1.0)
        elif mapping.get("default") is not None:
            value = mapping["default"]
    ladder = mapping.get("table")
    if ladder is not None:
        rungs = {float(k): v for k, v in ladder.items()}
        picked = min(rungs)
        for k in sorted(rungs):
            if k <= float(value):
                picked = k
        value = rungs[picked]
    scale, offset = mapping.get("scale"), mapping.get("offset")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and (scale, offset) != (None, None):
        value = value * (1.0 if scale is None else float(scale)) + (0.0 if offset is None else float(offset))
    return value


NOISE_KEYS = ("ppm_of_range", "ref_time_s", "table", "rms")


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def noise_problems(value: Any) -> list[tuple[str, str]]:
    """What is wrong with a ``noise`` parameter (#746, #752, #750), as (sub-pointer, message) pairs:
    only ``ppm_of_range`` >= 0, ``ref_time_s`` > 0, ``table`` (range -> rms, both >= 0) and ``rms``
    (an absolute rms, >= 0, or a mapping of reading quantity -> rms) are allowed; ``rms`` is for the
    components without a range concept and excludes ``ppm_of_range``/``table``. None is noiseless; a
    state binding is checked where it is resolved."""
    if value is None or binding_of(value) is not None:
        return []
    allowed = ", ".join(NOISE_KEYS)
    if not isinstance(value, dict):
        return [("", f"应是 {{{allowed}}} 的映射，得到 {value!r}")]
    out: list[tuple[str, str]] = []
    for k, v in value.items():
        if k not in NOISE_KEYS:
            out.append((str(k), f"没有键 {k!r}，允许的键只有 {allowed}"))
        elif k == "ppm_of_range" and not (_number(v) and v >= 0):
            out.append((k, f"ppm_of_range 应是 ≥ 0 的数，得到 {v!r}"))
        elif k == "ref_time_s" and not (_number(v) and v > 0):
            out.append((k, f"ref_time_s 应是 > 0 的数，得到 {v!r}"))
        elif k == "rms" and not (
            (_number(v) and v >= 0)
            or (
                isinstance(v, dict)
                and v
                and all(isinstance(rk, str) and _number(rv) and rv >= 0 for rk, rv in v.items())
            )
        ):
            out.append((k, f"rms 应是 ≥ 0 的数，或“读数量 → rms”的非空映射，得到 {v!r}"))
        elif k == "table" and v is not None:
            if not isinstance(v, dict):
                out.append((k, f"table 应是“量程 → rms”的映射，得到 {v!r}"))
                continue
            for rk, rv in v.items():
                try:
                    key_ok = _number(float(rk)) and float(rk) >= 0
                except (TypeError, ValueError):
                    key_ok = False
                if not key_ok:
                    out.append((f"{k}/{rk}", f"table 的键（量程）应是 ≥ 0 的数，得到 {rk!r}"))
                elif not (_number(rv) and rv >= 0):
                    out.append((f"{k}/{rk}", f"table[{rk!r}] 的 rms 应是 ≥ 0 的数，得到 {rv!r}"))
    if isinstance(value, dict) and "rms" in value and ("ppm_of_range" in value or "table" in value):
        out.append(
            ("", "rms 与 ppm_of_range/table 不同时给：有量程的读数用 ppm_of_range/table，无量程的用 rms")
        )
    return out


class Component:
    """Base class for every component type."""

    type_name: ClassVar[str] = "component"
    #: Where the component may be placed: "part" (a bench node of its own), "accessory" (on a wire) or "internal"
    #: (inside instrument definitions only).
    category: ClassVar[str] = "internal"
    label: ClassVar[str] = ""
    label_en: ClassVar[str] = ""
    ports: ClassVar[dict[str, PortSpec]] = {}
    #: Ports the instrument wiring creates (a regular expression), e.g. ``in\d+`` for in1..inN.
    port_pattern: ClassVar[str | None] = None
    #: What a port matching ``port_pattern`` is (the multiplexer's relay terminals are electrical).
    pattern_port: ClassVar[PortSpec] = PortSpec("inout", "electrical")
    #: Circuit role (see :data:`Role`); input impedance comes from ``impedance_of(params)``.
    role: ClassVar[Role | None] = None
    #: Ports that carry a source's remote-sense feedback, and the parameter that enables it: the
    #: bench validator warns when the setting is on but either port is left unconnected
    #: (metrology-authoring.md 6).
    sense_ports: ClassVar[tuple[str, ...]] = ()
    sense_param: ClassVar[str] = ""
    #: False for a component written for one model's own behavior: built-in real models may not
    #: use one beyond the list in tests/test_model_components.py (metrology-authoring.md 9).
    generic: ClassVar[bool] = True
    defaults: ClassVar[dict[str, Any]] = {}
    units: ClassVar[dict[str, str]] = {}
    #: Word parameters that take one of a set of SCPI words (long or short form, any case), e.g.
    #: {"function": ("VOLTage", "CURRent", "RESistance")}. Definition validation refuses a literal
    #: or a bound setting that can take another word (E-DEF-PARAM-VALUE, #308); :meth:`choice`
    #: reads the parameter as its canonical word.
    choices: ClassVar[dict[str, tuple[str, ...]]] = {}
    #: Instrument settings a component reads by path rather than through a bound parameter, whose
    #: changes it still hears in :meth:`on_state` (the analyzer's marker group, #274). Paths the
    #: definition does not declare are ignored.
    watches: ClassVar[tuple[str, ...]] = ()
    #: Parameters that name another component of the instrument: parameter -> (the component type
    #: it must be, or derive from — or a Protocol (``engine.component``) its class must implement,
    #: checked by its members; "instrument" or "channel" for where it is declared). Definition
    #: validation checks them (E-DEF-COMPONENT-REF).
    refs: ClassVar[dict[str, tuple[str | type, Literal["instrument", "channel"]]]] = {}
    #: Of those, the ones that may be left empty (the component then does without, e.g. a source
    #: without a reference clock).
    optional_refs: ClassVar[frozenset[str]] = frozenset()
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

    def choice(self, key: str) -> str | None:
        """A word parameter (``choices``) as its canonical word, None when unset; a word outside
        the set is a -224 (a bound setting cannot reach here once validation passed)."""
        value = self.param(key)
        if value is None:
            return None
        from ..scpi import values as V

        return V.parse_enum(str(value).encode(), self.choices[key])

    def port_spec(self, port: str) -> PortSpec:
        """A declared port, or one the instrument creates through ``port_pattern``."""
        spec = self.ports.get(port)
        if spec is not None:
            return spec
        if self.port_pattern and re.fullmatch(self.port_pattern, port):
            return self.pattern_port
        raise KeyError(port)

    def reply_number(self, value: float, digits: int = 6) -> bytes:
        """A number this component answers: ``digits`` after the point (the component's own
        format), or the definition's response format when it says ``readings: format`` (#366)."""
        from ..scpi import values as V

        owner = self.owner
        if owner is not None and getattr(owner.definition.format, "readings", "component") == "format":
            return owner.format_number(value)
        return V.format_float(value, digits)

    def param(self, key: str) -> Any:
        if key in self.overrides:
            return self.overrides[key]
        return self._resolve(key, self._params.get(key))

    def _resolve(self, key: str, value: Any) -> Any:
        if isinstance(value, dict) and "from" in value:  # a mapping of a setting (ParamMapping)
            # A mapped value may itself be $path, another setting (#383: the SR86x output offset
            # is COFP's percentage while COFA is on, else 0).
            return self._resolve(key, map_setting(value, self._resolve(key, value["from"])))
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

    # ---- reading noise (#746) --------------------------------------------------------------------
    #: Counts the frames the Web panel took of this component's readings (see :meth:`panel_frame`).
    _panel_frames = 0

    def noise_sample(self, sigma: float, stream: str, *key: object) -> float:
        """One normal sample of rms ``sigma`` that is a pure function of (seed, this component,
        ``stream``, ``key``): the same instant gives the same value, a replay is bit-identical."""
        if not sigma > 0:
            return 0.0
        return sigma * float(gaussian(stream_key(self.session.seed, self.uid, stream, *key), np.zeros(1))[0])

    def noise_rms(self, range_: float | None, integration_s: float, quantity: str | None = None) -> float:
        """The rms of this component's reading noise (the ``noise`` parameter) on ``range_`` at an
        integration time: ``ppm_of_range`` of the range, or the ``table`` entry for that range, at the
        reference time ``ref_time_s``, falling as the square root of the integration time. A component
        without a range concept gives an absolute ``rms`` instead — a number, or a mapping that
        ``quantity`` picks from (#750). 0 when the parameter is unset (every definition before #746)
        or nothing applies."""
        spec = self.param("noise")
        if not isinstance(spec, dict):
            return 0.0
        if "rms" in spec:
            rms = spec["rms"]
            if isinstance(rms, dict):
                if quantity is None:
                    return 0.0
                rms = rms.get(quantity, 0.0)  # a quantity the definition does not name reads noiseless
            return self._scaled_rms(float(rms), spec, integration_s)
        if range_ is None:
            return 0.0
        table = spec.get("table") or {}
        rms = next((float(v) for k, v in table.items() if math.isclose(float(k), range_)), None)
        if rms is None:
            rms = float(spec.get("ppm_of_range", 0.0)) * 1e-6 * abs(range_)
        return self._scaled_rms(rms, spec, integration_s)

    def _scaled_rms(self, rms: float, spec: dict[str, Any], integration_s: float) -> float:
        ref = float(spec.get("ref_time_s", 0.02))
        if integration_s > 0 and ref > 0:
            rms *= math.sqrt(ref / integration_s)
        return rms

    def panel_frame(self) -> int:
        """The next frame number of the Web panel's readings: its own counter, so the panel's noise
        moves without touching what a script sees or the virtual clock (D32, like #730)."""
        self._panel_frames += 1
        return self._panel_frames

    def peer(self, name: str, *, required: bool = False) -> Component | None:
        """The component another one names (a ``refs`` parameter): for a channel component its own
        channel's ``ch<n>.<name>`` first, then the instrument-level ``name`` (#382). None when the
        name is empty or names nothing; ``required=True`` raises a -300 naming the lookup instead —
        for a ref the definition must fill (not in ``optional_refs``) it cannot legitimately miss."""
        if not name or self.owner is None:
            if required:
                raise ScpiError(-300, f"{self.uid}: {name!r} is not a component of this instrument")
            return None
        comp = None
        if self.channel is not None:
            comp = self.owner.components.get(f"ch{self.channel}.{name}")
        if comp is None:
            comp = self.owner.components.get(name)
        if comp is None and required:
            raise ScpiError(-300, f"{self.uid}: {name!r} is not a component of this instrument")
        return comp

    def ref(self, key: str) -> Component | None:
        """The component a ``refs`` parameter names — the sibling accessor the declaration yields:
        :meth:`peer` on the parameter's value, required unless the ref is in ``optional_refs``."""
        name = str(self.param(key) or "")
        if not name and key in self.optional_refs:
            return None
        return self.peer(name, required=key not in self.optional_refs)

    def ref_links(self) -> list[tuple[str, str]]:
        """What this component uses: ``(refs parameter, the name of the component it resolves to)`` for
        every ref that is set and names a component, as :meth:`peer` finds it. Never raises: the
        internal-structure view (#729) shows what is there."""
        out: list[tuple[str, str]] = []
        for key in self.refs:
            target = self.peer(str(self.param(key) or ""))
            if target is None or self.owner is None:
                continue
            name = next((n for n, c in self.owner.components.items() if c is target), None)
            if name is not None:
                out.append((key, name))
        return out

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

    def bound_path(self, key: str) -> str | None:
        """The resolved state path one parameter binds, None when the parameter is not bound
        (:meth:`bound_paths` for one key) — how a component writes the setting a parameter tracks."""
        ref = binding_of(self._params.get(key))
        return None if ref is None else self.resolve_path(ref)

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

    def wired(self, port: str) -> bool:
        """Whether an electrical port is wired into the bench (a wire connects it)."""
        ref = self.session.port_ref(self, port)
        return ref is not None and ref in self.session.port_net

    # ---- electrical --------------------------------------------------------------------------

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        """Add this component's elements to the bench circuit. ``node(port)`` gives the node name."""

    def emf(self) -> dict[str, Signal]:
        """EMF of each independent source this component stamped, keyed by source id."""
        return {}

    # ---- lifecycle ---------------------------------------------------------------------------

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> Any:
        """Called after a state variable a parameter binds changed; ``key`` is that parameter's
        name (the first one bound to the path when several bind it), None when the change reached
        the component through ``watches`` or a nested mapping instead. ``path`` is the state path.
        Raise ScpiError to refuse the change, or return the value to keep instead (e.g. the nearest
        legal one). None keeps ``new``."""

    def check_settings(self) -> None:
        """After a command that writes several settings at once (a ``quantities`` command such as
        ``OUT 1 A, 400 HZ``): raise ScpiError when the settings together are not allowed (a
        frequency beyond the new range's band, #580); the command's writes are then taken back.
        Each write alone is checked in :meth:`on_state`, where the others may not be set yet."""

    def state_limits(self, path: str) -> tuple[float | None, float | None] | None:
        """Limits narrower than the setting's declared ones, now (#437: a function generator's
        maximum frequency depends on the waveform): (min, max), either None for the declared
        one; None when this component has no say. ``MIN``/``MAX`` queries and keywords use them."""
        return None

    def reset(self) -> None:
        """Return runtime state to power-on (``*RST``)."""

    def update_mode(self, dc_voltage: Callable[[str], float]) -> bool:
        """Piecewise-linear devices: choose the mode the DC operating point calls for.

        ``dc_voltage(port)`` is the solved DC voltage on one of the component's electrical ports;
        ``dc_voltage.signal(port)`` (when present) the whole solved signal there, for a limit on the
        AC terminal voltage (#570). Return True when the mode changed (the session then solves again).
        """
        return False

    def status_flags(self) -> dict[str, bool]:
        """Level conditions a ``status_register`` may show as bits, by name (#248, #273): e.g. an
        acquisition's ``RUN``. Momentary events go through :meth:`Instrument.status_event`."""
        return {}

    def sync(self) -> None:
        """Dynamic components: note what changed at the current instant (see :attr:`dynamic`)."""

    def retry_pending(self) -> None:
        """An operation waiting for an event (a scope armed for a trigger) looks for it again: called
        whenever a command arrives, before what is due is finished (``Instrument.settle_pending``)."""

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

    def watched_paths(self) -> list[str]:
        """Every state path a parameter reads, nested mappings included (see ``nested_bindings``)."""
        out: list[str] = []
        for value in self._params.values():
            for ref in nested_bindings(value):
                path = self.resolve_path(ref)
                if path not in out:
                    out.append(path)
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
