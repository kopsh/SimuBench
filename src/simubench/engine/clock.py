"""The bench's virtual clock (D47, #1531).

Virtual time, how it moves (``command``, ``stepped``, or a live clock: ``wall``, ``scaled``), the
idle policy of the command clock, and the time actions — advance, wait, seek, a live clock's
rescale. The clock reads the session only through :class:`ClockHost`: moving time first lets the
dynamic components record the instant (``sync``), then counts a change of the signals, announces
the step (an ``advance`` event) and lets the faults tick.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from ..errors import Invalid
from ..models.bench import LIVE_CLOCKS

#: The fastest a ``scaled`` clock may run: virtual seconds per real second.
MAX_TIME_SCALE = 1_000_000.0
#: In command-driven mode, idle gaps shorter than this are script overhead and do not advance time.
IDLE_THRESHOLD_NS = 10_000_000
#: Idle gaps are rounded to this resolution so that sleeps produce stable virtual times.
IDLE_QUANTUM_NS = 1_000_000


def check_scale(scale: float) -> None:
    if not (0 < scale <= MAX_TIME_SCALE) or scale != scale:
        raise Invalid(f"time scale must be above 0 and at most {MAX_TIME_SCALE:g}, not {scale!r}")


class _Emitter(Protocol):
    def emit(self, event: dict[str, Any]) -> None: ...


class _Ticker(Protocol):
    def tick(self) -> None: ...


class ClockHost(Protocol):
    """What the clock asks of its session when time moves."""

    @property
    def events(self) -> _Emitter: ...

    @property
    def faults(self) -> _Ticker: ...

    def sync(self) -> None:
        """Let the dynamic components record changes at the current instant, before time moves."""

    def revise(self, *, electrical: bool) -> None:
        """Count a change of the signals (time-dependent components may change)."""


class Clock:
    def __init__(
        self,
        host: ClockHost,
        lock: threading.RLock,
        *,
        mode: str,
        time_scale: float,
        idle: str,
        start: dt.datetime,
    ) -> None:
        self._host = host
        self._lock = lock
        self.mode = mode
        #: Virtual seconds per real second on a live clock: 1 on the wall clock, settable on
        #: ``scaled`` (:meth:`set_time_scale`). Time is ``_scaled_base_ns`` + real time since
        #: ``_scaled_ref_ns`` × scale, so changing the scale re-bases instead of jumping.
        self.time_scale = 1.0 if mode == "wall" else float(time_scale)
        check_scale(self.time_scale)
        if idle not in ("wall", "sleep", "off"):
            raise Invalid(f"idle must be wall, sleep or off, not {idle!r}")
        self.idle_mode = idle
        #: The bench's run.start: calendar time at virtual time 0.
        self.start = start
        self.now_ns = 0
        #: Replaying a run record: time moves only where the record says (no idle steps).
        self.replaying = False
        #: Set by the serving layer on a served session (``server.manager``): called with the
        #: caller's ``force`` before an explicit advance and raises to refuse it, because scripts
        #: are connected to the ports and their view of time would move (#121, issue 624).
        #: In-process sessions (pytest plugin, ``@simubench`` backend, replay) have no clients.
        self.confirm_advance: Callable[[bool], None] | None = None
        wall = time.monotonic_ns()
        self._scaled_ref_ns = wall
        self._scaled_base_ns = 0
        self._last_wall = wall

    @property
    def live(self) -> bool:
        """A live clock (wall, scaled): virtual time follows real time, never advanced by hand."""
        return self.mode in LIVE_CLOCKS

    def calendar(self) -> dt.datetime:
        """Calendar time now: the bench's run.start plus virtual time (instrument clocks, due dates)."""
        return self.start + dt.timedelta(microseconds=self.now_ns // 1000)

    def _live_ns(self, wall_ns: int) -> int:
        """Virtual time at real time ``wall_ns`` on a live clock (wall, or scaled by ``time_scale``)."""
        return self._scaled_base_ns + round((wall_ns - self._scaled_ref_ns) * self.time_scale)

    def _catch_up(self, wall_ns: int) -> None:
        self._host.sync()
        self.now_ns = max(self.now_ns, self._live_ns(wall_ns))

    def follow_wall(self) -> None:
        """Engine-internal: live clock only, bring virtual time up to real time (D47; passive
        views read the present). Called by actions (:meth:`begin_action`) and by ``Session.view``
        — external code reads the bench through ``view``, never by calling this directly."""
        if self.live:
            self._catch_up(time.monotonic_ns())

    def set_time_scale(self, scale: float) -> None:
        """Change a ``scaled`` clock's rate while it runs. Time already passed stays passed: the
        clock is first brought up to now and the new rate counts from there."""
        check_scale(scale)
        with self._lock:
            if self.mode != "scaled":
                raise RuntimeError("the time scale can only be set on a scaled clock")
            self.follow_wall()
            self._scaled_base_ns = self.now_ns
            self._scaled_ref_ns = time.monotonic_ns()
            self.time_scale = float(scale)

    def begin_action(self) -> None:
        """An action starts: a live clock catches up with real time; the command clock with
        ``idle: wall`` counts the idle real time since the last action."""
        wall = time.monotonic_ns()
        if self.live:
            self._catch_up(wall)
        elif self.mode == "command" and not self.replaying and self.idle_mode == "wall":
            # Counting idle real time makes results depend on machine speed; only 'wall' does it
            # (D47). 'sleep' and 'off' move time only by command, advance and time.sleep.
            idle = wall - self._last_wall
            if idle >= IDLE_THRESHOLD_NS:
                self.step((idle // IDLE_QUANTUM_NS) * IDLE_QUANTUM_NS, reason="idle")

    def end_action(self) -> None:
        self._last_wall = time.monotonic_ns()

    def step(self, dt_ns: int, reason: str, **detail: Any) -> None:
        """Move virtual time by ``dt_ns``; the advance event says why (``reason``) and, for a
        command, which instrument and header took the time (#1260), so the UI can explain it.
        The engine's own step: the caller holds the session lock."""
        if dt_ns <= 0:
            return
        host = self._host
        host.sync()
        self.now_ns += int(dt_ns)
        host.revise(electrical=False)  # time-dependent components (filters, sweeps) may change
        host.events.emit({"kind": "advance", "dt_ns": int(dt_ns), "reason": reason, **detail})
        host.faults.tick()

    def delay(self, seconds: float) -> None:
        """Link latency: virtual time on the command clock, real time on a live clock."""
        if seconds <= 0:
            return
        if self.mode == "command":
            self.step(round(seconds * 1e9), reason="latency")
        elif self.live:
            time.sleep(min(seconds / self.time_scale, 5.0))  # real time for that much virtual time

    def seek(self, t_ns: int, reason: str = "replay") -> None:
        """Move virtual time forward to ``t_ns`` (replaying a record); never backwards."""
        with self._lock:
            if t_ns > self.now_ns:
                self.step(t_ns - self.now_ns, reason=reason)

    def advance(self, seconds: float, reason: str = "explicit", force: bool = False) -> None:
        """Advance virtual time (action: advance). Works in every clock mode except the live ones.

        On a served session the serving layer's ``confirm_advance`` refuses while scripts are
        connected to the ports, unless ``force`` (issue 624); the guard travels with the call, so
        no facade can forget it."""
        with self._lock:
            if self.confirm_advance is not None:
                self.confirm_advance(force)
            if self.live:
                raise RuntimeError(f"cannot advance a {self.mode} clock")
            self.step(round(seconds * 1e9), reason=reason)

    def wait(self, seconds: float, reason: str = "wait") -> None:
        """Time a client spends waiting: a read that timed out, a wait for a service request.

        Virtual time passes on a command clock and real time on a wall clock, so the wait costs
        what the client waited. Unlike :meth:`advance` it is not refused while scripts are
        connected (#121, issue 624): the one waiting is one of them, and its own view of time is
        what moves. It is recorded like an advance and replays as one."""
        if seconds <= 0:
            return
        if self.mode == "wall":
            time.sleep(seconds)
            return
        with self._lock:
            self.step(round(seconds * 1e9), reason=reason)
