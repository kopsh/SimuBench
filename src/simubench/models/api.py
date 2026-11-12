"""HTTP and WebSocket message models of the local service.

These Pydantic models are the single source of the interface (D21): FastAPI derives the OpenAPI
description from them, and the web client's TypeScript types are generated from that.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..engine.circuit import SourceContribution  # re-export (D57)
from ..engine.circuit import SourceLine as SourceLine
from ..engine.edit import EditOp
from ..engine.events import Event as Event  # re-export (D57, issue 595)
from ..engine.events import RunEvent as RunEvent
from ..engine.replay import ReplayMismatch as ReplayMismatch  # re-export (D57, issue 626)
from ..engine.signal import SpectralLine
from ..engine.silence import SilenceReason as SilenceReason  # re-export (D57, issue 626)
from ..engine.validate import Diagnostic as Diagnostic  # re-export (D57, issue 626)
from .bench import Fault, LayoutItem
from .definition import Panel, Reference, StateSpec


class _Model(BaseModel):
    # Fields with defaults are always present in responses; say so in the schema so that the
    # generated TypeScript types do not mark them optional.
    model_config = ConfigDict(populate_by_name=True, json_schema_serialization_defaults_required=True)


class Health(_Model):
    status: Literal["ok"] = "ok"
    version: str


class ErrorItem(_Model):
    code: int
    message: str


class BenchFile(_Model):
    path: str
    name: str
    description: str | None = None
    instruments: int = 0
    error: str | None = Field(None, description="Why the file could not be read, if it could not")


class DefinitionChange(_Model):
    """One change between two versions of a definition, in the instrument's language (T3): the
    category, the object, and the old and new values. Labels (新增、删除、范围…) are the
    interface's (i18n); the values are the definition's own."""

    kind: str = Field(
        ...,
        description="command | deviation | state | component | channel_component | identity | specs"
        " | template | channels, or the top-level key for anything else (ports, wiring, display…)",
    )
    action: Literal["added", "removed", "changed"]
    target: str = Field(
        ..., description="The object: a command header, a setting path, '<component>.<param>', 'identity.idn'"
    )
    detail: str | None = Field(
        None, description="What changed within the object: a field ('reply', 'min', 'limit', 'DC', ...)"
    )
    old: Any = Field(None, description="The value before (None for an added object)")
    new: Any = Field(None, description="The value after (None for a removed object)")


class DefinitionVersion(_Model):
    """One saved version of a definition (D35), with what changed since the one before it."""

    seq: int
    sha256: str
    source: Literal["ui", "agent", "api", "external", "restore"] = Field(
        ..., description="Who wrote it; external: changed outside SimuBench, seen at the next save"
    )
    saved_at: str = Field(..., description="UTC, ISO 8601")
    changes: list[DefinitionChange] = Field(
        default_factory=list, description="Relative to the previous version (the first: everything)"
    )


class DefinitionChangeList(_Model):
    """The changes between two chosen versions of a definition."""

    base: int = Field(..., description="The seq the comparison starts from (0: nothing, i.e. creation)")
    target: int
    changes: list[DefinitionChange]


class DefinitionDocument(_Model):
    """An instrument definition's content as stored, with its static-check diagnostics."""

    id: str
    path: str | None = Field(None, description="Workspace-relative file, e.g. instruments/acme-x1.yaml")
    editable: bool = Field(
        description="False for definitions outside the workspace (built in or on the search path): "
        "create a copy under a new id to change one"
    )
    sha256: str = Field(..., description="Hash of the file bytes; pass back as base_sha256 to edit")
    data: dict[str, Any]
    ok: bool
    diagnostics: list[Diagnostic]
    sessions_to_restart: list[str] = Field(
        default_factory=list, description="Running sessions that use an older version of this definition"
    )
    changes: list[DefinitionChange] = Field(
        default_factory=list,
        description="What this save changed (edit, create, restore): relative to the previous version "
        "of the file; empty when nothing did (a read, or a save that only reorders keys)",
    )


class CoverageEntryInfo(_Model):
    """One command of a real model's coverage table (D42)."""

    section: str = Field(..., description="The manual section the command is listed under")
    header: str = Field(..., description="Header as the definition writes it, e.g. '[:SENSe]:VOLTage:NPLC'")
    level: Literal["implemented", "accepted", "not_modeled"] = Field(
        ...,
        description="implemented: modeled; accepted: parsed and stored without effect on the data; "
        "not_modeled: answers -113",
    )
    note: str | None = None


class DefinitionCoverage(_Model):
    """A real model's coverage table: every command of its manual and how far SimuBench goes
    (the acceptance page's command list; none for definitions without a reference)."""

    model: str
    stats: dict[str, int] = Field(..., description="Counts by level: implemented, accepted, not_modeled")
    entries: list[CoverageEntryInfo]


