"""LeCroy-style oscilloscope dialect: short headers, key-value parameters and text waveform readback.

Teledyne LeCroy's remote commands are not SCPI trees. Headers are short words (``TDIV``,
``C1:VDIV``, ``TRSE``), some parameters are key-value lists (``TRSE EDGE,SR,C1``,
``WAVEFORM_SETUP SP,0,NP,0,FP,0,SN,0``) and replies repeat the header and carry the unit
(``C1:VDIV 200E-3 V``) unless ``COMM_HEADER OFF``. These components implement the subset a host
calibration program uses (docs/lowfreq-cal.md); the physics is the shared oscilloscope engine:

* ``lecroy_scope``: the commands the definition cannot declare (settings, ``TRSE``, ``MSIZ`` and
  ``WAVEFORM_SETUP`` are declared with ``dialect``, reply templates and ``pairs`` parameters);
* ``lecroy_acquisition``: timebase, edge trigger, run control (``TRMD``) and waveform transfer,
  including the text readback ``C<n>:INSPECT? "DATA_ARRAY_1",FLOAT``: blank-separated numbers
  between the header and quotes, ``C1:INSP " 1.000000e-03 ... "``, in pieces chosen by
  ``WAVEFORM_SETUP`` (first point, number of points, sparsing);
* ``lecroy_input`` / ``lecroy_frontend``: the coupling (``A1M``, ``D1M``, ``D50``, ``GND``) as
  input impedance and front-end filter.

What is simplified, and said so in docs/lowfreq-cal.md: the record has exactly ``MSIZ`` points over
ten divisions (the sample rate is ``MSIZ / (10 TDIV)`` up to ``max_rate``, not snapped to the
instrument's rate table), so any point count reads back whole; segments (``SN``) are not modeled.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, component, op
from ...engine.noise import gaussian, stream_key
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .electrical import InputPort
from .scope import DIVS_H, Record, ScopeAcquisition, ScopeFrontend, _measure, channel_index

#: Vertical divisions on screen; the ADC reaches a little beyond (``adc_divs``).
DIVS_V = 8
#: PAVA? measurements by LeCroy name: the engine's measurement and the unit of the reply.
MEASUREMENTS = {
    "AMPL": ("VAMPlitude", "V"),
    "PKPK": ("VPP", "V"),
    "MAX": ("VMAX", "V"),
    "MIN": ("VMIN", "V"),
    "MEAN": ("VAVerage", "V"),
    "RMS": ("VRMS", "V"),
    "FREQ": ("FREQuency", "HZ"),
    "PER": ("PERiod", "S"),
}
#: Trigger modes as TRMD? writes them.
TRMD_TEXT = {"AUTO": "AUTO", "NORMal": "NORM", "SINGLE": "SINGLE", "STOP": "STOP"}


class _Dialect(Component):
    """Shared helpers: state access and the header that starts every reply."""

    def _st(self, path: str) -> Any:
        assert self.owner is not None
        return self.owner.get_state(path)

    def _set(self, path: str, value: Any) -> None:
        assert self.owner is not None
        self.owner.set_state(path, value)

    def reply(self, header: str, body: str, channel: int | None = None, long: str | None = None) -> bytes:
        """``C1:VDIV 200E-3 V``; with ``COMM_HEADER LONG`` the long header, with ``OFF`` the body alone."""
        mode = str(self._st("comm.header"))
        if mode == "OFF":
            return body.encode()
        name = long if mode == "LONG" and long else header
        prefix = f"C{channel}:" if channel is not None else ""
        return f"{prefix}{name} {body}".encode()


@component("lecroy_scope")
class LecroyScope(_Dialect):
    """What the definition cannot declare: PACU (a slot number that picks the setting), *CLS?, presets.

    Settings, TRSE, MSIZ and WAVEFORM_SETUP are declared in the definition (dialect, reply templates
    and ``pairs`` parameters, instrument-authoring.md 2)."""

    @op("param_custom")
    def param_custom(self, query: bool = False, params: list[bytes] | None = None, **_: Any) -> OpResult:
        """``PACU 1,AMPL,C1``: what measurement slot P1 measures (kept; read with ``C<n>:PAVA?``)."""
        items = [V.parse_string(p).strip().upper() for p in params or []]
        if not items:
            raise ScpiError(-109)
        try:
            slot = int(items[0])
        except ValueError:
            raise ScpiError(-224, f"measurement slot {items[0]!r}") from None
        if not 1 <= slot <= 8:
            raise ScpiError(-222, "measurement slots are 1..8")
        path = f"measure.p{slot}"
        if query:
            text = str(self._st(path)) or "NULL"
            return OpResult(self.reply("PACU", f"{slot},{text}", long="PARAMETER_CUSTOM"))
        if len(items) < 2:
            raise ScpiError(-109)
        if items[1] not in MEASUREMENTS and items[1] != "NULL":
            raise ScpiError(-224, f"measurement {items[1]} not modeled by SimuBench")
        self._set(path, ",".join(items[1:]))
        return OpResult()

    @op("preset")
    def preset(self, **_: Any) -> OpResult:
        """``SYSTem:PRESet``: the same default setup as ``*RST``."""
        assert self.owner is not None
        self.owner.reset()
        return OpResult()

    @op("cls")
    def cls(self, query: bool = False, **_: Any) -> OpResult:
        """``*CLS`` clears the error queue and event status; ``*CLS?`` does the same and answers ``0``."""
        assert self.owner is not None
        self.owner.errors.clear()
        self.owner.esr = 0
        return OpResult(b"0" if query else None)


@component("lecroy_acquisition")
class LecroyAcquisition(ScopeAcquisition, _Dialect):
    """Run control, record length, noise and the ADC, waveform readback and PAVA? measurements.

    Run control follows ``TRMD``: AUTO and NORM acquire anew whenever a readback starts (``FP 0``)
    and keep that record for the following pieces, so pieces read one after another join into one
    record; SINGLE acquires once on the next trigger and then shows ``STOP``; STOP keeps the record.
    """

    defaults = {
        **ScopeAcquisition.defaults,
        "max_rate": 2.0e10,
        "search_windows": 2,
        "noise_rms": 0.0,  # input-referred white noise, V rms; the definition sets it
        "adc_bits": 12,
        "adc_divs": 10.0,  # the ADC spans +-5 div around the screen centre
        "trigger_jitter_s": 0.0,
        "transfer": "xfer",  # the waveform_transfer component that encodes DATA_ARRAY_1
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.armed = False

    def reset(self) -> None:
        super().reset()
        self.armed = False
        self.acq_count = 0

    # ---- timing and sampling ----------------------------------------------------------------------

    def sample_rate(self) -> float:
        window = DIVS_H * float(self._st("timebase.scale"))
        return min(float(self.param("max_rate")), int(self._st("memory.size")) / window)

    def _timing(self) -> tuple[int, float, int, int]:
        window = DIVS_H * float(self._st("timebase.scale"))
        points = max(2, round(self.sample_rate() * window))
        dt = window / points
        pre = points // 2  # trigger delay 0: the trigger sits at the centre of the screen
        return points, dt, pre, points - pre

    def trigger_level(self) -> float:
        src = str(self._st("trigger.source"))
        return float(self._st(f"ch{channel_index(src)}.trigger_level")) if src.startswith("C") else 0.0

    def _search(self, sig: Any, t0: int, dt: float, pre: int, points: int) -> tuple[int, bool]:
        """First edge of the trigger slope at or after ``pre`` samples (history before the trigger)."""
        n = channel_index(self._st("trigger.source"))
        level = self.trigger_level()
        rising = self._st(f"ch{n}.trigger_slope") == "POSitive"
        hyst = 0.02 * DIVS_V * float(self._st(f"ch{n}.scale"))
        span = max(points * int(self.param("search_windows")), pre + 2 * points)
        idx = np.arange(span)
        v = sig.sample_rel(t0, idx * dt)
        armed_side, crossed = (v < level - hyst, v >= level) if rising else (v > level + hyst, v <= level)
        armed = np.nonzero(armed_side & (idx >= pre - 1))[0]
        if armed.size:
            after = np.nonzero(crossed & (idx > armed[0]))[0]
            if after.size:
                return int(after[0]), True
        return pre, False

    def compute(self, channels: list[int]) -> tuple[dict[int, Record], int, bool]:
        points, dt, pre, post = self._timing()
        t0 = self.session.now_ns
        src = str(self._st("trigger.source"))
        if src.startswith("C"):
            k, triggered = self._search(self._display_signal(channel_index(src)), t0, dt, pre, points)
        else:  # EX, EX10, LINE: nothing modeled drives them
            k, triggered = pre, False
        start = k - pre
        t_start_ns = t0 + round(start * dt * 1e9)
        jitter = float(self.param("trigger_jitter_s"))
        if jitter:
            key = stream_key(self.session.seed, self.uid, "jitter", self.acq_count)
            jitter *= float(gaussian(key, np.zeros(1))[0])
        offsets = (start + np.arange(points)) * dt + jitter
        records = {}
        for n in channels:
            scale, offset = float(self._st(f"ch{n}.scale")), float(self._st(f"ch{n}.offset"))
            volts = self._display_signal(n).sample_rel(t0, offsets) + self._noise(n, points)
            records[n] = Record(
                n, t_start_ns, dt, pre, self._adc(volts, scale, offset), scale, offset, triggered
            )
        used = (k + post) * dt + float(self.param("overhead_s"))
        return records, round(used * 1e9), triggered

    def _noise(self, n: int, points: int) -> np.ndarray:
        """White input noise in display volts; a pure function of the seed, channel and acquisition.

        Keyed by the acquisition count rather than by virtual time, so a script that pauses for a
        different real time between commands still reads the same numbers for the same seed.
        """
        sigma = float(self.param("noise_rms")) * float(self._st(f"ch{n}.probe"))
        if sigma <= 0:
            return np.zeros(points)
        key = stream_key(self.session.seed, self.uid, "noise", n, self.acq_count)
        return sigma * gaussian(key, np.arange(points))

    def _adc(self, volts: np.ndarray, scale: float, offset: float) -> np.ndarray:
        """What the ADC keeps: ``adc_bits`` over ``adc_divs`` divisions around the screen centre."""
        divs = float(self.param("adc_divs"))
        per_div = (1 << int(self.param("adc_bits"))) / divs
        half = divs / 2 * per_div
        codes = np.clip(np.round((volts + offset) / scale * per_div), -half, half - 1)
        return codes / per_div * scale - offset

    # ---- run control ------------------------------------------------------------------------------

    def _store(self, records: dict[int, Record], used: int, forced: bool = False) -> int:
        self.records.update(records)
        self.acq_count += 1
        self.session.invalidate()
        assert self.owner is not None
        self.session.instrument_event(
            self.owner,
            "acquisition",
            {
                "count": self.acq_count,
                "forced": forced,
                "channels": {
                    str(ch): {
                        "t_start_ns": r.t_start_ns,
                        "dt_s": r.dt_s,
                        "points": int(r.volts.size),
                        "trigger_index": r.trigger_index,
                        "triggered": r.triggered,
                    }
                    for ch, r in sorted(records.items())
                },
                "seed": self.session.seed,
            },
        )
        return used

    def _channels_for(self, channel: int | None) -> list[int]:
        chans = set(self.displayed())
        if channel is not None:
            chans.add(channel)
        return sorted(chans)

    def acquire_now(self, channel: int | None = None) -> int:
        """One acquisition attempt as the trigger mode allows it. Returns the virtual time used."""
        mode = self._st("trigger.mode")
        if mode == "STOP":
            return 0
        records, used, triggered = self.compute(self._channels_for(channel))
        if mode == "AUTO" or triggered:
            self._store(records, used)
            if mode == "SINGLE":
                self.armed = False
                self._set("trigger.mode", "STOP")
        return used

    def _poll(self) -> int:
        """A SINGLE acquisition still waiting: try again now (the signal may have arrived since)."""
        return self.acquire_now() if self.armed and self._st("trigger.mode") == "SINGLE" else 0

    def _record_for(self, channel: int, fresh: bool) -> tuple[Record, int]:
        """The record a readback uses: a new acquisition when ``fresh`` and the scope is running."""
        used = 0
        mode = self._st("trigger.mode")
        if (fresh and mode in ("AUTO", "NORMal")) or (self.armed and mode == "SINGLE"):
            used += self.acquire_now(channel)
        rec = self.records.get(channel)
        if rec is None:
            # Nothing acquired on this channel yet (stopped, or no trigger): rather than an empty
            # trace, read what the channel shows now, and say so in the event history.
            records, t, _ = self.compute(self._channels_for(channel))
            used += self._store(records, t, forced=True)
            rec = self.records[channel]
        return rec, used

    @op("trig_mode")
    def trig_mode(self, query: bool = False, mode: str | None = None, **_: Any) -> OpResult:
        """``TRMD AUTO|NORM|SINGLE|STOP``; SINGLE arms one acquisition and acquires at once if it can."""
        if query:
            used = self._poll()
            return OpResult(
                self.reply("TRMD", TRMD_TEXT[str(self._st("trigger.mode"))], long="TRIG_MODE"), used
            )
        if mode is None:
            raise ScpiError(-109)
        self._set("trigger.mode", mode)
        self.armed = mode == "SINGLE"
        return OpResult(duration_ns=self.acquire_now() if self.armed else 0)

    @op("arm")
    def arm(self, **_: Any) -> OpResult:
        """``ARM``: a single acquisition, like ``TRMD SINGLE``."""
        return self.trig_mode(mode="SINGLE")

    @op("stop")
    def stop(self, **_: Any) -> OpResult:
        self.armed = False
        self._set("trigger.mode", "STOP")
        return OpResult()

    @op("force_trigger")
    def force_trigger(self, **_: Any) -> OpResult:
        """``FRTR``: one acquisition now, triggered or not; a SINGLE acquisition then stops."""
        records, used, _ = self.compute(self.displayed())
        self._store(records, used)
        if self._st("trigger.mode") == "SINGLE":
            self.armed = False
            self._set("trigger.mode", "STOP")
        return OpResult(duration_ns=used)

    @op("wait")
    def wait(self, timeout: float | None = None, **_: Any) -> OpResult:
        """``WAIT [timeout]``: until a pending SINGLE acquisition is done (or the timeout passes)."""
        return OpResult(duration_ns=self._poll())

    @op("opc")
    def opc(self, query: bool = False, **_: Any) -> OpResult:
        """``*OPC?`` answers ``1`` once a pending SINGLE acquisition is done; ``*OPC`` sets the ESR bit."""
        used = self._poll()
        if query:
            return OpResult(b"1", used)
        assert self.owner is not None
        self.owner.esr |= 1
        return OpResult(duration_ns=used)

    # ---- waveform readback -------------------------------------------------------------------------

    def transfer_record(self, channel: int, first: int) -> tuple[Record | None, int]:
        """A readback starting at the first point acquires anew on a running scope (see _record_for)."""
        return self._record_for(channel, fresh=first == 0)

    def _transfer(self) -> Any:
        assert self.owner is not None
        return self.owner.components[str(self.param("transfer"))]

    @op("inspect")
    def inspect(self, query: bool = False, channel: int | None = None, name: str | None = None,
                fmt: str | None = None, **_: Any) -> OpResult:  # fmt: skip
        """``C<n>:INSPECT? "DATA_ARRAY_1",FLOAT``: the selected points in volts, as text.

        The numbers are the ``waveform_transfer`` component's (``transfer``); the command's reply
        template frames them, ``C1:INSP " 1.000000e-03 ... "``, all on one line: no line feed before
        the end and never the connection's name (no ``TCPIP``). A readback starting at ``FP 0``
        acquires anew on a running scope; later pieces read the same record. ``"WAVEDESC"`` gives the
        descriptor variables a script needs to scale and time the data.
        """
        assert channel is not None
        what = (name or "").strip().upper()
        if what not in ("DATA_ARRAY_1", "SIMPLE", "WAVEDESC"):
            raise ScpiError(-224, f"INSPECT {name!r} not modeled by SimuBench")
        if what == "WAVEDESC":
            rec, used = self._record_for(channel, fresh=False)
            return OpResult(self._wavedesc(rec).encode(), used)
        if (fmt or "FLOAT") != "FLOAT":
            raise ScpiError(-224, f"INSPECT format {fmt} not modeled by SimuBench (FLOAT is)")
        return self._transfer().transfer(channel)  # type: ignore[no-any-return]

    def _wavedesc(self, rec: Record) -> str:
        """The descriptor variables, ``NAME : value`` separated by blanks (one line)."""
        sel = self._transfer().selection(rec.volts.size)
        items = {
            "WAVE_ARRAY_COUNT": str(rec.volts.size),
            "FIRST_POINT": str(int(sel[0]) if sel.size else 0),
            "SPARSING_FACTOR": str(max(1, int(self._st("waveform.sp")))),
            "HORIZ_INTERVAL": f"{rec.dt_s:.9e}",
            "HORIZ_OFFSET": f"{-rec.trigger_index * rec.dt_s:.9e}",
            "VERTICAL_OFFSET": f"{rec.offset:.6e}",
            "VERTICAL_GAIN": f"{rec.scale / ((1 << int(self.param('adc_bits'))) / float(self.param('adc_divs'))):.6e}",
            "TIMEBASE": f"{float(self._st('timebase.scale')):.6e}",
            "VOLT_PER_DIV": f"{rec.scale:.6e}",
            "ACQ_COUNT": str(self.acq_count),
        }
        return " ".join(f"{k} : {v}" for k, v in items.items())

    @op("pava")
    def pava(self, query: bool = False, channel: int | None = None, kind: str | None = None,
             **_: Any) -> OpResult:  # fmt: skip
        """``C<n>:PAVA? AMPL``: a measurement on the latest record, ``C1:PAVA AMPL,200.1E-3 V,OK``."""
        assert channel is not None
        what = (kind or "").strip().upper()
        if what not in MEASUREMENTS:
            raise ScpiError(-224, f"measurement {kind} not modeled by SimuBench")
        engine_name, unit = MEASUREMENTS[what]
        rec, used = self._record_for(channel, fresh=True)
        value = _measure(engine_name, rec.volts, rec.dt_s)
        state = "OK" if math.isfinite(value) else "UF"
        body = f"{what},{V.format_engineering(value).decode() if math.isfinite(value) else 'UNDEF'} {unit},{state}"
        return OpResult(self.reply("PAVA", body, channel, "PARAMETER_VALUE"), used)


@component("lecroy_input")
class LecroyInput(InputPort):
    """The input connector: 50 Ω with ``D50``, 1 MΩ (and its capacitance) otherwise."""

    defaults = {**InputPort.defaults, "coupling": "D1M"}

    @classmethod
    def impedance_of(cls, params: dict[str, Any]) -> float:
        return 50.0 if params.get("coupling") == "D50" else super().impedance_of(params)


@component("lecroy_frontend")
class LecroyFrontend(ScopeFrontend):
    """Coupling as the front end sees it: ``A1M`` is AC, ``D1M`` and ``D50`` are DC, ``GND`` is ground."""

    _COUPLING = {"A1M": "AC", "D1M": "DC", "D50": "DC", "GND": "GND"}

    def param(self, key: str) -> Any:
        value = super().param(key)
        return self._COUPLING.get(str(value), value) if key == "coupling" else value
