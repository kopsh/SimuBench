"""Siglent SDS oscilloscope acquisition (real model, D42): run control, WAVEDESC preamble, codes.

Behavior follows the *SDS Series Digital Oscilloscope Programming Guide* EN11F; the trigger search
and sampling are the shared oscilloscope engine (:class:`ScopeAcquisition`). What differs from the
``sb-*`` scopes:

* run control is the trigger mode: ``:TRIGger:MODE SINGle`` acquires once and stops,
  ``:TRIGger:STATus?`` reports ``Stop`` / ``Trig'd`` / ``Auto`` / ``Ready``; a running scope
  (AUTO or NORMal) hands out a fresh acquisition for every read;
* the record length follows the sample rate, the memory depth and the timebase
  (memory management AUTO): rate = min(maximum rate, depth / 10 div), on a 1-2-5 ladder;
* ``:WAVeform:PREamble?`` is the 346-byte binary WAVEDESC block (little-endian) in a ``#9``
  block; ``:WAVeform:DATA?`` signed codes with ``volts = code / code_per_div * vdiv - offset`` in
  a block whose header has as many digits as the length needs (since E11D); both are followed by
  an extra line feed (the manual's "0A 0A" end of data);
* 8-bit codes have 30 codes per division; in 10-bit mode a WORD carries the code left-aligned in
  16 bits, so the preamble reports 30 * 256 codes per division.
"""

from __future__ import annotations

import math
import struct
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope import DIVS_H, FALLING, RISING, Record, ScopeAcquisition, _measure, channel_index

CODES_PER_DIV = 30
#: :WAVeform:MAXPoint? of the SDS2000X Plus (manual example).
MAX_POINT = 10_000_000
#: Time/div enumeration of the WAVEDESC (manual's waveform example).
TDIV_ENUM = [
    200e-12, 500e-12, 1e-9, 2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3,
    1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0,
]  # fmt: skip
#: Measurements of :MEASure:SIMPle:VALue? that SimuBench computes, by the engine's names.
SIMPLE_MEASUREMENTS = {
    "PKPK": "VPP",
    "MAX": "VMAX",
    "MIN": "VMIN",
    "AMPL": "VAMPlitude",
    "MEAN": "VAVerage",
    "RMS": "VRMS",
    "PER": "PERiod",
    "FREQ": "FREQuency",
}
AVERAGES = (4, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192)
ERES_BITS = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)


def _block9(payload: bytes) -> bytes:
    """``#9`` and nine digits, then the payload: the header the manual shows for both blocks."""
    return b"#9" + f"{len(payload):09d}".encode() + payload


def _depth(text: str) -> int:
    t = text.strip().upper()
    mult = {"K": 1_000, "M": 1_000_000, "G": 1_000_000_000}.get(t[-1:], 1)
    return round(float(t.rstrip("KMG")) * mult)


def _ladder_down(value: float) -> float:
    """Largest 1-2-5 step not above ``value`` (sample rates come in such steps)."""
    k = math.floor(math.log10(value))
    for m in (5.0, 2.0, 1.0):
        if m * 10.0**k <= value * (1 + 1e-12):
            return m * 10.0**k
    return 10.0 ** (k - 1) * 5.0