class BenchDocument(_Model):
    """A bench file's content as stored, with its diagnostics."""

    path: str
    sha256: str = Field(..., description="Hash of the file bytes; pass back as base_sha256 to save")
    fingerprint: str = Field(
        ..., description="Hash of what a session is built from (layout and description excluded)"
    )
    data: dict[str, Any]
    ok: bool
    diagnostics: list[Diagnostic]


SaveSource = Literal["ui", "agent", "api"]


class SaveBenchRequest(_Model):
    path: str
    base_sha256: str | None = Field(
        ..., description="Hash the edit started from; null creates a new file (409 if it exists)"
    )
    data: dict[str, Any]
    source: SaveSource = Field("api", description="Who is saving, kept in the version history (D35)")


class EditBenchRequest(_Model):
    path: str
    base_sha256: str
    ops: list[EditOp]
    source: SaveSource = Field("api", description="Who is saving, kept in the version history (D35)")


class BenchVersion(_Model):
    """One saved version of a bench file (D35)."""

    seq: int
    sha256: str
    fingerprint: str
    source: Literal["ui", "agent", "api", "external", "restore"] = Field(
        ..., description="Who wrote it; external: changed outside SimuBench, seen at the next save"
    )
    saved_at: str = Field(..., description="UTC, ISO 8601")
    layout_only: bool = Field(False, description="Only node positions changed since the previous version")


class BenchVersionDocument(_Model):
    path: str
    version: BenchVersion
    data: dict[str, Any]


class RestoreBenchRequest(_Model):
    path: str
    seq: int
    base_sha256: str


class ValidateRequest(_Model):
    data: dict[str, Any]
    path: str | None = Field(None, description="Bench file the data belongs to; finds its own definitions")


class ValidateResponse(_Model):
    ok: bool
    diagnostics: list[Diagnostic]


class DefinitionSummary(_Model):
    id: str
    manufacturer: str
    model: str
    instrument_class: str
    description: str | None = None
    channels: int
    ports: list[str]
    template: str | None = None
    workspace: bool = Field(
        False, description="Defined in the workspace's instruments/ (the user's own), not built in"
    )


class ComponentTypeInfo(_Model):
    type: str
    category: Literal["part", "accessory", "internal"]
    label: str | None = None
    label_en: str | None = None
    doc: str | None = None
    ports: list[PortInfo]
    defaults: dict[str, Any]
    units: dict[str, str]


class ParamInfo(_Model):
    name: str
    default: Any = None
    unit: str | None = None
    doc: str | None = Field(None, description="The comment next to the default in the component's source")


class OperationParam(_Model):
    name: str
    type: str | None = Field(None, description="The Python annotation, e.g. 'float | None'")
    default: Any = None
    required: bool = False


class OperationInfo(_Model):
    name: str = Field(description="What a command names: op: <component>.<name>")
    doc: str | None = None
    params: list[OperationParam] = Field(
        description="Keyword parameters; a command's params pass under these names. Every operation "
        "also receives query (a query form), channel (the command's <ch>) and suffixes"
    )


class ComponentDoc(_Model):
    """A component type as instrument definitions use it: ports, parameters, operations, role."""

    type: str
    category: Literal["part", "accessory", "internal"]
    role: Literal["driver", "series"] | None = None
    generic: bool = Field(
        True,
        description="False for a component written for one model's own behavior; built-in real models "
        "use generic components only (metrology-authoring.md 9)",
    )
    label: str | None = None
    label_en: str | None = None
    doc: str | None = None
    ports: list[PortInfo]
    port_pattern: str | None = Field(None, description="Ports created by the wiring, e.g. 'in\\d+'")
    params: list[ParamInfo]
    operations: list[OperationInfo]


class Catalog(_Model):
    """What can be placed on a bench: instrument definitions, DUT and accessory types."""

    instruments: list[DefinitionSummary]
    components: list[ComponentTypeInfo]


class PortInfo(_Model):
    name: str
    direction: str
    medium: str
    net: str | None = None
    channel: int | None = None


# A state variable as the session serves it: the definition's own spec (StateSpec, reused instead
# of a hand-copied mirror, like Panel above) plus the runtime view — the path, the current value,
# the channel behind a `ch.` path, and whether a command bound to it is dangerous.
class StateVarInfo(StateSpec):
    model_config = ConfigDict(populate_by_name=True, json_schema_serialization_defaults_required=True)

    path: str
    dangerous: bool = Field(
        False, description="A command bound to it is marked dangerous; confirm before changing"
    )
    value: Any = None
    channel: int | None = None


class DisplayInfo(_Model):
    template: str | None = None
    summary: list[str] = Field(default_factory=list)
    channel_summary: list[str] = Field(default_factory=list)
    actions: dict[str, str] = Field(default_factory=dict, description="Commands for front-panel actions")
    panel: Panel | None = Field(
        None,
        description="The front panel the UI draws: the definition's own, or its class's default layout "
        "(multimeter, calibrator); none for a class with a dedicated screen or without a default",
    )
    panel_source: Literal["declared", "class_default"] | None = None


