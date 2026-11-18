"""Oscilloscope components: the channel chain (probe, vertical and front end, ADC, decimator)
and the timebase. The trigger, the acquisition and the measurements live beside it
(``scope_trigger``, ``scope_acquisition``, ``scope_measurements``, #1386)."""

from __future__ import annotations

import itertools
import math
import re
from typing import Any

import numpy as np

from ...engine.component import (
    Component,
    OpResult,
    PortSpec,
    component,
    op,
)
from ...engine.signal import Signal, bessel4, brickwall, butterworth4, gaussian_lp, highpass1, lowpass1
from ...engine.stream import (
    FrameRequest,
    SampleFrame,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from ...units import parse_number
from .calstore import CalibrationStore
from .scope_record import (
    AdcView,
    DecimatorView,
    converter_half,
    quantize,
)

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
    choices = {
        "response": ("first_order", "brickwall", "gaussian", "bessel4", "butterworth4"),
        "analog_response": ("first_order", "brickwall", "gaussian", "bessel4", "butterworth4"),
    }
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
        # the analog front end's own shape when ``response`` is a DSP-corrected one (#1423): the
        # trigger taps the analog path, before the converter, so it sees this one; None: the same
        "analog_response": None,
        "scale": "$ch.scale",  # V/div as displayed
        "offset": "$ch.offset",  # V as displayed
        "invert": False,  # the waveform reflects about 0 V (:CHANnel<N>:INVert, #1067)
        "probe_ratio": 1.0,  # the probe setting the displayed scale and offset include
        "scale_range": None,  # [min, max] V/div at the input connector (None: any); may map a setting
        "offset_ranges": None,  # [[up to V/div, +-limit V], ...] at the input connector (None: any)
        "out_of_range": "error",  # error: refuse with -222 | clamp: take the nearest legal value
        "noise_table": None,  # [[V/div, noise V rms], ...] at the input, per division (None: quiet)
        "noise_divs": 0.0,  # without a table: input noise rms as a fraction of a division (scales with V/div)
        # the bandwidth the noise table was measured at (Hz): a different ``bandwidth`` scales the
        # table by sqrt(bandwidth / noise_bandwidth), white noise (#1427); None: the table as written
        "noise_bandwidth": None,
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
        "noise_bandwidth": "Hz",
        "bw_limit_hz": "Hz",
        "ac_corner": "Hz",
        "scale": "V",
        "offset": "V",
        "offset_error": "V",
    }

    def output(self, port: str) -> Signal:
        sig = self._before_bandwidth()
        if sig is None:
            return Signal.zero()
        return self._shaped(sig, self.choice("response") or "first_order")

    def _before_bandwidth(self) -> Signal | None:
        """The channel after the coupling and this unit's errors, before the bandwidth: None when
        the input is grounded."""
        sig = self.input("in")
        coupling = self.param("coupling")
        if coupling == "GND":
            return None
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
        return sig

    def analog_output(self) -> Signal:
        """The channel as the analog path carries it (#1423): what :meth:`output` gives, shaped by
        ``analog_response`` instead of ``response`` — the front end's own roll-off, without the
        DSP flatness correction a scope applies after its converter. The trigger taps this."""
        shape = self.choice("analog_response")
        if not shape:
            return self.output("out")
        sig = self._before_bandwidth()
        return Signal.zero() if sig is None else self._shaped(sig, shape)

    def record_band(self) -> tuple[str, float]:
        """The shape and the cutoff (Hz) of the response the record gets: ``response`` at the
        bandwidth or the bandwidth limit, whichever is lower (inf: none)."""
        return self.choice("response") or "first_order", min(self._limit(), float(self.param("bandwidth")))

    def _shaped(self, sig: Signal, shape: str) -> Signal:
        fc = min(self._limit(), float(self.param("bandwidth")))
        if not math.isfinite(fc):
            return sig
        return sig.transfer(RESPONSES[shape](fc))

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
        the sensitive divisions first, D46), times :meth:`noise_bandwidth_factor`. No table:
        ``noise_divs`` of the division, else none."""
        rows = sorted((float(r[0]), float(r[1])) for r in self.param("noise_table") or ())
        scale = self.input_scale()
        if not rows:
            return float(self.param("noise_divs")) * scale
        row = next((rms for div, rms in reversed(rows) if div <= scale * (1 + 1e-9)), rows[0][1])
        return row * self.noise_bandwidth_factor()

    def noise_bandwidth_factor(self) -> float:
        """How the noise table's values scale to this channel's hardware ``bandwidth`` (#1427):
        sqrt(bandwidth / noise_bandwidth) when the table says the bandwidth it was measured at,
        else 1. White noise grows with the square root of the band it is integrated over; a
        definition that extends a model and widens its bandwidth gets the noise that follows
        without rewriting the table. The bandwidth limit and the DSP filters after the ADC are
        not part of it: they filter the noise the ADC adds at the full band (D46)."""
        measured = self.param("noise_bandwidth")
        bandwidth = float(self.param("bandwidth"))
        if measured is None or not math.isfinite(bandwidth) or float(measured) <= 0:
            return 1.0
        return math.sqrt(bandwidth / float(measured))

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
        return self.convert(self.input("in"))

    def convert(self, sig: Signal) -> Signal:
        """``sig`` as this probe setting shows it: times the ratio, inverted when set."""
        k = self.effective_ratio()
        return sig.scale(-k if self.param("invert") in (True, 1, "1", "ON") else k)

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


def strongest_line(signal: Signal) -> float | None:
    """The frequency of a signal's strongest line (Hz), None without lines: what a frequency-
    dependent converter figure is read at (#1421; a several-tone signal takes its largest tone's)."""
    if not signal.freq.size:
        return None
    on = signal.freq > 0
    if not np.any(on):
        return None
    amps = np.abs(signal.amp) * on
    return float(signal.freq[int(np.argmax(amps))])


def _interpolated(points: list[tuple[float, Any]], freq: float | None) -> float:
    """``points`` (Hz, value) read at ``freq``: linear between the two around it, the nearest
    outside them; no frequency: the lowest one's."""
    given = sorted((float(f), float(v)) for f, v in points)
    if freq is None or freq <= given[0][0]:
        return given[0][1]
    if freq >= given[-1][0]:
        return given[-1][1]
    for (f0, v0), (f1, v1) in itertools.pairwise(given):
        if f0 <= freq <= f1:
            return v0 + (v1 - v0) * (freq - f0) / (f1 - f0)
    return given[-1][1]


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
    polynomial's do. A row may also name the input frequency it holds at, ``[V/div, Hz, HD2, HD3]``
    (#1421): the rows of one division are interpolated linearly over frequency (outside them the
    nearest holds) at the frequency of the signal's strongest line. ``enob_table`` rows ``[Hz, ENOB]`` give the
    effective bits of a full-scale sine at that frequency (interpolated the same way): the converter
    adds the noise that brings the record down to them — what the input noise, the quantization,
    the distortion and the aperture jitter leave of the error budget; none when they already exceed
    it (F1-1). An input without a line gets the lowest frequency's noise: the floor stays (#1442). ``interleave`` sub-converters on the ``max_rate`` grid differ by one cosine
    pattern in gain (``interleave_image_dbc``: each image at k·fs/N ± f) and offset
    (``interleave_spur_dbfs``: the spur at k·fs/N), and each sub-converter may carry its own gain
    error, offset and sampling-time skew (``interleave_gain_errors``, ``interleave_offsets_fs``,
    ``interleave_skews_s``, one value per sub-converter, #1422) — the images and spurs are then the
    discrete Fourier series of those lists. A record slower than ``max_rate`` takes every M-th of the
    full-rate samples, so its samples keep their sub-converters and the pattern folds into its band
    (every N-th: one sub-converter, a gain error and a DC offset); ``aperture_jitter_s`` moves each sample instant
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
        # [[V/div at the input, HD2 dBc, HD3 dBc], ...] or [[V/div, Hz, HD2, HD3], ...] at 0 dBFS (None: none)
        "distortion_table": None,
        "enob_table": None,  # [[Hz, effective bits of a full-scale sine], ...] (None: none, #1421)
        "interleave": 0,  # sub-converters interleaved on the max_rate grid (0, 1: none)
        "interleave_image_dbc": None,  # their gain mismatch: each image at k*fs/N +- f, dBc (None: none)
        "interleave_spur_dbfs": None,  # their offset mismatch: the spur at k*fs/N, dBFS (None: none)
        # each sub-converter's own mismatch, one value per sub-converter, added to the above (#1422):
        "interleave_gain_errors": None,  # gain errors, relative (None: none)
        "interleave_offsets_fs": None,  # offsets, fraction of the full scale (None: none)
        "interleave_skews_s": None,  # sampling-time skews, s (None: none)
        "aperture_jitter_s": 0.0,  # the sampling clock's aperture jitter, s rms
        # the record's noise passes the vertical's response (a DSP-corrected record's spectrum ends
        # at its band, #1446); False: white noise up to the record's Nyquist frequency
        "shape_noise": False,
        "vertical": "",  # the per-channel scope_vertical component: the scale, offset and input noise
        "probe": "",  # the per-channel scope_probe component: its ratio (noise) and skew
    }
    units = {
        "max_rate": "Sa/s", "max_rate_paired": "Sa/s", "min_full_scale": "V", "aperture_jitter_s": "s",
        "interleave_skews_s": "s",
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

    def _distortion(self, input_scale: float, freq: float | None = None) -> tuple[float, float]:
        """The x², x³ coefficients of the front end at this input V/div (#1266) and, for rows that
        name a frequency, at ``freq`` (#1421): HD2 = a2/2 and HD3 = a3/4 on a sine that spans the
        converter's range."""
        table = [list(r) for r in self.param("distortion_table") or ()]
        if not table:
            return 0.0, 0.0
        divs = sorted({float(r[0]) for r in table})
        div = next((d for d in reversed(divs) if d <= input_scale * (1 + 1e-9)), divs[0])
        band = [r for r in table if float(r[0]) == div]
        if len(band[0]) == 3:
            hd2, hd3 = float(band[0][1]), float(band[0][2])
        else:
            hd2 = _interpolated([(float(r[1]), r[2]) for r in band], freq)
            hd3 = _interpolated([(float(r[1]), r[3]) for r in band], freq)
        return 2.0 * 10 ** (hd2 / 20), 4.0 * 10 ** (hd3 / 20)

    def _enob_sigma(
        self, freq: float | None, half: float, lsb: float, sigma: float, hd: tuple[float, float]
    ) -> float:
        """The noise (display volts rms) the converter adds so that a full-scale sine at ``freq``
        records with ``enob_table``'s effective bits (#1421): the error a full-scale sine of that
        many bits has, less what ``sigma`` (input noise), the quantization (``lsb``/√12), the
        distortion (``hd``) and the aperture jitter already give, in quadrature; 0 without a table
        or room left. Without a frequency (no line: a DC, grounded or noise-only input) the table's
        lowest frequency is read (#1442): the broadband part of the converter's error is there with
        or without a signal, so the noise floor does not drop when the sine is taken away."""
        rows = self.param("enob_table")
        if not rows or half <= 0:
            return 0.0
        if freq is None:
            freq = min(float(r[0]) for r in rows)
        enob = _interpolated([(float(r[0]), r[1]) for r in rows], freq)
        rms = half / math.sqrt(2)  # a full-scale sine
        allowed = rms * 10 ** (-(6.02 * enob + 1.76) / 20)
        jitter = 2 * math.pi * freq * float(self.param("aperture_jitter_s")) * rms
        present = sigma**2 + lsb**2 / 12 + (rms * math.hypot(hd[0] / 2, hd[1] / 4)) ** 2 + jitter**2
        return math.sqrt(max(0.0, allowed**2 - present))

    @classmethod
    def literal_problems(cls, params: dict[str, Any]) -> list[tuple[str, str]]:
        """A per-sub-converter list must have one value for each of the ``interleave`` converters
        (#1422): a definition that changes the count keeps a parent's lists otherwise."""
        count = params.get("interleave", cls.defaults["interleave"])
        if not isinstance(count, int):
            return []
        out = []
        for name in ("interleave_gain_errors", "interleave_offsets_fs", "interleave_skews_s"):
            values = params.get(name)
            if isinstance(values, list) and len(values) != count:
                out.append((name, f"{name} 有 {len(values)} 个值，interleave 是 {count}：每个子 ADC 一个值"))
        return out

    def _per_sub(self, name: str) -> tuple[float, ...]:
        """A per-sub-converter list (#1422): one value for each of the ``interleave`` converters."""
        values = self.param(name)
        if not values:
            return ()
        n = int(self.param("interleave"))
        if len(values) != n:
            raise ValueError(f"{self.uid}: {name} has {len(values)} values, interleave is {n}")
        return tuple(float(v) for v in values)

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
        """Channel n's signal at the converter's input, in display volts."""
        return self.input(f"in{n}")

    def trigger_signal(self, n: int) -> Signal:
        """Channel n as the trigger comparator sees it, in display volts: the converter's input,
        or, when the vertical declares its analog front end's own shape (``analog_response``,
        #1423), the channel through that shape and the probe setting — the trigger taps the
        analog path, before the DSP flatness correction the record gets."""
        vertical = self._vertical(n)
        if not vertical.choice("analog_response"):
            return self.analog(n)
        probe = self._probe(n)
        sig = vertical.analog_output()
        return probe.convert(sig) if probe is not None else sig

    def frames(self, port: str, request: FrameRequest) -> SampleFrame:
        return self.view(_channel_of(port)).frame(request)

    def view(self, n: int) -> AdcView:
        """What channel n's frames are computed from now, as values (a deep record keeps it)."""
        vertical, probe = self._vertical(n), self._probe(n)
        scale = self.converter_scale(float(vertical.param("scale")), float(vertical.param("probe_ratio")))
        bits, per_div = int(self.param("bits")), self.codes_per_div()
        image, spur = self.param("interleave_image_dbc"), self.param("interleave_spur_dbfs")
        top = float(self.param("max_rate"))
        signal = self.analog(n)
        freq = strongest_line(signal)
        hd = self._distortion(vertical.input_scale(), freq)
        half = converter_half(scale, bits, per_div, float(self.param("divs")))
        sigma = vertical.input_noise_rms() * (float(probe.param("ratio")) if probe is not None else 1.0)
        lsb = scale / per_div if bits > 0 else 0.0
        sigma = math.hypot(sigma, self._enob_sigma(freq, half, lsb, sigma, hd))
        return AdcView(
            signal=signal,
            skew=probe.delay() if probe is not None else 0.0,
            sigma=sigma,
            seed=self.session.seed,
            scale=scale,
            offset=float(vertical.param("offset")),
            bits=bits,
            per_div=per_div,
            add=str(self.param("offset_sign")).lower() == "add",
            hd=hd,
            half=half,
            ilv=int(self.param("interleave")),
            ilv_gain=0.0 if image is None else 2.0 * 10 ** (float(image) / 20),
            ilv_offset=0.0 if spur is None else 10 ** (float(spur) / 20),
            ilv_gains=self._per_sub("interleave_gain_errors"),
            ilv_offsets=tuple(2.0 * v for v in self._per_sub("interleave_offsets_fs")),  # of half the span
            ilv_skews=self._per_sub("interleave_skews_s"),
            native_dt=1.0 / top if top > 0 else 0.0,
            aperture=float(self.param("aperture_jitter_s")),
            **self._noise_shape(vertical),
        )

    def _noise_shape(self, vertical: Any) -> dict[str, Any]:
        """The response the record's noise passes (#1446): the vertical's, when ``shape_noise``."""
        if not self.param("shape_noise"):
            return {}
        shape, fc = vertical.record_band()
        return {"noise_shape": shape, "noise_fc": fc} if math.isfinite(fc) else {}

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
    top rate and at most ``max_points / 10 div``; the record then holds ``rate x 10 div`` points.
    ``max_points`` (the memory the unit has) also caps a set memory depth (#1424). The trigger sits at the centre, or ``delay``
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
        most = memory_depth(self.param("max_points") or 0)
        if most > 0:  # the record holds what the memory has, whatever was asked (#1424)
            depth = min(depth, most)
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
