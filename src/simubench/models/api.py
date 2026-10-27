"""HTTP and WebSocket message models of the local service.

These Pydantic models are the single source of the interface (D21): FastAPI derives the OpenAPI
description from them, and the web client's TypeScript types are generated from that.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..engine.edit import EditOp
from .bench import LayoutItem


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


class BenchDocument(_Model):
    """A bench file's content as stored, with its diagnostics."""

    path: str
    sha256: str = Field(..., description="Hash of the file bytes; pass back as base_sha256 to save")
    data: dict[str, Any]
    ok: bool
    diagnostics: list[Diagnostic]


class SaveBenchRequest(_Model):
    path: str
    base_sha256: str | None = Field(
        ..., description="Hash the edit started from; null creates a new file (409 if it exists)"
    )
    data: dict[str, Any]


class EditBenchRequest(_Model):
    path: str
    base_sha256: str
    ops: list[EditOp]


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
    category: Literal["dut", "accessory", "internal"]
    label: str | None = None
    label_en: str | None = None
    doc: str | None = None
    ports: list[PortInfo]
    defaults: dict[str, Any]
    units: dict[str, str]


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
    scpi_port: int | None = None
    display: DisplayInfo
    ports: list[PortInfo]
    state: list[StateVarInfo]
    errors: list[ErrorItem]


class ComponentInfo(_Model):
    name: str
    type: str
    label: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    ports: list[PortInfo] = Field(default_factory=list)


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


class SessionInfo(_Model):
    id: str
    bench_name: str
    bench_path: str | None = None
    description: str | None = None
    clock_mode: Literal["command", "wall", "stepped"]
    now_ns: int
    seed: int
    reveal_true_values: bool
    record_path: str | None = None
    instruments: list[InstrumentInfo]
    duts: list[ComponentInfo]
    wires: list[WireInfo]
    nets: list[NetInfo]
    layout: dict[str, LayoutItem]


class StartSessionRequest(_Model):
    bench_path: str
    clock: Literal["command", "wall", "stepped"] | None = None
    seed: int | None = None


class ScpiRequest(_Model):
    command: str = Field(..., description="Program message, e.g. ':CHANnel1:SCALe 0.05' or '*IDN?'")


class ScpiResponse(_Model):
    response: str | None = None
    errors: list[ErrorItem] = Field(default_factory=list)
    now_ns: int


class ConfigureRequest(_Model):
    path: str
    value: Any


class ConfigureResponse(_Model):
    ok: bool
    error: ErrorItem | None = None
    value: Any = None


class AdvanceRequest(_Model):
    seconds: float = Field(..., gt=0)


class ClockInfo(_Model):
    mode: Literal["command", "wall", "stepped"]
    now_ns: int


class Trace(_Model):
    channel: int
    t0_s: float = Field(..., description="Time of the first sample relative to the trigger")
    dt_s: float
    volts: list[float]
    scale: float
    offset: float
    triggered: bool


class ScopeScreen(_Model):
    instrument: str
    source: Literal["record", "preview"]
    timebase_scale: float
    trigger_level: float
    trigger_source: int
    traces: list[Trace]
    acquisitions: int


class NetWaveform(_Model):
    net: str
    t0_ns: int
    dt_s: float
    volts: list[float]
    exact: bool
    notes: list[str] = Field(default_factory=list)


class Event(_Model):
    """One event of the session's event stream (WebSocket)."""

    seq: int
    t_ns: int
    kind: str = Field(..., description="scpi | state | advance | error | session")
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
    code: int | None = None
    message: str | None = None
    dt_ns: int | None = None
    reason: str | None = None
    wall: float | None = None
