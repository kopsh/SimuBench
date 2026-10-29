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
    option_labels: dict[str, str] | None = Field(
        None, description="How the UI shows enum options, e.g. {ONEMeg: '1 MΩ', FIFTy: '50 Ω'}"
    )
    display_unit: str | None = Field(
        None, description="Unit shown in the UI when it differs from `unit`, e.g. '×'"
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


class ResponseFormat(_Model):
    """How query responses are written (real models, D42): numbers, booleans and enumerations.

    The defaults are what ``sb-*`` instruments have always returned, e.g. ``+1.000000E+03``.
    """

    number: Literal["nr3", "nr2", "nr1"] = Field(
        "nr3", description="nr3: +1.000000E+03; nr2: +1000.000000; nr1: +1000 (rounded)"
    )
    digits: int = Field(6, ge=0, le=17, description="Digits after the decimal point (nr3 mantissa, nr2)")
    sign: bool = Field(True, description="Write '+' before non-negative numbers")
    exponent_digits: int = Field(2, ge=1, le=3, description="nr3: digits of the exponent, e.g. E+03 is 2")
    integer: Literal["plain", "signed"] = Field("plain", description="plain: 5; signed: +5")
    boolean: Literal["numeric", "word"] = Field("numeric", description="numeric: 1/0; word: ON/OFF")
    enum: Literal["short", "long", "as_defined"] = Field(
        "short", description="short: SIN; long: SINUSOID (upper case); as_defined: SINusoid, as in `options`"
    )
    error_code: Literal["plain", "signed"] = Field(
        "plain", description='Code in :SYSTem:ERRor? replies: plain: 0,"No error"; signed: +0,"No error"'
    )


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
    format: ResponseFormat | None = Field(None, description="Overrides the definition's response format")
    dangerous: bool = False
    doc: str | None = None

    @model_validator(mode="after")
    def _check(self) -> CommandSpec:
        if (self.state is None) == (self.op is None):
            raise ValueError(f"command {self.header!r}: give exactly one of state or op")
        return self


class Reference(_Model):
    """The manual a real-model definition follows (D42): behavior is checked against this edition."""

    title: str = Field(..., description="e.g. 'Keysight Truevolt Series Operating and Service Guide'")
    document: str | None = Field(None, description="Document or part number")
    version: str | None = Field(None, description="Edition, revision or date")
    firmware: str | None = Field(None, description="Firmware version the manual describes")


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
    media: dict[str, Literal["signal", "electrical", "reference"]] = Field(
        default_factory=dict,
        description="What an exposed port carries when it is not plain electrical, e.g. {REF_IN: reference} "
        "for a timebase or lock-in reference; the canvas draws such wires as reference wires",
    )

    @model_validator(mode="after")
    def _check_media(self) -> Ports:
        unknown = set(self.media) - set(self.instrument) - set(self.channel)
        if unknown:
            raise ValueError(f"media names ports that are not declared: {sorted(unknown)}")
        return self


class Display(_Model):
    template: str | None = Field(None, description="UI class template, e.g. 'oscilloscope'")
    summary: list[str] = Field(default_factory=list)
    channel_summary: list[str] = Field(default_factory=list)
    actions: dict[str, str] = Field(
        default_factory=dict,
        description="Commands the UI sends for front-panel actions, e.g. {single: ':TRIGger:MODE SINGle'}; "
        "defaults: single ':SINGle'",
    )


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
    # ---- real models (D42, docs/real-models.md) ----
    reference: Reference | None = Field(
        None,
        description="The manual this definition follows; a definition with a reference is a real model: "
        "unknown commands answer -113 'not modeled by SimuBench', error texts follow `errors`",
    )
    errors: dict[int, str] = Field(
        default_factory=dict,
        description="Error texts by code: overrides standard texts and adds device-specific codes",
    )
    builtins: list[str] | None = Field(
        None,
        description="Built-in headers this model answers (e.g. ['*IDN', '*OPC', '*RST']); the others are not "
        "modeled. Default: all of them",
    )
    error_queue: int | None = Field(
        None, ge=2, description="Error queue length; on overflow the last entry becomes -350 (SCPI-99 21.8)"
    )
    format: ResponseFormat = Field(default_factory=ResponseFormat, description="Default response format")
    specs: dict[str, Any] = Field(
        default_factory=dict,
        description="Accuracy and other specifications from the manual; stored, not yet used in calculations",
    )
    unknown_commands: Literal["error", "ignore"] = Field(
        "error",
        description="A header the definition does not have: 'error' queues -113; 'ignore' swallows it (no "
        "error, no reply) and logs an 'ignored' event. Only for definitions without `reference` (D42)",
    )

    @model_validator(mode="after")
    def _check_unknown(self) -> InstrumentDefinition:
        if self.unknown_commands == "ignore" and self.reference is not None:
            raise ValueError(
                "a real model (with `reference`) refuses unknown commands; unknown_commands: ignore"
            )
        return self

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
