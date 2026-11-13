"""Measurement data saved as files on the instrument's drives (#1071): a meter's reading memory as
CSV, a scope's record as text or Infiniium binary.

A file holds the same data a query would answer at that moment: the readings ``FETCh?`` writes,
written the same way, and the record ``:WAVeform:DATA?`` transfers (the acquisition is asked the
same way, so a stopped scope gives the same samples and a run record's recipe regenerates both).
Layouts the manual gives are followed; where it gives none the layout below is SimuBench's and the
coverage table says so. Vendor container formats nobody can write from the manual (Infiniium's
``.wfm``, HDF5, MATLAB) are refused with the definition's error rather than written wrong.
"""

from __future__ import annotations

import re
import struct
from typing import Any

import numpy as np

from ...engine.component import Acquisition, Component, OpResult, component, op
from ...scpi.errors import ScpiError

SEPARATORS = {"COMMa": ",", "SEMicolon": ";", "TAB": "\t"}
#: Waveform formats and the extension each adds to a file name without one (Infiniium
#: :DISK:SAVE:WAVeform); the text ones and BIN are written, the others refused.
WAVEFORM_EXTENSIONS = {
    "BIN": ".bin",
    "CSV": ".csv",
    "TSV": ".tsv",
    "TXT": ".txt",
    "INTernal": ".wfm",
    "H5": ".h5",
    "H5INt": ".h5",
    "MATlab": ".mat",
}
TEXT_SEPARATORS = {"CSV": ",", "TSV": "\t", "TXT": " "}
_SOURCE = re.compile(r"^CHAN(?:NEL)?(\d+)$", re.IGNORECASE)


def readings_table(rows: list[dict[int, str]], channels: list[int], header: str, sep: str) -> str:
    """Readings by channel: a header row (``header`` with ``{channel}``), then one row per sweep."""
    lines = [sep.join(header.format(channel=c) for c in channels)]
    lines += [sep.join(row.get(c, "") for c in channels) for row in rows]
    return "\r\n".join(lines) + "\r\n"


def waveform_text(
    volts: np.ndarray, x0: float, dx: float, sep: str, header: dict[str, str] | None = None
) -> str:
    """A record as ``time<sep>volts`` lines, after ``key<sep>value`` header lines when given."""
    lines = [f"{k}{sep}{v}" for k, v in (header or {}).items()]
    t = x0 + dx * np.arange(volts.size)
    lines += [f"{ti:.12E}{sep}{vi:.6E}" for ti, vi in zip(t, volts, strict=True)]
    return "\r\n".join(lines) + "\r\n"


def infiniium_bin(
    volts: np.ndarray,
    *,
    x_origin: float,
    x_increment: float,
    x_display_range: float,
    x_display_origin: float,
    date: str,
    time: str,
    frame: str,
    label: str,
    count: int,
) -> bytes:
    """One waveform in the Keysight oscilloscope binary file (``.bin``): file header ``AG10``,
    a 140-byte waveform header (normal waveform, one buffer, x in seconds, y in volts) and one
    float32 data buffer, little-endian."""
    data = np.asarray(volts, dtype="<f4").tobytes()
    wave = struct.pack(
        "<5if3d2i16s16s24s16sdI",
        140,  # header size
        1,  # waveform type: normal
        1,  # waveform buffers
        int(volts.size),  # points
        int(count),  # count (acquisitions averaged)
        float(x_display_range),
        float(x_display_origin),
        float(x_increment),
        float(x_origin),
        2,  # x units: second
        1,  # y units: volt
        date.encode()[:16],
        time.encode()[:16],
        frame.encode()[:24],
        label.encode()[:16],
        0.0,  # time tag
        0,  # segment index
    )
    buffer = struct.pack("<ihhi", 12, 1, 4, len(data))  # header size, float32 data, 4 bytes/point
    body = wave + buffer + data
    return struct.pack("<2s2sii", b"AG", b"10", 12 + len(body), 1) + body


