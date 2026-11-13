"""Oscilloscope components: analog front end and acquisition (trigger, sampling, quantization, transfer)."""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any, cast

import numpy as np

from ...engine.component import Component, OpResult, PortSpec, RecordSource, binding_of, component, op
from ...engine.noise import gaussian, stream_key, uniform
from ...engine.signal import Signal, bessel4, brickwall, butterworth4, gaussian_lp, highpass1, lowpass1
from ...engine.stream import FrameRequest, SampleFormat, SampleFrame
from ...scpi import values as V
from ...scpi.errors import ScpiError
from ...units import parse_number
from . import adc
from .calstore import CalibrationStore
from .measurements import (
    FREQ_HYSTERESIS,
    TIME_MEASUREMENTS,
    crossings,
    edge_spec,
    measure,
    mid_crossings,
    top_base,
)
from .scope_pending import Acquisition, PendingAcquisitions, _Segment

#: Vertical full scale is 8 divisions; 16-bit codes span ±4 divisions.
DIVS_V = 8
DIVS_H = 10


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

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    choices = {"response": ("first_order", "brickwall", "gaussian", "bessel4", "butterworth4")}
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
    }
    units = {
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
        """The record as the calibration constants correct it: (raw − offset) × gain, keyed by
        the displayed V/div (the setting the store names)."""
        store = self._store()
        if store is None:
            return volts
        gain, offset = store.coefficients()
        return (volts - offset) * gain

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


@component("scope_probe")
class ScopeProbe(Component):
    """The probe setting of one channel: display volts are the volts at the input times ``ratio``.

    A display conversion, not part of the analog path (instrument-authoring.md 8.4). ``skew``
    (bound to the model's channel-skew setting where it has one) and ``skew_error`` (this unit's
    own, a ``deviations`` draw) delay the channel's record by that many seconds: the acquisition
    samples the channel's signal that much earlier (instrument-authoring.md 8.4, G3). A real
    scope applies the delay after the ADC, so the trigger path — which detects on the analog
    signal — is not delayed with it."""

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
    }
    units = {"skew": "s", "skew_error": "s"}

    def output(self, port: str) -> Signal:
        return self.input("in").scale(float(self.param("ratio")))

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


