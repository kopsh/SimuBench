"""Instrument definition file model (``simubench.instrument/1``).

An instrument definition declares the components an instrument is made of, how they are wired,
the settable state, and the SCPI command tree that maps onto that state and onto component
operations (architecture.md section 3). This Pydantic model is also the source of the JSON Schema
used to validate definition files (D21).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .deviation import DeviationSpec

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

    @model_validator(mode="before")
    @classmethod
    def _no_ratio_guess(cls, data: Any) -> Any:
        if isinstance(data, dict) and "ratio" in data:
            raise ValueError(
                "`ratio` is gone: a number is no longer read as a percentage when it is above 1 (0.1 % "
                "and 10 % would be the same). Write the setting in the unit the commands use (a ratio "
                "0..1, or a percentage with max 100) and bind it to the component as that unit, e.g. "
                "waveform_synth duty_unit: ratio (#144)"
            )
        return data

    @model_validator(mode="after")
    def _check(self) -> StateSpec:
        if self.type == "enum" and not self.options:
            raise ValueError("enum state needs options")
        return self


class ParamMapping(_Model):
    """A component parameter derived from a setting, for a dialect's values the component reads
    differently: ``{from: $ch.wave, map: {SINe: SINusoid}}``, ``{from: $ch.duty, scale: 100}``.

    The value of ``from`` is looked up in ``map`` (``default`` when it is not there, else it passes
    through), or through a numeric ``table`` (the largest entry not above the value), then numbers
    become ``value * scale + offset``. There is no expression language: what a table and a straight
    line cannot say belongs in a component.
    """

    from_: str = Field(..., alias="from", pattern=r"^\$", description="The setting, e.g. '$ch.wave'")
    map: dict[str, Any] | None = Field(None, description="Setting value -> parameter value")
    table: dict[float, Any] | None = Field(
        None,
        description="Setting value -> parameter value for numbers: the largest table key not above "
        "the value wins (NPLC 0.5 uses the 0.2 entry)",
    )
    scale: float | None = None
    offset: float | None = None
    default: Any = Field(None, description="The parameter value for a setting value `map` does not list")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ComponentSpec(_Model):
    name: str
    type: str
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameter values: a literal, a setting ('$ch.frequency') or a mapping of a setting "
        "({from, map, scale, offset, default})",
    )

    @model_validator(mode="after")
    def _check_mappings(self) -> ComponentSpec:
        for value in self.params.values():
            if isinstance(value, dict) and "from" in value:
                ParamMapping.model_validate(value)
        return self


class QuantityUnit(_Model):
    """What one unit of a ``quantities`` parameter means (metrology-authoring.md 5.2): the setting
    the value writes, and further settings written at the same time."""

    state: str = Field(..., description="The setting the value writes, e.g. 'out.level'")
    sets: dict[str, Any] = Field(
        default_factory=dict,
        description="Further settings written along with it, e.g. {out.unit: V} or {out.function: DCI}",
    )


class ParamSpec(_Model):
    """A parameter of an operation command."""

    name: str
    type: Literal[
        "float", "int", "bool", "enum", "string", "float_list", "block", "raw", "pairs", "quantities"
    ]
    unit: str | None = None
    options: list[str] | None = None
    optional: bool = False
    min: float | None = None
    max: float | None = None
    state: str | None = Field(
        None,
        description="Setting this parameter writes (a command without `state` or `op`); it is parsed "
        "as that setting reads values",
    )
    keys: dict[str, str | None] | None = Field(
        None,
        description="type pairs: 'name,value,name,value...'; each name writes the setting it maps to, "
        "null accepts the name and ignores its value. All values are checked before any is set",
    )
    index: bool = Field(
        False,
        description="An int that picks which settings the other parameters write: their state paths name "
        "it in braces, e.g. 'measure.p{slot}.kind' (PACU 1,AMPL,C1); a query takes it too (PACU? 1)",
    )
    units: dict[str, QuantityUnit] | None = Field(
        None,
        description="type quantities: comma-separated 'value unit' parameters, each written into the "
        "setting its unit names, e.g. {V: {state: out.level}, HZ: {state: out.frequency}}; the "
        "multiplier moves into the value (mV, kHz)",
    )
    combine: dict[str, dict[str, Any]] | None = Field(
        None,
        description="type quantities: what a combination of units writes besides, keyed by the units "
        "joined with commas in the `units` table's order ('V,HZ'); a value may be conditional, "
        "{if_zero: <state>, then: ..., else: ...}. A combination not listed is refused",
    )
    missing_unit_error: int = Field(
        -131, description="Error code for a value without a unit (the text comes from `errors`)"
    )
    unknown_unit_error: int = Field(-131, description="Error code for a unit the `units` table does not name")
    duplicate_unit_error: int = Field(-224, description="Error code for a unit given twice in one command")
    combine_error: int = Field(
        -224, description="Error code for a combination of units `combine` does not list"
    )

    @model_validator(mode="after")
    def _check(self) -> ParamSpec:
        if (self.type == "pairs") != (self.keys is not None):
            raise ValueError(f"parameter {self.name!r}: keys go with type pairs, and pairs need keys")
        if self.index and (self.type != "int" or self.state is not None):
            raise ValueError(f"parameter {self.name!r}: an index is an int and writes no setting itself")
        if self.type == "quantities":
            if not self.units:
                raise ValueError(f"parameter {self.name!r}: quantities need a units table")
            if self.state is not None or self.keys is not None or self.index:
                raise ValueError(
                    f"parameter {self.name!r}: quantities write through units, no state/keys/index"
                )
        elif self.units is not None or self.combine is not None:
            raise ValueError(f"parameter {self.name!r}: units and combine go with type quantities")
        return self


class ResponseFormat(_Model):
    """How query responses are written (real models, D42): numbers, booleans and enumerations.

    The defaults are what ``sb-*`` instruments have always returned, e.g. ``+1.000000E+03``.
    """

    number: Literal["nr3", "nr2", "nr1", "engineering"] = Field(
        "nr3",
        description="nr3: +1.000000E+03; nr2: +1000.000000; nr1: +1000 (rounded); engineering: 1E+3, "
        "200E-3 (exponent a multiple of 3, shortest mantissa)",
    )
    digits: int = Field(6, ge=0, le=17, description="Digits after the decimal point (nr3 mantissa, nr2)")
    significant: int | None = Field(
        None,
        ge=1,
        le=17,
        description="engineering with this many significant digits, signed and with exponent_digits: "
        "+40.0E+00 (3), +0.00000E+00 (6); a command's own format may set its own",
    )
    sign: bool = Field(True, description="Write '+' before non-negative numbers")
    exponent_digits: int = Field(2, ge=1, le=3, description="nr3: digits of the exponent, e.g. E+03 is 2")
    integer: Literal["plain", "signed", "engineering"] = Field(
        "plain", description="plain: 5; signed: +5; engineering: 100E+3"
    )
    boolean: Literal["numeric", "word"] = Field("numeric", description="numeric: 1/0; word: ON/OFF")
    enum: Literal["short", "long", "as_defined"] = Field(
        "short", description="short: SIN; long: SINUSOID (upper case); as_defined: SINusoid, as in `options`"
    )
    error_code: Literal["plain", "signed"] = Field(
        "plain", description='Code in :SYSTem:ERRor? replies: plain: 0,"No error"; signed: +0,"No error"'
    )


class CommandSpec(_Model):
    """One SCPI command: bound to a setting (``state``), to a component operation (``op``), or, through
    its parameters' ``state``, to several settings at once."""

    header: str = Field(..., description="Header pattern, e.g. '[:SOURce<ch>]:FREQuency[:CW]'")
    state: str | None = Field(None, description="State path bound to the command, e.g. 'ch.frequency'")
    op: str | None = Field(None, description="Component operation, e.g. 'acq.digitize'")
    set: bool = True
    query: bool = True
    params: list[ParamSpec] = Field(default_factory=list)
    args: dict[str, Any] = Field(
        default_factory=dict, description="Constant arguments passed to the operation"
    )
    factor: float = Field(
        1.0,
        description="The command's number is the bound setting times factor: :TIMebase:RANGe is "
        "factor 10 on the time/div setting; a write divides by it",
    )
    duration: str | float | None = Field(None, description="Virtual time the command consumes (D12)")
    format: ResponseFormat | None = Field(None, description="Overrides the definition's response format")
    reply: str | None = Field(
        None,
        description="Query reply template: {header} (the echoed header, see dialect.echo_header; with "
        "echo off it and the space after it are left out), {value} (the bound setting, or an "
        "operation's reply) and {<setting>}, e.g. '{header} {value} V'. Default: '{header} {value}' for "
        "settings; an operation's reply as it is",
    )
    when: dict[str, str] | None = Field(
        None,
        description="Use this command only when parameters have these values, e.g. {name: DATA_ARRAY_1}: "
        'several commands may share a header, told apart by a parameter (C1:INSPECT? "WAVEDESC")',
    )
    dangerous: bool = False
    doc: str | None = None

    @property
    def bound_params(self) -> list[ParamSpec]:
        return [p for p in self.params if p.state is not None or p.keys is not None or p.type == "quantities"]

    @model_validator(mode="after")
    def _check(self) -> CommandSpec:
        kinds = (self.state is not None) + (self.op is not None) + bool(self.bound_params)
        if kinds != 1:
            raise ValueError(
                f"command {self.header!r}: give one of state, op, or parameters bound to settings"
            )
        indexes = [p for p in self.params if p.index]
        if self.bound_params and len(self.bound_params) + len(indexes) != len(self.params):
            raise ValueError(f"command {self.header!r}: bind every parameter to a setting, or none")
        if indexes and not self.bound_params:
            raise ValueError(f"command {self.header!r}: an index parameter picks the settings others write")
        quantities = [p for p in self.params if p.type == "quantities"]
        if quantities:
            if self.state is not None or self.op is not None:
                raise ValueError(
                    f"command {self.header!r}: a quantities parameter writes the settings itself, "
                    "no state or op beside it"
                )
            if len(quantities) > 1 or len(self.params) > 1:
                raise ValueError(f"command {self.header!r}: a quantities parameter is the only parameter")
            if self.query and self.reply is None:
                raise ValueError(
                    f"command {self.header!r}: a quantities query answers through its reply template"
                )
        names = {p.name for p in self.params}
        for key in self.when or {}:
            if key not in names:
                raise ValueError(f"command {self.header!r}: when names {key!r}, which is not a parameter")
        return self


