"""The workspace event stream (issue #1484): what no session owns, fanned out to ``/api/events``.

The session's event stream (:mod:`simubench.engine.events`) carries what happens *inside* a
running bench; a workspace file's version and a script's arrival at the access ports have no
session to live on. The shape is declared here and nowhere else (the engine's ``EVENT_KINDS``
is the counterpart for the session stream): the write points (:class:`BenchFiles`,
:class:`DefinitionFiles`) and the access servers' connection bookkeeping publish, the socket in
``routes/events.py`` forwards to the Web, whose file and session queries re-key on the events
instead of polling. Publishing is thread-safe and never blocks a write: with no subscriber the
event is dropped, and content the server wrote itself is not announced twice (the write points
mark it through :meth:`WorkspaceWatcher.note`, so the watcher's scan stays silent about it).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..library.loader import SKIP_DIRS
from ..library.workspace_layout import is_bench_file, is_definition_file

#: Every kind the workspace stream carries, with what the event says. A new kind is a row here
#: and nowhere else — the WS endpoint's ``kind`` description is generated from this mapping.
WORKSPACE_KINDS: dict[str, str] = {
    "file_changed": "a workspace file's version changed (path: bench or definition file, "
    "workspace-relative; written through the server's write points or noticed on disk)",
    "clients_changed": "a script connected to or left a session's access ports (sid)",
    "sessions_changed": "a session started or stopped (sid; its ports, scripts and life are the "
    "sessions domain's, not one session's own)",
}


class WorkspaceEvent(BaseModel):
    """One event of the workspace's event stream (WebSocket ``/api/events``)."""

    model_config = ConfigDict(
        frozen=True, populate_by_name=True, json_schema_serialization_defaults_required=True
    )

    seq: int
    kind: str = Field(..., description=" | ".join(WORKSPACE_KINDS))
    path: str | None = Field(None, description="The workspace-relative file, for file_changed")
    sid: str | None = Field(
        None,
        description="The session the event is about: whose connected scripts changed "
        "(clients_changed), or which started or stopped (sessions_changed)",
    )


def put_bounded(queue: asyncio.Queue[dict[str, Any]], ev: dict[str, Any]) -> None:
    """One event into a bounded subscriber queue, without blocking the writer: when the queue is
    full the oldest event is dropped — a slow client must not stall what produces the events.
    Both event streams' sockets queue this way: the workspace's (:meth:`WorkspaceEvents.publish`)
    and the session's (``routes/events.py``)."""
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()  # drop the oldest rather than block the writer
    queue.put_nowait(ev)


class WorkspaceEvents:
    """The fan-out hub: one queue per connected socket, publishes from any thread.

    The first subscriber starts the workspace watcher (nobody is listening before that, so
    scanning would only burn stat calls); the last one's departure stops it."""

    def __init__(self, watcher: WorkspaceWatcher | None = None) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: list[asyncio.Queue[dict[str, Any]]] = []
        self._watcher = watcher
        self._scan_task: asyncio.Task[None] | None = None

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue[dict[str, Any]]:
        """Register one listener's queue; events are numbered per connection, at the socket."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._loop = loop
            self._subs.append(queue)
            first = len(self._subs) == 1
        if first and self._watcher is not None and self._scan_task is None:
            self._scan_task = asyncio.create_task(self._watcher.run())
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if queue in self._subs:
                self._subs.remove(queue)
            last = not self._subs
        if last and self._scan_task is not None:
            self._scan_task.cancel()
            self._scan_task = None

    def publish(self, kind: str, **fields: Any) -> None:
        """Queue one event for every subscriber. Thread-safe: a write runs on an engine worker
        thread, the listeners drain on the service's loop. With no subscriber it is dropped."""
        if kind not in WORKSPACE_KINDS:
            raise ValueError(f"unknown workspace event kind {kind!r}")
        with self._lock:
            subs, loop = list(self._subs), self._loop
        if not subs or loop is None:
            return
        ev = {"kind": kind, **fields}
        for queue in subs:
            loop.call_soon_threadsafe(put_bounded, queue, ev)

    def expect_write(self, path: str, sha256: str | None) -> None:
        """A write point is about to write this content and will publish it (#1526): the watcher's
        scan must not announce it first. ``None`` withdraws it after a rolled-back write."""
        if self._watcher is not None:
            self._watcher.expect(path, sha256)

    def note_written(self, path: str, sha256: str) -> None:
        """A write point published this path's content itself: the watcher's scan must not
        repeat it. Call without a matching :meth:`publish` only for content already on disk."""
        if self._watcher is not None:
            self._watcher.note(path, sha256)


#: How often the watcher re-stats the workspace's bench and definition files, in seconds. Fast
#: enough that an external editor's save reads as immediate, slow enough to be nothing to a
#: local service (a stat per file; the content is read only when a stat moved or once after a
#: change — two writes inside one timestamp tick leave the stat unmoved, #1502).
SCAN_S = 2.0

