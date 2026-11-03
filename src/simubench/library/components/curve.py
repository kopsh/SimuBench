"""Curve storage: traces sampled at a fixed rate, read back by bin (#273, the SR830's data buffer).

A lock-in's data storage is not a reading buffer behind a trigger model: it records one point per
sample interval on each of its traces (the SR830 stores its two displays, CH1 and CH2) from the
moment the scan starts, may be paused and resumed where it stopped, stops or wraps when full, and
is read back from bin 0 (the oldest point) in ASCII or binary. ``curve_buffer`` is that block:

* ``trace1`` .. ``trace4`` name what each trace records, ``<component>.<quantity>`` (a component
  with ``trace_value(quantity, t_ns)``, e.g. ``demod.X`` or ``aux.AUX1``); a definition binds them
  to its display settings. An empty trace records nothing.
* ``rate`` is the sample rate in Hz; 0 means one point per trigger (the SR830's SRAT 14).
* ``end`` is ``SHOT`` (stop when ``capacity`` points are stored) or ``LOOP`` (keep the newest
  ``capacity`` points: bin 0 is always the oldest one kept).
* ``trigger_start`` lets a trigger start the scan (the SR830's TSTR).

The points are taken in virtual time: a running scan's samples are at ``start + k / rate``; the
buffer catches up whenever time moves or it is read, asking each trace's component for its value
at the sample's instant (the lock-in output's filter history makes that exact).
"""

from __future__ import annotations

import math
import struct
from typing import Any

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError

TRACES = 4


def lia16(value: float) -> bytes:
    """The SR830's 16-bit non-normalized float (manual 5-17): a signed 16-bit mantissa m and a
    16-bit exponent e, value = m × 2^(e − 124), both little-endian (four bytes per point)."""
    if value == 0 or not math.isfinite(value):
        return struct.pack("<hh", 0, 0)
    e = math.floor(math.log2(abs(value)))
    shift = e - 14  # m in 16384 .. 32767
    m = round(value / 2.0**shift)
    if abs(m) > 32767:
        shift += 1
        m = round(value / 2.0**shift)
    return struct.pack("<hh", m, shift + 124)


