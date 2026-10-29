"""Run record: an append-only JSON Lines file of everything that happened to a session (architecture 5.6).

The header carries the bench itself and a hash of every instrument definition, so a record can be
replayed on its own (D36). Waveform blocks are not stored byte for byte: a read reply larger than
:data:`BLOCK_MIN` bytes that is not text is kept as its size and hash, next to the acquisition recipe that regenerates it
(trigger instant, sample interval, points, seed). ``raw=True`` keeps the bytes as well.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import platform
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import __version__
from ..plugins import report as plugin_report

if TYPE_CHECKING:
    from .session import Session

RECORD_SCHEMA = "simubench.record/1"
BLOCK_MIN = 256  # binary replies at least this long are waveform blocks: kept as hash + recipe


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
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def definition_hash(definition: Any) -> str:
    text = definition.model_dump_json(by_alias=True)
    return hashlib.sha256(text.encode()).hexdigest()


class Recorder:
    """Writes nothing until the first action, so a bench that is only opened leaves no record."""

    def __init__(self, directory: Path, session: Session, *, raw: bool = False) -> None:
        self.raw = raw
        self._last_acquisition: dict[str, int] = {}  # instrument -> seq of its latest acquisition record
        self.run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.path = directory / f"run-{self.run_id}.jsonl"
        self.session = session
        self._f = None
        self._seq = 0
        self._header: dict[str, Any] | None = None  # written ahead of the first event, not at open

    def _write(self, record: dict[str, Any]) -> None:
        record = {"seq": self._seq, **record}
        self._seq += 1
        if self._f is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._f = self.path.open("a", encoding="utf-8")
        self._f.write(json.dumps(_jsonable(record), ensure_ascii=False, separators=(",", ":")) + "\n")
        self._f.flush()

    def header(self) -> None:
        bench_json = self.session.bench.model_dump_json(by_alias=True)
        self._header = {
            "kind": "header",
            "schema": RECORD_SCHEMA,
            "run_id": self.run_id,
            "engine": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "bench": self.session.bench.name,
            "bench_sha256": hashlib.sha256(bench_json.encode()).hexdigest(),
            "definitions": {
                name: {
                    "id": inst.definition.id,
                    "version": inst.definition.version,
                    "sha256": definition_hash(inst.definition),
                }
                for name, inst in self.session.instruments.items()
            },
            # Plugin components the bench may be using; a replay warns when a hash changed (D36).
            "plugins": plugin_report(),
            # Not the JSON dump: it writes infinity (a HiZ load setting) as null.
            "bench_data": self.session.bench.model_dump(by_alias=True),
            "seed": self.session.seed,
            "clock": self.session.clock_mode,
            "idle": self.session.idle_mode,
            "started": time.time(),
        }

    def event(self, event: dict[str, Any]) -> None:
        if self._header is not None:
            header, self._header = self._header, None
            self._write(header)
        kind = event.get("kind")
        if kind == "acquisition":
            self._last_acquisition[str(event.get("instrument"))] = self._seq
        data = event.get("data")
        if (
            kind == "scpi"
            and event.get("dir") == "read"
            and isinstance(data, bytes)
            and len(data) >= BLOCK_MIN
            and "b64" in encode_bytes(data)
        ):
            # A binary block: its recipe regenerates it; the hash proves it (D36).
            ref: dict[str, Any] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            acq = self._last_acquisition.get(str(event.get("instrument")))
            if acq is not None:
                ref["recipe"] = acq
            if self.raw:
                ref.update(encode_bytes(data))
            event = {**event, "data": ref}
        self._write(event)

    def close(self) -> None:
        if self._f is not None and not self._f.closed:
            self._write({"kind": "end", "t_ns": self.session.now_ns, "ended": time.time()})
            self._f.close()
