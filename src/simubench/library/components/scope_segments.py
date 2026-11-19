"""Segmented memory: the segments an acquisition fills, the one on screen, their time tags and the segment list. Part of ``ScopeAcquisition`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...engine.component import (
    OpResult,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope import (
    trigger_channel,
)
from .scope_pending import _Segment
from .scope_record import (
    Record,
)

if TYPE_CHECKING:
    pass
from .scope_acquisition_host import AcquisitionHost


class Segments:
    def __init__(self, host: AcquisitionHost) -> None:
        self.host = host
        self.segment_records: list[dict[int, Record]] = []  # the latest acquisition's segments, if segmented

    def segment_count(self) -> int:
        """Records one acquisition takes: ``segments`` while ``segmented``, else one."""
        return max(1, int(self.host.param("segments"))) if V.flag(self.host.param("segmented")) else 1

    def check_memory(self) -> None:
        """The segments of one acquisition fit the memory (``memory_points``), or -221."""
        count, limit = self.segment_count(), self.host.param("memory_points")
        if count > 1 and limit is not None and count * self.host._timing()[0] > float(limit):
            raise ScpiError(
                -221,
                f"{count} segments of {self.host._timing()[0]} points exceed the memory ({int(limit)} points)",
            )

    def acquire_segments(
        self, channels: list[int], taken: list[_Segment] | tuple[()] = (), force: bool = False
    ) -> tuple[list[_Segment], bool]:
        """The segments of one acquisition starting now, after those already ``taken``, without
        changing any state: each on its own trigger, the trigger armed for the next ``rearm_s``
        after a record is full (so with the pre-trigger time to fill first), the holdoff counted
        from the segment before. Returns the segments and whether all of them are there: a segment
        whose trigger does not come ends the list, unless the acquisition is forced or the mode is
        an auto mode (the segment is then taken untriggered)."""
        count = self.segment_count()
        self.check_memory()
        trig = self.host.trigger_unit()
        now = self.host.session.now_ns
        _, nominal_dt, pre, _ = self.host._timing()
        fill_ns = round(pre * nominal_dt * 1e9)
        overhead, rearm = (round(float(self.host.param(k)) * 1e9) for k in ("overhead_s", "rearm_s"))
        segments = list(taken)
        held = trig.last_ns
        try:
            while len(segments) < count:
                if segments:
                    # Armed since the record before was full and the re-arm time passed; when that
                    # is long ago the pre-trigger memory is full already, and the search starts now.
                    t0, earliest = max(segments[-1].end_ns + rearm, now - fill_ns), now
                    if segments[-1].triggered:
                        trig.last_ns = segments[-1].trigger_ns
                else:
                    t0, earliest = now, None
                records, used, triggered, trigger_ns = self.host.capture.acquire(
                    channels, t0=t0, segment=len(segments), earliest_ns=earliest
                )
                if not (triggered or force or self.host._is("auto")):
                    return segments, False
                segments.append(_Segment(records, triggered, trigger_ns, t0 + used - overhead))
        finally:
            trig.last_ns = held
        return segments, True

    def store_segments(self, segments: list[_Segment], used: int) -> int:
        """Keep the segments of one acquisition (:meth:`store` for each); reads and measurements
        then use the one ``segment_index`` selects. Returns ``used``."""
        if len(segments) == 1:
            return self.host.recorder.store(segments[0].records, used)
        for k, seg in enumerate(segments):
            self.host.recorder.store(seg.records, used, segment=k, of=len(segments))
        self.segment_records = [dict(seg.records) for seg in segments]
        self.select_segment(int(self.host.param("segment_index")))
        return used

    def _selected(self, index: int | None = None) -> int:
        """The selected segment, from 0, among those the latest acquisition has."""
        i = int(self.host.param("segment_index")) if index is None else index
        return min(max(i, 1), max(len(self.segment_records), 1)) - 1

    def select_segment(self, index: int) -> None:
        if self.segment_records:
            self.host.recorder.records = dict(self.segment_records[self._selected(index)])

    def segment_time_tag(self) -> float:
        """The selected segment's trigger, seconds after the first segment's (0 without segments)."""
        if not self.segment_records:
            return 0.0

        def instant(records: dict[int, Record]) -> int:
            rec = next(iter(records.values()))
            if rec.trigger_ns is not None:
                return rec.trigger_ns
            return rec.t_start_ns + round(rec.trigger_index * rec.dt_s * 1e9)

        return (instant(self.segment_records[self._selected()]) - instant(self.segment_records[0])) * 1e-9

    def read_segments(self, channel: int) -> list[Record] | None:
        """The latest acquisition's segments' records for a channel (``:WAVeform:SEGMented:ALL``,
        #1066), each segment one full record; None when the latest acquisition was not a segmented
        one or the channel has no records in it. Read as they are: the transfer concatenates."""
        if not self.segment_records:
            return None
        recs: list[Record] = []
        for seg in self.segment_records:
            rec = seg.get(channel)
            if rec is None:
                return None
            recs.append(rec)
        return recs

    def set_segment_index(self, query: bool = False, index: int | None = None, **_: Any) -> OpResult:
        assert self.host.owner is not None
        path = self.host.bound_path("segment_index")
        if path is None:
            raise ScpiError(-300, f"{self.host.uid}: segment_index is not bound to a setting")
        if query:
            return OpResult(str(int(self.host.owner.get_state(path))).encode())
        if index is None:
            raise ScpiError(-109, "an index is required")
        i = int(index)
        limit = max(1, len(self.segment_records))
        if i < 1 or i > limit:
            self.host.owner.set_state(path, min(max(i, 1), limit))
            raise ScpiError(-222, f"segment {i} is out of range: {limit} segment(s) acquired")
        self.host.owner.set_state(path, i)
        return OpResult()

    def segment_list(
        self, what: str = "TTAG", source: Any = None, format: str = "%.10E", **_: Any
    ) -> OpResult:
        assert self.host.owner is not None
        kind = str(what).upper()
        n = trigger_channel(source) or 1
        segments = self.segment_records or (
            [dict(self.host.recorder.records)] if self.host.recorder.records else []
        )
        if not segments or n not in segments[0]:
            raise ScpiError(-230, "no acquisition for this source")

        def instant(rec: Record) -> int:
            if rec.trigger_ns is not None:
                return rec.trigger_ns
            return rec.t_start_ns + round(rec.trigger_index * rec.dt_s * 1e9)

        first = instant(segments[0][n])
        values: list[float] = []
        for seg in segments:
            rec = seg[n]
            tag = (instant(rec) - first) * 1e-9
            rel = float(rec.origin)
            if kind.startswith("REL"):
                values.append(rel)
            elif kind.startswith("ABS"):
                values.append(rel + tag)
            elif kind.startswith("TTAG"):
                values.append(tag)
            elif kind.startswith("OFFS"):
                return OpResult(b"0")
            else:
                raise ScpiError(-224, f"no segment figure {what!r}")
        return OpResult(",".join(format % v for v in values).encode())

    def analyze_segments(self, **_: Any) -> OpResult:
        return OpResult()