class DeviationInfo(_Model):
    """One parameter's individual deviation (个体偏差): its range, and this unit's value when shown."""

    param: str = Field(..., description="'<component>.<parameter>' (instrument) or '<parameter>' (DUT)")
    limit: Any = Field(..., description="±limit: a number, or a table keyed by the last `by` value")
    unit: str | None = None
    distribution: str
    by: dict[str, list[Any]] | None = None
    value: Any = Field(
        None,
        description="This unit's value; null while hidden (run.reveal_true_values off) or not drawn",
    )
    drawn: bool = Field(False, description="Drawn from the bench seed (else the nominal or a hidden value)")


class InstrumentInfo(_Model):
    name: str
    label: str | None = None
    model: str
    manufacturer: str
    definition_id: str
    instrument_class: str
    description: str | None = None
    idn: str
    channels: int
    address_virtual: str
    address_real: str | None = None
    address_hislip: str | None = None
    address_vxi11: str | None = None
    scpi_port: int | None = None
    display: DisplayInfo
    ports: list[PortInfo]
    state: list[StateVarInfo]
    errors: list[ErrorItem]
    dut: bool = Field(False, description="Marked as a DUT: the object under test or calibration")
    deviation: Literal["nominal", "from_specs"] = "nominal"
    deviations: list[DeviationInfo] = Field(
        default_factory=list, description="The definition's deviation ranges, with this unit's values"
    )
    remote: list[str] = Field(
        default_factory=list,
        description="Clients connected to this instrument's ports (socket, HiSLIP, VXI-11): while any is, the "
        "instrument is remote and the UI's settings, console and panel buttons are refused (#121)",
    )
    local: bool = Field(
        False, description="Taken back to local from the UI (LOCAL) until a remote client's next command"
    )


class ComponentInfo(_Model):
    name: str
    type: str
    label: str | None = None
    params: dict[str, Any] = Field(
        default_factory=dict, description="Parameters; a drawn deviation shows its nominal value while hidden"
    )
    ports: list[PortInfo] = Field(default_factory=list)
    dut: bool = Field(False, description="Marked as a DUT: the object under test or calibration")
    deviations: list[DeviationInfo] = Field(default_factory=list)


class InternalParam(_Model):
    name: str
    value: Any = Field(
        None, description="The model's value; for a bound parameter, the setting's current value"
    )
    binding: str | None = Field(
        None, description="Instrument setting the parameter follows, e.g. 'ch1.coupling'"
    )
    unit: str | None = None


class InternalCommand(_Model):
    """A SCPI command that drives a component (#739): it writes a setting the component follows, or
    runs one of the component's operations."""

    header: str = Field(
        ...,
        description="The command header as the definition writes it, with a channel component's "
        "channel filled in (':CHANnel1:SCALe')",
    )
    setting: str | None = Field(
        None,
        description="The component's setting the command writes (e.g. 'ch1.scale'); none for an operation",
    )
    op: str | None = Field(
        None, description="The component's operation the command runs (e.g. 'figure'); none for a setting"
    )
    set: bool = True
    query: bool = True


class InternalReading(_Model):
    """A query command whose answer the component influences (#739): the command reads a component
    at or downstream of it."""

    header: str = Field(..., description="As in InternalCommand, with the provider's channel filled in")
    provider: str = Field(..., description="The component the query reads, e.g. 'xfer' or 'ch1.probe'")
    via: Literal["self", "wire", "ref", "select"] = Field(
        ...,
        description="How the influence reaches the provider (the last step of path): 'self' (the "
        "component's own query), 'wire' (a signal wire), 'ref' (a component that uses it), 'select' "
        "(the provider picks its input channel with a setting, e.g. a waveform source — not a ref)",
    )
    path: list[str] = Field(
        default_factory=list,
        description="The components the influence passes, from the component itself to the provider "
        "(the shortest such path)",
    )
    terminal: bool = Field(
        False,
        description="The provider feeds nothing further: its queries are the instrument's results "
        "(waveform data, measurements), not the state of a component on the way",
    )
    selected_by: str | None = Field(
        None,
        description="For a channel component: the setting that picks which channel a component on the "
        "path reads (e.g. 'waveform.source'); the reading reflects this channel only when it is chosen",
    )


class InternalSelect(_Model):
    """A component that picks the channel it reads with a setting (#739), e.g. a waveform transfer's
    source: every channel reaches it, though no wire or ref says so."""

    component: str
    setting: str = Field(..., description="The setting that names the channel, e.g. 'waveform.source'")


