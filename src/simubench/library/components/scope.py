"""Oscilloscope components: analog front end and acquisition (trigger, sampling, quantization, transfer)."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, PortSpec, binding_of, component, op
from ...engine.noise import gaussian, stream_key
from ...engine.signal import Signal, highpass1, lowpass1
from ...scpi import values as V
from ...scpi.errors import ScpiError
from ...units import parse_number

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


@component("scope_vertical")
class ScopeVertical(Component):
    """The vertical channel: coupling, bandwidth, and the scale and offset with their ranges.

    Analog first-order filters (physics, D13): AC coupling is a ``ac_corner`` high-pass and the
    bandwidth a first-order low-pass at ``bandwidth`` (or ``bw_limit_hz`` when the limit is on).
    The ``scale`` (V/div) and ``offset`` (V) are display values; with ``scale_range`` and
    ``offset_ranges`` a change outside the range is refused with -222 and the old value kept. The
    ranges are given at the input connector, i.e. the display values divided by ``probe_ratio``
    (instrument-authoring.md 8.4).
    """

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    defaults = {
        "coupling": "DC",  # DC | AC | GND
        "bandwidth": 200e6,  # -3 dB point of the first-order low-pass
        "bw_limit": False,  # bandwidth limit on, or a named filter (FULL, 20M)
        "bw_limit_hz": 20e6,  # the limit when bw_limit is on
        "ac_corner": 10.0,  # corner of the AC coupling high-pass
        "scale": "$ch.scale",  # V/div as displayed
        "offset": "$ch.offset",  # V as displayed
        "probe_ratio": 1.0,  # the probe setting the displayed scale and offset include
        "scale_range": None,  # [min, max] V/div at the input connector (None: any); may map a setting
        "offset_ranges": None,  # [[up to V/div, +-limit V], ...] at the input connector (None: any)
    }
    units = {"bandwidth": "Hz", "bw_limit_hz": "Hz", "ac_corner": "Hz", "scale": "V", "offset": "V"}

    def output(self, port: str) -> Signal:
        sig = self.input("in")
        coupling = self.param("coupling")
        if coupling == "GND":
            return Signal.zero()
        if coupling == "AC":
            sig = sig.transfer(highpass1(float(self.param("ac_corner"))))
        return sig.transfer(lowpass1(min(self._limit(), float(self.param("bandwidth")))))

    def _limit(self) -> float:
        """Bandwidth limit: on/off (``bw_limit_hz``) or a named filter such as Siglent's 20M / FULL."""
        limit = self.param("bw_limit")
        if isinstance(limit, str):
            if limit.upper() in ("FULL", "OFF"):
                return math.inf
            return parse_number(limit.upper().removesuffix("HZ").replace("M", "e6").replace("K", "e3"))
        return float(self.param("bw_limit_hz")) if limit else math.inf

    def on_state(self, path: str, old: Any, new: Any) -> None:
        """Refuse a scale or offset outside its range. A probe change scales both together (the
        probe setting moves the display values, not the input), so it is not checked."""
        if path == self.bound_paths().get("probe_ratio"):
            return
        ranges, limits = self.param("scale_range"), self.param("offset_ranges")
        if ranges is None and limits is None:
            return
        ratio = float(self.param("probe_ratio"))
        scale = float(self.param("scale")) / ratio
        if ranges is not None:
            lo, hi = (float(x) for x in ranges)
            if not lo * (1 - 1e-9) <= scale <= hi * (1 + 1e-9):
                raise ScpiError(-222, f"scale outside {lo:g}..{hi:g} V/div at the input")
        if limits is not None:
            limit = next((float(lim) for top, lim in limits if scale <= float(top) * (1 + 1e-9)), None)
            if limit is None:
                limit = float(limits[-1][1])
            if abs(float(self.param("offset")) / ratio) > limit * (1 + 1e-9):
                raise ScpiError(-222, f"offset outside +-{limit:g} V at this scale (at the input)")


@component("scope_frontend")
class ScopeFrontend(ScopeVertical):
    """The earlier name of ``scope_vertical`` (same behavior), kept so existing definitions load."""


@component("scope_probe")
class ScopeProbe(Component):
    """The probe setting of one channel: display volts are the volts at the input times ``ratio``.

    A display conversion, not part of the analog path (instrument-authoring.md 8.4)."""

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    defaults = {
        "ratio": "$ch.probe",  # probe attenuation the display assumes (1, 10, ...)
    }

    def output(self, port: str) -> Signal:
        return self.input("in").scale(float(self.param("ratio")))


def channel_index(source: object) -> int:
    """Channel number of a source name: CHANnel2 (sb-*) or C2 (Siglent)."""
    text = str(source).upper()
    for prefix in ("CHANNEL", "CHAN", "C"):
        if text.startswith(prefix) and text[len(prefix) :].isdigit():
            return int(text[len(prefix) :])
    return 1


