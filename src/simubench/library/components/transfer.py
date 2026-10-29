"""Waveform transfer: a scope's record as the reply to a data query (instrument-authoring.md 3.1).

One component for the encodings scopes use, set by parameters that may be bound to settings, so a
new vendor's format is a definition, not code:

* ``encoding: block``: an IEEE 488.2 definite-length block of ADC codes (``width`` 8 or 16 bits,
  ``byteorder``, ``unsigned``; ``block_digits`` fixes the length digits, e.g. 9 for ``#9...``);
* ``encoding: ascii``: comma-separated NR3 numbers (``digits``);
* ``encoding: text``: numbers in a printf ``format`` joined by ``separator`` (a command's reply
  template adds what goes around them, e.g. ``'{header} " {value} "'``).

The points sent are ``first``, then every ``step``-th, ``count`` of them (0: to the end, at most
``max_points`` when that is set). ``values`` are volts or ADC codes: ``round((v -/+ offset) / scale *
codes_per_div)`` limited to ``adc_bits`` and aligned to ``width`` (shifted up or down), as a scope's
digitizer writes them. Which record is current (acquire anew when a read starts, or keep the last
one) is the acquisition component's business: it is asked through ``transfer_record``.
"""

from __future__ import annotations

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
        "max_points": 0,  # 0: no limit
        "encoding": "block",  # block | ascii | text
        "values": "codes",  # codes | volts
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
    }

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
        sel = np.arange(first, size, step)
        limit = count if count > 0 else int(self.param("max_points"))
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
        if width == 8:
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
        rec, used = acq.read_record(self._source(channel), start=False)  # type: ignore[attr-defined]
        if rec is None:
            self.owner.push_error(-230, str(self.param("no_record")))
            return OpResult(b"", used)
        sel = self.selection(rec.volts.size)
        figures = {
            "points": rec.volts.size,
            "first": int(sel[0]) if sel.size else 0,
            "step": max(1, int(self.param("step"))),
            "dt": rec.dt_s,
            "x_offset": -rec.trigger_index * rec.dt_s,
            "offset": rec.offset,
            "scale": rec.scale,
            "gain": rec.scale / acq.adc_codes_per_div() if acq.adc_codes_per_div() else 0.0,  # type: ignore[attr-defined]
            "timebase": float(acq.param("timebase")),
            "count": acq.acq_count,  # type: ignore[attr-defined]
        }
        item = str(self.param("descriptor_item"))
        parts = [
            item.format(name=name, value=str(fmt).format(**figures))
            for name, fmt in (self.param("descriptor") or {}).items()
        ]
        return OpResult(str(self.param("descriptor_separator")).join(parts).encode(), used)

    @op("data")
    def data(self, query: bool = False, channel: int | None = None, **_: Any) -> OpResult:
        """``:WAVeform:DATA?``, ``C1:INSPECT?``: the selected points of the source's record."""
        return self.transfer(channel)
