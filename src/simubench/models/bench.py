"""Bench definition file model (``simubench.bench/1``), architecture.md section 4."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ClockMode = Literal["command", "wall", "stepped"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class InstrumentInstance(_Model):
    model: str = Field(..., description="Instrument definition id, e.g. 'siglent-sds2104xplus'")
    label: str | None = None
    address: str | None = Field(
        None, description="Resource address of the real bench, e.g. 'TCPIP0::192.168.1.20::5025::SOCKET' (D5)"
    )
    serial: str | None = Field(None, description="Overrides the definition's serial number")
    firmware: str | None = None
    idn: str | None = Field(
        None,
        description="Overrides the definition's *IDN? reply, with the same fields, e.g. "
        "'LECROY,WAVEPRO 254HD,{serial},{firmware}' (the model token a host program checks)",
    )
    state: dict[str, Any] = Field(
        default_factory=dict, description="Initial state, e.g. {'ch1.frequency': 1000}"
    )
    hidden: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Hidden state: component parameter overrides, e.g. {'ch1.out': {'z_out': 50.4}}",
    )


class ComponentInstance(_Model):
    type: str = Field(..., description="Component type, e.g. 'rc_lowpass'")
    label: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    hidden: dict[str, Any] = Field(default_factory=dict)


class Accessory(_Model):
    type: str = Field(..., description="Two-port component placed on the wire, e.g. 'passive_probe'")
    params: dict[str, Any] = Field(default_factory=dict)


class Wire(_Model):
    from_: str = Field(..., alias="from", description="Port reference 'instance.PORT'")
    to: str = Field(..., description="Port reference 'instance.PORT'")
    accessories: list[Accessory] = Field(default_factory=list)
    label: str | None = None


class LayoutItem(_Model):
    x: float = 0
    y: float = 0
    w: float | None = None
    h: float | None = None


class RunConditions(_Model):
    seed: int = 1
    clock: ClockMode = "command"
    reveal_true_values: bool = Field(False, description="Allow clients to read hidden state (D9)")
    start: dt.datetime = Field(
        dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        description="Calendar time at virtual t = 0 (instrument clocks, calibration due dates); fixed for repeatability",
    )


class Asset(_Model):
    """One entry of the virtual calibration register (an equipment list, ISO/IEC 17025 6.4.13)."""

    serial: str = Field(..., description="Serial number as *IDN? reports it")
    model: str | None = Field(None, description="Model, e.g. 'SB-DMM-1'")
    status: Literal["in_use", "out_of_service", "quarantined", "retired"] = "in_use"
    cal_date: dt.date | None = Field(None, description="Date of the last calibration")
    cal_due: dt.date | None = Field(None, description="Calibration due date")
    certificate: str | None = None
    notes: str | None = None


FaultKind = Literal["timeout", "disconnect", "garble", "latency", "busy", "error", "trip", "drift", "hidden"]


class Fault(_Model):
    """A fault: on the link to an instrument, in an instrument, or in a component (architecture.md 5.5).

    Link faults (``timeout``, ``disconnect``, ``garble``, ``latency``, ``busy``) act on every client's
    SCPI traffic to ``instrument``. ``error`` queues an error code and ``trip`` switches the
    instrument's outputs off with an error, both once. ``drift`` makes a component parameter
    drift by ``drift_per_s`` (relative, per second of virtual time); ``hidden`` sets it to
    ``value``. Both are undone when the fault ends.
    """

    kind: FaultKind
    instrument: str | None = Field(None, description="Instrument the fault acts on")
    component: str | None = Field(
        None, description="For drift / hidden: '<instrument>/<component>' or a DUT name, e.g. 'rc'"
    )
    param: str | None = Field(None, description="For drift / hidden: the component parameter, e.g. 'r'")
    value: Any = Field(None, description="For hidden: the new value")
    drift_per_s: float | None = Field(None, description="For drift: relative change per second, e.g. 1e-4")
    code: int | None = Field(None, description="For error / trip: SCPI error code (default -300)")
    message: str | None = None
    latency_s: float | None = Field(None, ge=0, le=60, description="For latency: added to every message")
    garble_rate: float | None = Field(None, gt=0, le=1, description="For garble: share of bytes corrupted")
    at_s: float | None = Field(None, ge=0, description="Virtual time to start; omitted: at once")
    duration_s: float | None = Field(None, gt=0, description="How long it lasts; omitted: until cleared")
    count: int | None = Field(None, ge=1, description="Link faults: number of messages affected")
    label: str | None = None


class BenchDefinition(_Model):
    schema_: Literal["simubench.bench/1"] = Field("simubench.bench/1", alias="schema")
    name: str
    description: str | None = None
    instruments: dict[str, InstrumentInstance] = Field(default_factory=dict)
    duts: dict[str, ComponentInstance] = Field(default_factory=dict)
    wires: list[Wire] = Field(default_factory=list)
    run: RunConditions = Field(default_factory=RunConditions)
    faults: list[Fault] = Field(default_factory=list, description="Faults preset for every run")
    assets: list[Asset] = Field(
        default_factory=list,
        description="Virtual calibration register: the instruments a test script may check before running",
    )
    layout: dict[str, LayoutItem] = Field(
        default_factory=dict, description="UI positions only; never affects simulation results"
    )