class InternalComponent(_Model):
    name: str = Field(..., description="Unique within the instrument, e.g. 'ch1.front' or 'acq'")
    type: str
    channel: int | None = Field(
        None, description="Channel the component belongs to; none for the whole instrument"
    )
    doc: str | None = Field(
        None, description="What the component type does (first paragraph of its description)"
    )
    params: list[InternalParam] = Field(default_factory=list)
    ports: list[PortInfo] = Field(default_factory=list)
    settings: list[str] = Field(
        default_factory=list,
        description="The settings its parameters follow (binding), each once, in parameter order (#739)",
    )
    commands: list[InternalCommand] = Field(
        default_factory=list,
        description="SCPI commands that drive it: they write one of its settings or run its operations (#739)",
    )
    readings: list[InternalReading] = Field(
        default_factory=list,
        description="Query commands whose answer it influences: its own and those of components "
        "downstream of it (#739)",
    )


class ProbeFormat(_Model):
    """How a sample stream's numbers read (signal-chain.md 3.1)."""

    kind: Literal["volts", "codes", "iq", "bins"]
    bits: int = Field(0, description="The converter's resolution; 0 when the samples are not quantized")


class InternalWire(_Model):
    from_: str = Field(..., alias="from", description="'<component>.<port>'")
    to: str
    medium: Literal["signal", "samples"] = Field(
        "signal", description="'signal': analog (analytic); 'samples': a sample stream (digital)"
    )
    sample_rate_hz: float | None = Field(
        None, description="samples: the rate the stream runs at with the current settings; none when unknown"
    )
    format: ProbeFormat | None = Field(None, description="samples: the stream's format")


class InternalRef(_Model):
    """A component using another one (a ``refs`` parameter): not a signal wire, a dependency."""

    from_: str = Field(
        ..., alias="from", description="The component used (it feeds the one that uses it, e.g. 'adc')"
    )
    to: str = Field(..., description="The component that uses it (e.g. 'acq')")
    param: str = Field(..., description="The using component's parameter that names it, e.g. 'adc'")


class InternalPort(_Model):
    name: str = Field(..., description="Port on the instrument's panel, e.g. 'CH1'")
    target: str = Field(..., description="Component port it leads to, e.g. 'ch1.inp.pin'")
    direction: str
    medium: str
    channel: int | None = None


class TuneMark(_Model):
    """A frequency an internal probe can be tuned to besides the center: a marker that is on."""

    kind: Literal["marker", "reference"] = Field(
        ..., description="'marker': the marker; 'reference': the fixed reference of a delta marker"
    )
    index: int = Field(1, description="The marker's number, from 1")
    freq_hz: float


class InternalTuning(_Model):
    """The tuning context of a receiver (signal-chain.md 1.3, "tuned to"): the signal after its
    mixer depends on where the local oscillator is tuned, so an internal probe there is read at a
    tuning — the center frequency unless another is asked for."""

    center_hz: float = Field(..., description="Where the receiver is tuned outside a read: the default")
    min_hz: float | None = Field(None, description="Lowest tuning a probe can ask for; none: no limit")
    max_hz: float | None = Field(None, description="Highest tuning a probe can ask for; none: no limit")
    marks: list[TuneMark] = Field(default_factory=list, description="The markers that are on")
    ports: list[str] = Field(
        default_factory=list,
        description="Output ports ('<component>.<port>') whose signal depends on the tuning: the "
        "mixer's output and everything after it",
    )


class InstrumentInternals(_Model):
    """How an instrument is built from components (issue 20): read-only, the model's nominal values.

    Parameters show the definition's values; a bench's per-unit overrides are not part of the model.
    """

    instrument: str
    definition_id: str
    reference: Reference | None = Field(
        None,
        description="The basis the definition follows (D42): the block split is drawn from it, not "
        "from the model's actual circuit design; none for a definition without one",
    )
    channels: int
    components: list[InternalComponent]
    wires: list[InternalWire]
    refs: list[InternalRef] = Field(
        default_factory=list,
        description="Dependencies between components (a component names another in a refs parameter), "
        "from the one used to the one that uses it; not signal wiring",
    )
    selects: list[InternalSelect] = Field(
        default_factory=list,
        description="Components that pick their input channel with a setting (#739); not wiring, not refs",
    )
    ports: list[InternalPort]
    tuning: InternalTuning | None = Field(
        None, description="A receiver's tuning context with the current settings; none for other instruments"
    )


class WireInfo(_Model):
    index: int
    from_: str = Field(..., alias="from")
    to: str
    label: str | None = None
    accessories: list[ComponentInfo] = Field(default_factory=list)
    net: str | None = Field(None, description="Net of the 'from' end")


class NetInfo(_Model):
    id: str
    members: list[str]
    notes: list[str] = Field(
        default_factory=list, description="Why the signal on this net is not exact (empty when it is)"
    )


class ClientInfo(_Model):
    """A client connected to the session's SCPI ports (raw socket, HiSLIP or VXI-11)."""

    client: str = Field(..., description="Label as it appears in the communication log")
    instrument: str | None = Field(None, description="The instrument whose port it is connected to")
    locked: bool = Field(False, description="Holds a HiSLIP or VXI-11 lock, i.e. a script is in control")


