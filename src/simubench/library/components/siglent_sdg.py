"""Siglent SDG waveform generator command set (real model, D42): key-value commands and replies.

Behavior follows the *SDG Series Arbitrary Waveform Generator Programming Guide* PG02_E05C for the
SDG2000X. The commands are Siglent's own, not SCPI trees: ``C1:BSWV WVTP,SINE,FRQ,1000,AMP,2``
sets several parameters at once and ``C1:BSWV?`` answers with the header and every parameter,
numbers carrying their unit (``C1:BSWV WVTP,SINE,FRQ,100HZ,PERI,0.01S,AMP,2V,...``). This
component parses and writes those messages; the waveform itself is the shared
``function_generator_core`` and ``output_stage`` (amplitude defined for the load setting, D13).
"""

from __future__ import annotations

import math
from typing import Any

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError
from ...units import UnitError, parse_number

#: Waveform types of the guide; the modeled ones and their highest frequency (SDG2042X data sheet).
WAVE_TYPES = ("SINE", "SQUARE", "RAMP", "PULSE", "NOISE", "ARB", "DC", "PRBS", "IQ")
MAX_FREQ = {"SINE": 40e6, "SQUARE": 25e6, "RAMP": 1e6}
MIN_FREQ = 1e-6
Z_OUT = 50.0
#: HiZ amplitude range (data sheet: 2 mVpp to 20 Vpp up to 20 MHz, 10 Vpp above) and offset reach.
MIN_AMP, MAX_AMP, MAX_AMP_HF, MAX_LEVEL = 2e-3, 20.0, 10.0, 10.0
#: RMS per Vpp by waveform (for AMPVRMS).
RMS_PER_VPP = {"SINE": 1 / (2 * math.sqrt(2)), "SQUARE": 0.5, "RAMP": 1 / (2 * math.sqrt(3))}
#: The settings whose ranges depend on each other.
LIMITED = ("function", "frequency", "amplitude", "offset", "load")


def _num(value: float, unit: str = "") -> str:
    """A number as the generator writes it: shortest form, then the unit (``100HZ``, ``0.01S``)."""
    text = f"{value:.12g}"
    if "e" in text:
        mant, exp = text.split("e")
        text = f"{mant}e{int(exp)}"
    return text + unit


def _value(raw: str, unit: str | None) -> float:
    try:
        return parse_number(raw.strip(), unit)
    except (UnitError, ValueError):
        raise ScpiError(-224, f"bad number {raw!r}") from None


def check_ranges(s: dict[str, Any]) -> None:
    """The data sheet's ranges for one channel's settings (function, frequency, amplitude, offset, load)."""
    wave = s["function"]
    load = float(s["load"])
    # The data sheet's ranges are for HiZ; a load divides them (by 2 at 50 Ohm).
    k = 1.0 if not math.isfinite(load) else load / (load + Z_OUT)
    if wave in MAX_FREQ:
        if not MIN_FREQ * (1 - 1e-9) <= float(s["frequency"]) <= MAX_FREQ[wave] * (1 + 1e-9):
            raise ScpiError(-222, f"{wave} frequency outside {MIN_FREQ:g}..{MAX_FREQ[wave]:g} Hz")
        top = (MAX_AMP if float(s["frequency"]) <= 20e6 else MAX_AMP_HF) * k
        amp = float(s["amplitude"])
        if not MIN_AMP * k * (1 - 1e-9) <= amp <= top * (1 + 1e-9):
            raise ScpiError(-222, f"amplitude outside {MIN_AMP * k:g}..{top:g} Vpp at this load")
        if abs(float(s["offset"])) + amp / 2 > MAX_LEVEL * k * (1 + 1e-9):
            raise ScpiError(-221, "amplitude and offset exceed the output range")
    elif abs(float(s["offset"])) > MAX_LEVEL * k * (1 + 1e-9):
        raise ScpiError(-222, f"DC level outside +-{MAX_LEVEL * k:g} V at this load")


