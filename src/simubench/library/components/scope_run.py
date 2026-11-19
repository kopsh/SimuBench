"""Run control: the run, stop and single modes, an acquisition waiting for its trigger, the status flags and the screen's state. Part of ``ScopeAcquisition`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ...engine.component import (
    OpResult,
    ScreenState,
)
from ...engine.stream import (
    ModelLimit,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope_pending import Acquisition, PendingAcquisitions
from .scope_record import (
    channel_index,
)

if TYPE_CHECKING:
    pass
from .scope_acquisition_host import AcquisitionHost


class RunControl:
    def __init__(self, host: AcquisitionHost) -> None:
        self.host = host
        self.running = True
        self.armed = False
        self.last_triggered = False
        # ty sees the @component class as type[Component] and cannot match it to the protocol.
        self.pending = PendingAcquisitions(cast("Acquisition", host))

    def run_state(self) -> str:
        """RUN, STOP or SING (``:RSTate?``, #248): a single acquisition armed and not yet done reads
        SING, a stopped scope STOP, a running one RUN."""
        if self.armed and self.host._is("single"):
            return "SING"
        return "RUN" if self.is_running() else "STOP"

    def status_flags(self) -> dict[str, bool]:
        """RUN while acquiring, WAIT while waiting for a trigger (the operation status register).

        A single acquisition waits until it is done; a continuous run (auto, normal) waits while
        its trigger finds no edge now, whatever an earlier acquisition found (#352)."""
        running = self.run_state() != "STOP"
        if not running:
            return {"RUN": False, "WAIT": False}
        if self.waiting():
            return {"RUN": True, "WAIT": True}
        if self.host._is("auto") or self.host._is("normal"):
            return {"RUN": True, "WAIT": not self.host.capture.triggers_now()}
        return {"RUN": True, "WAIT": not self.last_triggered}

    def op_run_state(self, **_: Any) -> OpResult:
        return OpResult(self.run_state().encode())

    def is_running(self) -> bool:
        """Stopped by a stop mode (when the model has one) or by a stop command."""
        if self.host.param("stop"):
            return not self.host._is("stop")
        return self.running

    def shows_live(self) -> bool:
        """Whether the screen follows the signal (running in an auto or normal mode) rather than
        holding the last acquisition (stopped, or a single acquisition waiting)."""
        return self.is_running() and (self.host._is("auto") or self.host._is("normal")) and not self.armed

    def screen_state(self, frame: int | None = None) -> ScreenState:
        """What the screen shows (the :class:`~simubench.engine.component.ScreenSource` protocol):
        the screen's own rules live here — a running scope redraws the signal now (a frame is not an
        acquisition: no record, no virtual time, noise and untriggered phase of its own, D47); an
        untriggered normal mode holds; a frame beyond the model leaves the screen holding what it
        has (#986). ``frame`` is the projection's redraw counter (ServedSession.frames)."""
        stored = dict(self.host.recorder.records)
        live, records, shown = self.shows_live(), stored, False
        if live and frame is not None:
            try:
                drawn, _, triggered = self.host.capture.compute(self.host.displayed(), frame=frame)
                # A triggered or auto frame replaces the records; an untriggered normal frame
                # does not (the screen keeps the last acquisition until the trigger comes).
                if triggered or self.host._is("auto") or not stored:
                    records, shown = drawn, True
            except ModelLimit:
                live = False  # the screen holds what it has, as a read of such an acquisition is refused
        try:
            _, dt, pre, _ = self.host._timing()
            screen_left = -pre * dt
        except (AttributeError, ModelLimit, ScpiError, ValueError):  # a sampling scope has no such timing
            screen_left = -5.0 * float(self.host._st("timebase.scale"))
        return ScreenState(
            live=live,
            shown=shown,
            records=records,
            stored=stored,
            waiting=bool(self.armed and self.host._is("single")),
            trigger_level=self.host.trigger_level(),
            trigger_source=channel_index(self.host._st("trigger.source")),
            acquisitions=int(self.host.recorder.acq_count),
            screen_left_s=screen_left,
        )

    def finish_single(self) -> None:
        self.armed = False
        self.running = False
        after = str(self.host.param("after_single") or "")
        if after:
            self.host.set_mode(after)

    def acquisition_channels(self, channel: int | None) -> list[int]:
        chans = set(self.host.displayed())
        if channel is not None and self.host.param("missing") == "acquire":
            chans.add(channel)
        return sorted(chans)

    def pending_mode(self) -> bool:
        """Acquisitions finish in virtual time; one a front-panel button starts finishes at once,
        as the panel's own (D32: no virtual time passes for a panel operation)."""
        on_panel = self.host.owner is not None and self.host.owner.on_panel
        return str(self.host.param("completion")) == "pending" and not on_panel

    def acquire_now(
        self, channels: list[int] | None = None, force: bool = False, stopped_too: bool = False
    ) -> int:
        """One acquisition attempt as the run mode allows it (``force``: whatever the trigger and the
        run state; ``stopped_too``: also when stopped, still waiting for a trigger). Returns the
        virtual time used (ns): with ``completion: pending`` the attempt is scheduled instead, and
        uses none."""
        if not force and not stopped_too and not self.is_running():
            return 0
        if self.pending_mode() and (force or stopped_too or self.host._is("single")):
            self.pending.schedule(channels, force)  # continuous runs (auto, normal) refresh at once
            return 0
        chans = self.host.displayed() if channels is None else channels
        if self.host.segments.segment_count() > 1:
            segments, complete = self.host.segments.acquire_segments(chans, force=force)
            self.last_triggered = complete and all(seg.triggered for seg in segments)
            if not complete:
                return 0  # waiting for a segment's trigger: the previous records stay
            used = (
                segments[-1].end_ns
                + round(float(self.host.param("overhead_s")) * 1e9)
                - self.host.session.now_ns
            )
            self.host.segments.store_segments(segments, used)
            if self.host._is("single"):
                self.finish_single()
            return used
        records, used, triggered = self.host.capture.compute(chans)
        self.last_triggered = triggered
        if force or triggered or self.host._is("auto"):
            self.host.recorder.store(records, used)
            if self.host._is("single"):
                self.finish_single()
        return used  # waiting for a trigger: the previous records stay

    def poll(self) -> int:
        """A single acquisition still waiting: try again now (the signal may have arrived since)."""
        if self.pending_mode():
            self.retry_pending()
            return 0
        return self.acquire_now() if self.armed and self.host._is("single") else 0

    def waiting(self) -> bool:
        """A single acquisition (or a digitize) not done yet."""
        return self.pending.waiting()

    def retry_pending(self) -> None:
        """Armed for a trigger that had not come: look again now (the signal may have changed)."""
        self.pending.retry()

    def mode_text(self) -> bytes:
        """The run mode as the definition formats its settings."""
        assert self.host.owner is not None
        path = self.host.resolve_path(self.host.mode_path() or "")
        return self.host.owner.runner.format_setting(path)

    def run_mode(self, query: bool = False, mode: str | None = None, **_: Any) -> OpResult:
        if query:
            used = self.poll()
            return OpResult(self.mode_text(), used)
        if mode is None:
            raise ScpiError(-109)
        self.pending.cancel()  # a new mode replaces whatever acquisition was waiting
        if self.pending_mode() and self.is_running() and (self.host._is("auto") or self.host._is("normal")):
            self.host.recorder.keep_last_frame()
        self.host.set_mode(mode)
        if self.host._is("force", mode):
            used = self.acquire_now(force=True)
            self.running, self.armed = False, False
            return OpResult(duration_ns=used)
        if self.host._is("single", mode):
            self.running, self.armed = True, True
            if self.host.owner is not None:
                self.host.owner.status_event("ARM")  # armed for a trigger (:AER?, #248)
            return OpResult(duration_ns=self.acquire_now())
        self.running, self.armed = not self.host._is("stop", mode), False
        return OpResult()

    def single(self, **_: Any) -> OpResult:
        return self.run_mode(mode=str(self.host.param("single")[0]))

    def run(self, **_: Any) -> OpResult:
        self.running = True
        if self.host.param("stop") and self.host._is("stop"):
            self.host.set_mode(str(self.host.param("auto")[0]))
        if self.host.owner is not None:
            self.host.owner.status_event("ARM")  # armed for a trigger (:AER?, #248)
        if self.host._is("single"):
            self.armed = True
            return OpResult(duration_ns=self.acquire_now())
        return OpResult()

    def stop(self, **_: Any) -> OpResult:
        self.pending.cancel()
        self.running, self.armed = False, False
        stops = self.host.param("stop")
        if stops:
            self.host.set_mode(str(stops[0]))
        return OpResult()

    def force_trigger(self, **_: Any) -> OpResult:
        return OpResult(duration_ns=self.acquire_now(force=True))

    def wait(self, timeout: float | None = None, **_: Any) -> OpResult:
        used = self.poll()
        if self.pending_mode() and self.host.owner is not None:
            if self.pending.due_ns is not None:
                used += self.host.owner.wait_until(self.pending.due_ns)
            elif timeout is not None and self.waiting():
                used += self.host.owner.wait_until(self.host.owner.now_ns() + round(float(timeout) * 1e9))
        return OpResult(duration_ns=used)

    def opc(self, query: bool = False, **_: Any) -> OpResult:
        assert self.host.owner is not None
        used = self.poll()
        if query:
            reply, waited = self.host.owner.opc_query()  # and whatever else the instrument has pending
            return OpResult(reply, used + waited)
        used += self.host.owner.wait_pending()
        self.host.owner.arm_opc()
        return OpResult(duration_ns=used)

    def status(self) -> str:
        """The trigger status in the model's words (``status_text``)."""
        words = {**self.host.defaults["status_text"], **(self.host.param("status_text") or {})}
        if not self.is_running():
            return str(words["stopped"])
        if self.host._is("single"):
            assert self.host.owner is not None
            if self.pending.trigger_ns is not None and self.host.owner.now_ns() >= self.pending.trigger_ns:
                return str(words["triggered"])  # the record after the trigger is filling (pending)
            return str(words["waiting"])
        _, _, triggered = self.host.capture.compute([])
        if self.host._is("auto"):
            return str(words["triggered" if triggered else "auto"])
        return str(words["triggered" if triggered else "waiting"])

    def status_query(self, **_: Any) -> OpResult:
        return OpResult(self.status().encode())

    def digitize(self, sources: list[bytes] | None = None, **_: Any) -> OpResult:
        chans: list[int] = []
        for raw in sources or []:
            name = V.parse_string(raw).upper()
            m = name.removeprefix("CHANNEL").removeprefix("CHAN")
            if not m.isdigit() or not 1 <= int(m) <= self.host._channels():
                raise ScpiError(-224, f"unknown source {name!r}")
            chans.append(int(m))
            assert self.host.owner is not None
            self.host.owner.set_state(f"ch{int(m)}.display", True)
        if not chans:
            chans = self.host.displayed()
        used = self.acquire_now(chans, stopped_too=True)  # whatever the run state
        if self.pending_mode() and self.pending.due_ns is not None and self.host.owner is not None:
            used += self.host.owner.wait_until(
                self.pending.due_ns
            )  # a digitize returns once its record is full
        if self.host.param("digitize_stops"):
            self.running, self.armed = False, False  # the InfiniiVision stops after :DIGitize (#248)
        return OpResult(duration_ns=used)
