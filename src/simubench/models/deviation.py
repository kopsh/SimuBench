"""Individual deviations (个体偏差): how far one unit's parameters sit from the model's nominal values,
drawn per bench seed within the limits of the data sheet (D3 layer 2, metrology-authoring.md 7).

A deviation is invisible to commands: only measuring finds it. The definition gives the range a
model's units fall in (``deviations``); a bench instance with ``deviation: from_specs`` draws its own
values, reproducibly from the bench seed; hidden values written in the bench take precedence.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DeviationRange(BaseModel):
    """The range one parameter's deviation falls in."""

    model_config = ConfigDict(extra="forbid")

    limit: float | dict[Any, float] = Field(
        ...,
        description="±limit. A number, or with `by` a table keyed by the values of the last `by` key "
        "(e.g. {0.1: 30, 1: 20} per range)",
    )
    unit: Literal["%", "ppm"] | None = Field(
        None,
        description="'%' or 'ppm': relative to the nominal value, value = nominal × (1 + d). Omitted: the "
        "limit is in the parameter's own unit, value = nominal + d (a parameter that is itself an error, "
        "e.g. gain_error_ppm, or an offset)",
    )
    distribution: Literal["uniform", "normal"] = Field(
        "uniform",
        description="uniform: anywhere within ±limit; normal: σ = limit / coverage, truncated at ±limit "
        "(a unit outside its specification is a fault, injected separately)",
    )
    coverage: float = Field(2.0, gt=0, description="normal: the coverage factor k of the limit (limit = k·σ)")
    by: dict[str, list[Any]] | None = Field(
        None,
        description="Draw one value per cell of a table, e.g. {function: [DC], range: [0.1, 1, 10]}: the "
        "parameter becomes the nested table {DC: {0.1: …, 1: …, 10: …}}",
    )

    @model_validator(mode="after")
    def _check(self) -> DeviationRange:
        if isinstance(self.limit, dict):
            if not self.by:
                raise ValueError("a table of limits needs `by`")
            last = list(self.by.values())[-1]
            missing = [v for v in last if limit_for(self, v) is None]
            if missing:
                raise ValueError(f"no limit for {missing} (the values of the last `by` key)")
        if self.by is not None and (not self.by or any(not v for v in self.by.values())):
            raise ValueError("`by` lists at least one value for each key")
        return self


class DeviationSpec(DeviationRange):
    """A definition's deviation: which component parameter, and its range."""

    param: str = Field(
        ...,
        pattern=r"^[^.]+(\.[^.]+)*\.[^.]+$",
        description="'<component>.<parameter>', e.g. 'meter.gain_error_ppm'; a channel component's name "
        "(e.g. 'out.z_out') draws one value per channel",
    )

    @property
    def component(self) -> str:
        return self.param.rsplit(".", 1)[0]

    @property
    def parameter(self) -> str:
        return self.param.rsplit(".", 1)[1]


def limit_for(rng: DeviationRange, cell: Any) -> float | None:
    """The ±limit of one table cell (keyed by the last `by` value), or the single limit."""
    if not isinstance(rng.limit, dict):
        return float(rng.limit)
    for key, value in rng.limit.items():
        if key == cell or str(key) == str(cell):
            return float(value)
        try:
            if float(key) == float(cell):
                return float(value)
        except (TypeError, ValueError):
            pass
    return None