#: Edge slope names across models: SCPI-style and Siglent-style.
RISING = ["POSitive", "EITHer", "RISing", "ALTernate"]
FALLING = ["NEGative", "EITHer", "FALLing", "ALTernate"]


def trigger_channel(source: object) -> int | None:
    """Channel number of a trigger source (CHANnel2, C2), None for EXTernal, LINE and the like."""
    text = str(source).upper()
    for prefix in ("CHANNEL", "CHAN", "C"):
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


@dataclass
class Record:
    channel: int
    t_start_ns: int  # instant of sample 0
    dt_s: float
    trigger_index: int
    volts: np.ndarray  # display volts (probe ratio applied)
    scale: float
    offset: float
    triggered: bool


def _upper(values: Any) -> set[str]:
    return {str(v).upper() for v in values or ()}


@component("scope_adc")
class ScopeAdc(Component):
    """The ADC group shared by a scope's channels: resolution, top sample rates and interleaving.

    ``bits`` (0: records are not quantized) and ``codes_per_div`` (0: ``2**bits / divs``) set the
    quantization; ``offset_sign`` says whether the offset setting is added to or subtracted from the
    volts. ``max_rate`` is the top sample rate (0: no limit); channels of a ``rate_pairs`` pair share
    a converter, so with both on the top rate is ``max_rate_paired``."""

    defaults = {
        "bits": 0,  # 0: records are not quantized
        "codes_per_div": 0.0,  # 0: 2**bits / divs
        "divs": 10.0,  # divisions the ADC spans
        "offset_sign": "add",  # add | subtract: how the offset setting enters the codes
        "max_rate": 0.0,  # top sample rate, Sa/s (0: no limit)
        "max_rate_paired": 0.0,  # the top rate when both channels of a rate pair are on
        "rate_pairs": [],  # channels sharing a converter, e.g. [[1, 2], [3, 4]]
    }
    units = {"max_rate": "Sa/s", "max_rate_paired": "Sa/s"}

    def codes_per_div(self) -> float:
        bits = int(self.param("bits"))
        return float(self.param("codes_per_div")) or (1 << bits) / float(self.param("divs"))

    def top_rate(self, on: set[int]) -> float:
        """The top sample rate with channels ``on`` displayed (inf when unlimited)."""
        top = float(self.param("max_rate")) or math.inf
        if any(set(pair) <= on for pair in self.param("rate_pairs") or ()):
            top = float(self.param("max_rate_paired")) or top
        return top

    def quantize(self, volts: np.ndarray, scale: float, offset: float) -> np.ndarray:
        """What the ADC keeps: the codes of ``bits``, back in volts."""
        bits = int(self.param("bits"))
        if bits <= 0:
            return volts
        per_div = self.codes_per_div()
        half = 1 << (bits - 1)
        off = offset if str(self.param("offset_sign")).lower() == "add" else -offset
        codes = np.clip(np.round((volts + off) / scale * per_div), -half, half - 1)
        return codes / per_div * scale - off


@component("scope_timebase")
class ScopeTimebase(Component):
    """The horizontal system: time per division, the record length rule and the trigger position.

    ``record_length``: ``fixed`` (``points`` over ten divisions), ``exact`` (the rate is
    ``points / 10 div`` up to the ADC group's top rate) or ``rate_ladder`` (that rate rounded down
    to a 1-2-5 step; samples ``1 / rate`` apart). The trigger sits at the centre, or ``delay``
    seconds before it when that is bound."""

    defaults = {
        "scale": "$timebase.scale",  # seconds per division (ten divisions across)
        "delay": None,  # unbound: the trigger at the centre of the record
        "record_length": "fixed",  # fixed | exact | rate_ladder
        "points": "$waveform.points",  # record points or memory depth (10000, "10k")
    }
    units = {"scale": "s", "delay": "s"}

    def window(self) -> float:
        return DIVS_H * float(self.param("scale"))

    def sample_rate(self, top: float) -> float:
        """The sample rate under the ADC group's top rate ``top``."""
        window = self.window()
        depth = memory_depth(self.param("points"))
        rule = str(self.param("record_length"))
        if rule == "fixed":
            return depth / window
        if rule == "rate_ladder":
            return min(top, ladder_down(depth / window))
        return min(top, depth / window)

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
            dt = 1.0 / rate if rule == "rate_ladder" else window / points
        # Sample 0 is at delay - 5 div: the trigger sits (5 div - delay) / dt in (no delay: the centre).
        delay = self.param("delay")
        pre = points // 2 if delay in (None, "") else round((window / 2 - float(delay)) / dt)
        return points, dt, pre, points - pre


