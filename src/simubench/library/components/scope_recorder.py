"""The records an acquisition stores and how they are read back: the stored record, a read of it, the measurement record, the acquisition figures. Part of ``ScopeAcquisition`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import numpy as np

from ...engine.component import (
    OpResult,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope_record import (
    Record,
)

if TYPE_CHECKING:
    pass
from .scope_acquisition_host import AcquisitionHost


class Recorder:
    def __init__(self, host: AcquisitionHost) -> None:
        self.host = host
        self.records: dict[int, Record] = {}
        self.acq_count = 0

    def store(
        self,
        records: dict[int, Record],
        used: int,
        forced: bool = False,
        segment: int | None = None,
        of: int = 1,
    ) -> int:
        """Keep ``records`` as the latest acquisition and log its recipe (D36). Returns ``used``.

        ``forced``: read from a channel that had no record, whatever the run mode. ``segment``
        (from 0) ``of`` that many: one segment of a segmented acquisition (:meth:`store_segments`);
        the acquisition counts once, with its last segment."""
        if segment is None:
            self.host.segments.segment_records = []  # the latest acquisition is not a segmented one
        self.records.update(records)
        instants = [r.trigger_ns for r in records.values() if r.triggered and r.trigger_ns is not None]
        if instants:
            self.host.trigger_unit().last_ns = max(instants)  # the holdoff runs from here
            if self.host.owner is not None:
                self.host.owner.status_event("TRG")  # the trigger event register (:TER?, #248)
        if self.host.owner is not None and records:
            # every stored acquisition is done (Infiniium :ADER?, #602); one an auto sweep took
            # without its trigger is an auto trigger (:ATER?)
            self.host.owner.status_event("DONE")
            if not instants and not forced and self.host._is("auto"):
                self.host.owner.status_event("AUTO")
        count = self.acq_count + 1
        if segment is None or segment == of - 1:
            self.acq_count = count
        self.host.session.invalidate()
        if self.host.owner is not None:
            # The acquisition's recipe: everything needed to regenerate it from the bench (D36).
            self.host.session.events.instrument_event(
                self.host.owner,
                "acquisition",
                {
                    "count": count,
                    **({} if segment is None else {"segment": segment + 1, "segments": of}),
                    "forced": forced,
                    "channels": {
                        str(ch): {
                            "t_start_ns": rec.t_start_ns,
                            "dt_s": rec.dt_s,
                            "points": rec.points,
                            "trigger_index": rec.trigger_index,
                            "x_origin_s": rec.origin,
                            "triggered": rec.triggered,
                            # a deep record is computed where it is read (#1012): no digest of it
                            **(
                                {"computed": "on_read"}
                                if rec.deep is not None
                                else {
                                    "sha256": hashlib.sha256(
                                        np.ascontiguousarray(rec.volts).tobytes()
                                    ).hexdigest()
                                }
                            ),
                        }
                        for ch, rec in sorted(records.items())
                    },
                    "seed": self.host.session.seed,
                },
            )
        return used

    def read_record(self, channel: int, start: bool = True) -> tuple[Record | None, int]:
        """The record a read uses, and the virtual time getting it took (``reacquire``, ``missing``).

        ``start``: the read starts a transfer (``on_read_start`` acquires anew only then)."""
        used = 0
        how = self.host.param("reacquire")
        fresh = how == "every_read" or (how == "on_read_start" and start)
        running = self.host.runner.is_running() and (self.host._is("auto") or self.host._is("normal"))
        if self.host.runner.pending_mode() and self.host.runner.waiting():
            if str(self.host.param("while_waiting")) == "error":
                raise ScpiError(-221, "acquisition in progress")
        elif (fresh and running) or (self.host.runner.armed and self.host._is("single")):
            used += self.host.runner.acquire_now(self.host.runner.acquisition_channels(channel))
        rec = self.records.get(channel)
        if rec is None and self.host.param("missing") == "acquire":
            # Nothing acquired on this channel yet (stopped, or no trigger): rather than an empty
            # trace, read what the channel shows now, and say so in the event history.
            records, t, _ = self.host.capture.compute(self.host.runner.acquisition_channels(channel))
            used += self.store(records, t, forced=True)
            rec = self.records[channel]
        return rec, used

    def transfer_record(self, channel: int, first: int) -> tuple[Record | None, int]:
        """The record a waveform transfer reads (``waveform_transfer``) and the time getting it took;
        ``first`` is the transfer's first point."""
        return self.read_record(channel, start=first == 0)

    def measure_record(self, channel: int) -> tuple[Record, int]:
        """The record a measurement uses: as a read, acquiring the channel when it has none."""
        rec, used = self.read_record(channel)
        if rec is None:
            records, t, _ = self.host.capture.compute([channel])
            used += self.store(records, t, forced=True)
            rec = self.records[channel]
        return rec, used

    def keep_last_frame(self) -> None:
        """A running scope has been acquiring all along: before a single acquisition (or a stop)
        replaces the run, the frame it shows now is its latest acquisition, which a read gets
        until the single one is done (completion: pending)."""
        records, used, triggered = self.host.capture.compute(self.host.displayed())
        if records and (triggered or self.host._is("auto")):
            self.store(records, used)

    def figure(
        self, what: str = "points", integer: bool = False, significant: int = 0, format: str = "", **_: Any
    ) -> OpResult:
        assert self.host.owner is not None
        if what == "count":
            return OpResult(str(self.acq_count).encode())
        if what == "segments":  # the segments the latest acquisition has (0: it was not segmented)
            return OpResult(str(len(self.host.segments.segment_records)).encode())
        if integer and what == "points":
            return OpResult(str(int(self.host._timing()[0])).encode())
        if what == "points":
            value = float(self.host._timing()[0])
        elif what == "sample_rate":
            value = self.host.sample_rate()
        elif what == "time_tag":
            value = self.host.segments.segment_time_tag()
        else:
            raise ScpiError(-300, f"{self.host.uid}: no figure {what!r}")
        if significant:
            return OpResult(V.format_engineering_digits(value, int(significant)))
        if format:
            return OpResult((format % value).encode())
        return OpResult(self.host.owner.format_number(value))

    def clear_display(self, **_: Any) -> OpResult:
        self.records.clear()
        self.host.segments.segment_records = []
        self.host.session.invalidate()
        return OpResult()
