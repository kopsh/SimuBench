"""HTTP and WebSocket message models of the local service.

These Pydantic models are the single source of the interface (D21): FastAPI derives the OpenAPI
description from them, and the web client's TypeScript types are generated from that.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..engine.edit import EditOp
from .bench import Fault, LayoutItem
from .definition import Panel


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


class Diagnostic(_Model):
    severity: Literal["error", "warning"]
    code: str = Field(..., description="Stable code, e.g. 'E-WIRE-PORT'")
    pointer: str = Field(..., description="JSON pointer into the bench file, e.g. '/wires/1/to'")
    message: str
    suggestion: str | None = None
    subject: str | None = Field(None, description="Instrument, DUT or wire the diagnostic is about")


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


class StateVarInfo(_Model):
    path: str
    type: str
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    options: list[str] | None = None
    option_labels: dict[str, str] | None = None
    display_unit: str | None = None
    label: str | None = None
    label_en: str | None = None
    readonly: bool = False
    electrical: bool = False
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
        description="Clients connected to this instrument's ports (socket, HiSLIP): while any is, the "
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


class InternalWire(_Model):
    from_: str = Field(..., alias="from", description="'<component>.<port>'")
    to: str


class InternalPort(_Model):
    name: str = Field(..., description="Port on the instrument's panel, e.g. 'CH1'")
    target: str = Field(..., description="Component port it leads to, e.g. 'ch1.inp.pin'")
    direction: str
    medium: str
    channel: int | None = None


class InstrumentInternals(_Model):
    """How an instrument is built from components (issue 20): read-only, the model's nominal values.

    Parameters show the definition's values; a bench's per-unit overrides are not part of the model.
    """

    instrument: str
    definition_id: str
    channels: int
    components: list[InternalComponent]
    wires: list[InternalWire]
    ports: list[InternalPort]


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
    """A client connected to the session's SCPI ports (raw socket or HiSLIP)."""

    client: str = Field(..., description="Label as it appears in the communication log")
    instrument: str | None = Field(None, description="The instrument whose port it is connected to")
    locked: bool = Field(False, description="Holds a HiSLIP lock, i.e. a script is in control")


class SessionInfo(_Model):
    id: str
    bench_name: str
    bench_path: str | None = None
    description: str | None = None
    clock_mode: Literal["command", "wall", "stepped"]
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
    source: str = Field(..., description="bench (preset), ui, mcp or api")
    started_ns: int | None = None
    ends_ns: int | None = None
    remaining: int | None = Field(None, description="Messages left for a counted link fault")
    spec: Fault


class StartSessionRequest(_Model):
    bench_path: str
    clock: Literal["command", "wall", "stepped"] | None = None
    seed: int | None = None
    force: bool = Field(
        False, description="Start even though scripts are connected to the running bench (they are dropped)"
    )


class ResetSessionRequest(_Model):
    """Power-cycle the bench: a fresh session (new record) with the same id and startup settings."""

    clock: Literal["command", "wall", "stepped"] | None = None
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
    mode: Literal["command", "wall", "stepped"]
    now_ns: int


class SilenceReason(_Model):
    """Why a port or net reads 0 V, found by tracing the circuit upstream."""

    kind: Literal["unconnected", "source_off", "zero_setting", "no_source", "fault"]
    path: list[str] = Field(
        default_factory=list,
        description="From the silent port to the source: instrument ports ('awg.OUT1') and bench components ('rc')",
    )
    source: str | None = Field(None, description="The source this reason is about, e.g. 'awg.OUT1'")
    fault_id: str | None = None
    detail: str = ""


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
    acquisitions: int


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


class SpectralLine(_Model):
    freq_hz: float
    amplitude: float = Field(..., description="Peak amplitude, V")


class SourceLine(_Model):
    freq_hz: float
    emf: float = Field(
        ..., description="Peak amplitude of the source at this frequency (V, or A for a current source)"
    )
    gain: float = Field(..., description="|transfer| from the source to the net")
    phase_deg: float = Field(..., description="Phase of the transfer, degrees")
    contribution: float = Field(..., description="Peak volts this source puts on the net at this frequency")


class SourceContribution(_Model):
    """One independent source's share of a net's signal: its EMF times the circuit's transfer."""

    source: str = Field(..., description="Source id, e.g. 'awg/ch1.out'")
    instrument: str | None = None
    component: str
    dc_emf: float
    dc_gain: float
    dc_contribution: float
    lines: list[SourceLine]
    rms_contribution: float


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


class Event(_Model):
    """One event of the session's event stream (WebSocket)."""

    seq: int
    t_ns: int
    kind: str = Field(
        ...,
        description="scpi | state | advance | error | fault | acquisition | panel | remote | session",
    )
    instrument: str | None = None
    dir: Literal["write", "read"] | None = None
    text: str | None = Field(None, description="SCPI bytes as text when printable")
    size: int | None = Field(None, description="Byte count of SCPI data")
    duration_ns: int | None = None
    client: str | None = None
    path: str | None = None
    old: Any = None
    new: Any = None
    source: str | None = None
    fault: str | None = Field(None, description="Fault id, for fault events and messages a fault affected")
    code: int | None = None
    message: str | None = None
    dt_ns: int | None = None
    reason: str | None = None
    wall: float | None = None
    action: str | None = Field(
        None,
        description="A front-panel button, for panel events: run | stop | single; "
        "for remote events: local (taken back from the UI) | remote (a remote client's command)",
    )


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


class RunEvent(Event):
    """An event as recorded; binary replies carry their hash and the acquisition that regenerates them."""

    sha256: str | None = None
    recipe: int | None = Field(None, description="seq of the acquisition event a waveform block came from")
    channels: dict[str, Any] | None = Field(None, description="Acquisition recipe, per channel")


class RunDetail(_Model):
    summary: RunSummary
    events: list[RunEvent]
    truncated: bool = Field(False, description="More events than returned (the first ones are returned)")


class ReplayMismatch(_Model):
    seq: int
    instrument: str
    t_ns: int
    expected: str
    actual: str
    command: str | None = None


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