@component("curve_buffer")
class CurveBuffer(Component):
    """Traces sampled at a rate into bins, with start, pause, resume and reset (#273).

    The SR830's data storage: two traces (the CH1/CH2 displays), 62.5 mHz – 512 Hz or one point
    per trigger, single shot or loop, ``TRCA?``/``TRCB?``/``TRCL?`` reading k points from bin j.
    """

    defaults = {
        "trace1": "",  # what trace 1 records: <component>.<quantity> (empty: nothing)
        "trace2": "",  # what trace 2 records
        "trace3": "",  # what trace 3 records
        "trace4": "",  # what trace 4 records
        "rate": 1.0,  # Hz, samples per second (0: one point per trigger)
        "end": "SHOT",  # SHOT: stop when full | LOOP: keep the newest `capacity` points
        "capacity": 16383,  # points per trace
        "trigger_start": False,  # a trigger starts the scan (and the rate takes over)
    }
    units = {"rate": "Hz"}
    choices = {"end": ("SHOT", "LOOP")}
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reset()

    def reset(self) -> None:
        self._state = "idle"  # idle | run | paused | done
        self._points: list[list[float]] = [[] for _ in range(TRACES)]
        self._taken = 0  # points taken since the scan started (wrapping ones included)
        self._next_ns = 0  # the instant of the next timed sample

    # ---- sampling ---------------------------------------------------------------------------------

    def _rate(self) -> float:
        return max(0.0, float(self.param("rate") or 0.0))

    def _period_ns(self) -> int:
        return max(1, round(1e9 / self._rate()))

    def _value(self, spec: str, t_ns: int) -> float:
        name, _, quantity = str(spec).partition(".")
        assert self.owner is not None
        comp = self.owner.components.get(name)
        if comp is None or not hasattr(comp, "trace_value"):
            raise ValueError(f"{self.uid}: trace source {spec!r} is not a component with trace values")
        return float(comp.trace_value(quantity, t_ns))

    def _take(self, t_ns: int) -> None:
        """One point on every trace at instant ``t_ns``."""
        capacity = int(self.param("capacity"))
        for i in range(TRACES):
            spec = self.param(f"trace{i + 1}")
            if not spec:
                continue
            points = self._points[i]
            points.append(self._value(spec, t_ns))
            if len(points) > capacity:
                del points[0]
        self._taken += 1
        if self.choice("end") == "SHOT" and self._taken >= capacity:
            self._state = "done"

    def _catch_up(self) -> None:
        """Take the timed samples due up to now."""
        if self._state != "run" or self._rate() <= 0:
            return
        now = self.session.now_ns
        period = self._period_ns()
        while self._state == "run" and self._next_ns <= now:
            self._take(self._next_ns)
            self._next_ns += period

    def sync(self) -> None:
        self._catch_up()

    def points(self) -> int:
        self._catch_up()
        return max((len(p) for p in self._points), default=0)

    # ---- control ------------------------------------------------------------------------------------

    def start(self) -> None:
        """Start the scan, or resume a paused one where it stopped."""
        self._catch_up()
        if self._state in ("run", "done"):
            return
        if self._state == "idle":
            self._points = [[] for _ in range(TRACES)]
            self._taken = 0
        self._state = "run"
        self._next_ns = self.session.now_ns  # the first point is taken at the start
        self._catch_up()

    def pause(self) -> None:
        self._catch_up()
        if self._state == "run":
            self._state = "paused"

    def clear(self) -> None:
        """Stop and discard every point (the SR830's REST)."""
        self.reset()

    def trigger(self) -> None:
        """A trigger: one point in the trigger rate; with ``trigger_start`` it starts the scan."""
        self._catch_up()
        if self._state == "idle" and bool(self.param("trigger_start")):
            self.start()
            return
        if self._state == "run" and self._rate() <= 0:
            self._take(self.session.now_ns)

    # ---- reading ------------------------------------------------------------------------------------

    def _slice(self, trace: int, start: int, count: int) -> list[float]:
        if not 1 <= trace <= TRACES or not self.param(f"trace{trace}"):
            raise ScpiError(-222, f"trace {trace} does not exist")
        self._catch_up()
        points = self._points[trace - 1]
        if start < 0 or count < 1 or start + count > len(points):
            raise ScpiError(-222, f"bins {start}..{start + count - 1} are not stored ({len(points)} points)")
        return points[start : start + count]

    @op("start")
    def op_start(self, fresh: bool = False, **_: Any) -> OpResult:
        """Start (or resume) the scan; ``fresh`` discards what is stored first (the SR86x's
        CAPTURESTART clears the buffer and starts a new capture)."""
        if fresh:
            self.reset()
        self.start()
        return OpResult()

    @op("pause")
    def op_pause(self, **_: Any) -> OpResult:
        self.pause()
        return OpResult()

    @op("reset")
    def op_reset(self, **_: Any) -> OpResult:
        self.clear()
        return OpResult()

    @op("trigger")
    def op_trigger(self, **_: Any) -> OpResult:
        self.trigger()
        return OpResult()

    @op("points")
    def op_points(self, **_: Any) -> OpResult:
        return OpResult(V.format_int(self.points()))

    @op("read")
    def op_read(
        self, trace: int = 1, start: int = 0, count: int = 1, encoding: str = "ASCII", **_: Any
    ) -> OpResult:
        """``count`` points of a trace from bin ``start`` (0: the oldest point). ``ASCII``: each
        value followed by a comma; ``FLOAT``: IEEE single precision, little-endian; ``LIA``: the
        SR830's 16-bit mantissa/exponent pairs."""
        values = self._slice(int(trace), int(start), int(count))
        kind = str(encoding).upper()
        if kind == "FLOAT":
            return OpResult(struct.pack(f"<{len(values)}f", *values))
        if kind == "LIA":
            return OpResult(b"".join(lia16(v) for v in values))
        return OpResult(b"".join(self.reply_number(v, 8) + b"," for v in values))

    # ---- the SR86x capture view (#333): all traces at one index, bytes, state, rate ---------------

    def _active(self) -> list[int]:
        return [i for i in range(TRACES) if self.param(f"trace{i + 1}")]

    @op("sample")
    def op_sample(self, index: int = 0, **_: Any) -> OpResult:
        """``CAPTUREVAL? n``: every recorded trace's value at bin ``n`` (0: the oldest), comma
        separated; a bin inside the buffer that holds no data yet reads 0 (the zero fill)."""
        self._catch_up()
        n = int(index)
        if not 0 <= n < int(self.param("capacity")):
            raise ScpiError(-222, f"bin {n} is outside the buffer")
        values = [self._points[i][n] if n < len(self._points[i]) else 0.0 for i in self._active()]
        return OpResult(b",".join(self.reply_number(v, 8) for v in values))

    def captured_bytes(self) -> int:
        """Bytes of data stored: points × recorded traces × 4 (single precision)."""
        return self.points() * len(self._active()) * 4

    @op("bytes")
    def op_bytes(self, **_: Any) -> OpResult:
        return OpResult(V.format_int(self.captured_bytes()))

    @op("progress")
    def op_progress(self, block_kb: int = 2, **_: Any) -> OpResult:
        """kB written by the last capture, in whole blocks of ``block_kb`` (CAPTUREPROG?)."""
        kb = math.ceil(self.captured_bytes() / 1024 / int(block_kb)) * int(block_kb)
        return OpResult(V.format_int(kb))

    @op("capture_state")
    def op_capture_state(self, **_: Any) -> OpResult:
        """CAPTURESTAT? bits: 1 capturing, 2 started (triggered) since the last reset, 4 wrapped."""
        self._catch_up()
        bits = (1 if self._state == "run" else 0) | (2 if self._state != "idle" else 0)
        bits |= 4 if self._taken >= int(self.param("capacity")) else 0
        return OpResult(V.format_int(bits))

    @op("rate")
    def op_rate(self, **_: Any) -> OpResult:
        """The sample rate in use, Hz (CAPTURERATE? answers the rate, not its setting)."""
        return OpResult(self.reply_number(self._rate(), 8))

    @op("get")
    def op_get(self, offset_kb: int = 0, length_kb: int = 1, **_: Any) -> OpResult:
        """CAPTUREGET? i, j: ``length_kb`` kB from ``offset_kb`` kB of the buffer as IEEE single
        precision, little-endian, the traces interleaved point by point; past the data, zeros."""
        self._catch_up()
        if self._state == "run":
            raise ScpiError(-222, "the capture must be stopped to read it as a block")
        if not 1 <= int(length_kb) <= 64:
            raise ScpiError(-222, "a block is 1 to 64 kB")
        active = self._active()
        stored = [v for k in range(self.points()) for i in active for v in (self._points[i][k],)]
        data = struct.pack(f"<{len(stored)}f", *stored)
        start, size = int(offset_kb) * 1024, int(length_kb) * 1024
        chunk = data[start : start + size]
        return OpResult(chunk + bytes(size - len(chunk)))

    def status_flags(self) -> dict[str, bool]:
        """SCN while no scan is in progress (paused counts as none: the SRS serial poll bit 0)."""
        self._catch_up()
        return {"SCN": self._state != "run"}

    def readings(self) -> dict[str, Any]:
        n = self.points()
        return {"values": {"state": self._state, "points": n, "rate": self._rate()}}


__all__ = ["CurveBuffer", "lia16"]
