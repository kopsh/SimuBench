"""The FFT function of a scope: its spectrum and window, the whole-record transform, peaks, measurements and resolution, and the gating function. Part of ``ScopeMath`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine.component import Acquisition, OpResult
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .reading_math import noise_bandwidth
from .scope_math_host import MathHost, MathRecord, Operand, _in_dbm, _source_function, _suffix, aligned
from .scope_math_ops import (
    _TIMEBASE_BOUND as _TIMEBASE_BOUND,
)
from .scope_math_ops import (
    BESSEL4_SECTIONS as BESSEL4_SECTIONS,
)
from .scope_math_ops import (
    CANONICAL as CANONICAL,
)
from .scope_math_ops import (
    FFT_OPERATORS as FFT_OPERATORS,
)
from .scope_math_ops import (
    GUIDE_UNIT_WORDS as GUIDE_UNIT_WORDS,
)
from .scope_math_ops import (
    MIN_FFT_POINTS as MIN_FFT_POINTS,
)
from .scope_math_ops import (
    OPERATORS as OPERATORS,
)
from .scope_math_ops import (
    UNARY as UNARY,
)
from .scope_math_ops import (
    VUNIT_WORDS as VUNIT_WORDS,
)
from .scope_math_ops import (
    VUNITS as VUNITS,
)
from .scope_math_ops import (
    WHOLE_OPERATORS as WHOLE_OPERATORS,
)
from .scope_math_ops import (
    WINDOW_WORDS as WINDOW_WORDS,
)
from .scope_math_ops import (
    WINDOWS as WINDOWS,
)
from .scope_math_ops import (
    _bessel4_sections as _bessel4_sections,
)
from .scope_math_ops import (
    as_density as as_density,
)
from .scope_math_ops import (
    band_power as band_power,
)
from .scope_math_ops import (
    bin_power as bin_power,
)
from .scope_math_ops import (
    bucket_extreme as bucket_extreme,
)
from .scope_math_ops import (
    derivative as derivative,
)
from .scope_math_ops import (
    find_peaks as find_peaks,
)
from .scope_math_ops import (
    highpass as highpass,
)
from .scope_math_ops import (
    integral as integral,
)
from .scope_math_ops import (
    lowpass as lowpass,
)
from .scope_math_ops import (
    magnitude as magnitude,
)
from .scope_math_ops import (
    occupied_bandwidth as occupied_bandwidth,
)
from .scope_math_ops import (
    one_sided_fft as one_sided_fft,
)
from .scope_math_ops import (
    phase as phase,
)
from .scope_math_ops import (
    pointwise as pointwise,
)
from .scope_math_ops import (
    running_mean as running_mean,
)
from .scope_math_ops import (
    window as window,
)


class Fft:
    def __init__(self, host: MathHost) -> None:
        self.host = host

    def _goft_setting(self, name: str, default: Any = None) -> Any:
        """One of the g(t) source's settings (``goft_prefix`` names the paths)."""
        prefix = str(self.host.param("goft_prefix") or "")
        path = f"{prefix}.{name}"
        if not self.host.has_state(path):
            return default
        value = self.host.get_state(path)
        return default if value is None else value

    def goft_record(self, start: bool) -> tuple[MathRecord, int]:
        """The g(t) source a ``GOFT`` operand names: the goft operator (ADD, SUBTract or
        MULTiply) over its two channel sources, as a function reads them — a raw record, no
        vertical scaling of its own."""
        prefix = str(self.host.param("goft_prefix") or "")
        if not prefix:
            raise ScpiError(-224, "GOFT: this model has no g(t) source")
        word = str(self._goft_setting("operator", "ADD") or "ADD").strip().upper()
        if word not in ("ADD", "SUBTRACT", "MULTIPLY"):
            raise ScpiError(-224, f"{word}: no such g(t) operation")
        operator = CANONICAL[word]
        records: list[Any] = [None, None]
        used = 0
        acq = self.host.ref("record")
        if acq is None or not isinstance(acq, Acquisition):
            raise ScpiError(-300, f"{self.host.uid}: no acquisition to take sources from")
        for which in (1, 2):
            operand = Operand(
                str(self._goft_setting(f"source{which}", "") or ""),
                int(self.host.param("channels")),
                int(self.host.param("functions")),
            )
            if not operand.word or operand.channel is None:
                raise ScpiError(-224, f"{operand.word or ''}: the g(t) source names channels")
            rec, u = acq.read_record(operand.channel, start)
            used += u
            if rec is None:
                raise ScpiError(-230, f"channel {operand.channel} has no record")
            records[which - 1] = rec
        aligned(records[0], records[1])
        spec = {
            "f": 0,
            "operator": operator,
            "params": {},
            "dt_s": float(records[0].dt_s),
            "x_origin_s": float(records[0].origin),
            "points": int(records[0].points),
            "index0": 0,
            "scale": 1.0,
            "offset": 0.0,
        }
        return MathRecord(spec, records, [None, None]), used

    def spec(self, f: int, operator: str, records: list[Any]) -> dict[str, Any]:
        """The operator's recipe, fixed at the read: the parameters it reads now, the output's
        grid (dt, x origin, points) and its vertical scale and offset."""
        source = next((r for r in records if r is not None), None)
        if source is None:  # only constants: there is no waveform to compute (#1451)
            raise ScpiError(-221, f"function {f} has no source")
        if bool(getattr(source, "x_hz", False)) and operator not in (
            "ADD",
            "SUBTract",
            "MULTiply",
            "DIVide",
            "COMMonmode",
            "MAGNify",
        ):
            raise ScpiError(-221, f"{operator}: the source is a frequency-domain trace")
        dt, x0 = float(source.dt_s), float(source.origin)
        points = int(source.points)
        params: dict[str, Any] = {}
        index0 = 0
        if operator == "DELay":
            x0 += float(self.host.setting(f, "delay", 0.0) or 0.0)
        elif operator == "GATing":
            start = float(self.host.setting(f, "gate_start", 0.0) or 0.0)
            stop = float(self.host.setting(f, "gate_stop", 0.0) or 0.0)
            if stop <= start:
                raise ScpiError(-221, "the gating window is empty")
            index0 = max(0, min(points, math.ceil((start - x0) / dt)))
            end = max(0, min(points, math.ceil((stop - x0) / dt)))
            points = max(0, end - index0)
            x0 += index0 * dt
        elif operator in ("SMOoth", "AVERage"):
            key = "smooth" if operator == "SMOoth" else "average"
            params["window"] = max(1, int(float(self.host.setting(f, key, 1) or 1)))
        elif operator in ("MAXimum", "MINimum"):
            params["bucket"] = max(1, int(float(self.host.setting(f, "bucket", 1) or 1)))
            points = points // params["bucket"]
        elif operator == "LINear":
            gain = self.host.setting(f, "lin_gain", 1.0)
            off = self.host.setting(f, "lin_off", 0.0)
            params["gain"] = 1.0 if gain is None else float(gain)  # 0 is a legal gain
            params["offset"] = 0.0 if off is None else float(off)
        spec: dict[str, Any] = {
            "f": f,
            "operator": operator,
            "params": params,
            "dt_s": dt,
            "x_origin_s": x0,
            "points": points,
            "index0": index0,
            "scale": 1.0,
            "offset": 0.0,
        }
        if operator in FFT_OPERATORS:
            self._fft_spec(f, operator, source, spec)
        else:
            spec["scale"], spec["offset"] = self.vertical(f, source)
        return spec

    def vertical(self, f: int, source: Any) -> tuple[float, float]:
        """The function's vertical scale (a division) and offset: manual (the ``range`` and
        ``offset`` settings, the range a division is an eighth of) or automatic, the source's."""
        if str(self.host.setting(f, "vertical", "AUTO") or "AUTO").upper().startswith("MAN"):
            return float(self.host.setting(f, "range", 8.0) or 8.0) / 8.0, float(
                self.host.setting(f, "offset", 0.0) or 0.0
            )
        return float(getattr(source, "scale", 1.0)), float(getattr(source, "offset", 0.0))

    def whole(self, spec: dict[str, Any], records: list[Any], constants: list[float | None]) -> MathRecord:
        """The operator computed over the whole source record: the integral, the filters and the
        transforms (a transform's trace replaces the grid — its bins, its unit, its span)."""
        source = next(r for r in records if r is not None)
        operator = spec["operator"]
        v = np.asarray(source.volts, dtype=np.float64)
        if operator == "INTegrate":
            # the DC offset correction an INTegrate:IOFFset sets comes off before integrating
            off = float(self.host.setting(spec["f"], "i_offset", 0.0) or 0.0)
            volts = integral(v - off, spec["dt_s"])
        elif operator == "HIGHpass":
            volts = highpass(v, spec["dt_s"], float(self.host.setting(spec["f"], "hp_bw", 1e3) or 1e3))
        elif operator == "LOWPass":
            volts = lowpass(v, spec["dt_s"], float(self.host.setting(spec["f"], "lp_bw", 1e3) or 1e3))
        else:
            volts = self._compute_fft(spec, v)
        rec = MathRecord(spec, records, constants, volts=volts)
        if operator in FFT_OPERATORS and not str(
            self.host.setting(spec["f"], "vertical", "AUTO") or "AUTO"
        ).upper().startswith("MAN"):
            lo, hi = float(np.nanmin(volts)), float(np.nanmax(volts))  # the trace's own scale
            rec.scale = (hi - lo) / 8.0 if hi > lo else max(abs(hi), abs(lo), 1e-12) / 4.0
            rec.offset = (hi + lo) / 2.0
        return rec

    def _fft_spec(self, f: int, operator: str, source: Any, spec: dict[str, Any]) -> None:
        """The transform's recipe: its length (the effective memory depth, capped), its window,
        its vertical unit, impedance and span — the trace's grid replaces the source's."""
        n = int(source.points)
        points = 1
        while points * 2 <= n and points * 2 <= int(self.host.param("fft_max_points")):
            points *= 2
        if points < MIN_FFT_POINTS:
            raise ScpiError(-221, f"a record of {n} points has no transform this model computes")
        dt = float(source.dt_s)
        rate = 1.0 / dt
        unit = str(self.host.setting(f, "vunits", "DB") or "DB").upper()
        unit = VUNIT_WORDS.get(unit, unit)
        window_word = str(self.host.setting(f, "window", "HANNing") or "HANNing").upper()
        if window_word not in WINDOW_WORDS:
            raise ScpiError(-224, f"{window_word}: no such window")
        window_kind = WINDOW_WORDS[window_word]
        # A span of 0 Hz is a nonsense request, so the settings without a default (0.0 at
        # power-on) read as unset — the trace then spans the whole one-sided band, as the
        # instrument's AUTO does.
        span = self.host.setting(f, "span") or None
        center = self.host.setting(f, "center") or None
        start = self.host.setting(f, "start") or None
        stop = self.host.setting(f, "stop") or None
        if span is None and center is None and start is None and stop is None:
            start, stop = 0.0, rate / 2.0  # the whole one-sided span
        elif span is not None or center is not None:
            span = float(span) if span is not None else float(stop or 0.0) - float(start or 0.0)
            center = float(center) if center is not None else (float(stop or 0.0) + float(start or 0.0)) / 2.0
            start, stop = center - span / 2.0, center + span / 2.0
        else:
            start, stop = float(start or 0.0), float(stop or rate / 2.0)
        most = float(self.host.param("fft_span_max") or rate)
        if stop <= start or stop - start > most:
            raise ScpiError(-222, f"the span {stop - start:g} Hz is beyond this model's {most:g} Hz")
        spec["fft"] = {
            "points": points,
            "dt_s": dt,
            "unit": unit,
            "window": window_kind,
            "start": start,
            "stop": stop,
            "impedance": float(
                self.host.setting(f, "impedance", self.host.param("impedance"))
                or self.host.param("impedance")
            ),
            "t_delay": float(self.host.setting(f, "tdelay", 0.0) or 0.0),
            "detector": str(self.host.setting(f, "detector", "OFF") or "OFF").upper(),
            "detector_points": int(float(self.host.setting(f, "detector_points", 0) or 0)),
        }
        spec["x_hz"] = True

    def _compute_fft(self, spec: dict[str, Any], v: np.ndarray) -> np.ndarray:
        """The transform's trace: the windowed one-sided FFT, in its unit, over its span, then
        the detector's decimation. The trace's grid (bin width, first frequency) lands in the
        spec — a record reading it answers a frequency axis."""
        fft = spec["fft"]
        n = int(fft["points"])
        w = window(fft["window"], n)
        x = one_sided_fft(v[:n], w)
        df = 1.0 / (n * fft["dt_s"])
        freqs = np.arange(x.size) * df
        if spec["operator"] == "FFTPhase":
            trace = phase(x, fft["t_delay"], freqs)
        else:
            trace = magnitude(x, fft["unit"], fft["impedance"])
            if spec["operator"] == "PSDensity":
                trace = as_density(trace, fft["unit"], noise_bandwidth(w, fft["dt_s"]))
        inside = np.flatnonzero((freqs >= fft["start"]) & (freqs <= fft["stop"]))
        if not inside.size:
            raise ScpiError(-221, "the span holds no point of this trace")
        trace = trace[inside]
        spec["dt_s"] = df
        spec["x_origin_s"] = float(freqs[inside[0]])
        detector = fft["detector"]
        k = int(fft["detector_points"])
        if detector != "OFF" and k > 0 and trace.size > k:
            edges = np.linspace(0, trace.size, k + 1, dtype=int)
            buckets = [trace[edges[i] : edges[i + 1]] for i in range(k)]
            if detector == "SAMPle":
                trace = np.array([b[b.size // 2] for b in buckets])
            elif detector == "AVERage":
                trace = np.array([b.mean() for b in buckets])
            elif detector == "PPOSitive":
                trace = np.array([b.max() for b in buckets])
            elif detector == "PNEGative":
                trace = np.array([b.min() for b in buckets])
            else:  # NORMal: the sample furthest from the bucket's mean each bucket
                trace = np.array([b[np.argmax(np.abs(b - b.mean()))] for b in buckets])
        return trace

    def _trace(self, f: int) -> tuple[MathRecord, np.ndarray, np.ndarray]:
        """Function ``f``'s transform trace, its frequencies and its values (a time-domain
        function is a settings conflict: the FFT measurements need a transform)."""
        rec, _ = self.host.measure_function(f)
        if not rec.x_hz:
            raise ScpiError(-221, f"function {f} is not a transform")
        freqs = rec.origin + np.arange(rec.points) * rec.dt_s
        return rec, freqs, np.asarray(rec.volts, dtype=float)

    def _peaks(self, rec: MathRecord, freqs: np.ndarray, values: np.ndarray, level_dbm: float) -> list[int]:
        """The trace's peaks above a level in dBm, ordered as the ``peak_sort`` parameter asks."""
        impedance = float(rec.spec["fft"]["impedance"])
        unit = str(rec.spec["fft"]["unit"])
        order = str(self.host.param("peak_sort") or "DMAG").upper()
        peaks = [
            i
            for i in find_peaks(freqs, values, -math.inf)
            if _in_dbm(float(values[i]), unit, impedance) > level_dbm
        ]
        if order.startswith("IF"):
            peaks.sort(key=lambda i: freqs[i])
        elif order.startswith("DFR"):
            peaks.sort(key=lambda i: -freqs[i])
        elif order.startswith("IMA"):
            peaks.sort(key=lambda i: values[i])
        else:  # DMAG: by decreasing magnitude
            peaks.sort(key=lambda i: -values[i])
        return peaks

    def op_fft_peak(
        self, kind: str = "FREQuency", suffixes: dict[str, int] | None = None, **_: Any
    ) -> OpResult:
        f = self.host.function(_suffix(suffixes))
        rec, freqs, values = self._trace(f)
        if not V.flag(self.host.setting(f, "peak_state", True)):
            return OpResult(str(self.host.param("invalid_value")).encode())
        level = float(self.host.setting(f, "peak_level", -math.inf) or -math.inf)
        peaks = self._peaks(rec, freqs, values, level)
        count = int(float(self.host.setting(f, "peak_count", 0) or 0))
        if count > 0:
            peaks = peaks[:count]
        if not peaks:
            return OpResult(str(self.host.param("invalid_value")).encode())
        column = freqs if kind.upper().startswith("FREQ") else values
        return OpResult(b",".join(self.host.format_number(float(column[i])) for i in peaks))

    def op_fft_measure(
        self,
        kind: str = "FREQuency",
        source: Any = None,
        peak: int | None = None,
        peak1: int | None = None,
        peak2: int | None = None,
        level: float | None = -60.0,
        center: float | None = 0.0,
        bandwidth: float | None = 0.0,
        percent: float | None = 99.0,
        mode: str | None = None,
        value: float | None = None,
        **_: Any,
    ) -> OpResult:
        f = _source_function(source, int(self.host.param("functions")))
        rec, freqs, values = self._trace(f)
        fft = rec.spec["fft"]
        kind = kind.upper()
        invalid = str(self.host.param("invalid_value")).encode()

        def number(value: float) -> bytes:
            return self.host.format_number(value)

        def numbered(threshold: float) -> list[int]:
            """The peaks above ``threshold`` dBm, numbered from low to high frequency."""
            unit, impedance = str(fft["unit"]), float(fft["impedance"])
            return [
                i
                for i in find_peaks(freqs, values, -math.inf)
                if _in_dbm(float(values[i]), unit, impedance) > threshold
            ]  # find_peaks answers them by rising frequency

        if kind in ("FREQUENCY", "MAGNITUDE", "DFREQUENCY", "DMAGNITUDE"):
            peaks = numbered(float(level) if level is not None else -60.0)
            first = int(peak1 if peak1 is not None else peak if peak is not None else 1)
            second = int(peak2) if peak2 is not None else 2
            if first < 1 or second < 1:  # peaks number from 1: no peak 0 or below
                return OpResult(invalid)
            try:
                if kind in ("FREQUENCY", "MAGNITUDE"):
                    i = peaks[first - 1]
                    value = float(freqs[i]) if kind == "FREQUENCY" else float(values[i])
                else:
                    i, j = peaks[first - 1], peaks[second - 1]
                    value = (
                        float(freqs[j] - freqs[i]) if kind == "DFREQUENCY" else float(values[j] - values[i])
                    )
            except IndexError:
                return OpResult(invalid)
            return OpResult(number(value))
        power = bin_power(values, str(fft["unit"]), float(fft["impedance"]), rec.dt_s)
        if kind in ("CPOWER", "PSD"):
            center = float(center) if center is not None else 0.0
            bandwidth = float(bandwidth) if bandwidth is not None else 0.0
            value = band_power(power, freqs, center, bandwidth / 2.0)
            if kind == "PSD":
                value = value / bandwidth if bandwidth > 0 else math.nan
            return OpResult(number(10.0 * math.log10(value / 1e-3) if value > 0 else -math.inf))
        if kind == "OBW":
            return OpResult(
                number(occupied_bandwidth(freqs, power, float(percent if percent is not None else 99.0)))
            )
        if kind == "THDISTORTION":
            if value is None:
                raise ScpiError(-109, "missing the fundamental (MANual) or the level (PEAK)")
            if str(mode or "").upper().startswith("PEAK"):
                # The manual does not specify an order; use the same low-to-high numbering as the
                # other FFT peak measurements, independently of :FUNCtion:FFT:PEAK:SORT (#1375).
                found, n = numbered(float(value)), int(peak) if peak is not None else 1
                fund = float(freqs[found[n - 1]]) if 1 <= n <= len(found) else 0.0
            else:
                fund = float(value)
            # a fundamental within the first bin (0 Hz or below included) has no harmonics to sum,
            # and above it the harmonics are at most the trace's bins (#1362: a 0 Hz one never ended)
            if not math.isfinite(fund) or fund < rec.dt_s:
                return OpResult(invalid)
            half = 1.5 * rec.dt_s  # the fundamental and each harmonic: the peak bin ± its neighbours
            base = band_power(power, freqs, fund, half)
            if base <= 0.0:
                return OpResult(invalid)
            harm, h = 0.0, 2
            while fund * h <= freqs[-1]:
                harm += band_power(power, freqs, fund * h, half)
                h += 1
            return OpResult(number(100.0 * math.sqrt(harm / base)))
        raise ScpiError(-300, f"{self.host.uid}: no FFT measurement {kind!r}")

    def op_fft_resolution(
        self,
        query: bool = False,
        resolution: float | None = None,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        f = self.host.function(_suffix(suffixes))
        fft = self.host.measure_function(f)[0].spec["fft"]
        enbw = noise_bandwidth(window(str(fft["window"]), int(fft["points"])), float(fft["dt_s"]))
        if query or resolution is None:
            return OpResult(self.host.format_number(enbw))
        name = str(self.host.param("resolution_scale") or "")
        if not name or not self.host.has_state(self.host.resolve_path(name)):
            raise ScpiError(-221, "this model's timebase does not follow the FFT resolution")
        window_s = int(fft["points"]) * float(fft["dt_s"])  # the transform's span now
        self.host.set_state(
            self.host.resolve_path(name),
            window_s * (enbw / float(resolution)) / 10.0,  # longer by the resolution ratio
            source="scpi",
            internal=True,
        )
        return OpResult()
