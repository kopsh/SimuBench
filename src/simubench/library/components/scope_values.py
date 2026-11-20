"""The values a measurement reads off a record: the record behind a source, a threshold and the crossings it makes, the time and level measurements. Part of ``ScopeMeasurements`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from ...engine.component import (
    MathSource,
)
from ...engine.stream import (
    MAX_FRAME,
    beyond_model,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .measurements import (
    DEFAULT_THRESHOLDS,
    TIME_MEASUREMENTS,
    Levels,
    crossings,
    measure,
    mid_crossings,
    threshold_levels,
    top_base,
)
from .scope import SCREEN_MEASUREMENTS
from .scope_measurements_host import MeasurementsHost
from .scope_record import (
    CHUNKED_MEASUREMENTS,
    Record,
    chunked_value,
)


class Values:
    def __init__(self, host: MeasurementsHost) -> None:
        self.host = host

    def record(self, n: int) -> tuple[Any, int]:
        """The record a measurement on source key ``n`` reads: channel n's acquisition, or math
        function -n's waveform over its operands (#1074) — a model without math functions refuses
        the source (-224), a function without an operator is -230."""
        if n >= 0:
            return self.host.acq().measure_record(n)
        block = self.host.ref("math")
        if block is None or not isinstance(block, MathSource):
            raise ScpiError(-224, f"FUNCtion{-n}: this model has no math functions to measure")
        rec, used = block.read_function(-n)
        if rec is None:
            raise ScpiError(-230, f"function {-n} is not defined")
        return rec, used

    def value(self, what: str, n: int) -> tuple[float, int]:
        """The engine measurement ``what`` on source n's record (NaN: cannot be measured), and the
        virtual time getting the record took."""
        rec, used = self.record(n)
        return self.record_value(what, rec), used

    def screen_values(self, records: Mapping[int, Record]) -> list[dict[str, Any]]:
        """The screen's measurement panel (the :class:`~simubench.engine.component.ScreenMeasurer`
        protocol), one row per record by channel: the engine measurements the screen shows, by the
        view's key, each ``None`` where they cannot be measured. A deep record's measurements read
        the record (#1012) and the screen does not — its row carries the channel alone."""
        out = []
        for n, rec in sorted(records.items()):
            row: dict[str, Any] = {"channel": n}
            if rec.deep is None:
                for key, what in SCREEN_MEASUREMENTS.items():
                    value = float(self.record_value(what, rec))
                    row[key] = value if math.isfinite(value) else None
            out.append(row)
        return out

    @staticmethod
    def impedance(value: Any) -> float:
        """A dBm result's reference impedance: AUTO (or none) 50 Ω, else 10 to 200 Ω."""
        if isinstance(value, list):  # a raw parameter: the words after the units
            value = value[0] if value else None
        if value is None:
            return 50.0
        word = (V.parse_string(value) if isinstance(value, bytes) else str(value)).strip()
        if word.upper().startswith("AUTO"):
            return 50.0
        ohms = V.parse_float(word.encode(), "OHM")
        if not 10.0 <= ohms <= 200.0:
            raise ScpiError(-222, "the reference impedance is 10 to 200 ohms")
        return ohms

    def record_value(self, what: str, rec: Any) -> float:
        """The engine measurement ``what`` on a record by this model's rules (gate, clipping, top
        and base, crossing level): what :MEASure? answers, and what the screen's measurement
        panel shows for the record on screen (#280). A ``:DBM<ohms>`` suffix gives an RMS as dBm
        into that impedance."""
        base, dbm, ohms = what.partition(":DBM")
        if dbm:
            volts = self._record_value(base, rec)
            if not math.isfinite(volts) or volts <= 0:
                return math.nan
            return 10.0 * math.log10(volts * volts / float(ohms) / 1e-3)
        return self._record_value(what, rec)

    def _record_value(self, what: str, rec: Any) -> float:
        acq = self.host.acq()
        if rec.points > MAX_FRAME:
            return self._deep_value(what, rec)
        v = np.asarray(rec.volts, dtype=float)
        gate = self.host.param("gate")
        if gate:
            lo, hi = (max(0, min(v.size, round(float(g) / 10 * v.size))) for g in gate)
            v = v[lo:hi]
        if v.size and str(self.host.param("clipped")) == "invalid" and rec.channel > 0:  # a function: no ADC
            rails = acq.adc_unit().rails(rec)
            if acq.adc_unit().param("bits") and (v.max() >= rails[0] or v.min() <= rails[1]):
                return math.nan
        if what in TIME_MEASUREMENTS:
            return self._time_value(what, v, rec)
        if self.host.param("thresholds"):
            gen = self.levels(v, rec, "general")
            if gen is None:
                return math.nan  # HISTONLY on a record whose histogram shows no levels
            return measure(
                what,
                v,
                rec.dt_s,
                min_cycles=int(self.host.param("min_cycles")),
                general=gen,
                rise_fall=self.levels(v, rec, "rfall"),
                edge_min_samples=int(self.host.param("edge_min_samples")),
                shoot=str(self.host.param("shoot")),
            )
        mid = None
        if v.size and what in ("FREQuency", "PERiod"):
            m = float(self.host.param("mid"))
            if str(self.host.param("levels")) == "absolute":
                mid = m
            elif m != 50.0:
                levels = top_base(v) if self.host.param("top_base") == "histogram" else None
                top, base = levels or (float(v.max()), float(v.min()))
                mid = base + m / 100 * (top - base)
        value = measure(
            what,
            v,
            rec.dt_s,
            top_base=str(self.host.param("top_base")),
            mid=mid,
            hysteresis=float(self.host.param("hysteresis")),
            min_cycles=int(self.host.param("min_cycles")),
            min_band=self.band_floor(rec),
            edge_min_samples=int(self.host.param("edge_min_samples")),
            shoot=str(self.host.param("shoot")),
        )
        return value

    def _deep_value(self, what: str, rec: Record) -> float:
        """A measurement on a record too deep to hold in memory (#1012): those that reduce it chunk
        by chunk (``CHUNKED_MEASUREMENTS``) read it in chunks; the others need every sample at once
        and are refused as beyond the model."""
        if what not in CHUNKED_MEASUREMENTS:
            raise beyond_model(
                f"{what} on a record of {rec.points} points needs the whole record in memory "
                f"(at most {MAX_FRAME} points)"
            )
        lo, hi = 0, rec.points
        gate = self.host.param("gate")
        if gate:
            lo, hi = (max(0, min(rec.points, round(float(g) / 10 * rec.points))) for g in gate)
        acq = self.host.acq()
        if str(self.host.param("clipped")) == "invalid" and rec.channel > 0 and acq.adc_unit().param("bits"):
            rails = acq.adc_unit().rails(rec)
            if (
                chunked_value("VMAX", rec, lo, hi) >= rails[0]
                or chunked_value("VMIN", rec, lo, hi) <= rails[1]
            ):
                return math.nan
        return chunked_value(what, rec, lo, hi)

    def threshold_spec(self, n: int, group: str) -> dict[str, Any]:
        """Channel n's threshold set ``group`` (general / rfall) from the settings ``thresholds``
        names; a field without a setting keeps the default."""
        prefix = str(self.host.param("thresholds"))
        out: dict[str, Any] = {}
        for key in DEFAULT_THRESHOLDS:
            path = f"{prefix}.{group}{n}.{key}"
            if path in self.host.specs:
                out[key] = self.host.get_state(path)
        return out

    def levels(self, v: Any, rec: Any, group: str = "general") -> Levels | None:
        """The record's top, base and reference levels by its channel's threshold set (or the
        fixed 90/50/10 % without ``thresholds``)."""
        spec = self.threshold_spec(rec.channel, group) if self.host.param("thresholds") else {}
        return threshold_levels(
            np.asarray(v, dtype=float), spec, float(self.host.param("hysteresis")), self.band_floor(rec)
        )

    def crossings_of(self, v: Any, rec: Any, position: str = "MIDDle") -> list[tuple[float, int]]:
        """The record's crossings of a reference level: the threshold set's with ``thresholds``,
        else the middle as before."""
        if not self.host.param("thresholds"):
            return mid_crossings(v, rec.dt_s, float(self.host.param("hysteresis")))
        lv = self.levels(v, rec, "general")
        if lv is None:
            return []
        return crossings(np.asarray(v, dtype=float), rec.dt_s, lv.at(position), lv.band)

    def band_floor(self, rec: Any) -> float:
        """The crossing band's absolute floor: divisions of the record's scale (#745)."""
        return float(self.host.param("hysteresis_floor_divs")) * rec.scale

    def _time_value(self, what: str, v: np.typing.NDArray[np.float64], rec: Any) -> float:
        """XMAX/XMIN: the time of the first maximum / minimum, from the trigger; BRATe: the bit rate,
        1 / the narrowest pulse (positive or negative) at the middle level (#350)."""
        if v.size == 0:
            return math.nan
        if what in ("XMAX", "XMIN"):
            k = int(np.argmax(v) if what == "XMAX" else np.argmin(v))
            return rec.origin + k * rec.dt_s
        cross = mid_crossings(v, rec.dt_s, float(self.host.param("hysteresis")), self.band_floor(rec))
        widths = [u - t for (t, a), (u, b) in itertools.pairwise(cross) if b == -a and u > t]
        return 1.0 / min(widths) if widths else math.nan