class Dialect(_Model):
    """How replies are framed, for instruments that are not plain SCPI (e.g. LeCroy's ``C1:VDIV 200E-3 V``).

    The defaults are SCPI: no header in replies, numbers without multiplier suffixes.
    """

    echo_header: str = Field(
        "off",
        pattern=r"^(off|short|long|\$.+)$",
        description="Replies start with the command header: off, short (C1:VDIV), long (C1:VOLT_DIV), or "
        "a setting whose value is OFF, SHORT or LONG ('$comm.header', switched by COMM_HEADER)",
    )
    multipliers: dict[str, float] = Field(
        default_factory=dict,
        description="Suffixes of numbers written without a unit, e.g. {K: 1.0e3, M: 1.0e6} for 'MSIZ 10K'",
    )


class Reference(_Model):
    """The manual a real-model definition follows (D42): behavior is checked against this edition."""

    title: str = Field(..., description="e.g. 'Keysight Truevolt Series Operating and Service Guide'")
    document: str | None = Field(None, description="Document or part number")
    version: str | None = Field(None, description="Edition, revision or date")
    firmware: str | None = Field(None, description="Firmware version the manual describes")


class TemplateSource(_Model):
    """The template a definition was expanded from and the inputs it was given (instrument-authoring.md
    7.4, D3). A record only: loading and running ignore it; expanding the inputs again reproduces the
    definition as the template generated it."""

    id: str = Field(..., description="Template id, e.g. 'oscilloscope'")
    version: int = Field(..., ge=1, description="Template version the inputs were expanded with")
    inputs: dict[str, Any] = Field(..., description="The inputs, in the template's data-sheet language")


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


