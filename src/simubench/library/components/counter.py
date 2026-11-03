"""Frequency counter as its real blocks (D50, F #192): input conditioning per channel, the gate, the
timebase, the counting core with its measurement modes, and the statistics block.

``counter_input`` is the input terminal: the impedance is circuit level (a resistor to ground that
the setting switches), the coupling is a data-level high-pass, and the threshold and sensitivity are
the trigger level and the smallest peak-to-peak amplitude the input counts. ``counter_gate`` gives
the gate duration from the gate time and the timebase (a fast timebase makes the gate shorter in
true time). ``counter_core`` counts the positive-going threshold crossings: frequency and period
from reciprocal counting over the gate (a fast timebase reads low, as on the spectrum analyzer),
totalize the crossings inside the gate, and a time interval between the first event on channel A
and the next one on channel B, with the trigger error of two channels that do not share it.
``counter_statistics`` keeps the measurements for mean / minimum / maximum / standard deviation.

The counting model is periodic: the events come from the dominant spectral line of a harmonic signal
(``counter_input``'s threshold crossing of the synthesized waveform, sampled with sub-nanosecond
interpolation). A signal that is not on one harmonic grid is counted at its strongest line — a model
limit recorded in docs/component-map.md.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine.circuit import GROUND, Circuit
from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.noise import gaussian, stream_key
from ...engine.signal import Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError

POINTS_PER_PERIOD = 2048  # resolution of the threshold-crossing search (linear interpolation between)


@component("counter_input")
class CounterInput(Component):
    """One input channel (F #192): impedance (1 MΩ or 50 Ω) at the pin, AC/DC coupling, the trigger
    level and the sensitivity (the smallest peak-to-peak amplitude the channel counts; below it the
    signal is rejected as noise)."""

    ports = {
        "pin": PortSpec("in", "electrical"),
        "out": PortSpec("out", "signal"),
    }
    defaults = {
        "z_in": 1e6,  # input resistance to ground, ohm (the 1 MΩ / 50 Ω setting)
        "coupling": "DC",  # DC | AC input coupling
        "ac_corner": 10.0,  # Hz, corner of the AC coupling high-pass
        "threshold": 0.0,  # V, trigger level
        "sensitivity": 0.1,  # V peak to peak below which the input is not counted
    }
    units = {"z_in": "OHM", "ac_corner": "Hz", "threshold": "V", "sensitivity": "V"}

    def stamp(self, circuit: Circuit, node: Any) -> None:
        circuit.resistor(node("pin"), GROUND, float(self.param("z_in")))

    def output(self, port: str) -> Signal:
        if port != "out":
            raise NotImplementedError(f"{self.type_name} has no signal output {port!r}")
        sig = self.pin_signal("pin")
        if self.param("coupling") == "AC":
            fc = float(self.param("ac_corner"))
            sig = sig.transfer(lambda f: (1j * np.asarray(f) / fc) / (1 + 1j * np.asarray(f) / fc))
        return sig

    # ---- events ---------------------------------------------------------------------------------

    def crossings(self, start_ns: int) -> tuple[float, float] | None:
        """(period_s, first positive-going threshold crossing at or after ``start_ns``), or None.

        The waveform is sampled over one period of its dominant line (``sample_rel`` keeps the
        sub-nanosecond phase of the line origins) and the crossings are interpolated linearly.
        """
        sig = self.output("out").merged()
        pos = sig.freq > 0
        if not pos.any():
            return None
        freq, amp = sig.freq[pos], sig.amp[pos]
        f0 = float(freq[int(np.argmax(np.abs(amp)))])
        if f0 <= 0:
            return None
        period = 1.0 / f0
        offsets = np.linspace(-period, period, POINTS_PER_PERIOD)
        volts = sig.sample_rel(start_ns, offsets)
        if float(np.max(volts) - np.min(volts)) < float(self.param("sensitivity")):
            return None
        level = float(self.param("threshold"))
        rising = np.nonzero((volts[:-1] < level) & (volts[1:] >= level))[0]
        if not rising.size:
            return None
        for i in rising:
            v0, v1 = float(volts[i]), float(volts[i + 1])
            frac = 0.0 if v1 == v0 else (level - v0) / (v1 - v0)
            offset = float(offsets[i] + frac * (offsets[i + 1] - offsets[i]))
            if offset >= -1e-12:  # a crossing at the start instant counts as at the start
                return period, start_ns + max(0.0, offset) * 1e9
        # The only crossing in [-T, T) came before the start: the next one is one period on.
        i = int(rising[0])
        v0, v1 = float(volts[i]), float(volts[i + 1])
        frac = 0.0 if v1 == v0 else (level - v0) / (v1 - v0)
        offset = float(offsets[i] + frac * (offsets[i + 1] - offsets[i]))
        return period, start_ns + (offset + period) * 1e9

    def events(self, start_ns: int, end_ns: int) -> tuple[int, float, float]:
        """(count, first_ns, last_ns) of the positive-going threshold crossings in the window."""
        found = self.crossings(start_ns)
        if found is None or end_ns <= start_ns:
            return 0, float(start_ns), float(start_ns)
        period, first = found
        if first >= end_ns:
            return 0, first, first
        # The gate is half-open [start, end): an event exactly at the end belongs to the next one.
        count = math.floor((end_ns - first) / (period * 1e9) - 1e-12) + 1
        return count, first, first + (count - 1) * period * 1e9


@component("counter_gate")
class CounterGate(Component):
    """The gate (F #192): its time and source, and the true duration the timebase produces. A fast
    timebase makes the instrument's second short, so the gate lasts ``T / (1 + ε)`` of true time."""

    refs = {"clock": ("reference_clock", "instrument")}
    defaults = {
        "time": 0.1,  # s, the gate time
        "source": "INTernal",  # INTernal | EXTernal (who starts the gate: the trigger model)
        "clock": "",  # the reference_clock the gate time is counted on
    }
    units = {"time": "s"}

    def error(self) -> float:
        assert self.owner is not None
        return float(self.owner.components[str(self.param("clock"))].error())

    def duration_ns(self) -> int:
        """The gate's true length in virtual nanoseconds."""
        return max(1, round(float(self.param("time")) / (1 + self.error()) * 1e9))


@component("counter_statistics")
class CounterStatistics(Component):
    """The statistics block (F #192): the measurements taken, and their mean, minimum, maximum and
    standard deviation (sample standard deviation, n − 1)."""

    defaults = {"capacity": 10000}  # measurements kept (the oldest go past this)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.samples: list[float] = []

    def reset(self) -> None:
        self.samples.clear()

    def add(self, value: float) -> None:
        self.samples.append(float(value))
        if len(self.samples) > int(self.param("capacity")):
            del self.samples[: -int(self.param("capacity"))]

    def mean(self) -> float:
        return float(np.mean(self.samples)) if self.samples else 0.0

    def minimum(self) -> float:
        return float(min(self.samples)) if self.samples else 0.0

    def maximum(self) -> float:
        return float(max(self.samples)) if self.samples else 0.0

    def sdev(self) -> float:
        return float(np.std(self.samples, ddof=1)) if len(self.samples) > 1 else 0.0

    def readings(self) -> dict[str, Any]:
        return {
            "values": {
                "count": len(self.samples),
                "mean": self.mean(),
                "minimum": self.minimum(),
                "maximum": self.maximum(),
                "sdev": self.sdev(),
            }
        }

    @op("mean")
    def op_mean(self, **_: Any) -> OpResult:
        return OpResult(self.reply_number(self.mean(), 10))

    @op("minimum")
    def op_minimum(self, **_: Any) -> OpResult:
        return OpResult(self.reply_number(self.minimum(), 10))

    @op("maximum")
    def op_maximum(self, **_: Any) -> OpResult:
        return OpResult(self.reply_number(self.maximum(), 10))

    @op("sdev")
    def op_sdev(self, **_: Any) -> OpResult:
        return OpResult(self.reply_number(self.sdev(), 10))

    @op("count")
    def op_count(self, **_: Any) -> OpResult:
        return OpResult(V.format_int(len(self.samples)))

    @op("clear")
    def op_clear(self, **_: Any) -> OpResult:
        self.samples.clear()
        return OpResult()


@component("counter_timebase")
class CounterTimebase(Component):
    """The counter's timebase (#328, component-map frequency_counter.timebase): the reference clock
    turned into the instrument's time scale. A timestamp is the true instant on that scale — a fast
    clock reads time long by its error ε — with the interpolator's single-shot resolution (normal,
    seeded by the instant, so a repeated reading agrees).

    ``gap_free`` is continuous measurement without dead time: each frequency reading spans from the
    previous gate's last edge to this gate's last edge, so adjacent readings share their boundary
    edge and the readings times their spans add up exactly to the edges counted (the 53230A's
    gap-free mode; without it each gate measures its own first-to-last edge span).
    """

    refs = {"clock": ("reference_clock", "instrument")}
    defaults = {
        "clock": "",  # the reference_clock the timebase runs on
        "single_shot_s": 0.0,  # s, the interpolator's single-shot resolution (rms)
        "gap_free": False,  # continuous gates without dead time (a setting)
    }
    units = {"single_shot_s": "s"}

    def error(self) -> float:
        assert self.owner is not None
        return float(self.owner.components[str(self.param("clock"))].error())

    def timestamp(self, at_ns: float) -> float:
        """The instrument's timestamp (s) of a true instant (ns)."""
        t = float(at_ns) * 1e-9 * (1 + self.error())
        sigma = float(self.param("single_shot_s"))
        if sigma > 0 and self.owner is not None:
            key = stream_key(self.session.seed, self.owner.name, self.uid, "stamp", sigma)
            t += sigma * float(gaussian(key, np.array([round(at_ns)]))[0])
        return t

    def gap_free(self) -> bool:
        return bool(self.param("gap_free"))


@component("counter_core")
class CounterCore(Component):
    """The counting core (F #192): frequency, period, totalize and time interval over the gate.

    Frequency and period use reciprocal counting over the events inside the gate, so a fast
    timebase reads low by ε (the same convention as the spectrum analyzer's display); totalize
    reports the crossings inside the gate; time interval is measured between the first event on
    channel A and the next on channel B, plus each channel's trigger error (normal, seeded by the
    virtual instant, so two readings at one instant agree).
    """

    ports = {
        "a": PortSpec("in", "signal"),
        "b": PortSpec("in", "signal"),
    }
    refs = {
        "gate": ("counter_gate", "instrument"),
        "clock": ("reference_clock", "instrument"),
        "a_input": ("counter_input", "instrument"),
        "b_input": ("counter_input", "instrument"),
        "statistics": ("counter_statistics", "instrument"),
        "timebase": ("counter_timebase", "instrument"),
    }
    optional_refs = frozenset({"statistics", "timebase"})
    defaults = {
        "function": "FREQuency",  # FREQuency | PERiod | TOTalize | TINTerval | TSTamp (time stamps, #385)
        "gate": "",  # the counter_gate
        "clock": "",  # the reference_clock (timebase)
        "a_input": "",  # the counter_input of channel A (threshold, sensitivity)
        "b_input": "",  # the counter_input of channel B
        "statistics": "",  # the counter_statistics (empty: no statistics)
        "timebase": "",  # the counter_timebase: timestamps and gap-free gates (empty: none, #328)
        "trigger_error": 1e-9,  # s, one channel's trigger error (model value)
        "single_shot_s": 1e-9,  # s, the interpolator's single-shot resolution (model value)
        "timestamp_rate": 1.0e6,  # Hz, the highest rate time stamps are generated at (TSTamp:RATE, #385)
    }
    units = {"trigger_error": "s", "single_shot_s": "s", "timestamp_rate": "Hz"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._run_start_ns: int | None = None
        self._span_s = 0.0  # the last reading's span on the timebase (gap-free), s
        self._stamps: tuple[int, float, float] | None = None  # time stamps: (scale, period s, first ns)

    # ---- the blocks -----------------------------------------------------------------------------

    def _block(self, key: str) -> Any:
        assert self.owner is not None
        return self.owner.components[str(self.param(key))]

    def _gate(self) -> CounterGate:
        return self._block("gate")

    def error(self) -> float:
        return float(self._block("clock").error())

    def function(self) -> str:
        return str(self.param("function"))

    def _input(self, channel: str) -> CounterInput:
        return self._block(f"{channel}_input")

    # ---- the measurement ------------------------------------------------------------------------

    def _event_error(self, at_ns: int, channel: str) -> float:
        """One event's time error: the interpolator's single-shot resolution ⊕ the channel's
        trigger error, normal and seeded by the event's virtual instant (so it repeats)."""
        sigma = math.hypot(float(self.param("trigger_error")), float(self.param("single_shot_s")))
        if sigma <= 0:
            return 0.0
        assert self.owner is not None
        key = stream_key(self.session.seed, self.owner.name, self.uid, "event", channel, sigma)
        return sigma * float(gaussian(key, np.array([int(at_ns)]))[0])

    def measure(self, function: str | None, start_ns: int) -> float:
        """One measurement over the gate starting at ``start_ns`` (true-time windows)."""
        kind = (function or self.function()).upper()
        gate = self._gate()
        eps = self.error()
        end_ns = start_ns + gate.duration_ns()
        if kind.startswith("FREQ") or kind.startswith("PER"):
            count, first, last = self._input("a").events(start_ns, end_ns)
            if count >= 2 and last > first:
                span = (
                    (last - first) * 1e-9
                    + self._event_error(int(last), "a")
                    - self._event_error(int(first), "a")
                )
                f_true = (count - 1) / span
            else:
                found = self._input("a").crossings(start_ns)
                if found is None:
                    f_true = 0.0
                else:
                    period = (
                        found[0]
                        + self._event_error(int(found[1] + found[0] * 1e9), "a")
                        - self._event_error(int(found[1]), "a")
                    )
                    f_true = 1.0 / period
            reported = f_true / (1 + eps)  # a fast timebase reads low
            return 1.0 / reported if kind.startswith("PER") and reported else reported
        if kind.startswith("TOT"):
            count, _, _ = self._input("a").events(start_ns, end_ns)
            return float(count)
        if kind.startswith("TINT") or kind.startswith("TIME") or kind.startswith("INT"):
            a = self._input("a").crossings(start_ns)
            if a is None:
                return 0.0
            _, a_first = a
            b = self._input("b").crossings(math.ceil(a_first))
            if b is None:
                return 0.0
            _, b_first = b
            interval = (b_first - a_first) * 1e-9 * (1 + eps)
            return interval + self._event_error(int(a_first), "a") - self._event_error(int(b_first), "b")
        raise ScpiError(-224, f"unknown measurement {function!r}")

    def _record(self, function: str | None, start_ns: int) -> float:
        value = self.measure(function, start_ns)
        if str(self.param("statistics")):
            self._block("statistics").add(value)
        return value

    @op("measure")
    def op_measure(self, what: str | None = None, **_: Any) -> OpResult:
        """``:MEASure:<function>?``: one gated measurement, taking the gate time."""
        value = self._record(what, self.session.now_ns)
        return OpResult(self.reply_number(value, 12), duration_ns=self._gate().duration_ns())

    # ---- the trigger model's sampler ------------------------------------------------------------

    def _timestamping(self) -> bool:
        return self.function().upper().startswith("TST")

    def _stamp_plan(self) -> tuple[int, float, float] | None:
        """Time stamps (#385, the 53230A): every ``scale``-th rising edge on channel A from the
        first one, ``scale`` the smallest number of input cycles whose span is longer than one
        time-stamp period (1 below the rate; the guide's 10 MHz at 1 MHz gives 11)."""
        found = self._input("a").crossings(self.session.now_ns)
        if found is None:
            return None
        period, first = found
        rate = float(self.param("timestamp_rate"))
        scale = math.floor(1.0 / (period * rate)) + 1 if rate > 0 else 1
        return scale, period, first

    def sample_time_ns(self) -> int:
        if self._timestamping() and self._stamps is not None:
            scale, period, _ = self._stamps
            return max(1, round(scale * period * 1e9))
        return self._gate().duration_ns()

    def run_begin(self) -> None:
        self._run_start_ns = self.session.now_ns
        self._stamps = self._stamp_plan() if self._timestamping() else None

    def _stamp_sample(self, index: int) -> float:
        """Reading 0 is the scale factor; reading k the time between stamped edges k−1 and k on
        the timebase (scale × the input period, with the interpolator's resolution)."""
        if self._stamps is None:
            return 0.0
        scale, period, first = self._stamps
        if index == 0:
            return float(scale)
        timebase = self._timebase()
        if timebase is None:
            raise ScpiError(-221, "this counter has no timebase block for time stamps")
        step = scale * period * 1e9
        return timebase.timestamp(first + index * step) - timebase.timestamp(first + (index - 1) * step)

    def sample(self, index: int) -> float:
        if self._timestamping():
            return self._stamp_sample(index)
        start = self._run_start_ns if self._run_start_ns is not None else self.session.now_ns
        at = int(start + index * self.sample_time_ns())
        timebase = self._timebase()
        kind = self.function().upper()
        if (
            timebase is not None
            and timebase.gap_free()
            and (kind.startswith("FREQ") or kind.startswith("PER"))
        ):
            value = self._gap_free(at, timebase)
            if kind.startswith("PER"):
                value = 1.0 / value if value else 0.0
            if str(self.param("statistics")):
                self._block("statistics").add(value)
            return value
        return self._record(None, at)

    def _timebase(self) -> CounterTimebase | None:
        name = str(self.param("timebase") or "")
        if not name or self.owner is None:
            return None
        return self.owner.components[name]  # type: ignore[return-value]

    def _last_edge_before(self, at_ns: int) -> float | None:
        """The last rising edge strictly before ``at_ns`` (the boundary edge of the gate ending there)."""
        count, _, last = self._input("a").events(at_ns - self._gate().duration_ns(), at_ns)
        return last if count else None

    def _gap_free(self, start_ns: int, timebase: CounterTimebase) -> float:
        """A gate without dead time (#328): from the previous gate's last edge to this one's, the
        edges in between counted, on the timebase."""
        end_ns = start_ns + self._gate().duration_ns()
        count, _, last = self._input("a").events(start_ns, end_ns)
        prev = self._last_edge_before(start_ns)
        if count == 0 or prev is None:
            self._span_s = 0.0
            return 0.0
        span = timebase.timestamp(last) - timebase.timestamp(prev)
        self._span_s = span
        return count / span if span > 0 else 0.0

    def sample_fields(self, index: int) -> dict[str, float]:
        """The gap-free reading's span on the timebase (``SPAN``, s): readings × spans add up to
        the edges counted (#328)."""
        return {"SPAN": self._span_s}

    @op("timestamps")
    def op_timestamps(self, count: int = 1, **_: Any) -> OpResult:
        """The timebase's timestamps (s) of the next ``count`` rising edges on channel A (#328)."""
        timebase = self._timebase()
        if timebase is None:
            raise ScpiError(-221, "this counter has no timebase block for timestamps")
        found = self._input("a").crossings(self.session.now_ns)
        if found is None:
            return OpResult(b"")
        period, first = found
        stamps = [timebase.timestamp(first + k * period * 1e9) for k in range(max(1, int(count)))]
        return OpResult(b",".join(self.reply_number(t, 12) for t in stamps))

    # ---- readings -------------------------------------------------------------------------------

    def value_now(self) -> float:
        """The current setting's value from the signals as they are, without a gated measurement
        (what the front panel shows between readings)."""
        now = self.session.now_ns
        kind = self.function().upper()
        eps = self.error()
        gate = float(self._gate().param("time"))
        a = self._input("a").crossings(now)
        if a is None:
            return 0.0
        period, a_first = a
        reported = 1.0 / period / (1 + eps)
        if kind.startswith("FREQ"):
            return reported
        if kind.startswith("PER"):
            return 1.0 / reported
        if kind.startswith("TOT"):
            return float(math.floor(gate / (period * (1 + eps)) + 1e-9)) + 1.0
        if kind.startswith("TINT") or kind.startswith("TIME") or kind.startswith("INT"):
            b = self._input("b").crossings(now)
            if b is None:
                return 0.0
            return ((b[1] - a_first) % (period * 1e9)) * 1e-9 * (1 + eps)
        return 0.0

    def readings(self) -> dict[str, Any]:
        return {
            "values": {
                "value": self.value_now(),
                "gate": float(self._gate().param("time")),
            }
        }
