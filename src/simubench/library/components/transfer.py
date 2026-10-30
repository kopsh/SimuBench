"""Waveform transfer: a scope's record as the reply to a data query (instrument-authoring.md 3.1).

One component for the encodings scopes use, set by parameters that may be bound to settings, so a
new vendor's format is a definition, not code:

* ``encoding: block``: an IEEE 488.2 definite-length block of ADC codes (``width`` 8 or 16 bits,
  ``byteorder``, ``unsigned``; ``block_digits`` fixes the length digits, e.g. 9 for ``#9...``);
* ``encoding: ascii``: comma-separated NR3 numbers (``digits``);
* ``encoding: text``: numbers in a printf ``format`` joined by ``separator`` (a command's reply
  template adds what goes around them, e.g. ``'{header} " {value} "'``).

* ``encoding: block`` with ``values: volts`` and ``float: 32`` or ``64``: a block of IEEE floats
  (R&S ``FORMat REAL,32``).

The points sent are ``first``, then every ``step``-th, ``count`` of them (0: to the end, at most
``max_points`` when that is set). ``count_mode: spread`` takes ``count`` points spread over the
record instead of the first ones (a points mode: 1000 points of a 100 000-point record are every
100th). ``over_limit`` says what an explicit ``count`` above ``max_points`` does: ``allow`` it,
``truncate`` it, or refuse the read with an ``error`` (-222).

A preamble is declared, not coded (``header``, op ``header``): a binary block of fields at byte
offsets (Siglent's 346-byte WAVEDESC) or a text line of fields (an ASCII preamble). Each field takes
a constant, a ``figure`` of the record and the transfer's selection (``figures`` below) or a
``setting`` (``{n}`` is the source channel), then optionally ``map`` (value -> written value),
``nearest`` (the index of the closest list entry, on a log scale: a time/div enumeration) and
``scale``. ``values`` are volts or ADC codes: ``round((v -/+ offset) / scale *
codes_per_div)`` limited to ``adc_bits`` and aligned to ``width`` (shifted up or down), as a scope's
digitizer writes them. Which record is current (acquire anew when a read starts, or keep the last
one) is the acquisition component's business: it is asked through ``transfer_record``.
"""

from __future__ import annotations

import math
import struct
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope import Record, channel_index


