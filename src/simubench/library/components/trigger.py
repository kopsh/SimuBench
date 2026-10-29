"""The trigger model and reading memory of meters and source-measure units (metrology-authoring.md 3).

``trigger_model`` runs the SCPI trigger system on a :class:`~simubench.engine.component.Sampler`:
INITiate arms it, each trigger (immediate, ``*TRG`` on the bus, or an external one) takes
``sample_count`` readings after ``delay``, ``trigger_count`` triggers make one run. The readings
take virtual time (the sampler's reading time, D12): on the command clock INITiate returns at once
and FETCh? waits, using that time; ``*OPC?`` and ``*WAI`` wait for the run too.

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


@component("trigger_model")
class TriggerModel(Component):
    """INITiate, triggers and FETCh? of a meter: ``trigger_count`` × ``sample_count`` readings of the
    ``sampler`` component, kept in ``buffer`` when one is named."""

    defaults = {
        "sampler": "",  # the component that takes the readings (a Sampler: a meter's core, an SMU)
        "source": "immediate",  # immediate | bus (*TRG) | external; map the model's names to these
        "trigger_count": 1,  # triggers in one run
        "sample_count": 1,  # readings per trigger
        "delay": 0.0,  # s, from each trigger to its first reading
        "buffer": "",  # the reading_buffer component that keeps the readings ('' for none)
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
        self.phase = "idle"  # idle | waiting (for a trigger) | measuring
        self.readings: list[Reading] = []  # the readings of the last run, what FETCh? returns
        self.triggers = 0  # triggers taken in this run
        self.done_ns = 0  # when the readings being taken are done
        if self.owner is not None:
            self.owner.cancel_pending(self.uid)

    # ---- the run -----------------------------------------------------------------------------------

    def _sampler(self) -> Sampler:
        assert self.owner is not None
        comp = self.owner.components.get(str(self.param("sampler")))
        if not isinstance(comp, Sampler):
            raise ScpiError(-300, f"{self.uid}: sampler {self.param('sampler')!r} takes no readings")
        return comp

    def _source(self) -> str:
        return str(self.param("source")).lower()

    def _counts(self) -> tuple[int, int]:
        triggers, samples = int(float(self.param("trigger_count"))), int(float(self.param("sample_count")))
        if triggers < 1 or samples < 1 or triggers * samples > int(self.param("max_readings")):
            raise ScpiError(-222, f"{triggers} triggers × {samples} readings is out of range")
        return triggers, samples

    def _batch_ns(self, samples: int) -> int:
        return round(float(self.param("delay")) * 1e9) + samples * self._sampler().sample_time_ns()

    def _start(self, batches: int) -> None:
        """Take ``batches`` triggers' readings from now; they are done at ``done_ns``."""
        assert self.owner is not None
        _, samples = self._counts()
        self.phase = "measuring"
        self.done_ns = self.owner.now_ns() + batches * self._batch_ns(samples)
        self._batches = batches
        self.owner.register_pending(self.uid, self.done_ns, self._finish)

    def _finish(self) -> None:
        """The readings of the batches started last, taken at their own instants."""
        assert self.owner is not None
        triggers, samples = self._counts()
        sampler = self._sampler()
        delay = round(float(self.param("delay")) * 1e9)
        step = sampler.sample_time_ns()
        t = self.done_ns - self._batches * self._batch_ns(samples)
        buffer = self._buffer()
        for _ in range(self._batches):
            t += delay
            for _ in range(samples):
                t += step
                reading = Reading(float(sampler.sample(len(self.readings))), t)
                self.readings.append(reading)
                if buffer is not None:
                    buffer.append(reading)
        self.triggers += self._batches
        self.phase = "waiting" if self.triggers < triggers else "idle"
        self.owner.cancel_pending(self.uid)

    def _buffer(self) -> ReadingBuffer | None:
        name = str(self.param("buffer") or "")
        if not name:
            return None
        assert self.owner is not None
        comp = self.owner.components.get(name)
        if not isinstance(comp, ReadingBuffer):
            raise ScpiError(-300, f"{self.uid}: buffer {name!r} is not a reading_buffer")
        return comp

    def _wait(self) -> int:
        """Finish the readings in progress: the virtual time that takes (see Instrument.wait_until)."""
        assert self.owner is not None
        if self.phase != "measuring":
            return 0
        used = self.owner.wait_until(self.done_ns)
        if self.owner.now_ns() + used >= self.done_ns:
            self._finish()
        return used

    def _reply(self) -> bytes:
        assert self.owner is not None
        return b",".join(self.owner.format_number(r.value) for r in self.readings)

    # ---- operations ----------------------------------------------------------------------------------

    @op("initiate")
    def initiate(self, **_: Any) -> OpResult:
        """``INITiate``: start a run. With an immediate trigger the readings start now; otherwise the
        model waits for its triggers. Readings of the previous run are cleared."""
        if self.phase != "idle":
            raise ScpiError(int(self.param("ignored_error")), "Init ignored")
        triggers, _ = self._counts()
        self.readings, self.triggers = [], 0
        buffer = self._buffer()
        if buffer is not None:
            buffer.clear_buffer()
        if self._source() == "immediate":
            self._start(triggers)
        else:
            self.phase = "waiting"
        return OpResult()

    @op("bus_trigger")
    def bus_trigger(self, **_: Any) -> OpResult:
        """``*TRG`` (and the model's bus trigger command): one trigger, when waiting for a bus one."""
        if self.phase != "waiting" or self._source() != "bus":
            raise ScpiError(int(self.param("trigger_ignored_error")), "Trigger ignored")
        self._start(1)
        return OpResult()

    @op("fetch")
    def fetch(self, **_: Any) -> OpResult:
        """``FETCh?``: the readings of the last run, once they are taken (waiting for them)."""
        used = self._wait()
        if self.phase == "measuring" or not self.readings or (self.phase == "waiting" and not self.triggers):
            raise ScpiError(int(self.param("waiting_error")), "Data stale; no readings to fetch")
        return OpResult(self._reply(), used)

    @op("read")
    def read(self, **_: Any) -> OpResult:
        """``READ?``: INITiate and FETCh?; with a bus or external trigger nothing would ever arrive."""
        if self._source() != "immediate":
            raise ScpiError(int(self.param("deadlock_error")), "Trigger deadlock")
        self.abort()
        self.initiate()
        used = self._wait()
        return OpResult(self._reply(), used)

    @op("abort")
    def abort(self, **_: Any) -> OpResult:
        """``ABORt``: stop the run; readings already taken stay."""
        assert self.owner is not None
        self.owner.cancel_pending(self.uid)
        self.phase = "idle"
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

    # ---- operations ----------------------------------------------------------------------------------

    def _numbers(self, readings: list[Reading], elements: list[str] | None = None) -> bytes:
        assert self.owner is not None
        out: list[bytes] = []
        first = readings[0].t_ns if readings else 0
        for r in readings:
            for e in [x.upper() for x in elements or ["READ"]]:
                if e.startswith("READ"):
                    out.append(self.owner.format_number(r.value))
                elif e.startswith("REL"):
                    out.append(self.owner.format_number((r.t_ns - first) / 1e9))
                elif e in {k.upper() for k in r.fields}:
                    out.append(
                        self.owner.format_number(next(v for k, v in r.fields.items() if k.upper() == e))
                    )
                else:
                    raise ScpiError(int(self.param("missing_error")), f"no buffer element {e!r}")
        return b",".join(out)

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