class PanelKey(_Model):
    """One key of a button group: the value it writes, and what it reads on the instrument."""

    value: Any = Field(..., description="The value the key writes to the control's setting")
    label: str | None = Field(None, description="The key's text; default: the option's label or the value")
    label_en: str | None = None
    sets: dict[str, Any] = Field(
        default_factory=dict,
        description="Further settings written along with it, e.g. {sense.range_auto: false} for a range "
        "key; the key is lit while all of them hold",
    )


class PanelControl(_Model):
    """One setting on the panel, and the control it is set with (instrument-authoring.md 7.3)."""

    state: str = Field(..., description="The setting, e.g. 'sense.function' (a channel's: 'ch1.level')")
    control: Literal["buttons", "number", "switch", "select"] = Field(
        ...,
        description="buttons: a key per value (an enum's options, or `keys`); number: a value with its "
        "unit and step keys; switch: on / off (bool); select: a drop-down (enum)",
    )
    label: str | None = Field(None, description="Default: the setting's label")
    label_en: str | None = None
    keys: list[PanelKey] | None = Field(
        None, description="buttons: the keys; default for an enum: one per option, for a bool: on and off"
    )
    step: float | None = Field(None, gt=0, description="number: what the step keys add and subtract")
    unit: str | None = Field(None, description="number: the unit shown; default: the setting's")


