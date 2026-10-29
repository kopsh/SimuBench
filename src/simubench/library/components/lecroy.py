"""LeCroy-style oscilloscope dialect: short headers, key-value parameters and text waveform readback.

Teledyne LeCroy's remote commands are not SCPI trees. Headers are short words (``TDIV``,
``C1:VDIV``, ``TRSE``), some parameters are key-value lists (``TRSE EDGE,SR,C1``,
``WAVEFORM_SETUP SP,0,NP,0,FP,0,SN,0``) and replies repeat the header and carry the unit
(``C1:VDIV 200E-3 V``) unless ``COMM_HEADER OFF``. These components implement the subset a host
calibration program uses (docs/lowfreq-cal.md); the physics is the shared oscilloscope engine:

* ``lecroy_scope``: the commands the definition cannot declare (settings, ``TRSE``, ``MSIZ`` and
  ``WAVEFORM_SETUP`` are declared with ``dialect``, reply templates and ``pairs`` parameters);
* ``lecroy_acquisition``: the configurable scope acquisition (record length, trigger, run control
  ``TRMD``, noise and the ADC are set in the definition) plus the text readback
  ``C<n>:INSPECT? "DATA_ARRAY_1",FLOAT`` (blank-separated numbers between the header and quotes,
  ``C1:INSP " 1.000000e-03 ... "``, in pieces chosen by ``WAVEFORM_SETUP``), its ``"WAVEDESC"``
  and ``PAVA?`` measurements;
* ``lecroy_input`` / ``lecroy_frontend``: the coupling (``A1M``, ``D1M``, ``D50``, ``GND``) as
  input impedance and front-end filter.

What is simplified, and said so in docs/lowfreq-cal.md: the record has exactly ``MSIZ`` points over
ten divisions (the sample rate is ``MSIZ / (10 TDIV)`` up to ``max_rate``, not snapped to the
instrument's rate table), so any point count reads back whole; segments (``SN``) are not modeled.
"""

from __future__ import annotations

import math
from typing import Any

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .electrical import InputPort
from .scope import Record, ScopeAcquisition, ScopeFrontend, _measure

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
    """INSPECT? readback and PAVA? measurements on the configurable scope acquisition.

    The definition sets the run control: AUTO and NORM acquire anew whenever a readback starts
    (``FP 0``) and keep that record for the following pieces, so pieces read one after another join
    into one record; SINGLE acquires once on the next trigger and then shows ``STOP``.
    """

    defaults = {
        **ScopeAcquisition.defaults,
        "transfer": "xfer",  # the waveform_transfer component that encodes DATA_ARRAY_1
    }

    # ---- waveform readback -------------------------------------------------------------------------

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
            rec, used = self.read_record(channel, start=False)
            assert rec is not None  # missing: acquire
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
            "VERTICAL_GAIN": f"{rec.scale / self.adc_codes_per_div():.6e}",
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
        rec, used = self.measure_record(channel)
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