@component("scope_trigger")
class ScopeTrigger(_ChannelBound):
    """Edge trigger: ``source``, ``level`` and ``slope`` (``rising`` and ``falling`` list the slope
    names that take each edge); a level or slope per channel is bound with the channel number
    written ``{n}``: ``$ch{n}.trigger_level``. A source that is not a channel (external, line)
    never triggers. The search looks ``search_windows`` records ahead."""

    defaults = {
        "source": "$trigger.source",  # a channel (C1, CHANnel1); anything else never triggers
        "level": "$trigger.level",  # display volts; per channel: $ch{n}.trigger_level
        "slope": "$trigger.slope",  # per channel: $ch{n}.trigger_slope
        "rising": RISING,  # slope values that take a rising edge
        "falling": FALLING,  # slope values that take a falling edge
        "search_windows": 4,  # how many records long the trigger search is
    }
    units = {"level": "V"}

    def source_channel(self) -> int | None:
        """The channel the source names, None for external, line and the like."""
        return trigger_channel(self.param("source"))

    def level(self) -> float:
        """The trigger level in display volts (the scope screen draws it)."""
        level = self.channel_param("level", self.source_channel())
        return 0.0 if level is None else float(level)

    def search(
        self, sig: Signal, t0: int, dt: float, pre: int, points: int, n: int, scale: float
    ) -> tuple[int, bool]:
        """First qualifying edge at or after ``pre`` samples (enough history before the trigger).

        Returns (trigger sample index measured from t0, triggered?)."""
        level = float(self.channel_param("level", n))
        slope = str(self.channel_param("slope", n)).upper()
        hyst = 0.02 * DIVS_V * scale
        span = max(points * int(self.param("search_windows")), pre + 2 * points)
        idx = np.arange(span)
        v = sig.sample_rel(t0, idx * dt)
        best: int | None = None
        for wanted, armed_side, crossed in (
            (slope in _upper(self.param("rising")), v < level - hyst, v >= level),
            (slope in _upper(self.param("falling")), v > level + hyst, v <= level),
        ):
            if not wanted:
                continue
            armed = np.nonzero(armed_side & (idx >= pre - 1))[0]
            if armed.size:
                after = np.nonzero(crossed & (idx > armed[0]))[0]
                if after.size and (best is None or after[0] < best):
                    best = int(after[0])
        return (pre, False) if best is None else (best, True)


