"""Replaying a run record (D36): rebuild the bench, resend every action at its recorded virtual
time, and compare every reply with the record.

A record is replayable on its own: its header carries the bench and a hash of each instrument
definition. The replay runs on the recorded seed; time moves only where the record says, so a
record made on the command or wall clock replays identically. Waveform blocks recorded as a hash
(their recipe) are compared by hash.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..api import open_bench
from ..models.bench import BenchDefinition, Fault
from .record import RECORD_SCHEMA, definition_hash


@dataclass
class Mismatch:
    seq: int
    instrument: str
    t_ns: int
    expected: str
    actual: str
    command: str | None = None  # the last command sent to that instrument


@dataclass
class ReplayReport:
    record: str
    bench: str
    actions: int = 0
    replies: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def as_dict(self) -> dict[str, Any]:
        return {
            "record": self.record,
            "bench": self.bench,
            "actions": self.actions,
            "replies": self.replies,
            "ok": self.ok,
            "mismatches": [m.__dict__ for m in self.mismatches],
            "warnings": self.warnings,
        }


def read_record(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _bytes(data: Any) -> bytes | None:
    if not isinstance(data, dict):
        return None
    if "text" in data:
        return str(data["text"]).encode("ascii")
    if "b64" in data:
        return base64.b64decode(data["b64"])
    return None


def _show(data: bytes | None, limit: int = 120) -> str:
    if data is None:
        return "(no reply)"
    try:
        text = data.decode("ascii")
        return text if len(text) <= limit else text[:limit] + f"… ({len(data)} bytes)"
    except UnicodeDecodeError:
        return f"<{len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()[:16]}…>"


def replay(
    path: str | Path, *, definition_dirs: list[str | Path] | None = None, stop_at_first: bool = False
) -> ReplayReport:
    path = Path(path)
    events = iter(read_record(path))
    header = next(events, None)
    if not header or header.get("kind") != "header" or header.get("schema") != RECORD_SCHEMA:
        raise ValueError(f"{path.name} is not a SimuBench run record")
    if "bench_data" not in header:
        raise ValueError(
            f"{path.name} was recorded before records carried their bench; it cannot be replayed"
        )
    bench = BenchDefinition.model_validate(header["bench_data"])
    clock = "stepped" if header.get("clock") == "wall" else header.get("clock")
    idle = header.get("idle", "wall")
    if idle == "sleep":
        idle = "off"  # the replay drives time from the record; nothing here may fake a sleep (D47)
    session = open_bench(
        bench, clock=clock, seed=header.get("seed"), idle=idle, definition_dirs=definition_dirs
    )
    session.replaying = True
    report = ReplayReport(record=str(path), bench=bench.name)
    for name, want in (header.get("definitions") or {}).items():
        inst = session.instruments.get(name)
        if inst is None:
            continue
        if want.get("sha256") and want["sha256"] != definition_hash(inst.definition):
            report.warnings.append(
                f"{name}: definition {want.get('id')} differs from the one recorded; differences may come from it"
            )
    last_command: dict[str, str] = {}
    try:
        for ev in events:
            kind = ev.get("kind")
            t = int(ev.get("t_start_ns", ev.get("t_ns", 0)))
            if kind == "scpi" and ev.get("dir") == "write":
                # A write a fault dropped is resent too: the replayed fault drops it the same way.
                session.seek(t)
                data = _bytes(ev.get("data")) or b""
                last_command[ev["instrument"]] = data.decode("ascii", errors="replace").strip()
                session.write(ev["instrument"], data, client="replay")
                report.actions += 1
            elif kind == "scpi" and ev.get("dir") == "read":
                session.seek(t)
                got = session.read(ev["instrument"], client="replay")
                report.replies += 1
                recorded = ev.get("data")
                want = _bytes(recorded)
                if want is None and isinstance(recorded, dict) and "sha256" in recorded:
                    same = got is not None and hashlib.sha256(got).hexdigest() == recorded["sha256"]
                    expected = f"<{recorded.get('size')} bytes, sha256 {str(recorded['sha256'])[:16]}…>"
                else:
                    same = got == want
                    expected = _show(want)
                if not same:
                    report.mismatches.append(
                        Mismatch(
                            ev["seq"],
                            ev["instrument"],
                            t,
                            expected,
                            _show(got),
                            last_command.get(ev["instrument"]),
                        )
                    )
                    if stop_at_first:
                        break
            elif kind == "state" and ev.get("source") not in ("scpi", None) and ev.get("path") != "*RST":
                session.seek(t)
                session.instrument(ev["instrument"]).set_state(ev["path"], ev["new"], source="replay")
                report.actions += 1
            elif kind == "fault" and ev.get("reason") == "injected" and ev.get("spec"):
                session.seek(t)
                session.faults.inject(Fault.model_validate(ev["spec"]), source="replay")
                report.actions += 1
            elif kind == "fault" and ev.get("reason") == "cleared" and ev.get("source") != "replay":
                session.seek(t)
                fid = ev.get("fault")
                if fid in session.faults.faults:
                    session.faults.clear(fid)
            elif kind == "advance" and ev.get("reason") in ("explicit", "idle", "sleep"):
                session.seek(int(ev["t_ns"]), reason=str(ev["reason"]))  # t_ns is the time after the step
    finally:
        session.close()
    return report
