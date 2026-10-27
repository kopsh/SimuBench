"""Run records in the record folder: list them, read one, replay it (D36)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..engine.replay import read_record, replay
from ..models import api as M

RUN_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")
MAX_EVENTS = 20_000


class Runs:
    def __init__(self, directory: Path | None) -> None:
        self.directory = directory

    def _path(self, run_id: str) -> Path:
        if self.directory is None or not RUN_ID.match(run_id):
            raise FileNotFoundError(run_id)
        path = self.directory / f"run-{run_id}.jsonl"
        if not path.is_file():
            raise FileNotFoundError(run_id)
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

    def _summary(self, path: Path, events: list[dict[str, Any]] | None = None) -> M.RunSummary:
        records = events if events is not None else list(read_record(path))
        header = records[0] if records else {}
        if header.get("kind") != "header":
            raise ValueError(path.name)
        end = records[-1] if records[-1].get("kind") == "end" else None
        last_t = max((int(r.get("t_ns", 0)) for r in records[1:]), default=0)
        return M.RunSummary(
            run_id=str(header["run_id"]),
            bench=str(header.get("bench", "")),
            started=float(header.get("started", 0)),
            ended=float(end["ended"]) if end else None,
            clock=str(header.get("clock", "")),
            seed=int(header.get("seed", 0)),
            engine=str(header.get("engine", "")),
            events=len(records) - 1 - (1 if end else 0),
            duration_ns=last_t,
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
        report = replay(self._path(run_id))
        return M.ReplayResult(
            run_id=run_id,
            ok=report.ok,
            actions=report.actions,
            replies=report.replies,
            mismatches=[M.ReplayMismatch(**m.__dict__) for m in report.mismatches],
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
