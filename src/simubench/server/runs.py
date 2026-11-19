"""Run records in the record folder: list them, read one, replay it (D36)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..contracts import api as M
from ..replay import read_record, replay
from .errors import Invalid, NotFound

RUN_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")
MAX_EVENTS = 20_000
#: Bytes the search for a record's last line starts with; it widens when one line is longer
#: (a reply kept as text can be kilobytes).
_TAIL = 4096


def _head_tail(path: Path) -> list[dict[str, Any]]:
    """A record's first and last lines, parsed (#1636): a summary needs only these two and the
    list endpoint is polled every second, so the lines between are never read. ``ValueError``
    when the file is not a record, or ends in a line that is still being written."""
    out: list[dict[str, Any]] = []
    with path.open("rb") as f:
        if first := f.readline().strip():
            out.append(json.loads(first))
        pos = f.seek(0, os.SEEK_END)
        window = _TAIL
        while True:
            start = max(0, pos - window)
            f.seek(start)
            head, _, line = f.read(pos - start).rstrip(b"\n").rpartition(b"\n")
            if head or start == 0:
                break  # `line` is the file's last line, whole
            window *= 4  # the last line is longer than the window: take more of it
        if line.strip():
            out.append(json.loads(line))
    return out


class Runs:
    def __init__(self, directory: Path | None, definition_dirs: list[Path] | None = None) -> None:
        self.directory = directory
        self.definition_dirs = definition_dirs or []

    def _path(self, run_id: str) -> Path:
        if self.directory is None or not RUN_ID.match(run_id):
            raise NotFound(run_id)
        path = self.directory / f"run-{run_id}.jsonl"
        if not path.is_file():
            raise NotFound(run_id)
        return path

    def list(self) -> list[M.RunSummary]:
        if self.directory is None or not self.directory.is_dir():
            return []
        out = []
        for path in self.directory.glob("run-*.jsonl"):
            try:
                out.append(self._summary(path))
            except (ValueError, KeyError, OSError):
                continue  # not a record, or one being written right now
        return sorted(out, key=lambda r: r.started, reverse=True)

    def _summary(self, path: Path, records: list[dict[str, Any]] | None = None) -> M.RunSummary:
        """The summary from the record's first and last line (#1636): the header carries every
        field but the end, the end event carries its own ``t_ns``, and ``seq`` counts the lines
        written before it. ``records`` when the caller has parsed the whole file already
        (:meth:`detail`) — the same fields, one computation."""
        if records is None:
            records = _head_tail(path)
        header = records[0] if records else {}
        if header.get("kind") != "header":
            raise Invalid(path.name)
        last = records[-1]
        end = last if last.get("kind") == "end" else None
        seq = int(last.get("seq", len(records) - 1))  # one per line written: the header is seq 0
        return M.RunSummary(
            run_id=str(header["run_id"]),
            bench=str(header.get("bench", "")),
            started=float(header.get("started", 0)),
            ended=float(end["ended"]) if end else None,
            clock=str(header.get("clock", "")),
            seed=int(header.get("seed", 0)),
            engine=str(header.get("engine", "")),
            events=seq - (1 if end else 0),
            duration_ns=int((end or last).get("t_ns", 0)),
            instruments=sorted((header.get("definitions") or {}).keys()),
            size=path.stat().st_size,
        )

    def detail(self, run_id: str) -> M.RunDetail:
        path = self._path(run_id)
        records = list(read_record(path))
        summary = self._summary(path, records)
        body = [r for r in records[1:] if r.get("kind") not in ("end",)]
        return M.RunDetail(
            summary=summary,
            events=[_event(r) for r in body[:MAX_EVENTS]],
            truncated=len(body) > MAX_EVENTS,
        )

    def replay(self, run_id: str) -> M.ReplayResult:
        report = replay(self._path(run_id), definition_dirs=list(self.definition_dirs))
        return M.ReplayResult(
            run_id=run_id,
            ok=report.ok,
            actions=report.actions,
            replies=report.replies,
            mismatches=list(report.mismatches),
            warnings=report.warnings,
        )


def _event(r: dict[str, Any]) -> M.RunEvent:
    data = r.get("data")
    extra: dict[str, Any] = {}
    if isinstance(data, dict):
        if "text" in data:
            extra["text"] = data["text"]
            extra["size"] = len(str(data["text"]))
        if "size" in data:
            extra["size"] = int(data["size"])
        if "sha256" in data:
            extra["sha256"] = data["sha256"]
            extra["recipe"] = data.get("recipe")
    fields = set(M.RunEvent.model_fields)
    plain = {k: v for k, v in r.items() if k in fields and k not in ("text", "size")}
    t = int(r.get("t_start_ns", r.get("t_ns", 0)))
    return M.RunEvent(**{**plain, **extra, "t_ns": t, "seq": int(r.get("seq", 0))})