@component("siglent_sdg")
class SiglentSdg(Component):
    """C<n>:BSWV, C<n>:OUTP, C<n>:INVT, C<n>:SYNC, PACP and BUZZ of an SDG2000X."""

    # ---- helpers ----------------------------------------------------------------------------------

    def _st(self, ch: int, key: str) -> Any:
        assert self.owner is not None
        return self.owner.get_state(f"ch{ch}.{key}")

    def _set(self, ch: int, values: dict[str, Any]) -> None:
        """Apply several settings at once after checking them together; refuse all on a violation."""
        assert self.owner is not None
        merged = {k: self._st(ch, k) for k in LIMITED}
        merged.update(values)
        check_ranges(merged)
        limits = self.owner.components[f"ch{ch}.limits"]
        limits.batch = True  # checked as a whole above; intermediate states may be out of range
        try:
            for key, value in values.items():
                self.owner.set_state(f"ch{ch}.{key}", value)
        finally:
            limits.batch = False

    @staticmethod
    def _pairs(params: list[bytes]) -> list[tuple[str, str]]:
        items = [V.parse_string(p) for p in params]
        if len(items) % 2:
            raise ScpiError(-109, "parameters come in name,value pairs")
        return [(items[i].upper(), items[i + 1]) for i in range(0, len(items), 2)]

    def _not_modeled(self, what: str) -> ScpiError:
        return ScpiError(-224, f"{what} not modeled by SimuBench")

    # ---- C<n>:BaSic_WaVe ---------------------------------------------------------------------------

    @op("bswv")
    def bswv(self, query: bool = False, channel: int | None = None, params: list[bytes] | None = None,
             **_: Any) -> OpResult:  # fmt: skip
        assert channel is not None
        if query:
            return OpResult(self._bswv_reply(channel).encode())
        if not params:
            raise ScpiError(-109)
        wave = str(self._st(channel, "function"))
        amp, offset = float(self._st(channel, "amplitude")), float(self._st(channel, "offset"))
        new: dict[str, Any] = {}
        for name, raw in self._pairs(params):
            if name == "WVTP":
                wave = raw.strip().upper()
                if wave not in WAVE_TYPES:
                    raise ScpiError(-224, f"unknown waveform {raw!r}")
                if wave not in MAX_FREQ and wave != "DC":
                    raise self._not_modeled(f"waveform {wave}")
                new["function"] = wave
            elif name == "FRQ":
                new["frequency"] = _value(raw, "HZ")
            elif name == "PERI":
                new["frequency"] = 1.0 / _value(raw, "S")
            elif name == "AMP":
                amp = new["amplitude"] = _value(raw, "V")
            elif name == "AMPVRMS":
                amp = new["amplitude"] = _value(raw, "V") / RMS_PER_VPP.get(wave, RMS_PER_VPP["SINE"])
            elif name == "AMPDBM":
                load = float(self._st(channel, "load"))
                if not math.isfinite(load):
                    raise ScpiError(-221, "AMPDBM needs a finite load setting")
                vrms = math.sqrt(10 ** (_value(raw, None) / 10) / 1000 * load)
                amp = new["amplitude"] = vrms / RMS_PER_VPP.get(wave, RMS_PER_VPP["SINE"])
            elif name == "OFST":
                offset = new["offset"] = _value(raw, "V")
            elif name in ("HLEV", "LLEV"):
                hi, lo = offset + amp / 2, offset - amp / 2
                if name == "HLEV":
                    hi = _value(raw, "V")
                else:
                    lo = _value(raw, "V")
                if hi <= lo:
                    raise ScpiError(-221, "high level must be above low level")
                amp, offset = new["amplitude"], new["offset"] = hi - lo, (hi + lo) / 2
            elif name == "PHSE":
                phase = _value(raw, None)
                if not 0 <= phase <= 360:
                    raise ScpiError(-222, "phase outside 0..360")
                new["phase"] = phase
            elif name == "DUTY":
                duty = _value(raw, None)
                if wave != "SQUARE":
                    raise ScpiError(-221, "DUTY applies to SQUARE (PULSE is not modeled)")
                if not 0.001 <= duty <= 99.999:
                    raise ScpiError(-222, "duty outside 0.001..99.999 %")
                new["duty"] = duty
            elif name == "SYM":
                sym = _value(raw, None)
                if wave != "RAMP":
                    raise ScpiError(-221, "SYM applies to RAMP")
                if not 0 <= sym <= 100:
                    raise ScpiError(-222, "symmetry outside 0..100 %")
                new["symmetry"] = sym
            elif name == "MAX_OUTPUT_AMP":
                limit = _value(raw, "V")
                if not 1 <= limit <= 20:
                    raise ScpiError(-222, "MAX_OUTPUT_AMP outside 1..20 Vpp")
                new["max_output_amp"] = limit
            else:
                raise self._not_modeled(f"BSWV parameter {name}")
        self._set(channel, new)
        return OpResult()

    def _bswv_reply(self, ch: int) -> str:
        wave = str(self._st(ch, "function"))
        offset = float(self._st(ch, "offset"))
        if wave == "DC":
            return f"C{ch}:BSWV WVTP,DC,OFST,{_num(offset, 'V')}"
        freq, amp = float(self._st(ch, "frequency")), float(self._st(ch, "amplitude"))
        parts = [
            f"WVTP,{wave}",
            f"FRQ,{_num(freq, 'HZ')}",
            f"PERI,{_num(1 / freq, 'S')}",
            f"AMP,{_num(amp, 'V')}",
            f"OFST,{_num(offset, 'V')}",
            f"HLEV,{_num(offset + amp / 2, 'V')}",
            f"LLEV,{_num(offset - amp / 2, 'V')}",
            f"PHSE,{_num(float(self._st(ch, 'phase')))}",
        ]
        if wave == "SQUARE":
            parts.append(f"DUTY,{_num(float(self._st(ch, 'duty')))}")
        if wave == "RAMP":
            parts.append(f"SYM,{_num(float(self._st(ch, 'symmetry')))}")
        return f"C{ch}:BSWV " + ",".join(parts)

    # ---- C<n>:OUTPut, C<n>:INVerT, C<n>:SYNC ------------------------------------------------------

    @op("outp")
    def outp(self, query: bool = False, channel: int | None = None, params: list[bytes] | None = None,
             **_: Any) -> OpResult:  # fmt: skip
        assert channel is not None and self.owner is not None
        if query:
            load = float(self._st(channel, "load"))
            state = "ON" if self._st(channel, "output") else "OFF"
            plrt = "INVT" if self._st(channel, "invert") else "NOR"
            load_text = "HZ" if not math.isfinite(load) else _num(load)
            return OpResult(f"C{channel}:OUTP {state},LOAD,{load_text},PLRT,{plrt}".encode())
        items = [V.parse_string(p).strip().upper() for p in params or []]
        if not items:
            raise ScpiError(-109)
        new: dict[str, Any] = {}
        i = 0
        while i < len(items):
            word = items[i]
            if word in ("ON", "OFF"):
                new["output"] = word == "ON"
                i += 1
            elif word == "LOAD" and i + 1 < len(items):
                raw = items[i + 1]
                if raw in ("HZ", "HIZ"):
                    new["load"] = math.inf
                else:
                    load = _value(raw, "OHM")
                    if not 50 <= load <= 100000:
                        raise ScpiError(-222, "load outside 50..100000 Ohm (or HZ)")
                    new["load"] = load
                i += 2
            elif word == "PLRT" and i + 1 < len(items):
                if items[i + 1] not in ("NOR", "INVT"):
                    raise ScpiError(-224, "polarity is NOR or INVT")
                new["invert"] = items[i + 1] == "INVT"
                i += 2
            else:
                raise self._not_modeled(f"OUTP parameter {word}")
        if "load" in new:
            self._set(channel, {"load": new.pop("load")})
        for key, value in new.items():
            self.owner.set_state(f"ch{channel}.{key}", value)
        return OpResult()

    @op("invt")
    def invt(self, query: bool = False, channel: int | None = None, state: bool | None = None,
             **_: Any) -> OpResult:  # fmt: skip
        assert channel is not None and self.owner is not None
        if query:
            return OpResult(f"C{channel}:INVT {'ON' if self._st(channel, 'invert') else 'OFF'}".encode())
        if state is None:
            raise ScpiError(-109)
        self.owner.set_state(f"ch{channel}.invert", state)
        return OpResult()

    @op("sync")
    def sync(self, query: bool = False, channel: int | None = None, params: list[bytes] | None = None,
             **_: Any) -> OpResult:  # fmt: skip
        assert channel is not None and self.owner is not None
        if query:
            state = "ON" if self._st(channel, "sync") else "OFF"
            return OpResult(f"C{channel}:SYNC {state},TYPE,{self._st(channel, 'sync_type')}".encode())
        items = [V.parse_string(p).strip().upper() for p in params or []]
        i = 0
        while i < len(items):
            if items[i] in ("ON", "OFF"):
                self.owner.set_state(f"ch{channel}.sync", items[i] == "ON")
                i += 1
            elif items[i] == "TYPE" and i + 1 < len(items):
                self.owner.set_state(f"ch{channel}.sync_type", V.parse_enum(items[i + 1].encode(),
                                     ["CH1", "CH2", "MOD_CH1", "MOD_CH2"]))  # fmt: skip
                i += 2
            else:
                raise ScpiError(-224, f"SYNC parameter {items[i]!r}")
        return OpResult()

    # ---- instrument-wide ----------------------------------------------------------------------------

    @op("pacp")
    def pacp(self, params: list[bytes] | None = None, **_: Any) -> OpResult:
        """ParaCoPy <destination>,<source>: every channel parameter from one channel to the other."""
        assert self.owner is not None
        items = [V.parse_string(p).strip().upper() for p in params or []]
        if len(items) != 2 or not all(x in ("C1", "C2") for x in items) or items[0] == items[1]:
            raise ScpiError(-224, "PACP <destination>,<source> with C1 and C2")
        dst, src = int(items[0][1]), int(items[1][1])
        for key in ("function", "frequency", "amplitude", "offset", "phase", "duty", "symmetry", "load",
                    "invert", "max_output_amp"):  # fmt: skip
            self.owner.set_state(f"ch{dst}.{key}", self._st(src, key))
        return OpResult()

    @op("buzz")
    def buzz(self, query: bool = False, state: bool | None = None, **_: Any) -> OpResult:
        assert self.owner is not None
        if query:
            return OpResult(f"BUZZ {'ON' if self.owner.get_state('buzzer') else 'OFF'}".encode())
        if state is None:
            raise ScpiError(-109)
        self.owner.set_state("buzzer", state)
        return OpResult()


@component("siglent_sdg_channel_limits")
class SiglentSdgChannelLimits(Component):
    """Checks a channel's ranges whenever one of its settings changes outside a command (UI, bench)."""

    defaults = {k: f"$ch.{k}" for k in LIMITED}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.batch = False

    def on_state(self, path: str, old: Any, new: Any) -> None:
        if not self.batch:
            check_ranges({k: self.param(k) for k in LIMITED})