@component("data_file")
class DataFile(Component):
    """Write measurement data to a file on the instrument's drives: the reading memory
    (MMEMory:STORe:DATA RDG_STORE) or a channel's record (:DISK:SAVE:WAVeform).

    Readings are written as the buffer's queries write them, one column per scanned channel
    (``column_header``, ``{channel}``) and one row per sweep, separated by ``separator`` (a
    setting: COMMa, SEMicolon or TAB); readings without a channel go in one column. A reading file
    whose extension is in ``refused_extensions`` (the DAQ970A's binary ``.dat``) is refused with
    ``refused_error``. Waveforms: CSV, TSV and TXT are ``time,volts`` lines (``header`` adds
    ``key,value`` lines first), BIN is the Keysight binary file; INTernal, H5, H5INt and MATlab are
    refused. Model limits: the text layout and its header lines are SimuBench's (the guide does not
    give them); only channel sources, one record per file (no segments, no ALL).
    """

    refs = {
        "storage": ("file_system", "instrument"),
        "readings": ("reading_buffer", "instrument"),
        "record": (Acquisition, "instrument"),
    }
    optional_refs = frozenset({"readings", "record"})
    defaults = {
        "storage": "",  # the file_system the files go to
        "readings": "",  # the reading_buffer MMEMory:STORe:DATA writes (empty: none)
        "record": "",  # the scope acquisition :DISK:SAVE:WAVeform writes (empty: none)
        "separator": "COMMa",  # COMMa, SEMicolon or TAB between the columns of a reading file (a setting)
        "column_header": "Chan {channel}",  # the header of a reading column ({channel}: its number)
        "reading_extension": ".csv",  # added to a reading file name without one
        "refused_extensions": [".dat"],  # reading file formats not written (binary)
        "refused_error": -257,  # a format or extension that is not written
        "frame": "",  # the BIN header's frame string (the model and serial number)
    }
    choices = {"separator": tuple(SEPARATORS)}

    def _storage(self) -> Any:
        return self.ref("storage")

    @staticmethod
    def _named(name: str, extension: str) -> str:
        tail = re.split(r"[\\/:]", name)[-1]
        return name if "." in tail else name + extension

    def _refuse(self, text: str) -> ScpiError:
        return ScpiError(int(self.param("refused_error")), text)

    # ---- readings --------------------------------------------------------------------------------

    @op("store_readings")
    def op_store_readings(self, name: str, source: str | None = None, **_: Any) -> OpResult:
        """Write the reading memory to a file (MMEMory:STORe:DATA RDG_STORE,<file>)."""
        buffer: Any = self.ref("readings")
        if buffer is None:
            raise ScpiError(-300, f"{self.uid}: no reading memory to store")
        file = self._named(name, str(self.param("reading_extension")))
        ext = "." + file.rsplit(".", 1)[-1].casefold()
        if ext in {str(e).casefold() for e in self.param("refused_extensions") or []}:
            raise self._refuse(f"{ext} files are not written (binary format not modeled)")
        sep = SEPARATORS[self.choice("separator") or "COMMa"]
        stored = buffer.stored()
        text_of = buffer.number_text
        if stored and all("CHAN" in r.fields for r in stored):
            channels: list[int] = []
            rows: list[dict[int, str]] = []
            sweep: float | None = None
            for r in stored:
                ch = int(r.fields["CHAN"])
                if ch not in channels:
                    channels.append(ch)
                if r.fields.get("SCAN") != sweep or not rows or ch in rows[-1]:
                    rows.append({})
                    sweep = r.fields.get("SCAN")
                rows[-1][ch] = text_of(r.value)
            text = readings_table(rows, channels, str(self.param("column_header")), sep)
        else:
            text = "".join(text_of(r.value) + "\r\n" for r in stored)
        self._storage().store(file, text.encode())
        return OpResult()

    # ---- waveforms -------------------------------------------------------------------------------

    def _acquisition(self) -> Acquisition:
        acq = self.ref("record")
        if not isinstance(acq, Acquisition):
            raise ScpiError(-300, f"{self.uid}: no acquisition to save")
        return acq

    @op("store_waveform")
    def op_store_waveform(
        self, source: str, name: str, format: str | None = None, header: bool | None = None, **_: Any
    ) -> OpResult:
        """Write a channel's record to a file (:DISK:SAVE:WAVeform <source>,"<file>"[,<format>[,<header>]])."""
        assert self.owner is not None
        m = _SOURCE.match(source.strip())
        if m is None:
            raise ScpiError(-224, f"saving {source} is not modeled (channels only)")
        channel = int(m.group(1))
        if (
            not 1
            <= channel
            <= int(self.owner.definition.channels.count if self.owner.definition.channels else 0)
        ):
            raise ScpiError(-224, f"no channel {channel}")
        word = format
        if word is None:
            ext = ("." + name.rsplit(".", 1)[-1].casefold()) if "." in re.split(r"[\\/:]", name)[-1] else ""
            word = next((w for w, e in WAVEFORM_EXTENSIONS.items() if e == ext), "INTernal")
        if word not in ("BIN", *TEXT_SEPARATORS):
            raise self._refuse(f"the {word} waveform format is not written (not modeled)")
        file = self._named(name, WAVEFORM_EXTENSIONS[word])
        rec, used = self._acquisition().read_record(channel, start=True)
        if rec is None:
            raise ScpiError(-230, f"channel {channel} has no record")
        volts = np.asarray(rec.volts, dtype=float)
        if word == "BIN":
            now = self.session.calendar()
            acq = self._acquisition()
            data = infiniium_bin(
                volts,
                x_origin=rec.origin,
                x_increment=rec.dt_s,
                x_display_range=10.0 * float(acq.timebase_scale()),
                x_display_origin=rec.origin,
                date=now.strftime("%d %b %Y"),
                time=now.strftime("%H:%M:%S"),
                frame=str(self.param("frame") or self.owner.definition.model),
                label=f"CHAN{channel}",
                count=1,
            )
        else:
            fields = None
            if header:
                fields = {
                    "Source": f"CHANnel{channel}",
                    "Points": str(volts.size),
                    "XOrigin": f"{rec.origin:.12E}",
                    "XIncrement": f"{rec.dt_s:.12E}",
                    "XUnits": "Second",
                    "YUnits": "Volt",
                }
            data = waveform_text(volts, rec.origin, rec.dt_s, TEXT_SEPARATORS[word], fields).encode()
        self._storage().store(file, data)
        return OpResult(None, used)