def _boxcar(v: np.typing.NDArray[np.float64], w: int) -> np.typing.NDArray[np.float64]:
    """The centered boxcar average of ``w`` samples; at the edges the window runs past the record
    and the edge sample is repeated to fill it (high resolution, F4/D46: white noise falls by
    sqrt(w), the bandwidth by the window)."""
    if w <= 1:
        return v
    c = np.concatenate(([0.0], np.cumsum(np.pad(v, (w // 2, w - 1 - w // 2), mode="edge"))))
    return (c[w:] - c[:-w]) / w  # as many samples as it was given, each the mean of its own window


@dataclass
class Record:
    channel: int
    t_start_ns: int  # instant of sample 0
    dt_s: float
    trigger_index: int
    volts: np.typing.NDArray[np.float64]  # display volts (probe ratio applied)
    scale: float
    offset: float
    triggered: bool
    #: Time of sample 0 relative to the trigger as the scope measured it, s: -trigger_index * dt
    #: plus the trigger's fraction of a sample and its jitter (G5). None: -trigger_index * dt.
    x_origin_s: float | None = None
    trigger_ns: int | None = None  # the trigger's instant (holdoff); None: untriggered

    @property
    def origin(self) -> float:
        """Time of sample 0 relative to the trigger, s."""
        return self.x_origin_s if self.x_origin_s is not None else -self.trigger_index * self.dt_s


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
    a converter, so with both on the top rate is ``max_rate_paired``."""

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
        "vertical": "",  # the per-channel scope_vertical component: the scale, offset and input noise
        "probe": "",  # the per-channel scope_probe component: its ratio (noise) and skew
    }
    units = {"max_rate": "Sa/s", "max_rate_paired": "Sa/s"}

    @classmethod
    def spec_of(cls, port: str) -> PortSpec | None:
        if cls.port_pattern and re.fullmatch(cls.port_pattern, port):
            return _ANALOG_IN if port.startswith("in") else _SAMPLES_OUT
        return None

    def codes_per_div(self) -> float:
        bits = int(self.param("bits"))
        return float(self.param("codes_per_div")) or (1 << bits) / float(self.param("divs"))

    def top_rate(self, on: set[int]) -> float:
        """The top sample rate with channels ``on`` displayed (inf when unlimited)."""
        top = float(self.param("max_rate")) or math.inf
        if any(set(pair) <= on for pair in self.param("rate_pairs") or ()):
            top = float(self.param("max_rate_paired")) or top
        return top

    def quantize(
        self, volts: np.typing.NDArray[np.float64], scale: float, offset: float
    ) -> np.typing.NDArray[np.float64]:
        """What the ADC keeps: the codes of ``bits``, back in volts."""
        bits = int(self.param("bits"))
        if bits <= 0:
            return volts
        per_div = self.codes_per_div()
        off = offset if str(self.param("offset_sign")).lower() == "add" else -offset
        codes = adc.codes(volts, scale, off, per_div, bits)
        return codes / per_div * scale - off

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
        n = _channel_of(port)
        vertical, probe = self._vertical(n), self._probe(n)
        scale, offset = float(vertical.param("scale")), float(vertical.param("offset"))
        skew = probe.delay() if probe is not None else 0.0
        volts = self.analog(n).sample_rel(request.t0_ns, request.offsets_s() - skew)
        sigma = vertical.input_noise_rms() * (float(probe.param("ratio")) if probe is not None else 1.0)
        if sigma > 0:
            volts = volts + sigma * gaussian(stream_key(self.session.seed, *request.key), request.indexes())
        bits = int(self.param("bits"))
        kept = self.quantize(volts, scale, offset)
        clipped = False
        if bits > 0:
            top, bottom = self.quantize(np.array([1e30, -1e30]), scale, offset)
            clipped = bool(np.max(kept) >= top or np.min(kept) <= bottom)
        fmt = SampleFormat("volts", bits=bits, lsb=scale / self.codes_per_div() if bits > 0 else 0.0)
        return SampleFrame(kept, request.t0_ns, request.dt_s, fmt, clipped=clipped)

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
        proc = str(self.param("processing"))
        m = self.oversample()
        native = replace(request, first=request.first * m, count=request.count * m, dt_s=request.dt_s / m)
        count = self.averages() if m == 1 else 1
        if count > 1:
            # Every acquisition of the average is one more request of the same read: one that would
            # go beyond the model is refused here, before any of them is computed (#986).
            self.session.reserve_frames(self, source, count * native.count)
        frame = self.input_frames(source, native)
        volts, clipped = frame.data, frame.clipped
        if m > 1:
            volts = peak_detect(volts, m)
        elif proc == "average":
            for k in range(1, count):
                more = self.input_frames(source, replace(native, index0=native.index0 + k * native.count))
                volts, clipped = volts + more.data, clipped or more.clipped
            volts = volts / count
        elif proc == "eres":
            volts = _boxcar(volts, round(4.0 ** float(self.param("eres_bits"))))
        return SampleFrame(volts, request.t0_ns, request.dt_s, frame.fmt, clipped=clipped)

    def sample_interval(self, port: str) -> float:
        """The record's sample interval with the current settings."""
        adc_unit, horizontal = self._modules()
        return float(horizontal.timing(adc_unit.top_rate(set(_displayed(self))))[1])

    def input_interval(self, port: str) -> float:
        """The converter's interval behind one record sample: the record's, over the oversampling."""
        return self.sample_interval(port) / self.oversample()


def peak_detect(samples: np.typing.NDArray[np.float64], m: int) -> np.typing.NDArray[np.float64]:
    """Peak detect over intervals of ``m`` samples: of each interval's smallest and largest value,
    the one that departs farther from the interval's ends."""
    grid = samples.reshape(-1, m)
    ends = (grid[:, 0] + grid[:, -1]) / 2
    lo, hi = grid.min(axis=1), grid.max(axis=1)
    return np.where(np.abs(hi - ends) >= np.abs(lo - ends), hi, lo)


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
        # A model with a reference mode setting (DELay | POSition) places the trigger from it: in
        # POSition mode the trigger sits `reference_position` percent across the record and the
        # delay setting does not move it; in DELay mode it starts at the centre, moved by `delay`.
        "reference_mode": "",  # the reference mode setting; empty: the delay alone places the trigger
        "reference_position": None,  # the trigger position setting, 0..100 percent (POSition mode)
        "clock_ppm": 0.0,  # the sampling clock's frequency error, ppm (hidden)
        "rosc": "INTernal",  # INTernal | EXTernal: the clock follows the 10 MHz on ref
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
        if mode.startswith("POS") and position not in (None, ""):
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
        """The 10 MHz on ``ref``: within +-10 ppm and present, it is the clock (as the spectrum
        analyzer accepts its reference); anything else leaves the internal clock in charge."""
        self._ref_locked = False
        sig = self.input("ref").merged()
        if sig.freq.size:
            k = int(np.argmax(np.abs(sig.amp)))
            f = float(sig.freq[k])
            if abs(f - 10e6) <= 10e6 * 10e-6 and abs(sig.amp[k]) > 0.05:
                self._ref_error, self._ref_locked = f / 10e6 - 1, True


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
    records ahead (its auto timeout); a normal or single acquisition waits up to
    ``search_horizon_s`` of virtual time for its edge.

    ``mode`` ``glitch`` triggers on a pulse by its width instead (signal-chain.md 7, #956): a pulse
    of ``polarity`` runs from one crossing of the level to the next opposite one — the same
    interpolated crossings, hysteresis, coupling, noise and holdoff as the edge trigger — and
    ``qualifier`` compares that time with ``width_low`` and ``width_high``: ``less`` (narrower
    than ``width_high``) and ``range`` (between the two) trigger at the edge that ends the pulse,
    ``greater`` at the instant the pulse has lasted ``width_low`` (it need not have ended). The
    whole pulse lies after the trigger is armed: one that began within the pre-trigger time or the
    holdoff does not count. ``slope`` plays no part. The search takes the record grid for as many
    samples as ``max_search_points`` allows (fewer for a signal of many lines), then a grid an
    eighth of the narrowest width bound the qualifier uses for as many again, the instants there
    bisected on the signal (without trigger noise); a pulse narrower than that grid can be missed
    there, and an acquisition that finds nothing within that reach waits, looking again at the
    next command (#1000).
    """

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
        "search_windows": 4,  # records an auto mode looks ahead for an edge (its auto timeout)
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
        "holdoff_on": True,  # whether holdoff applies (e.g. a holdoff mode that is TIME)
        "holdoff": 0.0,  # s after a trigger before the next one is taken
        "external": ["EX", "EX5", "EXTernal", "EXT"],  # sources read from the ext port
        "line": ["LINE"],  # sources that trigger on the mains
        "line_hz": 50.0,  # mains frequency
        "mode": "edge",  # the trigger type: edge, or glitch (a pulse by its width)
        "polarity": "positive",  # glitch: the pulse above the level (positive) or below it (negative)
        "qualifier": "less",  # glitch: narrower than width_high (less), wider than width_low (greater), between (range)
        "width_low": 20e-9,  # glitch: the width a greater or range pulse exceeds, s
        "width_high": 30e-9,  # glitch: the width a less or range pulse stays under, s
    }
    units = {
        "level": "V", "holdoff": "s", "search_horizon_s": "s", "line_hz": "Hz", "noise_rms": "V",
        "width_low": "s", "width_high": "s",
    }  # fmt: skip
    choices = {
        "mode": ("edge", "glitch"),
        "polarity": ("positive", "negative"),
        "qualifier": ("less", "greater", "range"),
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_ns: int | None = None  # the instant of the latest trigger (holdoff)
        # The latest search and its result: a search is a pure function of its inputs, and the
        # status registers ask the same one again after every command of a line (#1000).
        self._memo: tuple[str, TriggerHit | None] | None = None

    def reset(self) -> None:
        self.last_ns = None
        self._memo = None

    def _search_key(self, sig: Signal, args: tuple[Any, ...], n: int | None) -> str:
        """Everything a search's result depends on: its arguments, the signal's lines, the
        holdoff's anchor and every parameter as it resolves now (the level and slope included)."""
        lines = hashlib.blake2b(digest_size=16)
        for a in (sig.freq, sig.amp, sig.t0_ns):
            lines.update(np.ascontiguousarray(a).tobytes())
        level = self.channel_param("level", n) if n is not None else self.level()
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

    def source_channel(self) -> int | None:
        """The channel the source names, None for external, line and the like."""
        return trigger_channel(self.param("source"))

    def source_kind(self) -> str:
        """channel | external | line | none."""
        src = str(self.param("source")).upper()
        if self.source_channel() is not None:
            return "channel"
        if src in _upper(self.param("external")):
            return "external"
        if src in _upper(self.param("line")):
            return "line"
        return "none"

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

    def path_signal(self, sig: Signal) -> Signal:
        """The source's signal as the trigger sees it: through the coupling's filters."""
        table = {str(k).upper(): v for k, v in (self.param("filters") or {}).items()}
        keys = [str(self.param("coupling")).upper(), str(self.param("reject") or "").upper()]
        if self.param("hf_reject") in (True, 1, "1", "ON", "on"):
            keys.append("HF")
        for key in keys:
            for kind, corner in table.get(key, []) if key else []:
                sig = sig.transfer(
                    (highpass1 if str(kind).lower() == "highpass" else lowpass1)(float(corner))
                )
        return sig

    def _hysteresis(self, scale: float) -> float:
        on = self.param("noise_reject") in (True, 1, "1", "ON", "on")
        divs = float(self.param("reject_hysteresis_divs" if on else "hysteresis_divs"))
        return divs * scale

    def _noisy(
        self, v: np.typing.NDArray[np.float64], t0: int, offsets: np.typing.NDArray[np.float64]
    ) -> np.typing.NDArray[np.float64]:
        sigma = float(self.param("noise_rms"))
        if sigma <= 0 or self.session is None:
            return v
        # Keyed by the absolute instant (ns) of each sample: the same instant, the same noise.
        key = stream_key(self.session.seed, self.uid, "trigger-noise")
        return v + sigma * gaussian(key, t0 + np.round(offsets * 1e9).astype(np.int64))

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
        kind = self.source_kind()
        start_s = max(pre * dt, earliest_s)
        if self.last_ns is not None and self.param("holdoff_on") in (True, 1, "1", "ON", "on"):
            start_s = max(start_s, (self.last_ns - t0) * 1e-9 + float(self.param("holdoff")))
        span = max(points * int(self.param("search_windows")), pre + 2 * points) * dt
        horizon = max(span, float(horizon_s or 0.0))
        glitch = self.choice("mode") == "glitch"
        if glitch and kind not in ("channel", "external"):
            return None  # the mains has no pulse to time
        if kind == "line":
            period = 1.0 / float(self.param("line_hz"))
            phase = (t0 * 1e-9) % period
            t = math.ceil((start_s + phase) / period - 1e-12) * period - phase
            return TriggerHit(t, math.ceil(t / dt - 1e-9)) if t <= horizon else None
        if kind == "none":
            return None
        level = float(self.channel_param("level", n) if n is not None else self.level())
        slope = str(self.channel_param("slope", n) if n is not None else self.param("slope")).upper()
        rising = slope in _upper(self.param("rising"))
        falling = slope in _upper(self.param("falling"))
        hyst = self._hysteresis(scale)
        sig = self.path_signal(sig)
        # An edge the signal cannot make (its level beyond the signal's whole swing, noise
        # included) is not searched for: a waiting acquisition looks again at every command.
        swing = float(np.abs(sig.amp).sum()) + 6.0 * float(self.param("noise_rms"))
        top, bottom = sig.dc + swing, sig.dc - swing
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
        period = _period(sig) if self.param("noise_rms") in (0, 0.0, None) else None
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
            used = {"less": (high,), "greater": (low,)}.get(qualifier, (low, high))
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

        opened: _Crossing | None = None  # the opening edge of the pulse under way
        for c in self._edges(sig, t0, dt, start_s, stretches, level, hyst):
            if opened is not None and qualifier == "greater" and c.at - opened.before > low:
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
                if opened is not None and (
                    (qualifier == "less" and c.before - opened.at < high)
                    or (qualifier == "range" and c.before - opened.at < high and c.at - opened.before > low)
                ):
                    width = instant(c) - instant(opened)
                    if width < high and (qualifier == "less" or width > low):
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
        noiseless = self.param("noise_rms") in (0, 0.0, None)
        a = start_s - stretches[0][1]
        for stop, step in stretches:
            chunk = 1 << 12
            final = not (step > dt * (1 + 1e-9) and noiseless)
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
        coarser one that carries no noise."""
        t_k, v_k = float(offsets[k]), float(v[k])
        t_p, v_p = (float(offsets[k - 1]), float(v[k - 1])) if k > 0 else (prev or (t_k, v_k))
        if step > dt * (1 + 1e-9) and self.param("noise_rms") in (0, 0.0, None):
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
        self.acq_count = 0
        self.running = True
        self.armed = False
        self.last_triggered = False
        # ty sees the @component class as type[Component] and cannot match it to the protocol.
        self.pending = PendingAcquisitions(cast("Acquisition", self))

    def reset(self) -> None:
        self.records = {}
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
        return float(self._module("probe", f"ch{channel}.{self.param('probe')}").param("ratio"))

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
        for n in channels:
            scale, offset = self.scale_offset(n)
            # The channel's chain (ADC, then the decimator's acquisition mode) gives the record's
            # samples: the codes, as volts (D46: they are kept).
            volts = self.input_frames(
                f"in{n}", self._request(n, t0, start, dt, points, t_start_ns, m, frame, segment)
            ).data
            records[n] = Record(
                channel=n,
                t_start_ns=t_start_ns,
                dt_s=nominal_dt,
                trigger_index=pre,
                volts=self.vertical_of(n).correct(volts),
                scale=scale,
                offset=offset,
                triggered=triggered,
                x_origin_s=x_origin * nominal_dt / dt,  # in the scope's own (nominal) time
                trigger_ns=trigger_ns,
            )
        if triggered or self.param("untriggered") == "record":
            used = (k + max(post, 0)) * dt
        else:  # the whole trigger search
            used = points * int(self.trigger_unit().param("search_windows")) * dt
        used = used * self._averages() + float(self.param("overhead_s"))
        return records, round(used * 1e9), triggered, t0 + round(k * dt * 1e9)

    def _trigger_hit(
        self, t0: int, dt: float, pre: int, points: int, waits: bool, earliest_s: float = 0.0
    ) -> TriggerHit | None:
        """The trigger the acquisition starting at ``t0`` finds (``waits``: as far as the trigger's
        search horizon; none before ``earliest_s``), or None."""
        trig = self.trigger_unit()
        n_trig = trig.source_channel()
        kind = trig.source_kind()
        if kind == "channel":
            sig, scale_t = self._display_signal(n_trig), self.scale_offset(n_trig)[0]  # type: ignore[arg-type]
        elif kind == "external":
            sig, scale_t = trig.input("ext"), 1.0
        else:
            sig, scale_t = Signal.zero(), 1.0
        return trig.search(
            sig, t0, dt, pre, points, n_trig, scale_t,
            float(trig.param("search_horizon_s")) if waits else None,
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
                            "points": int(rec.volts.size),
                            "trigger_index": rec.trigger_index,
                            "x_origin_s": rec.origin,
                            "triggered": rec.triggered,
                            "sha256": hashlib.sha256(np.ascontiguousarray(rec.volts).tobytes()).hexdigest(),
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

    @op("analyze_segments")
    def analyze_segments(self, **_: Any) -> OpResult:
        """``:ACQuire:SEGMented:ANALyze``: accepted. Measurement statistics and persistence over
        all the segments are not modeled; a measurement reads the selected segment."""
        return OpResult()

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
    * ``gate``: [from, to] divisions of the record (0 to 10) the measurement looks at;
    * what cannot be measured answers ``invalid_value`` with the second ``status`` word.

    Every value taken is kept for ``statistics`` (count, mean, sdev, min, max) until cleared.
    """

    refs = {"record": (RecordSource, "instrument")}
    defaults = {
        "record": "acq",  # the acquisition component holding the records (RecordSource: a real-time or sampling one)
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
        "gate": None,  # [from, to] divisions of the record; None: all of it
        "invalid_value": "UNDEF",  # what an unmeasurable value answers
        "status": ["OK", "UF"],  # measured, cannot be measured
        "sources": [],  # the two settings DELay?/PHASe? use without sources, e.g. [measure.source, ...]
        "trigger_levels": {},  # channel -> its trigger level setting, for :TRIGger:LEVel:ASETup
        # The screen's measurement list (#373): a measurement command without '?' installs one;
        # past `slots` the oldest leaves. 0: no list (the query only answers).
        "slots": 0,
        "all": [],  # the measurements :MEASure:ALL? answers, in order (engine names)
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.history: dict[tuple[str, int], list[float]] = {}
        self.installed: list[tuple[str, int]] = []  # the screen's measurements, oldest first (#373)

    def reset(self) -> None:
        self.history = {}
        self.installed = []

    # ---- the screen's measurement list (#373) ------------------------------------------------------

    def install(self, what: str, n: int) -> None:
        """Put a measurement on the screen: a second install of the same one moves it to the end;
        past ``slots`` the oldest leaves."""
        slots = int(self.param("slots") or 0)
        if slots <= 0:
            raise ScpiError(-113, f"{what}: this model has no measurement list")
        entry = (what, n)
        if entry in self.installed:
            self.installed.remove(entry)
        self.installed.append(entry)
        del self.installed[:-slots]

    @op("results")
    def op_results(self, **_: Any) -> OpResult:
        """``:MEASure:RESults?``: each installed measurement, oldest first, as
        ``<label>,<current>,<min>,<max>,<mean>,<std dev>,<count>`` — the statistics of every value
        taken since the list was cleared (the current one included). Label: ``<name>(<channel>)``.
        Nothing installed: an empty reply."""
        assert self.owner is not None
        parts: list[bytes] = []
        used = 0
        for what, n in self.installed:
            value, u = self.value(what, n)
            used += u
            self._keep(what, n, value)
            values = np.array(self.history.get((what, n)) or [])
            fields = [f"{what}({n})".encode(), self._number(value)]
            if values.size:
                fields += [
                    self._number(float(f())) for f in (values.min, values.max, values.mean, values.std)
                ]
            else:
                fields += [self._number(math.nan)] * 4
            fields.append(str(values.size).encode())
            parts.append(b",".join(fields))
        return OpResult(b",".join(parts), used)

    def _number(self, value: float) -> bytes:
        assert self.owner is not None
        if not math.isfinite(value) and self.param("invalid_value") != "UNDEF":
            return str(self.param("invalid_value")).encode()
        return self.owner.format_number(value)

    @op("clear_list")
    def op_clear_list(self, **_: Any) -> OpResult:
        """``:MEASure:CLEar``: no measurements on the screen, their statistics gone."""
        self.installed = []
        self.history = {}
        return OpResult()

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

    def _channel(self, channel: int | None, source: Any = None) -> int:
        if isinstance(source, list):  # raw parameters: [<interval>][,<type>][,<source>]
            words = [V.parse_string(b).strip().upper() for b in source]
            source = next((w for w in words if w.startswith("CH")), None)
        if source:
            name = source.strip().upper()
            m = name.removeprefix("CHANNEL").removeprefix("CHAN").removeprefix("C")
            if not m.isdigit():
                raise ScpiError(-224, f"unknown source {source!r}")
            return int(m)
        bound = self.param("source")
        if bound not in (None, ""):
            return channel_index(bound)
        return channel or 1

    def value(self, what: str, n: int) -> tuple[float, int]:
        """The engine measurement ``what`` on channel n's record (NaN: cannot be measured), and the
        virtual time getting the record took."""
        rec, used = self._acq().measure_record(n)
        return self.record_value(what, rec), used

    def record_value(self, what: str, rec: Any) -> float:
        """The engine measurement ``what`` on a record by this model's rules (gate, clipping, top
        and base, crossing level): what :MEASure? answers, and what the screen's measurement
        panel shows for the record on screen (#280)."""
        acq = self._acq()
        v = np.asarray(rec.volts, dtype=float)
        gate = self.param("gate")
        if gate:
            lo, hi = (max(0, min(v.size, round(float(g) / 10 * v.size))) for g in gate)
            v = v[lo:hi]
        if v.size and str(self.param("clipped")) == "invalid":
            rails = acq.adc_unit().quantize(np.array([1e30, -1e30]), rec.scale, rec.offset)
            if acq.adc_unit().param("bits") and (v.max() >= rails[0] or v.min() <= rails[1]):
                return math.nan
        if what in TIME_MEASUREMENTS:
            return self._time_value(what, v, rec)
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
        )
        return value

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
        channels = [self._channel(None, w) for w in words if w.upper().startswith("CH")]
        return [w for w in words if not w.upper().startswith("CH")], channels

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
            self._follow(n, f"CHANnel{n}")
        rec, used = self._acq().measure_record(n)
        v = np.asarray(rec.volts, dtype=float)
        dt, origin = rec.dt_s, rec.origin
        if what == "VTIMe":
            if not args:
                raise ScpiError(-109, "VTIMe? takes a time")
            x = (V.parse_float(args[0].encode(), "S") - origin) / dt
            if v.size == 0 or not 0 <= x <= v.size - 1:
                return self._reply(math.nan, used)
            k = min(int(x), v.size - 2) if v.size > 1 else 0
            frac = x - k
            value = float(v[k]) if v.size == 1 else float(v[k] + (v[k + 1] - v[k]) * frac)
            return self._reply(value, used)
        if what == "TEDGe":
            sign, occurrence = edge_spec(args[0] if args else None)
            cross = [t for t, d in mid_crossings(v, dt, float(self.param("hysteresis"))) if d == sign]
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
    def op_pair(self, what: str = "", source: list[bytes] | None = None, **_: Any) -> OpResult:
        """``DELay? [<source1>][,<source2>]``: the time from source 1's first rising edge to source 2's
        first rising edge at their middle levels; ``PHASe?``: that delay over source 1's period, in
        degrees (#350). Without sources, :MEASure:SOURce's two (``sources``)."""
        assert self.owner is not None
        _words, channels = self._sources(source)
        if len(channels) < 2:
            pair = [channel_index(self.owner.get_state(p)) for p in (self.param("sources") or [])]
            channels = (channels + pair[len(channels) :])[:2]
        if len(channels) < 2:
            raise ScpiError(-109, f"{what}? needs two sources")
        hyst = float(self.param("hysteresis"))
        times, used = [], 0
        first = None
        for n in channels:
            rec, u = self._acq().measure_record(n)
            used += u
            v = np.asarray(rec.volts, dtype=float)
            first = first or (v, rec)
            rising = [t for t, d in mid_crossings(v, rec.dt_s, hyst) if d == 1]
            times.append(rec.origin + rising[0] if rising else math.nan)
        delay = times[1] - times[0]
        if what == "DELay":
            return self._reply(delay, used)
        assert first is not None
        period = measure("PERiod", first[0], first[1].dt_s, hysteresis=hyst)
        return self._reply(delay / period * 360.0 if math.isfinite(period) and period > 0 else math.nan, used)

    @op("auto_trigger_levels")
    def op_auto_trigger_levels(self, **_: Any) -> OpResult:
        """``:TRIGger:LEVel:ASETup``: every displayed channel's trigger level to its waveform's 50 %
        (halfway between top and base; 0 V with AC coupling) — the levels of ``trigger_levels``,
        channel -> setting (#350)."""
        assert self.owner is not None
        acq = self._acq()
        for key, path in (self.param("trigger_levels") or {}).items():
            n = int(key)
            if n not in acq.displayed():
                continue
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
        match = next((o for o in options if trigger_channel(o) == n), None)
        if match is not None:
            self.owner.set_state(path, match)

    def _keep(self, name: str, n: int, value: float) -> None:
        if math.isfinite(value):
            self.history.setdefault((name, n), []).append(value)

    @op("measure")
    def measure(
        self, what: str = "", source: Any = None, channel: int | None = None, query: bool = True, **_: Any
    ) -> OpResult:
        """``:MEASure:VPP? [CHANnel1]``: the engine measurement ``what`` (a constant argument of the
        command) in the definition's number format; what cannot be measured is NaN's sentinel.
        Without '?' (``:MEASure:VPP CHANnel1``) the measurement goes on the screen's list (#373)."""
        assert self.owner is not None
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
