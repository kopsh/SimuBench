"""The bench preview (the acceptance page's "what would this bench look like"): build the bench
from its file without running it, leaving out what cannot be built, and show the session that
would come up. This is a projection over a bench *file* — validation diagnostics turned into a
build that keeps the sound parts — so it lives beside the views, not in the manager, whose job is
the sessions' lifecycle and the access guards. Nothing here runs, starts or records a session."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from ..contracts import api as M
from ..engine.portref import split
from ..engine.session import Session
from ..models.bench import BenchDefinition
from .manager import ServedSession

if TYPE_CHECKING:
    from .manager import SessionManager


def preview(manager: SessionManager, bench_path: str) -> M.BenchPreview:
    """Build the bench without running it, leaving out what cannot be built (the file's errors name
    what is left out; a wire whose end is gone goes too, by its ends, #1269)."""
    doc = manager.files.read(bench_path)
    out = M.BenchPreview(path=bench_path, sha256=doc.sha256)
    errors = [d for d in doc.diagnostics if d.severity == "error"]
    if any(d.code == "E-SCHEMA" for d in errors):
        out.error = "文件不符合测试台格式"
        return out
    data = copy.deepcopy(doc.data)
    instruments, parts = data.get("instruments") or {}, data.get("parts") or {}
    dropped_wires: set[int] = set()
    for d in errors:
        segs = [p.replace("~1", "/").replace("~0", "~") for p in d.pointer.split("/")[1:]]
        if d.code.startswith("E-STATE-") and len(segs) >= 4:
            instruments.get(segs[1], {}).get("state", {}).pop(segs[3], None)
            out.omitted.append(f"{segs[1]}.{segs[3]}")
        elif d.code == "E-MODEL-UNKNOWN" and d.subject:
            instruments.pop(d.subject, None)
            out.omitted.append(d.subject)
        elif d.code in ("E-COMPONENT-UNKNOWN", "E-NAME-DUPLICATE") and d.subject:
            parts.pop(d.subject, None)
            out.omitted.append(d.subject)
        elif d.subject and d.subject.startswith("wire:"):
            dropped_wires.add(int(d.subject[5:]))
    names = set(instruments) | set(parts)
    wires = data.get("wires") or []
    kept: list[int] = []
    for i, w in enumerate(wires):
        ends = (str(w.get("from", "")), str(w.get("to", "")))
        if i in dropped_wires or any(split(e).owner not in names for e in ends):
            out.omitted.append(f"{ends[0]} → {ends[1]}")  # the wire by its ends, not its index (#1269)
        else:
            kept.append(i)
    data["wires"] = [wires[i] for i in kept]
    path = manager.resolve(bench_path)
    try:
        bench = BenchDefinition.model_validate(data)
        session = Session(bench, manager.files.library(path), base_dir=path.parent)
    except Exception as exc:
        out.error = str(exc).splitlines()[0]
        return out
    try:
        info = manager.views.describe(ServedSession("preview", session, path))
    finally:
        session.close()
    for w in info.wires:
        w.index = kept[w.index]
    info.bench_sha256 = doc.sha256
    info.bench_fingerprint = doc.fingerprint
    out.session = info
    return out