#: The stat placeholder for content a write point has just announced (its stat is not known
#: here): the next pass hashes the file once, and stays silent while the content is the one
#: the write point already published.
NOTED = (-1, -1)


class WorkspaceWatcher:
    """Notices version changes the server's write points did not make (issue #1484): a text
    editor, a second service on the same workspace. One pass stats every bench and definition
    file, reads and hashes the ones whose stat moved, and publishes ``file_changed`` for the
    ones whose content hash moved too. A file whose stat just moved is read once more on the
    next pass: a second write inside the same timestamp tick leaves mtime and size unmoved, and
    only the content tells it apart (#1502). The first pass baselines the workspace; a write
    point that published marks its content (:meth:`note`), so the scan does not repeat it."""

    def __init__(self, workspace: Path, publish: Callable[..., None], scan_s: float = SCAN_S) -> None:
        self.workspace = workspace
        self.publish = publish
        self.scan_s = scan_s
        self._lock = threading.Lock()
        # Workspace-relative path -> (the stat it was last seen with, the sha256 it carried, and
        # whether a change may still share that stat: a write that lands in the same timestamp
        # tick as the one the stat was taken from leaves it unchanged, so the next pass re-reads).
        self._known: dict[str, tuple[tuple[int, int], str, bool]] = {}
        # Workspace-relative path -> the content a write point is putting on disk and will
        # announce itself: a pass that reads it before the write point's note() stays silent.
        self._expected: dict[str, str] = {}

    def expect(self, path: str, sha256: str | None) -> None:
        """A write point is about to put this content on disk and announce it (#1526): call before
        the file changes, so a pass landing between the write and :meth:`note` does not announce
        it first. ``None`` withdraws it (the write was rolled back)."""
        with self._lock:
            if sha256 is None:
                self._expected.pop(path, None)
            else:
                self._expected[path] = sha256

    def note(self, path: str, sha256: str) -> None:
        """A write point published this path's content: the scan must not repeat it."""
        with self._lock:
            self._known[path] = (NOTED, sha256, False)
            if self._expected.get(path) == sha256:
                del self._expected[path]

    def _pass(self) -> None:
        """One scan, run off the event loop (``asyncio.to_thread``): the files are kilobytes,
        but reads and hashes are still not the loop's work."""
        seen: set[str] = set()
        for path in self._files():
            rel = path.relative_to(self.workspace).as_posix()
            seen.add(rel)
            with self._lock:
                known = self._known.get(rel)
            try:
                st = path.stat()
            except OSError:
                st = None
            if st is None:
                if known is not None:  # deleted outside the server: the queries must follow
                    with self._lock:
                        self._known.pop(rel, None)
                    self.publish("file_changed", path=rel)
                continue
            stat = (st.st_mtime_ns, st.st_size)
            if known is not None and known[0] == stat and not known[2]:
                continue  # exactly as last seen, and verified since: no read, no hash
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            with self._lock:
                # A write point may have noted this content while the file was being read: judge
                # against what is known now, not the snapshot taken before the read (#1526).
                current = self._known.get(rel)
                if current is not known and current is not None and current[1] != sha:
                    # The note landed during the read and this read is not what it noted: the
                    # read may predate the write. Keep the note; its placeholder stat makes the
                    # next pass read again and judge against it (#1547).
                    continue
                known = current
                # A stat that just moved may still collect a write in the same timestamp tick,
                # so the next pass reads this file once more (a verified file is not re-read).
                self._known[rel] = (stat, sha, known is None or known[0] != stat)
                announced = self._expected.get(rel) == sha  # its write point publishes it (#1526)
            if known is None or known[1] == sha or announced:
                continue  # the first pass baselines; a noted write or a bare touch stays silent
            self.publish("file_changed", path=rel)
        with self._lock:
            # A file a write point created and noted after this pass listed the folder is known but
            # unseen: it is not gone, and announcing it would repeat the server's own write (#1547).
            gone = [rel for rel in self._known if rel not in seen and not (self.workspace / rel).exists()]
            for rel in gone:
                del self._known[rel]
        for rel in gone:  # a folder removed whole: same announcement as a deleted file
            self.publish("file_changed", path=rel)

    def _files(self) -> list[Path]:
        """The workspace's bench files and definition files (any ``instruments/`` folder's
        ``*.yaml``; the layout files are read by no re-keyed query)."""
        out = []
        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                p = Path(root) / name
                if is_bench_file(name) or is_definition_file(p):
                    out.append(p)
        return sorted(out)

    async def run(self) -> None:
        # The first pass baselines now, not one scan late: a change made while the first listener
        # connects must not vanish into the baseline.
        with contextlib.suppress(OSError):
            await asyncio.to_thread(self._pass)
        while True:
            await asyncio.sleep(self.scan_s)
            try:
                await asyncio.to_thread(self._pass)
            except asyncio.CancelledError:
                raise
            except OSError:  # a file vanished under the pass: the next one sees the truth
                pass