class SessionInfo(_Model):
    id: str
    bench_name: str
    bench_path: str | None = None
    description: str | None = None
    clock_mode: Literal["command", "wall", "stepped", "scaled"]
    time_scale: float = Field(1.0, description="Virtual seconds per real second (scaled clock)")
    now_ns: int
    seed: int
    reveal_true_values: bool
    bench_sha256: str | None = Field(None, description="Hash of the bench file the session started from")
    bench_fingerprint: str | None = Field(
        None, description="Fingerprint of the bench content; differs from the file's when a restart is needed"
    )
    stale_definitions: list[str] = Field(
        default_factory=list,
        description="Instrument definitions changed on disk since the session started: restart to apply them",
    )
    record_path: str | None = None
    link_address: str | None = Field(
        None,
        description="host:port scripts in other processes connect to with PYVISA_LIBRARY=<this>@simubench "
        "(GPIB, USB and TCPIP addresses then work unchanged, D63)",
    )
    clients: list[ClientInfo] = Field(
        default_factory=list, description="Scripts currently connected to the SCPI ports"
    )
    instruments: list[InstrumentInfo]
    parts: list[ComponentInfo]
    duts: list[str] = Field(
        default_factory=list, description="Names of the instruments and parts marked as DUTs"
    )
    wires: list[WireInfo]
    nets: list[NetInfo]
    layout: dict[str, LayoutItem]


class BenchPreview(_Model):
    """How a bench file would look when started, without running it (the edit canvas).

    Parts that cannot be built (unknown model, bad wire) are left out and named in ``omitted``;
    the diagnostics of the document say why. Wire indices are those of the file.
    """

    path: str
    sha256: str
    session: SessionInfo | None = None
    omitted: list[str] = Field(
        default_factory=list, description="Instruments, parts, 'wire:<i>' or state paths"
    )
    error: str | None = Field(None, description="Why no preview could be built at all")


class FaultInfo(_Model):
    id: str
    status: Literal["pending", "active", "ended"]
    description: str
    source: str = Field(..., description="bench (preset), ui, tui, mcp or api")
    started_ns: int | None = None
    ends_ns: int | None = None
    remaining: int | None = Field(None, description="Messages left for a counted link fault")
    spec: Fault


class StartSessionRequest(_Model):
    bench_path: str
    clock: Literal["command", "wall", "stepped", "scaled"] | None = None
    time_scale: float | None = Field(
        None, gt=0, le=1_000_000, description="For the scaled clock: virtual seconds per real second"
    )
    seed: int | None = None
    force: bool = Field(
        False, description="Start even though scripts are connected to the running bench (they are dropped)"
    )


class ResetSessionRequest(_Model):
    """Power-cycle the bench: a fresh session (new record) with the same id and startup settings."""

    clock: Literal["command", "wall", "stepped", "scaled"] | None = None
    seed: int | None = None
    force: bool = Field(False, description="Reset even though scripts are connected (they are dropped)")


class ScpiRequest(_Model):
    command: str = Field(..., description="Program message, e.g. ':CHANnel1:SCALe 0.05' or '*IDN?'")


class ScpiResponse(_Model):
    response: str | None = None
    errors: list[ErrorItem] = Field(default_factory=list)
    timed_out: bool = Field(False, description="A query got no answer (as a VISA read would time out)")
    link_error: str | None = Field(None, description="The link to the instrument is down (a fault)")
    now_ns: int


class ConfigureRequest(_Model):
    path: str
    value: Any


class ConfigureResponse(_Model):
    ok: bool
    error: ErrorItem | None = None
    value: Any = None


class PanelRequest(_Model):
    action: Literal["run", "stop", "single"] = Field(
        description="A front-panel button (D32): not a SCPI command, no communication log, no virtual time"
    )


class PanelResponse(_Model):
    ok: bool
    error: ErrorItem | None = None


class AdvanceRequest(_Model):
    seconds: float = Field(..., gt=0)
    force: bool = Field(
        False, description="Advance even though scripts are connected (they see the time move)"
    )


class ClockInfo(_Model):
    mode: Literal["command", "wall", "stepped", "scaled"]
    now_ns: int
    scale: float = Field(1.0, description="Virtual seconds per real second: 1 except on a scaled clock")


class TimeScaleRequest(_Model):
    scale: float = Field(..., gt=0, le=1_000_000, description="Virtual seconds per real second")


class Trace(_Model):
    channel: int
    t0_s: float = Field(..., description="Time of the first sample relative to the trigger")
    dt_s: float
    volts: list[float]
    scale: float
    offset: float
    triggered: bool
    silence: list[SilenceReason] = Field(
        default_factory=list, description="Why the channel's input reads 0 V; empty when it carries a signal"
    )
    minmax: list[float] | None = Field(
        None,
        description="When the request passed ``points``: per bin the minimum and the maximum, 2 values "
        "per bin in order (``2*points`` values) — the pixel extraction of architecture.md 8.3 for a "
        "renderer with a fixed dot width. ``volts`` comes back empty then, so a terminal asks for "
        "exactly the bins it can draw and never receives all samples",
    )


