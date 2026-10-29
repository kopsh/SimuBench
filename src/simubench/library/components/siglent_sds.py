"""Siglent SDS oscilloscope acquisition (real model, D42): status, WAVEDESC preamble, limits.

Behavior follows the *SDS Series Digital Oscilloscope Programming Guide* EN11F. Run control, the
record length (memory management AUTO: rate = min(maximum rate, depth / 10 div) on a 1-2-5 ladder),
noise and the ADC are the configurable :class:`ScopeAcquisition`, set in the definition
(``siglent-sds2104xplus.yaml``). What only this model has:

* ``:TRIGger:STATus?`` reports ``Stop`` / ``Trig'd`` / ``Auto`` / ``Ready``;
* ``:WAVeform:PREamble?`` is the 346-byte binary WAVEDESC block (little-endian) in a ``#9``
  block, followed by an extra line feed (the manual's "0A 0A" end of data); in 10-bit mode a WORD
  carries the code left-aligned in 16 bits, so it reports 30 * 256 codes per division;
* limits that depend on other settings (trigger level, delay), ``:ACQuire:TYPE`` with its count,
  ``:CHANnel<n>:PROBe`` and the read-only acquisition figures.
"""

from __future__ import annotations

import math
import struct
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope import Record, ScopeAcquisition, _measure, channel_index

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


@component("siglent_sds_acquisition")
class SiglentSdsAcquisition(ScopeAcquisition):
    """SDS-series status, WAVEDESC and setting limits on the configurable scope acquisition."""

    generic = False

    defaults = {
        **ScopeAcquisition.defaults,
        "level_divs": 4.1,  # trigger level range: +-4.1 div around the offset
    }

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

    # ---- run control ------------------------------------------------------------------------------

    def status(self) -> str:
        if not self.is_running():
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

    def _selection(self, rec: Record) -> tuple[np.ndarray, int, int]:
        start = int(self._st("waveform.start"))
        count = int(self._st("waveform.point"))
        step = max(1, int(self._st("waveform.interval")))
        sel = np.arange(start, rec.volts.size, step)
        if count > 0:
            sel = sel[:count]
        return sel, start, step

    @op("wav_preamble")
    def wav_preamble(self, **_: Any) -> OpResult:
        rec, used = self.read_record(channel_index(self._st("waveform.source")))
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
        per_div, bits = self.adc_codes_per_div(), int(self.param("adc_bits"))
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

    # ---- measurements -----------------------------------------------------------------------------

    @op("measure_simple")
    def measure_simple(self, kind: str | None = None, **_: Any) -> OpResult:
        if kind is None:
            raise ScpiError(-109)
        what = SIMPLE_MEASUREMENTS.get(kind.upper())
        if what is None:
            raise ScpiError(-224, f"measurement {kind} not modeled by SimuBench")
        rec, used = self.measure_record(channel_index(self._st("measure.source")))
        value = _measure(what, rec.volts, rec.dt_s)
        return OpResult(self._nr3(value if math.isfinite(value) else 9.9e37), used)


#: Offset range at the input (probe 1X) by vertical scale (data sheet): (largest scale, offset limit).
OFFSET_RANGES = ((0.1, 2.0), (1.0, 20.0), (math.inf, 200.0))


@component("siglent_sds_channel_limits")
class SiglentSdsChannelLimits(Component):
    """Vertical scale and offset ranges of one channel, which depend on the impedance and the probe
    (data sheet: 500 uV/div to 10 V/div at 1 MOhm, to 1 V/div at 50 Ohm; offset by scale)."""

    generic = False

    defaults = {
        "scale": "$ch.scale",  # V/div, checked per division at probe 1X
        "offset": "$ch.offset",  # V, range depends on the scale
        "probe": "$ch.probe",  # probe ratio (1, 10, ...)
        "impedance": "$ch.impedance",  # ONEMeg or FIFTy: scale tops out at 10 or 1 V/div
        "min_scale": 500e-6,  # V/div, smallest scale (probe 1X)
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
