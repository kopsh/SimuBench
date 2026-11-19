"""Waveform transfer: a scope's record as the reply to a data query (instrument-authoring.md 3.1).

One component for the encodings scopes use, set by parameters that may be bound to settings, so a
new vendor's format is a definition, not code:

* ``encoding: block``: an IEEE 488.2 definite-length block of ADC codes (``width`` 8 or 16 bits,
  ``byteorder``, ``unsigned``; ``block_digits`` fixes the length digits, e.g. 9 for ``#9...``;
  ``indefinite`` sends ``#0`` and the data to the message end instead);
* ``encoding: ascii``: comma-separated NR3 numbers (``digits``); ``ascii_block``: the same list
  inside a definite-length block (``block_digits``, ``trailer``);
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
one) is the acquisition component's business: it is asked through ``transfer_record``. The
``record`` ref names the acquisition, which implements the ``Acquisition`` protocol
(``engine.component``) — checked when the definition loads and again when a command runs;
``AcquisitionInput`` is its optional input chain (probe ratio, ADC, codes per division).
"""

from __future__ import annotations

import math
import struct
from typing import Any

import numpy as np
from typing_extensions import get_protocol_members

from ...engine.component import (
    Acquisition,
    AcquisitionInput,
    Component,
    MathSource,
    OpResult,
    binding_of,
    component,
    op,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from . import adc
from .scope_math import function_index
from .scope_record import Points, Record, channel_index


@component("waveform_transfer")
class WaveformTransfer(Component):
    """The selected points of a channel's record, encoded as text, an ASCII list or a binary
    block. The source may name a math function (``FUNCtion<F>``): the record then comes
    from the ``math`` block instead of the acquisition, with the same encoding and preamble."""

    label = "波形传输"
    label_en = "Waveform transfer"
    about = "把通道记录的选定点编码成文本、ASCII 列表或二进制块。"
    about_en = "Encodes the selected points of a channel's record as text, ASCII or a binary block."

    refs = {"record": (Acquisition, "instrument"), "math": (MathSource, "instrument")}
    optional_refs = frozenset({"math"})
    defaults = {
        "record": "acq",  # the acquisition component holding the records
        "math": "",  # the math block a FUNCtion<F> source reads from; empty: channels only
        "source": "",  # the channel (C1, CHANnel1, 1) or function (FUNC1); empty: the command's channel
        "first": 0,  # index of the first point sent
        "count": 0,  # points sent; 0: to the end, at most max_points when that is set
        "step": 1,  # send every step-th point
        "count_mode": "first",  # first: the first count points; spread: count points over the record
        "max_points": 0,  # 0: no limit
        "over_limit": "allow",  # an explicit count above max_points: allow | truncate | error (-222)
        "encoding": "block",  # block | ascii | ascii_block | text
        "values": "codes",  # codes | volts
        "float": 0,  # block of volts as IEEE floats: 32 | 64 (0: codes or volts as integers)
        "format": "%.6e",  # text
        "separator": " ",  # text
        "digits": 8,  # ascii
        "codes_per_div": 25.0,  # ADC codes per division of the record scale ("adc": the acquisition's ADC's)
        "offset": "subtract",  # subtract: (v - offset); add: (v + offset), the record's offset setting
        "adc_bits": 8,  # bits of the ADC codes, signed around zero ("adc": the acquisition's ADC's)
        "width": 8,  # bits of a block word: codes shift up or down to fit
        "unsigned": False,  # block codes offset by 2**(width-1)
        "reserve_lowest": False,  # the lowest output code means "no data" (a hole): samples stay above it
        "byteorder": "little",  # little | big (also LSBFirst | MSBFirst)
        "block_digits": 0,  # 0: as few as the length needs
        # an indefinite-length block: #0 and the data up to the message end, no length (Infiniium
        # :WAVeform:STReaming ON); a setting may switch it
        "indefinite": False,
        "trailer": "",  # appended after a block, e.g. a line feed
        "no_record": "no acquisition for this source",  # the -230 message when there is no record
        "seg_all": False,  # :WAVeform:SEGMented:ALL: the data read returns every segment
        "coupling": "DC",  # the input coupling word :WAVeform:COUPling? replies (the guide: always DC)
        # describe: NAME -> a format of the record's figures (points, first, step, dt, x_offset,
        # offset, scale, gain, timebase, count), e.g. {HORIZ_INTERVAL: "{dt:.9e}"}
        "descriptor": {},
        "descriptor_item": "{name} : {value}",  # how describe writes one entry
        "descriptor_separator": " ",  # between describe's entries
        # header: a declared preamble (op header). {encoding: binary | text, size: bytes (binary),
        # byteorder, block_digits, trailer, separator (text), fields: [{at: byte offset (binary),
        # type: i8 | u8 | i16 | u16 | i32 | u32 | i64 | f32 | f64 | str, value | figure | setting,
        # map, nearest, scale, format (text: a printf format), when: {setting: value} with else};
        # a text field's setting may be a list, joined by its format: '"%s:%s"']}
        "header": None,
    }

    #: The parameters that may say ``adc``: take the value from the acquisition's ADC (#1445).
    ADC_PARAMS = ("adc_bits", "codes_per_div")

    @classmethod
    def literal_problems(cls, params: dict[str, Any]) -> list[tuple[str, str]]:
        """``adc_bits`` / ``codes_per_div`` are a number or ``adc`` (#1460): a misspelt ``ADC``
        is a definition error when it loads, not a ValueError at the first read."""
        out = []
        for key in cls.ADC_PARAMS:
            value = params.get(key)
            if not isinstance(value, str) or value == "adc" or binding_of(value) is not None:
                continue
            try:
                float(value)
            except ValueError:
                out.append(
                    (key, f"waveform_transfer.{key} 取数值或 adc（取采集链 ADC 的值），得到 {value!r}")
                )
        return out

    @classmethod
    def ref_problems(cls, params: dict[str, Any], targets: dict[str, type | None]) -> list[tuple[str, str]]:
        """``adc`` needs an acquisition with an ADC (:class:`AcquisitionInput`) behind ``record``
        (#1460): checked when the definition loads, not at the first read (-300)."""
        target = targets.get("record")
        if target is None:
            return []
        missing = [
            n for n in get_protocol_members(AcquisitionInput) if not callable(getattr(target, n, None))
        ]
        if not missing:
            return []
        name = params.get("record", cls.defaults["record"])
        return [
            (
                key,
                f"waveform_transfer.{key} 为 adc，但 record 指向的 {name!r} 没有 ADC 采集链（缺 {', '.join(missing)}）",
            )
            for key in cls.ADC_PARAMS
            if params.get(key) == "adc"
        ]

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

    def _read(
        self, channel: int | None, first: int | None = None, start: bool = True
    ) -> tuple[Record | None, int, int, bool]:
        """The record the source names — a channel's, from the acquisition, or a math function's
        (``FUNCtion<F>``, #1074), from the ``math`` block — the virtual time getting it took, the
        number its preamble's ``{n}`` fields use, and whether it is a function's record (an input
        chain the preamble figures name does not apply to one)."""
        source = self.param("source")
        f = function_index(source) if source not in (None, "") else None
        if f is not None:
            block = self.ref("math")
            if block is None or not isinstance(block, MathSource):
                raise ScpiError(-224, f"{source}: this model has no math functions to transfer")
            if first is not None:
                rec, used = block.transfer_function(f, first)
            else:
                rec, used = block.read_function(f, start)
            return rec, used, f, True
        acq = self._acquisition()
        n = self._source(channel)
        rec, used = acq.transfer_record(n, first) if first is not None else acq.read_record(n, start)
        return rec, used, n, False

    def _code_scale(self) -> tuple[int, float]:
        """The bits and codes per division the codes are written with: the transfer's own
        ``adc_bits`` / ``codes_per_div``, or, where one is ``adc``, the acquisition's ADC's (#1445) —
        a definition that changes the converter's bits then changes the codes sent with it."""
        bits, per_div = self.param("adc_bits"), self.param("codes_per_div")
        if bits == "adc" or per_div == "adc":
            acq = self._acquisition()
            if not isinstance(acq, AcquisitionInput):
                raise ScpiError(
                    -300, f"{self.uid}: adc_bits / codes_per_div 'adc' needs an acquisition with an ADC"
                )
            bits = acq.adc_unit().param("bits") if bits == "adc" else bits
            per_div = acq.adc_codes_per_div() if per_div == "adc" else per_div
        try:
            return int(bits), float(per_div)
        except (TypeError, ValueError):  # the definition check reports it; never a Python error here
            raise ScpiError(
                -300, f"{self.uid}: adc_bits {bits!r} / codes_per_div {per_div!r} is not a number"
            ) from None

    def _acquisition(self) -> Acquisition:
        """The acquisition the ``record`` ref names (``engine.component.Acquisition``): the refs
        check validates the protocol when the definition loads, this when a command runs."""
        acq = self.ref("record")
        if not isinstance(acq, Acquisition):
            raise ScpiError(-300, f"{self.uid}: {self.param('record')!r} keeps no records to transfer")
        return acq

    def selection(self, size: int, first: int | None = None, count: int | None = None) -> Points:
        """Indices of the points sent from a record of ``size`` points; ``first`` and ``count``
        given: this read's own, instead of the parameters (``:WAVeform:DATA? <start>,<size>``)."""
        first = int(self.param("first")) if first is None else first
        count = int(self.param("count")) if count is None else count
        step = max(1, int(self.param("step")))
        most = int(self.param("max_points"))
        if count > 0 and most > 0 and count > most:
            over = str(self.param("over_limit"))
            if over == "error":
                raise ScpiError(-222, f"at most {most} points per read")
            if over == "truncate":
                count = most
        sel = range(first, size, step)  # a range: a deep record's indices are never listed (#1012)
        if count > 0 and str(self.param("count_mode")) == "spread" and len(sel) > count:
            sel = sel[:: len(sel) // count]
        limit = count if count > 0 else most
        return Points(sel[:limit] if limit > 0 else sel)

    def codes(self, rec: Record, volts: np.ndarray | None = None) -> np.ndarray:
        """ADC codes of ``volts`` (default: the whole record), aligned to the output word."""
        bits, per_div = self._code_scale()
        width = int(self.param("width"))
        sign = 1.0 if str(self.param("offset")).lower() == "add" else -1.0
        codes = adc.codes(
            rec.volts if volts is None else volts,
            getattr(rec, "code_scale", rec.scale),  # the converter's step below a magnified division (#1265)
            sign * rec.offset,
            per_div,
            bits,
        ).astype(np.int64)
        if width > bits:
            codes = codes << (width - bits)
        elif width < bits:
            codes = codes >> (bits - width)
        if self.param("unsigned") in (True, 1, "1", "ON", "on"):
            codes = codes + (1 << (width - 1))
        if self.param("reserve_lowest") in (True, 1, "1", "ON", "on"):
            lowest = 0 if self.param("unsigned") in (True, 1, "1", "ON", "on") else -(1 << (width - 1))
            step = 1 << max(0, width - bits)  # the code above the lowest the data can reach
            codes = np.maximum(codes, lowest + step)
        return codes

    def encode(self, rec: Record | None, first: int | None = None, count: int | None = None) -> bytes:
        encoding = str(self.param("encoding")).lower()
        if rec is None:
            values = np.zeros(0)
        else:
            if first is not None and first >= rec.points:
                raise ScpiError(-222, f"the record has {rec.points} points; the first point sent is {first}")
            sel = self.selection(rec.points, first, count)
            # only the points sent are computed: a deep record computes what is read (#1012)
            picked = rec.take(sel)
            values = self.codes(rec, picked) if self.param("values") == "codes" else picked
        if encoding == "text":
            fmt = str(self.param("format"))
            return str(self.param("separator")).join(fmt % x for x in values.tolist()).encode()
        if encoding in ("ascii", "ascii_block"):
            text = b",".join(V.format_float(x, int(self.param("digits"))) for x in values.tolist())
            if encoding == "ascii":
                return text
            return self._block(text)
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
        return self._block(payload)

    def _block(self, payload: bytes) -> bytes:
        if self.param("indefinite") in (True, 1, "1", "ON", "on"):
            return b"#0" + payload + str(self.param("trailer")).encode()
        digits = int(self.param("block_digits"))
        if digits:
            block = b"#" + str(digits).encode() + f"{len(payload):0{digits}d}".encode() + payload
        else:
            block = V.format_block(payload)
        return block + str(self.param("trailer")).encode()

    def transfer(self, channel: int | None, first: int | None = None, count: int | None = None) -> OpResult:
        """Encode the current record of the source — a channel's or a math function's (#1074);
        ``first`` and ``count``: this read's points, instead of the parameters'.

        With ``:WAVeform:SEGMented:ALL ON`` and a channel source (#1066), the record is every
        segment of the latest acquisition as one (the guide, p. 1624), ``first``/``count`` then
        indexing into the combined record (the guide: ``<start>,<size>`` still work)."""
        assert self.owner is not None
        rec, used, n, _ = self._read(channel, int(self.param("first")) if first is None else first)
        if rec is None:
            self.owner.push_error(-230, str(self.param("no_record")))
        else:
            rec = self._all_segments(rec, n) or rec
        return OpResult(self.encode(rec, first, count), used)

    def _all_segments(self, rec: Record, n: int) -> Record | None:
        """Every segment of the latest acquisition as one record (``:WAVeform:SEGMented:ALL ON``,
        #1066), or None when this read is single-segment: no flag, a function source (the guide:
        functions transfer the current segment only), or the latest acquisition was not a
        segmented one. The segments share the timing and the vertical, so their samples
        concatenate over the first segment's origin (a deep segment computes whole)."""
        if not V.flag(self.param("seg_all")):
            return None
        read_segments = getattr(self._acquisition(), "read_segments", None)
        recs = read_segments(n) if callable(read_segments) else None
        if not recs:
            return None
        first = recs[0]
        return Record(
            channel=first.channel,
            t_start_ns=first.t_start_ns,
            dt_s=first.dt_s,
            trigger_index=first.trigger_index,
            scale=first.scale,
            offset=first.offset,
            triggered=all(r.triggered for r in recs),
            volts=np.concatenate([r.volts for r in recs]),
            x_origin_s=first.x_origin_s,
            trigger_ns=first.trigger_ns,
        )

    @op("describe")
    def op_describe(self, query: bool = False, channel: int | None = None, **_: Any) -> OpResult:
        """A text descriptor of the record a transfer reads (LeCroy's ``C1:INSPECT? "WAVEDESC"``):
        one ``descriptor_item`` per ``descriptor`` entry, figures of the record, this transfer's
        selection and the acquisition (``count``: acquisitions so far)."""
        assert self.owner is not None
        rec, used, n, function = self._read(channel, start=False)
        if rec is None:
            self.owner.push_error(-230, str(self.param("no_record")))
            return OpResult(b"", used)
        figures = self.figures(rec, n, function=function)
        item = str(self.param("descriptor_item"))
        parts = [
            item.format(name=name, value=str(fmt).format(**figures))
            for name, fmt in (self.param("descriptor") or {}).items()
        ]
        return OpResult(str(self.param("descriptor_separator")).join(parts).encode(), used)

    # ---- a declared preamble ------------------------------------------------------------------------

    def figures(self, rec: Record, n: int, function: bool = False) -> dict[str, Any]:
        """What header fields and descriptor entries can name: the record, this transfer's
        selection and the acquisition. ``function``: the record is a math function's (#1074), to
        which an input chain the preamble figures name does not apply."""
        assert self.owner is not None
        acq = self._acquisition()
        sel = self.selection(rec.points)
        # the acquisition's input chain when it declares one (AcquisitionInput), else this
        # transfer's own parameters; a math record has none
        chain = None if function else (acq if isinstance(acq, AcquisitionInput) else None)
        probe = float(chain.probe_ratio(n)) if chain is not None else 1.0
        bits = int(chain.adc_unit().param("bits")) if chain is not None else self._code_scale()[0]
        per_div = chain.adc_codes_per_div() if chain is not None else 0.0
        width = int(self.param("width"))
        first = int(sel[0]) if sel.size else int(self.param("first"))
        last = int(sel[-1]) if sel.size else first - 1
        span = int(sel[1] - sel[0]) if sel.size > 1 else max(1, int(self.param("step")))
        unsigned = self.param("unsigned") in (True, 1, "1", "ON", "on")
        # codes per division of the word sent: the ADC's, shifted up or down to fit the word
        own_bits, own_per_div = self._code_scale()
        word_per_div = own_per_div * 2.0 ** (width - own_bits)
        figures = {
            "points": int(sel.size),
            "record_points": rec.points,
            "first": first,
            "step": span,  # the spacing of the points sent (a points mode spreads them)
            "x_increment": span * rec.dt_s,
            "x_reference": 0,  # the point x_start belongs to
            # code -> volts: (code - y_reference) * y_increment + y_origin
            "y_increment": getattr(rec, "code_scale", rec.scale) / word_per_div if word_per_div else 0.0,
            # ``+ 0.0``: no negative zero from negating a zero offset
            "y_origin": (rec.offset if str(self.param("offset")).lower() != "add" else -rec.offset) + 0.0,
            "y_reference": (1 << (width - 1)) if unsigned else 0,
            "bytes": int(sel.size) * (int(self.param("float") or 0) or width) // 8,
            "dt": rec.dt_s,
            "x_offset": rec.origin,
            # the time of the first point sent, and one sent point's span after the last (trigger: 0)
            "x_start": rec.origin + first * rec.dt_s,
            "x_stop": rec.origin + (last + span) * rec.dt_s,
            "offset": rec.offset,
            "scale": rec.scale,
            "probe": probe,
            "scale_input": rec.scale / probe,
            "offset_input": rec.offset / probe,
            "bits": bits,
            "codes_per_div": per_div,
            "gain": rec.scale / per_div if per_div else 0.0,
            "timebase": acq.timebase_scale(),
            "count": acq.acq_count,
            "channel": n,
            "channel0": n - 1,
            # the input coupling word the model declares (:WAVeform:COUPling?, #1066)
            "coupling": str(self.param("coupling")),
        }
        # the appended <segment count> preamble element (the guide's table 19): only while the
        # ALL-ON read returns segments of a channel; left out otherwise, which drops the element
        records = getattr(acq, "segment_records", None)
        if V.flag(self.param("seg_all")) and not function and records:
            figures["segments"] = len(records)
        return figures

    def _field(self, spec: dict[str, Any], figures: dict[str, Any], n: int) -> Any:
        assert self.owner is not None
        when = spec.get("when")
        if when and any(str(self.owner.get_state(p)) != str(v) for p, v in when.items()):
            return spec.get("else")  # the field's value while the settings are otherwise
        if "figure" in spec:
            # a figure the current state has no value for (the segment count outside the ALL-ON
            # read): None, and the text preamble drops the field
            value = figures.get(str(spec["figure"]))
        elif isinstance(spec.get("setting"), list):
            # several settings in one text field, joined by ``format`` (the Infiniium preamble's
            # "model:serial" frame field, #1441): a tuple for the printf format
            return tuple(self.owner.get_state(str(p).replace("{n}", str(n))) for p in spec["setting"])
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

    def header_bytes(self, rec: Record, n: int, function: bool = False) -> bytes:
        """The declared preamble of a record (without the block around a binary one)."""
        h = self.param("header") or {}
        figures = self.figures(rec, n, function=function)
        fields = h.get("fields") or []
        if str(h.get("encoding", "binary")) == "text":
            parts = []
            for spec in fields:
                value = self._field(spec, figures, n)
                if value is None:
                    continue  # a ``when`` field with no ``else``: absent here (the segment count)
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
        ``trailer``) or a text line. Without a record the query does not answer and -230 waits
        in the error queue (``no_record``), as a scope's own ``:WAVeform:PREamble?`` does."""
        assert self.owner is not None
        h = self.param("header") or {}
        rec, used, n, function = self._read(channel)
        if rec is None:
            raise ScpiError(-230, str(self.param("no_record")))
        text = str(h.get("encoding", "binary")) == "text"
        payload = self.header_bytes(rec, n, function=function)
        if text:
            return OpResult(payload, used)
        digits = int(h.get("block_digits", 0))
        if digits:
            block = b"#" + str(digits).encode() + f"{len(payload):0{digits}d}".encode() + payload
        else:
            block = V.format_block(payload)
        return OpResult(block + str(h.get("trailer", "")).encode(), used)

    @op("field")
    def field(self, query: bool = False, channel: int | None = None, field: dict[str, Any] | None = None,
              **_: Any) -> OpResult:  # fmt: skip
        """One field written the way a declared preamble writes it (``header.fields``: a figure, a
        setting, a value, ``when``/``else``, ``map``, ``scale``, ``format``; ``{n}`` in a setting is
        the source channel), e.g. ``:WAVeform:XRANge?`` = ``{figure: timebase, scale: 10.0}`` or
        ``:WAVeform:YUNits?`` = ``{setting: "ch{n}.units"}``. Without a record: -230 and no value."""
        assert self.owner is not None
        if not field:
            raise ScpiError(-300, f"{self.uid}: field names nothing")
        acq = self._acquisition()
        n = self._source(channel)
        rec, used = acq.read_record(n, start=False)
        if rec is None:
            self.owner.push_error(-230, str(self.param("no_record")))
            return OpResult(b"", used)
        value = self._field(field, self.figures(rec, n), n)
        fmt = field.get("format")
        if fmt:
            return OpResult((str(fmt) % value).encode(), used)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return OpResult(str(value).encode(), used)
        if isinstance(value, int):
            return OpResult(str(value).encode(), used)
        return OpResult(self.owner.format_number(float(value)), used)

    @op("figure")
    def figure(self, query: bool = False, channel: int | None = None, what: str = "points",
               format: str = "", scale: float = 1.0, **_: Any) -> OpResult:  # fmt: skip
        """One figure of the source's record and this transfer's selection (``figures``), e.g.
        ``:WAVeform:XINCrement?``; ``format`` is a printf format, else the definition's numbers.
        ``scale`` rescales a number (the display spans are figures of the record times the grid's
        divisions); a word (``coupling``) is returned as it stands."""
        assert self.owner is not None
        rec, used, n, function = self._read(channel, start=False)
        if rec is None:
            self.owner.push_error(-230, str(self.param("no_record")))
            return OpResult(b"", used)
        value = self.figures(rec, n, function=function)[what]
        if isinstance(value, str):
            return OpResult(value.encode(), used)
        if scale != 1.0:
            value = float(value) * scale
            return OpResult((format % value).encode() if format else self.owner.format_number(value), used)
        if format:
            return OpResult((format % value).encode(), used)
        if isinstance(value, int):
            return OpResult(str(value).encode(), used)
        return OpResult(self.owner.format_number(float(value)), used)

    @op("data")
    def data(
        self,
        query: bool = False,
        channel: int | None = None,
        start: int | None = None,
        size: int | None = None,
        **_: Any,
    ) -> OpResult:
        """``:WAVeform:DATA?``, ``C1:INSPECT?``: the selected points of the source's record.

        ``start``/``size`` (Infiniium ``:WAVeform:DATA? [<start>[,<size>]]``): this read's first
        point and number of points, ``size`` cut to the end of the record; a ``start`` past the
        record is -222. A deep record is read in parts this way, each part computed as it is read."""
        if start is None:
            return self.transfer(channel)
        if start < 0 or (size is not None and size < 1):
            raise ScpiError(-222, f"start {start}, size {size}: start is 0 or more, size 1 or more")
        return self.transfer(channel, int(start), 0 if size is None else int(size))

    # ---- what the read is (Infiniium's :WAVeform state queries, #1066) -----------------------------

    @op("complete")
    def complete(self, channel: int | None = None, **_: Any) -> OpResult:
        """``:WAVeform:COMPlete?``: the percent of time buckets that are complete (the guide,
        p. 1598). A finished acquisition's buckets are all complete: 100; nothing read yet: 0."""
        rec, used, _n, _f = self._read(channel, start=False)
        return OpResult(b"100" if rec is not None else b"0", used)

    @op("hits")
    def hits(
        self, channel: int | None = None, averaging: bool = False, averages: int = 1, **_: Any
    ) -> OpResult:
        """``:WAVeform:COUNt?``: the fewest number of hits in all of the time buckets (the guide,
        p. 1599) — the averages one averaging completed with, one hit otherwise; nothing read
        yet: 0. The model's records have no holes, so the zero the guide gives for holes is the
        no-record case only."""
        rec, used, _n, _f = self._read(channel, start=False)
        if rec is None:
            return OpResult(b"0", used)
        return OpResult(str(int(averages)).encode() if V.flag(averaging) else b"1", used)

    @op("wfm_type")
    def wfm_type(
        self, averaging: bool = False, averaged: str = "AVER", normal: str = "RAW", **_: Any
    ) -> OpResult:
        """``:WAVeform:TYPE?``: how the source's waveform was acquired (the guide, p. 1632), in
        the guide's short words — the averaged record AVER, the raw one RAW."""
        return OpResult((averaged if V.flag(averaging) else normal).encode())

    @op("axis_units")
    def axis_units(self, axis: str = "X", channel: int | None = None, **_: Any) -> OpResult:
        """``:WAVeform:XUNits?``/``:YUNits?`` (the guide, pp. 1641/1647): the source's axis units
        in the guide's words. A channel's trace is time and volts; a function's the math block
        says (the transform: HERTz against its vertical unit's word)."""
        source = self.param("source")
        f = function_index(source) if source not in (None, "") else None
        if f is not None:
            block = self.ref("math")
            if block is None or not isinstance(block, MathSource):
                raise ScpiError(-224, f"{source}: this model has no math functions to transfer")
            x_word, y_word = block.function_units(f)
        else:
            x_word, y_word = "SECond", "VOLT"  # a channel's time-domain trace
            assert self.owner is not None
            units = str(self.owner.get_state(f"ch{self._source(channel)}.units") or "").upper()
            y_word = {"AMPERE": "AMP", "WATT": "WATT", "UNKNOWN": "UNKNown"}.get(units, "VOLT")
        return OpResult((x_word if str(axis).upper().startswith("X") else y_word).encode())

    @op("bandpass")
    def bandpass(self, low: float = 0.0, high: float = 0.0, digits: int = 6, **_: Any) -> OpResult:
        """``:WAVeform:BANDpass?``: the acquisition path's lower and upper cutoff (the guide,
        p. 1594) — the lower is the coupling's high-pass corner (0: DC), the upper the digital
        filter's ``:ACQuire:BANDwidth``."""
        return OpResult(f"{float(low):.{digits}E},{float(high):.{digits}E}".encode())

    @op("segment_points")
    def segment_points(self, channel: int | None = None, **_: Any) -> OpResult:
        """``:WAVeform:SEGMented:POINts?`` (the guide, p. 1627): the points the data read sends —
        every segment's with ``:WAVeform:SEGMented:ALL ON``, the current segment's otherwise."""
        assert self.owner is not None
        rec, used, n, _f = self._read(channel, start=False)
        if rec is None:
            self.owner.push_error(-230, str(self.param("no_record")))
            return OpResult(b"", used)
        rec = self._all_segments(rec, n) or rec
        return OpResult(str(int(self.selection(rec.points).size)).encode(), used)

    @op("xlist")
    def xlist(self, what: str, channel: int | None = None, **_: Any) -> OpResult:
        """``:WAVeform:SEGMented:XLISt? {RELXorigin|ABSXorigin|TTAG|OFFSet}`` (the guide, p. 1629):
        one x value per segment of the source channel's latest segmented acquisition, in the data
        read's ASCii format. The guide supports the ASCii and BINary float64 formats for it; this
        model's ``:WAVeform:FORMat`` has no BINary, so a WORD/BYTE selection is -224."""
        if str(self.param("encoding")).lower() != "text":
            raise ScpiError(-224, "XLISt reads the ASCii format (the guide's BINary float64 is not modeled)")
        kind = str(what).upper()
        if kind not in ("RELXORIGIN", "ABSXORIGIN", "TTAG", "OFFSET"):
            raise ScpiError(-224, f"{what!r}: no such x list (RELXorigin, ABSXorigin, TTAG, OFFSet)")
        source = self.param("source")
        f = function_index(source) if source not in (None, "") else None
        if f is not None:
            raise ScpiError(-224, "XLISt reads a channel source (the guide: CHANnel<N> or POD<N>)")
        n = self._source(channel)
        read_segments = getattr(self._acquisition(), "read_segments", None)
        recs = read_segments(n) if callable(read_segments) else None
        if not recs:
            raise ScpiError(-221, f"channel {n} has no segments to list")
        first = recs[0]

        def instant(rec: Record) -> float:
            if rec.trigger_ns is not None:
                return rec.trigger_ns * 1e-9
            return rec.t_start_ns * 1e-9 + rec.trigger_index * rec.dt_s

        ttags = [instant(rec) - instant(first) for rec in recs]
        if kind == "TTAG":
            values: list[float] = ttags
        elif kind == "RELXORIGIN":
            values = [rec.origin for rec in recs]
        elif kind == "ABSXORIGIN":
            values = [rec.origin + ttag for rec, ttag in zip(recs, ttags, strict=True)]
        else:  # OFFSet: where each segment begins in the ALL-ON data record; zeros with ALL OFF
            offset = self.selection(first.points).size if V.flag(self.param("seg_all")) else 0
            values = [float(k * offset) for k in range(len(recs))]
        fmt = str(self.param("format"))
        return OpResult(str(self.param("separator")).join(fmt % v for v in values).encode())