class ScopeMeasurement(_Model):
    """One channel's automatic measurements on the record the screen shows (#280), taken by the
    scope's own measurement rules, so they agree with its :MEASure? replies on the same record.
    None: not measurable (fewer cycles than a period needs, a clipped record)."""

    channel: int
    vpp: float | None = None
    vmax: float | None = None
    vmin: float | None = None
    mean: float | None = None
    rms: float | None = None
    frequency: float | None = None
    period: float | None = None


class ScopeScreen(_Model):
    """What the scope's screen shows: while it runs, a frame of the signal now (not an acquisition:
    noise of its own, no virtual time); stopped or waiting for a single trigger, the last acquisition."""

    instrument: str
    source: Literal["live", "record"]
    running: bool = Field(description="Running in an auto or normal mode: the screen follows the signal")
    waiting: bool = Field(description="A single acquisition armed, waiting for a trigger")
    timebase_scale: float
    trigger_level: float
    trigger_source: int
    traces: list[Trace]
    acquisitions: int = Field(
        description="Acquisitions taken (commands, single, a stop); a running screen draws live frames, "
        "which are not acquisitions"
    )
    measurements: list[ScopeMeasurement] = Field(
        default_factory=list, description="Per displayed channel, on the record shown (#280)"
    )


class ReadingTrace(_Model):
    name: str
    t0_s: float = Field(..., description="Start relative to now")
    dt_s: float
    values: list[float]


class InstrumentReadings(_Model):
    """Front-panel readings, evaluated without side effects (no SCPI traffic, no error queue)."""

    instrument: str
    now_ns: int
    values: dict[str, float | int | bool | str] = Field(
        default_factory=dict, description="Keyed '<component>.<name>', e.g. 'demod.R'"
    )
    traces: list[ReadingTrace] = Field(default_factory=list)


class NetWaveform(_Model):
    net: str
    t0_ns: int
    dt_s: float
    volts: list[float]
    exact: bool
    notes: list[str] = Field(default_factory=list)


# SpectralLine, SourceLine and SourceContribution are the engine's own pydantic models (D57):
# defined next to the decomposition math (engine.circuit, engine.signal) and re-exported here, so
# the interface keeps one source and the probe assembles no field-by-field copies.


class ProbeSpectrum(_Model):
    """The amplitude spectrum of one frame. Real samples: a windowed FFT from 0 Hz to half the
    rate. Complex baseband samples (IQ): the two-sided windowed FFT, on the radio frequencies
    around the tuning. A transform's frequency points (bins): their levels as they are, on the
    radio frequencies they stand for — no second transform."""

    freq_hz: list[float]
    level: list[float]
    unit: Literal["dBFS", "dBV", "dBm"]


class InternalProbe(_Model):
    """The signal at one output port inside an instrument (signal-chain.md 6.1). Reading it changes
    nothing: not the clock, not the run record, not what a script reads next."""

    port: str = Field(..., description="'<component>.<port>', e.g. 'adc.out1'")
    medium: Literal["signal", "samples"]
    t_ns: int
    dc: float
    rms: float
    peak: float
    waveform: NetWaveform = Field(
        ..., description="signal: the analytic waveform; samples: the frame's samples, one value each"
    )
    quadrature: list[float] | None = Field(
        None,
        description="IQ samples only: the quadrature part of each sample (the waveform holds the in-phase part)",
    )
    lines: list[SpectralLine] = Field(default_factory=list, description="signal only: strongest lines")
    spectrum: ProbeSpectrum | None = Field(None, description="samples only")
    sample_rate_hz: float | None = None
    format: ProbeFormat | None = None
    full_scale_used: float | None = Field(
        None, description="Quantized samples: the frame's peak-to-peak span as a fraction of full scale"
    )
    clipped: bool = False
    tune_hz: float | None = Field(
        None,
        description="The local-oscillator tuning the port was read at (the one asked for, else the "
        "center frequency); none where the port's signal does not depend on a tuning",
    )
    notes: list[str] = Field(default_factory=list, description="Why the signal is not exact, if it is not")
    silence: list[SilenceReason] = Field(
        default_factory=list, description="Why it reads 0, traced upstream; empty when it does not"
    )


class NetProbe(_Model):
    """A virtual probe on a net (no loading, no instrument involved), and why it reads what it reads."""

    net: str
    members: list[str]
    t_ns: int
    dc: float
    rms: float
    peak: float
    lines: list[SpectralLine] = Field(..., description="Strongest lines, by amplitude")
    line_count: int
    waveform: NetWaveform
    sources: list[SourceContribution] = Field(..., description="By RMS contribution, largest first")
    notes: list[str] = Field(default_factory=list, description="Why the signal is not exact, if it is not")
    silence: list[SilenceReason] = Field(
        default_factory=list, description="Why the net reads 0 V; empty when it carries a signal"
    )


