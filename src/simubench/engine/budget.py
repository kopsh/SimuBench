"""The real-time budget of a read, and cancelling one (D88, #1510).

Actions run one at a time under the session lock (D9) and a read can take a while: a measurement
over a deep record, a sampled sweep. While it runs, the canvas, the probes and every other client
wait, and nothing can stop it. Two things end such a read early — both at the **checkpoints** the
frame computation passes before it computes each frame (:meth:`.session.Session.checkpoint`), so a
read stops between two frames, never inside one:

* **the budget**: ``compute_budget_s`` seconds of real time per action or view, off by default;
* **a cancel**: :meth:`Session.cancel`, safe to call from any thread without the session lock
  (closing a session whose read is running, a client that gave up).

An ended read is refused like any read beyond the model (:class:`ComputeAborted` is a
:class:`~.stream.ModelLimit`, so the seams that answer those answer this too: -221 on the
instrument, the session stays usable). It also leaves an ``abort`` event in the run record: the
budget and a cancel depend on the machine's speed and on the moment, so a replay does not time
anything — it reads the event and ends that action the same way (:meth:`ComputeBudget.abort_next`),
which is what keeps a replay identical to the run it records (D12).
"""

from __future__ import annotations

import threading
import time

from .stream import ModelLimit

#: Why a read ended early: the budget ran out, or someone cancelled it.
REASONS = ("budget", "cancel")


class ComputeAborted(ModelLimit):
    """A read ended at a checkpoint; ``abort`` is one of :data:`REASONS`. (``reason``, the
    inherited field, is the classification of a command that was not executed: another thing.)

    Not a dataclass of its own, deliberately: ``@dataclass(frozen=True)`` on this class would make
    it refuse ``__traceback__``, and an exception that passes through a ``contextlib`` context
    manager (``Session.view``) gets that assigned."""

    abort: str

    def __init__(self, code: int, detail: str, abort: str) -> None:
        super().__init__(code, detail)
        object.__setattr__(self, "abort", abort)  # the base class is frozen


def aborted(abort: str, detail: str, code: int = -221) -> ComputeAborted:
    return ComputeAborted(code, f"beyond the model: {detail}", abort)


class ComputeBudget:
    def __init__(self, seconds: float | None = None) -> None:
        if seconds is not None and not seconds > 0:
            raise ValueError(f"a compute budget is a positive number of seconds, not {seconds!r}")
        self.seconds = seconds
        self._cancelled = threading.Event()
        self._deadline: float | None = None
        self._forced: str | None = None

    def arm(self) -> None:
        """An action or view begins: the budget counts from now, a cancel meant for the one before
        is stale. A reason a replay forced stays — it is for this action."""
        self._cancelled.clear()
        # perf_counter, not monotonic: on Windows monotonic ticks every ~15.6 ms, so a budget below
        # that would never be seen to run out within a quick action (#1538)
        self._deadline = None if self.seconds is None else time.perf_counter() + self.seconds

    def disarm(self) -> None:
        self._deadline = None
        self._forced = None

    def cancel(self) -> None:
        """End the read in progress at its next checkpoint. Any thread, no lock."""
        self._cancelled.set()

    def abort_next(self, reason: str) -> None:
        """A replay ends the next action the way the record says it ended."""
        if reason not in REASONS:
            raise ValueError(f"an abort's reason is one of {REASONS}, not {reason!r}")
        self._forced = reason

    def reason(self, *, live: bool = True) -> str | None:
        """Why the read must end now, or None. ``live`` is False while a replay runs: nothing it
        times or hears counts, only what the record forced."""
        if self._forced is not None:
            return self._forced
        if not live:
            return None
        if self._cancelled.is_set():
            return "cancel"
        if self._deadline is not None and time.perf_counter() > self._deadline:
            return "budget"
        return None

    @staticmethod
    def describe(reason: str) -> str:
        """The error's text: what the record says and nothing the machine knows — a replay's reply
        must be the run's, byte for byte (the budget's seconds ride in the ``abort`` event)."""
        return "the read was cancelled" if reason == "cancel" else "the read passed its compute budget"