@component("siglent_sds_acquisition")
class SiglentSdsAcquisition(ScopeAcquisition):
    """SDS-series run control, record length and waveform transfer on the shared scope engine."""

    defaults = {
        **ScopeAcquisition.defaults,
        "max_rate": 2e9,  # interleaved: one of CH1/CH2 and one of CH3/CH4 on (data sheet)
        "max_rate_both": 1e9,  # non-interleaved
        "level_divs": 4.1,  # trigger level range: +-4.1 div around the offset
        "trigger_level": "$trigger.level",
        "delay": "$timebase.delay",
        "tdiv": "$timebase.scale",
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.running = True
        self.single_armed = False
        self.last_triggered = False

    def reset(self) -> None:
        super().reset()
        self.running = True
        self.single_armed = False
        self.last_triggered = False
        self.acq_count = 0

    # ---- limits that depend on other settings (raise -222 and keep the old value) ---------------

    def on_state(self, path: str, old: Any, new: Any) -> None:
        if path == "trigger.level":
            src = str(self._st("trigger.source"))
            if src.startswith("C"):
                n = channel_index(src)
                scale, offset = float(self._st(f"ch{n}.scale")), float(self._st(f"ch{n}.offset"))
                lim = float(self.param("level_divs")) * scale
                if not -lim - offset - 1e-12 <= float(new) <= lim - offset + 1e-12:
                    raise ScpiError(-222, f"trigger level outside +-{self.param('level_divs')} div")
        if path == "timebase.delay":
            tb = float(self._st("timebase.scale"))
            if not -5000 * tb * (1 + 1e-9) <= float(new) <= 5 * tb * (1 + 1e-9):
                raise ScpiError(-222, "delay outside [-5000 div, 5 div]")

    # ---- timing ---------------------------------------------------------------------------------

    def sample_rate(self) -> float:
        on = {n for n in range(1, self._channels() + 1) if self._st(f"ch{n}.display")}
        both = {1, 2} <= on or {3, 4} <= on
        top = float(self.param("max_rate_both" if both else "max_rate"))
        window = DIVS_H * float(self._st("timebase.scale"))
        return min(top, _ladder_down(_depth(str(self._st("acquire.mdepth"))) / window))

    def _timing(self) -> tuple[int, float, int, int]:
        rate = self.sample_rate()
        tb = float(self._st("timebase.scale"))
        points = max(2, round(rate * DIVS_H * tb))
        dt = 1.0 / rate
        # Time of sample 0 is delay - 5 div (manual): the trigger sits at (5 div - delay) / dt.
        pre = round((DIVS_H / 2 * tb - float(self._st("timebase.delay"))) / dt)
        return points, dt, pre, points - pre

    def compute(self, channels: list[int]) -> tuple[dict[int, Record], int, bool]:
        points, dt, pre, post = self._timing()
        t0 = self.session.now_ns
        trig_src = str(self._st("trigger.source"))
        if trig_src.startswith("C"):
            sig = self._display_signal(channel_index(trig_src))
            k, triggered = self._search(sig, t0, dt, pre, points)
        else:  # EX, EX5, LINE: nothing modeled drives them; the scope never triggers
            k, triggered = pre, False
        start = k - pre
        idx = np.arange(points)
        records = {
            n: Record(
                channel=n,
                t_start_ns=t0 + round(start * dt * 1e9),
                dt_s=dt,
                trigger_index=pre,
                volts=self._display_signal(n).sample_rel(t0, (start + idx) * dt),
                scale=float(self._st(f"ch{n}.scale")),
                offset=float(self._st(f"ch{n}.offset")),
                triggered=triggered,
            )
            for n in channels
        }
        used = (k + max(post, 0)) * dt + float(self.param("overhead_s"))
        return records, round(used * 1e9), triggered

    def _search(self, sig: Any, t0: int, dt: float, pre: int, points: int) -> tuple[int, bool]:
        """First qualifying edge at or after ``pre`` samples (enough history before the trigger)."""
        level = float(self._st("trigger.level"))
        slope = self._st("trigger.slope")
        n = channel_index(self._st("trigger.source"))
        hyst = 0.02 * 8 * float(self._st(f"ch{n}.scale"))
        span = max(points * int(self.param("search_windows")), pre + 2 * points)
        idx = np.arange(span)
        v = sig.sample_rel(t0, idx * dt)
        best: int | None = None
        for wanted, armed_side, crossed in (
            (slope in RISING, v < level - hyst, v >= level),
            (slope in FALLING, v > level + hyst, v <= level),
        ):
            if not wanted:
                continue
            armed = np.nonzero(armed_side & (idx >= pre - 1))[0]
            if armed.size:
                after = np.nonzero(crossed & (idx > armed[0]))[0]
                if after.size and (best is None or after[0] < best):
                    best = int(after[0])
        return (pre, False) if best is None else (best, True)

    def displayed(self) -> list[int]:
        return [n for n in range(1, self._channels() + 1) if self._st(f"ch{n}.display")]

    # ---- run control ------------------------------------------------------------------------------

    def _acquire_now(self, force: bool = False) -> int:
        records, used, triggered = self.compute(self.displayed())
        self.last_triggered = triggered
        if not triggered and not force and self._st("trigger.mode") in ("NORMal", "SINGle"):
            return used  # waiting for a trigger: the previous records stay
        return self.acquire_records(records, used)

    def acquire_records(self, records: dict[int, Record], used: int) -> int:
        """Keep ``records`` as the latest acquisition and log its recipe (D36)."""
        self.records.update(records)
        self.acq_count += 1
        self.session.invalidate()
        if self.owner is not None:
            self.session.instrument_event(
                self.owner,
                "acquisition",
                {
                    "count": self.acq_count,
                    "channels": {
                        str(ch): {
                            "t_start_ns": rec.t_start_ns,
                            "dt_s": rec.dt_s,
                            "points": int(rec.volts.size),
                            "trigger_index": rec.trigger_index,
                            "triggered": rec.triggered,
                        }
                        for ch, rec in sorted(records.items())
                    },
                    "seed": self.session.seed,
                },
            )
        return used

    def _fresh(self) -> int:
        """A running scope hands out the newest acquisition: acquire now. Returns the time used."""
        if self.running and self._st("trigger.mode") in ("AUTO", "NORMal"):
            return self._acquire_now()
        return 0

    @op("trigger_mode")
    def trigger_mode(self, query: bool = False, mode: str | None = None, **_: Any) -> OpResult:
        if query:
            return OpResult(str(self._st("trigger.mode")).encode())
        if mode is None:
            raise ScpiError(-109)
        assert self.owner is not None
        self.owner.set_state("trigger.mode", mode)
        if mode == "SINGle":
            self.running, self.single_armed = True, True
            used = self._acquire_now()
            if self.last_triggered:
                self.running, self.single_armed = False, False
            return OpResult(duration_ns=used)
        if mode == "FTRIG":  # force one frame whatever the trigger condition, then stop
            used = self._acquire_now(force=True)
            self.running = False
            return OpResult(duration_ns=used)
        self.running, self.single_armed = True, False
        return OpResult()

    @op("run")
    def run(self, **_: Any) -> OpResult:
        self.running = True
        if self._st("trigger.mode") == "SINGle":
            self.single_armed = True
            used = self._acquire_now()
            if self.last_triggered:
                self.running, self.single_armed = False, False
            return OpResult(duration_ns=used)
        return OpResult()

    @op("stop")
    def stop(self, **_: Any) -> OpResult:
        self.running, self.single_armed = False, False
        return OpResult()

    def status(self) -> str:
        if not self.running:
            return "Stop"
        mode = self._st("trigger.mode")
        if mode == "SINGle":
            return "Ready"
        _, _, triggered = self.compute([])
        if mode == "AUTO":
            return "Trig'd" if triggered else "Auto"
        return "Trig'd" if triggered else "Ready"

    @op("status")
    def status_query(self, **_: Any) -> OpResult:
        return OpResult(self.status().encode())

    # ---- acquisition settings that are not plain state ------------------------------------------

    @op("acquire_type")
    def acquire_type(self, query: bool = False, kind: str | None = None, value: float | None = None,
                     **_: Any) -> OpResult:  # fmt: skip
        assert self.owner is not None
        if query:
            kind = str(self._st("acquire.type"))
            if kind == "AVERage":
                return OpResult(f"AVERage,{int(self._st('acquire.average'))}".encode())
            if kind == "ERES":
                return OpResult(f"ERES,{float(self._st('acquire.eres')):.1f}".encode())
            return OpResult(kind.encode())
        if kind is None:
            raise ScpiError(-109)
        if kind == "AVERage" and value is not None:
            if round(value) not in AVERAGES:
                raise ScpiError(-224, f"averages must be one of {AVERAGES}")
            self.owner.set_state("acquire.average", round(value))
        if kind == "ERES" and value is not None:
            if value not in ERES_BITS:
                raise ScpiError(-224, f"ERES bits must be one of {ERES_BITS}")
            self.owner.set_state("acquire.eres", value)
        self.owner.set_state("acquire.type", kind)
        return OpResult()

    @op("probe")
    def probe(self, query: bool = False, channel: int | None = None, mode: str | None = None,
              value: float | None = None, **_: Any) -> OpResult:  # fmt: skip
        """``:CHANnel<n>:PROBe {DEFault|VALue},<value>``: changing the ratio rescales the
        channel's scale and offset (they are in probe-tip volts)."""
        assert self.owner is not None and channel is not None
        path = f"ch{channel}.probe"
        if query:
            return OpResult(self._nr3(float(self._st(path))))
        if mode is None:
            raise ScpiError(-109)
        if mode == "VALue":
            if value is None:
                raise ScpiError(-109, "probe ratio missing")
            if not 1e-6 <= value <= 1e6:
                raise ScpiError(-222)
            ratio = float(value)
        else:
            ratio = 1.0
        old = float(self._st(path))
        k = ratio / old
        self.owner.set_state(path, ratio)
        for key in ("scale", "offset"):
            p = f"ch{channel}.{key}"
            self.owner.set_state(p, float(self._st(p)) * k, internal=True)
        return OpResult()

    # ---- read-only acquisition figures ------------------------------------------------------------

    def _nr3(self, value: float) -> bytes:
        assert self.owner is not None
        f = self.owner.definition.format
        return V.format_float(value, f.digits, style=f.number, sign=f.sign, exponent_digits=f.exponent_digits)

    @op("points")
    def points(self, **_: Any) -> OpResult:
        return OpResult(self._nr3(self._timing()[0]))

    @op("srate")
    def srate(self, **_: Any) -> OpResult:
        return OpResult(self._nr3(self.sample_rate()))

    @op("numacq")
    def numacq(self, **_: Any) -> OpResult:
        return OpResult(str(self.acq_count).encode())

    @op("maxpoint")
    def maxpoint(self, **_: Any) -> OpResult:
        return OpResult(str(MAX_POINT).encode())

    # ---- waveform transfer ------------------------------------------------------------------------

    def _bits(self) -> int:
        return 10 if self._st("acquire.resolution") == "10Bits" else 8

    def _selection(self, rec: Record) -> tuple[np.ndarray, int, int]:
        start = int(self._st("waveform.start"))
        count = int(self._st("waveform.point"))
        step = max(1, int(self._st("waveform.interval")))
        sel = np.arange(start, rec.volts.size, step)
        if count > 0:
            sel = sel[:count]
        return sel, start, step

    def _codes(self, rec: Record) -> tuple[np.ndarray, float, int]:  # type: ignore[override]
        """ADC codes at the record's resolution: (codes, codes per division, ADC bits)."""
        bits = self._bits()
        per_div = CODES_PER_DIV * (1 << (bits - 8))
        half = 1 << (bits - 1)
        codes = np.round((rec.volts + rec.offset) / rec.scale * per_div)
        return np.clip(codes, -half, half - 1).astype(np.int64), float(per_div), bits

    def _wave_record(self) -> tuple[Record | None, int]:
        used = self._fresh()
        return self.records.get(self._source()), used

    @op("wav_preamble")
    def wav_preamble(self, **_: Any) -> OpResult:
        rec, used = self._wave_record()
        if rec is None:
            assert self.owner is not None
            self.owner.push_error(-230, "no acquisition for this source")
            return OpResult(_block9(b"") + b"\n", used)
        return OpResult(_block9(self._wavedesc(rec)) + b"\n", used)

    def _wavedesc(self, rec: Record) -> bytes:
        n = rec.channel
        probe = float(self._st(f"ch{n}.probe"))
        sel, start, step = self._selection(rec)
        word = self._st("waveform.width") == "WORD"
        _, per_div, bits = self._codes(rec)
        if bits > 8:
            per_div *= 1 << (16 - bits)  # left-aligned in a 16-bit word
        tb = float(self._st("timebase.scale"))
        tdiv_index = min(range(len(TDIV_ENUM)), key=lambda i: abs(math.log10(TDIV_ENUM[i] / tb)))
        coupling = {"DC": 0, "AC": 1, "GND": 2}[str(self._st(f"ch{n}.coupling"))]
        bwl = {"FULL": 0, "20M": 1, "200M": 2}[str(self._st(f"ch{n}.bwlimit"))]
        d = bytearray(346)
        d[0:8] = b"WAVEDESC"
        d[16:23] = b"WAVEACE"
        struct.pack_into("<hh", d, 32, 1 if word else 0, 0)
        struct.pack_into("<i", d, 36, 346)
        struct.pack_into("<i", d, 60, sel.size * (2 if word else 1))
        d[76:87] = b"Siglent SDS"
        struct.pack_into("<i", d, 116, sel.size)
        struct.pack_into("<ii", d, 132, start, step)
        struct.pack_into("<ii", d, 144, 1, 1)
        struct.pack_into("<fff", d, 156, rec.scale / probe, rec.offset / probe, per_div)
        struct.pack_into("<hh", d, 172, bits, 1)
        struct.pack_into("<f", d, 176, rec.dt_s)
        struct.pack_into("<d", d, 180, float(self._st("timebase.delay")))
        struct.pack_into("<hh", d, 324, tdiv_index, coupling)
        struct.pack_into("<f", d, 328, probe)
        struct.pack_into("<hh", d, 334, bwl, n - 1)
        return bytes(d)

    @op("wav_data")
    def wav_data(self, **_: Any) -> OpResult:
        rec, used = self._wave_record()
        if rec is None:
            assert self.owner is not None
            self.owner.push_error(-230, "no acquisition for this source")
            return OpResult(V.format_block(b"") + b"\n", used)
        codes, _, bits = self._codes(rec)
        sel, _, _ = self._selection(rec)
        codes = codes[sel]
        if self._st("waveform.width") == "WORD":
            payload = (codes << (16 - bits)).astype("<i2").tobytes()
        else:
            payload = (codes >> (bits - 8)).astype("i1").tobytes()
        return OpResult(V.format_block(payload) + b"\n", used)

    # ---- measurements -----------------------------------------------------------------------------

    @op("measure_simple")
    def measure_simple(self, kind: str | None = None, **_: Any) -> OpResult:
        if kind is None:
            raise ScpiError(-109)
        what = SIMPLE_MEASUREMENTS.get(kind.upper())
        if what is None:
            raise ScpiError(-224, f"measurement {kind} not modeled by SimuBench")
        used = self._fresh()
        n = channel_index(self._st("measure.source"))
        rec = self.records.get(n)
        if rec is None:
            records, t, _ = self.compute([n])
            used += self.acquire_records(records, t)
            rec = self.records[n]
        value = _measure(what, rec.volts, rec.dt_s)
        return OpResult(self._nr3(value if math.isfinite(value) else 9.9e37), used)


#: Offset range at the input (probe 1X) by vertical scale (data sheet): (largest scale, offset limit).
OFFSET_RANGES = ((0.1, 2.0), (1.0, 20.0), (math.inf, 200.0))


@component("siglent_sds_channel_limits")
class SiglentSdsChannelLimits(Component):
    """Vertical scale and offset ranges of one channel, which depend on the impedance and the probe
    (data sheet: 500 uV/div to 10 V/div at 1 MOhm, to 1 V/div at 50 Ohm; offset by scale)."""

    defaults = {
        "scale": "$ch.scale",
        "offset": "$ch.offset",
        "probe": "$ch.probe",
        "impedance": "$ch.impedance",
        "min_scale": 500e-6,
    }

    def on_state(self, path: str, old: Any, new: Any) -> None:
        probe = float(self.param("probe"))
        if path.endswith(".probe"):
            return  # the probe op rescales scale and offset with it
        scale = float(self.param("scale")) / probe
        max_scale = 1.0 if self.param("impedance") == "FIFTy" else 10.0
        if not float(self.param("min_scale")) * (1 - 1e-9) <= scale <= max_scale * (1 + 1e-9):
            raise ScpiError(-222, f"scale outside {self.param('min_scale')}..{max_scale} V/div (probe 1X)")
        limit = next(lim for top, lim in OFFSET_RANGES if scale <= top * (1 + 1e-9))
        if abs(float(self.param("offset")) / probe) > limit * (1 + 1e-9):
            raise ScpiError(-222, f"offset outside +-{limit:g} V at this scale (probe 1X)")
