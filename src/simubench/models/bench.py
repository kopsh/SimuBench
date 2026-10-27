"""Bench definition file model (``simubench.bench/1``), architecture.md section 4."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ClockMode = Literal["command", "wall", "stepped"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class InstrumentInstance(_Model):
    model: str = Field(..., description="Instrument definition id, e.g. 'sb-dso-4'")
    label: str | None = None
    address: str | None = Field(
        None, description="Resource address of the real bench, e.g. 'TCPIP0::192.168.1.20::5025::SOCKET' (D5)"
    )
    serial: str | None = Field(None, description="Overrides the definition's serial number")
    firmware: str | None = None
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


class BenchDefinition(_Model):
    schema_: Literal["simubench.bench/1"] = Field("simubench.bench/1", alias="schema")
    name: str
    description: str | None = None
    instruments: dict[str, InstrumentInstance] = Field(default_factory=dict)
    duts: dict[str, ComponentInstance] = Field(default_factory=dict)
    wires: list[Wire] = Field(default_factory=list)
    run: RunConditions = Field(default_factory=RunConditions)
    layout: dict[str, LayoutItem] = Field(
        default_factory=dict, description="UI positions only; never affects simulation results"
    )