class NetLevel(_Model):
    """A net's level for the canvas: whether a signal is there, and a small picture of it."""

    net: str
    dc: float
    rms: float
    peak: float
    active: bool = Field(..., description="Carries a signal (not a flat 0 V)")
    window_s: float = Field(..., description="Span of ``wave``: two periods of the strongest line, or 2 ms")
    wave: list[float] = Field(..., description="A few dozen samples over ``window_s``, for a thumbnail")


class NetLevels(_Model):
    t_ns: int
    nets: list[NetLevel]


# ---- try a definition without a bench (7.2, T2) ---------------------------------------------------


class TryOpened(_Model):
    """A temporary session that serves the definition's instrument alone: no bench file, no SCPI
    ports, no run record, and no effect on an open bench session."""

    session_id: str
    definition_id: str
    instrument: str = Field(..., description="The instrument's name in the session: the definition id")
    now_ns: int


class StepResult(_Model):
    """One step of a case as it ran: the command, and (a query) what was expected and got."""

    index: int
    action: Literal["send", "query"]
    instrument: str
    command: str
    ok: bool
    expected: str | None = Field(None, description="A query: what the case expected (a query only)")
    actual: str | None = Field(None, description="A query: the reply, truncated like a failure report")
    error: str | None = Field(None, description="Why the step could not run at all")


class CaseRun(_Model):
    """One case as run: its outcome (as `simubench cases` reports it) and every step in order."""

    name: str
    manual: str | None = None
    passed: bool
    seconds: float
    step: int | None = Field(None, description="The failing step, counted from 1")
    command: str | None = None
    expected: str | None = None
    actual: str | None = None
    error: str | None = None
    steps: list[StepResult] = Field(default_factory=list)


class CaseRunRequest(_Model):
    path: str = Field(..., description="Workspace-relative cases file")


class CaseRunResult(_Model):
    path: str
    passed: int
    failed: int
    cases: list[CaseRun]


class CaseFile(_Model):
    """A cases file in the workspace whose model is the definition."""

    path: str = Field(..., description="Workspace-relative cases file")
    cases: int


# ---- run records (D36) ---------------------------------------------------------------------------


class RunSummary(_Model):
    run_id: str
    bench: str
    started: float = Field(..., description="Unix time the run started")
    ended: float | None = Field(None, description="Unix time it ended; null while the session runs")
    clock: str
    seed: int
    engine: str
    events: int
    duration_ns: int = Field(..., description="Virtual time the run covered")
    instruments: list[str]
    size: int = Field(..., description="Bytes on disk")


class RunDetail(_Model):
    summary: RunSummary
    events: list[RunEvent]
    truncated: bool = Field(False, description="More events than returned (the first ones are returned)")


class ReplayResult(_Model):
    run_id: str
    ok: bool
    actions: int
    replies: int
    mismatches: list[ReplayMismatch]
    warnings: list[str]


class AssetRegister(_Model):
    """The bench's virtual calibration register on its calendar (M5)."""

    calendar: str = Field(..., description="Calendar time now (run.start + virtual time), ISO 8601 UTC")
    assets: list[dict[str, Any]] = Field(..., description="Register entries, each with due_in_days")


# ---- copy a bench, my templates, definition restore (#874, #875) ---------------------------------


class CopyBenchRequest(_Model):
    path: str = Field(..., description="The bench to copy")
    new_path: str | None = Field(
        None, description="Workspace-relative file of the copy; default <stem>-copy.bench.yaml beside it"
    )
    name: str | None = Field(None, description="Name of the copy; default the original's with “（副本）”")


class RestoreDefinitionRequest(_Model):
    seq: int
    base_sha256: str


class SaveTemplateRequest(_Model):
    bench_path: str
    base_sha256: str = Field(..., description="sha256 of the bench as read")
    instrument: str = Field(..., description="The bench instrument to save")
    name: str = Field(
        ..., min_length=1, max_length=80, description="The template's name; the id is made from it"
    )


class SaveTemplateResponse(_Model):
    definition: DefinitionDocument = Field(..., description="The new template: a definition without extends")
    bench: BenchDocument = Field(..., description="The bench, its instrument now pointing at the template")
    based_on: str = Field(..., description="The model the template was expanded from")
    not_folded: list[str] = Field(
        default_factory=list,
        description="Initial settings that could not become defaults (channels set differently); "
        "they stay in the bench",
    )


# ---- the AI assistant (#871, D66, docs/ai-assistant.md) -------------------------------------------

AssistantProtocol = Literal["openai", "anthropic"]


