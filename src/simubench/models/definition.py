"""Instrument definition file model (``simubench.instrument/1``).

An instrument definition declares the components an instrument is made of, how they are wired,
the settable state, and the SCPI command tree that maps onto that state and onto component
operations (architecture.md section 3). This Pydantic model is also the source of the JSON Schema
used to validate definition files (D21).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

StateType = Literal["float", "int", "bool", "enum", "string"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StateSpec(_Model):
    """One settable state variable."""

    type: StateType
    unit: str | None = Field(None, description="Base SCPI unit for suffix parsing, e.g. HZ, V, S, OHM")
    min: float | None = None
    max: float | None = None
    default: Any = None
    rst: Any = Field(None, description="Value after *RST; defaults to `default`")
    options: list[str] | None = Field(None, description="Enum options in SCPI mixed case, e.g. SINusoid")
    ladder: str | list[float] | None = Field(
        None, description="Snap writes to a ladder: '1-2-5', '1-3' or an explicit list of values"
    )
    special: dict[str, float] | None = Field(
        None, description="Extra keywords accepted for numbers, e.g. {INF: .inf} for a high-Z load"
    )
    electrical: bool = Field(False, description="Changing this variable changes the bench's circuit")
    readonly: bool = False
    label: str | None = None
    label_en: str | None = None

    @model_validator(mode="after")
    def _check(self) -> StateSpec:
        if self.type == "enum" and not self.options:
            raise ValueError("enum state needs options")
        return self


class ComponentSpec(_Model):
    name: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class ParamSpec(_Model):
    """A parameter of an operation command."""

    name: str
    type: Literal["float", "int", "bool", "enum", "string", "float_list", "block", "raw"]
    unit: str | None = None
    options: list[str] | None = None
    optional: bool = False
    min: float | None = None
    max: float | None = None


class CommandSpec(_Model):
    """One SCPI command. Exactly one of ``state`` or ``op`` is given."""

    header: str = Field(..., description="Header pattern, e.g. '[:SOURce<ch>]:FREQuency[:CW]'")
    state: str | None = Field(None, description="State path bound to the command, e.g. 'ch.frequency'")
    op: str | None = Field(None, description="Component operation, e.g. 'acq.digitize'")
    set: bool = True
    query: bool = True
    params: list[ParamSpec] = Field(default_factory=list)
    args: dict[str, Any] = Field(
        default_factory=dict, description="Constant arguments passed to the operation"
    )
    duration: str | float | None = Field(None, description="Virtual time the command consumes (D12)")
    dangerous: bool = False
    doc: str | None = None

    @model_validator(mode="after")
    def _check(self) -> CommandSpec:
        if (self.state is None) == (self.op is None):
            raise ValueError(f"command {self.header!r}: give exactly one of state or op")
        return self


class Identity(_Model):
    idn: str = "{manufacturer},{model},{serial},{firmware}"
    serial: str = "SB000001"
    firmware: str = "1.0.0"


class Channels(_Model):
    count: int = 1
    overrides: dict[int, dict[str, dict[str, Any]]] = Field(
        default_factory=dict,
        description="Per-channel overrides of channel_state specs, e.g. {3: {frequency: {max: 2e8}}}",
    )


class Wiring(_Model):
    channel: list[tuple[str, str]] = Field(
        default_factory=list, description="Signal wires inside each channel"
    )
    instrument: list[tuple[str, str]] = Field(
        default_factory=list, description="Signal wires at instrument level; '{n}' expands per channel"
    )


class Ports(_Model):
    channel: dict[str, str] = Field(default_factory=dict, description="'OUT{n}': 'out.pin'")
    instrument: dict[str, str] = Field(default_factory=dict)


class Display(_Model):
    template: str | None = Field(None, description="UI class template, e.g. 'oscilloscope'")
    summary: list[str] = Field(default_factory=list)
    channel_summary: list[str] = Field(default_factory=list)


class InstrumentDefinition(_Model):
    schema_: Literal["simubench.instrument/1"] = Field("simubench.instrument/1", alias="schema")
    id: str
    manufacturer: str
    model: str
    instrument_class: str = Field(..., alias="class")
    version: int = 1
    description: str | None = None
    identity: Identity = Field(default_factory=Identity)
    channels: Channels = Field(default_factory=Channels)
    state: dict[str, StateSpec] = Field(default_factory=dict)
    channel_state: dict[str, StateSpec] = Field(default_factory=dict)
    components: list[ComponentSpec] = Field(default_factory=list)
    channel_components: list[ComponentSpec] = Field(default_factory=list)
    wiring: Wiring = Field(default_factory=Wiring)
    ports: Ports = Field(default_factory=Ports)
    commands: list[CommandSpec] = Field(default_factory=list)
    display: Display = Field(default_factory=Display)
    capabilities: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
