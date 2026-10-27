"""Run record: an append-only JSON Lines file of everything that happened to a session (architecture 5.6)."""

from __future__ import annotations

import base64
import hashlib
import json
import platform
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import __version__

if TYPE_CHECKING:
    from .session import Session

RECORD_SCHEMA = "simubench.record/1"


def encode_bytes(data: bytes) -> dict[str, str]:
    """Keep raw bytes exactly: text when it is printable ASCII, base64 otherwise."""
    try:
        text = data.decode("ascii")
        if all(32 <= ord(c) < 127 or c in "\r\n\t" for c in text):
            return {"text": text}
    except UnicodeDecodeError:
        pass
    return {"b64": base64.b64encode(data).decode(), "size": str(len(data))}


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return encode_bytes(value)
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


class Recorder:
    def __init__(self, directory: Path, session: Session) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.path = directory / f"run-{self.run_id}.jsonl"
        self.session = session
        self._f = self.path.open("a", encoding="utf-8")
        self._seq = 0

    def _write(self, record: dict[str, Any]) -> None:
        record = {"seq": self._seq, **record}
        self._seq += 1
        self._f.write(json.dumps(_jsonable(record), ensure_ascii=False, separators=(",", ":")) + "\n")
        self._f.flush()

    def header(self) -> None:
        bench_json = self.session.bench.model_dump_json(by_alias=True)
        self._write(
            {
                "kind": "header",
                "schema": RECORD_SCHEMA,
                "run_id": self.run_id,
                "engine": __version__,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "bench": self.session.bench.name,
                "bench_sha256": hashlib.sha256(bench_json.encode()).hexdigest(),
                "definitions": {
                    name: {"id": inst.definition.id, "version": inst.definition.version}
                    for name, inst in self.session.instruments.items()
                },
                "seed": self.session.seed,
                "clock": self.session.clock_mode,
                "started": time.time(),
            }
        )

    def event(self, event: dict[str, Any]) -> None:
        self._write(event)

    def close(self) -> None:
        if not self._f.closed:
            self._write({"kind": "end", "t_ns": self.session.now_ns, "ended": time.time()})
            self._f.close()
