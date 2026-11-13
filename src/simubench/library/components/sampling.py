"""Equivalent-time sampling (#1094): a clock recovered from the data, and the records a sampling
oscilloscope builds by taking a few samples per trigger and placing them in time against the
pattern (a DCA's "pattern lock").

A sampling scope never sees a single-shot event: it samples a repeating waveform a little later
each time and assembles the record from many triggers. The bench's signals are already line
spectra, so a repeating pattern is exactly periodic and every sample can be computed directly at
its place in the pattern — the record is what the scope would assemble, with the sampler's noise
and timebase jitter drawn anew per sample. What is not modelled (product-plan.md 6): eye diagrams
and their measurements, jitter and noise decomposition, TDECQ, mask tests, equalizers; the clock
recovery's loop dynamics (it either locks or it does not); a waveform that is not periodic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ...engine.component import Component, NoiseSource, OpResult, PortSpec, component, op
from ...engine.noise import gaussian, stream_key
from ...engine.signal import Signal
from ...engine.stream import beyond_model
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope import Record

#: Samples one acquisition may compute (record points times averages): beyond it the request is
#: refused (architecture.md 2.3), not truncated.
MAX_SAMPLES = 1 << 24
#: A repetition rate below this many symbols' worth is not a pattern the scope can lock to.
MAX_PATTERN = 1 << 16


@dataclass(frozen=True)
class Lock:
    """What a locked clock recovery knows: the symbol rate it recovered (Hz), the pattern's length in
    symbols and its repetition period (s)."""

    rate: float
    pattern_length: int
    period: float


def repetition_rate(sig: Signal, rel_floor: float = 1e-9) -> float | None:
    """The fundamental every line of ``sig`` is a multiple of (Hz): the pattern's repetition rate.
    None without lines, or when the lines share no common fundamental (not a repeating waveform)."""
    merged = sig.merged()
    amp = np.abs(merged.amp)
    if not amp.size or amp.max() <= 0:
        return None
    freqs = np.sort(merged.freq[(amp > rel_floor * amp.max()) & (merged.freq > 0)])
    if not freqs.size:
        return None
    g = float(freqs[0])
    for f in freqs[1:]:
        g = _float_gcd(g, float(f))
        if g <= 0 or freqs[-1] / g > MAX_PATTERN * 64:
            return None
    return g


def _float_gcd(a: float, b: float, rel: float = 1e-9) -> float:
    """Greatest common divisor of two frequencies, to a relative tolerance."""
    scale = max(a, b)
    while b > rel * scale:
        a, b = b, math.fmod(a, b)
        if abs(b - a) <= rel * scale:  # fmod's rounding near a full multiple
            b = 0.0
    return a


def symbol_multiple(sig: Signal, f0: float, rel_floor: float = 1e-6) -> int | None:
    """The symbol rate as a multiple of the repetition rate ``f0``, from the pattern's spectrum: a
    pulse train of whole symbols has nulls at every multiple of its symbol rate (the pulse's sinc),
    so the smallest k ≥ 2 whose line is missing while lines exist above it gives the rate. None
    when the spectrum shows no such null (too few lines kept, or not a symbol stream)."""
    merged = sig.merged()
    amp = np.abs(merged.amp)
    if not amp.size:
        return None
    strong = amp > rel_floor * amp.max()
    present = {round(f / f0) for f in merged.freq[strong & (merged.freq > 0)]}
    if not present:
        return None
    top = max(present)
    return next((k for k in range(2, top) if k not in present and any(m > k for m in present)), None)


@component("clock_recovery")
class ClockRecovery(Component):
    """A clock recovered from the data on ``in``: the trigger of a sampling scope with no separate
    clock. It locks when the data's symbol rate lies within ``lock_range`` (relative) of the nominal
    ``rate`` and between ``min_rate`` and ``max_rate``; the pattern's length comes with it (the
    repetition rate's multiple), which pattern lock uses. The symbol rate is read from the pattern's
    spectrum (:func:`symbol_multiple`); when the spectrum does not show it, the multiple of the
    repetition rate nearest the nominal rate is taken. The loop is not simulated: ``loop_bandwidth``
    is kept and answered, the recovered clock follows the data exactly (no tracking error, no
    transfer of jitter)."""

    ports = {"in": PortSpec("in", "signal")}
    units = {"rate": "HZ", "min_rate": "HZ", "max_rate": "HZ", "loop_bandwidth": "HZ"}
    defaults = {
        "rate": 26.5625e9,  # nominal symbol rate the recovery searches at, Bd
        "lock_range": 1e-3,  # how far (relative) the data's rate may be from the nominal one and still lock
        "min_rate": 1e6,  # lowest symbol rate the recovery locks to, Bd
        "max_rate": 1.2e11,  # highest symbol rate the recovery locks to, Bd
        "loop_bandwidth": 4e6,  # the loop's bandwidth, Hz (kept and answered; the loop is not simulated)
        "enabled": True,  # the recovery is on (off: never locked)
    }

    def lock(self) -> Lock | None:
        """The lock the recovery holds now, None when it is not locked."""
        if not bool(self.param("enabled")):
            return None
        sig = self.input("in")
        f0 = repetition_rate(sig)
        nominal = float(self.param("rate"))
        if f0 is None or nominal <= 0:
            return None
        k = symbol_multiple(sig, f0)
        if k is None:
            k = max(1, round(nominal / f0))
        rate = k * f0
        if not float(self.param("min_rate")) <= rate <= float(self.param("max_rate")):
            return None
        if abs(rate - nominal) > float(self.param("lock_range")) * nominal:
            return None
        if k > MAX_PATTERN:
            return None
        return Lock(rate, k, 1.0 / f0)

    def _answer(self, value: float) -> OpResult:
        assert self.owner is not None
        return OpResult(self.owner.format_number(value))

    @op("locked")
    def op_locked(self, **_: Any) -> OpResult:
        """1 when the recovery is locked, else 0."""
        return OpResult(b"1" if self.lock() is not None else b"0")

    @op("recovered_rate")
    def op_recovered_rate(self, **_: Any) -> OpResult:
        """The recovered symbol rate; not locked: -230 (data corrupt or stale)."""
        found = self.lock()
        if found is None:
            raise ScpiError(-230, "clock recovery is not locked")
        return self._answer(found.rate)

    @op("pattern_length")
    def op_pattern_length(self, **_: Any) -> OpResult:
        """The pattern's length in symbols; not locked: -230."""
        found = self.lock()
        if found is None:
            raise ScpiError(-230, "clock recovery is not locked")
        return OpResult(str(found.pattern_length).encode())


@component("sampling_acquisition")
class SamplingAcquisition(Component):
    """The acquisition of an equivalent-time sampling scope with pattern lock.

    Trigger: the ``clock`` (a ``clock_recovery``) locked to the data; with ``pattern_lock`` the
    record's time zero is the pattern's start and the record repeats with the pattern. The record
    spans ``divs`` divisions of ``scale`` from ``position`` (s after the pattern start) in
    ``points`` samples; each sample is the channel's signal (wired to ``in<n>``, after its
    ``vertical``, a ``scope_vertical`` giving scale, offset and the sampler's noise per division)
    at its instant plus the timebase jitter (``jitter_s`` rms), plus the noise of the vertical and
    of the channel components named in ``noise_from`` (their ``output_noise_rms``, e.g. an
    ``optical_receiver``), added in power. ``averages`` (while ``average``) records are averaged:
    noise falls by √N, jitter is averaged into the shape as a real scope's average does.

    An acquisition takes ``points × averages / sample_rate`` of virtual time (the sampler takes
    ``sample_rate`` samples per second) plus ``overhead_s``. Not locked, or without pattern lock
    (eye mode, not modelled), it is refused with ``unlocked_error``. ``adc`` (optional) quantizes.

    The surface a scope's measurements, waveform transfer and screen read is the real-time
    acquisition's: ``read_record``, ``transfer_record``, ``measure_record``, ``displayed``,
    ``compute``, ``records``, ``acq_count``.
    """

    port_pattern = r"in\d+"  # inputs in1..inN are created by the instrument wiring
    pattern_port = PortSpec("in", "signal")  # each channel's signal after its vertical
    refs = {
        "clock": ("clock_recovery", "instrument"),
        "vertical": ("scope_vertical", "channel"),
        "adc": ("scope_adc", "instrument"),
    }
    optional_refs = frozenset({"adc"})
    units = {"scale": "s", "position": "s", "jitter_s": "s", "sample_rate": "Hz", "overhead_s": "s"}
    defaults = {
        "clock": "",  # the clock_recovery component that triggers the sampler
        "vertical": "",  # the per-channel scope_vertical component (its name without ch<n>.)
        "adc": "",  # an optional scope_adc that quantizes the samples
        "noise_from": [],  # per-channel components whose output_noise_rms adds to the sampler's (e.g. orx)
        "scale": 1e-11,  # s/div
        "position": 0.0,  # s from the pattern's start to the record's first sample
        "divs": 10.0,  # horizontal divisions the record spans
        "points": 1024,  # samples in a record
        "average": False,  # averaging on
        "averages": 16,  # records averaged while averaging is on
        "pattern_lock": True,  # pattern lock on (off: eye mode, not modelled)
        "jitter_s": 0.0,  # rms timebase jitter of each sample, s
        "sample_rate": 250e3,  # samples the sampler takes per second (sets the acquisition time)
        "overhead_s": 1e-3,  # virtual time an acquisition takes besides its samples, s
        "unlocked_error": -230,  # the error a read or acquisition gets when not locked or not in pattern lock
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.records: dict[int, Record] = {}
        self.acq_count = 0
        self.running = True
        self.armed = False

    def reset(self) -> None:
        self.records = {}
        self.acq_count = 0
        self.running = True
        self.armed = False

    # ---- what the scope's other blocks read ----------------------------------------------------

    def displayed(self) -> list[int]:
        assert self.owner is not None
        return [n for n in range(1, self.owner.channel_count + 1) if self.owner.get_state(f"ch{n}.display")]

    def timebase_scale(self) -> float:
        return float(self.param("scale"))

    def probe_ratio(self, channel: int) -> float:
        return 1.0

    def adc_unit(self) -> Component:
        adc = self.ref("adc")
        if adc is None:
            raise ScpiError(-300, f"{self.uid}: no ADC declared")
        return adc

    def adc_codes_per_div(self) -> float:
        adc: Any = self.adc_unit()
        return float(adc.codes_per_div())

    def shows_live(self) -> bool:
        return self.running

    def _is(self, key: str, value: Any = None) -> bool:
        """The real-time acquisition's run-mode test; a sampling scope has no auto/normal/single."""
        return False

    def trigger_level(self) -> float:
        return 0.0

    def clock_unit(self) -> Any:
        """The clock_recovery the ``clock`` ref names."""
        return self.ref("clock")

    def _vertical(self, n: int) -> Any:
        return self.peer(f"ch{n}.{self.param('vertical')}", required=True)

    # ---- acquisition ---------------------------------------------------------------------------

    def _grid(self) -> tuple[int, float, float]:
        points = int(self.param("points"))
        if points < 2:
            raise ScpiError(-222, "a record needs at least 2 points")
        dt = float(self.param("divs")) * float(self.param("scale")) / points
        return points, dt, float(self.param("position"))

    def _averages(self) -> int:
        return max(1, int(self.param("averages"))) if bool(self.param("average")) else 1

    def acquisition_ns(self) -> int:
        """Virtual time one acquisition takes."""
        points, _, _ = self._grid()
        rate = float(self.param("sample_rate"))
        return round((points * self._averages() / rate + float(self.param("overhead_s"))) * 1e9)

    def channel_noise_rms(self, n: int) -> float:
        """The noise of channel n's samples at the sampler's input, V rms: the vertical's per-division
        noise and the named channel components' output noise, added in power."""
        total = float(self._vertical(n).input_noise_rms()) ** 2
        assert self.owner is not None
        for name in self.param("noise_from") or []:
            comp = self.owner.components.get(f"ch{n}.{name}")
            if isinstance(comp, NoiseSource):
                total += float(comp.output_noise_rms()) ** 2
        return math.sqrt(total)

    def compute(self, channels: list[int], frame: int | None = None) -> tuple[dict[int, Record], int, bool]:
        """The records of the listed channels now, without storing them. ``frame`` numbers a frame
        the screen shows (its noise is its own); None: the next acquisition's noise."""
        if not bool(self.param("pattern_lock")):
            raise ScpiError(
                int(self.param("unlocked_error")), "pattern lock is off (eye mode is not modelled)"
            )
        locked = self.clock_unit().lock()
        if locked is None:
            raise ScpiError(int(self.param("unlocked_error")), "clock recovery is not locked")
        points, dt, position = self._grid()
        n_avg = self._averages()
        if points * n_avg > MAX_SAMPLES:
            raise beyond_model(f"{points} points x {n_avg} averages exceed {MAX_SAMPLES} samples")
        offsets = position + dt * np.arange(points)
        jitter = float(self.param("jitter_s"))
        tag = ("frame", frame) if frame is not None else ("acq", self.acq_count + 1)
        adc: Any = self.ref("adc")
        out: dict[int, Record] = {}
        for n in channels:
            vertical = self._vertical(n)
            sig = self.input(f"in{n}")
            sigma = self.channel_noise_rms(n)
            scale, offset = float(vertical.param("scale")), float(vertical.param("offset"))
            acc = np.zeros(points)
            for a in range(n_avg):
                idx = np.arange(points, dtype=np.int64) + a * points
                t = offsets
                if jitter > 0:
                    t = offsets + jitter * gaussian(
                        stream_key(self.session.seed, self.uid, "jitter", n, *tag), idx
                    )
                # pattern lock: time zero is the pattern's start; the record repeats with the pattern
                v = sig.sample_rel(0, t)
                if sigma > 0:
                    v = v + sigma * gaussian(stream_key(self.session.seed, self.uid, "noise", n, *tag), idx)
                if adc is not None:
                    v = adc.quantize(v, scale, offset)
                acc += v
            volts = vertical.correct(acc / n_avg)
            out[n] = Record(
                channel=n,
                t_start_ns=self.session.now_ns,
                dt_s=dt,
                trigger_index=0,
                volts=volts,
                scale=scale,
                offset=offset,
                triggered=True,
                x_origin_s=position,
            )
        return out, self.acquisition_ns(), True

    def acquire(self, channels: list[int] | None = None) -> int:
        """Take and store one acquisition of the channels (default: the displayed ones); its time."""
        chans = channels or self.displayed()
        records, used, _ = self.compute(chans)
        self.acq_count += 1
        self.records.update(records)
        if self.owner is not None:
            self.owner.status_event("DONE")
        return used

    def read_record(self, channel: int, start: bool = True) -> tuple[Record | None, int]:
        """A running scope keeps acquiring, so a read from the first point takes a new acquisition;
        a stopped one reads what it holds (None: nothing acquired on that channel)."""
        if self.running and start:
            used = self.acquire(sorted({*self.displayed(), channel}))
            return self.records.get(channel), used
        return self.records.get(channel), 0

    def transfer_record(self, channel: int, first: int) -> tuple[Record | None, int]:
        return self.read_record(channel, start=first <= 1)

    def measure_record(self, channel: int) -> tuple[Record, int]:
        rec, used = self.read_record(channel)
        if rec is None:
            used += self.acquire(sorted({*self.displayed(), channel}))
            rec = self.records[channel]
        return rec, used

    # ---- run control ---------------------------------------------------------------------------

    @op("run")
    def op_run(self, **_: Any) -> OpResult:
        self.running = True
        return OpResult()

    @op("stop")
    def op_stop(self, **_: Any) -> OpResult:
        self.running = False
        return OpResult()

    @op("single")
    def op_single(self, sources: list[bytes] | None = None, **_: Any) -> OpResult:
        """One acquisition of the displayed channels (or the named ones, which it turns on), then
        stop: the command returns when the record is complete."""
        assert self.owner is not None
        chans: list[int] = []
        for raw in sources or []:
            name = V.parse_string(raw).upper()
            m = name.removeprefix("CHANNEL").removeprefix("CHAN")
            if not m.isdigit() or not 1 <= int(m) <= self.owner.channel_count:
                raise ScpiError(-224, f"unknown source {name!r}")
            chans.append(int(m))
            self.owner.set_state(f"ch{int(m)}.display", True)
        used = self.acquire(chans or None)
        self.running = False
        return OpResult(duration_ns=used)

    @op("clear")
    def op_clear(self, **_: Any) -> OpResult:
        """Clear the records and the acquisition count."""
        self.records = {}
        self.acq_count = 0
        return OpResult()

    @op("count")
    def op_count(self, **_: Any) -> OpResult:
        """Acquisitions taken since the last clear."""
        return OpResult(str(self.acq_count).encode())