class AssistantConfig(_Model):
    """The LLM the assistant talks to, as the settings page shows it: never the key itself."""

    protocol: AssistantProtocol = Field(
        "openai", description="openai: /v1/chat/completions compatible; anthropic: /v1/messages compatible"
    )
    base_url: str | None = Field(None, description="The service's address, e.g. http://llm.internal:8000/v1")
    model: str | None = None
    timeout_s: float = Field(60.0, description="Per request, seconds")
    context_tokens: int = Field(
        32000, description="The model's context length; older content is compacted before it fills"
    )
    api_key_set: bool = Field(False, description="Whether a key is stored; the key is write-only")
    configured: bool = Field(False, description="Address and model are set: the assistant can be used")
    overridden: list[str] = Field(
        default_factory=list,
        description="Fields the settings page set, over the server's defaults (environment or settings.json)",
    )


class AssistantConfigUpdate(_Model):
    """What the settings page saves; a field left out keeps its value."""

    protocol: AssistantProtocol | None = None
    base_url: str | None = None
    model: str | None = None
    timeout_s: float | None = Field(None, gt=0, le=600)
    context_tokens: int | None = Field(None, ge=1000, le=10_000_000)
    api_key: str | None = Field(None, description='A new key; "" removes the stored one; null keeps it')
    reset: bool = Field(False, description="Drop every override: back to the server's defaults")


class AssistantTestResult(_Model):
    ok: bool
    message: str = Field(..., description="What happened, in words; never contains the key")
    latency_ms: float | None = None


class AssistantStep(_Model):
    """One tool call of a turn, shown as a short progress line (“正在添加示波器”), never JSON."""

    action: str = Field(
        ...,
        description="What the step does: a tool name, or for bench edits the edit operation "
        "(add_instrument, connect, set_state, ...)",
    )
    subject: str | None = Field(None, description="What it acts on: an instrument class, a name, a file")
    ok: bool | None = Field(None, description="None while it runs")
    error: str | None = None


class AssistantFileChange(_Model):
    """What a turn changed in one file, in the bench's and instrument's language (no YAML)."""

    path: str
    kind: Literal["bench", "definition"]
    created: bool = False
    changes: list[DefinitionChange] = Field(default_factory=list)


class AssistantTurn(_Model):
    id: str
    user: str = Field(..., description="The user's message")
    reply: str = Field("", description="The assistant's answer, as streamed")
    steps: list[AssistantStep] = Field(default_factory=list)
    files: list[AssistantFileChange] = Field(
        default_factory=list, description="What changed: the summary under the answer"
    )
    status: Literal["running", "done", "stopped", "stuck", "error"] = "running"
    stop_reason: str | None = Field(
        None,
        description="Why the turn ended early, in words (stopped by the user, going in circles, an error)",
    )
    compacted: bool = Field(False, description="Older content was compacted during this turn")
    rolled_back: bool = Field(False, description="The user restored the files to before this turn")
    started_at: str
    finished_at: str | None = None


class AssistantConversation(_Model):
    bench: str
    turns: list[AssistantTurn]
    running: bool = False
    configured: bool = False
    compacted: bool = Field(
        False, description="Some earlier content is only kept in compacted form for the LLM"
    )


class AssistantMessageRequest(_Model):
    bench: str = Field(..., description="The bench the conversation belongs to")
    text: str = Field(..., min_length=1, max_length=20000)


class AssistantStopRequest(_Model):
    bench: str


class AssistantEvent(_Model):
    """One event of the stream (``GET /api/assistant/events``, server-sent events)."""

    seq: int
    type: Literal["text", "step", "step_done", "compacted", "turn"] = Field(
        ...,
        description="text: a piece of the answer; step / step_done: a tool call starts / ends; "
        "compacted: older content was compacted; turn: the turn as it stands (sent at the end)",
    )
    turn_id: str
    text: str | None = None
    step: AssistantStep | None = None
    index: int | None = Field(None, description="The step's index in the turn")
    turn: AssistantTurn | None = None


class RollbackFile(_Model):
    path: str
    kind: Literal["bench", "definition"]
    action: Literal["restore", "delete"] = Field(
        ..., description="restore: back to the version before the turn; delete: the turn created it"
    )
    modified_since: bool = Field(False, description="Changed after the turn (e.g. by hand on the canvas)")
    lost: list[DefinitionChange] = Field(
        default_factory=list,
        description="With modified_since: the changes made after the turn that would be lost",
    )


class RollbackPlan(_Model):
    turn_id: str | None = Field(None, description="The last turn; None when there is nothing to restore")
    files: list[RollbackFile] = Field(default_factory=list)
    sessions: list[str] = Field(
        default_factory=list, description="Running sessions that use one of the files: stopped first"
    )


class RollbackRequest(_Model):
    bench: str
    turn_id: str = Field(..., description="The last turn (the plan's turn_id)")
    confirm_lost: bool = Field(False, description="The user agreed to lose the changes made after the turn")
    stop_sessions: bool = Field(False, description="The user agreed to stop the sessions using the files")


class RollbackResult(_Model):
    done: bool
    needs: Literal["confirm_lost", "stop_sessions"] | None = Field(
        None, description="Not done yet: what the user has to agree to first"
    )
    plan: RollbackPlan
