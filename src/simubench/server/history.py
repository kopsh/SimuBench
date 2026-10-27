"""Version history of bench files (D35).

Every save keeps a snapshot of the content written, with who wrote it, in
``<workspace>/.simubench/history/<bench path>/``. Content that changed outside SimuBench (a text
editor, a git checkout) is recorded as an external version the next time the file is saved, so
the history never skips what an engineer saw. Consecutive saves that only move nodes are merged
into one version. Restoring writes an old version as a new one; nothing is ever deleted except
the oldest versions beyond :data:`KEEP`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Literal

from ..models import api as M

Source = Literal["ui", "agent", "api", "external", "restore"]
KEEP = 200
LAYOUT_MERGE_S = 120.0  # node moves within this time of the previous move from the same source merge


class History:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace / ".simubench" / "history"

    def _dir(self, bench_path: str) -> Path:
        return self.root / Path(bench_path)

    def _index(self, bench_path: str) -> list[dict]:
        index = self._dir(bench_path) / "index.jsonl"
        if not index.exists():
            return []
        out = []
        for line in index.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def _write_index(self, bench_path: str, entries: list[dict]) -> None:
        d = self._dir(bench_path)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / ".index.jsonl.tmp"
        tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
        os.replace(tmp, d / "index.jsonl")

    def versions(self, bench_path: str) -> list[M.BenchVersion]:
        """Newest first."""
        return [
            M.BenchVersion(
                seq=e["seq"],
                sha256=e["sha256"],
                fingerprint=e["fingerprint"],
                source=e["source"],
                saved_at=e["saved_at"],
                layout_only=e.get("layout_only", False),
            )
            for e in reversed(self._index(bench_path))
        ]

    def content(self, bench_path: str, seq: int) -> bytes:
        entry = next((e for e in self._index(bench_path) if e["seq"] == seq), None)
        if entry is None:
            raise FileNotFoundError(f"{bench_path} has no version {seq}")
        return (self._dir(bench_path) / entry["file"]).read_bytes()

    def record(
        self,
        bench_path: str,
        raw: bytes,
        sha256: str,
        fingerprint: str,
        source: Source,
        *,
        previous: tuple[bytes, str, str] | None = None,
    ) -> None:
        """Record ``raw`` as the newest version.

        ``previous`` is the file as it was on disk before this save (bytes, sha256, fingerprint);
        when the history does not end with it, it changed outside SimuBench and is recorded first.
        """
        entries = self._index(bench_path)
        if previous is not None and (not entries or entries[-1]["sha256"] != previous[1]):
            entries = self._append(bench_path, entries, previous[0], previous[1], previous[2], "external")
        if entries and entries[-1]["sha256"] == sha256:
            return
        last = entries[-1] if entries else None
        now = dt.datetime.now(dt.UTC)
        layout_only = last is not None and last["fingerprint"] == fingerprint
        if (
            layout_only
            and last is not None
            and last.get("layout_only")
            and last["source"] == source
            and (now - dt.datetime.fromisoformat(last["saved_at"])).total_seconds() < LAYOUT_MERGE_S
        ):
            # Another node move right after the last one: update that version instead of adding one.
            (self._dir(bench_path) / last["file"]).write_bytes(raw)
            last.update(sha256=sha256, saved_at=now.isoformat())
            self._write_index(bench_path, entries)
            return
        self._append(bench_path, entries, raw, sha256, fingerprint, source, layout_only=layout_only)

    def _append(
        self,
        bench_path: str,
        entries: list[dict],
        raw: bytes,
        sha256: str,
        fingerprint: str,
        source: Source,
        *,
        layout_only: bool = False,
    ) -> list[dict]:
        seq = entries[-1]["seq"] + 1 if entries else 1
        suffix = ".json" if bench_path.endswith(".json") else ".yaml"
        d = self._dir(bench_path)
        d.mkdir(parents=True, exist_ok=True)
        name = f"{seq:06d}{suffix}"
        (d / name).write_bytes(raw)
        entries = [
            *entries,
            {
                "seq": seq,
                "file": name,
                "sha256": sha256,
                "fingerprint": fingerprint,
                "source": source,
                "saved_at": dt.datetime.now(dt.UTC).isoformat(),
                "layout_only": layout_only,
            },
        ]
        for old in entries[:-KEEP]:
            (d / old["file"]).unlink(missing_ok=True)
        entries = entries[-KEEP:]
        self._write_index(bench_path, entries)
        return entries
