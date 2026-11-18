"""Oscilloscope components: analog front end and acquisition (trigger, sampling, quantization, transfer)."""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, cast

import numpy as np

from ...engine.component import (
    Component,
    MathSource,
    OpResult,
    PortSpec,
    RecordSource,
    ScreenState,
    binding_of,
    component,
    op,
)
from ...engine.noise import gaussian, stream_key, uniform
from ...engine.signal import Signal, bessel4, brickwall, butterworth4, gaussian_lp, highpass1, lowpass1
from ...engine.stream import (
    MAX_FRAME,
    MAX_REQUEST_FANOUT,
    FrameRequest,
    ModelLimit,
    SampleFrame,
    beyond_model,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from ...units import parse_number
from .calstore import CalibrationStore
from .measurements import (
    DEFAULT_THRESHOLDS,
    FREQ_HYSTERESIS,
    LEVEL_MEASUREMENTS,
    TIME_MEASUREMENTS,
    Levels,
    burst_measure,
    crossing_voltage,
    crossings,
    edge_spec,
    measure,
    mid_crossings,
    pulse_window_mean,
    threshold_levels,
    top_base,
)
from .scope_math import function_index
from .scope_pending import Acquisition, PendingAcquisitions, _Segment
from .scope_record import (
    CHUNKED_MEASUREMENTS,
    EAGER_POINTS,
    AdcView,
    DecimatorView,
    DeepRecord,
    Record,
    boxcar,
    chunked_value,
    converter_half,
    peak_detect,
    quantize,
)

__all__ = ["Record", "peak_detect"]

_boxcar = boxcar  # the earlier name (tests)

#: Vertical full scale is 8 divisions; 16-bit codes span ±4 divisions.
DIVS_V = 8
DIVS_H = 10

#: The screen's measurement panel (#280/#373): the view's key -> the engine measurement, in the
#: order the panel shows them. The screen measures the record it shows, so the values agree with
#: the model's :MEASure? replies on the same record.
SCREEN_MEASUREMENTS = {
    "amplitude": "VAMPlitude",
    "vpp": "VPP",
    "vmax": "VMAX",
    "vmin": "VMIN",
    "mean": "VAVerage",
    "rms": "VRMS",
    "frequency": "FREQuency",
    "period": "PERiod",
}


class _ChannelBound(Component):
    """Parameters that may name a channel's setting: ``$ch{n}.trigger_level`` for channel n."""

    def channel_param(self, key: str, n: int | None) -> Any:
        if key in self.overrides:
            return self.overrides[key]
        value = self._params.get(key)
        if isinstance(value, dict) and isinstance(value.get("from"), str) and "{n}" in value["from"]:
            if n is None:
                return None
            value = {**value, "from": value["from"].replace("{n}", str(n))}
        elif isinstance(value, str) and "{n}" in value:
            if n is None:
                return None
            value = value.replace("{n}", str(n))
        return self._resolve(key, value)

    def bound_paths(self) -> dict[str, str]:
        return {k: v for k, v in super().bound_paths().items() if "{n}" not in v}


#: Instants across an acquisition's window the overload check draws the input at (#1264).
EXTREME_POINTS = 4096

#: Bandwidth filter shapes of the vertical channel (``response``, #607).
RESPONSES = {
    "first_order": lowpass1,
    "brickwall": brickwall,
    "gaussian": gaussian_lp,
    "bessel4": bessel4,
    "butterworth4": butterworth4,
}


@component("scope_vertical")
class ScopeVertical(Component):
    """The vertical channel: coupling, bandwidth, and the scale and offset with their ranges.

    Analog first-order filters (physics, D13): AC coupling is a ``ac_corner`` high-pass and the
    bandwidth a first-order low-pass at ``bandwidth`` (or ``bw_limit_hz`` when the limit is on).
    A scope whose bandwidth is a DSP filter declares its shape with ``response``: ``brickwall``
    (flat magnitude, linear phase: ringing on a step), ``gaussian`` (no ringing), ``bessel4`` or
    ``butterworth4``, -3 dB at the same frequency and with the filter's delay removed (#607, #609).
    The ``scale`` (V/div) and ``offset`` (V) are display values; with ``scale_range`` and
    ``offset_ranges`` a change outside the range is refused with -222 and the old value kept. The
    ranges are given at the input connector, i.e. the display values divided by ``probe_ratio``
    (instrument-authoring.md 8.4). The input noise is a ``noise_table`` row of the scale: real
    scopes' noise rides the vertical setting (the front-end gain sets it at the ADC), so a fixed
    input value would leave the coarse divisions far quieter than 1 LSB and averaging could not
    resolve more (F4, D46).

    The unit's own metrology (instrument-authoring.md 8.4, G3). Each V/div at the input
    connector has its own attenuator and gain stage: ``gain_error_ppm`` (relative) and
    ``offset_error`` (V at the input) are what this unit adds there — a number, or one per input
    V/div, the table a ``deviations`` entry with ``by: {scale: …}`` draws; a scale the table does
    not list has no characterized error and contributes 0. ``dac_error_ppm`` is the offset DAC's
    gain error, referred to the displayed offset setting: a display offset of V moves the record
    by V·(1 + d), i.e. the reading is off by V·d. The vertical runs before the probe (which is a
    display conversion), so the errors ride the input path and the DAC term is divided by
    ``probe_ratio`` here. They sit in signal order — coupling, the attenuator's gain, the bias,
    then the bandwidth — and the record shows them, since it is reconstructed with the nominal
    scale and offset. ``store`` names a ``calibration_store`` whose coefficients correct the
    record digitally, (raw − offset) × gain, keyed by the displayed V/div
    (metrology-authoring.md 4); :meth:`self_calibrate` writes them for every tabulated scale.
    """

    label = "垂直通道"
    label_en = "Vertical channel"
    about = "垂直通道：耦合、带宽、刻度与偏置及其范围。"
    about_en = "The vertical channel: coupling, bandwidth, scale and offset with their ranges."

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    choices = {"response": ("first_order", "brickwall", "gaussian", "bessel4", "butterworth4")}
    # A channel's own store first, then the instrument's (Component.peer); none: no correction.
    refs = {"store": ("calibration_store", "instrument")}
    optional_refs = frozenset({"store"})
    defaults = {
        "coupling": "DC",  # DC | AC | GND
        "bandwidth": 200e6,  # -3 dB point of the first-order low-pass
        "bw_limit": False,  # bandwidth limit on, or a named filter (FULL, 20M)
        "bw_limit_hz": 20e6,  # the limit when bw_limit is on
        "ac_corner": 10.0,  # corner of the AC coupling high-pass
        # the shape of the bandwidth filter at min(bandwidth, limit): first_order (an analog front end),
        # brickwall (flat magnitude, linear phase), gaussian (no ringing), bessel4 or butterworth4; the
        # DSP shapes are zero phase, their delay removed as a scope deskews it (#607, #609)
        "response": "first_order",
        "scale": "$ch.scale",  # V/div as displayed
        "offset": "$ch.offset",  # V as displayed
        "invert": False,  # the waveform reflects about 0 V (:CHANnel<N>:INVert, #1067)
        "probe_ratio": 1.0,  # the probe setting the displayed scale and offset include
        "scale_range": None,  # [min, max] V/div at the input connector (None: any); may map a setting
        "offset_ranges": None,  # [[up to V/div, +-limit V], ...] at the input connector (None: any)
        "out_of_range": "error",  # error: refuse with -222 | clamp: take the nearest legal value
        "noise_table": None,  # [[V/div, noise V rms], ...] at the input, per division (None: quiet)
        "noise_divs": 0.0,  # without a table: input noise rms as a fraction of a division (scales with V/div)
        "gain_error_ppm": 0.0,  # this unit's gain error, ppm (hidden); a number or by input V/div
        "offset_error": 0.0,  # this unit's offset error, V at the input (hidden); a number or by V/div
        "dac_error_ppm": 0.0,  # the offset DAC's gain error, ppm of the offset setting (hidden)
        "store": "",  # the calibration_store correcting the record, keyed by the displayed V/div
        # the maximum input range: the input may swing this many divisions (at the input) either side
        # of the offset; beyond it the channel overloads (None: no limit, #1264)
        "max_input_divs": None,
        "max_input_v": None,  # the absolute maximum at the input connector, V peak (None: none, #1264)
    }
    units = {
        "max_input_v": "V",
        "bandwidth": "Hz",
        "bw_limit_hz": "Hz",
        "ac_corner": "Hz",
        "scale": "V",
        "offset": "V",
        "offset_error": "V",
    }

    def output(self, port: str) -> Signal:
        sig = self.input("in")
        coupling = self.param("coupling")
        if coupling == "GND":
            return Signal.zero()
        if coupling == "AC":
            sig = sig.transfer(highpass1(float(self.param("ac_corner"))))
        gain = self._error("gain_error_ppm")
        offset = self._error("offset_error")  # the vertical runs before the probe: input volts
        dac = float(self.param("dac_error_ppm")) * 1e-6
        if gain or offset or dac:
            sig = sig.scale(1.0 + gain * 1e-6)
            # The DAC error belongs to the displayed offset setting; the probe scales the input
            # path by probe_ratio after this stage, so divide it here to keep it display-referred.
            ratio = float(self.param("probe_ratio"))
            sig = sig + Signal(dc=offset + float(self.param("offset")) * dac / ratio)
        fc = min(self._limit(), float(self.param("bandwidth")))
        if not math.isfinite(fc):
            return sig
        return sig.transfer(RESPONSES[self.choice("response") or "first_order"](fc))

    def _limit(self) -> float:
        """Bandwidth limit: on/off (``bw_limit_hz``) or a named filter such as Siglent's 20M / FULL."""
        limit = self.param("bw_limit")
        if isinstance(limit, str):
            if limit.upper() in ("FULL", "OFF"):
                return math.inf
            return parse_number(limit.upper().removesuffix("HZ").replace("M", "e6").replace("K", "e3"))
        return float(self.param("bw_limit_hz")) if limit else math.inf

    def input_noise_rms(self) -> float:
        """The input-referred noise (V rms) of the current division: its ``noise_table`` row, or
        the row of the largest listed division not above it (a table that starts at a floor holds
        the sensitive divisions first, D46). No table: ``noise_divs`` of the division, else none."""
        rows = sorted((float(r[0]), float(r[1])) for r in self.param("noise_table") or ())
        scale = self.input_scale()
        if not rows:
            return float(self.param("noise_divs")) * scale
        return next((rms for div, rms in reversed(rows) if div <= scale * (1 + 1e-9)), rows[0][1])

    def input_scale(self) -> float:
        """V/div at the input connector: the displayed scale divided by the probe ratio."""
        return float(self.param("scale")) / float(self.param("probe_ratio"))

    def input_extremes(self, t0_ns: int, span_s: float) -> tuple[float, float]:
        """The lowest and highest the signal at the input connector reaches over ``span_s`` from
        ``t0_ns``, V: drawn at ``EXTREME_POINTS`` instants across it (#1264)."""
        sig = self.input("in")
        if not sig.freq.size:
            return float(sig.dc), float(sig.dc)
        v = sig.sample_rel(t0_ns, np.linspace(0.0, max(span_s, 0.0), EXTREME_POINTS))
        return float(np.min(v)), float(np.max(v))

    def overload(self, add: bool, t0_ns: int, span_s: float) -> tuple[bool, bool]:
        """(beyond the maximum input range, beyond the absolute maximum) now (#1264). The range is
        ``max_input_divs`` input divisions either side of the screen's middle — the offset, or minus
        it where the converter adds it (``add``); the absolute maximum ``max_input_v`` at the input."""
        divs, limit = self.param("max_input_divs"), self.param("max_input_v")
        if divs is None and limit is None:
            return False, False
        lo, hi = self.input_extremes(t0_ns, span_s)
        ratio = float(self.param("probe_ratio"))
        middle = (-1.0 if add else 1.0) * float(self.param("offset")) / ratio
        over = divs is not None and (
            hi > middle + float(divs) * self.input_scale() * (1 + 1e-9)
            or lo < middle - float(divs) * self.input_scale() * (1 + 1e-9)
        )
        damage = limit is not None and max(abs(lo), abs(hi)) > float(limit) * (1 + 1e-9)
        return bool(over), bool(damage)

    def _error(self, key: str, scale: float | None = None) -> float:
        """This unit's error ``key`` at input V/div ``scale`` (default: now); a scalar, or a table."""
        value = self.param(key)
        if not isinstance(value, dict):
            return float(value)
        scale = self.input_scale() if scale is None else scale
        for k, entry in value.items():
            if math.isclose(float(k), scale, rel_tol=1e-9):
                return float(entry)
        return 0.0

    def _store(self) -> CalibrationStore | None:
        name = str(self.param("store") or "")
        if not name:
            return None
        store = self.peer(name, required=True)
        assert store is not None
        return store  # type: ignore[no-any-return]

    def correct(self, volts: np.typing.NDArray[np.float64]) -> np.typing.NDArray[np.float64]:
        """The record as the display stage shows it: the calibration's (raw − offset) × gain,
        keyed by the displayed V/div (the setting the store names); ``invert`` negates the gain
        (the waveform reflects about 0 V, #1067)."""
        coefficients = self.correction()
        if coefficients is None:
            return volts
        gain, offset = coefficients
        return (volts - offset) * gain

    def correction(self) -> tuple[float, float] | None:
        """The display stage's (gain, offset) now: the calibration's coefficients, or (−1, 0)
        while ``invert`` reflects the waveform about 0 V (:CHANnel<N>:INVert, #1067) — the same
        transform a deep record applies from the tuple alone."""
        store = self._store()
        coefficients = None if store is None else store.coefficients()
        if not V.flag(self.param("invert")):
            return None if coefficients is None else (float(coefficients[0]), float(coefficients[1]))
        gain, offset = (1.0, 0.0) if coefficients is None else coefficients
        return -float(gain), float(offset)

    def self_calibrate(self) -> None:
        """A self-calibration's write: the coefficients that cancel this unit's gain and offset
        error for every V/div the tables hold (scalars: the setting now), keyed by the displayed
        scale the store names. The scope compares against its internal references at zero offset,
        so the offset DAC's error at the user's offset setting stays. Without a store there is
        nothing to write (a scope without calibratable constants). A later probe-ratio change
        moves the displayed settings (and their keys), so the new settings start at nominal."""
        store = self._store()
        if store is None:
            return
        ratio = float(self.param("probe_ratio"))
        inputs = {self.input_scale()}
        for key in ("gain_error_ppm", "offset_error"):
            table = self.param(key)
            if isinstance(table, dict):
                inputs |= {float(k) for k in table}
        corrections: dict[tuple, tuple[float, float]] = {}
        for inp in inputs:
            corrections[store.state_key(inp * ratio)] = (
                1.0 / (1.0 + self._error("gain_error_ppm", inp) * 1e-6),
                self._error("offset_error", inp) * ratio,  # the record is in display volts
            )
        store.calibrate(corrections)

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> Any:
        """Refuse a scale or offset outside its range, or with ``out_of_range: clamp`` take the
        nearest legal value (a smaller scale also pulls the offset in). A probe change scales both
        together (the probe setting moves the display values, not the input), so it is not checked."""
        if key == "probe_ratio":
            return None
        ranges, limits = self.param("scale_range"), self.param("offset_ranges")
        if ranges is None and limits is None:
            return None
        clamp = str(self.param("out_of_range")) == "clamp"
        ratio = float(self.param("probe_ratio"))
        scale = float(self.param("scale")) / ratio
        if ranges is not None:
            lo, hi = (float(x) for x in ranges)
            if not lo * (1 - 1e-9) <= scale <= hi * (1 + 1e-9):
                if clamp and key == "scale":
                    return min(max(scale, lo), hi) * ratio
                raise ScpiError(-222, f"scale outside {lo:g}..{hi:g} V/div at the input")
        if limits is not None:
            limit = next((float(lim) for top, lim in limits if scale <= float(top) * (1 + 1e-9)), None)
            if limit is None:
                limit = float(limits[-1][1])
            offset = float(self.param("offset")) / ratio
            if abs(offset) > limit * (1 + 1e-9):
                nearest = math.copysign(limit, offset) * ratio
                if clamp and key == "offset":
                    return nearest
                offset_path = self.bound_path("offset")
                if clamp and self.owner is not None and offset_path is not None:
                    self.owner.set_state(offset_path, nearest, internal=True)
                    return None
                raise ScpiError(-222, f"offset outside +-{limit:g} V at this scale (at the input)")
        return None


@component("scope_frontend")
class ScopeFrontend(ScopeVertical):
    """The earlier name of ``scope_vertical`` (same behavior), kept so existing definitions load."""

    label = "模拟前端（旧名）"
    label_en = "Analog front end (old name)"
    about = "垂直通道的旧名称，行为相同，保留以载入旧定义。"
    about_en = "The earlier name of the vertical channel, kept so existing definitions load."


@component("scope_probe")
class ScopeProbe(Component):
    """The probe setting of one channel: display volts are the volts at the input times ``ratio``.

    A display conversion, not part of the analog path (instrument-authoring.md 8.4). ``skew``
    (bound to the model's channel-skew setting where it has one) and ``skew_error`` (this unit's
    own, a ``deviations`` draw) delay the channel's record by that many seconds: the acquisition
    samples the channel's signal that much earlier (instrument-authoring.md 8.4, G3). A real
    scope applies the delay after the ADC, so the trigger path — which detects on the analog
    signal — is not delayed with it."""

    label = "探头设置"
    label_en = "Probe setting"
    about = "通道的探头衰减比：显示电压等于输入电压乘以衰减比。"
    about_en = "A channel's probe ratio: display volts are input volts times the ratio."

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    defaults = {
        "ratio": "$ch.probe",  # probe attenuation the display assumes (1, 10, ...)
        # op set_ratio: what the probe command's keywords mean (a ratio, or "value": the number
        # that follows), the range of a ratio, and the display settings that scale with it
        "keywords": {"DEFault": 1.0, "VALue": "value"},
        "ratio_range": None,  # [min, max]; None: any positive ratio
        "rescales": [],  # settings in display units that follow the ratio, e.g. [$ch.scale, $ch.offset]
        "skew": None,  # the channel's skew setting, s (unbound: the model has none)
        "skew_error": 0.0,  # this unit's own skew error, s (hidden; a deviations entry)
        "invert": False,  # the channel's invert setting: the waveform reflected about 0 V
        # an external scaling gain the display divides by (an attenuator in front: 0.1), as a ratio
        "gain": 1.0,
    }
    units = {"skew": "s", "skew_error": "s"}

    def effective_ratio(self) -> float:
        """Display volts per input volt: the ratio over the external gain (the sign of an invert
        aside)."""
        return float(self.param("ratio")) / float(self.param("gain") or 1.0)

    def output(self, port: str) -> Signal:
        k = self.effective_ratio()
        return self.input("in").scale(-k if self.param("invert") in (True, 1, "1", "ON") else k)

    def delay(self) -> float:
        """How many seconds this channel's record lags the trigger timeline."""
        setting = self.param("skew")
        return (0.0 if setting in (None, "") else float(setting)) + float(self.param("skew_error"))

    @op("set_ratio")
    def set_ratio(self, query: bool = False, mode: str | None = None, value: float | None = None,
                  **_: Any) -> OpResult:  # fmt: skip
        """``:CHANnel<n>:PROBe VALue,10`` / ``DEFault``: a keyword gives the ratio or says the number
        that follows is; the ``rescales`` settings scale with it (the display shows probe-tip
        volts). The query answers the ratio in the definition's number format."""
        assert self.owner is not None
        path = self.bound_path("ratio")
        if path is None:
            raise ScpiError(-300, f"{self.uid}: ratio names no setting")
        if query:
            return OpResult(self.owner.format_number(float(self.owner.get_state(path))))
        if mode is None:
            raise ScpiError(-109)
        meaning = {str(k).upper(): v for k, v in (self.param("keywords") or {}).items()}.get(mode.upper())
        if meaning is None:
            raise ScpiError(-224, f"unknown probe setting {mode!r}")
        if meaning == "value":
            if value is None:
                raise ScpiError(-109, "probe ratio missing")
            ratio = float(value)
        else:
            ratio = float(meaning)
        limits = self.param("ratio_range")
        if limits is not None and not float(limits[0]) <= ratio <= float(limits[1]):
            raise ScpiError(-222)
        self.owner.set_state(path, ratio)  # the display settings follow it (on_state)
        return OpResult()

    @op("set_attenuation")
    def set_attenuation(self, query: bool = False, word: str = "", words: Any = None, **_: Any) -> OpResult:
        """A probe attenuation given as the model's words (``DIV1``/``DIV10``, the 1154A's input
        amplifier, #1067): the ``words`` argument maps each word to the display ratio it sets
        (the readings change with it); the query answers the word that is set."""
        assert self.owner is not None
        table = {str(k).upper(): float(v) for k, v in (words or {}).items()}
        if query:
            ratio = round(float(self.param("ratio")), 9)
            answer = next((w for w, r in table.items() if round(r, 9) == ratio), str(ratio))
            return OpResult(answer.encode())
        w = V.parse_string(word).upper() if isinstance(word, bytes) else str(word).upper()
        if w not in table:
            raise ScpiError(-224, f"{word}: no such attenuation ({', '.join(table) or 'none declared'})")
        path = self.bound_path("ratio")
        if path is None:
            raise ScpiError(-300, f"{self.uid}: ratio names no setting")
        self.owner.set_state(path, table[w])
        return OpResult()

    @op("skew_auto")
    def skew_auto(self, **_: Any) -> OpResult:
        """``:CHANnel<n>:PROBe:SKEW:AUTO``: the deskew against the probe's own compensation signal.
        The model has no compensation signal to compare against (no probe identification), so
        the command parses and the skew keeps its value (#1067)."""
        return OpResult()

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> Any:
        """The ``rescales`` settings follow a change of the ratio, however it is written."""
        if key != "ratio" or self.owner is None or not old:
            return None
        k = float(new) / float(old)
        for ref in self.param("rescales") or []:
            other = self.resolve_path(str(ref).removeprefix("$"))
            self.owner.set_state(other, float(self.owner.get_state(other)) * k, internal=True)
        return None


def channel_index(source: object) -> int:
    """Channel number of a source name: CHANnel2 (sb-*), CH2 or C2 (Siglent)."""
    text = str(source).upper()
    for prefix in ("CHANNEL", "CHAN", "CH", "C"):
        if text.startswith(prefix) and text[len(prefix) :].isdigit():
            return int(text[len(prefix) :])
    return 1


def short_source(source: object) -> str:
    """A source word as the reply's short form (the manual's Returned Format): CHANnel1 -> CHAN1,
    FUNCtion -> FUNC, FUNCtion2 -> FUNC2, anything else as it came."""
    text = str(source).strip()
    upper = text.upper()
    for prefix, short in (("CHANNEL", "CHAN"), ("FUNCTION", "FUNC")):
        if upper.startswith(prefix):
            rest = upper[len(prefix) :]
            if rest == "" or rest.isdigit():
                return short + rest
    return text


#: Edge slope names across models: SCPI-style and Siglent-style.
RISING = ["POSitive", "EITHer", "RISing", "ALTernate"]
FALLING = ["NEGative", "EITHer", "FALLing", "ALTernate"]


def trigger_channel(source: object) -> int | None:
    """Channel number of a trigger source (CHANnel2, CH2, C2), None for EXTernal, LINE and the like."""
    text = str(source).upper()
    for prefix in ("CHANNEL", "CHAN", "CH", "C"):
        if text.startswith(prefix) and text[len(prefix) :].isdigit():
            return int(text[len(prefix) :])
    return None


def memory_depth(value: object) -> int:
    """A point count as settings write it: 10000, "10k", "2.5M"."""
    if isinstance(value, (int, float)):
        return round(value)
    t = str(value).strip().upper()
    mult = {"K": 1_000, "M": 1_000_000, "G": 1_000_000_000}.get(t[-1:], 1)
    return round(float(t.rstrip("KMG")) * mult)


def ladder_down(value: float) -> float:
    """Largest 1-2-5 step not above ``value`` (sample rates come in such steps)."""
    k = math.floor(math.log10(value))
    for m in (5.0, 2.0, 1.0):
        if m * 10.0**k <= value * (1 + 1e-12):
            return m * 10.0**k
    return 10.0 ** (k - 1) * 5.0


def _upper(values: Any) -> set[str]:
    return {str(v).upper() for v in values or ()}


_ANALOG_IN = PortSpec("in", "signal")
_SAMPLES_IN = PortSpec("in", "samples")
_SAMPLES_OUT = PortSpec("out", "samples")


def _channel_of(port: str) -> int:
    """The channel of a per-channel port (``in3``, ``out3``)."""
    return int(port.lstrip("inout"))


def _displayed(comp: Component) -> list[int]:
    """The channels that are on (``ch<n>.display``): the ones an acquisition samples."""
    assert comp.owner is not None
    return [n for n in range(1, comp.owner.channel_count + 1) if comp.owner.get_state(f"ch{n}.display")]


@component("scope_adc")
class ScopeAdc(Component):
    """The ADC group shared by a scope's channels: where each channel's analog signal becomes
    samples (D67), its resolution, top sample rates and interleaving.

    ``in<n>`` takes channel n's signal in display volts (the probe module's output); ``out<n>``
    gives its sample stream. A frame is made in this order: the signal is evaluated at the
    request's instants, less the channel's skew (a digital delay; ``probe``); the input noise of
    the channel's vertical division (``vertical``, D46) is added, drawn from the request's random
    stream; the sum is quantized. The samples are the codes, kept in volts (D46) — the format says
    how many bits and what one code step is worth.

    ``bits`` (0: records are not quantized) and ``codes_per_div`` (0: ``2**bits / divs``) set the
    quantization; ``offset_sign`` says whether the offset setting is added to or subtracted from the
    volts. ``max_rate`` is the top sample rate (0: no limit); channels of a ``rate_pairs`` pair share
    a converter, so with both on the top rate is ``max_rate_paired``.

    ``min_full_scale`` (V at the input; 0: none) is the hardware's smallest range: a division
    smaller than ``min_full_scale / divs`` magnifies that range, so the code step stays the range's
    (a ``codes_per_div``-th of ``min_full_scale / divs``) and the converter clips at the range's
    edges, not the screen's (#1265).

    The converter's own non-idealities (#1266), referred to a sine that spans the converter's range
    (0 dBFS): ``distortion_table`` rows ``[V/div at the input, HD2 dBc, HD3 dBc]`` (the row of the
    largest division not above the setting) bend the signal around the middle of the range with
    x² and x³ terms — a smaller signal has less, HD2 falling 6 dB and HD3 12 dB per halving, as a
    polynomial's do; ``interleave`` sub-converters on the ``max_rate`` grid differ by one cosine
    pattern in gain (``interleave_image_dbc``: each image at k·fs/N ± f) and offset
    (``interleave_spur_dbfs``: the spur at k·fs/N); ``aperture_jitter_s`` moves each sample instant
    by its own draw (σ rms), whose noise on a sine of frequency f is 2πf·σ of it. Unset (the
    defaults), none is drawn and the frames are what they were."""

    label = "ADC 组"
    label_en = "ADC group"
    about = "各通道共用的 ADC：分辨率、最高采样率和交织。"
    about_en = "The ADC group the channels share: resolution, top sample rates and interleaving."

    port_pattern = r"(in|out)\d+"  # in1..inN and out1..outN are created by the instrument wiring
    refs = {"vertical": ("scope_vertical", "channel"), "probe": ("scope_probe", "channel")}
    # The acquisition macro fills these from the acquisition's own (library/macros.py); a frame
    # asks for the vertical module when it is made.
    optional_refs = frozenset({"vertical", "probe"})
    defaults = {
        "bits": 0,  # 0: records are not quantized
        "codes_per_div": 0.0,  # 0: 2**bits / divs
        "divs": 10.0,  # divisions the ADC spans
        "offset_sign": "add",  # add | subtract: how the offset setting enters the codes
        "max_rate": 0.0,  # top sample rate, Sa/s (0: no limit)
        "max_rate_paired": 0.0,  # the top rate when both channels of a rate pair are on
        "rate_pairs": [],  # channels sharing a converter, e.g. [[1, 2], [3, 4]]
        "min_full_scale": 0.0,  # the hardware's smallest range, V at the input across divs (0: none)
        "distortion_table": None,  # [[V/div at the input, HD2 dBc, HD3 dBc], ...] at 0 dBFS (None: none)
        "interleave": 0,  # sub-converters interleaved on the max_rate grid (0, 1: none)
        "interleave_image_dbc": None,  # their gain mismatch: each image at k*fs/N +- f, dBc (None: none)
        "interleave_spur_dbfs": None,  # their offset mismatch: the spur at k*fs/N, dBFS (None: none)
        "aperture_jitter_s": 0.0,  # the sampling clock's aperture jitter, s rms
        "vertical": "",  # the per-channel scope_vertical component: the scale, offset and input noise
        "probe": "",  # the per-channel scope_probe component: its ratio (noise) and skew
    }
    units = {
        "max_rate": "Sa/s", "max_rate_paired": "Sa/s", "min_full_scale": "V", "aperture_jitter_s": "s",
    }  # fmt: skip

    @classmethod
    def spec_of(cls, port: str) -> PortSpec | None:
        if cls.port_pattern and re.fullmatch(cls.port_pattern, port):
            return _ANALOG_IN if port.startswith("in") else _SAMPLES_OUT
        return None

    def codes_per_div(self) -> float:
        bits = int(self.param("bits"))
        return float(self.param("codes_per_div")) or (1 << bits) / float(self.param("divs"))

    def converter_scale(self, scale: float, ratio: float = 1.0) -> float:
        """The V/div one code step is a ``codes_per_div``-th of, in display volts: the displayed
        ``scale``, or below it the hardware's smallest range (``min_full_scale`` over ``divs``,
        given at the input; ``ratio`` the display volts per input volt, #1265)."""
        floor = float(self.param("min_full_scale")) / float(self.param("divs")) * ratio
        return max(scale, floor)

    def _distortion(self, input_scale: float) -> tuple[float, float]:
        """The x², x³ coefficients of the front end at this input V/div (#1266): HD2 = a2/2 and
        HD3 = a3/4 on a sine that spans the converter's range."""
        rows = sorted((float(r[0]), float(r[1]), float(r[2])) for r in self.param("distortion_table") or ())
        if not rows:
            return 0.0, 0.0
        row = next((r for r in reversed(rows) if r[0] <= input_scale * (1 + 1e-9)), rows[0])
        return 2.0 * 10 ** (row[1] / 20), 4.0 * 10 ** (row[2] / 20)

    def top_rate(self, on: set[int]) -> float:
        """The top sample rate with channels ``on`` displayed (inf when unlimited)."""
        top = float(self.param("max_rate")) or math.inf
        if any(set(pair) <= on for pair in self.param("rate_pairs") or ()):
            top = float(self.param("max_rate_paired")) or top
        return top

    def quantize(
        self, volts: np.typing.NDArray[np.float64], scale: float, offset: float, ratio: float = 1.0
    ) -> np.typing.NDArray[np.float64]:
        """What the ADC keeps: the codes of ``bits``, back in volts; ``ratio`` the display volts per
        input volt, as ``converter_scale`` takes it (#1294: the same floor as ``view``)."""
        return self._codes(volts, self.converter_scale(scale, ratio), offset)

    def rails(self, rec: Any) -> np.typing.NDArray[np.float64]:
        """A record's converter limits [top, bottom] in display volts: the codes of its own code
        scale (``Record.code_scale``, the floor already in display volts, #1294)."""
        return self._codes(np.array([1e30, -1e30]), float(getattr(rec, "code_scale", rec.scale)), rec.offset)

    def _codes(
        self, volts: np.typing.NDArray[np.float64], code_scale: float, offset: float
    ) -> np.typing.NDArray[np.float64]:
        add = str(self.param("offset_sign")).lower() == "add"
        return quantize(volts, code_scale, offset, int(self.param("bits")), self.codes_per_div(), add)

    # ---- the sample stream ------------------------------------------------------------------------

    def _vertical(self, n: int) -> Any:
        """Channel n's scope_vertical module."""
        return self.peer(f"ch{n}.{self.param('vertical')}", required=True)

    def _probe(self, n: int) -> Any:
        """Channel n's scope_probe module, None when the scope has none."""
        if not self.param("probe"):
            return None
        return self.peer(f"ch{n}.{self.param('probe')}", required=True)

    def analog(self, n: int) -> Signal:
        """Channel n's signal at the converter's input, in display volts: what the trigger
        comparator taps."""
        return self.input(f"in{n}")

    def frames(self, port: str, request: FrameRequest) -> SampleFrame:
        return self.view(_channel_of(port)).frame(request)

    def view(self, n: int) -> AdcView:
        """What channel n's frames are computed from now, as values (a deep record keeps it)."""
        vertical, probe = self._vertical(n), self._probe(n)
        scale = self.converter_scale(float(vertical.param("scale")), float(vertical.param("probe_ratio")))
        bits, per_div = int(self.param("bits")), self.codes_per_div()
        image, spur = self.param("interleave_image_dbc"), self.param("interleave_spur_dbfs")
        top = float(self.param("max_rate"))
        return AdcView(
            signal=self.analog(n),
            skew=probe.delay() if probe is not None else 0.0,
            sigma=vertical.input_noise_rms() * (float(probe.param("ratio")) if probe is not None else 1.0),
            seed=self.session.seed,
            scale=scale,
            offset=float(vertical.param("offset")),
            bits=bits,
            per_div=per_div,
            add=str(self.param("offset_sign")).lower() == "add",
            hd=self._distortion(vertical.input_scale()),
            half=converter_half(scale, bits, per_div, float(self.param("divs"))),
            ilv=int(self.param("interleave")),
            ilv_gain=0.0 if image is None else 2.0 * 10 ** (float(image) / 20),
            ilv_offset=0.0 if spur is None else 10 ** (float(spur) / 20),
            native_dt=1.0 / top if top > 0 else 0.0,
            aperture=float(self.param("aperture_jitter_s")),
        )

    def sample_interval(self, port: str) -> float:
        """The converter's own interval: its top rate with the channels that are on. One without a
        top rate has no rate of its own — it samples whenever it is asked (read the decimator's
        output, at the record's rate)."""
        top = self.top_rate(set(_displayed(self)))
        if not math.isfinite(top):
            raise ScpiError(
                -221, f"{self.uid}: no top sample rate (max_rate is 0); it runs at the rate asked of it"
            )
        return 1.0 / top


@component("scope_decimator")
class ScopeDecimator(Component):
    """From the ADC's rate to the record's: the acquisition modes (F4, D46, D67).

    ``in<n>`` takes channel n's samples at the converter's rate, ``out<n>`` gives them at the
    record's rate, after ``processing``:

    * ``normal`` keeps them;
    * ``average`` (``averages``) is the mean of that many acquisitions, each of them noisy and
      quantized, so the noise falls by √N and the resolution gains (it takes N acquisitions' time);
    * ``peak`` examines each record interval at the ADC's native rate (at most
      ``peak_max_oversample`` samples per record sample) and keeps, of the interval's smallest and
      largest code, the one that departs farther from its ends — a pulse the record rate would step
      over shows up;
    * ``eres`` smooths ``4**eres_bits`` codes (a boxcar), so the noise falls by ``2**eres_bits`` —
      that many more effective bits, at the boxcar's bandwidth.

    A request's ``index0`` is counted on the converter's grid (:meth:`oversample` samples per record
    sample): the k-th acquisition of an average continues the random stream after the previous
    one's samples there.
    """

    label = "抽取与采集方式"
    label_en = "Decimation and acquisition mode"
    about = "从 ADC 采样率降到记录采样率：各采集方式。"
    about_en = "From the ADC's rate to the record's: the acquisition modes."

    port_pattern = r"(in|out)\d+"  # in1..inN and out1..outN are created by the instrument wiring
    refs = {"adc": ("scope_adc", "instrument"), "horizontal": ("scope_timebase", "instrument")}
    optional_refs = frozenset({"adc", "horizontal"})  # filled by the acquisition macro; asked for at a frame
    defaults = {
        "adc": "",  # the scope_adc component: its top rate
        "horizontal": "",  # the scope_timebase component: the record's rate
        "processing": "normal",  # normal | average | peak | eres: what the quantized codes go through
        "averages": 1,  # acquisitions an average is the mean of (processing: average)
        "eres_bits": 0.0,  # effective bits a high-resolution acquisition gains (boxcar of 4^b codes)
        "peak_max_oversample": 64,  # native samples a peak-detect interval examines at most
    }

    @classmethod
    def spec_of(cls, port: str) -> PortSpec | None:
        if cls.port_pattern and re.fullmatch(cls.port_pattern, port):
            return _SAMPLES_IN if port.startswith("in") else _SAMPLES_OUT
        return None

    def _modules(self) -> tuple[Any, Any]:
        """The scope_adc and the scope_timebase this block reads its rates from."""
        return (
            self.peer(str(self.param("adc")), required=True),
            self.peer(str(self.param("horizontal")), required=True),
        )

    def averages(self) -> int:
        """Acquisitions one record takes (1 unless averaging)."""
        return max(1, int(self.param("averages"))) if str(self.param("processing")) == "average" else 1

    def oversample(self) -> int:
        """Converter samples per record sample. Peak detect samples the ADC's native rate (F4),
        capped so a deep record at a slow timebase stays feasible — the real detector is hardware
        too; every other mode takes the record's rate."""
        if str(self.param("processing")) != "peak":
            return 1
        adc_unit, horizontal = self._modules()
        top = adc_unit.top_rate(set(_displayed(self)))
        rate = horizontal.sample_rate(top)
        if rate > 0 and top > rate:
            return max(1, min(int(self.param("peak_max_oversample")), round(top / rate)))
        return 1

    def frames(self, port: str, request: FrameRequest) -> SampleFrame:
        source = f"in{_channel_of(port)}"
        return self.view().frame(
            request,
            lambda r: self.input_frames(source, r),
            lambda samples: self.session.reserve_frames(self, source, samples),
        )

    def view(self) -> DecimatorView:
        """The acquisition mode now, as values (a deep record keeps it)."""
        proc = str(self.param("processing"))
        m = self.oversample()
        return DecimatorView(
            proc=proc,
            m=m,
            count=self.averages() if m == 1 else 1,
            w=round(4.0 ** float(self.param("eres_bits"))) if proc == "eres" else 1,
        )

    def sample_interval(self, port: str) -> float:
        """The record's sample interval with the current settings."""
        adc_unit, horizontal = self._modules()
        return float(horizontal.timing(adc_unit.top_rate(set(_displayed(self))))[1])

    def input_interval(self, port: str) -> float:
        """The converter's interval behind one record sample: the record's, over the oversampling."""
        return self.sample_interval(port) / self.oversample()


@component("scope_timebase")
class ScopeTimebase(Component):
    """The horizontal system: time per division, the record length rule and the trigger position.

    ``record_length``: ``fixed`` (``points`` over ten divisions), ``exact`` (the rate is
    ``points / 10 div`` up to the ADC group's top rate), ``rate_ladder`` (that rate rounded down
    to a 1-2-5 step; samples ``1 / rate`` apart) or ``rate_pow2`` (rounded down to the top rate
    divided by a power of two, the Infiniium UXR's rates, #602). A ``sample_rate`` above 0 sets the
    rate instead of the points (a manual rate takes precedence over the memory depth, the
    Infiniium guide's :ACQuire:SRATe): rounded up to the next rate of the rule's steps, at most the
    top rate and at most ``max_points / 10 div``; the record then holds ``rate x 10 div`` points. The trigger sits at the centre, or ``delay``
    seconds before it when that is bound. A model with a reference mode setting places it from
    ``reference_mode`` (DELay / POSition) and, in POSition mode, from ``reference_position``
    percent across the record.

    The sampling clock has a frequency error ``clock_ppm`` (this unit's own, a ``deviations``
    draw); with ``rosc`` EXTernal it follows the 10 MHz on ``ref`` instead, whose fractional
    error the instrument inherits (instrument-authoring.md 8.4, G3). A fast clock takes samples
    closer together than the nominal interval, so the records keep the nominal dt the scope
    reports and time measurements read low — the spectrum analyzer's reference convention.
    """

    label = "时基"
    label_en = "Timebase"
    about = "水平系统：每格时间、记录长度规则和触发位置。"
    about_en = "The horizontal system: time per division, record length and trigger position."

    ports = {"ref": PortSpec("in", "signal", "REF_IN")}
    defaults = {
        "scale": "$timebase.scale",  # seconds per division (ten divisions across)
        "delay": None,  # unbound: the trigger at the centre of the record
        "record_length": "fixed",  # fixed | exact | rate_ladder | rate_pow2
        "sample_rate": 0.0,  # Sa/s set by the user (a setting); 0: the rate follows the points
        "max_points": 0,  # the most points a record holds with a set sample_rate; 0: no limit
        "points": "$waveform.points",  # record points or memory depth (10000, "10k")
        "delay_range_divs": None,  # [min, max] of delay in divisions, e.g. [-5000, 5]; None: any
        "reference_divs": 5.0,  # where the trigger (delay 0) sits, divisions from the left; may map a setting
        # the reference as 0-100 percent of screen (a setting, :TIMebase:REFerence:PERCent, #1067);
        # set (not None) it takes the place of reference_divs, the delay still moving the trigger
        "reference_percent": None,
        # A model with a reference mode setting (DELay | POSition) places the trigger from it: in
        # POSition mode the trigger sits `reference_position` percent across the record and the
        # delay setting does not move it; in DELay mode it starts at the centre, moved by `delay`.
        "reference_mode": "",  # the reference mode setting; empty: the delay alone places the trigger
        "reference_position": None,  # the trigger position setting, 0..100 percent (POSition mode)
        "clock_ppm": 0.0,  # the sampling clock's frequency error, ppm (hidden)
        "rosc": "INTernal",  # INTernal | EXTernal: the clock follows the 10 MHz on ref
        "lock_ppm": 10.0,  # the reference's lock range around 10 MHz, ppm
        "lock_min_v": 0.05,  # the smallest reference amplitude it locks to, V peak
        "lock_max_v": math.inf,  # the largest, V peak (above it the internal clock takes over)
    }
    units = {"scale": "s", "delay": "s", "clock_ppm": "ppm"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._ref_error = 0.0  # fractional frequency error of the reference in use
        self._ref_locked = False

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        """Refuse a delay outside ``delay_range_divs`` (-222, the old value kept)."""
        limits = self.param("delay_range_divs")
        if limits is None or key != "delay":
            return
        tb = float(self.param("scale"))
        lo, hi = (float(x) * tb for x in limits)
        if not lo - abs(lo) * 1e-9 <= float(new) <= hi + abs(hi) * 1e-9:
            raise ScpiError(-222, f"delay outside [{limits[0]} div, {limits[1]} div]")

    def window(self) -> float:
        return DIVS_H * float(self.param("scale"))

    def sample_rate(self, top: float) -> float:
        """The sample rate under the ADC group's top rate ``top``."""
        window = self.window()
        rule = str(self.param("record_length"))
        wanted = float(self.param("sample_rate") or 0.0)
        if wanted > 0 and rule != "fixed":
            rate = min(top, self._step_up(wanted, top, rule))
            most = memory_depth(self.param("max_points") or 0)
            if most > 0 and rate * window > most:
                rate = self._step_down(most / window, top, rule)
            return rate
        depth = memory_depth(self.param("points"))
        if rule == "fixed":
            return depth / window
        return min(top, self._step_down(depth / window, top, rule))

    @staticmethod
    def _step_down(rate: float, top: float, rule: str) -> float:
        """``rate`` rounded down to the rule's steps (exact: as it is)."""
        if rule == "rate_ladder":
            return ladder_down(rate)
        if rule == "rate_pow2":
            if rate >= top:
                return top
            return top / 2.0 ** math.ceil(math.log2(top / rate) - 1e-12)
        return rate

    @staticmethod
    def _step_up(rate: float, top: float, rule: str) -> float:
        """``rate`` rounded up to the rule's next step (the guide: "rounded to the next fastest")."""
        if rule == "rate_ladder":
            down = ladder_down(rate)
            if down >= rate * (1 - 1e-12):
                return down
            return ladder_down(down * 2.5 * (1 + 1e-12))  # the next 1-2-5 step
        if rule == "rate_pow2":
            if rate >= top:
                return top
            return top / 2.0 ** math.floor(math.log2(top / rate) + 1e-12)
        return rate

    def timing(self, top: float) -> tuple[int, float, int, int]:
        """Points, sample interval, samples before the trigger and samples from it on."""
        window = self.window()
        rule = str(self.param("record_length"))
        if rule == "fixed":
            points = memory_depth(self.param("points"))
            dt = window / points
        else:
            rate = self.sample_rate(top)
            points = max(2, round(rate * window))
            stepped = rule in ("rate_ladder", "rate_pow2") or float(self.param("sample_rate") or 0.0) > 0
            dt = 1.0 / rate if stepped else window / points
        # Sample 0 is at delay - 5 div: the trigger sits (5 div - delay) / dt in (no delay: the centre).
        # A reference mode setting places the trigger differently: in POSition mode the position
        # setting puts it that far across the record (the delay setting no longer moves it).
        delay = self.param("delay")
        mode = str(self.param("reference_mode") or "").upper()
        position = self.param("reference_position")
        percent = self.param("reference_percent")
        if percent not in (None, ""):
            # the reference as a percent of screen (:TIMebase:REFerence:PERCent, #1067); the delay
            # still moves the trigger
            ref = window * float(percent) / 100.0
            if delay in (None, "") and abs(ref - window / 2) < 1e-15 * window:
                pre = points // 2
            else:
                pre = round((ref - float(delay or 0.0)) / dt)
        elif mode.startswith("POS") and position not in (None, ""):
            ref = window * float(position) / 100.0
            pre = round(ref / dt)
            pre = max(0, min(points - 1, pre))  # the record still has one sample after the trigger
        else:
            ref = float(self.param("reference_divs")) * float(
                self.param("scale")
            )  # trigger from the left edge
            if delay in (None, "") and abs(ref - window / 2) < 1e-15 * window:
                pre = points // 2
            else:
                pre = round((ref - float(delay or 0.0)) / dt)
        return points, dt, pre, points - pre

    def clock_error(self) -> float:
        """The sampling clock's fractional frequency error: the internal ppm, or the reference's
        when ``rosc`` is EXTernal and a usable 10 MHz is on ``ref``."""
        if str(self.param("rosc")).upper().startswith("EXT"):
            self._update_clock()
            if self._ref_locked:
                return self._ref_error
        return float(self.param("clock_ppm")) * 1e-6

    def _update_clock(self) -> None:
        """The 10 MHz on ``ref``: within ``lock_ppm`` and between ``lock_min_v`` and ``lock_max_v``,
        it is the clock (as the spectrum analyzer accepts its reference); anything else leaves the
        internal clock in charge."""
        self._ref_locked = False
        sig = self.input("ref").merged()
        if sig.freq.size:
            k = int(np.argmax(np.abs(sig.amp)))
            f = float(sig.freq[k])
            a = abs(sig.amp[k])
            lo, hi = float(self.param("lock_min_v")), float(self.param("lock_max_v"))
            if abs(f - 10e6) <= 10e6 * float(self.param("lock_ppm")) * 1e-6 and lo < a <= hi:
                self._ref_error, self._ref_locked = f / 10e6 - 1, True

    # ---- reference position, AUTO controls (:TIMebase, #1067) -----------------------------------------

    @op("set_reference")
    def set_reference(self, query: bool = False, word: str = "", **_: Any) -> OpResult:
        """``:TIMebase:REFerence``: the reference at LEFT, CENTer or RIGHt — 10, 50 or 90 percent
        of screen, the same setting :TIMebase:REFerence:PERCent writes. The query answers the
        word, PERC once a percent position is set (the guide, p. 1472)."""
        assert self.owner is not None
        path = self.bound_path("reference_percent")
        if path is None:
            raise ScpiError(-300, f"{self.uid}: reference_percent is not bound to a setting")
        words = {"LEFT": 10, "CENT": 50, "RIGH": 90}
        written = {"LEFT": 10, "CENT": 50, "CENTER": 50, "RIGH": 90, "RIGHT": 90}
        if query:
            pct = int(self.owner.get_state(path))
            word = {v: k for k, v in words.items()}.get(pct, "PERC")
            # the long-form echo answers the model's own words (SYSTem:LONGform)
            if self.owner.get_state("system.longform"):
                word = {v: k for k, v in written.items()}.get(pct, "PERCent")
            return OpResult(word.encode())
        w = V.parse_string(word).upper() if isinstance(word, bytes) else str(word).upper()
        if w not in written:
            raise ScpiError(-224, f"{word}: no such reference (LEFT, CENTer, RIGHt)")
        self.owner.set_state(path, written[w])
        return OpResult()

    def auto_control(self, key: str, query: bool, on: bool | None, actual: Any = None) -> OpResult:
        """The AUTO on/off control of the ``points`` or ``sample_rate`` setting (the acquisition's
        ``:ACQuire:POINts:AUTO``/``:SRATe:AUTO``, #1067): the query answers whether the setting
        sits at its AUTO value, ON moves it there, OFF makes the present value (the actual rate,
        for a sample rate) a manual one."""
        assert self.owner is not None
        path = self.bound_path(key)
        if path is None:
            raise ScpiError(-300, f"{self.uid}: {key} is not bound to a setting")
        spec = self.owner.specs[path]
        auto = (spec.special or {}).get("AUTO")
        if auto is None:
            raise ScpiError(-300, f"{self.uid}: {path} has no AUTO value")
        if query:
            return OpResult(b"1" if self.owner.get_state(path) == auto else b"0")
        if on is None:
            raise ScpiError(-109, "ON or OFF is required")
        if V.flag(on):
            self.owner.set_state(path, auto)
        elif self.owner.get_state(path) == auto:
            self.owner.set_state(path, actual() if actual is not None else auto)
        return OpResult()


@dataclass
class TriggerHit:
    """Where a trigger search found its edge: the instant (seconds after the search's base
    instant, interpolated between samples) and the first record-grid sample at or after it."""

    t_s: float
    index: int


@dataclass
class _Crossing:
    """A crossing of the trigger level found on a search grid: between the samples at ``before``
    and ``at`` (s), ``estimate`` linear between them (``final``: that is the instant; otherwise
    the grid was coarse and the instant is still to be bisected). ``direction`` None: no crossing,
    only how far the search has looked."""

    direction: str | None
    before: float
    at: float
    estimate: float
    final: bool


def _between(t_p: float, v_p: float, t_k: float, v_k: float, level: float) -> float:
    """Where the line from (t_p, v_p) to (t_k, v_k) meets ``level``, kept within the two instants."""
    frac = (level - v_p) / (v_k - v_p) if v_k != v_p else 1.0
    return t_p + min(max(frac, 0.0), 1.0) * (t_k - t_p)


def _period(sig: Signal) -> float | None:
    """The time after which the signal repeats (its lines are harmonics of one frequency), None
    when it has no line."""
    f = np.unique(sig.freq[(sig.freq > 0) & (np.abs(sig.amp) > 0)])
    if not f.size:
        return None
    g = float(f[0])
    for x in f[1:]:
        a, b = float(x), g
        while b > 1e-9 * float(x):  # Euclid, a remainder within rounding of a multiple is none
            r = a - b * round(a / b)
            a, b = b, (0.0 if abs(r) < 1e-9 * a else a % b)
        g = a
    return 1.0 / g


#: The trigger families that compare against both thresholds or the signal's state (#1184): each
#: keeps its own source setting and shares the edge search's machinery.
_ADVANCED = ("runt", "transition", "window", "timeout", "nedge")


@component("scope_trigger")
class ScopeTrigger(_ChannelBound):
    """Edge and pulse-width (glitch) trigger on its own analog path (instrument-authoring.md 8.4,
    G5).

    The trigger looks at the signal before the ADC, through its own ``coupling`` filter (a table
    ``filters`` of high-pass and low-pass corners per coupling value: AC, low- and high-frequency
    reject), a ``reject`` filter alongside it and ``hf_reject`` (filters keyed HF), with its own
    ``noise_rms``. An edge counts once the signal has been beyond the
    ``hysteresis_divs`` band on the far side of the level (``reject_hysteresis_divs`` while
    ``noise_reject`` is on); the instant is interpolated between samples, so a record reports the
    trigger to a fraction of a sample. After a trigger, the next is not taken before ``holdoff``
    seconds (while ``holdoff_on``).

    ``source``, ``level`` and ``slope`` select the edge (``rising`` and ``falling`` list the slope
    names that take each); a level or slope per channel is bound with the channel number written
    ``{n}``: ``$ch{n}.trigger_level``. ``external`` sources read the ``ext`` port (wired from an
    instrument input); ``line`` sources trigger on the rising zero crossings of the mains
    (``line_hz``, phase zero at the session's start). An auto mode looks ``search_windows``
    records ahead on the record's grid and on as far as ``auto_timeout_s`` of virtual time beyond
    (its auto timeout: a signal that repeats within it triggers at any timebase, #1331); one that
    finds nothing still takes only ``search_windows`` records. A normal or single acquisition
    waits up to ``search_horizon_s`` of virtual time for its edge. Beyond the record's grid the
    search takes a coarser one, and an edge found there is bisected on the signal (without the
    trigger's noise) to a thousandth of a record sample.

    ``mode`` ``glitch`` triggers on a pulse by its width instead (signal-chain.md 7, #956): a pulse
    of ``polarity`` runs from one crossing of the level to the next opposite one — the same
    interpolated crossings, hysteresis, coupling, noise and holdoff as the edge trigger — and
    ``qualifier`` compares that time with ``width_low`` and ``width_high``: ``less`` (narrower
    than ``width_high``), ``range`` (between the two) and ``outside`` (narrower than ``width_low``
    or wider than ``width_high``) trigger at the edge that ends the pulse; ``greater`` (wider than
    ``width_low``) triggers at the instant the pulse has lasted ``width_low`` (it need not have
    ended), or with ``greater_at: end`` at the edge that ends it. The
    whole pulse lies after the trigger is armed: one that began within the pre-trigger time or the
    holdoff does not count. ``slope`` plays no part. The search takes the record grid for as many
    samples as ``max_search_points`` allows (fewer for a signal of many lines), then a grid an
    eighth of the narrowest width bound the qualifier uses for as many again, the instants there
    bisected on the signal (without trigger noise); a pulse narrower than that grid can be missed
    there, and an acquisition that finds nothing within that reach waits, looking again at the
    next command (#1000).

    The advanced families (#1184) keep the same machinery on the steady-state signal — an object
    to trigger on without transient signals (C7): ``mode`` ``runt`` fires on a pulse that crosses
    ``low_threshold`` but never ``high_threshold`` (a negative one the mirror below
    ``high_threshold``), ``transition`` on an edge whose time between the two thresholds fails
    ``transition_mode`` (``gthan``/``lthan`` against ``transition_time``, ``range``/``orange``
    against ``transition_range_low``/``transition_range_high``), ``window`` on the signal entering or leaving the band between
    the two thresholds (``window_condition``; ``window_tpoint`` at the boundary instant or after
    ``window_time`` of staying put, and ``inside``/``outside`` conditions with a ``greater``/
    ``less`` duration qualifier), ``timeout`` when the signal has stayed high, low or unchanged
    for ``timeout_time``, and ``nedge`` on the ``nedge_count``-th edge of the family's slope.
    Each family keeps its own ``<family>_source`` (the edge trigger's source until one is set);
    the runt, transition and window comparisons read the per-source ``high_threshold``/
    ``low_threshold`` (``high_by_source``/``low_by_source`` name the sources' settings, like
    ``level_by_source``), and the hysteresis band follows ``hysteresis_setting`` — ``normal``,
    ``nreject`` (the noise-reject band) or ``hsensitivity`` (``hsensitivity_divs``, half the
    normal band when unset). An unset threshold leaves runt, transition and window nothing to
    compare against: the search finds nothing and a waiting acquisition looks again at the next
    command.
    """

    label = "触发"
    label_en = "Trigger"
    about = "在独立模拟通路上的边沿触发和脉宽（毛刺）触发。"
    about_en = "Edge and pulse-width (glitch) trigger on its own analog path."

    ports = {"ext": PortSpec("in", "signal", "external trigger input")}
    defaults = {
        "source": "$trigger.source",  # a channel (C1, CHANnel1), an external or a line source
        "level": "$trigger.level",  # display volts; per channel: $ch{n}.trigger_level
        # Each source's own level setting, by source word (#248), e.g. {CHANnel1: trigger.level1,
        # EXTernal: trigger.level5}; a source not listed uses `level`.
        "level_by_source": {},
        "slope": "$trigger.slope",  # per channel: $ch{n}.trigger_slope
        "rising": RISING,  # slope values that take a rising edge
        "falling": FALLING,  # slope values that take a falling edge
        "search_windows": 4,  # records an auto mode looks ahead on the record's grid
        "auto_timeout_s": 0.05,  # virtual time an auto mode looks ahead for an edge in all, s (#1331)
        "search_horizon_s": 10.0,  # virtual time a normal or single acquisition waits for an edge, s
        "max_search_points": 4_000_000,  # samples a search may take (a long horizon is searched coarser)
        "level_range_divs": None,  # the level stays within +-this many divisions of the source's screen
        "coupling": "DC",  # the trigger path's coupling: a key of filters, or DC
        "reject": "",  # a reject filter alongside the coupling (LFReject, HFReject): a key of filters
        "hf_reject": False,  # on: the filters keyed HF as well (a separate high-frequency reject)
        # coupling or reject -> [[highpass | lowpass, corner Hz], ...], e.g. {AC: [[highpass, 8]]}
        "filters": {},
        "hysteresis_divs": 0.16,  # half the band an edge must cross, in divisions of the source
        "noise_reject": False,  # on: the wider band below
        "reject_hysteresis_divs": 0.5,  # the band while noise_reject is on
        "noise_rms": 0.0,  # the trigger path's own noise, display volts rms
        # the trigger path's noise by the source's division: [[V/div, V rms], ...] (the row of the
        # largest division not above the source's), added to noise_rms (None: none, #1263)
        "noise_table": None,
        "bandwidth": math.inf,  # the trigger path's own bandwidth, Hz: a first-order low-pass (#1263)
        "holdoff_on": True,  # whether holdoff applies (e.g. a holdoff mode that is TIME)
        "holdoff": 0.0,  # s after a trigger before the next one is taken
        "external": ["EX", "EX5", "EXTernal", "EXT"],  # sources read from the ext port
        "line": ["LINE"],  # sources that trigger on the mains
        "line_hz": 50.0,  # mains frequency
        "mode": "edge",  # the trigger type: edge, glitch (a pulse by its width), runt,
        # transition, window, timeout or nedge (the Nth edge); each non-edge family keeps its own
        # source (see <family>_source) while edge and glitch share the main one
        "polarity": "positive",  # glitch: the pulse above the level (positive) or below it (negative)
        # glitch: narrower than width_high (less), wider than width_low (greater), between (range),
        # narrower than width_low or wider than width_high (outside)
        "qualifier": "less",
        "greater_at": "timeout",  # glitch greater: trigger once the pulse lasted width_low (timeout) or at its end
        "width_low": 20e-9,  # glitch: the width a greater or range pulse exceeds, s
        "width_high": 30e-9,  # glitch: the width a less or range pulse stays under, s
        # ---- the advanced families (#1184) ----
        "high_threshold": None,  # display volts, runt/transition/window; per source like the level
        "low_threshold": None,  # display volts, the pair's other half; per source like the level
        "high_by_source": {},  # each source's own threshold setting, by source word, e.g.
        "low_by_source": {},  # {CHANnel1: trigger.hth1, CHANnel2: trigger.hth2}; unlisted: the fallback above
        "hysteresis_setting": "normal",  # :TRIGger:HYSTeresis: normal, nreject (the wider noise-reject
        # band) or hsensitivity (the narrower band below); the band the edges arm and cross within
        "hsensitivity_divs": None,  # the hsensitivity band, in divisions of the source; None: half the normal band
        "runt_source": None,  # the runt family's source word; None: the main source
        "runt_polarity": "positive",  # the pulse between low and high threshold (positive) or below high (negative)
        "runt_qualified": False,  # on: the runt's width must exceed runt_time as well
        "runt_time": 250e-12,  # the time a qualified runt exceeds, s (manual: 250 ps to 30 ns)
        "transition_source": None,  # the transition family's source word; None: the main source
        "transition_type": "risetime",  # rise time (rising edge) or fall time (falling edge)
        "transition_mode": "gthan",  # the violation: gthan/lthan transition_time, or inside
        # (range) / outside (orange) transition_range
        "transition_time": 1e-9,  # s, the greater/less bound
        "transition_range_low": 10e-9,  # s, the minimum of the range modes
        "transition_range_high": 20e-9,  # s, the maximum of the range modes
        "window_source": None,  # the window family's source word; None: the main source
        "window_condition": "enter",  # enter, exit, inside or outside the band; inside/outside take a
        "window_qualifier": "",  # duration qualifier: gthan (held window_time) or lthan (left before window_time)
        "window_time": 1e-6,  # s, the duration the condition is compared with
        "window_tpoint": "boundary",  # enter/exit fire at the boundary instant (boundary) or once the
        # new state has lasted window_time (timeout)
        "timeout_source": None,  # the timeout family's source word; None: the main source
        "timeout_condition": "unchanged",  # high, low or unchanged
        "timeout_time": 1e-3,  # s, the time the state lasts before the trigger
        "nedge_source": None,  # the nth-edge family's source word; None: the main source
        "nedge_slope": None,  # the counted edges' slope; None: the main slope
        "nedge_count": 1,  # the edge that triggers: the nedge_count-th (1 is the first)
    }
    units = {
        "level": "V", "holdoff": "s", "search_horizon_s": "s", "auto_timeout_s": "s", "line_hz": "Hz", "noise_rms": "V", "bandwidth": "Hz",
        "width_low": "s", "width_high": "s", "runt_time": "s", "transition_time": "s",
        "window_time": "s", "timeout_time": "s",
    }  # fmt: skip
    choices = {
        "mode": ("edge", "glitch", "runt", "transition", "window", "timeout", "nedge"),
        "polarity": ("positive", "negative"),
        "qualifier": ("less", "greater", "range", "outside"),
        "greater_at": ("timeout", "end"),
        "hysteresis_setting": ("normal", "nreject", "hsensitivity"),
        "runt_polarity": ("positive", "negative"),
        "transition_type": ("risetime", "falltime"),
        "transition_mode": ("gthan", "lthan", "range", "orange"),
        "window_condition": ("enter", "exit", "inside", "outside"),
        "window_qualifier": ("", "gthan", "lthan"),
        "window_tpoint": ("boundary", "timeout"),
        "timeout_condition": ("high", "low", "unchanged"),
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_ns: int | None = None  # the instant of the latest trigger (holdoff)
        # The latest search and its result: a search is a pure function of its inputs, and the
        # status registers ask the same one again after every command of a line (#1000).
        self._memo: tuple[str, TriggerHit | None] | None = None
        self._source_scale: float | None = None  # the division of the source being searched

    def reset(self) -> None:
        self.last_ns = None
        self._memo = None

    def _search_key(self, sig: Signal, args: tuple[Any, ...], n: int | None) -> str:
        """Everything a search's result depends on: its arguments, the signal's lines, the
        holdoff's anchor and every parameter as it resolves now (the level and slope included)."""
        lines = hashlib.blake2b(digest_size=16)
        for a in (sig.freq, sig.amp, sig.t0_ns):
            lines.update(np.ascontiguousarray(a).tobytes())
        level = self.source_level(n)
        slope = self.channel_param("slope", n) if n is not None else self.param("slope")
        params = []
        for k in sorted(self.defaults):
            try:
                params.append(self.param(k))
            except KeyError:  # bound per channel ({n}): the level and slope above resolve it
                params.append(None)
        return repr((args, sig.dc, lines.hexdigest(), self.last_ns, level, slope, params))

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        """Refuse a level off the source channel's screen by more than ``level_range_divs``
        divisions around its offset (-222, the old value kept)."""
        divs = self.param("level_range_divs")
        if divs is None or key != "level":
            return
        n = self.source_channel()
        acq = next(
            (c for c in (self.owner.components.values() if self.owner else ())
             if isinstance(c, ScopeAcquisition) and c.param("trigger") == self.name),
            None,
        )  # fmt: skip
        if n is None or acq is None:
            return
        scale, offset = acq.scale_offset(n)
        lim = float(divs) * scale
        if not -lim - offset - 1e-12 <= float(new) <= lim - offset + 1e-12:
            raise ScpiError(-222, f"trigger level outside +-{divs} div")

    def select_taps(self) -> list[str] | None:
        """The trigger compares the analog signal at the ADC's input, before the digital filters
        (#1263): per channel, the component wired to the acquisition's converter."""
        if self.owner is None:
            return None
        acq = next(
            (c for c in self.owner.components.values()
             if isinstance(c, ScopeAcquisition) and c.param("trigger") == self.name),
            None,
        )  # fmt: skip
        if acq is None:
            return None
        adc = cast(Any, acq).adc_unit()  # ty sees the @component class as Component
        taps = []
        for n in range(1, self.owner.channel_count + 1):
            src = self.owner.wiring.source(adc.name, f"in{n}")
            if src is not None:
                taps.append(src[0])
        return taps

    def source_channel(self, source: str | None = None) -> int | None:
        """The channel the source names, None for external, line and the like."""
        return trigger_channel(self.param("source") if source is None else source)

    def source_kind(self, source: str | None = None) -> str:
        """channel | external | line | none."""
        src = str(self.param("source") if source is None else source).upper()
        if self.source_channel(src) is not None:
            return "channel"
        if src in _upper(self.param("external")):
            return "external"
        if src in _upper(self.param("line")):
            return "line"
        return "none"

    def family_source(self) -> str:
        """The source word the current mode's family looks at: each advanced family keeps its own
        source setting (#1184), the edge and glitch families share the main one."""
        key = {
            "runt": "runt_source",
            "transition": "transition_source",
            "window": "window_source",
            "timeout": "timeout_source",
            "nedge": "nedge_source",
        }.get(str(self.choice("mode")))
        if key is None:
            return str(self.param("source"))
        own = self.param(key)
        return str(self.param("source")) if own in (None, "") else str(own)

    def threshold(self, which: str, source: str) -> float | None:
        """The ``high`` or ``low`` threshold of a source in display volts: the source's own
        setting (``high_by_source``/``low_by_source``, like the level's), else the fallback
        (``high_threshold``/``low_threshold``), else None — unset, nothing to compare against."""
        by = {str(k).upper(): str(v) for k, v in dict(self.param(f"{which}_by_source") or {}).items()}
        if by and self.owner is not None:
            src = str(source).upper()
            for word, path in by.items():
                if src in (word, V.short_form(word)):
                    return float(self.owner.get_state(path))
        value = self.param(f"{which}_threshold")
        return None if value is None else float(value)

    @op("window_condition")
    def op_window_condition(
        self, condition: Any = None, qualifier: Any = None, query: bool = False, **_: Any
    ) -> OpResult:
        """``:TRIGger:WINDow:CONDition {ENTer | EXIT | INSide[,{GTHan | LTHan}] | OUTSide[,…]}``
        (manual p.1587): the duration qualifier belongs to inside and outside, and the query
        answers it only for those two (the manual's returned format). The words arrive as the raw
        parameter's list."""
        assert self.owner is not None
        cond, qual = self.bound_path("window_condition"), self.bound_path("window_qualifier")
        if cond is None or qual is None:
            raise ScpiError(-300, "the window condition is not bound to a setting")
        if query:
            word = str(self.owner.get_state(cond))
            if word in ("INSide", "OUTSide"):
                word = f"{word},{self.owner.get_state(qual)}"
            return OpResult(",".join(V.short_form(part) for part in word.split(",")).encode())
        given = list(condition) if isinstance(condition, list) else ([] if condition is None else [condition])

        def word(value: Any, options: tuple[str, ...]) -> str:
            raw = value if isinstance(value, bytes) else str(value).encode()
            return V.parse_enum(raw, options)

        if not given:
            raise ScpiError(-109, "a condition is required")
        words = word(given[0], ("ENTer", "EXIT", "INSide", "OUTSide"))
        self.owner.set_state(cond, words)
        if words in ("INSide", "OUTSide"):
            if len(given) > 1:  # the qualifier is optional: given, it replaces the setting
                self.owner.set_state(qual, word(given[1], ("GTHan", "LTHan")))
        elif len(given) > 1:
            raise ScpiError(
                -108, f"the qualifier belongs to {V.short_form('INSide')} and {V.short_form('OUTSide')}"
            )
        return OpResult()

    def level(self) -> float:
        """The trigger level in display volts (the scope screen draws it). With ``level_by_source``
        each source keeps its own level (#248: :TRIGger:LEVel <level>,<source>)."""
        by_source = {str(k).upper(): str(v) for k, v in dict(self.param("level_by_source") or {}).items()}
        if by_source and self.owner is not None:
            src = str(self.param("source")).upper()
            for word, path in by_source.items():
                if src in (word, V.short_form(word)):
                    return float(self.owner.get_state(path))
        level = self.channel_param("level", self.source_channel())
        return 0.0 if level is None else float(level)

    def source_level(self, n: int | None) -> float:
        """The level of the source a search looks at: channel ``n`` (None: the edge source, e.g.
        external). With ``level_by_source`` the channel's own level setting, the same one
        :meth:`level` draws (#1361: a level bound to one channel's setting answered CH1's for
        every source); otherwise ``level`` resolved for the channel."""
        if n is None:
            return self.level()
        by_source = dict(self.param("level_by_source") or {})
        if by_source and self.owner is not None:
            for word, path in by_source.items():
                if trigger_channel(word) == n:
                    return float(self.owner.get_state(str(path)))
        level = self.channel_param("level", n)
        return 0.0 if level is None else float(level)

    def path_signal(self, sig: Signal) -> Signal:
        """The source's signal as the trigger sees it: through the coupling's filters and the trigger
        path's own ``bandwidth`` (#1263)."""
        table = {str(k).upper(): v for k, v in (self.param("filters") or {}).items()}
        keys = [str(self.param("coupling")).upper(), str(self.param("reject") or "").upper()]
        if self.param("hf_reject") in (True, 1, "1", "ON", "on"):
            keys.append("HF")
        for key in keys:
            for kind, corner in table.get(key, []) if key else []:
                sig = sig.transfer(
                    (highpass1 if str(kind).lower() == "highpass" else lowpass1)(float(corner))
                )
        bandwidth = float(self.param("bandwidth"))
        if math.isfinite(bandwidth):
            sig = sig.transfer(lowpass1(bandwidth))
        return sig

    def noise_sigma(self) -> float:
        """The trigger path's noise, display volts rms: ``noise_rms`` and the ``noise_table`` row of
        the source's division (the search sets it, #1263)."""
        sigma = float(self.param("noise_rms") or 0.0)
        rows = sorted((float(r[0]), float(r[1])) for r in self.param("noise_table") or ())
        if rows:
            scale = rows[0][0] if self._source_scale is None else self._source_scale
            sigma += next((rms for div, rms in reversed(rows) if div <= scale * (1 + 1e-9)), rows[0][1])
        return sigma

    def _hysteresis(self, scale: float) -> float:
        setting = str(self.choice("hysteresis_setting") or "normal")
        normal = float(self.param("hysteresis_divs"))
        if setting == "nreject":  # the widest band, whether or not the old noise_reject flag is on
            divs = float(self.param("reject_hysteresis_divs"))
        elif setting == "hsensitivity":  # the narrowest band (a 4 GHz and above waveform)
            own = self.param("hsensitivity_divs")
            divs = normal / 2.0 if own is None else float(own)
        elif self.param("noise_reject") in (True, 1, "1", "ON", "on"):
            divs = float(self.param("reject_hysteresis_divs"))
        else:
            divs = normal
        return divs * scale

    def _noisy(
        self, v: np.typing.NDArray[np.float64], t0: int, offsets: np.typing.NDArray[np.float64]
    ) -> np.typing.NDArray[np.float64]:
        sigma = self.noise_sigma()
        if sigma <= 0 or self.session is None:
            return v
        # Keyed by the absolute instant (ps) of each sample: the same instant, the same noise; a
        # 256 GSa/s grid's samples (3.9 ps apart) each draw their own (#1263).
        key = stream_key(self.session.seed, self.uid, "trigger-noise")
        return v + sigma * gaussian(key, t0 * 1000 + np.round(offsets * 1e12).astype(np.int64))

    def search(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        pre: int,
        points: int,
        n: int | None,
        scale: float,
        horizon_s: float | None = None,
        earliest_s: float = 0.0,
    ) -> TriggerHit | None:
        """The first qualifying edge at least ``pre`` samples after ``t0`` (enough history before
        the trigger), past the holdoff and not before ``earliest_s``, within ``search_windows``
        records or, with ``horizon_s``, that much virtual time. None: no edge."""
        self._source_scale = scale  # the noise table's row follows the source's division (#1263)
        key = self._search_key(sig, (t0, dt, pre, points, n, scale, horizon_s, earliest_s), n)
        if self._memo is not None and self._memo[0] == key:
            return self._memo[1]
        hit = self._search(sig, t0, dt, pre, points, n, scale, horizon_s, earliest_s)
        self._memo = (key, hit)
        return hit

    def _search(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        pre: int,
        points: int,
        n: int | None,
        scale: float,
        horizon_s: float | None,
        earliest_s: float,
    ) -> TriggerHit | None:
        """:meth:`search` itself."""
        kind = self.source_kind(self.family_source())
        start_s = max(pre * dt, earliest_s)
        if self.last_ns is not None and self.param("holdoff_on") in (True, 1, "1", "ON", "on"):
            start_s = max(start_s, (self.last_ns - t0) * 1e-9 + float(self.param("holdoff")))
        span = max(points * int(self.param("search_windows")), pre + 2 * points) * dt
        horizon = max(span, float(horizon_s or 0.0))
        mode = self.choice("mode") or "edge"
        glitch = mode == "glitch"
        if glitch and kind not in ("channel", "external"):
            return None  # the mains has no pulse to time
        if kind == "line":
            period = 1.0 / float(self.param("line_hz"))
            phase = (t0 * 1e-9) % period
            t = math.ceil((start_s + phase) / period - 1e-12) * period - phase
            return TriggerHit(t, math.ceil(t / dt - 1e-9)) if t <= horizon else None
        if kind == "none":
            return None
        level = self.source_level(n)
        slope = str(self.channel_param("slope", n) if n is not None else self.param("slope")).upper()
        rising = slope in _upper(self.param("rising"))
        falling = slope in _upper(self.param("falling"))
        hyst = self._hysteresis(scale)
        sig = self.path_signal(sig)
        # An edge the signal cannot make (its level beyond the signal's whole swing, noise
        # included) is not searched for: a waiting acquisition looks again at every command.
        swing = float(np.abs(sig.amp).sum()) + 6.0 * self.noise_sigma()
        top, bottom = sig.dc + swing, sig.dc - swing
        if mode in _ADVANCED:
            return self._advanced(sig, t0, dt, start_s, span, horizon, mode, n, scale, hyst, top, bottom)
        can_rise = bottom < level - hyst and top >= level
        can_fall = top > level + hyst and bottom <= level
        if glitch:  # a pulse needs both of its edges
            if not (can_rise and can_fall):
                return None
            return self._glitch(sig, t0, dt, start_s, span, horizon, level, hyst)
        if not ((rising and can_rise) or (falling and can_fall)):
            return None
        # The first stretch on the record grid; further on, a grid as coarse as the signal allows
        # (sixteen samples a period of its highest line) and no finer than the record's.
        hit = self._scan(sig, t0, dt, start_s, span, level, hyst, rising, falling, dt)
        if hit is not None or horizon <= span:
            return hit
        step = self._far_step(sig, dt, span, horizon)
        return self._scan(
            sig, t0, dt, max(start_s, span), horizon, level, hyst, rising, falling, step, lead=False
        )

    def _far_step(self, sig: Signal, dt: float, span: float, horizon: float) -> float:
        """The grid beyond the first stretch of a search: as coarse as the signal allows (sixteen
        samples a period of its highest line), no finer than the record's."""
        lines = sig.freq[np.abs(sig.amp) > 1e-6 * max(1e-300, float(np.abs(sig.amp).max(initial=0.0)))]
        f_top = float(lines.max()) if lines.size else 0.0
        step = dt if f_top <= 0 else max(dt, 1.0 / (16.0 * f_top))
        # The work is samples times lines: a signal of many lines is searched on fewer samples.
        budget = float(self.param("max_search_points")) / max(1.0, sig.freq.size / 16.0)
        return max(step, (horizon - span) / budget)

    # ---- the advanced families (#1184): runt, transition, window, timeout, nedge ----------------
    #
    # The same steady-state signal the edge and glitch searches look at, the same hysteresis band
    # and instants; what changes is what qualifies. The pulse families (runt, transition) walk the
    # crossings of both thresholds, the state families (window, timeout) the runs of the signal's
    # state, nedge counts the edge trigger's own crossings.

    def _stretches(
        self, sig: Signal, dt: float, start_s: float, span: float, horizon: float, *bounds: float
    ) -> list[tuple[float, float]]:
        """The grids a pulse- or state-based search walks: the record grid for as many samples as
        ``max_search_points`` allows (fewer for a signal of many lines), then a grid an eighth of
        the narrowest bound the qualification compares, so a coarse grid still bounds the durations
        it judges (#1000)."""
        budget = float(self.param("max_search_points")) / max(1.0, sig.freq.size / 16.0)
        fine = min(span, horizon, start_s + budget * dt)
        stretches = [(fine, dt)]
        if horizon > fine:
            usable = [b for b in bounds if 0 < b < math.inf]
            step = max(dt, min(usable) / 8) if usable else self._far_step(sig, dt, fine, horizon)
            stretches.append((min(horizon, fine + step * budget), step))
        return stretches

    def _level_stream(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stretches: list[tuple[float, float]],
        level: float,
        hyst: float,
    ) -> Iterator[_Crossing]:
        """The crossings of one threshold, in time order, for the pulse families: the side of the
        threshold flips only beyond the hysteresis band, and the flip's own crossing of the level —
        between the last sample on the old side and this one — is what counts. A threshold of a
        pair is reached from its partner, not from beyond its own far band, so the edge search's
        arming rule would miss just the edges the pulse families look for (an edge whose top is
        barely past the threshold never sees the far side of it). The side the signal is on at the
        search's start is carried in: the crossing that put it there lies before the search."""
        side = 0  # -1 below the band, +1 above it, 0 not yet decided
        anchor: tuple[float, float] | None = None  # the last sample decisively on this side
        noisy = self.noise_sigma() > 0
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self._noisy(sig.sample_rel(t0, offsets), t0, offsets)
                # Only the samples beyond the band (decisive) move the side, and a flip is a
                # decisive sample on the other side from the decisive one before it: found with
                # numpy over the chunk, then walked in order (a per-sample loop cost seconds on a
                # noisy UXR record of a million samples).
                decisive = np.where(v > level + hyst, 1, np.where(v < level - hyst, -1, 0))
                nz = np.flatnonzero(decisive)
                if nz.size:
                    sides = decisive[nz]
                    before = np.concatenate(([side], sides[:-1]))
                    # a flip needs a side behind it (which is where its anchor sample came from)
                    flips = np.flatnonzero((before != 0) & (sides != before))
                    if flips.size:
                        ks = nz[flips]
                        # each flip's anchor: the decisive sample before it, in this chunk or the
                        # one carried in from the chunk before
                        prior = nz[np.maximum(flips - 1, 0)]
                        first = flips == 0
                        if first.any():
                            assert anchor is not None
                        t_p = np.where(first, anchor[0] if anchor else 0.0, offsets[prior])
                        v_p = np.where(first, anchor[1] if anchor else 0.0, v[prior])
                        t_ks, v_ks = offsets[ks], v[ks]
                        if noisy:
                            # the line between the two samples carries the estimate (_between)
                            same = v_ks == v_p
                            frac = np.where(same, 1.0, (level - v_p) / np.where(same, 1.0, v_ks - v_p))
                            estimates = t_p + np.clip(frac, 0.0, 1.0) * (t_ks - t_p)
                        for i, j in enumerate(flips):
                            # the level was crossed between the last sample on the old side and this
                            # one; without noise the crossing is bisected on the signal
                            rising = bool(sides[j] > 0)
                            a_t, t_k = float(t_p[i]), float(t_ks[i])
                            if noisy:
                                estimate = float(estimates[i])
                            else:
                                estimate = self._bisect(sig, t0, a_t, t_k, level, rising, dt)
                            yield _Crossing("rise" if rising else "fall", a_t, t_k, estimate, True)
                    side = int(sides[-1])
                    anchor = (float(offsets[nz[-1]]), float(v[nz[-1]]))
                prev = (float(offsets[-1]), float(v[-1]))
                a = prev[0] + step
        yield _Crossing(
            None,
            prev[0] if prev else start_s,
            prev[0] if prev else start_s,
            prev[0] if prev else start_s,
            True,
        )

    def _merged(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stretches: list[tuple[float, float]],
        low: float,
        high: float,
        hyst: float,
    ) -> Iterator[tuple[str, _Crossing]]:
        """The crossings of both thresholds in time order, ``(which, crossing)`` with ``which``
        ``low`` or ``high`` — each level walked by the pulse families' own rule
        (:meth:`_level_stream`). ``""`` carries the progress markers: how far the search has come
        where neither level crossed."""
        lows = self._level_stream(sig, t0, dt, start_s, stretches, low, hyst)
        highs = self._level_stream(sig, t0, dt, start_s, stretches, high, hyst)
        # Each stream's head, taken when needed; an ended stream's head stays None. A plain
        # two-way merge: the per-crossing work of a general one showed on noisy records (#1361).
        lo, hi = next(lows, None), next(highs, None)
        while lo is not None or hi is not None:
            lo_real = lo is not None and lo.direction is not None
            hi_real = hi is not None and hi.direction is not None
            if not lo_real and not hi_real:  # only progress markers ahead: hand them on
                if lo is not None:
                    yield ("", lo)
                    lo = next(lows, None)
                if hi is not None:
                    yield ("", hi)
                    hi = next(highs, None)
                continue
            # the earlier crossing first; at the same instant the high threshold's
            if lo is not None and lo_real and (hi is None or not hi_real or lo.at < hi.at):
                yield ("low", lo)
                lo = next(lows, None)
            elif hi is not None:
                yield ("high", hi)
                hi = next(highs, None)

    def _advanced(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        span: float,
        horizon: float,
        mode: str,
        n: int | None,
        scale: float,
        hyst: float,
        top: float,
        bottom: float,
    ) -> TriggerHit | None:
        """The advanced families (#1184), each on the steady-state signal with the edge search's
        arming and instants: runt and transition on the crossings of the two thresholds, window
        and timeout on the runs of the signal's state, nedge on the counted crossings."""
        source = self.family_source()
        level = self.source_level(n)
        noiseless = self.noise_sigma() <= 0
        period = _period(sig) if noiseless else None
        high = self.threshold("high", source)
        low = self.threshold("low", source)

        if mode == "nedge":
            slope = self.param("nedge_slope")
            if slope in (None, ""):
                slope = str(self.channel_param("slope", n) if n is not None else self.param("slope"))
            slope = str(slope).upper()
            rising = slope in _upper(self.param("rising"))
            falling = slope in _upper(self.param("falling"))
            directions = [d for d, on in (("rise", rising), ("fall", falling)) if on]
            if not directions or not (
                (rising and bottom < level - hyst and top >= level)
                or (falling and top > level + hyst and bottom <= level)
            ):
                return None
            count = max(1, int(self.param("nedge_count")))
            if period is not None:
                # The edges repeat each period: what one period shows is what every period shows,
                # so the nth lands in its own period — the horizon reaches that far (within the
                # sample budget; a search that cannot look that far waits and looks again at the
                # next command, as the glitch search does).
                probe_stop = min(span, 2.0 * period + dt)
                probe = [
                    c
                    for c in self._edges(sig, t0, dt, start_s, [(probe_stop, dt)], level, hyst)
                    if c.direction in directions
                ]
                per_period = max(len(probe) / max(probe_stop / period, 1.0), 1.0)
                horizon = min(
                    horizon,
                    max(probe_stop, start_s + period * math.ceil(count / per_period) + 2.0 * dt),
                )
            seen = 0
            for c in self._edges(
                sig, t0, dt, start_s, self._stretches(sig, dt, start_s, span, horizon), level, hyst
            ):
                if c.direction not in directions:
                    continue
                seen += 1
                if seen >= count:
                    if not c.final:  # found on the coarse grid: bisected on the signal
                        c.estimate = self._bisect(sig, t0, c.before, c.at, level, c.direction == "rise", dt)
                        c.final = True
                    return TriggerHit(c.estimate, math.ceil(c.estimate / dt - 1e-9))
            return None

        if mode == "timeout":
            limit = float(self.param("timeout_time"))
            cond = str(self.choice("timeout_condition") or "unchanged")
            if limit <= 0:
                return None
            if period is not None:  # one period shows every state run there is
                horizon = min(horizon, start_s + period + limit + 2.0 * dt)
            for state, entry, upto, _final in self._runs(
                sig, t0, dt, start_s, self._stretches(sig, dt, start_s, span, horizon, limit), level, hyst
            ):
                if cond != "unchanged" and state != cond:
                    continue
                begin = max(entry, start_s)  # armed here: the run counts from the arming
                if upto - begin >= limit:
                    return TriggerHit(begin + limit, math.ceil((begin + limit) / dt - 1e-9))
            return None

        if mode == "window":
            if high is None or low is None:  # no thresholds set: nothing to enter or leave
                return None
            high, low = float(high), float(low)
            if low > high:
                low, high = high, low
            condition = str(self.choice("window_condition") or "enter")
            wanted = "inside" if condition in ("enter", "inside") else "outside"
            qualifier = str(self.choice("window_qualifier") or "")
            tpoint = str(self.choice("window_tpoint") or "boundary")
            limit = float(self.param("window_time"))
            if not (top >= low and bottom <= high) or not (bottom < low - hyst or top > high + hyst):
                return None  # the signal never reaches the band, or never leaves it
            if period is not None:
                horizon = min(horizon, start_s + 2.0 * period + limit + 2.0 * dt)

            def instant(c: tuple) -> float:
                bound, d, before, at, estimate, final = c
                if final:
                    return estimate
                return self._bisect(sig, t0, before, at, bound, d == "rise", dt)

            for state, entry, exit_c, upto, _final in self._runs_window(
                sig, t0, dt, start_s, self._stretches(sig, dt, start_s, span, horizon, limit), low, high, hyst
            ):
                if state != wanted:
                    continue
                carried = entry is None  # the signal already sat in this state at the arming
                begin = (
                    start_s if carried else max(instant(entry), start_s)
                )  # armed here: the transition counts from then
                left = instant(exit_c) if exit_c is not None else None
                if condition in ("enter", "exit") and tpoint == "boundary":
                    if carried:
                        continue  # being in the state already is not a transition of the search
                    return TriggerHit(begin, math.ceil(begin / dt - 1e-9))
                if qualifier == "lthan":
                    # left before the time: the exit instant is the trigger
                    if left is not None and left - begin < limit:
                        return TriggerHit(left, math.ceil(left / dt - 1e-9))
                    continue
                held = (left if left is not None else upto) - begin
                if held >= limit:  # boundary timeout, or a greater inside/outside: still there
                    return TriggerHit(begin + limit, math.ceil((begin + limit) / dt - 1e-9))
            return None

        # ---- runt and transition: the crossings of both thresholds in time order ----------------
        if high is None or low is None:
            return None
        high, low = float(high), float(low)
        if low > high:
            low, high = high, low
        polarity = str(self.choice("runt_polarity") or "positive")
        ttype = str(self.choice("transition_type") or "risetime")
        if mode == "runt":
            opens = ("low", "rise") if polarity == "positive" else ("high", "fall")
            closes = ("low", "fall") if polarity == "positive" else ("high", "rise")
            rejects = {("high", "rise")} if polarity == "positive" else {("low", "fall")}
            entry = (
                (bottom < low - hyst and top >= low)
                if polarity == "positive"
                else (top > high + hyst and bottom <= high)
            )
            if not entry:
                return None
            qualified = V.flag(self.param("runt_qualified"))
            bounds = (float(self.param("runt_time")),)
        else:
            opens = ("low", "rise") if ttype == "risetime" else ("high", "fall")
            closes = ("high", "rise") if ttype == "risetime" else ("low", "fall")
            rejects = (
                {("low", "fall"), ("high", "fall")}
                if ttype == "risetime"
                else {("high", "rise"), ("low", "rise")}
            )
            entry = (
                (bottom < low - hyst and top > high)
                if ttype == "risetime"
                else (top > high + hyst and bottom < low)
            )
            if not entry:
                return None
            qualified = False
            tmode = str(self.choice("transition_mode") or "greater")
            t_limit = float(self.param("transition_time"))
            t_lo = float(self.param("transition_range_low"))
            t_hi = float(self.param("transition_range_high"))
            bounds = (t_limit,) if tmode in ("gthan", "lthan") else (t_lo, t_hi)
        if period is not None:  # a signal that repeats shows every pulse within two periods
            horizon = min(horizon, start_s + 2.0 * period + 2.0 * dt)
        levels = {"low": low, "high": high}

        def instant(c: _Crossing, which: str) -> float:
            if not c.final:  # found on the coarse grid: bisected on the signal
                c.estimate = self._bisect(sig, t0, c.before, c.at, levels[which], c.direction == "rise", dt)
                c.final = True
            return c.estimate

        def hit(t: float) -> TriggerHit:
            return TriggerHit(t, math.ceil(t / dt - 1e-9))

        began: tuple[str, _Crossing] | None = None  # the opening crossing of the pulse under way
        for which, c in self._merged(
            sig, t0, dt, start_s, self._stretches(sig, dt, start_s, span, horizon, *bounds), low, high, hyst
        ):
            if which == "":  # progress: a runt still open has lasted its time
                if mode == "runt" and qualified and began is not None:
                    begun = instant(began[1], began[0])
                    if c.at - begun > float(self.param("runt_time")):
                        return hit(begun + float(self.param("runt_time")))
                continue
            key = (which, str(c.direction))
            if began is not None and key in rejects:
                began = None
                continue
            if key == closes and began is not None:
                begun = instant(began[1], began[0])
                done = instant(c, which)
                if mode == "runt":
                    if not qualified or done - begun > float(self.param("runt_time")):
                        return hit(done)
                else:
                    edge_time = done - begun
                    inside = t_lo < edge_time < t_hi
                    if (
                        (tmode == "gthan" and edge_time > t_limit)
                        or (tmode == "lthan" and edge_time < t_limit)
                        or (tmode == "range" and inside)
                        or (tmode == "orange" and not inside)
                    ):
                        return hit(done)
                began = None
                continue
            if key == opens:
                began = (which, c)
            elif key == closes:
                began = None  # a close without an open: whatever ran before is over
        return None

    def _runs(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stretches: list[tuple[float, float]],
        level: float,
        hyst: float,
    ) -> Iterator[tuple[str, float, float, bool]]:
        """The runs of the signal's state against ``level`` (the timeout family): high above
        ``level + hyst``, low below ``level - hyst``, in between keeps the previous state (the
        hysteresis band; at the very start, with no state behind it, the nearer side decides).
        Yields ``(state, entry, upto, final)`` when a run closes — ``entry`` where it began,
        ``upto`` where it ended — and once more for the run still open where the search ends."""
        state = ""
        entry = 0.0
        anchor: tuple[float, float] | None = None  # the last sample decisively in this state
        noisy = self.noise_sigma() > 0
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self._noisy(sig.sample_rel(t0, offsets), t0, offsets)
                for k in range(count):
                    t_k, v_k = float(offsets[k]), float(v[k])
                    if v_k > level + hyst:
                        decisive = "high"
                    elif v_k < level - hyst:
                        decisive = "low"
                    else:
                        decisive = ""  # the hysteresis band keeps the state
                    new = decisive or state or ("high" if v_k >= level else "low")
                    if state and new != state:
                        yield (state, entry, t_k, True)
                        # the state truly began where the band edge was crossed, between the last
                        # sample on the other side and this one; bisected without noise, the line
                        # between the two samples carrying the estimate with it
                        bound = level + hyst if new == "high" else level - hyst
                        if anchor is not None and prev is not None:
                            if noisy:
                                entry = _between(anchor[0], anchor[1], t_k, v_k, bound)
                            else:
                                entry = self._bisect(sig, t0, anchor[0], t_k, bound, new == "high", dt)
                        else:
                            entry = t_k
                    elif not state:
                        entry = t_k
                    state = new
                    if decisive:
                        anchor = (t_k, v_k)  # a sample of the state the next flip is measured from
                    prev = (t_k, v_k)
                prev = (float(offsets[-1]), float(v[-1]))
                a = prev[0] + step
        if state:
            yield (state, entry, prev[0] if prev else entry, True)

    def _runs_window(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stretches: list[tuple[float, float]],
        low: float,
        high: float,
        hyst: float,
    ) -> Iterator[tuple[str, tuple | None, tuple | None, float, bool]]:
        """The runs of the signal inside and outside the band ``[low, high]`` (the window family):
        inside between the bounds, outside beyond either bound widened by the hysteresis, the ring
        between keeping the previous state. Yields ``(state, entry, exit, upto, final)`` when a run
        closes — ``exit`` the crossing out of it — and once for the run still open where the search
        ends (``exit`` None, ``upto`` how far the search has come). An entry or exit is
        ``(bound, direction, before, at, estimate, final)``: the crossing of the bound the signal
        actually crossed, its instant bisected later on the coarse grids."""
        state = ""  # "" until a sample decides one way or the other
        entry: tuple | None = None
        anchor: tuple[float, float] | None = None  # the last sample decisively in this state
        noisy = self.noise_sigma() > 0
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self._noisy(sig.sample_rel(t0, offsets), t0, offsets)
                for k in range(count):
                    t_k, v_k = float(offsets[k]), float(v[k])
                    if low <= v_k <= high:
                        decisive = "inside"
                    elif v_k > high + hyst or v_k < low - hyst:
                        decisive = "outside"
                    else:
                        decisive = ""  # the ring between the band and its hysteresis: stays
                    if decisive and decisive != state:
                        # the bound was crossed between the last sample decisively on the other
                        # side and this one (the ring in between never says so); the estimate is
                        # the line between the two samples, bisected by the consumer on the exact
                        # signal when there is no noise (final False). The run the signal already
                        # sits in at the search's start has no crossing behind it (entry None):
                        # being in the state is not the same as entering it.
                        cross = (
                            self._window_crossing(anchor, (t_k, v_k), low, high, noisy)
                            if anchor is not None
                            else None
                        )
                        if state:
                            yield (state, entry, cross, prev[0] if prev else t_k, noisy)
                        entry = cross
                        state = decisive
                    if decisive:
                        anchor = (t_k, v_k)  # a sample of the state the next crossing is found from
                    prev = (t_k, v_k)
                prev = (float(offsets[-1]), float(v[-1]))
                a = prev[0] + step
        if state and entry is not None:
            yield (state, entry, None, prev[0] if prev else start_s, True)

    @staticmethod
    def _window_crossing(
        before: tuple[float, float] | None, at: tuple[float, float], low: float, high: float, final: bool
    ) -> tuple:
        """The crossing into or out of the window band between the samples ``before`` and ``at``:
        into the band from above is the high bound falling, from below the low bound rising; out of
        it upward the high bound rising, downward the low bound falling."""
        (t_k, v_k) = at
        if before is not None:
            t_p, v_p = before
            if low <= v_k <= high:  # into the band
                bound, d = (high, "fall") if v_p > high else (low, "rise")
            elif v_k > high:  # out of it upward
                bound, d = high, "rise"
            else:  # out of it downward
                bound, d = low, "fall"
            return (bound, d, t_p, t_k, _between(t_p, v_p, t_k, v_k, bound), final)
        return (low, "rise", t_k, t_k, t_k, True)

    def _glitch(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        span: float,
        horizon: float,
        level: float,
        hyst: float,
    ) -> TriggerHit | None:
        """The first pulse of ``polarity`` whose width meets ``qualifier``, among the crossings from
        ``start_s`` on: the instant its closing edge crosses the level (less, range) or the instant
        it has lasted ``width_low`` (greater)."""
        opens, closes = ("rise", "fall") if self.choice("polarity") == "positive" else ("fall", "rise")
        qualifier = self.choice("qualifier")
        low, high = float(self.param("width_low")), float(self.param("width_high"))
        # A signal that repeats shows every pulse it has within two periods: none there, none ever
        # (a waiting acquisition searches again at every command).
        period = _period(sig) if self.noise_sigma() <= 0 else None
        if period is not None:
            horizon = min(horizon, max(start_s, 0.0) + 2.0 * period + 2.0 * dt)
        # The work is samples times lines, as for an edge's far search: the record grid for as
        # many samples as max_search_points allows (fewer for a signal of many lines), then a grid
        # an eighth of the narrowest width bound the qualifier uses for as many again. A grid
        # coarser than the widths judged would see pulses with a narrow gap between them as one
        # wide pulse (#1000); what lies beyond is looked at again at the next command.
        budget = float(self.param("max_search_points")) / max(1.0, sig.freq.size / 16.0)
        fine = min(span, horizon, start_s + budget * dt)
        stretches = [(fine, dt)]
        if horizon > fine:
            used = {"less": (high,), "greater": (low,)}.get(qualifier, (low, high))  # range, outside
            bounds = [w for w in used if 0 < w < math.inf]
            step = max(dt, min(bounds) / 8) if bounds else self._far_step(sig, dt, fine, horizon)
            stretches.append((min(horizon, fine + step * budget), step))

        def instant(c: _Crossing) -> float:
            if not c.final:  # found on the coarse grid: to a thousandth of a record sample
                c.estimate = self._bisect(sig, t0, c.before, c.at, level, c.direction == "rise", dt)
                c.final = True
            return c.estimate

        def hit(t: float) -> TriggerHit:
            return TriggerHit(t, math.ceil(t / dt - 1e-9))

        at_end = qualifier == "outside" or (qualifier == "greater" and self.choice("greater_at") == "end")

        def may_qualify(c: _Crossing, o: _Crossing) -> bool:
            """Whether the pulse from ``o`` to ``c`` can meet the qualifier, judged on the grid
            (the shortest and longest widths the two crossings allow)."""
            shortest, longest = c.before - o.at, c.at - o.before
            if qualifier == "less":
                return shortest < high
            if qualifier == "range":
                return shortest < high and longest > low
            if qualifier == "greater":
                return longest > low
            return shortest < low or longest > high  # outside

        def qualifies(width: float) -> bool:
            if qualifier == "less":
                return width < high
            if qualifier == "range":
                return low < width < high
            if qualifier == "greater":
                return width > low
            return width < low or width > high  # outside

        opened: _Crossing | None = None  # the opening edge of the pulse under way
        for c in self._edges(sig, t0, dt, start_s, stretches, level, hyst):
            if opened is not None and qualifier == "greater" and not at_end and c.at - opened.before > low:
                # Nothing closed the pulse before it had lasted width_low: ``c`` is the next
                # crossing, or how far the search has looked.
                begun = instant(opened)
                if (c.at if c.direction is None else instant(c)) - begun > low:
                    return hit(begun + low)
            if c.direction == opens:
                opened = c
            elif c.direction == closes:
                # The grid already bounds the width; the instants are refined only for a pulse
                # that may qualify.
                if (
                    opened is not None
                    and (qualifier != "greater" or at_end)
                    and may_qualify(c, opened)
                    and qualifies(instant(c) - instant(opened))
                ):
                    return hit(instant(c))
                opened = None
        return None

    def _edges(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stretches: list[tuple[float, float]],
        level: float,
        hyst: float,
    ) -> Iterator[_Crossing]:
        """The crossings of the level from ``start_s`` on, in time order, each found by the edge
        trigger's own rule (:meth:`_scan`: armed beyond the hysteresis band on the far side, taken
        at the first sample on the level's other side). ``stretches`` are (stop, step) grids
        searched one after the other, the first from one step before ``start_s`` (a sample that
        may arm but not cross). After every chunk of samples a crossing of no direction says how
        far the search has come."""
        armed = {"rise": False, "fall": False}
        prev: tuple[float, float] | None = None
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            # A crossing on a coarser grid than the record's is bisected on the signal (#1331).
            final = not step > dt * (1 + 1e-9)
            while a <= stop:
                count = min(chunk, max(2, math.ceil((stop - a) / step) + 1))
                chunk = min(chunk * 2, 1 << 18)
                offsets = a + step * np.arange(count, dtype=np.float64)
                v = self._noisy(sig.sample_rel(t0, offsets), t0, offsets)
                live = offsets >= start_s - 1e-15
                far = {"rise": np.nonzero(v < level - hyst)[0], "fall": np.nonzero(v > level + hyst)[0]}
                cross = {
                    "rise": np.nonzero((v >= level) & live)[0],
                    "fall": np.nonzero((v <= level) & live)[0],
                }
                at = {"rise": 0, "fall": 0}  # where each direction looks on from
                while True:
                    found: tuple[int, str] | None = None
                    for d in ("rise", "fall"):
                        if not armed[d]:
                            i = int(np.searchsorted(far[d], at[d]))
                            if i == far[d].size:
                                continue
                            armed[d], at[d] = True, int(far[d][i]) + 1
                        j = int(np.searchsorted(cross[d], at[d]))
                        if j < cross[d].size and (found is None or int(cross[d][j]) < found[0]):
                            found = (int(cross[d][j]), d)
                    if found is None:
                        break
                    k, d = found
                    armed[d], at[d] = False, k + 1
                    t_k, v_k = float(offsets[k]), float(v[k])
                    t_p, v_p = (float(offsets[k - 1]), float(v[k - 1])) if k > 0 else (prev or (t_k, v_k))
                    yield _Crossing(d, t_p, t_k, _between(t_p, v_p, t_k, v_k, level), final)
                prev = (float(offsets[-1]), float(v[-1]))
                yield _Crossing(None, prev[0], prev[0], prev[0], True)
                a = prev[0] + step

    def _instant(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        step: float,
        offsets: np.typing.NDArray[np.float64],
        v: np.typing.NDArray[np.float64],
        k: int,
        prev: tuple[float, float] | None,
        level: float,
        rising: bool,
    ) -> float:
        """The instant of the crossing taken at sample ``k``: between it and the sample before
        (``prev``: the last one of the chunk before), linear on the record's grid, bisected on a
        coarser one on the signal without the trigger's noise: a straight line between samples
        that may lie periods apart says nothing of where the edge is (#1331)."""
        t_k, v_k = float(offsets[k]), float(v[k])
        t_p, v_p = (float(offsets[k - 1]), float(v[k - 1])) if k > 0 else (prev or (t_k, v_k))
        if step > dt * (1 + 1e-9):
            return self._bisect(sig, t0, t_p, t_k, level, rising, dt)
        return _between(t_p, v_p, t_k, v_k, level)

    @staticmethod
    def _bisect(sig: Signal, t0: int, lo: float, hi: float, level: float, rising: bool, dt: float) -> float:
        """The crossing in (lo, hi] found on a coarse grid, to a thousandth of a record sample."""
        while hi - lo > dt * 1e-3:
            mid = (lo + hi) / 2
            v = float(sig.sample_rel(t0, np.array([mid]))[0])
            if (v >= level) == rising:
                hi = mid
            else:
                lo = mid
        return hi

    def _scan(
        self,
        sig: Signal,
        t0: int,
        dt: float,
        start_s: float,
        stop_s: float,
        level: float,
        hyst: float,
        rising: bool,
        falling: bool,
        step: float,
        lead: bool = True,
    ) -> TriggerHit | None:
        """Search [start_s, stop_s] on a grid of ``step`` (with ``lead``, its first sample one step
        before start_s, which may arm but not trigger). An edge is armed once the signal has been beyond
        the band on the far side of the level; it triggers at the first sample on the level's other
        side, the instant interpolated between that sample and the one before."""
        # Chunks grow from a few thousand samples: the first edge is usually early, and sampling
        # the whole window of a many-line signal up front costs more than the rest of the record.
        chunk = 1 << 12
        directions = [d for d, on in (("rise", rising), ("fall", falling)) if on]
        armed = dict.fromkeys(directions, False)
        prev: tuple[float, float] | None = None
        a = start_s - step if lead else start_s
        while a <= stop_s:
            count = min(chunk, max(2, math.ceil((stop_s - a) / step) + 1))
            chunk = min(chunk * 2, 1 << 18)
            offsets = a + step * np.arange(count, dtype=np.float64)
            v = self._noisy(sig.sample_rel(t0, offsets), t0, offsets)
            best: float | None = None
            for d in directions:
                far = v < level - hyst if d == "rise" else v > level + hyst
                cross = (v >= level if d == "rise" else v <= level) & (offsets >= start_s - 1e-15)
                if armed[d]:
                    after = 0
                else:
                    first = np.nonzero(far)[0]
                    if not first.size:
                        continue
                    after = int(first[0]) + 1
                    armed[d] = True
                hits = np.nonzero(cross[after:])[0]
                if not hits.size:
                    continue
                k = after + int(hits[0])
                t = self._instant(sig, t0, dt, step, offsets, v, k, prev, level, d == "rise")
                best = t if best is None else min(best, t)
            if best is not None:
                return TriggerHit(best, math.ceil(best / dt - 1e-9))
            prev = (float(offsets[-1]), float(v[-1]))
            a = float(offsets[-1]) + step
        return None


@component("scope_acquisition")
class ScopeAcquisition(Component):
    """Run control, sampling and the records of a scope, built from the scope's modules.

    One component for every oscilloscope model, set by parameters (instrument-authoring.md 3.2, 8.4).
    It names the modules it reads: ``horizontal`` (a ``scope_timebase``), ``trigger`` (a
    ``scope_trigger``), ``adc`` (a ``scope_adc``), and per channel ``vertical`` (a
    ``scope_vertical``: the scale and offset) and ``probe`` (a ``scope_probe``, whose output is wired
    to ``in<n>``). Written the earlier way, with the modules' parameters on the acquisition itself and
    no module named, the definition is expanded into the modules when it loads (a macro):

    * **run control** ``mode`` and the values that mean ``auto``, ``normal``, ``single``, ``stop``
      and ``force`` (one frame whatever the trigger, then stop); ``after_single`` is the mode a
      completed single acquisition leaves (empty: the mode stays and the scope stops).
      ``reacquire`` says when a read gets a new acquisition from a running scope: ``explicit``
      (only a digitize command), ``every_read`` or ``on_read_start`` (a read from the first point;
      later pieces read the same record). A pending single acquisition is retried on every read.
      ``missing: acquire`` reads a channel that has no record yet, ``error`` refuses it;
    * **noise** the input noise comes from the channel's ``scope_vertical`` ``noise_table``, per
      division at the input connector, in display volts times the probe ratio; it is keyed by
      ``noise_key``: ``time`` (the absolute sample instants, D46; ``noise_stream`` names a setting
      that selects another stream) or ``acquisition`` (the acquisition count, so pauses of any
      real length between commands give the same numbers); ``trigger_jitter_s``;
      * **processing** (F4, D46) what the quantized codes go through: ``normal`` keeps them;
        ``average`` (``averages``) is the mean of that many acquisitions, each of them noisy and
        quantized, so the noise falls by √N and the resolution gains (it takes N acquisitions'
        time); ``peak`` examines each record interval at the ADC's native rate (at most
        ``peak_max_oversample`` samples per record sample) and keeps, of the interval's smallest
        and largest code, the one that departs farther from its ends — a pulse the record rate
        would step over shows up; ``eres`` smooths ``4**eres_bits`` codes (a boxcar), so the
        noise falls by ``2**eres_bits`` — that many more effective bits, at the boxcar's
        bandwidth.

    * **synchronization** (instrument-authoring.md 8.4, G2): ``completion: immediate`` finishes a
      single acquisition, a digitize or a forced frame within the command that starts it;
      ``pending`` makes it an operation that finishes in virtual time, when the record after the
      trigger is full: until then the trigger status is waiting (then triggered), a read gets the
      previous record (``while_waiting: previous``) or an error (``error``), ``*OPC?`` and ``*WAI``
      wait for it unless ``opc_waits`` is false, and a digitize waits for it. Without a trigger it
      waits for one, looking again whenever a command arrives; ``no_trigger: wait`` leaves ``*OPC?``
      unanswered meanwhile (the client times out), ``return`` answers it at once.

    * **segmented memory** (#957): with ``segments`` above one (while ``segmented``), a single
      acquisition or a digitize takes that many records, each on its own trigger; the trigger is
      armed for the next ``rearm_s`` after a record is full, and the acquisition is done with its
      last segment (in an auto mode a segment whose trigger does not come within the auto timeout
      is taken untriggered; otherwise the acquisition waits). ``segment_index`` (from 1) selects
      the segment that reads, transfers and measurements use; op ``figure`` gives the number of
      segments acquired and the selected segment's time tag. ``memory_points`` limits segments
      times record length (-221). The first segment's noise is an unsegmented acquisition's; each
      later one has its own.

    Status events (for ``status_register`` bits of these names): ``TRG`` when a stored acquisition met
    its trigger, ``DONE`` for every stored acquisition, ``AUTO`` for one an auto mode took without
    its trigger, ``ARM`` when a run or a single acquisition arms the trigger.

    Per-channel ``ch<n>.display`` says which channels are acquired. The trigger status
    (``status_text``), an acquisition type with its count (``acquire_type``, ``acquire_counts``)
    and the read-only figures (op ``figure``: points, sample rate, acquisitions) are declared.
    Measurements are a ``scope_measurements`` module, the transfer and its preamble a
    ``waveform_transfer``.
    """

    label = "采集"
    label_en = "Acquisition"
    about = "示波器的运行控制、采样和记录。"
    about_en = "A scope's run control, sampling and records."

    ports: dict[str, PortSpec] = {}
    port_pattern = r"in\d+"  # inputs in1..inN are created by the instrument wiring
    pattern_port = _SAMPLES_IN  # each channel's samples at the record's rate (the decimator's output)
    refs = {
        "horizontal": ("scope_timebase", "instrument"),
        "trigger": ("scope_trigger", "instrument"),
        "adc": ("scope_adc", "instrument"),
        "decimator": ("scope_decimator", "instrument"),
        "vertical": ("scope_vertical", "channel"),
        "probe": ("scope_probe", "channel"),
    }
    optional_refs = frozenset({"decimator"})  # filled by the acquisition macro; asked for at an acquisition
    defaults = {
        "horizontal": "",  # the scope_timebase component
        "trigger": "",  # the scope_trigger component
        "adc": "",  # the scope_adc component
        "decimator": "",  # the scope_decimator component: the acquisition mode and what it costs in time
        "vertical": "",  # the per-channel scope_vertical component (its name without ch<n>.)
        "probe": "",  # the per-channel scope_probe component (its name without ch<n>.)
        "overhead_s": 1e-3,  # virtual time an acquisition takes besides its record, s
        "digitize_stops": False,  # :DIGitize leaves the scope stopped (the InfiniiVision's rule, #248)
        "mode": "$trigger.mode",  # the run mode setting
        "auto": ["AUTO"],  # modes that keep untriggered acquisitions
        "normal": ["NORMal"],  # modes that wait for a trigger
        "single": [],  # modes that acquire once on the next trigger
        "stop": [],  # modes that stop (none: a stop command stops)
        "force": [],  # modes that take one frame whatever the trigger, then stop
        "after_single": "",  # the mode a completed single acquisition sets (empty: unchanged)
        "reacquire": "explicit",  # explicit | every_read | on_read_start
        "missing": "error",  # error | acquire
        "untriggered": "search",  # the time an untriggered acquisition takes: search | record
        "noise_key": "acquisition",  # acquisition | time
        "noise_stream": "",  # a setting that selects another noise stream (time key)
        "trigger_jitter_s": 0.0,  # rms offset of each record around the trigger, s
        # op status: the trigger status as the model words it (stopped, waiting for a trigger,
        # triggered, running untriggered in an auto mode)
        "status_text": {"stopped": "STOP", "waiting": "READY", "triggered": "TRIG'D", "auto": "AUTO"},
        # op acquire_type: the acquisition type setting and, per type that takes a count, the
        # setting the count writes, its allowed values and how a query writes it, e.g.
        # {AVERage: {setting: acquire.average, values: [4, 16], format: "{kind},{value:d}"}}
        "acquire_type": "",
        "acquire_counts": {},  # type -> {setting, values, format} for the types that take a count
        "completion": "immediate",  # immediate | pending: when a single acquisition or digitize is done
        "opc_waits": True,  # *OPC?, *OPC and *WAI wait for a pending acquisition
        "while_waiting": "previous",  # previous | error: a read while an acquisition is pending
        "no_trigger": "wait",  # wait | return: *OPC? while armed for a trigger that has not come
        "segments": 1,  # records one acquisition takes, each on its own trigger (segmented memory); 1: one
        "segmented": True,  # whether segments applies (e.g. an acquisition mode that is SEGMented)
        "rearm_s": 0.0,  # s from a segment's full record until the trigger is armed for the next
        "segment_index": 1,  # the segment that reads and measurements use, from 1
        "memory_points": None,  # points the segments of one acquisition may take together (None: no limit)
    }
    units = {"rearm_s": "s", "overhead_s": "s", "trigger_jitter_s": "s"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.records: dict[int, Record] = {}
        self.segment_records: list[dict[int, Record]] = []  # the latest acquisition's segments, if segmented
        # the ADC's clip detectors' latches per channel (#1067): the ADC status (:ADC:CLIPped?,
        # low/high, read clears) and the channel's own (:CLIPped?, 0/1, read clears and a scale or
        # offset change away from the latched (scale, offset) resets it)
        self.clip_latches: dict[int, dict[str, Any]] = {}
        # channels whose input went beyond the maximum input range since :OVLRegister? was last read (#1264)
        self.overloaded: set[int] = set()
        self.acq_count = 0
        self.running = True
        self.armed = False
        self.last_triggered = False
        # ty sees the @component class as type[Component] and cannot match it to the protocol.
        self.pending = PendingAcquisitions(cast("Acquisition", self))

    def reset(self) -> None:
        self.records = {}
        self.overloaded = set()
        self.segment_records = []
        self.acq_count = 0
        self.running = True
        self.armed = False
        self.last_triggered = False
        self.pending.clear()

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        """A segment index beyond the segment count is refused (-222); more segments than the
        memory holds at the present record length are refused (-221); the old value is kept."""
        if key == "segment_index":
            if not 1 <= int(new) <= self.segment_count():
                raise ScpiError(-222, f"segment index outside 1 to {self.segment_count()}")
            self._select_segment(int(new))
            self.session.invalidate()
        elif key in ("segments", "segmented"):
            self._check_memory()

    # ---- the modules ------------------------------------------------------------------------------

    def _module(self, key: str, name: str | None = None) -> Any:
        comp = self.peer(name or str(self.param(key)), required=True)
        assert comp is not None
        return comp

    def horizontal(self) -> ScopeTimebase:
        return self._module("horizontal")  # type: ignore[no-any-return]

    def trigger_unit(self) -> ScopeTrigger:
        return self._module("trigger")  # type: ignore[no-any-return]

    def adc_unit(self) -> ScopeAdc:
        return self._module("adc")  # type: ignore[no-any-return]

    def decimator_unit(self) -> Any:
        """The scope_decimator module."""
        return self._module("decimator")

    def vertical_of(self, n: int) -> ScopeVertical:
        return self._module("vertical", f"ch{n}.{self.param('vertical')}")  # type: ignore[no-any-return]

    def probe_ratio(self, channel: int) -> float:
        return float(self._module("probe", f"ch{channel}.{self.param('probe')}").effective_ratio())

    def probe_skew(self, n: int) -> float:
        """Channel n's skew (the setting and the unit's own), seconds; 0 without a probe module."""
        if not self.param("probe"):
            return 0.0
        return self._module("probe", f"ch{n}.{self.param('probe')}").delay()  # type: ignore[no-any-return]

    def scale_offset(self, n: int) -> tuple[float, float]:
        """Channel n's scale (V/div) and offset (V) as displayed."""
        v = self.vertical_of(n)
        return float(v.param("scale")), float(v.param("offset"))

    # ---- helpers ----------------------------------------------------------------------------------

    def _st(self, path: str) -> Any:
        assert self.owner is not None
        return self.owner.get_state(path)

    def _channels(self) -> int:
        assert self.owner is not None
        return self.owner.channel_count

    def _is(self, key: str, value: Any = None) -> bool:
        """Whether the run mode (or ``value``) is one of the values listed in parameter ``key``."""
        return str(self.param("mode") if value is None else value).upper() in _upper(self.param(key))

    def _set_mode(self, value: str) -> None:
        assert self.owner is not None
        path = binding_of(self._params.get("mode"))
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the run mode is not bound to a setting")
        self.owner.set_state(self.resolve_path(path), value)

    def _display_signal(self, n: int) -> Signal:
        """Signal of channel n in display volts, before the converter: what the trigger compares."""
        return self.adc_unit().analog(n)

    def trigger_level(self) -> float:
        """The trigger level in display volts (the scope screen draws it)."""
        return self.trigger_unit().level()

    def timebase_scale(self) -> float:
        return float(self.horizontal().param("scale"))

    def displayed(self) -> list[int]:
        return _displayed(self)

    # ---- record length ------------------------------------------------------------------------------

    def _top_rate(self) -> float:
        return self.adc_unit().top_rate(set(self.displayed()))

    def sample_rate(self) -> float:
        return self.horizontal().sample_rate(self._top_rate())

    def _timing(self) -> tuple[int, float, int, int]:
        """Points, sample interval, samples before the trigger and samples from it on."""
        return self.horizontal().timing(self._top_rate())

    # ---- sampling, noise and the ADC ----------------------------------------------------------------

    def _averages(self) -> int:
        return self.decimator_unit().averages()

    def _jitter(self, t_start_ns: int, frame: int | None = None, segment: int = 0) -> float:
        jitter = float(self.param("trigger_jitter_s"))
        if not jitter:
            return 0.0
        if frame is not None:  # a displayed frame of a running scope (shows_live), not an acquisition
            return jitter * float(
                gaussian(stream_key(self.session.seed, self.uid, "frame", frame), np.zeros(1))[0]
            )
        if self.param("noise_key") == "time":
            key = stream_key(self.session.seed, self.uid, "jitter")
            return jitter * float(gaussian(key, np.array([t_start_ns]))[0])
        # A later segment of the same acquisition has a stream of its own; the first keeps the key
        # an unsegmented acquisition has.
        tail = ("segment", segment) if segment else ()
        key = stream_key(self.session.seed, self.uid, "jitter", self.acq_count, *tail)
        return jitter * float(gaussian(key, np.zeros(1))[0])

    def _request(
        self,
        n: int,
        t0: int,
        start: int,
        dt: float,
        points: int,
        t_start_ns: int,
        m: int,
        frame: int | None,
        segment: int = 0,
    ) -> FrameRequest:
        """What the acquisition asks channel n's chain for: ``points`` samples ``dt`` apart from
        sample ``start`` of the grid at ``t0``, and the random stream of its input noise — a pure
        function of the seed, the channel and the key. A displayed frame has a stream of its own;
        an acquisition's is keyed by the absolute sample instants (``noise_key: time``, D46) or by
        the acquisition count (and, after the first, the segment's number: the segments of one
        acquisition carry independent noise). The index counts on the converter's grid (``m``
        samples per record sample when peak detect oversamples)."""
        if frame is not None:
            return FrameRequest(t0, points, dt, (self.uid, "frame", n, frame), first=start)
        if self.param("noise_key") == "time":
            native_dt = dt / m
            key = (self.uid, "noise", n, native_dt, str(self.param("noise_stream")))
            return FrameRequest(t0, points, dt, key, math.floor(t_start_ns * 1e-9 / native_dt), first=start)
        tail = ("segment", segment) if segment else ()
        return FrameRequest(t0, points, dt, (self.uid, "noise", n, self.acq_count, *tail), first=start)

    def adc_codes_per_div(self) -> float:
        return self.adc_unit().codes_per_div()

    # ---- acquisition ------------------------------------------------------------------------------

    def compute(self, channels: list[int], frame: int | None = None) -> tuple[dict[int, Record], int, bool]:
        """Sample the listed channels at the current instant without changing any state.

        Returns the records, the virtual time an acquisition takes (ns) and whether it triggered.
        ``frame`` numbers a frame the screen of a running scope shows: its noise is its own.
        """
        records, used, triggered, _ = self._acquire(channels, frame)
        return records, used, triggered

    def _acquire(
        self,
        channels: list[int],
        frame: int | None = None,
        t0: int | None = None,
        segment: int = 0,
        earliest_ns: int | None = None,
    ) -> tuple[dict[int, Record], int, bool, int]:
        """:meth:`compute`, and the instant of the trigger (or of the record's trigger point) in ns.

        A later ``segment`` of a segmented acquisition is armed at ``t0`` (not now) and takes no
        trigger before ``earliest_ns``."""
        points, nominal_dt, pre, post = self._timing()
        # A fast clock (positive clock_ppm) samples closer together than the nominal interval; the
        # record keeps the nominal dt the scope reports and reconstructs with, so time measurements
        # read low by that fraction (the spectrum analyzer's reference convention, G3).
        dt = nominal_dt / (1.0 + self.horizontal().clock_error())
        t0 = self.session.now_ns if t0 is None else t0
        earliest_s = 0.0 if earliest_ns is None else max(0.0, (earliest_ns - t0) * 1e-9)
        # An auto mode gives up after its auto timeout; a normal or single acquisition waits for
        # its edge, the virtual time it takes included (G5).
        hit = self._trigger_hit(
            t0, dt, pre, points, waits=not self._is("auto") and frame is None, earliest_s=earliest_s
        )
        triggered = hit is not None
        k = hit.index if hit is not None else pre
        start = k - pre
        if frame is not None and hit is None:
            # An untriggered frame of a running scope: a free-running scope starts each sweep at an
            # arbitrary instant, so the trace is not locked to the signal and slides from frame to
            # frame. The offset is a pure function of the seed and the frame number (no virtual time
            # passes, and a replay shows the same screen).
            phase = float(
                uniform(stream_key(self.session.seed, self.uid, "frame-phase", frame), np.zeros(1))[0]
            )
            t0 += round(phase * points * dt * 1e9)
        t_start_ns = t0 + round(start * dt * 1e9)
        m = self.decimator_unit().oversample()  # converter samples per record sample (peak detect)
        # The record samples on the clock's grid; where the trigger fell between two samples, and
        # the trigger's jitter, are in the record's time origin (the scope's xorigin), not in the
        # samples.
        jitter = self._jitter(t_start_ns, frame, segment)
        x_origin = (start * dt - hit.t_s - jitter) if hit is not None else -pre * dt
        trigger_ns = t0 + round(hit.t_s * 1e9) if hit is not None else None
        records: dict[int, Record] = {}
        # ``frame`` numbers a screen frame of a running scope (compute: no state changes), never an
        # acquisition: an acquisition — each segment of a segmented one, each average — comes here
        # with frame None and is checked; a screen frame latches nothing (#1304).
        if frame is None:
            self._check_overload(channels, t_start_ns, points * dt)
        for n in channels:
            scale, offset = self.scale_offset(n)
            request = self._request(n, t0, start, dt, points, t_start_ns, m, frame, segment)
            # A deep record is computed where it is read (#1012); one up to EAGER_POINTS now, whole:
            # the channel's chain (ADC, then the decimator's acquisition mode) gives the record's
            # samples, the codes, as volts (D46: they are kept).
            deep = self._deep(n, request)
            volts, clip = None, None
            if deep is None:
                frames = self.input_frames(f"in{n}", request)
                volts = self.vertical_of(n).correct(frames.data)
                self._latch_clip(n, frames)
                if frames.clip_sides is not None:
                    clip = (1 if frames.clip_sides[0] else 0) | (2 if frames.clip_sides[1] else 0)
            records[n] = Record(
                channel=n,
                t_start_ns=t_start_ns,
                dt_s=nominal_dt,
                trigger_index=pre,
                volts=volts,
                deep=deep,
                scale=scale,
                offset=offset,
                triggered=triggered,
                x_origin_s=x_origin * nominal_dt / dt,  # in the scope's own (nominal) time
                trigger_ns=trigger_ns,
            )
            records[n].code_scale = self.adc_unit().converter_scale(
                scale, float(self.vertical_of(n).param("probe_ratio"))
            )
            records[n].clip = clip
        if triggered or self.param("untriggered") == "record":
            used = (k + max(post, 0)) * dt
        else:  # the whole trigger search
            used = points * int(self.trigger_unit().param("search_windows")) * dt
        used = used * self._averages() + float(self.param("overhead_s"))
        return records, round(used * 1e9), triggered, t0 + round(k * dt * 1e9)

    def _latch_clip(self, n: int, frame: Any) -> None:
        """The ADC's clip detectors latch a channel's clipping until it is read or cleared
        (:CHANnel<N>:ADC:CLIPped? / :CHANnel<N>:CLIPped?, #1067). Only a record computed whole
        carries the ADC's codes; a deep one computes at read and does not latch."""
        if not frame.clipped:
            return
        fmt = frame.fmt
        if frame.clip_sides is not None:  # the converter's own (a filter after it keeps them, #1262)
            low, high = frame.clip_sides
        elif fmt.bits <= 0:
            return
        else:
            half, lsb = 1 << (fmt.bits - 1), float(fmt.lsb)
            low = bool(np.min(frame.data) <= -half * lsb)
            high = bool(np.max(frame.data) >= (half - 1) * lsb)
        at = self.scale_offset(n)
        latch = self.clip_latches.setdefault(n, {"adc_low": False, "adc_high": False, "low": False, "at": at})
        latch["adc_low"] |= low
        latch["adc_high"] |= high
        latch["low"] |= low or high
        latch["at"] = at

    def _check_overload(self, channels: list[int], t0_ns: int, span_s: float) -> None:
        """Each acquired channel's input against its limits (#1264): beyond the maximum input range
        the channel's overload bit latches (:OVLRegister?, read clears); beyond the absolute maximum
        the instrument warns of possible damage (an event and the problems list)."""
        add = str(self.adc_unit().param("offset_sign")).lower() == "add"
        for n in channels:
            vertical = self.vertical_of(n)
            over, damage = vertical.overload(add, t0_ns, span_s)
            if over:
                self.overloaded.add(n)
            if damage and self.owner is not None:
                lo, hi = vertical.input_extremes(t0_ns, span_s)
                self.owner.warn(
                    f"CH{n}-OVERVOLTAGE",
                    f"CH{n} 的输入达到 {max(abs(lo), abs(hi)):.3g} V，超过最大输入 ±{float(vertical.param('max_input_v')):g} V，"
                    "真机可能损坏前端（Input beyond the absolute maximum: possible damage）",
                )

    def overload_bits(self) -> int:
        """The Overload Event Register: bit n-1 for each channel overloaded since the last read,
        which clears it (#1264)."""
        bits = sum(1 << (n - 1) for n in self.overloaded)
        self.overloaded = set()
        return bits

    @op("adc_clip_status")
    def adc_clip_status(self, channel: int | None = None, **_: Any) -> OpResult:
        """``:CHANnel<n>:ADC:CLIPped?``: whether the ADC clipped low, high or both since the last
        read or clear — 0, 1, 2 or 3; reading resets it (the guide, p. 484)."""
        n = channel or 1
        latch = self.clip_latches.get(n)
        code = (2 if latch is not None and latch["adc_high"] else 0) + (
            1 if latch is not None and latch["adc_low"] else 0
        )
        if latch is not None:
            latch["adc_low"] = latch["adc_high"] = False
        return OpResult(str(code).encode())

    @op("clipped_status")
    def clipped_status(self, channel: int | None = None, **_: Any) -> OpResult:
        """``:CHANnel<n>:CLIPped?``: whether the channel clipped at least once (0 or 1) since the
        last read or clear; reading resets it, and so does a change of the channel's scale or
        offset (the guide, p. 485)."""
        n = channel or 1
        latch = self.clip_latches.get(n)
        code = 0
        if latch is not None and latch["low"]:
            code = 1 if latch["at"] == self.scale_offset(n) else 0
            latch["low"] = False  # the read resets it (a scale or offset change resets it too)
        return OpResult(str(code).encode())

    @op("clear_clip")
    def clear_clip(self, **_: Any) -> OpResult:
        """``:ACQuire:ADC:CLIPped:CLEar``: the clipping status of every channel clears at once
        (the guide, p. 319)."""
        self.clip_latches.clear()
        return OpResult()

    def _deep(self, n: int, request: FrameRequest) -> DeepRecord | None:
        """Channel n's record for ``request`` as a deep record — the chain captured, nothing
        computed — when it is deeper than ``EAGER_POINTS`` or its samples along the chain exceed the
        model's fan-out (an average of many acquisitions). None: computed whole now. A chain other
        than the scope's own ADC, its digital filters (#1262) and decimator is computed whole (and
        refused beyond the model)."""
        dec = self.decimator_unit()
        view = dec.view() if isinstance(dec, ScopeDecimator) else None
        # A high-resolution boxcar is a running sum along the whole record, whose rounding a tile
        # cannot repeat: such a record stays whole as far as the model holds one (MAX_FRAME).
        eager = MAX_FRAME if view is not None and view.proc == "eres" and view.w > 1 else EAGER_POINTS
        if view is None or (request.count <= eager and request.count * view.cost <= MAX_REQUEST_FANOUT):
            return None
        assert self.owner is not None and dec.owner is not None
        if self.owner.wiring.source(self.name, f"in{n}") != (dec.name, f"out{n}"):
            return None
        from .scope_dsp import ScopeDspFilter  # the filter module builds on this one

        filters = []
        src = dec.owner.wiring.source(dec.name, f"in{n}")
        converter = dec.owner.components.get(src[0]) if src is not None else None
        while (
            isinstance(converter, ScopeDspFilter) and src == (converter.name, f"out{n}") and len(filters) < 8
        ):
            filters.append(converter.view(n, request.dt_s))
            src = dec.owner.wiring.source(converter.name, f"in{n}")
            converter = dec.owner.components.get(src[0]) if src is not None else None
        if not isinstance(converter, ScopeAdc) or src != (converter.name, f"out{n}"):
            return None
        return DeepRecord(
            request.count,
            request,
            converter.view(n),
            view,
            self.vertical_of(n).correction(),
            tuple(filters[::-1]),
        )

    def _trigger_hit(
        self, t0: int, dt: float, pre: int, points: int, waits: bool, earliest_s: float = 0.0
    ) -> TriggerHit | None:
        """The trigger the acquisition starting at ``t0`` finds (``waits``: as far as the trigger's
        search horizon; none before ``earliest_s``), or None."""
        trig = self.trigger_unit()
        # The mode's family looks at its own source (#1184): each advanced family keeps one, the
        # edge and glitch families share the main one.
        source = trig.family_source()
        n_trig = trig.source_channel(source)
        kind = trig.source_kind(source)
        if kind == "channel":
            sig, scale_t = self._display_signal(n_trig), self.scale_offset(n_trig)[0]  # type: ignore[arg-type]
        elif kind == "external":
            sig, scale_t = trig.input("ext"), 1.0
        else:
            sig, scale_t = Signal.zero(), 1.0
        # A waiting acquisition looks as far as its horizon; an auto one (or a screen frame) as
        # far as the auto timeout (#1331).
        horizon = trig.param("search_horizon_s") if waits else trig.param("auto_timeout_s")
        return trig.search(
            sig, t0, dt, pre, points, n_trig, scale_t,
            None if horizon is None else float(horizon),
            earliest_s,
        )  # fmt: skip

    # ---- segments (segmented memory, #957) ------------------------------------------------------------

    def segment_count(self) -> int:
        """Records one acquisition takes: ``segments`` while ``segmented``, else one."""
        return max(1, int(self.param("segments"))) if V.flag(self.param("segmented")) else 1

    def _check_memory(self) -> None:
        """The segments of one acquisition fit the memory (``memory_points``), or -221."""
        count, limit = self.segment_count(), self.param("memory_points")
        if count > 1 and limit is not None and count * self._timing()[0] > float(limit):
            raise ScpiError(
                -221,
                f"{count} segments of {self._timing()[0]} points exceed the memory ({int(limit)} points)",
            )

    def acquire_segments(
        self, channels: list[int], taken: list[_Segment] | tuple[()] = (), force: bool = False
    ) -> tuple[list[_Segment], bool]:
        """The segments of one acquisition starting now, after those already ``taken``, without
        changing any state: each on its own trigger, the trigger armed for the next ``rearm_s``
        after a record is full (so with the pre-trigger time to fill first), the holdoff counted
        from the segment before. Returns the segments and whether all of them are there: a segment
        whose trigger does not come ends the list, unless the acquisition is forced or the mode is
        an auto mode (the segment is then taken untriggered)."""
        count = self.segment_count()
        self._check_memory()
        trig = self.trigger_unit()
        now = self.session.now_ns
        _, nominal_dt, pre, _ = self._timing()
        fill_ns = round(pre * nominal_dt * 1e9)
        overhead, rearm = (round(float(self.param(k)) * 1e9) for k in ("overhead_s", "rearm_s"))
        segments = list(taken)
        held = trig.last_ns
        try:
            while len(segments) < count:
                if segments:
                    # Armed since the record before was full and the re-arm time passed; when that
                    # is long ago the pre-trigger memory is full already, and the search starts now.
                    t0, earliest = max(segments[-1].end_ns + rearm, now - fill_ns), now
                    if segments[-1].triggered:
                        trig.last_ns = segments[-1].trigger_ns
                else:
                    t0, earliest = now, None
                records, used, triggered, trigger_ns = self._acquire(
                    channels, t0=t0, segment=len(segments), earliest_ns=earliest
                )
                if not (triggered or force or self._is("auto")):
                    return segments, False
                segments.append(_Segment(records, triggered, trigger_ns, t0 + used - overhead))
        finally:
            trig.last_ns = held
        return segments, True

    def store_segments(self, segments: list[_Segment], used: int) -> int:
        """Keep the segments of one acquisition (:meth:`store` for each); reads and measurements
        then use the one ``segment_index`` selects. Returns ``used``."""
        if len(segments) == 1:
            return self.store(segments[0].records, used)
        for k, seg in enumerate(segments):
            self.store(seg.records, used, segment=k, of=len(segments))
        self.segment_records = [dict(seg.records) for seg in segments]
        self._select_segment(int(self.param("segment_index")))
        return used

    def _selected(self, index: int | None = None) -> int:
        """The selected segment, from 0, among those the latest acquisition has."""
        i = int(self.param("segment_index")) if index is None else index
        return min(max(i, 1), max(len(self.segment_records), 1)) - 1

    def _select_segment(self, index: int) -> None:
        if self.segment_records:
            self.records = dict(self.segment_records[self._selected(index)])

    def segment_time_tag(self) -> float:
        """The selected segment's trigger, seconds after the first segment's (0 without segments)."""
        if not self.segment_records:
            return 0.0

        def instant(records: dict[int, Record]) -> int:
            rec = next(iter(records.values()))
            if rec.trigger_ns is not None:
                return rec.trigger_ns
            return rec.t_start_ns + round(rec.trigger_index * rec.dt_s * 1e9)

        return (instant(self.segment_records[self._selected()]) - instant(self.segment_records[0])) * 1e-9

    def read_segments(self, channel: int) -> list[Record] | None:
        """The latest acquisition's segments' records for a channel (``:WAVeform:SEGMented:ALL``,
        #1066), each segment one full record; None when the latest acquisition was not a segmented
        one or the channel has no records in it. Read as they are: the transfer concatenates."""
        if not self.segment_records:
            return None
        recs: list[Record] = []
        for seg in self.segment_records:
            rec = seg.get(channel)
            if rec is None:
                return None
            recs.append(rec)
        return recs

    @op("set_segment_index")
    def set_segment_index(self, query: bool = False, index: int | None = None, **_: Any) -> OpResult:
        """``:ACQuire:SEGMented:INDex``: the segment the reads and the display use. Beyond the
        segments the latest acquisition has, the guide (p. 345) errors that the data is out of
        range and sets the index to the maximum segment number."""
        assert self.owner is not None
        path = self.bound_path("segment_index")
        if path is None:
            raise ScpiError(-300, f"{self.uid}: segment_index is not bound to a setting")
        if query:
            return OpResult(str(int(self.owner.get_state(path))).encode())
        if index is None:
            raise ScpiError(-109, "an index is required")
        i = int(index)
        limit = max(1, len(self.segment_records))
        if i < 1 or i > limit:
            self.owner.set_state(path, min(max(i, 1), limit))
            raise ScpiError(-222, f"segment {i} is out of range: {limit} segment(s) acquired")
        self.owner.set_state(path, i)
        return OpResult()

    def triggers_now(self) -> bool:
        """Whether an acquisition starting now finds its trigger within its search windows (a
        continuous run's state: it re-arms on every trigger, so it is waiting while none comes)."""
        points, nominal_dt, pre, _ = self._timing()
        dt = nominal_dt / (1.0 + self.horizontal().clock_error())
        return self._trigger_hit(self.session.now_ns, dt, pre, points, waits=False) is not None

    def store(
        self,
        records: dict[int, Record],
        used: int,
        forced: bool = False,
        segment: int | None = None,
        of: int = 1,
    ) -> int:
        """Keep ``records`` as the latest acquisition and log its recipe (D36). Returns ``used``.

        ``forced``: read from a channel that had no record, whatever the run mode. ``segment``
        (from 0) ``of`` that many: one segment of a segmented acquisition (:meth:`store_segments`);
        the acquisition counts once, with its last segment."""
        if segment is None:
            self.segment_records = []  # the latest acquisition is not a segmented one
        self.records.update(records)
        instants = [r.trigger_ns for r in records.values() if r.triggered and r.trigger_ns is not None]
        if instants:
            self.trigger_unit().last_ns = max(instants)  # the holdoff runs from here
            if self.owner is not None:
                self.owner.status_event("TRG")  # the trigger event register (:TER?, #248)
        if self.owner is not None and records:
            # every stored acquisition is done (Infiniium :ADER?, #602); one an auto sweep took
            # without its trigger is an auto trigger (:ATER?)
            self.owner.status_event("DONE")
            if not instants and not forced and self._is("auto"):
                self.owner.status_event("AUTO")
        count = self.acq_count + 1
        if segment is None or segment == of - 1:
            self.acq_count = count
        self.session.invalidate()
        if self.owner is not None:
            # The acquisition's recipe: everything needed to regenerate it from the bench (D36).
            self.session.instrument_event(
                self.owner,
                "acquisition",
                {
                    "count": count,
                    **({} if segment is None else {"segment": segment + 1, "segments": of}),
                    "forced": forced,
                    "channels": {
                        str(ch): {
                            "t_start_ns": rec.t_start_ns,
                            "dt_s": rec.dt_s,
                            "points": rec.points,
                            "trigger_index": rec.trigger_index,
                            "x_origin_s": rec.origin,
                            "triggered": rec.triggered,
                            # a deep record is computed where it is read (#1012): no digest of it
                            **(
                                {"computed": "on_read"}
                                if rec.deep is not None
                                else {
                                    "sha256": hashlib.sha256(
                                        np.ascontiguousarray(rec.volts).tobytes()
                                    ).hexdigest()
                                }
                            ),
                        }
                        for ch, rec in sorted(records.items())
                    },
                    "seed": self.session.seed,
                },
            )
        return used

    # ---- run control ------------------------------------------------------------------------------

    def run_state(self) -> str:
        """RUN, STOP or SING (``:RSTate?``, #248): a single acquisition armed and not yet done reads
        SING, a stopped scope STOP, a running one RUN."""
        if self.armed and self._is("single"):
            return "SING"
        return "RUN" if self.is_running() else "STOP"

    def status_flags(self) -> dict[str, bool]:
        """RUN while acquiring, WAIT while waiting for a trigger (the operation status register).

        A single acquisition waits until it is done; a continuous run (auto, normal) waits while
        its trigger finds no edge now, whatever an earlier acquisition found (#352)."""
        running = self.run_state() != "STOP"
        if not running:
            return {"RUN": False, "WAIT": False}
        if self.waiting():
            return {"RUN": True, "WAIT": True}
        if self._is("auto") or self._is("normal"):
            return {"RUN": True, "WAIT": not self.triggers_now()}
        return {"RUN": True, "WAIT": not self.last_triggered}

    @op("run_state")
    def op_run_state(self, **_: Any) -> OpResult:
        return OpResult(self.run_state().encode())

    def is_running(self) -> bool:
        """Stopped by a stop mode (when the model has one) or by a stop command."""
        if self.param("stop"):
            return not self._is("stop")
        return self.running

    def shows_live(self) -> bool:
        """Whether the screen follows the signal (running in an auto or normal mode) rather than
        holding the last acquisition (stopped, or a single acquisition waiting)."""
        return self.is_running() and (self._is("auto") or self._is("normal")) and not self.armed

    def screen_state(self, frame: int | None = None) -> ScreenState:
        """What the screen shows (the :class:`~simubench.engine.component.ScreenSource` protocol):
        the screen's own rules live here — a running scope redraws the signal now (a frame is not an
        acquisition: no record, no virtual time, noise and untriggered phase of its own, D47); an
        untriggered normal mode holds; a frame beyond the model leaves the screen holding what it
        has (#986). ``frame`` is the projection's redraw counter (ServedSession.frames)."""
        stored = dict(self.records)
        live, records, shown = self.shows_live(), stored, False
        if live and frame is not None:
            try:
                drawn, _, triggered = self.compute(self.displayed(), frame=frame)
                # A triggered or auto frame replaces the records; an untriggered normal frame
                # does not (the screen keeps the last acquisition until the trigger comes).
                if triggered or self._is("auto") or not stored:
                    records, shown = drawn, True
            except ModelLimit:
                live = False  # the screen holds what it has, as a read of such an acquisition is refused
        try:
            _, dt, pre, _ = self._timing()
            screen_left = -pre * dt
        except (AttributeError, ModelLimit, ScpiError, ValueError):  # a sampling scope has no such timing
            screen_left = -5.0 * float(self._st("timebase.scale"))
        return ScreenState(
            live=live,
            shown=shown,
            records=records,
            stored=stored,
            waiting=bool(self.armed and self._is("single")),
            trigger_level=self.trigger_level(),
            trigger_source=channel_index(self._st("trigger.source")),
            acquisitions=int(self.acq_count),
            screen_left_s=screen_left,
        )

    def _finish_single(self) -> None:
        self.armed = False
        self.running = False
        after = str(self.param("after_single") or "")
        if after:
            self._set_mode(after)

    def _acquisition_channels(self, channel: int | None) -> list[int]:
        chans = set(self.displayed())
        if channel is not None and self.param("missing") == "acquire":
            chans.add(channel)
        return sorted(chans)

    def pending_mode(self) -> bool:
        """Acquisitions finish in virtual time; one a front-panel button starts finishes at once,
        as the panel's own (D32: no virtual time passes for a panel operation)."""
        on_panel = self.owner is not None and self.owner.on_panel
        return str(self.param("completion")) == "pending" and not on_panel

    def acquire_now(
        self, channels: list[int] | None = None, force: bool = False, stopped_too: bool = False
    ) -> int:
        """One acquisition attempt as the run mode allows it (``force``: whatever the trigger and the
        run state; ``stopped_too``: also when stopped, still waiting for a trigger). Returns the
        virtual time used (ns): with ``completion: pending`` the attempt is scheduled instead, and
        uses none."""
        if not force and not stopped_too and not self.is_running():
            return 0
        if self.pending_mode() and (force or stopped_too or self._is("single")):
            self.pending.schedule(channels, force)  # continuous runs (auto, normal) refresh at once
            return 0
        chans = self.displayed() if channels is None else channels
        if self.segment_count() > 1:
            segments, complete = self.acquire_segments(chans, force=force)
            self.last_triggered = complete and all(seg.triggered for seg in segments)
            if not complete:
                return 0  # waiting for a segment's trigger: the previous records stay
            used = segments[-1].end_ns + round(float(self.param("overhead_s")) * 1e9) - self.session.now_ns
            self.store_segments(segments, used)
            if self._is("single"):
                self._finish_single()
            return used
        records, used, triggered = self.compute(chans)
        self.last_triggered = triggered
        if force or triggered or self._is("auto"):
            self.store(records, used)
            if self._is("single"):
                self._finish_single()
        return used  # waiting for a trigger: the previous records stay

    def poll(self) -> int:
        """A single acquisition still waiting: try again now (the signal may have arrived since)."""
        if self.pending_mode():
            self.retry_pending()
            return 0
        return self.acquire_now() if self.armed and self._is("single") else 0

    # ---- the pending acquisitions (completion: pending), a PendingAcquisitions ----------------------

    def waiting(self) -> bool:
        """A single acquisition (or a digitize) not done yet."""
        return self.pending.waiting()

    has_retry_pending = True

    def retry_pending(self) -> None:
        """Armed for a trigger that had not come: look again now (the signal may have changed)."""
        self.pending.retry()

    def _keep_last_frame(self) -> None:
        """A running scope has been acquiring all along: before a single acquisition (or a stop)
        replaces the run, the frame it shows now is its latest acquisition, which a read gets
        until the single one is done (completion: pending)."""
        records, used, triggered = self.compute(self.displayed())
        if records and (triggered or self._is("auto")):
            self.store(records, used)

    def read_record(self, channel: int, start: bool = True) -> tuple[Record | None, int]:
        """The record a read uses, and the virtual time getting it took (``reacquire``, ``missing``).

        ``start``: the read starts a transfer (``on_read_start`` acquires anew only then)."""
        used = 0
        how = self.param("reacquire")
        fresh = how == "every_read" or (how == "on_read_start" and start)
        running = self.is_running() and (self._is("auto") or self._is("normal"))
        if self.pending_mode() and self.waiting():
            if str(self.param("while_waiting")) == "error":
                raise ScpiError(-221, "acquisition in progress")
        elif (fresh and running) or (self.armed and self._is("single")):
            used += self.acquire_now(self._acquisition_channels(channel))
        rec = self.records.get(channel)
        if rec is None and self.param("missing") == "acquire":
            # Nothing acquired on this channel yet (stopped, or no trigger): rather than an empty
            # trace, read what the channel shows now, and say so in the event history.
            records, t, _ = self.compute(self._acquisition_channels(channel))
            used += self.store(records, t, forced=True)
            rec = self.records[channel]
        return rec, used

    def transfer_record(self, channel: int, first: int) -> tuple[Record | None, int]:
        """The record a waveform transfer reads (``waveform_transfer``) and the time getting it took;
        ``first`` is the transfer's first point."""
        return self.read_record(channel, start=first == 0)

    def measure_record(self, channel: int) -> tuple[Record, int]:
        """The record a measurement uses: as a read, acquiring the channel when it has none."""
        rec, used = self.read_record(channel)
        if rec is None:
            records, t, _ = self.compute([channel])
            used += self.store(records, t, forced=True)
            rec = self.records[channel]
        return rec, used

    def mode_text(self) -> bytes:
        """The run mode as the definition formats its settings."""
        assert self.owner is not None
        path = self.resolve_path(binding_of(self._params.get("mode")) or "")
        return self.owner.runner.format_setting(path)

    @op("run_mode")
    def run_mode(self, query: bool = False, mode: str | None = None, **_: Any) -> OpResult:
        """``:TRIGger:MODE``, ``TRMD``: a single mode arms one acquisition (and acquires at once if
        it can), a force mode takes one frame and stops, a stop mode stops."""
        if query:
            used = self.poll()
            return OpResult(self.mode_text(), used)
        if mode is None:
            raise ScpiError(-109)
        self.pending.cancel()  # a new mode replaces whatever acquisition was waiting
        if self.pending_mode() and self.is_running() and (self._is("auto") or self._is("normal")):
            self._keep_last_frame()
        self._set_mode(mode)
        if self._is("force", mode):
            used = self.acquire_now(force=True)
            self.running, self.armed = False, False
            return OpResult(duration_ns=used)
        if self._is("single", mode):
            self.running, self.armed = True, True
            if self.owner is not None:
                self.owner.status_event("ARM")  # armed for a trigger (:AER?, #248)
            return OpResult(duration_ns=self.acquire_now())
        self.running, self.armed = not self._is("stop", mode), False
        return OpResult()

    @op("single")
    def single(self, **_: Any) -> OpResult:
        """``ARM``: one acquisition, like setting the first single mode."""
        return self.run_mode(mode=str(self.param("single")[0]))

    @op("run")
    def run(self, **_: Any) -> OpResult:
        """``:TRIGger:RUN``: run again; in a single mode, arm one acquisition. A model whose stop is a
        mode (LeCroy's STOP) runs in its first auto mode."""
        self.running = True
        if self.param("stop") and self._is("stop"):
            self._set_mode(str(self.param("auto")[0]))
        if self.owner is not None:
            self.owner.status_event("ARM")  # armed for a trigger (:AER?, #248)
        if self._is("single"):
            self.armed = True
            return OpResult(duration_ns=self.acquire_now())
        return OpResult()

    @op("stop")
    def stop(self, **_: Any) -> OpResult:
        self.pending.cancel()
        self.running, self.armed = False, False
        stops = self.param("stop")
        if stops:
            self._set_mode(str(stops[0]))
        return OpResult()

    @op("force_trigger")
    def force_trigger(self, **_: Any) -> OpResult:
        """``FRTR``: one acquisition now, triggered or not; a single acquisition then stops."""
        return OpResult(duration_ns=self.acquire_now(force=True))

    @op("wait")
    def wait(self, timeout: float | None = None, **_: Any) -> OpResult:
        """``WAIT [timeout]``: until a pending single acquisition is done (or the timeout passes)."""
        used = self.poll()
        if self.pending_mode() and self.owner is not None:
            if self.pending.due_ns is not None:
                used += self.owner.wait_until(self.pending.due_ns)
            elif timeout is not None and self.waiting():
                used += self.owner.wait_until(self.owner.now_ns() + round(float(timeout) * 1e9))
        return OpResult(duration_ns=used)

    @op("opc")
    def opc(self, query: bool = False, **_: Any) -> OpResult:
        """``*OPC?`` answers ``1`` once a pending single acquisition is done; ``*OPC`` sets the ESR bit."""
        assert self.owner is not None
        used = self.poll()
        if query:
            reply, waited = self.owner.opc_query()  # and whatever else the instrument has pending
            return OpResult(reply, used + waited)
        used += self.owner.wait_pending()
        self.owner.arm_opc()
        return OpResult(duration_ns=used)

    # ---- declared status, acquisition type and figures ----------------------------------------------

    def status(self) -> str:
        """The trigger status in the model's words (``status_text``)."""
        words = {**self.defaults["status_text"], **(self.param("status_text") or {})}
        if not self.is_running():
            return str(words["stopped"])
        if self._is("single"):
            assert self.owner is not None
            if self.pending.trigger_ns is not None and self.owner.now_ns() >= self.pending.trigger_ns:
                return str(words["triggered"])  # the record after the trigger is filling (pending)
            return str(words["waiting"])
        _, _, triggered = self.compute([])
        if self._is("auto"):
            return str(words["triggered" if triggered else "auto"])
        return str(words["triggered" if triggered else "waiting"])

    @op("status")
    def status_query(self, **_: Any) -> OpResult:
        return OpResult(self.status().encode())

    @op("acquire_type")
    def acquire_type_op(self, query: bool = False, kind: str | None = None, value: float | None = None,
                        **_: Any) -> OpResult:  # fmt: skip
        """``:ACQuire:TYPE AVERage,64``: the type, and the count a type takes (``acquire_counts``);
        a count not in the type's ``values`` is refused (-224). The query writes the type, with its
        count by the type's ``format``."""
        assert self.owner is not None
        path = self.resolve_path(binding_of(self._params.get("acquire_type")) or "")
        if not path:
            raise ScpiError(-300, f"{self.uid}: acquire_type names no setting")
        counts = self.param("acquire_counts") or {}
        if query:
            current = str(self._st(path))
            spec = counts.get(current)
            if spec is None:
                return OpResult(current.encode())
            count = self._st(str(spec["setting"]))
            fmt = str(spec.get("format", "{kind},{value}"))
            return OpResult(fmt.format(kind=current, value=count).encode())
        if kind is None:
            raise ScpiError(-109)
        spec = counts.get(kind)
        if spec is not None and value is not None:
            allowed = spec.get("values")
            if allowed is not None and not any(abs(float(value) - float(a)) < 1e-9 for a in allowed):
                raise ScpiError(-224, f"{kind} count must be one of {list(allowed)}")
            setting = str(spec["setting"])
            self.owner.set_state(setting, round(value) if self.owner.specs[setting].type == "int" else value)
        self.owner.set_state(path, kind)
        return OpResult()

    @op("figure")
    def figure(
        self, what: str = "points", integer: bool = False, significant: int = 0, format: str = "", **_: Any
    ) -> OpResult:
        """A read-only figure of the acquisition: ``points`` (record length), ``sample_rate``,
        ``count`` (acquisitions so far, an integer), ``segments`` (the segments the latest
        acquisition has, an integer) or ``time_tag`` (the selected segment's trigger, seconds after
        the first segment's). Numbers come in the definition's number
        format, as an integer with ``integer``, in engineering notation with ``significant``
        digits, or in a printf ``format``."""
        assert self.owner is not None
        if what == "count":
            return OpResult(str(self.acq_count).encode())
        if what == "segments":  # the segments the latest acquisition has (0: it was not segmented)
            return OpResult(str(len(self.segment_records)).encode())
        if integer and what == "points":
            return OpResult(str(int(self._timing()[0])).encode())
        if what == "points":
            value = float(self._timing()[0])
        elif what == "sample_rate":
            value = self.sample_rate()
        elif what == "time_tag":
            value = self.segment_time_tag()
        else:
            raise ScpiError(-300, f"{self.uid}: no figure {what!r}")
        if significant:
            return OpResult(V.format_engineering_digits(value, int(significant)))
        if format:
            return OpResult((format % value).encode())
        return OpResult(self.owner.format_number(value))

    @op("segment_list")
    def segment_list(
        self, what: str = "TTAG", source: Any = None, format: str = "%.10E", **_: Any
    ) -> OpResult:
        """One x figure per segment of the latest acquisition, comma-separated (the Infiniium's
        ``:WAVeform:SEGMented:XLISt?``): ``RELXorigin`` (each segment's first sample, seconds from
        its trigger), ``TTAG`` (its trigger, seconds after the first segment's), ``ABSXorigin`` (the
        two added) or ``OFFSet`` (where it starts in an all-segments transfer: one 0, as only the
        current segment is transferred). Without segments: the current record as one segment."""
        assert self.owner is not None
        kind = str(what).upper()
        n = trigger_channel(source) or 1
        segments = self.segment_records or ([dict(self.records)] if self.records else [])
        if not segments or n not in segments[0]:
            raise ScpiError(-230, "no acquisition for this source")

        def instant(rec: Record) -> int:
            if rec.trigger_ns is not None:
                return rec.trigger_ns
            return rec.t_start_ns + round(rec.trigger_index * rec.dt_s * 1e9)

        first = instant(segments[0][n])
        values: list[float] = []
        for seg in segments:
            rec = seg[n]
            tag = (instant(rec) - first) * 1e-9
            rel = float(rec.origin)
            if kind.startswith("REL"):
                values.append(rel)
            elif kind.startswith("ABS"):
                values.append(rel + tag)
            elif kind.startswith("TTAG"):
                values.append(tag)
            elif kind.startswith("OFFS"):
                return OpResult(b"0")
            else:
                raise ScpiError(-224, f"no segment figure {what!r}")
        return OpResult(",".join(format % v for v in values).encode())

    @op("analyze_segments")
    def analyze_segments(self, **_: Any) -> OpResult:
        """``:ACQuire:SEGMented:ANALyze``: accepted. Measurement statistics and persistence over
        all the segments are not modeled; a measurement reads the selected segment."""
        return OpResult()

    @op("clear_display")
    def clear_display(self, **_: Any) -> OpResult:
        """``:CDISplay``: the display clears and the associated measurements reset (the guide,
        p. 293) — the stored records go (a stopped scope shows nothing until its next
        acquisition, a running one refills them). Waveform memories are not erased; the
        measurement commands stay installed (their values recompute from what is read)."""
        self.records.clear()
        self.segment_records = []
        self.session.invalidate()
        return OpResult()

    @op("points_auto")
    def points_auto(self, query: bool = False, on: bool | None = None, **_: Any) -> OpResult:
        """``:ACQuire:POINts:AUTO``: whether the memory depth follows the AUTO value (the guide,
        p. 339). ON selects the AUTO depth, OFF keeps the current depth as a manual one."""
        return self.horizontal().auto_control("points", query, on)

    @op("srate_auto")
    def srate_auto(self, query: bool = False, on: bool | None = None, **_: Any) -> OpResult:
        """``:ACQuire:SRATe[:ANALog]:AUTO``: whether the sample rate follows AUTO (the guide,
        p. 352). ON selects AUTO, OFF keeps the present rate as a manual one."""
        return self.horizontal().auto_control("sample_rate", query, on, actual=self.sample_rate)

    @op("digitize")
    def digitize(self, sources: list[bytes] | None = None, **_: Any) -> OpResult:
        chans: list[int] = []
        for raw in sources or []:
            name = V.parse_string(raw).upper()
            m = name.removeprefix("CHANNEL").removeprefix("CHAN")
            if not m.isdigit() or not 1 <= int(m) <= self._channels():
                raise ScpiError(-224, f"unknown source {name!r}")
            chans.append(int(m))
            assert self.owner is not None
            self.owner.set_state(f"ch{int(m)}.display", True)
        if not chans:
            chans = self.displayed()
        used = self.acquire_now(chans, stopped_too=True)  # whatever the run state
        if self.pending_mode() and self.pending.due_ns is not None and self.owner is not None:
            used += self.owner.wait_until(self.pending.due_ns)  # a digitize returns once its record is full
        if self.param("digitize_stops"):
            self.running, self.armed = False, False  # the InfiniiVision stops after :DIGitize (#248)
        return OpResult(duration_ns=used)

    @op("self_cal")
    def self_cal(self, **_: Any) -> OpResult:
        """A user self-calibration: every channel re-derives its calibration constants against the
        scope's internal references (``scope_vertical.self_calibrate``; a channel without a
        calibration store has nothing to write). The reply is the diagnostic code ``0``; the
        command's ``reply`` template gives the model's words (e.g. ``*CAL {value}``)."""
        for n in range(1, self._channels() + 1):
            self.vertical_of(n).self_calibrate()
        return OpResult(b"0")


@component("scope_measurements")
class ScopeMeasurements(Component):
    """Automatic measurements on a scope's records (instrument-authoring.md 8.4).

    The engine's measurements are VPP, VMAX, VMIN, VAVerage, VRMS, VAMPlitude (top - base),
    FREQuency and PERiod. ``names`` maps the model's names to them and to a unit (``C1:PAVA? AMPL``,
    ``:MEASure:SIMPle:VALue? PKPK``). How they are taken is declared:

    * the top and the base: the modes of the record's histogram (``histogram``) or its extremes;
    * the crossing level a period is counted on: ``mid`` percent of top minus base, or volts with
      ``levels: absolute``; ``hysteresis`` is half the counter's band, of top minus base;
    * ``min_cycles`` complete cycles a period needs, else it cannot be measured;
    * ``clipped: invalid``: a record that reaches the ADC's limits cannot be measured;
    * ``edge_min_samples``: the samples a rise or fall time needs on its edge (0: none);
      ``shoot``: overshoot and preshoot from the extremes or from the first edge (#1332);
    * ``gate``: [from, to] divisions of the record (0 to 10) the measurement looks at;
    * what cannot be measured answers ``invalid_value`` with the second ``status`` word.

    Every value taken is kept for ``statistics`` (count, mean, sdev, min, max) until cleared.
    """

    label = "自动测量"
    label_en = "Automatic measurements"
    about = "对示波器记录做自动测量。"
    about_en = "Automatic measurements on a scope's records."

    refs = {"record": (RecordSource, "instrument"), "math": (MathSource, "instrument")}
    optional_refs = frozenset({"math"})
    defaults = {
        "record": "acq",  # the acquisition component holding the records (RecordSource: a real-time or sampling one)
        "math": "",  # the math block a FUNCtion<F> source reads from (#1074); empty: channels only
        "source": "",  # the channel measured (a setting such as $measure.source); empty: the command's
        "names": {},  # model name -> [engine measurement, unit], e.g. {AMPL: [VAMPlitude, V]}
        "top_base": "histogram",  # histogram | extremes
        "levels": "percent",  # percent | absolute: what mid is
        "mid": 50.0,  # the crossing level a period is counted on
        "hysteresis": FREQ_HYSTERESIS,  # half the counter's band, of top minus base
        "min_cycles": 1,  # complete cycles a period needs
        "hysteresis_floor_divs": 0.16,  # the counter's band never drops below this fraction of a
        # division of the record's scale (#745): noise alone counts no crossings, so a flat trace
        # of noise measures as nothing, like the real counter's screen-referenced band — an
        # assumed value, no manual states a counter's minimum amplitude (#781); 0 turns it off
        "clipped": "measure",  # measure | invalid: a record at the ADC's limits
        # rise and fall times need this many samples on the edge, one beyond each threshold
        # (the Infiniium's four, #1332); 0: no rule
        "edge_min_samples": 0,
        # overshoot / preshoot: the record's extremes (extremes, InfiniiVision) or the first edge
        # on the screen and its local extreme (first_edge, Infiniium, #1332)
        "shoot": "extremes",
        "gate": None,  # [from, to] divisions of the record; None: all of it
        "invalid_value": "UNDEF",  # what an unmeasurable value answers
        "status": ["OK", "UF"],  # measured, cannot be measured
        "sources": [],  # the two settings DELay?/PHASe? use without sources, e.g. [measure.source, ...]
        "trigger_levels": {},  # channel -> its trigger level setting, for :TRIGger:LEVel:ASETup
        # The screen's measurement list (#373): a measurement command without '?' installs one;
        # past `slots` the oldest leaves. 0: no list (the query only answers).
        "slots": 0,
        "all": [],  # the measurements :MEASure:ALL? answers, in order (engine names)
        # ---- the markers (cursors, #1074): the settings the definition binds the :MARKer words to;
        # a model without them has no marker commands.
        "marker_mode": "",  # the marker mode setting, e.g. $marker.mode: OFF/MANual/XONly/YONly/WAVeform/MEASurement
        "marker_x1": None,  # the Ax marker's x, in the source's x unit (s, or Hz on a transform), e.g. $marker.x1
        "marker_x2": None,  # the Bx marker's x
        "marker_y1": None,  # the Ay marker's y, in the source's vertical unit
        "marker_y2": None,  # the By marker's y
        "marker_source1": "",  # the Ax/Ay markers' source word, e.g. $marker.source1 (CHANnel1, FUNCtion1, …)
        "marker_source2": "",  # the Bx/By markers' source word
        "marker_delta": None,  # whether the deltas are drawn on screen (a bool setting); None: no command
        "marker_measurement": None,  # the measurement the MEASurement mode tracks, 1-based (an int setting)
        "markers": 0,  # how many of the numbered :MARKer<K> markers the model has (1..60); 0: none
        # ---- the markers' units (2000 X-series :MARKer:XUNits/:YUNits, #1155): the words the
        # positions and deltas answer in, and the anchors a :UNits:USE write sets (None: no
        # command, the values pass through unscaled).
        "marker_x_units": "",  # the X cursors' unit word: SEConds/HERTz/DEGRees/PERCent
        "marker_y_units": "",  # the Y cursors' unit word: BASE/PERCent
        "marker_xa1": None,  # the X unit's 0 anchor (a position in the source's x unit)
        "marker_xa2": None,  # the X unit's full anchor (360 degrees, 100 percent)
        "marker_ya1": None,  # the Y unit's 0 anchor
        "marker_ya2": None,  # the Y unit's full anchor
        # The model's measurement thresholds, by setting: "<prefix>.<group><n>.<field>" for channel n,
        # groups "general" (every measurement but rise and fall) and "rfall" (rise and fall times),
        # fields those of measurements.DEFAULT_THRESHOLDS (method, upper, middle, lower, abs_*,
        # hyst_range, hyst_level, tb_method, tb_top, tb_base). Empty: the fixed 10/50/90 % levels.
        "thresholds": "",
        # :MEASure:RESults? without an order: "" oldest first (InfiniiVision), AORDered newest
        # first (the Infiniium: "AORDered (or no option)")
        "results_order": "",
        # what :MEASure:RESults? gives per measurement (a setting such as $measure.statistics): ON
        # (or empty) the label and every statistic; CURRent, MINimum, MAXimum, MEAN, STDDev or
        # COUNt that value alone
        "statistics": "",
        "storage": "",  # the file_system component :DISK:SAVE:MEASurements writes to (empty: none)
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.history: dict[tuple[str, int], list[float]] = {}
        self.installed: list[tuple[str, int]] = []  # the screen's measurements, oldest first (#373)
        self.k_markers: dict[int, dict[str, Any]] = {}  # the numbered markers' state, #1074
        self.names: dict[tuple[str, int], str] = {}  # a measurement's own name (:MEASure:NAME)
        self.screen: list[tuple[str, int]] = []  # top to bottom: the newest first unless moved

    def reset(self) -> None:
        self.history = {}
        self.installed = []
        self.k_markers = {}
        self.names = {}
        self.screen = []

    # ---- the screen's measurement list (#373) ------------------------------------------------------

    def install(self, what: str, n: int) -> None:
        """Put a measurement on the screen: a second install of the same one moves it to the end;
        past ``slots`` the oldest leaves. On screen the newest goes to the top."""
        slots = int(self.param("slots") or 0)
        if slots <= 0:
            raise ScpiError(-113, f"{what}: this model has no measurement list")
        entry = (what, n)
        if entry in self.installed:
            self.installed.remove(entry)
        self.installed.append(entry)
        del self.installed[:-slots]
        if entry in self.screen:
            self.screen.remove(entry)
        self.screen.insert(0, entry)
        self.screen = [e for e in self.screen if e in self.installed]

    def _label(self, what: str, n: int) -> str:
        return self.names.get((what, n)) or (f"{what}(f{-n})" if n < 0 else f"{what}({n})")

    @op("results")
    def op_results(self, order: str = "", **_: Any) -> OpResult:
        """``:MEASure:RESults?``: each installed measurement as ``<label>,<current>,<min>,<max>,<mean>,
        <std dev>,<count>`` — the statistics of every value taken since the list was cleared (the
        current one included). Label: its name, else ``<name>(<channel>)``. Oldest first; ``order``
        AORDered: the newest first, GORDered: the screen's order (top to bottom, after any move).
        Nothing installed: an empty reply."""
        assert self.owner is not None
        word = str(order or self.param("results_order") or "").strip().upper()
        entries = (
            list(reversed(self.installed))
            if word.startswith("AORD")
            else list(self.screen)
            if word.startswith("GORD")
            else list(self.installed)
        )
        parts, used = self._result_rows(entries)
        return OpResult(b",".join(parts), used)

    def _result_rows(self, entries: list[tuple[str, int]], every: bool = False) -> tuple[list[bytes], int]:
        """One row per measurement: label and every statistic, or the one ``statistics`` names
        (``every``: always the full row)."""
        assert self.owner is not None
        stat = "" if every else str(self.param("statistics") or "").strip().upper()
        parts: list[bytes] = []
        used = 0
        for what, n in entries:
            value, u = self.value(what, n)
            used += u
            self._keep(what, n, value)
            values = np.array(self.history.get((what, n)) or [])
            if stat and not stat.startswith("ON"):
                pick = {
                    "CURR": value,
                    "MIN": float(values.min()) if values.size else math.nan,
                    "MAX": float(values.max()) if values.size else math.nan,
                    "MEAN": float(values.mean()) if values.size else math.nan,
                    "STDD": float(values.std()) if values.size else math.nan,
                }
                key = next((k for k in pick if stat.startswith(k)), None)
                parts.append(str(values.size).encode() if key is None else self._number(pick[key]))
                continue
            fields = [self._label(what, n).encode(), self._number(value)]
            if values.size:
                fields += [
                    self._number(float(f())) for f in (values.min, values.max, values.mean, values.std)
                ]
            else:
                fields += [self._number(math.nan)] * 4
            fields.append(str(values.size).encode())
            parts.append(b",".join(fields))
        return parts, used

    @op("store_results")
    def op_store_results(self, name: str = "", **_: Any) -> OpResult:
        """``:DISK:SAVE:MEASurements "<file>"``: the screen's measurements to a CSV file on
        ``storage`` (``.csv`` added): a header line, then one row per measurement, top to bottom —
        label, current, min, max, mean, std dev, count (SimuBench's layout)."""
        assert self.owner is not None
        storage: Any = self.peer(str(self.param("storage")))
        if storage is None:
            raise ScpiError(-300, f"{self.uid}: no storage for measurement files")
        tail = re.split(r"[\\/:]", name)[-1]
        file = name if tail.lower().endswith(".csv") else name + ".csv"
        rows, used = self._result_rows(list(self.screen), every=True)
        text = "Measurement,Current,Min,Max,Mean,Std Dev,Count\r\n" + "".join(
            r.decode() + "\r\n" for r in rows
        )
        storage.store(file, text.encode())
        return OpResult(duration_ns=used)

    def _entry(self, index: Any) -> tuple[str, int]:
        """The screen's measurement at a position from the top (1, or ``MEAS3``)."""
        text = str(index if not isinstance(index, bytes) else V.parse_string(index)).strip().upper()
        digits = text.removeprefix("MEASUREMENT").removeprefix("MEAS")
        if not digits.isdigit() or int(digits) < 1:
            raise ScpiError(-224, f"no measurement {index!r}")
        k = int(digits)
        if k > len(self.screen):
            raise ScpiError(-221, f"measurement {k} is not on the screen")
        return self.screen[k - 1]

    @op("entry")
    def op_entry(
        self,
        query: bool = False,
        what: str = "",
        index: Any = None,
        value: Any = None,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """One measurement of the screen, by its position from the top (``index`` ``MEAS<n>``, or the
        header's ``<n>``): ``name`` (set or read its name, quoted), ``clear`` (take it off),
        ``position`` (move it to another position), ``source`` (read or change its channel)."""
        assert self.owner is not None
        if index is None and suffixes:
            index = next(iter(v for k, v in suffixes.items() if k != "ch"), None)
        entry = self._entry(index)
        k = self.screen.index(entry)
        if what == "name":
            if query:
                return OpResult(V.format_string(self._label(*entry)))
            self.names[entry] = V.parse_string(value) if isinstance(value, bytes) else str(value)
            return OpResult()
        if what == "clear":
            self.screen.remove(entry)
            self.installed.remove(entry)
            return OpResult()
        if what == "position":
            new = int(value)
            if not 1 <= new <= 40:
                raise ScpiError(-222, "position 1 to 40")
            self.screen.pop(k)
            self.screen.insert(min(new, len(self.screen) + 1) - 1, entry)
            return OpResult()
        if what == "source":
            if query:
                return OpResult(self._source_word(entry[1]).encode())
            n = self._channel(None, V.parse_string(value) if isinstance(value, bytes) else str(value))
            moved = (entry[0], n)
            self.screen[k] = moved
            self.installed[self.installed.index(entry)] = moved
            if entry in self.names:
                self.names[moved] = self.names.pop(entry)
            return OpResult()
        raise ScpiError(-300, f"{self.uid}: no entry action {what!r}")

    def _number(self, value: float) -> bytes:
        assert self.owner is not None
        if not math.isfinite(value) and self.param("invalid_value") != "UNDEF":
            return str(self.param("invalid_value")).encode()
        return self.owner.format_number(value)

    @op("clear_list")
    def op_clear_list(self, **_: Any) -> OpResult:
        """``:MEASure:CLEar``: no measurements on the screen, their statistics gone."""
        self.installed = []
        self.screen = []
        self.names = {}
        self.history = {}
        return OpResult()

    def clip_flags(self, n: int) -> int:
        """Whether channel n's record reaches the ADC's limits: 1 low, 2 high, 3 both, 0 neither
        (a channel without a record or an unquantized ADC: 0)."""
        acq = self._acq()
        if n < 0:
            return 0  # a math function has no ADC to reach
        try:
            rec, _ = acq.measure_record(n)
        except ScpiError:
            return 0
        if getattr(rec, "clip", None) is not None:
            return int(rec.clip)  # the converter's own, a filter after it notwithstanding (#1262)
        v = np.asarray(rec.volts, dtype=float)
        if not v.size or not acq.adc_unit().param("bits"):
            return 0
        high, low = acq.adc_unit().rails(rec)
        return (1 if v.min() <= low else 0) | (2 if v.max() >= high else 0)

    @op("clipped")
    def op_clipped(self, channel: int | None = None, sides: bool = False, **_: Any) -> OpResult:
        """ADC clipping of the channel's current record: 1 when it reaches either limit, else 0;
        with ``sides`` 1 low, 2 high, 3 both (the Infiniium's :CHANnel<n>:ADC:CLIPped?)."""
        flags = self.clip_flags(channel or 1)  # the header's channel, not the measurement source
        return OpResult(str(flags if sides else int(bool(flags))).encode())

    @op("overload")
    def op_overload(self, **_: Any) -> OpResult:
        """The Overload Event Register (:OVLRegister?): bit n-1 set for each channel n whose input
        went beyond its maximum input range since the last read; the read clears it (#1264)."""
        return OpResult(str(self._acq().overload_bits()).encode())

    @op("all")
    def op_all(self, source: Any = None, channel: int | None = None, **_: Any) -> OpResult:
        """``:MEASure:ALL?``: the current value of every measurement of ``all`` on one source, in
        that order (a snapshot; nothing is installed)."""
        n = self._channel(channel, source)
        used = 0
        parts = []
        for what in self.param("all") or []:
            value, u = self.value(str(what), n)
            used += u
            parts.append(self._number(value))
        return OpResult(b",".join(parts), used)

    def _acq(self) -> Any:
        assert self.owner is not None
        return self.ref("record")  # type: ignore[return-value]

    @staticmethod
    def _is_source(word: str) -> bool:
        """Whether a parameter word names a source: a channel (CHANnel<n>) or a math function
        (FUNCtion<F>, FUNC<F>, F<F>)."""
        return word.strip().upper().startswith("CH") or function_index(word) is not None

    def _channel(self, channel: int | None, source: Any = None) -> int:
        """The source key a measurement reads: channel n as n, math function F as -F (#1074) —
        the key the statistics, the screen's list and the labels carry."""
        if isinstance(source, list):  # raw parameters: [<interval>][,<type>][,<source>]
            words = [V.parse_string(b).strip().upper() for b in source]
            source = next((w for w in words if self._is_source(w)), None)
        if source:
            name = (source.decode() if isinstance(source, bytes) else str(source)).strip().upper()
            f = function_index(name)
            if f is not None:
                return -f
            m = name.removeprefix("CHANNEL").removeprefix("CHAN").removeprefix("C")
            if not m.isdigit():
                raise ScpiError(-224, f"unknown source {source!r}")
            return int(m)
        bound = self.param("source")
        if bound not in (None, ""):
            return self._key(bound)
        return channel or 1

    @staticmethod
    def _key(word: Any) -> int:
        """A setting's source word as a source key (CHANnel2, CH2, C2 -> 2; FUNCtion3 -> -3)."""
        f = function_index(word)
        return -f if f is not None else channel_index(word)

    @staticmethod
    def _source_word(n: int) -> str:
        """A source key's short word: CHAN<n>, or FUNC<F> for a function."""
        return f"FUNC{-n}" if n < 0 else f"CHAN{n}"

    def record(self, n: int) -> tuple[Any, int]:
        """The record a measurement on source key ``n`` reads: channel n's acquisition, or math
        function -n's waveform over its operands (#1074) — a model without math functions refuses
        the source (-224), a function without an operator is -230."""
        if n >= 0:
            return self._acq().measure_record(n)
        block = self.ref("math")
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
    def _impedance(value: Any) -> float:
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
        acq = self._acq()
        if rec.points > MAX_FRAME:
            return self._deep_value(what, rec)
        v = np.asarray(rec.volts, dtype=float)
        gate = self.param("gate")
        if gate:
            lo, hi = (max(0, min(v.size, round(float(g) / 10 * v.size))) for g in gate)
            v = v[lo:hi]
        if v.size and str(self.param("clipped")) == "invalid" and rec.channel > 0:  # a function: no ADC
            rails = acq.adc_unit().rails(rec)
            if acq.adc_unit().param("bits") and (v.max() >= rails[0] or v.min() <= rails[1]):
                return math.nan
        if what in TIME_MEASUREMENTS:
            return self._time_value(what, v, rec)
        if self.param("thresholds"):
            gen = self.levels(v, rec, "general")
            if gen is None:
                return math.nan  # HISTONLY on a record whose histogram shows no levels
            return measure(
                what,
                v,
                rec.dt_s,
                min_cycles=int(self.param("min_cycles")),
                general=gen,
                rise_fall=self.levels(v, rec, "rfall"),
                edge_min_samples=int(self.param("edge_min_samples")),
                shoot=str(self.param("shoot")),
            )
        mid = None
        if v.size and what in ("FREQuency", "PERiod"):
            m = float(self.param("mid"))
            if str(self.param("levels")) == "absolute":
                mid = m
            elif m != 50.0:
                levels = top_base(v) if self.param("top_base") == "histogram" else None
                top, base = levels or (float(v.max()), float(v.min()))
                mid = base + m / 100 * (top - base)
        value = measure(
            what,
            v,
            rec.dt_s,
            top_base=str(self.param("top_base")),
            mid=mid,
            hysteresis=float(self.param("hysteresis")),
            min_cycles=int(self.param("min_cycles")),
            min_band=self._band_floor(rec),
            edge_min_samples=int(self.param("edge_min_samples")),
            shoot=str(self.param("shoot")),
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
        gate = self.param("gate")
        if gate:
            lo, hi = (max(0, min(rec.points, round(float(g) / 10 * rec.points))) for g in gate)
        acq = self._acq()
        if str(self.param("clipped")) == "invalid" and rec.channel > 0 and acq.adc_unit().param("bits"):
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
        assert self.owner is not None
        prefix = str(self.param("thresholds"))
        out: dict[str, Any] = {}
        for key in DEFAULT_THRESHOLDS:
            path = f"{prefix}.{group}{n}.{key}"
            if path in self.owner.specs:
                out[key] = self.owner.get_state(path)
        return out

    def levels(self, v: Any, rec: Any, group: str = "general") -> Levels | None:
        """The record's top, base and reference levels by its channel's threshold set (or the
        fixed 90/50/10 % without ``thresholds``)."""
        spec = self.threshold_spec(rec.channel, group) if self.param("thresholds") else {}
        return threshold_levels(
            np.asarray(v, dtype=float), spec, float(self.param("hysteresis")), self._band_floor(rec)
        )

    def _crossings_of(self, v: Any, rec: Any, position: str = "MIDDle") -> list[tuple[float, int]]:
        """The record's crossings of a reference level: the threshold set's with ``thresholds``,
        else the middle as before."""
        if not self.param("thresholds"):
            return mid_crossings(v, rec.dt_s, float(self.param("hysteresis")))
        lv = self.levels(v, rec, "general")
        if lv is None:
            return []
        return crossings(np.asarray(v, dtype=float), rec.dt_s, lv.at(position), lv.band)

    def _band_floor(self, rec: Any) -> float:
        """The crossing band's absolute floor: divisions of the record's scale (#745)."""
        return float(self.param("hysteresis_floor_divs")) * rec.scale

    def _time_value(self, what: str, v: np.typing.NDArray[np.float64], rec: Any) -> float:
        """XMAX/XMIN: the time of the first maximum / minimum, from the trigger; BRATe: the bit rate,
        1 / the narrowest pulse (positive or negative) at the middle level (#350)."""
        if v.size == 0:
            return math.nan
        if what in ("XMAX", "XMIN"):
            k = int(np.argmax(v) if what == "XMAX" else np.argmin(v))
            return rec.origin + k * rec.dt_s
        cross = mid_crossings(v, rec.dt_s, float(self.param("hysteresis")), self._band_floor(rec))
        widths = [u - t for (t, a), (u, b) in itertools.pairwise(cross) if b == -a and u > t]
        return 1.0 / min(widths) if widths else math.nan

    def _sources(self, raw: list[bytes] | None) -> tuple[list[str], list[int]]:
        """Raw parameters split into the arguments and the channels named (CHANnel<n>). An item may
        also be a setting's value (a number or a word, op args ``[$bookmark.time, $bookmark.source]``)."""
        words = [
            (
                V.parse_string(b) if isinstance(b, bytes) else repr(b) if isinstance(b, float) else str(b)
            ).strip()
            for b in raw or []
        ]
        channels = [self._channel(None, w) for w in words if self._is_source(w)]
        return [w for w in words if not self._is_source(w)], channels

    def _reply(self, value: float, used: int) -> OpResult:
        assert self.owner is not None
        if not math.isfinite(value) and self.param("invalid_value") != "UNDEF":
            return OpResult(str(self.param("invalid_value")).encode(), used)
        return OpResult(self.owner.format_number(value), used)

    @op("at")
    def op_at(
        self, what: str = "", source: list[bytes] | None = None, channel: int | None = None, **_: Any
    ) -> OpResult:
        """Measurements with arguments (#350), times from the trigger:

        * ``VTIMe? <time>[,<source>]`` — the voltage at that time (linear between samples);
        * ``TEDGe? <slope><occurrence>[,<source>]`` — the time of that edge at the middle level;
        * ``TVALue? <value>[,<slope><occurrence>][,<source>]`` — the time the record crosses
          ``value`` that way.
        """
        args, channels = self._sources(source)
        if any(a.upper() == "NONE" for a in args):  # a bookmark on no waveform has no value
            return self._reply(math.nan, 0)
        n = channels[0] if channels else self._channel(channel)
        if channels:
            self._follow(n, self._source_word(n))
        rec, used = self.record(n)
        dt, origin = rec.dt_s, rec.origin
        if what == "VTIMe":
            if not args:
                raise ScpiError(-109, "VTIMe? takes a time")
            x = (V.parse_float(args[0].encode(), "S") - origin) / dt
            size = rec.points
            if size == 0 or not 0 <= x <= size - 1:
                return self._reply(math.nan, used)
            k = min(int(x), size - 2) if size > 1 else 0
            frac = x - k
            pair = rec.span(k, k + 2)  # the two samples around the time: a deep record computes no more
            value = float(pair[0]) if size == 1 else float(pair[0] + (pair[1] - pair[0]) * frac)
            return self._reply(value, used)
        v = np.asarray(rec.volts, dtype=float)
        if what == "TEDGe":
            position = "MIDDle"
            if args and args[0].strip().upper().startswith(("UPP", "MIDD", "LOW")):
                position, args = args[0], args[1:]  # the Infiniium's <meas_thres_txt> first
            sign, occurrence = edge_spec(args[0] if args else None)
            cross = [t for t, d in self._crossings_of(v, rec, position) if d == sign]
        elif what == "TVALue":
            if not args:
                raise ScpiError(-109, "TVALue? takes a value")
            level = V.parse_float(args[0].encode(), "V")
            sign, occurrence = edge_spec(args[1] if len(args) > 1 else None)
            span = float(v.max() - v.min()) if v.size else 0.0
            cross = [
                t for t, d in crossings(v, dt, level, float(self.param("hysteresis")) * span) if d == sign
            ]
        else:
            raise ScpiError(-224, f"measurement {what!r} takes no arguments here")
        value = origin + cross[occurrence - 1] if len(cross) >= occurrence else math.nan
        return self._reply(value, used)

    @op("pair")
    def op_pair(
        self,
        what: str = "",
        source: list[bytes] | None = None,
        define: list[Any] | None = None,
        **_: Any,
    ) -> OpResult:
        """``DELay? [<source1>][,<source2>]``: the time from source 1's first rising edge to source 2's
        first rising edge at their middle levels; ``PHASe?``: that delay over source 1's period, in
        degrees folded into -180..+180 (#350), with a trailing RISing / FALLing taking that edge on both. Without sources,
        :MEASure:SOURce's two (``sources``). ``CROSsing?``: the voltage where the two records cross
        (nearest the centre). ``DELTatime?`` (the Infiniium's): from source 1's edge ``define`` names
        to source 2's — ``define`` = [start direction, number, position, stop direction, number,
        position] (RISing / FALLing / EITHer, from 1, UPPer / MIDDle / LOWer); one source: both on it."""
        assert self.owner is not None
        words, channels = self._sources(source)
        if what == "DELTatime" and len(channels) == 1:
            channels = channels * 2
        if len(channels) < 2:
            pair = [self._key(self.owner.get_state(p)) for p in (self.param("sources") or [])]
            channels = (channels + pair[len(channels) :])[:2]
        if len(channels) < 2:
            raise ScpiError(-109, f"{what}? needs two sources")
        direction = next((w.upper() for w in words if w.upper().startswith(("RIS", "FALL"))), "RISING")
        sign = -1 if direction.startswith("FALL") else 1
        hyst = float(self.param("hysteresis"))
        recs, used = [], 0
        for n in channels:
            rec, u = self.record(n)
            used += u
            recs.append(rec)
        if what == "CROSsing":
            a, b = (np.asarray(r.volts, dtype=float) for r in recs)
            return self._reply(crossing_voltage(a, b), used)
        if what == "DELTatime":
            d = list(define or ["RISing", 1, "MIDDle", "RISing", 1, "MIDDle"])
            times = []
            for rec, (dirn, number, position) in zip(recs, (d[0:3], d[3:6]), strict=True):
                want = str(dirn).upper()
                edges = [
                    t
                    for t, e in self._crossings_of(np.asarray(rec.volts, dtype=float), rec, str(position))
                    if want.startswith("EITH") or e == (-1 if want.startswith("FALL") else 1)
                ]
                k = int(number)
                times.append(rec.origin + edges[k - 1] if len(edges) >= k else math.nan)
            return self._reply(times[1] - times[0], used)
        times = []
        for rec in recs:
            v = np.asarray(rec.volts, dtype=float)
            edges = [t for t, e in self._crossings_of(v, rec) if e == sign]
            times.append(rec.origin + edges[0] if edges else math.nan)
        delay = times[1] - times[0]
        if what == "DELay":
            return self._reply(delay, used)
        first = np.asarray(recs[0].volts, dtype=float)
        period = measure("PERiod", first, recs[0].dt_s, hysteresis=hyst)
        if not (math.isfinite(period) and period > 0):
            return self._reply(math.nan, used)
        # folded into one period, -180..+180 degrees: the first edges may lie whole periods apart
        return self._reply((delay / period * 360.0 + 180.0) % 360.0 - 180.0, used)

    @op("auto_thresholds")
    def op_auto_thresholds(self, source: Any = None, channel: int | None = None, **_: Any) -> OpResult:
        """The Infiniium's :MEASure:THResholds:GENAUTO: the source's general hysteresis threshold set
        from its record — the level halfway between top and base, the range the counter's band
        (``hysteresis`` of top minus base, never below the band floor)."""
        assert self.owner is not None
        if not self.param("thresholds"):
            raise ScpiError(-300, f"{self.uid}: thresholds names no settings")
        n = self._channel(channel, source)
        if n < 0:
            raise ScpiError(-224, "the threshold sets are per channel")
        rec, used = self.record(n)
        v = np.asarray(rec.volts, dtype=float)
        lv = threshold_levels(
            v, {"method": "PERCent"}, float(self.param("hysteresis")), self._band_floor(rec)
        )
        if lv is None:
            return OpResult(duration_ns=used)
        prefix = str(self.param("thresholds"))
        self.owner.set_state(f"{prefix}.general{n}.hyst_level", lv.middle)
        self.owner.set_state(f"{prefix}.general{n}.hyst_range", lv.band)
        return OpResult(duration_ns=used)

    @op("with_args")
    def op_with_args(self, what: str = "", source: list[bytes] | None = None, **_: Any) -> OpResult:
        """Measurements that take a number besides the source (the Infiniium's): ``PTOP? <source>,<%>``
        / ``PBASe?`` — the mean of the first positive / negative pulse within the centred window of
        that percent of its width; ``BWIDth? <source>,<idle>``, ``BPERiod?``, ``BINTerval?`` — the
        first complete burst's width, the start-to-start and the end-to-start time of the first two
        (bursts are runs of middle crossings closer than the idle time)."""
        words, channels = self._sources(source)
        n = channels[0] if channels else self._channel(None)
        if not words:
            raise ScpiError(-109, f"{what}? takes a number after the source")
        number = V.parse_float(words[0].encode(), "S" if what.startswith("B") else None)
        rec, used = self.record(n)
        v = np.asarray(rec.volts, dtype=float)
        lv = self.levels(v, rec, "general")
        if lv is None or v.size == 0:
            return self._reply(math.nan, used)
        if what in ("PTOP", "PBASe"):
            return self._reply(pulse_window_mean(v, rec.dt_s, lv, number, what == "PTOP"), used)
        if what in ("BWIDth", "BPERiod", "BINTerval"):
            if number <= 0:
                raise ScpiError(-222, "the idle time must be positive")
            return self._reply(burst_measure(what, v, rec.dt_s, lv, number), used)
        raise ScpiError(-224, f"measurement {what!r} takes no number here")

    @op("auto_trigger_levels")
    def op_auto_trigger_levels(self, source_only: bool = False, **_: Any) -> OpResult:
        """``:TRIGger:LEVel:ASETup``: every displayed channel's trigger level to its waveform's 50 %
        (halfway between top and base; 0 V with AC coupling) — the levels of ``trigger_levels``,
        channel -> setting (#350). ``source_only`` (the Infiniium's ``:TRIGger:LEVel:FIFTy``): the
        level of the channel the trigger takes its source from now, whatever the trigger type."""
        assert self.owner is not None
        acq = self._acq()
        only = None
        if source_only:
            trig = getattr(acq, "ref", lambda _n: None)("trigger")
            only = trigger_channel(trig.param("source")) if trig is not None else None
            if only is None:
                return OpResult()  # an external or line source: no waveform to take 50 % of
        for key, path in (self.param("trigger_levels") or {}).items():
            n = int(key)
            if (n != only) if only is not None else (n not in acq.displayed()):
                continue  # FIFTy: the trigger source's level, shown on screen or not
            coupling = str(self.owner.state.get(f"ch{n}.coupling", "DC")).upper()
            if coupling.startswith("AC"):
                level = 0.0
            else:
                rec, _ = acq.measure_record(n)
                v = np.asarray(rec.volts, dtype=float)
                levels = top_base(v) if v.size else None
                top, base = levels if levels else ((float(v.max()), float(v.min())) if v.size else (0.0, 0.0))
                level = (top + base) / 2
            self.owner.set_state(str(path), level)
        return OpResult()

    def _follow(self, n: int, source: Any) -> None:
        """A measurement that names its source makes it the default one (the bound ``source``)."""
        path = self.bound_path("source")
        if not source or path is None or self.owner is None:
            return
        options = self.owner.specs[path].options or []
        match = next(
            (o for o in options if (function_index(o) == -n if n < 0 else trigger_channel(o) == n)), None
        )
        if match is not None:
            self.owner.set_state(path, match)

    def _keep(self, name: str, n: int, value: float) -> None:
        if math.isfinite(value):
            self.history.setdefault((name, n), []).append(value)

    @op("measure")
    def measure(
        self,
        what: str = "",
        source: Any = None,
        channel: int | None = None,
        query: bool = True,
        interval: str | None = None,
        direction: str | None = None,
        coupling: str | None = None,
        units: str | None = None,
        impedance: Any = None,
        **_: Any,
    ) -> OpResult:
        """``:MEASure:VPP? [CHANnel1]``: the engine measurement ``what`` (a constant argument of the
        command) in the definition's number format; what cannot be measured is NaN's sentinel.
        Without '?' (``:MEASure:VPP CHANnel1``) the measurement goes on the screen's list (#373).
        For the level measurements (``LEVEL_MEASUREMENTS``) an ``interval`` CYCLe and a ``direction``
        FALLing parameter select their variants (``AREA:CYCLe``, ``VOVershoot:FALLing``); for
        VAVerage and VRMS the CYCLe interval is the first period on screen, a ``coupling`` AC takes
        the DC component out (``VRMS:CYCLe:AC``), ``units`` DBM gives the power into ``impedance``
        ohms (AUTO or none: 50 Ω, the guide's reference for single-ended channels and functions)."""
        assert self.owner is not None
        if interval and str(interval).upper().startswith("CYCL") and f"{what}:CYCLe" in LEVEL_MEASUREMENTS:
            what += ":CYCLe"
        if coupling and str(coupling).upper().startswith("AC") and f"{what}:AC" in LEVEL_MEASUREMENTS:
            what += ":AC"
        if what in LEVEL_MEASUREMENTS and direction and str(direction).upper().startswith("FALL"):
            what += ":FALLing"
        if units and str(units).upper().startswith("DBM"):
            what += f":DBM{self._impedance(impedance):g}"
        n = self._channel(channel, source)
        self._follow(n, source)
        if not query:
            self.install(what, n)
            return OpResult()
        value, used = self.value(what, n)
        self._keep(what, n, value)
        if not math.isfinite(value) and self.param("invalid_value") != "UNDEF":
            return OpResult(str(self.param("invalid_value")).encode(), used)
        return OpResult(self.owner.format_number(value), used)  # NaN: the SCPI sentinel 9.91E+37

    @op("named", fields=("kind", "unit", "status"))
    def named(self, channel: int | None = None, kind: str | None = None, **_: Any) -> OpResult:
        """A measurement by the model's name (``names``): the value, or ``invalid_value`` when it
        cannot be measured; the reply template also gets {kind}, {unit} and {status}, e.g.
        '{header} {kind},{value} {unit},{status}'."""
        assert self.owner is not None
        if kind is None:
            raise ScpiError(-109)
        table = {str(k).upper(): v for k, v in (self.param("names") or {}).items()}
        name = kind.strip().upper()
        if name not in table:
            raise ScpiError(-224, f"measurement {kind} not implemented in this definition")
        engine_name, unit = table[name]
        n = self._channel(channel)
        value, used = self.value(str(engine_name), n)
        self._keep(name, n, value)
        good, bad = self.param("status")
        ok = math.isfinite(value)
        text = self.owner.format_number(value) if ok else str(self.param("invalid_value")).encode()
        return OpResult(text, used, {"kind": name, "unit": str(unit), "status": str(good if ok else bad)})

    @op("statistics")
    def statistics(
        self, kind: str | None = None, item: str = "mean", channel: int | None = None, **_: Any
    ) -> OpResult:
        """The statistics of the values taken so far of a measurement: ``count``, ``mean``,
        ``sdev``, ``min`` or ``max`` (``item``, a constant argument or a parameter)."""
        assert self.owner is not None
        if kind is None:
            raise ScpiError(-109)
        table = {str(k).upper(): v for k, v in (self.param("names") or {}).items()}
        name = kind.strip().upper()
        n = self._channel(channel)
        values = np.array(
            self.history.get((name, n)) or self.history.get((str(table.get(name, [name])[0]), n)) or []
        )
        if item == "count":
            return OpResult(str(values.size).encode())
        if not values.size:
            return OpResult(str(self.param("invalid_value")).encode())
        stat = {"mean": values.mean, "sdev": values.std, "min": values.min, "max": values.max}.get(item)
        if stat is None:
            raise ScpiError(-224, f"no statistic {item!r}")
        return OpResult(self.owner.format_number(float(stat())))

    @op("clear_statistics")
    def clear_statistics(self, **_: Any) -> OpResult:
        self.history = {}
        return OpResult()

    # ---- the markers (cursors, #1074) ------------------------------------------------------------

    #: What a marker mode word stands for, by its first letters (the settings hold SCPI words).
    MODES = {
        "OFF": "off",
        "MAN": "manual",
        "XON": "x_only",
        "YON": "y_only",
        "WAV": "waveform",
        "MEAS": "measurement",
    }

    def _marker_mode(self) -> str:
        """The marker mode as one of ``MODES``' values, ``off`` when the model has none."""
        word = str(self.param("marker_mode") or "").upper()
        for key, mode in self.MODES.items():
            if word.startswith(key):
                return mode
        return "off"

    def _marker_record(self, which: int) -> tuple[Any, int]:
        """The record the A (``which`` 1) or B (2) markers read: their source's, a channel's or a
        math function's (#1074). A numberless ``FUNCtion`` — or its alias ``MATH`` (2000 X-series
        Programmer's Guide 02.66 p. 361) — names the one function a single-function layout has."""
        return self._source_record(str(self.param(f"marker_source{which}") or ""))

    def _source_record(self, word: str) -> tuple[Any, int]:
        """The record a marker's source word names (see ``_marker_record``); empty: channel 1."""
        f = function_index(word) if word else None
        if f is None and word.strip().upper() in ("FUNCTION", "FUNC", "F", "MATH"):
            f = 1
        if f is not None:
            block = self.ref("math")
            if block is None or not isinstance(block, MathSource):
                raise ScpiError(-224, f"{word}: this model has no math functions to mark")
            return block.read_function(f)  # type: ignore[return-value]
        n = self._channel(None, word.encode() if word else None)
        return self._acq().measure_record(n)  # type: ignore[return-value]

    def _value_at(self, rec: Any, x: float) -> float:
        """The record's value at x (its grid's unit — seconds, or Hz on a transform), the sample
        nearest x: the markers of the real instrument read the point the screen draws."""
        if rec.points < 1:
            return math.nan
        k = (x - rec.origin) / rec.dt_s
        i = int(min(max(round(k), 0), rec.points - 1))
        return float(rec.span(i, i + 1)[0])

    def _marker_y(self, which: int) -> tuple[float, int]:
        """The A (1) or B (2) marker's y: in the manual modes its setting, in the waveform mode
        the value its source's record has at the marker's x; the measurement mode tracks the
        measurement ``marker_measurement`` names."""
        mode = self._marker_mode()
        if mode in ("off", "x_only"):
            return math.nan, 0
        if mode == "measurement":
            n = self.param("marker_measurement")
            if n in (None, ""):
                # no tracked-measurement command: the cursors track the most recent measurement
                # on screen (the 2000 X-series semantics, Programmer's Guide 02.66 p. 359)
                if not self.installed:
                    return math.nan, 0
                what, channel = self.installed[-1]
                return self.value(what, channel)
            which_meas = int(n)
            if 1 <= which_meas <= len(self.installed):
                what, channel = self.installed[which_meas - 1]
                return self.value(what, channel)
            return math.nan, 0
        if mode == "waveform":
            rec, used = self._marker_record(which)
            x = self.param(f"marker_x{which}")
            return self._value_at(rec, float(x) if x is not None else 0.0), used
        y = self.param(f"marker_y{which}")  # manual and y_only: the marker is its setting
        return (float(y) if y is not None else math.nan), 0

    def _marker_word(self, mode: str) -> str:
        """The SCPI word a mode's setting carries, as the definition's options spell it."""
        assert self.owner is not None
        path = self.bound_path("marker_mode")
        if path is None:
            return mode
        for option in self.owner.specs[path].options or []:
            if option.upper().startswith(mode[:3]):
                return str(option)
        return mode

    @op("cursor")
    def cursor(self, which: str = "STARt", query: bool = True, **_: Any) -> OpResult:
        """``:MARKer:CURSor? {DELTa | STARt | STOP}``: the tracked positions as ``<x>,<y>``.
        The query also puts the markers in the waveform-tracking mode, whatever they were (the
        manual's caution: it changes the mode)."""
        assert self.owner is not None
        word = str(which).strip().upper()
        if not word.startswith(("DEL", "STA", "STO")):
            raise ScpiError(-224, f"{which}: DELTa, STARt or STOP")
        mode_path = self.bound_path("marker_mode")
        if self._marker_mode() != "waveform" and mode_path is not None:
            self.owner.set_state(mode_path, self._marker_word("WAV"), source="scpi", internal=True)
        if word.startswith("DEL"):
            x1, x2 = (float(self.param(k) or 0.0) for k in ("marker_x1", "marker_x2"))
            ya, yb, used = self._marker_ys()
            return OpResult(b",".join(self.owner.format_number(v) for v in (x2 - x1, yb - ya)), used)
        n = 1 if word.startswith("STA") else 2
        y, used = self._marker_y(n)
        x = float(self.param(f"marker_x{n}") or 0.0)
        return OpResult(b",".join(self.owner.format_number(v) for v in (x, y)), used)

    @op("x_delta")
    def x_delta(self, query: bool = True, **_: Any) -> OpResult:
        """``:MARKer:XDELta?``: Bx − Ax, in the X cursors' units — the value at each cursor in
        that unit, minus (the 2000 X-series manual's Xdelta; #1155)."""
        assert self.owner is not None
        x1, x2 = (float(self.param(k) or 0.0) for k in ("marker_x1", "marker_x2"))
        shown = self._marker_diff("x", x1, x2)
        if not math.isfinite(shown):
            return OpResult(str(self.param("invalid_value")).encode())
        return OpResult(self.owner.format_number(shown))

    @op("y_delta")
    def y_delta(self, query: bool = True, **_: Any) -> OpResult:
        """``:MARKer:YDELta?``: By − Ay — the markers' settings, or the values at their x in the
        tracking modes."""
        assert self.owner is not None
        ya, yb, used = self._marker_ys()
        value = self._marker_diff("y", ya, yb)
        if not math.isfinite(value) and self.param("invalid_value") != "UNDEF":
            return OpResult(str(self.param("invalid_value")).encode(), used)
        return OpResult(self.owner.format_number(value), used)

    def _marker_ys(self) -> tuple[float, float, int]:
        """Both markers' y and the virtual time reading them took. In the MEASurement mode both
        sit on the one result of the measurement they track: it is taken once, so their
        difference is that result's, not two acquisitions' noise apart."""
        if self._marker_mode() == "measurement":
            y, used = self._marker_y(1)
            return y, y, used
        ya, ua = self._marker_y(1)
        yb, ub = self._marker_y(2)
        return ya, yb, ua + ub

    # ---- the markers' units (2000 X-series :MARKer:XUNits/:YUNits, #1155) ------------------------

    XUNITS = {"SEC": "s", "HERT": "hz", "DEGR": "deg", "PERC": "pct"}
    YUNITS = {"BASE": "base", "PERC": "pct"}

    def _marker_units(self, axis: str) -> str:
        """The X or Y cursors' unit as this block spells it, from the setting's word."""
        word = str(self.param(f"marker_{axis}_units") or "").upper()
        table = self.XUNITS if axis == "x" else self.YUNITS
        for key, unit in table.items():
            if word.startswith(key):
                return unit
        return table[next(iter(table))]  # the first: seconds, or base units

    def _marker_scaled(self, axis: str, value: float) -> float:
        """A position in the axis's own units expressed in its cursors' unit: the degrees or
        percent from the unit's 0 anchor (:MARKer:XUNits:USE, :MARKer:YUNits:USE set the
        anchors); without anchors the value passes through (the unit has nothing to scale
        against). A difference between two positions scales the same way without the anchor's
        offset — see ``_marker_diff``."""
        if self._marker_units(axis) not in ("deg", "pct"):
            return value
        a1 = self.param(f"marker_{axis}a1")
        a2 = self.param(f"marker_{axis}a2")
        if a1 is None or a2 is None or float(a2) == float(a1):
            return value
        span = float(a2) - float(a1)
        full = 360.0 if self._marker_units(axis) == "deg" else 100.0
        return full * (value - float(a1)) / span

    def _marker_diff(self, axis: str, one: float, two: float) -> float:
        """The cursors' difference in their unit: the value at each cursor in the unit, minus —
        the 2000 X-series manual's Xdelta/Ydelta (degrees and percent scale the difference)."""
        unit = self._marker_units(axis)
        if unit == "s" or unit == "base":
            return two - one
        if unit == "hz":
            if one <= 0.0 or two <= 0.0:
                return math.nan
            return 1.0 / two - 1.0 / one
        a1 = self.param(f"marker_{axis}a1")
        a2 = self.param(f"marker_{axis}a2")
        if a1 is None or a2 is None or float(a2) == float(a1):
            return two - one
        span = float(a2) - float(a1)
        full = 360.0 if unit == "deg" else 100.0
        return full * (two - one) / span

    @op("x_position", fields=("which",))
    def op_x_position(self, which: str = "X1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        """``:MARKer:X1Position`` / ``:MARKer:X2Position``: the cursor's position. A write puts
        the cursors in the MANual mode unless they track (the manual); the input and the reply
        are in the X cursors' units (:MARKer:XUNits)."""
        return self._position(which, value, query, "x")

    @op("y_position", fields=("which",))
    def op_y_position(self, which: str = "Y1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        """``:MARKer:Y1Position`` / ``:MARKer:Y2Position``: the cursor's position. A write is
        refused while the cursors track (the manual: Y positions cannot be set in the WAVeform
        mode) and otherwise puts them in the MANual mode."""
        return self._position(which, value, query, "y")

    @op("tracked_y", fields=("which",))
    def op_tracked_y(self, which: str = "Y1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        """The Infiniium's ``:MARKer:Y1Position`` / ``:MARKer:Y2Position`` (and VSTArt/VSTOp): the
        query answers "the level at the marker position" — where the marker is: in the WAVeform
        mode the source's value at the marker's x, in the MEASurement mode the level of the
        measurement it tracks (as ``:MARKer:YDELta?`` reads it), otherwise the setting; nothing to
        track answers the invalid value. A write stores the setting and leaves the mode alone (the
        guide names no mode change, unlike the 2000 X-series' ``y_position``)."""
        assert self.owner is not None
        n = 1 if str(which).strip().upper().endswith("1") else 2
        param = f"marker_y{n}"
        if query:
            if self._marker_mode() in ("waveform", "measurement"):
                shown, used = self._marker_y(n)
                if not math.isfinite(shown):
                    return OpResult(str(self.param("invalid_value")).encode(), used)
                return OpResult(self.owner.format_number(shown), used)
            stored = self.param(param)
            return OpResult(self.owner.format_number(float(stored) if stored is not None else 0.0))
        path = self.bound_path(param)
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the {param} setting is not bound")
        self.owner.set_state(path, self._marker_number("y", value), source="scpi", internal=True)
        return OpResult()

    def _position(self, which: str, value: Any, query: bool, axis: str) -> OpResult:
        """One X or Y cursor's read or write, in the cursors' unit, with the mode moves the
        manual gives a position write (MANual unless tracking; a tracking Y is refused)."""
        assert self.owner is not None
        n = 1 if str(which).strip().upper().endswith("1") else 2
        param = f"marker_{axis}{n}"
        mode_path = self.bound_path("marker_mode")
        mode = self._marker_mode()
        if query:
            if mode == "off":
                raise ScpiError(-222, "the cursors are off: their positions are not defined")
            used = 0
            if axis == "y" and mode in ("waveform", "measurement"):
                shown, used = self._marker_y(n)
                if not math.isfinite(shown) and self.param("invalid_value") != "UNDEF":
                    return OpResult(str(self.param("invalid_value")).encode(), used)
            else:
                shown = float(self.param(param) if self.param(param) is not None else 0.0)
            unit = self._marker_units(axis)
            if unit == "hz":
                if shown <= 0.0:
                    return OpResult(str(self.param("invalid_value")).encode(), used)
                shown = 1.0 / shown
            elif unit in ("deg", "pct"):
                shown = self._marker_scaled(axis, shown)
            return OpResult(self.owner.format_number(shown), used)
        if axis == "y" and mode == "waveform":
            raise ScpiError(-222, "the cursors track the waveform: Y positions cannot be set")
        if mode != "waveform" and mode_path is not None:
            self.owner.set_state(mode_path, self._marker_word("MAN"), source="scpi", internal=True)
        path = self.bound_path(param)
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the {param} setting is not bound")
        number = self._marker_number(axis, value)
        self.owner.set_state(path, number, source="scpi", internal=True)
        return OpResult()

    def _marker_number(self, axis: str, value: Any) -> float:
        """A cursor position write in the cursors' unit, stored in the axis's own units (seconds
        or volts): Hz is its inverse, degrees and percent ride the anchors (:UNits:USE)."""
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if value is None:
            raise ScpiError(-109)
        text = value.decode("ascii", errors="replace") if isinstance(value, bytes) else str(value)
        v = float(V.parse_number(text))
        unit = self._marker_units(axis)
        if unit == "s" or unit == "base":
            return v
        if unit == "hz":
            if v <= 0.0:
                raise ScpiError(-222, f"{v:g} Hz: a cursor's frequency is positive")
            return 1.0 / v
        a1 = self.param(f"marker_{axis}a1")
        a2 = self.param(f"marker_{axis}a2")
        if a1 is None or a2 is None or float(a2) == float(a1):
            return v  # nothing to scale against: the value passes through (the model's convention)
        full = 360.0 if unit == "deg" else 100.0
        return float(a1) + (float(a2) - float(a1)) * v / full

    @op("units_use", fields=("axis",))
    def op_units_use(self, axis: str = "X", **_: Any) -> OpResult:
        """``:MARKer:XUNits:USE`` / ``:MARKer:YUNits:USE``: the current cursor locations become
        the unit's anchors — 0 and 360 degrees, or 0 and 100 percent (no query)."""
        assert self.owner is not None
        axis = axis.strip().lower()
        if axis not in ("x", "y"):
            raise ScpiError(-224, f"{axis}: X or Y")
        one = float(self.param(f"marker_{axis}1") or 0.0)
        two = float(self.param(f"marker_{axis}2") or 0.0)
        for name, value in ((f"marker_{axis}a1", one), (f"marker_{axis}a2", two)):
            path = self.bound_path(name)
            if path is None:
                raise ScpiError(-300, f"{self.uid}: the {name} setting is not bound")
            self.owner.set_state(path, value, source="scpi", internal=True)
        return OpResult()

    @op("marker_source", fields=("which",))
    def op_marker_source(
        self, which: str = "X1Y1", value: Any = None, query: bool = False, **_: Any
    ) -> OpResult:
        """``:MARKer:X1Y1source`` / ``:MARKer:X2Y2source``: the cursors' source. ``MATH`` is an
        alias stored as FUNCtion, so the query answers FUNC for either (the manual's note,
        Programmer's Guide 02.66 p. 361); the reply is the source's short form (CHAN1, FUNC)."""
        assert self.owner is not None
        param = f"marker_source{2 if str(which).strip().upper().endswith('2') else 1}"
        if query or value is None:
            return OpResult(short_source(self.param(param) or "NONE").encode())
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if value is None:
            raise ScpiError(-109)
        word = V.parse_string(value if isinstance(value, bytes) else str(value).encode())
        if word.strip().upper() == "MATH":
            word = "FUNCtion"
        path = self.bound_path(param)
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the {param} setting is not bound")
        self.owner.set_state(path, word, source="scpi", internal=True)
        return OpResult()

    @op("marker_measurements")
    def marker_measurements(
        self, measurement: Any = None, state: Any = None, query: bool = False, **_: Any
    ) -> OpResult:
        """``:MARKer:MEASurement:MEASurement``: which measurements the MEASurement mode tracks.
        Without ``state`` the list becomes that one measurement; with it, that measurement's
        tracking alone turns on or off. The query answers the tracked measurement, or empty."""
        if isinstance(measurement, list):  # a raw parameter takes all the remaining ones
            measurement = measurement[0] if measurement else None
        if measurement is None:
            if not query:
                raise ScpiError(-109)
            tracked = sorted(m["measurement"] for m in self.k_markers.values() if m.get("tracking"))
            return OpResult((",".join(f"MEAS{n}" for n in tracked)).encode()) if tracked else OpResult()
        word = V.parse_string(measurement if isinstance(measurement, bytes) else str(measurement).encode())
        digits = word.upper().removeprefix("MEAS").removeprefix("UREMENT").removeprefix("UREMENT")
        # MEASurement<N> rides its own short spellings (MEAS1, MEASurement1); a bare number too
        if not (digits.startswith("1") or digits.isdigit()):
            digits = digits.lstrip("0")
        if not digits.isdigit():
            raise ScpiError(-224, f"{word}: not a measurement number")
        n = int(digits)
        slot = next((k for k, m in self.k_markers.items() if m.get("measurement") == n), None)
        if state:  # an omitted parameter arrives as an empty list, not None
            on = V.flag(state)
            if slot is None and on:
                slot = max(self.k_markers, default=0) + 1
            if slot is not None:
                if on:
                    self.k_markers[slot] = {"tracking": True, "measurement": n}
                else:
                    self.k_markers.pop(slot, None)
            return OpResult()
        self.k_markers = {k: m for k, m in self.k_markers.items() if not m.get("tracking")}
        self.k_markers[max(self.k_markers, default=0) + 1] = {"tracking": True, "measurement": n}
        meas_path = self.bound_path("marker_measurement")
        if meas_path is not None:
            assert self.owner is not None
            self.owner.set_state(meas_path, n, source="scpi", internal=True)
        return OpResult()

    # ---- the numbered markers (:MARKer<K>, #1074) --------------------------------------------------

    def _k(self, suffixes: dict[str, int] | None) -> int:
        """The ``<K>`` a numbered-marker command carried, checked against the model's count."""
        most = int(self.param("markers") or 0)
        k = int(next(iter(suffixes.values()))) if suffixes else 1
        if most <= 0 or not 1 <= k <= most:
            raise ScpiError(-114, f"marker {k} is not 1..{most}")
        return k

    def _k_marker(self, k: int) -> dict[str, Any]:
        """Marker k's state, its defaults in: XMANual type, channel 1, off."""
        return self.k_markers.setdefault(
            k, {"type": "XMANual", "source": "CHANnel1", "enable": False, "x": 0.0, "y": 0.0}
        )

    @op("k_field", fields=("field",))
    def k_field(
        self,
        field: str = "type",
        value: Any = None,
        query: bool = False,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """One field of a numbered marker — its type, source, enable, name, color, colour mode, x
        or y position — as ``:MARKer<K>:<field>`` reads and writes it. A type change needs the
        marker off (the manual: -221); what the marker's type has no place for answers
        ``invalid_value``; a TRACk or RF marker's y is its source's value at its x. The query
        answers the short forms the manual's Returned Format names (XMAN, TRAC, CHAN1, CUST)."""
        assert self.owner is not None
        k = self._k(suffixes)
        marker = self._k_marker(k)
        key = {
            "type": "type",
            "source": "source",
            "enable": "enable",
            "name": "name",
            "color": "color",
            "cmode": "cmode",
            "x": "x",
            "y": "y",
        }[field]
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if query or value is None:
            if key == "x" and marker["type"] == "YMANual":
                return OpResult(str(self.param("invalid_value")).encode())
            if key == "y" and marker["type"] == "XMANual":
                return OpResult(str(self.param("invalid_value")).encode())
            if key == "y" and marker["type"] in ("TRACk", "RF"):
                # a tracking marker marks its source's value at its x (the guide's TRACk / RF)
                rec, used = self._source_record(str(marker.get("source") or ""))
                y = self._value_at(rec, float(marker.get("x") or 0.0))
                if not math.isfinite(y):
                    return OpResult(str(self.param("invalid_value")).encode(), used)
                return OpResult(self.owner.format_number(y), used)
            current = marker.get(key)
            if key == "enable":
                return OpResult(V.format_int(int(bool(current))))
            if isinstance(current, (int, float)) and key in ("x", "y"):
                return OpResult(self.owner.format_number(float(current)))
            if current in (None, ""):
                return OpResult(b'""')
            if key == "type":
                return OpResult(
                    {"XMANual": "XMAN", "YMANual": "YMAN", "TRACk": "TRAC", "RF": "RF"}[current].encode()
                )
            if key == "source":
                return OpResult(short_source(current).encode())
            if key == "cmode":
                return OpResult(b"CUST" if current == "CUSTom" else b"SOUR")
            if key in ("name", "color"):  # a quoted string, as the manual's Returned Format has it
                return OpResult(f'"{current}"'.encode())
            return OpResult(str(current).encode())
        raw = value if isinstance(value, bytes) else str(value).encode()
        if key == "type":
            word = V.parse_string(raw).upper()
            if word not in ("XMANUAL", "YMANUAL", "TRACK", "RF"):
                raise ScpiError(-224, f"{value}: XMANual, YMANual, TRACk or RF")
            if marker["enable"]:
                raise ScpiError(-221, f"marker {k} is on: turn it off before changing its type")
            marker["type"] = {"XMANUAL": "XMANual", "YMANUAL": "YMANual", "TRACK": "TRACk", "RF": "RF"}[word]
            return OpResult()
        if key == "enable":
            marker["enable"] = V.flag(raw)
            return OpResult()
        if key == "cmode":
            word = V.parse_string(raw).upper()
            if word not in ("CUSTOM", "SOURCE"):
                raise ScpiError(-224, f"{value}: CUSTom or SOURce")
            marker["cmode"] = "CUSTom" if word == "CUSTOM" else "SOURce"
            return OpResult()
        if key in ("source", "name", "color"):
            marker[key] = V.parse_string(raw)
            return OpResult()
        if key == "x" and marker["type"] == "YMANual":
            raise ScpiError(-221, "a Y-only marker has no x position")
        if key == "y" and marker["type"] in ("XMANual", "TRACk", "RF"):
            raise ScpiError(-221, "an X-only or tracking marker has no y position")
        marker[key] = parse_number(raw.decode("ascii", errors="replace"))
        return OpResult()

    @op("marker_delta")
    def marker_delta(
        self,
        other: Any = None,
        state: Any = None,
        query: bool = True,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """``:MARKer:DELTa`` and ``:MARKer<K>:DELTa``, which share a header: without a ``MARKer<L>``
        word the command is the screen's delta-markers switch (``{ON | OFF}``, the query answers
        0/1); with one, it is marker K's delta-to relationship with marker L — the query answers
        ``<state>,<Δx>,<1/Δx>,<Δy>,<Δy/Δx>`` (``invalid_value`` where not applicable)."""
        assert self.owner is not None
        if isinstance(other, list):  # a raw parameter takes all the remaining ones: the ON/OFF
            extra = other[1:]  # state of a :MARKer<K>:DELTa MARKer<L>,{ON | OFF} rides here
            other = other[0] if other else None
            if not state and extra:  # an omitted parameter arrives as an empty list
                state = extra[0]
        if isinstance(state, list):
            state = state[0] if state else None
        raw = other if isinstance(other, bytes) else (str(other).encode() if other is not None else b"")
        word = V.parse_string(raw).upper() if raw else ""
        if word.startswith("MARKER") and word.removeprefix("MARKER").isdigit():
            k = self._k(suffixes)
            other_k = int(word.removeprefix("MARKER"))
            if not 1 <= other_k <= int(self.param("markers") or 0) or other_k == k:
                raise ScpiError(-224, f"{other!r}: not another marker of this model")
            first, second = self._k_marker(k), self._k_marker(other_k)
            if not query:
                word = (
                    ""
                    if state is None
                    else V.parse_string(state if isinstance(state, bytes) else str(state).encode())
                )
                first["delta_to"] = other_k if state is None or V.flag(word) else None
                return OpResult()
            on = first.get("delta_to") == other_k
            dx = float(second["x"]) - float(first["x"])
            invalid = str(self.param("invalid_value")).encode()
            parts = [V.format_int(int(on)), self.owner.format_number(dx)]
            parts.append(self.owner.format_number(1.0 / dx) if dx else invalid)
            if first["type"] == second["type"] == "YMANual":
                dy = float(second["y"]) - float(first["y"])
                parts.append(self.owner.format_number(dy))
                parts.append(self.owner.format_number(dy / dx) if dx else invalid)
            else:
                parts += [invalid, invalid]
            return OpResult(b",".join(parts))
        if not query:  # :MARKer:DELTa {ON | OFF}: the screen's delta-markers check box
            path = self.bound_path("marker_delta")
            if path is None:
                raise ScpiError(-113, "this model has no delta-marker display setting")
            self.owner.set_state(path, V.flag(raw), source="scpi", internal=True)
            return OpResult()
        path = self.bound_path("marker_delta")
        if path is None:
            raise ScpiError(-113, "this model has no delta-marker display setting")
        return OpResult(V.format_int(int(V.flag(self.owner.get_state(path)))))