@component("waveform_transfer")
class WaveformTransfer(Component):
    """The selected points of a channel's record, encoded as text, an ASCII list or a binary block."""

    defaults = {
        "record": "acq",  # the acquisition component holding the records
        "source": "",  # the channel (C1, CHANnel1, 1); empty: the command's channel
        "first": 0,  # index of the first point sent
        "count": 0,  # points sent; 0: to the end, at most max_points when that is set
        "step": 1,  # send every step-th point
        "count_mode": "first",  # first: the first count points; spread: count points over the record
        "max_points": 0,  # 0: no limit
        "over_limit": "allow",  # an explicit count above max_points: allow | truncate | error (-222)
        "encoding": "block",  # block | ascii | text
        "values": "codes",  # codes | volts
        "float": 0,  # block of volts as IEEE floats: 32 | 64 (0: codes or volts as integers)
        "format": "%.6e",  # text
        "separator": " ",  # text
        "digits": 8,  # ascii
        "codes_per_div": 25.0,  # ADC codes per division of the record scale
        "offset": "subtract",  # subtract: (v - offset); add: (v + offset), the record's offset setting
        "adc_bits": 8,  # bits of the ADC codes (signed around zero)
        "width": 8,  # bits of a block word: codes shift up or down to fit
        "unsigned": False,  # block codes offset by 2**(width-1)
        "byteorder": "little",  # little | big (also LSBFirst | MSBFirst)
        "block_digits": 0,  # 0: as few as the length needs
        "trailer": "",  # appended after a block, e.g. a line feed
        "no_record": "no acquisition for this source",  # the -230 message when there is no record
        # describe: NAME -> a format of the record's figures (points, first, step, dt, x_offset,
        # offset, scale, gain, timebase, count), e.g. {HORIZ_INTERVAL: "{dt:.9e}"}
        "descriptor": {},
        "descriptor_item": "{name} : {value}",  # how describe writes one entry
        "descriptor_separator": " ",  # between describe's entries
        # header: a declared preamble (op header). {encoding: binary | text, size: bytes (binary),
        # byteorder, block_digits, trailer, separator (text), fields: [{at: byte offset (binary),
        # type: i8 | u8 | i16 | u16 | i32 | u32 | i64 | f32 | f64 | str, value | figure | setting,
        # map, nearest, scale, format (text: a printf format)}]}
        "header": None,
    }

    #: Field types of a binary header: struct codes.
    HEADER_TYPES = {
        "i8": "b", "u8": "B", "i16": "h", "u16": "H", "i32": "i", "u32": "I", "i64": "q", "f32": "f", "f64": "d",
    }  # fmt: skip

    def _source(self, channel: int | None) -> int:
        source = self.param("source")
        if source not in (None, ""):
            return channel_index(source)
        if channel is None:
            raise ScpiError(-300, f"{self.uid}: no source channel")
        return channel

    def selection(self, size: int) -> np.ndarray:
        """Indices of the points sent from a record of ``size`` points."""
        first = int(self.param("first"))
        count = int(self.param("count"))
        step = max(1, int(self.param("step")))
        most = int(self.param("max_points"))
        if count > 0 and most > 0 and count > most:
            over = str(self.param("over_limit"))
            if over == "error":
                raise ScpiError(-222, f"at most {most} points per read")
            if over == "truncate":
                count = most
        sel = np.arange(first, size, step)
        if count > 0 and str(self.param("count_mode")) == "spread" and sel.size > count:
            sel = sel[:: sel.size // count]
        limit = count if count > 0 else most
        return sel[:limit] if limit > 0 else sel

    def codes(self, rec: Record) -> np.ndarray:
        """ADC codes of the whole record, aligned to the output word."""
        bits = int(self.param("adc_bits"))
        width = int(self.param("width"))
        sign = 1.0 if str(self.param("offset")).lower() == "add" else -1.0
        half = 1 << (bits - 1)
        codes = np.round((rec.volts + sign * rec.offset) / rec.scale * float(self.param("codes_per_div")))
        codes = np.clip(codes, -half, half - 1).astype(np.int64)
        if width > bits:
            codes = codes << (width - bits)
        elif width < bits:
            codes = codes >> (bits - width)
        if self.param("unsigned") in (True, 1, "1", "ON", "on"):
            codes = codes + (1 << (width - 1))
        return codes

    def encode(self, rec: Record | None) -> bytes:
        encoding = str(self.param("encoding")).lower()
        if rec is None:
            values = np.zeros(0)
        else:
            sel = self.selection(rec.volts.size)
            values = (self.codes(rec) if self.param("values") == "codes" else rec.volts)[sel]
        if encoding == "text":
            fmt = str(self.param("format"))
            return str(self.param("separator")).join(fmt % x for x in values.tolist()).encode()
        if encoding == "ascii":
            digits = int(self.param("digits"))
            return b",".join(V.format_float(x, digits) for x in values.tolist())
        width = int(self.param("width"))
        unsigned = self.param("unsigned") in (True, 1, "1", "ON", "on")
        big = str(self.param("byteorder")).upper() in ("BIG", "MSBFIRST")
        bits = int(self.param("float") or 0)
        if bits:
            if bits not in (32, 64):
                raise ScpiError(-300, f"{self.uid}: float is 32 or 64")
            payload = values.astype(("<" if not big else ">") + ("f4" if bits == 32 else "f8")).tobytes()
        elif width == 8:
            payload = values.astype("u1" if unsigned else "i1").tobytes()
        else:
            payload = V.encode_words(values, little_endian=not big, unsigned=unsigned)
        digits = int(self.param("block_digits"))
        if digits:
            block = b"#" + str(digits).encode() + f"{len(payload):0{digits}d}".encode() + payload
        else:
            block = V.format_block(payload)
        return block + str(self.param("trailer")).encode()

    def transfer(self, channel: int | None) -> OpResult:
        """Encode the current record of the source channel (asking the acquisition for it)."""
        assert self.owner is not None
        acq = self.owner.components[str(self.param("record"))]
        n = self._source(channel)
        rec, used = acq.transfer_record(n, int(self.param("first")))  # type: ignore[attr-defined]
        if rec is None:
            self.owner.push_error(-230, str(self.param("no_record")))
        return OpResult(self.encode(rec), used)

    @op("describe")
    def describe(self, query: bool = False, channel: int | None = None, **_: Any) -> OpResult:
        """A text descriptor of the record a transfer reads (LeCroy's ``C1:INSPECT? "WAVEDESC"``):
        one ``descriptor_item`` per ``descriptor`` entry, figures of the record, this transfer's
        selection and the acquisition (``count``: acquisitions so far)."""
        assert self.owner is not None
        acq = self.owner.components[str(self.param("record"))]
        n = self._source(channel)
        rec, used = acq.read_record(n, start=False)  # type: ignore[attr-defined]
        if rec is None:
            self.owner.push_error(-230, str(self.param("no_record")))
            return OpResult(b"", used)
        figures = self.figures(rec, n)
        item = str(self.param("descriptor_item"))
        parts = [
            item.format(name=name, value=str(fmt).format(**figures))
            for name, fmt in (self.param("descriptor") or {}).items()
        ]
        return OpResult(str(self.param("descriptor_separator")).join(parts).encode(), used)

    # ---- a declared preamble ------------------------------------------------------------------------

    def figures(self, rec: Record, n: int) -> dict[str, Any]:
        """What header fields and descriptor entries can name: the record, this transfer's
        selection and the acquisition."""
        assert self.owner is not None
        acq = self.owner.components[str(self.param("record"))]
        sel = self.selection(rec.volts.size)
        probe = float(acq.probe_ratio(n)) if hasattr(acq, "probe_ratio") else 1.0
        bits = int(acq.adc_unit().param("bits")) if hasattr(acq, "adc_unit") else int(self.param("adc_bits"))
        per_div = acq.adc_codes_per_div() if hasattr(acq, "adc_codes_per_div") else 0.0
        width = int(self.param("width"))
        first = int(sel[0]) if sel.size else int(self.param("first"))
        last = int(sel[-1]) if sel.size else first - 1
        span = int(sel[1] - sel[0]) if sel.size > 1 else 1
        return {
            "points": int(sel.size),
            "record_points": rec.volts.size,
            "first": first,
            "step": max(1, int(self.param("step"))),
            "bytes": int(sel.size) * (int(self.param("float") or 0) or width) // 8,
            "dt": rec.dt_s,
            "x_offset": -rec.trigger_index * rec.dt_s,
            # the time of the first point sent, and one sent point's span after the last (trigger: 0)
            "x_start": (first - rec.trigger_index) * rec.dt_s,
            "x_stop": (last + span - rec.trigger_index) * rec.dt_s,
            "offset": rec.offset,
            "scale": rec.scale,
            "probe": probe,
            "scale_input": rec.scale / probe,
            "offset_input": rec.offset / probe,
            "bits": bits,
            "codes_per_div": per_div,
            "gain": rec.scale / per_div if per_div else 0.0,
            "timebase": acq.timebase_scale(),  # type: ignore[attr-defined]
            "count": acq.acq_count,  # type: ignore[attr-defined]
            "channel": n,
            "channel0": n - 1,
        }

    def _field(self, spec: dict[str, Any], figures: dict[str, Any], n: int) -> Any:
        assert self.owner is not None
        if "figure" in spec:
            value = figures[str(spec["figure"])]
        elif "setting" in spec:
            value = self.owner.get_state(str(spec["setting"]).replace("{n}", str(n)))
        else:
            value = spec.get("value")
        if spec.get("map") is not None:
            table = spec["map"]
            value = table.get(value, table.get(str(value), value))
        if spec.get("nearest"):
            ladder = [float(x) for x in spec["nearest"]]
            v = float(value)
            value = min(range(len(ladder)), key=lambda i: abs(math.log10(ladder[i] / v)) if v > 0 else 0)
        if spec.get("scale") is not None:
            value = float(value) * float(spec["scale"])
        return value

    def header_bytes(self, rec: Record, n: int) -> bytes:
        """The declared preamble of a record (without the block around a binary one)."""
        h = self.param("header") or {}
        figures = self.figures(rec, n)
        fields = h.get("fields") or []
        if str(h.get("encoding", "binary")) == "text":
            parts = []
            for spec in fields:
                value = self._field(spec, figures, n)
                fmt = spec.get("format")
                parts.append(str(fmt) % value if fmt else str(value))
            return str(h.get("separator", ",")).join(parts).encode()
        order = ">" if str(h.get("byteorder", "little")).upper() in ("BIG", "MSBFIRST") else "<"
        out = bytearray(int(h.get("size", 0)))
        for spec in fields:
            at, kind = int(spec["at"]), str(spec.get("type", "i32"))
            value = self._field(spec, figures, n)
            if kind == "str":
                data = str(value).encode()
                out[at : at + len(data)] = data
                continue
            code = self.HEADER_TYPES.get(kind)
            if code is None:
                raise ScpiError(-300, f"{self.uid}: header field type {kind!r}")
            if code in "fd":
                packed = struct.pack(order + code, float(value))
            else:
                packed = struct.pack(order + code, int(value))
            if at + len(packed) > len(out):
                out.extend(b"\0" * (at + len(packed) - len(out)))
            out[at : at + len(packed)] = packed
        return bytes(out)

    @op("header")
    def header(self, query: bool = False, channel: int | None = None, **_: Any) -> OpResult:
        """The declared preamble of the source's record: a binary block (``block_digits``, then
        ``trailer``) or a text line. Without a record: an empty block and -230."""
        assert self.owner is not None
        h = self.param("header") or {}
        acq = self.owner.components[str(self.param("record"))]
        n = self._source(channel)
        rec, used = acq.read_record(n)  # type: ignore[attr-defined]
        text = str(h.get("encoding", "binary")) == "text"
        if rec is None:
            self.owner.push_error(-230, str(self.param("no_record")))
            payload = b""
        else:
            payload = self.header_bytes(rec, n)
        if text:
            return OpResult(payload, used)
        digits = int(h.get("block_digits", 0))
        if digits:
            block = b"#" + str(digits).encode() + f"{len(payload):0{digits}d}".encode() + payload
        else:
            block = V.format_block(payload)
        return OpResult(block + str(h.get("trailer", "")).encode(), used)

    @op("data")
    def data(self, query: bool = False, channel: int | None = None, **_: Any) -> OpResult:
        """``:WAVeform:DATA?``, ``C1:INSPECT?``: the selected points of the source's record."""
        return self.transfer(channel)
