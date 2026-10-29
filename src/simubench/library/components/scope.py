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


@component("scope_frontend")
class ScopeFrontend(Component):
    """Coupling and bandwidth of one channel, applied to the voltage at the input connector.

    Analog first-order filters (physics, D13): AC coupling is a ``ac_corner`` high-pass and the
    bandwidth a first-order low-pass at ``bandwidth`` (or ``bw_limit_hz`` when the limit is on).
    """

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    defaults = {
        "coupling": "DC",
        "bandwidth": 200e6,
        "bw_limit": False,
        "bw_limit_hz": 20e6,
        "ac_corner": 10.0,
    }
    units = {"bandwidth": "Hz", "bw_limit_hz": "Hz", "ac_corner": "Hz"}

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


@component("scope_acquisition")
class ScopeAcquisition(Component):
    """Timebase, edge trigger, run control, sampling, noise and the ADC for all channels of a scope.

    One component for every oscilloscope model, set by parameters (instrument-authoring.md 3.2):

    * **record length** ``record_length``: ``fixed`` (``points`` over ten divisions), ``exact``
      (the rate is ``points / 10 div`` up to ``max_rate``) or ``rate_ladder`` (that rate rounded
      down to a 1-2-5 step; samples ``1 / rate`` apart). Channels of a ``rate_pairs`` pair share an
      ADC: with both on the top rate is ``max_rate_paired``. The trigger sits at the centre, or
      ``delay`` seconds before it when that is bound;
    * **trigger** ``trigger_source``, ``trigger_level``, ``trigger_slope`` (``rising`` and
      ``falling`` list the slope names that take each edge); a level or slope per channel is bound
      with the channel number written ``{n}``: ``$ch{n}.trigger_level``. A source that is not a
      channel (external, line) never triggers;
    * **run control** ``mode`` and the values that mean ``auto``, ``normal``, ``single``, ``stop``
      and ``force`` (one frame whatever the trigger, then stop); ``after_single`` is the mode a
      completed single acquisition leaves (empty: the mode stays and the scope stops).
      ``reacquire`` says when a read gets a new acquisition from a running scope: ``explicit``
      (only a digitize command), ``every_read`` or ``on_read_start`` (a read from the first point;
      later pieces read the same record). A pending single acquisition is retried on every read.
      ``missing: acquire`` reads a channel that has no record yet, ``error`` refuses it;
    * **ADC** ``adc_bits`` (0: not quantized), ``adc_codes_per_div`` (0: ``2**bits / adc_divs``)
      and ``adc_offset`` (the offset setting added to or subtracted from the volts);
    * **noise** ``noise_rms`` (input-referred white noise, times the probe ratio), keyed by
      ``noise_key``: ``time`` (the absolute sample instants, D46; ``noise_stream`` names a setting
      that selects another stream) or ``acquisition`` (the acquisition count, so pauses of any
      real length between commands give the same numbers); ``trigger_jitter_s``; ``averaging``
      with ``averages`` divides the noise by the square root of the count and takes that many
      acquisitions' time.

    Per-channel settings are ``ch<n>.scale``, ``ch<n>.offset``, ``ch<n>.probe`` and
    ``ch<n>.display``. Model-specific commands (Siglent's WAVEDESC, LeCroy's INSPECT?) are subclasses.
    """

    ports: dict[str, PortSpec] = {}
    port_pattern = r"in\d+"  # inputs in1..inN are created by the instrument wiring
    defaults = {
        "overhead_s": 1e-3,
        "search_windows": 4,
        "timebase": "$timebase.scale",
        "record_length": "fixed",  # fixed | exact | rate_ladder
        "points": "$waveform.points",
        "max_rate": 0.0,  # 0: no limit
        "max_rate_paired": 0.0,
        "rate_pairs": [],  # e.g. [[1, 2], [3, 4]]
        "delay": None,  # unbound: the trigger at the centre of the record
        "trigger_source": "$trigger.source",
        "trigger_level": "$trigger.level",
        "trigger_slope": "$trigger.slope",
        "rising": RISING,
        "falling": FALLING,
        "mode": "$trigger.mode",
        "auto": ["AUTO"],
        "normal": ["NORMal"],
        "single": [],
        "stop": [],
        "force": [],
        "after_single": "",
        "reacquire": "explicit",  # explicit | every_read | on_read_start
        "missing": "error",  # error | acquire
        "untriggered": "search",  # the time an untriggered acquisition takes: search | record
        "adc_bits": 0,
        "adc_codes_per_div": 0.0,
        "adc_divs": 10.0,
        "adc_offset": "add",  # add | subtract
        "noise_rms": 0.0,
        "noise_key": "acquisition",  # acquisition | time
        "noise_stream": "",
        "trigger_jitter_s": 0.0,
        "averaging": False,
        "averages": 1,
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.records: dict[int, Record] = {}
        self.acq_count = 0
        self.running = True
        self.armed = False
        self.last_triggered = False

    def reset(self) -> None:
        self.records = {}
        self.acq_count = 0
        self.running = True
        self.armed = False
        self.last_triggered = False

    # ---- helpers ----------------------------------------------------------------------------------

    def _st(self, path: str) -> Any:
        assert self.owner is not None
        return self.owner.get_state(path)

    def _channels(self) -> int:
        assert self.owner is not None
        return self.owner.channel_count

    def channel_param(self, key: str, n: int | None) -> Any:
        """A parameter that may name a channel's setting: ``$ch{n}.trigger_level`` for channel n."""
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
        """Signal of channel n in display volts: front-end output times the probe ratio."""
        return self.input(f"in{n}").scale(float(self._st(f"ch{n}.probe")))

    def trigger_level(self) -> float:
        """The trigger level in display volts (the scope screen draws it)."""
        level = self.channel_param("trigger_level", trigger_channel(self.param("trigger_source")))
        return 0.0 if level is None else float(level)

    def displayed(self) -> list[int]:
        return [n for n in range(1, self._channels() + 1) if self._st(f"ch{n}.display")]

    # ---- record length ------------------------------------------------------------------------------

    def sample_rate(self) -> float:
        window = DIVS_H * float(self.param("timebase"))
        depth = memory_depth(self.param("points"))
        rule = str(self.param("record_length"))
        if rule == "fixed":
            return depth / window
        top = float(self.param("max_rate")) or math.inf
        on = set(self.displayed())
        if any(set(pair) <= on for pair in self.param("rate_pairs") or ()):
            top = float(self.param("max_rate_paired")) or top
        if rule == "rate_ladder":
            return min(top, ladder_down(depth / window))
        return min(top, depth / window)

    def _timing(self) -> tuple[int, float, int, int]:
        """Points, sample interval, samples before the trigger and samples from it on."""
        window = DIVS_H * float(self.param("timebase"))
        rule = str(self.param("record_length"))
        if rule == "fixed":
            points = memory_depth(self.param("points"))
            dt = window / points
        else:
            rate = self.sample_rate()
            points = max(2, round(rate * window))
            dt = 1.0 / rate if rule == "rate_ladder" else window / points
        # Sample 0 is at delay - 5 div: the trigger sits (5 div - delay) / dt in (no delay: the centre).
        delay = self.param("delay")
        pre = points // 2 if delay in (None, "") else round((window / 2 - float(delay)) / dt)
        return points, dt, pre, points - pre

    # ---- trigger --------------------------------------------------------------------------------------

    def _trigger_search(
        self, sig: Signal, t0: int, dt: float, pre: int, points: int, n: int
    ) -> tuple[int, bool]:
        """First qualifying edge at or after ``pre`` samples (enough history before the trigger).

        Returns (trigger sample index measured from t0, triggered?)."""
        level = float(self.channel_param("trigger_level", n))
        slope = str(self.channel_param("trigger_slope", n)).upper()
        hyst = 0.02 * DIVS_V * float(self._st(f"ch{n}.scale"))
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

    # ---- sampling, noise and the ADC ----------------------------------------------------------------

    def _averages(self) -> int:
        return max(1, int(self.param("averages"))) if self.param("averaging") in (True, 1, "1", "ON") else 1

    def _jitter(self, t_start_ns: int) -> float:
        jitter = float(self.param("trigger_jitter_s"))
        if not jitter:
            return 0.0
        if self.param("noise_key") == "time":
            key = stream_key(self.session.seed, self.uid, "jitter")
            return jitter * float(gaussian(key, np.array([t_start_ns]))[0])
        key = stream_key(self.session.seed, self.uid, "jitter", self.acq_count)
        return jitter * float(gaussian(key, np.zeros(1))[0])

    def _noise(self, n: int, t_start_ns: int, dt: float, points: int) -> np.ndarray | None:
        """Input noise in display volts, a pure function of the seed, the channel and the key."""
        sigma = float(self.param("noise_rms")) * float(self._st(f"ch{n}.probe"))
        if sigma <= 0:
            return None
        sigma /= math.sqrt(self._averages())
        if self.param("noise_key") == "time":
            first = math.floor(t_start_ns * 1e-9 / dt)  # sample n of the absolute sample grid
            key = stream_key(self.session.seed, self.uid, "noise", n, dt, str(self.param("noise_stream")))
            return sigma * gaussian(key, first + np.arange(points))
        key = stream_key(self.session.seed, self.uid, "noise", n, self.acq_count)
        return sigma * gaussian(key, np.arange(points))

    def adc_codes_per_div(self) -> float:
        bits = int(self.param("adc_bits"))
        return float(self.param("adc_codes_per_div")) or (1 << bits) / float(self.param("adc_divs"))

    def _adc(self, volts: np.ndarray, scale: float, offset: float) -> np.ndarray:
        """What the ADC keeps: the codes of ``adc_bits``, back in volts."""
        bits = int(self.param("adc_bits"))
        if bits <= 0:
            return volts
        per_div = self.adc_codes_per_div()
        half = 1 << (bits - 1)
        off = offset if str(self.param("adc_offset")).lower() == "add" else -offset
        codes = np.clip(np.round((volts + off) / scale * per_div), -half, half - 1)
        return codes / per_div * scale - off

    # ---- acquisition ------------------------------------------------------------------------------

    def compute(self, channels: list[int]) -> tuple[dict[int, Record], int, bool]:
        """Sample the listed channels at the current instant without changing any state.

        Returns the records, the virtual time an acquisition takes (ns) and whether it triggered.
        """
        points, dt, pre, post = self._timing()
        t0 = self.session.now_ns
        n_trig = trigger_channel(self.param("trigger_source"))
        if n_trig is None:
            k, triggered = pre, False
        else:
            k, triggered = self._trigger_search(self._display_signal(n_trig), t0, dt, pre, points, n_trig)
        start = k - pre
        t_start_ns = t0 + round(start * dt * 1e9)
        offsets = (start + np.arange(points)) * dt
        jitter = self._jitter(t_start_ns)
        if jitter:
            offsets = offsets + jitter
        records: dict[int, Record] = {}
        for n in channels:
            scale, offset = float(self._st(f"ch{n}.scale")), float(self._st(f"ch{n}.offset"))
            volts = self._display_signal(n).sample_rel(t0, offsets)
            noise = self._noise(n, t_start_ns, dt, points)
            if noise is not None:
                volts = volts + noise
            records[n] = Record(
                channel=n,
                t_start_ns=t_start_ns,
                dt_s=dt,
                trigger_index=pre,
                volts=self._adc(volts, scale, offset),
                scale=scale,
                offset=offset,
                triggered=triggered,
            )
        if triggered or self.param("untriggered") == "record":
            used = (k + max(post, 0)) * dt
        else:  # the whole trigger search
            used = points * int(self.param("search_windows")) * dt
        used = used * self._averages() + float(self.param("overhead_s"))
        return records, round(used * 1e9), triggered

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

    def acquire_now(self, channels: list[int] | None = None, force: bool = False) -> int:
        """One acquisition attempt as the run mode allows it (``force``: whatever the trigger and the
        run state). Returns the virtual time used (ns)."""
        if not force and not self.is_running():
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
        return self.acquire_now() if self.armed and self._is("single") else 0

    def read_record(self, channel: int, start: bool = True) -> tuple[Record | None, int]:
        """The record a read uses, and the virtual time getting it took (``reacquire``, ``missing``).

        ``start``: the read starts a transfer (``on_read_start`` acquires anew only then)."""
        used = 0
        how = self.param("reacquire")
        fresh = how == "every_read" or (how == "on_read_start" and start)
        running = self.is_running() and (self._is("auto") or self._is("normal"))
        if (fresh and running) or (self.armed and self._is("single")):
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
        """``:TRIGger:RUN``: run again; in a single mode, arm one acquisition."""
        self.running = True
        if self._is("single"):
            self.armed = True
            return OpResult(duration_ns=self.acquire_now())
        return OpResult()

    @op("stop")
    def stop(self, **_: Any) -> OpResult:
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
        return OpResult(duration_ns=self.poll())

    @op("opc")
    def opc(self, query: bool = False, **_: Any) -> OpResult:
        """``*OPC?`` answers ``1`` once a pending single acquisition is done; ``*OPC`` sets the ESR bit."""
        used = self.poll()
        if query:
            return OpResult(b"1", used)
        assert self.owner is not None
        self.owner.esr |= 1
        return OpResult(duration_ns=used)

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
        return OpResult(duration_ns=self.acquire_now(chans))

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


def _measure(what: str, v: np.ndarray, dt: float) -> float:
    vmax, vmin = float(v.max()), float(v.min())
    mid = (vmax + vmin) / 2
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
        hi = v[v >= mid]
        lo = v[v < mid]
        return float(np.median(hi) - np.median(lo)) if hi.size and lo.size else vmax - vmin
    if what in ("FREQuency", "PERiod"):
        s = (v - mid) < 0  # not signbit: quantized records hold -0.0, which is not below the middle
        rising = np.nonzero(s[:-1] & ~s[1:])[0]
        if rising.size < 2:
            return math.nan
        # Interpolate each crossing for sub-sample accuracy.
        x0, x1 = v[rising] - mid, v[rising + 1] - mid
        t = (rising + (-x0) / (x1 - x0)) * dt
        period = float(np.mean(np.diff(t)))
        return 1.0 / period if what == "FREQuency" else period
    raise ScpiError(-113, f"measurement {what!r} not supported")