#: Parameters an acquisition written the earlier way (one component) gives each module, and the
#: module's name for them (instrument-authoring.md 8.4: the big component is a macro).
MACRO_MODULES: dict[str, tuple[str, dict[str, str]]] = {
    "horizontal": (
        "scope_timebase",
        {"timebase": "scale", "delay": "delay", "record_length": "record_length", "points": "points"},
    ),
    "trigger": (
        "scope_trigger",
        {
            "trigger_source": "source",
            "trigger_level": "level",
            "trigger_slope": "slope",
            "rising": "rising",
            "falling": "falling",
            "search_windows": "search_windows",
        },
    ),
    "adc": (
        "scope_adc",
        {
            "adc_bits": "bits",
            "adc_codes_per_div": "codes_per_div",
            "adc_divs": "divs",
            "adc_offset": "offset_sign",
            "max_rate": "max_rate",
            "max_rate_paired": "max_rate_paired",
            "rate_pairs": "rate_pairs",
        },
    ),
}
#: The names the expansion gives the modules.
MACRO_NAMES = {"horizontal": "timebase", "trigger": "trigger", "adc": "adc"}


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
    * **noise** ``noise_rms`` (input-referred white noise, times the probe ratio), keyed by
      ``noise_key``: ``time`` (the absolute sample instants, D46; ``noise_stream`` names a setting
      that selects another stream) or ``acquisition`` (the acquisition count, so pauses of any
      real length between commands give the same numbers); ``trigger_jitter_s``; ``averaging``
      with ``averages`` divides the noise by the square root of the count and takes that many
      acquisitions' time.

    * **synchronization** (instrument-authoring.md 8.4, G2): ``completion: immediate`` finishes a
      single acquisition, a digitize or a forced frame within the command that starts it;
      ``pending`` makes it an operation that finishes in virtual time, when the record after the
      trigger is full: until then the trigger status is waiting (then triggered), a read gets the
      previous record (``while_waiting: previous``) or an error (``error``), ``*OPC?`` and ``*WAI``
      wait for it unless ``opc_waits`` is false, and a digitize waits for it. Without a trigger it
      waits for one, looking again whenever a command arrives; ``no_trigger: wait`` leaves ``*OPC?``
      unanswered meanwhile (the client times out), ``return`` answers it at once.

    Per-channel ``ch<n>.display`` says which channels are acquired. Model-specific commands
    (Siglent's WAVEDESC, LeCroy's INSPECT?) are subclasses.
    """

    ports: dict[str, PortSpec] = {}
    port_pattern = r"in\d+"  # inputs in1..inN are created by the instrument wiring
    refs = {
        "horizontal": ("scope_timebase", "instrument"),
        "trigger": ("scope_trigger", "instrument"),
        "adc": ("scope_adc", "instrument"),
        "vertical": ("scope_vertical", "channel"),
        "probe": ("scope_probe", "channel"),
    }
    defaults = {
        "horizontal": "",  # the scope_timebase component
        "trigger": "",  # the scope_trigger component
        "adc": "",  # the scope_adc component
        "vertical": "",  # the per-channel scope_vertical component (its name without ch<n>.)
        "probe": "",  # the per-channel scope_probe component (its name without ch<n>.)
        "overhead_s": 1e-3,  # virtual time an acquisition takes besides its record, s
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
        "noise_rms": 0.0,  # input-referred white noise, V rms (times the probe ratio)
        "noise_key": "acquisition",  # acquisition | time
        "noise_stream": "",  # a setting that selects another noise stream (time key)
        "trigger_jitter_s": 0.0,  # rms offset of each record around the trigger, s
        "measurements": {},  # named measurements (op measurement): {AMPL: [VAMPlitude, V], ...}
        "invalid_value": "UNDEF",  # a named measurement's value when it cannot be measured
        "status": ["OK", "UF"],  # a named measurement's status: measured, cannot be measured
        "averaging": False,  # averaging on: noise / sqrt(averages), averages times the time
        "averages": 1,  # acquisitions averaged
        "completion": "immediate",  # immediate | pending: when a single acquisition or digitize is done
        "opc_waits": True,  # *OPC?, *OPC and *WAI wait for a pending acquisition
        "while_waiting": "previous",  # previous | error: a read while an acquisition is pending
        "no_trigger": "wait",  # wait | return: *OPC? while armed for a trigger that has not come
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.records: dict[int, Record] = {}
        self.acq_count = 0
        self.running = True
        self.armed = False
        self.last_triggered = False
        self._clear_due()

    def reset(self) -> None:
        self.records = {}
        self.acq_count = 0
        self.running = True
        self.armed = False
        self.last_triggered = False
        self._clear_due()

    def _clear_due(self) -> None:
        """No acquisition scheduled (completion: pending)."""
        #: The scheduled acquisition: when it is done, when it triggered, and what it keeps.
        self.due_ns: int | None = None
        self.trigger_ns: int | None = None
        self._due: tuple[dict[int, Record], int] | None = None
        #: Armed, the trigger not come yet: the channels to acquire once it does (None: those shown).
        self._awaiting: tuple[list[int] | None] | None = None

    # ---- the macro: the earlier one-component form expands into the modules -----------------------

    @classmethod
    def expand_macro(cls, data: dict[str, Any], spec: dict[str, Any]) -> None:
        """Expand an acquisition written the earlier way (no module named) into the modules.

        The parameters each module takes move to it under the module's names (``MACRO_MODULES``);
        the per-channel component wired to ``<acq>.in{n}`` becomes the ``vertical`` one, and a
        ``scope_probe`` named ``probe`` is put between it and the acquisition. Raises ValueError when
        the definition is not shaped so (write the modules out instead)."""
        params = dict(spec.get("params") or {})
        if any(key in params for key in MACRO_MODULES):
            return  # the modules are written out
        acq = str(spec["name"])
        comps = data.setdefault("components", [])
        chans = data.setdefault("channel_components", [])
        wiring = data.setdefault("wiring", {})
        inst_wires = wiring.setdefault("instrument", [])
        chan_wires = wiring.setdefault("channel", [])
        feed: tuple[int, str] | None = None
        for i, (src, dst) in enumerate(inst_wires):
            if dst == f"{acq}.in{{n}}" and src.startswith("ch{n}.") and src.endswith(".out"):
                feed = (i, src[len("ch{n}.") : -len(".out")])
        taken = {str(c.get("name")) for c in comps} | {f"ch.{c.get('name')}" for c in chans}
        clash = [n for n in (*MACRO_NAMES.values(), "ch.probe") if n in taken]
        if feed is None or clash:
            why = f"names {', '.join(clash)} are taken" if clash else f"nothing is wired to {acq}.in{{n}}"
            raise ValueError(
                f"{acq}: cannot expand the one-component acquisition ({why}); write the modules out"
            )
        modules = []
        for key, (type_name, moves) in MACRO_MODULES.items():
            moved = {new: params.pop(old) for old, new in moves.items() if old in params}
            modules.append({"name": MACRO_NAMES[key], "type": type_name, "params": moved})
        index, vertical = feed
        if not any(c.get("name") == vertical for c in chans):
            raise ValueError(f"{acq}: {vertical!r} wired to {acq}.in{{n}} is not a channel component")
        spec["params"] = {
            **{k: MACRO_NAMES[k] for k in MACRO_MODULES},
            "vertical": vertical,
            "probe": "probe",
            **params,
        }
        at = next(i for i, c in enumerate(comps) if c is spec) + 1
        comps[at:at] = modules
        after = next(i for i, c in enumerate(chans) if c.get("name") == vertical) + 1
        if chans[after - 1].get("type") == "scope_frontend":
            chans[after - 1]["type"] = "scope_vertical"  # the earlier name of the same component
        chans.insert(after, {"name": "probe", "type": "scope_probe", "params": {}})
        chan_wires.append([f"{vertical}.out", "probe.in"])
        inst_wires[index] = ["ch{n}.probe.out", f"{acq}.in{{n}}"]

    # ---- the modules ------------------------------------------------------------------------------

    def _module(self, key: str, name: str | None = None) -> Any:
        assert self.owner is not None
        name = name or str(self.param(key))
        comp = self.owner.components.get(name)
        if comp is None:
            raise ScpiError(-300, f"{self.uid}: {key} {name!r} is not a component of this instrument")
        return comp

    def horizontal(self) -> ScopeTimebase:
        return self._module("horizontal")  # type: ignore[no-any-return]

    def trigger_unit(self) -> ScopeTrigger:
        return self._module("trigger")  # type: ignore[no-any-return]

    def adc_unit(self) -> ScopeAdc:
        return self._module("adc")  # type: ignore[no-any-return]

    def vertical_of(self, n: int) -> ScopeVertical:
        return self._module("vertical", f"ch{n}.{self.param('vertical')}")  # type: ignore[no-any-return]

    def probe_ratio(self, n: int) -> float:
        return float(self._module("probe", f"ch{n}.{self.param('probe')}").param("ratio"))

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
        """Signal of channel n in display volts: the probe module's output."""
        return self.input(f"in{n}")

    def trigger_level(self) -> float:
        """The trigger level in display volts (the scope screen draws it)."""
        return self.trigger_unit().level()

    def timebase_scale(self) -> float:
        return float(self.horizontal().param("scale"))

    def displayed(self) -> list[int]:
        return [n for n in range(1, self._channels() + 1) if self._st(f"ch{n}.display")]

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
        return max(1, int(self.param("averages"))) if self.param("averaging") in (True, 1, "1", "ON") else 1

    def _jitter(self, t_start_ns: int, frame: int | None = None) -> float:
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
        key = stream_key(self.session.seed, self.uid, "jitter", self.acq_count)
        return jitter * float(gaussian(key, np.zeros(1))[0])

    def _noise(
        self, n: int, t_start_ns: int, dt: float, points: int, frame: int | None = None
    ) -> np.ndarray | None:
        """Input noise in display volts, a pure function of the seed, the channel and the key."""
        sigma = float(self.param("noise_rms")) * self.probe_ratio(n)
        if sigma <= 0:
            return None
        sigma /= math.sqrt(self._averages())
        if frame is not None:  # a displayed frame: noise of its own, no acquisition's stream used
            return sigma * gaussian(
                stream_key(self.session.seed, self.uid, "frame", n, frame), np.arange(points)
            )
        if self.param("noise_key") == "time":
            first = math.floor(t_start_ns * 1e-9 / dt)  # sample n of the absolute sample grid
            key = stream_key(self.session.seed, self.uid, "noise", n, dt, str(self.param("noise_stream")))
            return sigma * gaussian(key, first + np.arange(points))
        key = stream_key(self.session.seed, self.uid, "noise", n, self.acq_count)
        return sigma * gaussian(key, np.arange(points))

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
        self, channels: list[int], frame: int | None = None
    ) -> tuple[dict[int, Record], int, bool, int]:
        """:meth:`compute`, and the instant of the trigger (or of the record's middle) in ns."""
        points, dt, pre, post = self._timing()
        t0 = self.session.now_ns
        trig = self.trigger_unit()
        n_trig = trig.source_channel()
        if n_trig is None:
            k, triggered = pre, False
        else:
            k, triggered = trig.search(
                self._display_signal(n_trig), t0, dt, pre, points, n_trig, self.scale_offset(n_trig)[0]
            )
        start = k - pre
        t_start_ns = t0 + round(start * dt * 1e9)
        offsets = (start + np.arange(points)) * dt
        jitter = self._jitter(t_start_ns, frame)
        if jitter:
            offsets = offsets + jitter
        adc = self.adc_unit()
        records: dict[int, Record] = {}
        for n in channels:
            scale, offset = self.scale_offset(n)
            volts = self._display_signal(n).sample_rel(t0, offsets)
            noise = self._noise(n, t_start_ns, dt, points, frame)
            if noise is not None:
                volts = volts + noise
            records[n] = Record(
                channel=n,
                t_start_ns=t_start_ns,
                dt_s=dt,
                trigger_index=pre,
                volts=adc.quantize(volts, scale, offset),
                scale=scale,
                offset=offset,
                triggered=triggered,
            )
        if triggered or self.param("untriggered") == "record":
            used = (k + max(post, 0)) * dt
        else:  # the whole trigger search
            used = points * int(trig.param("search_windows")) * dt
        used = used * self._averages() + float(self.param("overhead_s"))
        return records, round(used * 1e9), triggered, t0 + round(k * dt * 1e9)

    def store(self, records: dict[int, Record], used: int, forced: bool = False) -> int:
        """Keep ``records`` as the latest acquisition and log its recipe (D36). Returns ``used``.

        ``forced``: read from a channel that had no record, whatever the run mode."""
        self.records.update(records)
        self.acq_count += 1
        self.session.invalidate()
        if self.owner is not None:
            # The acquisition's recipe: everything needed to regenerate it from the bench (D36).
            self.session.instrument_event(
                self.owner,
                "acquisition",
                {
                    "count": self.acq_count,
                    "forced": forced,
                    "channels": {
                        str(ch): {
                            "t_start_ns": rec.t_start_ns,
                            "dt_s": rec.dt_s,
                            "points": int(rec.volts.size),
                            "trigger_index": rec.trigger_index,
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
            self._schedule(channels, force)  # continuous runs (auto, normal) refresh at once
            return 0
        records, used, triggered = self.compute(self.displayed() if channels is None else channels)
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

    # ---- pending acquisitions (completion: pending) -------------------------------------------------

    def waiting(self) -> bool:
        """A single acquisition (or a digitize) not done yet."""
        return self.due_ns is not None or self._awaiting is not None or (self.armed and self._is("single"))

    def _schedule(self, channels: list[int] | None, force: bool) -> None:
        """Arm an acquisition that finishes in virtual time: when the record after the trigger is
        full, or, without a trigger, once one comes (``retry_pending``)."""
        assert self.owner is not None
        if self.due_ns is not None:
            return  # already on its way
        records, used, triggered, trigger_ns = self._acquire(
            self.displayed() if channels is None else channels
        )
        self.last_triggered = triggered
        if not (force or triggered or self._is("auto")):
            self._awaiting = (channels,)
            self.owner.block(self.uid, str(self.param("no_trigger")) == "wait")
            return
        self._awaiting = None
        now = self.owner.now_ns()
        self.due_ns, self.trigger_ns, self._due = now + used, trigger_ns, (records, used)
        self.owner.block(self.uid, False)
        self.owner.register_pending(
            self.uid, self.due_ns, self._complete, waited=bool(self.param("opc_waits"))
        )

    def _complete(self) -> None:
        """The scheduled acquisition is done: keep its records; a single acquisition then stops."""
        assert self._due is not None
        records, used = self._due
        self._clear_due()
        self.store(records, used)
        if self._is("single"):
            self._finish_single()
        else:
            self.armed = False

    def _keep_last_frame(self) -> None:
        """A running scope has been acquiring all along: before a single acquisition (or a stop)
        replaces the run, the frame it shows now is its latest acquisition, which a read gets
        until the single one is done (completion: pending)."""
        records, used, triggered = self.compute(self.displayed())
        if records and (triggered or self._is("auto")):
            self.store(records, used)

    def _cancel(self) -> None:
        """Stop waiting: nothing scheduled, nothing blocking ``*OPC?``."""
        if self.owner is not None:
            self.owner.cancel_pending(self.uid)
            self.owner.block(self.uid, False)
        self._clear_due()

    def retry_pending(self) -> None:
        """Armed for a trigger that had not come: look again now (the signal may have changed)."""
        if not self.pending_mode() or self.due_ns is not None:
            return
        if self._awaiting is not None:
            self._schedule(self._awaiting[0], force=False)
        elif self.armed and self._is("single"):  # armed from the panel, waiting for its trigger
            self._schedule(None, force=False)

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
        return self.owner.format_setting(path)

    @op("run_mode")
    def run_mode(self, query: bool = False, mode: str | None = None, **_: Any) -> OpResult:
        """``:TRIGger:MODE``, ``TRMD``: a single mode arms one acquisition (and acquires at once if
        it can), a force mode takes one frame and stops, a stop mode stops."""
        if query:
            used = self.poll()
            return OpResult(self.mode_text(), used)
        if mode is None:
            raise ScpiError(-109)
        self._cancel()  # a new mode replaces whatever acquisition was waiting
        if self.pending_mode() and self.is_running() and (self._is("auto") or self._is("normal")):
            self._keep_last_frame()
        self._set_mode(mode)
        if self._is("force", mode):
            used = self.acquire_now(force=True)
            self.running, self.armed = False, False
            return OpResult(duration_ns=used)
        if self._is("single", mode):
            self.running, self.armed = True, True
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
        if self._is("single"):
            self.armed = True
            return OpResult(duration_ns=self.acquire_now())
        return OpResult()

    @op("stop")
    def stop(self, **_: Any) -> OpResult:
        self._cancel()
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
            if self.due_ns is not None:
                used += self.owner.wait_until(self.due_ns)
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

    @op("measurement", fields=("kind", "unit", "status"))
    def measurement(self, channel: int | None = None, kind: str | None = None, **_: Any) -> OpResult:
        """A named measurement on the channel's record (``C1:PAVA? AMPL``): ``measurements`` maps the
        model's names to the engine's (VPP, VMAX, VMIN, VAMPlitude, VRMS, VAVerage, FREQuency,
        PERiod) and units. The reply is the value in the definition's number format; the reply
        template also gets {kind}, {unit} and {status}, e.g. '{header} {kind},{value} {unit},{status}'."""
        if channel is None:
            raise ScpiError(-300, f"{self.uid}: a measurement names its channel (<ch>)")
        table = {str(k).upper(): v for k, v in (self.param("measurements") or {}).items()}
        name = (kind or "").strip().upper()
        if name not in table:
            raise ScpiError(-224, f"measurement {kind} not modeled by SimuBench")
        engine_name, unit = table[name]
        rec, used = self.measure_record(channel)
        value = _measure(str(engine_name), rec.volts, rec.dt_s)
        ok, bad = self.param("status")
        assert self.owner is not None
        f = self.owner.definition.format
        text = (
            (V.format_engineering(value) if f.number == "engineering" else V.format_float(value, f.digits))
            if math.isfinite(value)
            else str(self.param("invalid_value")).encode()
        )
        fields = {"kind": name, "unit": str(unit), "status": str(ok if math.isfinite(value) else bad)}
        return OpResult(text, used, fields)

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
        if self.pending_mode() and self.due_ns is not None and self.owner is not None:
            used += self.owner.wait_until(self.due_ns)  # a digitize returns once its record is full
        return OpResult(duration_ns=used)

    def _source(self) -> int:
        return channel_index(self._st("waveform.source"))

    def _record(self) -> Record:
        rec = self.records.get(self._source())
        if rec is None:
            raise ScpiError(-230, "no acquisition for this source; send :DIGitize first")
        return rec

    def _codes(self, rec: Record) -> tuple[np.ndarray, float, int]:
        fmt = self._st("waveform.format")
        full = DIVS_V / 2 * rec.scale
        span = 128 if fmt == "BYTE" else 32768
        unsigned = bool(self._st("waveform.unsigned"))
        codes = np.round((rec.volts - rec.offset) / full * span)
        lo, hi = -span, span - 1
        codes = np.clip(codes, lo, hi).astype(np.int64)
        yref = 0
        if unsigned:
            codes = codes + span
            yref = span
        return codes, full / span, yref

    @op("preamble")
    def preamble(self, **_: Any) -> OpResult:
        rec = self._record()
        _, yinc, yref = self._codes(rec)
        fmt = {"BYTE": 0, "WORD": 1, "ASCii": 4}[self._st("waveform.format")]
        pts = rec.volts.size
        xorg = -rec.trigger_index * rec.dt_s
        fields = [
            str(fmt).encode(),
            b"0",
            str(pts).encode(),
            b"1",
            V.format_float(rec.dt_s),
            V.format_float(xorg),
            b"0",
            V.format_float(yinc),
            V.format_float(rec.offset),
            str(yref).encode(),
        ]
        return OpResult(b",".join(fields))

    # ---- measurements -----------------------------------------------------------------------------

    @op("measure")
    def measure(
        self, suffixes: dict[str, int] | None = None, source: str | None = None, **kw: Any
    ) -> OpResult:
        """Automatic measurements on the latest record.

        VPP, VMAX, VMIN, VAMPlitude, VRMS, VAVerage, FREQuency and PERiod.
        """
        what = kw.get("what") or ""
        n = self._source()
        if source:
            name = source.upper()
            m = name.removeprefix("CHANNEL").removeprefix("CHAN")
            if not m.isdigit():
                raise ScpiError(-224, f"unknown source {source!r}")
            n = int(m)
        rec, _ = self.measure_record(n)
        v = rec.volts
        value = _measure(what, v, rec.dt_s)
        return OpResult(V.format_float(value))


#: Bins of a record's histogram, from which the top and the base are read. Fine against any
#: ADC grid a record can have, so a modal bin is one level and its mean is that level.
TOP_BASE_BINS = 1000
#: A half of that histogram holds a level when its fullest bin holds at least this times the
#: average of the half's occupied bins; a ramp fills it evenly and has no levels to tell.
TOP_BASE_PEAK = 2.0
#: Half the frequency counter's hysteresis band, of top minus base: a crossing counts only
#: after the record reached below the band, so noise near the middle cannot fake one.
FREQ_HYSTERESIS = 0.1


def _top_base(v: np.ndarray) -> tuple[float, float] | None:
    """Top and base of a record: the modes of its histogram in the upper and the lower half.

    AMPLitude and the frequency counter take the levels the waveform rests on, not the halves'
    medians (a sine's upper median sits at 0.707 of its peak) and not the extremes (a noise
    spike would count as amplitude). Each half must form a peak — its fullest bin holding at
    least twice the average of the half's occupied bins — otherwise no levels can be told (a
    ramp or triangle fills the histogram evenly) and None comes back; the caller falls back to
    the extremes. The level is the mean of the samples in its modal bin.
    """
    vmax, vmin = float(v.max()), float(v.min())
    if not vmax > vmin:
        return None
    counts, edges = np.histogram(v, bins=TOP_BASE_BINS, range=(vmin, vmax))
    centres = (edges[:-1] + edges[1:]) / 2
    mid = (vmax + vmin) / 2
    levels: list[float] = []
    for above in (True, False):
        idx = np.nonzero(centres > mid if above else centres < mid)[0]
        if not idx.size:
            return None
        top = idx[np.argmax(counts[idx])]
        taken = counts[idx][counts[idx] > 0]
        if counts[top] < TOP_BASE_PEAK * taken.mean() or taken.size < 2:
            return None
        sel = (v >= edges[top]) & (v <= edges[top + 1])
        levels.append(float(v[sel].mean()))
    return levels[0], levels[1]


def _period(v: np.ndarray, dt: float) -> float | None:
    """The record's period, or None when it holds no complete cycle.

    The counter runs on the hysteresis band the top and the base set around the middle (the
    10 % / 50 % / 90 % reference levels; frequency counts the middle one): a rising crossing
    counts only after the record last reached below the band's lower edge, so noise and
    quantization near the middle cannot fake crossings, and a swing that never gets above the
    band's upper edge counts for nothing. Each crossing is interpolated on the middle for
    sub-sample time; the period is the least-squares slope of crossing time against crossing
    number, which noise on the single crossings averages out of. The band comes from the top
    and the base, falling back to the extremes when the histogram forms no peaks (a triangle
    has no levels to tell but a well-defined span).
    """
    levels = _top_base(v)
    if levels is None:
        vmax, vmin = float(v.max()), float(v.min())
        levels = (vmax, vmin) if vmax > vmin else None
    if levels is None:
        return None
    top, base = levels
    mid = (top + base) / 2
    h = FREQ_HYSTERESIS * (top - base)
    n = v.size
    idx = np.arange(n)
    last_below = np.maximum.accumulate(np.where(v <= mid - h, idx, -1))
    last_above = np.maximum.accumulate(np.where(v >= mid + h, idx, -1))
    if v[0] <= mid:  # the record opens in the lower half: its first rise is countable
        last_below = np.maximum(last_below, 0)
    above_before = np.concatenate(([-1], last_above[:-1]))  # the hit's own sample does not count
    hit = np.nonzero((last_below > above_before) & (v >= mid + h))[0]
    if hit.size < 2:
        return None
    times = np.empty(hit.size)
    for j, i in enumerate(hit):
        seg = v[last_below[i] : i + 1]  # the swing: from not above the middle to above the band
        k = last_below[i] + int(np.nonzero((seg[:-1] <= mid) & (seg[1:] > mid))[0][0])
        frac = (mid - float(v[k])) / (float(v[k + 1]) - float(v[k]))
        times[j] = (k + min(max(frac, 0.0), 1.0)) * dt
    seq = np.arange(times.size, dtype=float)
    seq -= seq.mean()
    slope = float(np.dot(seq, times - times.mean()) / np.dot(seq, seq))
    return slope if slope > 0 else None


def _measure(what: str, v: np.ndarray, dt: float) -> float:
    """VPP, VMAX, VMIN, VAVerage, VRMS, VAMPlitude (top - base), FREQuency and PERiod.

    Unmeasurable — no complete cycle in the record — comes back as NaN, which the callers
    report as the definition's invalid value.
    """
    vmax, vmin = float(v.max()), float(v.min())
    if what == "VPP":
        return vmax - vmin
    if what == "VMAX":
        return vmax
    if what == "VMIN":
        return vmin
    if what == "VAVerage":
        return float(v.mean())
    if what == "VRMS":
        return float(np.sqrt(np.mean(v**2)))
    if what == "VAMPlitude":
        levels = _top_base(v)
        return levels[0] - levels[1] if levels else vmax - vmin
    if what in ("FREQuency", "PERiod"):
        period = _period(v, dt)
        if period is None:
            return math.nan
        return 1.0 / period if what == "FREQuency" else period
    raise ScpiError(-113, f"measurement {what!r} not supported")
