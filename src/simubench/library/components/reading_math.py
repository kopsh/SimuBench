"""The reading math of a meter (#1041): what a multimeter does with a reading after its A/D.

``reading_math`` sits between a meter's sampler (``dmm_adc``) and whoever takes its readings — the
trigger model and the measurement commands — and is a sampler itself. One reading goes through,
each step switched on by its own setting:

1. **average** — the mean of ``average_count`` readings: moving (each reading the mean of the last
   N, the window refilling after a change) or repeat (each reading the mean of N new readings, so
   one reading takes N times as long);
2. **math** — ``(m·x − c) / z``, each factor on its own (Fluke 8588A ``CALC:MATH``; a null is
   ``c``, an ``mx+b`` scaling is ``m`` with ``c = −b``). A factor can be taken from the last
   reading (8588A ``LREading``, #1043): a factor written as :data:`LAST_READING` (the
   definition's ``special: {LRE: -.inf, LREADING: -.inf}``) becomes the last reading — the reading
   after the average and before the math, as the block last saw it — at the moment of the write,
   and stays that number; with no reading since power-on or ``*RST`` the write is refused (-230);
3. **units** — percent (×100), dB (``20·log10|y|``, relative to 1) or dBm into a reference
   impedance (``10·log10(y²/R / 1 mW)``);
4. **limits** — a lower and an upper limit, each switched on by itself: a reading outside an
   enabled limit sets the fail flag (kept until cleared) and pushes the status event ``LIMIT``;
5. **statistics** — count, mean, minimum, maximum, span and sample standard deviation of the
   readings since the statistics were cleared (a new run clears them when ``clear_on_run``).

With every step off a reading passes unchanged, bit for bit. An overload reading
(``overload_value`` or beyond) passes unchanged and is left out of the average and the statistics;
it is still limit-tested as it is (#1044): +overload is above an enabled upper limit, −overload
below an enabled lower one, and either sets the fail flag. It becomes the last reading too.
The steps are pure module-level functions; the block holds only the window and the statistics.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, Sampler, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError

#: Reference impedances of the dBm units (Ω).
DBM_UNITS = {"DB50": 50.0, "DB75": 75.0, "DB600": 600.0}
UNITS = ("NONE", "PERCent", "DB", *DBM_UNITS)
#: The value a factor (m, c, z) is written as to take it from the last reading (LREading, #1043).
LAST_READING = -math.inf
FACTORS = ("m", "c", "z")


def apply_math(x: float, *, m: float | None, c: float | None, z: float | None) -> float:
    """``(m·x − c) / z`` with the factors that are on (None: off)."""
    y = x * m if m is not None else x
    if c is not None:
        y -= c
    if z is not None:
        if z == 0:
            raise ScpiError(-222, "math divisor z is 0")
        y /= z
    return y


def apply_units(y: float, units: str) -> float:
    """The value in the math units: percent, dB relative to 1, or dBm into a reference impedance."""
    if units == "PERCent":
        return 100.0 * y
    if units == "DB":
        return 20.0 * math.log10(abs(y)) if y != 0 else -math.inf
    if units in DBM_UNITS:
        p = y * y / DBM_UNITS[units]
        return 10.0 * math.log10(p / 1e-3) if p > 0 else -math.inf
    return y


def outside(y: float, *, lower: float | None, upper: float | None) -> bool:
    """Whether the reading is outside the enabled limits (None: that limit is off)."""
    return (lower is not None and y < lower) or (upper is not None and y > upper)


@component("reading_math")
class ReadingMath(Component):
    """A meter's math after the A/D: average, (m·x − c)/z, percent/dB/dBm, limits and statistics.

    A sampler between the meter's A/D (``source``) and its trigger model; ``measure`` replaces the
    A/D's for the measurement commands. Model limits: the statistics are those of the readings that
    passed through since they were cleared, not a recomputation over the reading memory.
    """

    refs = {"source": (Sampler, "instrument")}
    defaults = {
        "source": "",  # the sampler whose readings it processes (the meter's A/D)
        "digits": 7,  # digits of a reading it answers
        "overload_value": 9.9e37,  # a reading this large (or larger) is an overload and passes unchanged
        "average": False,  # the averaging filter is on (a setting)
        "average_count": 1,  # readings in one average (a setting)
        "average_mode": "MOVing",  # MOVing: rolling window; REPeat: block of new readings (a setting)
        "math": False,  # the math is on (a setting); off, the factors and units are not applied
        "m": 1.0,  # multiplier m (a setting)
        "m_on": False,  # multiply by m (a setting)
        "c": 0.0,  # offset c subtracted after m (a setting)
        "c_on": False,  # subtract c (a setting)
        "z": 1.0,  # divisor z (a setting)
        "z_on": False,  # divide by z (a setting)
        "units": "NONE",  # NONE, PERCent, DB, DB50, DB75 or DB600 (a setting)
        "units_on": False,  # convert to the units (a setting)
        "limits": False,  # the limit test is on (a setting)
        "lower": 0.0,  # lower limit (a setting)
        "lower_on": False,  # test the lower limit (a setting)
        "upper": 0.0,  # upper limit (a setting)
        "upper_on": False,  # test the upper limit (a setting)
        "statistics": False,  # keep statistics of the readings (a setting)
        "clear_on_run": True,  # a new run (INITiate, READ?) clears the statistics and the window
    }
    choices = {"average_mode": ("MOVing", "REPeat"), "units": UNITS}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.window: deque[float] = deque()
        self.samples: list[float] = []
        self.failed = False
        self.last: float | None = None  # the last reading (after the average, before the math)

    def reset(self) -> None:
        self.window.clear()
        self.samples.clear()
        self.failed = False
        self.last = None

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> Any:
        if key in ("average", "average_count", "average_mode"):
            self.window.clear()  # the filter restarts after a change, as the instrument's does
        if key == "statistics" and V.flag(new) and not V.flag(old):
            self.samples.clear()
        if key in FACTORS and new == LAST_READING:
            if self.last is None:
                raise ScpiError(-230, f"{self.uid}: no last reading for the math factor {key}")
            return self.last
        return None

    # ---- the steps -------------------------------------------------------------------------------

    def _source(self) -> Sampler:
        comp = self.ref("source")
        if not isinstance(comp, Sampler):
            raise ScpiError(-300, f"{self.uid}: source {self.param('source')!r} takes no readings")
        return comp  # type: ignore[return-value]

    def _overload(self, x: float) -> bool:
        return not math.isfinite(x) or abs(x) >= float(self.param("overload_value"))

    def _count(self) -> int:
        return max(1, int(self.param("average_count"))) if V.flag(self.param("average")) else 1

    def _repeat(self) -> bool:
        return V.flag(self.param("average")) and self.choice("average_mode") == "REPeat"

    def _filter(self, x: float) -> float:
        """The moving average (the repeat average is taken where the readings are)."""
        if not V.flag(self.param("average")) or self._repeat():
            return x
        n = self._count()
        self.window.append(x)
        while len(self.window) > n:
            self.window.popleft()
        return float(np.mean(self.window))

    def _factor(self, key: str) -> float | None:
        return float(self.param(key)) if V.flag(self.param(f"{key}_on")) else None

    def _limit_test(self, y: float) -> None:
        """Set the fail flag (and push ``LIMIT``) when the reading is outside an enabled limit."""
        if not V.flag(self.param("limits")):
            return
        lower = float(self.param("lower")) if V.flag(self.param("lower_on")) else None
        upper = float(self.param("upper")) if V.flag(self.param("upper_on")) else None
        if outside(y, lower=lower, upper=upper):
            self.failed = True
            if self.owner is not None:
                self.owner.status_event("LIMIT")

    def process(self, x: float) -> float:
        """One reading through the enabled steps (the filtered value in, the reported value out)."""
        if self._overload(x):
            self.last = x
            self._limit_test(x)
            return x
        y = self._filter(x)
        self.last = y
        if V.flag(self.param("math")):
            y = apply_math(y, m=self._factor("m"), c=self._factor("c"), z=self._factor("z"))
            if V.flag(self.param("units_on")):
                y = apply_units(y, self.choice("units") or "NONE")
        self._limit_test(y)
        if V.flag(self.param("statistics")) and math.isfinite(y):
            self.samples.append(y)
        return y

    def _take(self, read: Any) -> float:
        """A raw reading from the source, the repeat average of N of them when that filter is on."""
        if not self._repeat():
            return float(read(0))
        values = [float(read(i)) for i in range(self._count())]
        bad = [v for v in values if self._overload(v)]
        return bad[0] if bad else float(np.mean(values))

    # ---- a Sampler for trigger_model -------------------------------------------------------------

    def sample_time_ns(self) -> int:
        n = self._count() if self._repeat() else 1
        return n * int(self._source().sample_time_ns())

    def sample(self, index: int) -> float:
        source = self._source()
        return self.process(self._take(lambda i: source.sample(index * self._count() + i)))

    def run_begin(self) -> None:
        if V.flag(self.param("clear_on_run")):
            self.window.clear()
            self.samples.clear()
        hook = getattr(self._source(), "run_begin", None)
        if hook is not None:
            hook()

    def run_done(self) -> None:
        hook = getattr(self._source(), "run_done", None)
        if hook is not None:
            hook()

    def run_samples(self) -> int | None:
        hook = getattr(self._source(), "run_samples", None)
        return hook() if hook is not None else None

    def sample_fields(self, index: int) -> dict[str, Any] | None:
        hook = getattr(self._source(), "sample_fields", None)
        return hook(index) if hook is not None else None

    # ---- statistics ------------------------------------------------------------------------------

    def statistics(self) -> dict[str, float]:
        s = self.samples
        if not s:
            return {"count": 0, "mean": 0.0, "minimum": 0.0, "maximum": 0.0, "span": 0.0, "sdev": 0.0}
        return {
            "count": len(s),
            "mean": float(np.mean(s)),
            "minimum": float(min(s)),
            "maximum": float(max(s)),
            "span": float(max(s) - min(s)),
            "sdev": float(np.std(s, ddof=1)) if len(s) > 1 else 0.0,
        }

    def readings(self) -> dict[str, Any]:
        return {"values": {**self.statistics(), "limit_fail": self.failed}}

    def status_flags(self) -> dict[str, bool]:
        return {"LIMIT": self.failed}

    # ---- operations ------------------------------------------------------------------------------

    def _number(self, value: float) -> bytes:
        return self.reply_number(value, int(self.param("digits")))

    @op("measure")
    def op_measure(self, what: str | None = None, **_: Any) -> OpResult:
        """A measurement command's reading (MEASure?): the source's, through the enabled steps."""
        source = self._source()
        measure = getattr(source, "measure", None)
        read = (lambda _i: measure(what)) if measure is not None else source.sample
        return OpResult(self._number(self.process(self._take(read))))

    @op("statistic", fields=("name",))
    def op_statistic(self, name: str = "mean", **_: Any) -> OpResult:
        """One statistic: count, mean, minimum, maximum, span or sdev."""
        stats = self.statistics()
        if name not in stats:
            raise ValueError(f"{self.uid}: no statistic {name!r}")
        if name == "count":
            return OpResult(V.format_int(int(stats["count"])))
        return OpResult(self._number(stats[name]))

    @op("statistics_clear")
    def op_statistics_clear(self, **_: Any) -> OpResult:
        self.samples.clear()
        return OpResult()

    @op("limit_fail")
    def op_limit_fail(self, **_: Any) -> OpResult:
        """1 when a reading has been outside an enabled limit since the flag was cleared, else 0."""
        return OpResult(V.format_int(int(self.failed)))

    @op("limit_clear")
    def op_limit_clear(self, **_: Any) -> OpResult:
        self.failed = False
        return OpResult()

    @op("math_reset")
    def op_math_reset(self, **_: Any) -> OpResult:
        """The factors m, c and z back to the defaults of the settings they are bound to."""
        assert self.owner is not None
        for key in FACTORS:
            path = self.bound_path(key)
            spec = self.owner.definition.state.get(path) if path is not None else None
            if path is not None and spec is not None:
                self.owner.set_state(path, spec.default, source="scpi", internal=True)
        return OpResult()
