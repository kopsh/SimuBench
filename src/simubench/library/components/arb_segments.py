"""Waveform memory with segments (#879): the AWG's DAC-code memory, defined and loaded segment by segment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import FidelityNote, Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError


@dataclass
class Segment:
    """One waveform memory segment: DAC codes, and what the instrument records beside them."""

    codes: np.ndarray
    write_only: bool = False  # defined write-only: it cannot be read back or stored
    comment: str = ""
    name: str = ""


@component("arb_segments")
class ArbSegments(Component):
    """An arbitrary waveform generator's waveform memory: segments of signed DAC codes, played in a
    loop at the sample frequency through a zero-order-hold DAC (the sin(x)/x roll-off of the
    spectrum). A segment is defined (``define``, ``define_new``, write-only variants), filled in
    pieces at a sample offset (``data``), listed (``catalog``), named, commented and deleted.

    Model limits: one segment plays, ``play``; a segment's length is a multiple of ``granularity`` and
    at least ``min_length``; the segments together hold at most ``max_samples`` (-225 beyond it).
    A write past a segment's length is -223 (too much data), a segment that is not defined or a
    number outside the code range is -222; reading back a write-only segment is -221 (the manual says
    only that it cannot be read back). Sequencing, markers and interpolated I/Q modes are not modeled.
    """

    ports = {"out": PortSpec("out", "signal")}
    defaults = {
        "rate": 1.0e9,  # samples per second, the DAC's sample frequency
        "scale": 0.5,  # volts per unit of full-scale code, so a full-scale wave is 1 Vpp
        "dac_bits": 8,  # the DAC's resolution: codes run -2^(bits-1) .. 2^(bits-1) - 1
        "granularity": 1,  # a segment's length is a multiple of this many samples
        "min_length": 1,  # the fewest samples of a segment
        "max_samples": 1_000_000,  # the samples all segments hold together
        "max_id": 1,  # the highest segment number (1 without the sequencing option)
        "play": 1,  # the segment played in a loop (none defined: no signal)
        "max_name": 32,  # most characters of a segment's name
        "max_comment": 256,  # most characters of a segment's comment
        "max_lines": 4096,  # harmonics of the loop's fundamental kept in the spectrum
    }
    units = {"rate": "Sa/s", "scale": "V"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.segments: dict[int, Segment] = {}

    # ---- limits ----------------------------------------------------------------------------------

    def _code_range(self) -> tuple[int, int]:
        half = 1 << (int(self.param("dac_bits")) - 1)
        return -half, half - 1

    def _seg_id(self, value: Any) -> int:
        n = int(value)
        if not 1 <= n <= int(self.param("max_id")):
            raise ScpiError(-222, f"segment {n}: the number is 1..{self.param('max_id')}")
        return n

    def _segment(self, value: Any) -> tuple[int, Segment]:
        n = self._seg_id(value)
        seg = self.segments.get(n)
        if seg is None:
            raise ScpiError(-222, f"segment {n} is not defined")
        return n, seg

    def _check_length(self, length: int, replacing: int | None = None) -> None:
        grain = int(self.param("granularity"))
        if length < int(self.param("min_length")) or length % grain:
            raise ScpiError(
                -222,
                f"segment length {length}: at least {self.param('min_length')} samples, a multiple of {grain}",
            )
        used = sum(s.codes.size for k, s in self.segments.items() if k != replacing)
        if used + length > int(self.param("max_samples")):
            raise ScpiError(-225, f"out of memory: {used + length} of {self.param('max_samples')} samples")

    def _code(self, value: Any) -> int:
        lo, hi = self._code_range()
        n = int(value)
        if not lo <= n <= hi:
            raise ScpiError(-222, f"DAC code {n}: the range is {lo}..{hi}")
        return n

    # ---- defining ----------------------------------------------------------------------------------

    def _define(self, seg_id: int, length: int, init: Any, write_only: bool) -> None:
        self._check_length(length, replacing=seg_id)
        value = 0 if init is None else self._code(init)
        old = self.segments.get(seg_id)
        seg = Segment(np.full(length, value, dtype=np.int16), write_only)
        if old is not None:  # redefining keeps the name and comment of the segment
            seg.comment, seg.name = old.comment, old.name
        self.segments[seg_id] = seg
        self._changed()

    def _free_id(self) -> int:
        for n in range(1, int(self.param("max_id")) + 1):
            if n not in self.segments:
                return n
        raise ScpiError(-225, "out of memory: every segment number is in use")

    @op("define")
    def op_define(self, segment_id: Any = None, length: Any = None, init: Any = None, **_: Any) -> OpResult:
        """``:TRACe:DEFine <segment_id>,<length>[,<init>]``: allocate a segment, every sample ``init``."""
        self._define(self._seg_id(segment_id), int(length), init, False)
        return OpResult()

    @op("define_new")
    def op_define_new(self, length: Any = None, init: Any = None, **_: Any) -> OpResult:
        """``:TRACe:DEFine:NEW? <length>[,<init>]``: allocate a segment under a free number and answer it."""
        n = self._free_id()
        self._define(n, int(length), init, False)
        return OpResult(V.format_int(n))

    @op("define_wonly")
    def op_define_wonly(
        self, segment_id: Any = None, length: Any = None, init: Any = None, **_: Any
    ) -> OpResult:
        """``:TRACe:DEFine:WONLy <segment_id>,<length>[,<init>]``: as define, but it cannot be read back."""
        self._define(self._seg_id(segment_id), int(length), init, True)
        return OpResult()

    @op("define_wonly_new")
    def op_define_wonly_new(self, length: Any = None, init: Any = None, **_: Any) -> OpResult:
        """``:TRACe:DEFine:WONLy:NEW? <length>[,<init>]``: a write-only segment under a free number."""
        n = self._free_id()
        self._define(n, int(length), init, True)
        return OpResult(V.format_int(n))

    @op("delete")
    def op_delete(self, segment_id: Any = None, **_: Any) -> OpResult:
        """``:TRACe:DELete <segment_id>``: free one segment."""
        n, _seg = self._segment(segment_id)
        del self.segments[n]
        self._changed()
        return OpResult()

    @op("delete_all")
    def op_delete_all(self, **_: Any) -> OpResult:
        """``:TRACe:DELete:ALL``: free every segment."""
        self.segments.clear()
        self._changed()
        return OpResult()

    @op("catalog")
    def op_catalog(self, **_: Any) -> OpResult:
        """``:TRACe:CATalog?``: ``<id>,<length>,…`` for the defined segments; ``0, 0`` with none."""
        if not self.segments:
            return OpResult(b"0, 0")
        return OpResult(",".join(f"{n},{s.codes.size}" for n, s in sorted(self.segments.items())).encode())

    # ---- data ------------------------------------------------------------------------------------

    def _codes_of(self, raw: list[bytes]) -> np.ndarray:
        """The data parameter: one IEEE 488.2 block of signed bytes (8-bit DAC) or words, or a list of numbers."""
        if not raw:
            raise ScpiError(-109, "missing data")
        if len(raw) == 1 and raw[0].startswith(b"#"):
            payload = V.parse_block(raw[0])
            width = 1 if int(self.param("dac_bits")) <= 8 else 2
            if len(payload) % width:
                raise ScpiError(-161, f"the block is not a whole number of {width}-byte samples")
            dtype = np.int8 if width == 1 else np.dtype("<i2")
            codes = np.frombuffer(payload, dtype=dtype).astype(np.int16)
        else:
            codes = np.array([V.parse_int(p) for p in raw], dtype=np.int32)
        lo, hi = self._code_range()
        if codes.size and (int(codes.min()) < lo or int(codes.max()) > hi):
            raise ScpiError(-222, f"DAC codes run {lo}..{hi}")
        return codes.astype(np.int16)

    @op("data", fields=("value",))
    def op_data(
        self,
        query: bool = False,
        segment_id: Any = None,
        offset: Any = None,
        data: list[bytes] | None = None,
        **_: Any,
    ) -> OpResult:
        """``:TRACe[:DATA] <segment_id>,<offset>,<block|values>`` writes samples from ``offset`` on;
        ``:TRACe[:DATA]? <segment_id>,<offset>,<length>`` reads them back as a block of signed codes."""
        _n, seg = self._segment(segment_id)
        start = int(offset)
        if start < 0:
            raise ScpiError(-222, "the offset is not negative")
        if query:
            if seg.write_only:
                raise ScpiError(-221, "a write-only segment cannot be read back")
            if not data:
                raise ScpiError(-109, "missing length")
            length = V.parse_int(data[0])
            if length < 0 or start + length > seg.codes.size:
                raise ScpiError(-222, f"{length} samples from {start}: the segment has {seg.codes.size}")
            window = seg.codes[start : start + length]
            width = 1 if int(self.param("dac_bits")) <= 8 else 2
            body = window.astype(np.int8 if width == 1 else np.dtype("<i2")).tobytes()
            return OpResult(V.format_block(body))
        codes = self._codes_of(list(data or []))
        if start + codes.size > seg.codes.size:
            raise ScpiError(-223, f"{codes.size} samples at offset {start}: the segment has {seg.codes.size}")
        seg.codes[start : start + codes.size] = codes
        self._changed()
        return OpResult()

    def _text(self, raw: Any, limit_key: str) -> str:
        text = V.parse_string(raw) if isinstance(raw, bytes) else str(raw)
        if len(text) > int(self.param(limit_key)):
            raise ScpiError(-224, f"at most {self.param(limit_key)} characters")
        return text

    @op("comment")
    def op_comment(self, query: bool = False, segment_id: Any = None, text: Any = None, **_: Any) -> OpResult:
        """``:TRACe:COMMent <segment_id>,<comment>`` / ``:COMMent? <segment_id>``: a segment's comment."""
        _n, seg = self._segment(segment_id)
        if query:
            return OpResult(V.format_string(seg.comment))
        seg.comment = self._text(text, "max_comment")
        return OpResult()

    @op("name")
    def op_name(self, query: bool = False, segment_id: Any = None, text: Any = None, **_: Any) -> OpResult:
        """``:TRACe:NAME <segment_id>,<name>`` / ``:NAME? <segment_id>``: a segment's name."""
        _n, seg = self._segment(segment_id)
        if query:
            return OpResult(V.format_string(seg.name))
        seg.name = self._text(text, "max_name")
        return OpResult()

    # ---- playing ---------------------------------------------------------------------------------

    def _changed(self) -> None:
        """The loop changed: the instrument's signals are recomputed."""
        self.session.invalidate()

    def points(self) -> int:
        """The samples of the segment played (the ArbSource protocol); 0 with none."""
        seg = self.segments.get(int(self.param("play")))
        return 0 if seg is None else int(seg.codes.size)

    def reset(self) -> None:
        """``*RST`` does not clear waveform memory (the manual lists no such effect)."""

    def output(self, port: str) -> Signal:
        seg = self.segments.get(int(self.param("play")))
        rate = float(self.param("rate"))
        if seg is None or seg.codes.size == 0 or rate <= 0:
            return Signal.zero()
        n = seg.codes.size
        lo, _hi = self._code_range()
        full = float(-lo - 1) or 1.0  # the code of +1: 127 for 8 bits
        table = np.clip(seg.codes / full, -1.0, 1.0) * float(self.param("scale"))
        spectrum = np.fft.fft(table) / n
        dc = float(spectrum[0].real)
        k = np.arange(1, int(self.param("max_lines")) + 1)
        # a zero-order-hold DAC: harmonic k of the loop is weighted by sinc(k / n), the sin(x)/x roll-off
        coef = spectrum[k % n] * np.sinc(k / n)
        keep = np.abs(coef) > 1e-15
        sig = Signal.lines(k[keep] * rate / n, 2 * coef[keep])
        notes = (FidelityNote("truncated", f"waveform memory: {int(keep.sum())} harmonics"),)
        return Signal(sig.freq, sig.amp, sig.t0_ns, dc, notes)