class PanelReadout(_Model):
    """A reading on the main display."""

    reading: str = Field(
        ..., description="A reading of the instrument, '<component>.<reading>', e.g. 'meter.DC'"
    )
    label: str | None = None
    label_en: str | None = None
    unit: str | None = Field(None, description="The reading's unit, e.g. V; none for a count or a word")
    digits: int | None = Field(None, ge=1, le=12, description="Significant digits shown")
    when: dict[str, Any] = Field(
        default_factory=dict,
        description="Shown only while these settings have these values, e.g. {sense.function: VOLT}",
    )


class PanelGroup(_Model):
    label: str = Field(..., description="The group's title, e.g. 测量, 量程, 触发")
    label_en: str | None = None
    controls: list[PanelControl] = Field(default_factory=list)


class PanelSoftkey(_Model):
    """A key under the display: a front-panel action (D32), or a setting written to a value."""

    label: str
    label_en: str | None = None
    action: Literal["run", "stop", "single"] | None = Field(
        None, description="A front-panel action, as `display.actions` names them"
    )
    state: str | None = Field(None, description="The setting the key writes")
    value: Any = None
    sets: dict[str, Any] = Field(default_factory=dict, description="Further settings written along with it")

    @model_validator(mode="after")
    def _check(self) -> PanelSoftkey:
        if (self.action is None) == (self.state is None):
            raise ValueError("a softkey has either `action` or `state`")
        return self


class Panel(_Model):
    """The instrument's front panel as the UI draws it (instrument-authoring.md 7.3): the main
    display's readings, groups of controls, and softkeys. Without one, a multimeter and a calibrator
    get their class's default layout; the classes with a dedicated screen do not use it."""

    readouts: list[PanelReadout] = Field(default_factory=list)
    groups: list[PanelGroup] = Field(default_factory=list)
    softkeys: list[PanelSoftkey] = Field(default_factory=list)


class Display(_Model):
    template: str | None = Field(None, description="UI class template, e.g. 'oscilloscope'")
    summary: list[str] = Field(default_factory=list)
    channel_summary: list[str] = Field(default_factory=list)
    actions: dict[str, str] = Field(
        default_factory=dict,
        description="Front-panel buttons as the model's own commands, run inside the instrument (not over "
        "SCPI, D32), e.g. {single: ':TRIGger:MODE SINGle'}; run, stop and single default to the acquisition "
        "component's operations",
    )
    panel: Panel | None = Field(
        None,
        description="Front-panel layout: readings on the main display, grouped controls, softkeys "
        "(instrument-authoring.md 7.3)",
    )


class InstrumentDefinition(_Model):
    schema_: Literal["simubench.instrument/1"] = Field("simubench.instrument/1", alias="schema")

    #: The file the definition was loaded from, as the loader received the path; the loader sets it
    #: (events that classify a command name the declaration's file, tui-design.md 1.1). Definitions
    #: built in memory have none.
    _source_file: Path | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _expand_macros(cls, data: Any) -> Any:
        """A component written the earlier, one-component way expands into its modules when the
        definition loads, wherever it is loaded from (instrument-authoring.md 8.1)."""
        from ..library.macros import expand_macros  # the component library needs the models first

        return expand_macros(data)

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
    template: TemplateSource | None = Field(
        None,
        description="The template and inputs this definition was expanded from (a record; not used when "
        "loading or running)",
    )
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
    dialect: Dialect = Field(
        default_factory=Dialect, description="Reply framing beyond SCPI (headers, suffixes)"
    )
    specs: dict[str, Any] = Field(
        default_factory=dict,
        description="Accuracy and other specifications from the manual, as written there; a record, not "
        "used in calculations (the deviations a unit draws are in `deviations`)",
    )
    deviations: list[DeviationSpec] = Field(
        default_factory=list,
        description="Parameters a unit deviates in, and their ranges: a bench instance with deviation: "
        "from_specs draws its own values from the bench seed (个体偏差, metrology-authoring.md 7)",
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
