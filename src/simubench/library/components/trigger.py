"""The trigger model and reading memory of meters and source-measure units (metrology-authoring.md 3).

``Triggerable`` is the arm → ready → fire → abort state machine a triggerable operation runs
(#632): a component mixes it in and writes only its action (``_act``) and its own SCPI
operations — the arming, the readiness, the trigger gating with its error codes and the trigger
delay in virtual time are the primitive's, as they are for the trigger model and the supply's
transient system alike.

``trigger_model`` runs the SCPI trigger system on a :class:`~simubench.engine.component.Sampler`:
INITiate arms it, each trigger (immediate, ``*TRG`` on the bus, or an external one) takes
``sample_count`` readings after ``delay``, ``trigger_count`` triggers make one run. The readings
take virtual time (the sampler's reading time, D12): on the command clock INITiate returns at once
and FETCh? waits, using that time; ``*OPC?`` and ``*WAI`` wait for the run too.

A sampler may give the run a shape of its own, beyond the two required protocol methods:

* ``run_samples()`` — how many readings one of its runs takes (a source-measure unit's sweep);
  it replaces the configured counts (one trigger, that many readings);
* ``samples_per_trigger()`` — with ``run_samples``, the readings one trigger takes, so the run
  is ``run_samples / samples_per_trigger`` triggers (a DAQ's scan list × scan count: one scan per
  trigger, so each ``*TRG`` on the bus runs one scan, #422);
* ``sample_fields(index)`` — additional fields of one reading (an SMU's VOLT, CURR, RES, STAT);
  ``reading_buffer``'s ``data`` op selects them by name, as ``READ`` and ``REL``;
* ``run_begin()`` / ``run_done()`` — around a run, for settings the sampler drives while it runs
  (a sweep holds the output on and the point levels set).

``reading_buffer`` keeps the readings: one or more named buffers, with a capacity and what happens
when one is full; the reading memory of a DMM (``R?``, ``DATA:POINts?``, ``DATA:REMove?``) and the
named buffers of an SMU (``TRACe:MAKE``, ``TRACe:DATA?``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...engine.component import Component, OpResult, Sampler, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError


@dataclass
class Reading:
    value: float
    t_ns: int  # virtual time the reading was taken
    fields: dict[str, float] = field(default_factory=dict)


def _number_formatter(owner: Component, digits: Any) -> Any:
    """The number writer a component's readings use: the definition's response format, or a fixed
    number of digits (an ``sb-*`` model's readings, which predate the definition format)."""
    if digits is None:
        return owner.format_number
    return lambda v: V.format_float(float(v), int(digits))


def format_readings(
    readings: list[Reading], elements: list[str] | None, number: Any, error: int = -222
) -> bytes:
    """Readings joined by commas; per reading each named element (``READ``: the reading, ``REL``:
    seconds since the first reading, else a field the sampler gave it)."""
    out: list[bytes] = []
    first = readings[0].t_ns if readings else 0
    # An element may be written long (VOLTage, from an enum_list setting, #252) or short (VOLT).
    names = [V.short_form(x) if x.upper() not in ("READ", "REL") else x.upper() for x in elements or ["READ"]]
    for r in readings:
        for e in names:
            if e.startswith("READ"):
                out.append(number(r.value))
            elif e.startswith("REL"):
                out.append(number((r.t_ns - first) / 1e9))
            elif e in {k.upper() for k in r.fields}:
                out.append(number(next(v for k, v in r.fields.items() if k.upper() == e)))
            else:
                raise ScpiError(error, f"no buffer element {e!r}")
    return b",".join(out)


class Triggerable:
    """The arm → ready → fire → abort state machine behind a triggerable operation (#632).

    A component that is armed (INITiate), becomes ready for a trigger (any ``ready_delay`` later,
    at once without one), takes a trigger (``*TRG`` on the bus, or the model's own trigger
    command), lets it take effect after ``delay`` and goes back to idle mixes this in and writes
    only its action and its own operations — like the protection latch's declared thresholds
    (#631), this is component-internal implementation, not a component type of its own (D50).

    States: ``idle`` (nothing armed) | ``armed`` (initiated, not ready yet) | ``ready`` (waiting
    for a trigger) | ``firing`` (a trigger accepted, its action on the way). The ready delay and
    the trigger delay take virtual time (D12) through the instrument's pending table, so
    ``*OPC?``/``*WAI`` wait for them and ABORt drops them; the params read are ``source``,
    ``ready_delay``, ``delay``, ``trigger_ignored_error`` (0: a trigger while not ready is ignored
    silently) and ``ignored_error`` (0: INITiate while armed restarts nothing).

    What the action *does* beyond the state machine is the carrier's increment: ``_act`` runs at
    the firing's end, ``_fire_span`` says when that is (a firing that itself takes virtual time —
    the trigger model's readings — returns it and prepares at the trigger), plus the ``_on_arm`` /
    ``_on_trigger`` / ``_on_settled`` hooks and where a firing settles (``_next_after_act``). The
    carrier knows no clocks: the primitive runs the delays.
    """

    def _init_trigger(self) -> None:
        """Reset to idle without actions (*RST; the instrument's pending table is cleared too)."""
        self._trig_state = "idle"
        self._fire_end_ns: int | None = None  # when the firing in progress is done
        self._act_ns = 0  # when the accepted trigger takes effect (the trigger delay's end)
        if self.owner is not None:
            self.owner.cancel_pending(self.uid)

    # ---- the state ---------------------------------------------------------------------------------

    @property
    def armed(self) -> bool:
        """Initiated (armed, ready or firing): TRAN-active until the action was taken."""
        return self._trig_state != "idle"

    @property
    def ready(self) -> bool:
        """Armed and waiting for a trigger: WTG-tran."""
        return self._trig_state == "ready"

    @property
    def delaying(self) -> bool:
        """A trigger was accepted and its action is still on the way."""
        return self._trig_state == "firing"

    def _source(self) -> str:
        return str(self.param("source")).lower()

    # ---- the machine ---------------------------------------------------------------------------------

    def arm(self, *, why: str = "Init ignored") -> None:
        """INITiate: arm it. An arm while armed is refused with ``ignored_error`` (0: silently,
        nothing restarts); ``_on_arm`` then clears the carrier's run state (and may refuse the
        run's shape: the state is only set once it stands)."""
        if self._trig_state != "idle":
            code = int(self.param("ignored_error") or 0)
            if code:
                raise ScpiError(code, why)
            return
        self._on_arm()
        self._trig_state = "armed"
        ready = float(self.param("ready_delay") or 0.0)
        assert self.owner is not None
        if ready > 0:
            self.owner.register_pending(
                self.uid, self.owner.now_ns() + round(ready * 1e9), self._became_ready
            )
        else:
            self._became_ready()

    def _became_ready(self) -> None:
        """The ready delay is over (or there was none): a trigger may come. An immediate source
        fires without one."""
        self._trig_state = "ready"
        if self._source().startswith("imm"):
            self.fire()

    def trigger_in(self, source: str, *, why: str = "Trigger ignored") -> None:
        """A trigger from ``source`` (``*TRG`` is "bus"): accepted only while ready for one from
        there, else ``trigger_ignored_error`` (0: ignored silently)."""
        if self._trig_state != "ready" or self._source() != source.lower():
            self._ignored(why)
            return
        self._on_trigger()
        self.fire()

    def trigger_now(self, *, why: str = "Trigger ignored") -> None:
        """The model's own trigger command: accepted while ready, whatever the source."""
        if self._trig_state != "ready":
            self._ignored(why)
            return
        self._on_trigger()
        self.fire()

    def _ignored(self, why: str) -> None:
        code = int(self.param("trigger_ignored_error") or 0)
        if code:
            raise ScpiError(code, why)

    def fire(self) -> None:
        """A trigger accepted: the firing runs to ``_fire_span()``'s end — at once when the
        action is instantaneous and there is no trigger delay — and settles then."""
        assert self.owner is not None
        self._trig_state = "firing"
        delay = float(self.param("delay") or 0.0)
        self._act_ns = self.owner.now_ns() + (round(delay * 1e9) if delay > 0 else 0)
        end = self._fire_span()
        if end is None:
            end = self._act_ns
        if self.owner.session.clock_mode == "stepped":
            # A stepped clock moves only when stepped, so nothing would ever complete a firing
            # that itself takes virtual time: that span is instantaneous (its spacing is what it
            # reports). The trigger delay is timer semantics, not the action's cost — it still
            # stands, like the ready delay does, and ``advance`` runs it (issue 659).
            end = min(end, self._act_ns)
        self._fire_end_ns = end
        if end > self.owner.now_ns():
            self.owner.register_pending(self.uid, end, self._fire_step)
        else:
            self._fire_step()

    def _fire_step(self) -> None:
        """The firing is done — the pending table ran it, or ``wait_fired`` by hand: the action,
        then the settle."""
        self._act()
        self._collect()

    def _collect(self) -> None:
        """The firing is over: settle — idle, or ready for the run's next trigger."""
        assert self.owner is not None
        self.owner.cancel_pending(self.uid)
        self._fire_end_ns = None
        self._trig_state = self._next_after_act()
        self._on_settled()

    def wait_fired(self) -> int:
        """Drive the firing in progress to its end: the virtual time that takes (see
        :meth:`~simubench.engine.instrument.Instrument.wait_until`), running the action when due."""
        assert self.owner is not None
        if self._trig_state != "firing" or self._fire_end_ns is None:
            return 0
        end = self._fire_end_ns
        used = self.owner.wait_until(end)
        if self.owner.now_ns() + used >= end:
            self._fire_step()
        return used

    def abort(self) -> None:
        """ABORt: back to idle. A pending ready or firing is dropped; what the action already did
        stays."""
        assert self.owner is not None
        self.owner.cancel_pending(self.uid)
        self._fire_end_ns = None
        self._trig_state = "idle"
        self._on_settled()

    # ---- the carrier's increments --------------------------------------------------------------------

    def _on_arm(self) -> None:
        """What arming does beyond the state (clearing the previous run's readings); nothing by
        default."""

    def _on_trigger(self) -> None:
        """What an accepted trigger does beyond firing (the bus trigger's TRIG event); nothing by
        default."""

    def _fire_span(self) -> int | None:
        """A trigger was accepted (at ``_act_ns``, the trigger delay's end): when the firing is
        done — None for a firing that ends there (an instantaneous action). What preparing the
        action takes (the sampler's ``run_begin``) happens here, at the trigger."""
        return None

    def _act(self) -> None:
        """The action a trigger takes, run when the firing is done."""
        raise NotImplementedError(f"{self.uid}: a triggerable component takes no action")

    def _next_after_act(self) -> str:
        """Where a firing settles: ``idle`` (default) or ``ready`` (the run's next trigger)."""
        return "idle"

    def _on_settled(self) -> None:
        """What settling does beyond the state (a finished run's ``run_done``); nothing by
        default."""


@component("trigger_model")
class TriggerModel(Triggerable, Component):
    """INITiate, triggers and FETCh? of a meter: ``trigger_count`` × ``sample_count`` readings of the
    ``sampler`` component, kept in ``buffer`` when one is named.

    The arm → ready → fire → abort machine is the ``Triggerable`` primitive's; what a trigger
    *does* here is the run: its readings (each batch after the trigger delay the primitive
    consumed), kept for FETCh? and in the reading buffer.
    """

    defaults = {
        "sampler": "",  # the component that takes the readings (a Sampler: a meter's core, an SMU)
        "source": "immediate",  # immediate | bus (*TRG) | external; map the model's names to these
        "trigger_count": 1,  # triggers in one run
        "sample_count": 1,  # readings per trigger
        "delay": 0.0,  # s, from each trigger to its first reading
        "buffer": "",  # the reading_buffer component that keeps the readings ('' for none)
        "digits": None,  # readings with a fixed number of digits (None: the definition's format)
        "fetch_from_buffer": False,  # FETCh? answers from the reading buffer, not the last run
        "max_readings": 1000000,  # trigger_count × sample_count above this is refused
        "waiting_error": -230,  # a fetch with no readings to give (waiting for a trigger, none taken)
        "ignored_error": -213,  # INITiate while a run is in progress (SCPI: Init ignored)
        "deadlock_error": -214,  # READ? with a bus or external trigger (SCPI: Trigger deadlock)
        "trigger_ignored_error": -211,  # a bus trigger while not waiting for one (SCPI: Trigger ignored)
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reset()

    def reset(self) -> None:
        self.taken: list[Reading] = []  # the readings of the last run, what FETCh? returns
        self.triggers = 0  # triggers taken in this run
        self._run_begun = False  # run_begin was called and run_done is still owed
        self._batches = 0  # batches the firing in progress takes
        self._fire_batches = 1  # batches one accepted trigger takes (an immediate run: all of them)
        self._init_trigger()

    @property
    def phase(self) -> str:
        """The run's phase: idle | waiting (for a trigger) | measuring (readings in progress)."""
        return {"idle": "idle", "armed": "waiting", "ready": "waiting", "firing": "measuring"}[
            self._trig_state
        ]

    # ---- the run -----------------------------------------------------------------------------------

    def _sampler(self) -> Sampler:
        assert self.owner is not None
        comp = self.peer(str(self.param("sampler")))
        if not isinstance(comp, Sampler):
            raise ScpiError(-300, f"{self.uid}: sampler {self.param('sampler')!r} takes no readings")
        return comp

    def _optional(self, name: str, *args: Any) -> Any:
        """Call an optional sampler hook (``run_samples``, ``run_begin``, ``run_done``,
        ``sample_fields``); ``None`` when the sampler does not have it."""
        hook = getattr(self._sampler(), name, None)
        return hook(*args) if hook is not None else None

    def _counts(self) -> tuple[int, int]:
        """(triggers, readings per trigger) of a run: the sampler's own size (a sweep) when it
        declares one, else the configured counts."""
        declared = self._optional("run_samples")
        if declared is not None:
            n = int(declared)
            if n < 1 or n > int(self.param("max_readings")):
                raise ScpiError(-222, f"{n} readings is out of range")
            per = self._optional("samples_per_trigger")
            if per is not None and 0 < int(per) < n and n % int(per) == 0:
                return n // int(per), int(per)
            return 1, n
        triggers, samples = int(float(self.param("trigger_count"))), int(float(self.param("sample_count")))
        if triggers < 1 or samples < 1 or triggers * samples > int(self.param("max_readings")):
            raise ScpiError(-222, f"{triggers} triggers × {samples} readings is out of range")
        return triggers, samples

    def _batch_ns(self, samples: int) -> int:
        return round(float(self.param("delay")) * 1e9) + samples * self._sampler().sample_time_ns()

    def _buffer(self) -> ReadingBuffer | None:
        name = str(self.param("buffer") or "")
        if not name:
            return None
        assert self.owner is not None
        comp = self.peer(name)
        if not isinstance(comp, ReadingBuffer):
            raise ScpiError(-300, f"{self.uid}: buffer {name!r} is not a reading_buffer")
        return comp

    def _number(self) -> Any:
        return _number_formatter(self.owner, self.param("digits"))

    def _buffer_readings(self) -> list[Any]:
        """The readings of the configured reading buffer (``fetch_from_buffer``, #382)."""
        buffer = self._buffer()
        if buffer is None:
            raise ScpiError(-300, "no reading buffer is configured")
        return buffer.buffers[buffer._default()]

    def _reply(self, elements: list[str] | None = None) -> bytes:
        readings = self._buffer_readings() if self.param("fetch_from_buffer") else self.taken
        return format_readings(readings, elements, self._number())

    # ---- the triggerable's increments ----------------------------------------------------------------

    def _on_arm(self) -> None:
        """A run starts: the previous run's readings go (the buffer's too); an immediate run takes
        all its triggers at once, a triggered one per trigger."""
        triggers, _ = self._counts()
        self.taken, self.triggers = [], 0
        self._fire_batches = triggers if self._source().startswith("imm") else 1
        buffer = self._buffer()
        if buffer is not None:
            buffer.clear_buffer()

    def _on_trigger(self) -> None:
        assert self.owner is not None
        self.owner.status_event("TRIG")  # a trigger was received (the SRS LIA bit 6, #273)

    def _fire_span(self) -> int | None:
        """One accepted trigger: its readings run from the trigger the primitive took (each batch
        one ``delay`` after the previous) and are done at the time returned. The sampler's
        ``run_begin`` happens here, at the trigger."""
        _, samples = self._counts()
        if not self._run_begun:
            self._run_begun = True
            self._optional("run_begin")
        self._batches = self._fire_batches
        return (
            self._act_ns
            + (self._batches - 1) * self._batch_ns(samples)
            + samples * self._sampler().sample_time_ns()
        )

    def _act(self) -> None:
        """The readings' time is over: take them at their own instants."""
        _, samples = self._counts()
        sampler = self._sampler()
        delay = round(float(self.param("delay")) * 1e9)
        step = sampler.sample_time_ns()
        t = self._act_ns
        buffer = self._buffer()
        for batch in range(self._batches):
            if batch:
                t += delay  # each further trigger's own delay
            for _ in range(samples):
                t += step
                index = len(self.taken)
                value = float(sampler.sample(index))
                fields = self._optional("sample_fields", index) or {}
                reading = Reading(value, t, fields)
                self.taken.append(reading)
                if buffer is not None:
                    buffer.append(reading)
        self.triggers += self._batches

    def _next_after_act(self) -> str:
        """A run with triggers left waits for its next one; a finished one is done."""
        if self.triggers < self._counts()[0]:
            return "ready"
        return "idle"

    def _on_settled(self) -> None:
        """The run is over (idle): the sampler's run_done is owed."""
        if self._trig_state == "idle" and self._run_begun:
            self._run_begun = False
            self._optional("run_done")

    # ---- operations ----------------------------------------------------------------------------------

    @op("initiate")
    def initiate(self, **_: Any) -> OpResult:
        """``INITiate``: start a run. With an immediate trigger the readings start now; otherwise the
        model waits for its triggers. Readings of the previous run are cleared."""
        self.arm()
        return OpResult()

    @op("bus_trigger")
    def bus_trigger(self, **_: Any) -> OpResult:
        """``*TRG`` (and the model's bus trigger command): one trigger, when waiting for a bus one."""
        self.trigger_in("bus")
        return OpResult()

    def status_flags(self) -> dict[str, bool]:
        """SCN while no scan is in progress (the SRS serial poll bit 0, #273)."""
        return {"SCN": self._trig_state == "idle"}

    @op("fetch")
    def fetch(self, elements: list[bytes] | None = None, **_: Any) -> OpResult:
        """``FETCh?``: the readings of the last run, once they are taken (waiting for them). With
        ``fetch_from_buffer`` the reading buffer answers instead (an SMU's ``FETCh?`` is its trace
        memory: ``TRAC:CLE`` empties it), each reading with the ``elements`` named."""
        names = [V.parse_string(e) if isinstance(e, bytes) else str(e) for e in elements or []]
        used = self.wait_fired()
        if self.param("fetch_from_buffer"):
            buffer = self._buffer()
            if buffer is None:  # fetch_from_buffer on but no buffer configured (#382 style guard)
                raise ScpiError(-300, "no reading buffer is configured")
            if not buffer.buffers[buffer._default()]:
                raise ScpiError(int(buffer.param("empty_error")), "the buffer is empty")
        elif self.phase == "measuring" or not self.taken or (self.phase == "waiting" and not self.triggers):
            raise ScpiError(int(self.param("waiting_error")), "Data stale; no readings to fetch")
        return OpResult(self._reply(names or None), used)

    @op("read")
    def read(self, elements: list[bytes] | None = None, **_: Any) -> OpResult:
        """``READ?``: INITiate and FETCh?; with a bus or external trigger nothing would ever arrive."""
        if self._source() != "immediate":
            raise ScpiError(int(self.param("deadlock_error")), "Trigger deadlock")
        self.abort()
        self.initiate()
        return self.fetch(elements=elements)

    @op("abort")
    def op_abort(self, **_: Any) -> OpResult:
        """``ABORt``: stop the run; readings already taken stay."""
        Triggerable.abort(self)
        return OpResult()

    @op("state")
    def state(self, query: bool = False, **_: Any) -> OpResult:
        """The trigger model's phase: IDLE, WAITING or MEASURING (for a status query)."""
        return OpResult(self.phase.upper().encode())


@component("reading_buffer")
class ReadingBuffer(Component):
    """Named reading buffers: a DMM's reading memory, an SMU's ``defbuffer1`` and the ones a script
    makes. Commands name a buffer with ``name``; without one they use ``default``."""

    defaults = {
        "default": "defbuffer1",  # the buffer commands use when they name none
        "capacity": 10000,  # readings a buffer keeps (a made buffer gives its own)
        "when_full": "stop",  # stop: new readings are dropped | overwrite: the oldest go
        "digits": None,  # readings with a fixed number of digits (None: the definition's format)
        "max_buffers": 8,  # buffers there may be, the default one included
        "missing_error": -222,  # a buffer name, index or count that does not exist
        "empty_error": -230,  # reading from an empty buffer
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reset()

    def reset(self) -> None:
        self.buffers: dict[str, list[Reading]] = {self._default(): []}
        self.capacities: dict[str, int] = {}

    def _default(self) -> str:
        return str(self.param("default"))

    def _key(self, name: str | None) -> str:
        key = self._default() if not name else name.strip().strip('"').strip("'")
        if key not in self.buffers:
            raise ScpiError(int(self.param("missing_error")), f"no reading buffer {key!r}")
        return key

    def _capacity(self, key: str) -> int:
        return self.capacities.get(key, int(float(self.param("capacity"))))

    # ---- from the trigger model ----------------------------------------------------------------------

    def append(self, reading: Reading, name: str | None = None) -> None:
        buf = self.buffers[self._key(name)]
        if len(buf) >= self._capacity(self._key(name)):
            if self.param("when_full") != "overwrite":
                return
            buf.pop(0)
        buf.append(reading)

    def clear_buffer(self, name: str | None = None) -> None:
        self.buffers[self._key(name)].clear()

    def stored(self, name: str | None = None) -> list[Reading]:
        """The readings a buffer holds, oldest first (a copy): what a data file writes (#1071)."""
        return list(self.buffers[self._key(name)])

    def number_text(self, value: float) -> str:
        """One reading written as the buffer's queries write it (FETCh?, DATA:REMove?)."""
        assert self.owner is not None
        return (
            self.owner.format_number(float(value)).decode()
            if self.param("digits") is None
            else (V.format_float(float(value), int(self.param("digits"))).decode())
        )

    # ---- operations ----------------------------------------------------------------------------------

    def _numbers(self, readings: list[Reading], elements: list[str] | None = None) -> bytes:
        return format_readings(
            readings,
            elements,
            _number_formatter(self.owner, self.param("digits")),
            int(self.param("missing_error")),
        )

    @op("points")
    def points(self, name: str | None = None, **_: Any) -> OpResult:
        """``DATA:POINts?``: readings in the buffer."""
        return OpResult(V.format_int(len(self.buffers[self._key(name)])))

    @op("actual")
    def actual(self, name: str | None = None, **_: Any) -> OpResult:
        """``TRACe:ACTual?``: readings in the buffer (an SMU's name for ``points``)."""
        return self.points(name)

    @op("remove")
    def remove(self, count: int = 1, name: str | None = None, **_: Any) -> OpResult:
        """``DATA:REMove? n``: the n oldest readings, taken out of the buffer."""
        buf = self.buffers[self._key(name)]
        n = int(count)
        if n < 1 or n > len(buf):
            raise ScpiError(int(self.param("missing_error")), f"{n} readings asked, {len(buf)} stored")
        taken, buf[:] = buf[:n], buf[n:]
        return OpResult(self._numbers(taken))

    @op("read_clear")
    def read_clear(self, count: int | None = None, name: str | None = None, **_: Any) -> OpResult:
        """``R? [max]``: the oldest readings (all, or at most ``count``) as an IEEE 488.2 definite-length
        block of comma-separated numbers, taken out of the buffer."""
        buf = self.buffers[self._key(name)]
        n = len(buf) if count is None else min(int(count), len(buf))
        taken, buf[:] = buf[:n], buf[n:]
        return OpResult(V.format_block(self._numbers(taken)))

    @op("data")
    def data(
        self,
        start: int = 1,
        end: int | None = None,
        name: str | None = None,
        elements: list[bytes] | None = None,
        **_: Any,
    ) -> OpResult:
        """``TRACe:DATA? start,end,"buffer",READ,REL``: readings start to end (from 1), each with the
        elements asked for (READ: the reading, REL: seconds since the first, or a sampler field)."""
        buf = self.buffers[self._key(name)]
        if not buf:
            raise ScpiError(int(self.param("empty_error")), "the buffer is empty")
        last = len(buf) if end is None else int(end)
        if not 1 <= int(start) <= last <= len(buf):
            raise ScpiError(int(self.param("missing_error")), f"readings {start} to {last} of {len(buf)}")
        names = [V.parse_string(e) if isinstance(e, bytes) else str(e) for e in elements or []]
        return OpResult(self._numbers(buf[int(start) - 1 : last], names or None))

    @op("clear")
    def clear(self, name: str | None = None, **_: Any) -> OpResult:
        """``TRACe:CLEar``: empty the buffer."""
        self.clear_buffer(name)
        return OpResult()

    @op("make")
    def make(self, name: str = "", capacity: int | None = None, **_: Any) -> OpResult:
        """``TRACe:MAKE "name",n``: a new buffer of ``n`` readings."""
        key = name.strip().strip('"').strip("'")
        if not key or key in self.buffers or len(self.buffers) >= int(self.param("max_buffers")):
            raise ScpiError(int(self.param("missing_error")), f"cannot make reading buffer {key!r}")
        self.buffers[key] = []
        if capacity is not None:
            if int(capacity) < 1:
                raise ScpiError(int(self.param("missing_error")), "a buffer holds at least one reading")
            self.capacities[key] = int(capacity)
        return OpResult()

    @op("delete")
    def delete(self, name: str = "", **_: Any) -> OpResult:
        """``TRACe:DELete "name"``: remove a buffer that was made (the default one stays)."""
        key = self._key(name)
        if key == self._default():
            raise ScpiError(int(self.param("missing_error")), "the default buffer cannot be deleted")
        del self.buffers[key]
        self.capacities.pop(key, None)
        return OpResult()
