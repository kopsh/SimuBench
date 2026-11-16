"""An oscilloscope's autoscale: the settings that display the input waveforms, found from them.

The Infiniium's :AUToscale (UXR Programmer's Guide 11.80, pp. 295-298) searches the channels for
waveforms, turns off the ones without, sets each one's vertical scale and offset, the timebase on
the lowest-numbered channel with a waveform, and an edge trigger on the first channel a search
finds a triggerable waveform on (from channel 4 down); with no waveform anywhere the scope keeps
its former state. :AUToscale:VERTical sets one channel's vertical settings and nothing else.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError

#: The spread of a waveform is read between these percentiles of its samples, so that the noise's
#: rare extremes do not set the scale (a 200 000-point record reaches about 3.3 noise rms at 0.05 %).
_LOW_PERCENTILE, _HIGH_PERCENTILE = 0.05, 99.95


def nice_ceil(x: float) -> float:
    """The smallest value of the 1-2-5 sequence at or above ``x`` (x > 0)."""
    exp = math.floor(math.log10(x))
    for mant in (1.0, 2.0, 5.0, 10.0):
        value = mant * 10.0**exp
        if value >= x * (1 - 1e-9):
            return value
    return 10.0 ** (exp + 1)  # pragma: no cover - the loop always returns


@dataclass
class Fit:
    """What autoscale found on one channel: whether a waveform is there, the vertical settings
    that show it, the levels it swings between, and whether an edge trigger can take it."""

    present: bool
    scale: float
    offset: float
    low: float
    high: float
    triggerable: bool

    @property
    def middle(self) -> float:
        return (self.low + self.high) / 2


@component("scope_autoscale")
class ScopeAutoscale(Component):
    """The autoscale of an oscilloscope (the front panel's Autoscale key, :AUToscale).

    Each channel searched is acquired at its coarsest scale, then again at the scale and offset its
    record asks for, until they settle (``passes``): the waveform's spread is read between the
    0.05 and 99.95 percentiles, so the scale puts it over ``fill_divs`` divisions (the next value
    of the 1-2-5 sequence), the offset centres it. A waveform is there when the record's spread
    beyond the channel's noise (its ``scope_vertical`` noise table at that scale) exceeds
    ``presence`` noise rms, or its mean is beyond ``dc_floor``; it is triggerable when it swings
    through a band of ``presence`` noise rms around its middle (a DC level is not). ``STACk``
    gives each waveform its own band of the screen, ``OVERlay`` and ``SEParate`` (each grid the
    whole range) the full screen.

    The timebase shows ``periods`` periods of the lowest-numbered channel's waveform. The period is
    its fundamental, the lowest of its spectral lines above a twentieth of the strongest — as the
    trigger's counter sees it, without the noise: a SimuBench simplification of the scope's
    search (a waveform without lines, a DC level, keeps the timebase). The edge trigger takes the
    first triggerable channel in ``trigger_order``, its level the waveform's middle; ``writes``
    are the settings autoscale leaves at fixed values (the trigger mode, slope and sweep, the marker
    mode, the functions off). Nothing found: every setting as it was."""

    refs = {"record": ("scope_acquisition", "instrument")}
    defaults = {
        "record": "acq",  # the scope_acquisition component that acquires the channels
        "scale": "ch{n}.scale",  # the setting each channel's vertical scale (V/div) is in
        "offset": "ch{n}.offset",  # the setting each channel's offset (V at the screen's centre) is in
        "display": "ch{n}.display",  # the setting that turns a channel on
        "timebase": "timebase.scale",  # the setting of the timebase (s/div)
        "position": "timebase.position",  # the horizontal position setting autoscale sets to 0 ("": none)
        "trigger_source": "trigger.source",  # the edge trigger's source setting, written CHANnel<n>
        "trigger_level": "trigger.level{n}",  # each channel's trigger level setting
        "writes": {},  # settings autoscale sets to fixed values: {path: value}
        "channels": "ALL",  # which channels are searched: ALL, or DISPlayed (only those on)
        "placement": "OVERlay",  # OVERlay, STACk (each waveform its own band) or SEParate
        "trigger_order": "DESCending",  # the trigger search: DESCending (channel 4 first) or ASCending
        "fill_divs": 6.0,  # divisions a waveform's peak-to-peak spans at most (SimuBench value)
        "periods": 3.0,  # periods of the timebase channel's waveform across the screen (SimuBench value)
        "presence": 3.0,  # noise rms a waveform's spread beyond the noise must exceed (SimuBench value)
        "dc_floor": 0.02,  # V: a mean beyond this (and the presence) is a waveform too (SimuBench value)
        "divisions": 8,  # vertical divisions of the screen
        "h_divisions": 10,  # horizontal divisions of the screen
        "passes": 5,  # acquisitions at most that refine the vertical settings
    }
    units = {"dc_floor": "V"}
    choices = {
        "channels": ("ALL", "DISPlayed"),
        "placement": ("OVERlay", "STACk", "SEParate"),
        "trigger_order": ("DESCending", "ASCending"),
    }

    # ---- the operations ----------------------------------------------------------------------------

    @op("autoscale")
    def op_autoscale(self, **_: Any) -> OpResult:
        """``:AUToscale``: search the channels, set the vertical settings of those with a
        waveform and turn the others off, the timebase on the lowest-numbered one, the edge trigger
        on the first triggerable one in ``trigger_order``. Nothing found: nothing changes."""
        assert self.owner is not None
        acq = self._acq()
        count = int(self.owner.channel_count)
        if self.choice("channels") == "DISPlayed":
            channels = [
                n for n in range(1, count + 1) if V.flag(self.owner.get_state(self._path("display", n)))
            ]
        else:
            channels = list(range(1, count + 1))
        if not channels:
            return OpResult()
        saved = self._snapshot(channels)
        for n in channels:
            self.owner.set_state(self._path("display", n), True, internal=True)
        self._window(channels)
        fits, used = self._fit(channels)
        found = [n for n in channels if fits[n].present]
        if not found:
            self._restore(saved)
            return OpResult(duration_ns=used)
        self._place(found, fits)
        for n in channels:
            if n in found:
                self._apply(n, fits[n].scale, fits[n].offset)
            else:  # off, with the settings it had
                scale, offset = (saved.get(self._path(k, n)) for k in ("scale", "offset"))
                if scale is not None and offset is not None:
                    self._apply(n, float(scale), float(offset))
                self.owner.set_state(self._path("display", n), False, internal=True)
        self._timebase(found[0])
        order = sorted(found, reverse=self.choice("trigger_order") == "DESCending")
        source = next((n for n in order if fits[n].triggerable), found[0])
        if self.param("trigger_source"):
            self._write_source(str(self.param("trigger_source")), source)
        if self.param("trigger_level"):
            self.owner.set_state(self._path("trigger_level", source), fits[source].middle, internal=True)
        for path, value in (self.param("writes") or {}).items():
            self.owner.set_state(str(path), value, internal=True)
        return OpResult(duration_ns=used + self._refresh(acq))

    @op("vertical")
    def op_vertical(self, source: Any = None, channel: int | None = None, **_: Any) -> OpResult:
        """``:AUToscale:VERTical CHANnel<N>``: that channel's scale and offset for its waveform,
        nothing else changed (no waveform: nothing changes)."""
        assert self.owner is not None
        n = self._channel(source, channel)
        saved = self._snapshot([n])
        self._window([n])
        fits, used = self._fit([n])
        self._restore(saved)  # the timebase as it was; the channel's own settings follow
        if not fits[n].present:
            return OpResult(duration_ns=used)
        self._apply(n, fits[n].scale, fits[n].offset)
        return OpResult(duration_ns=used + self._refresh(self._acq()))

    # ---- the search ----------------------------------------------------------------------------------

    def _acq(self) -> Any:
        return self.ref("record")

    def _path(self, key: str, n: int) -> str:
        return str(self.param(key)).replace("{n}", str(n))

    def _channel(self, source: Any, channel: int | None) -> int:
        if source is None:
            return channel or 1
        word = (V.parse_string(source) if isinstance(source, bytes) else str(source)).strip().upper()
        digits = word.removeprefix("CHANNEL").removeprefix("CHAN").removeprefix("CH").removeprefix("C")
        if not digits.isdigit():
            raise ScpiError(-224, f"{source!r}: autoscale takes a channel")
        return int(digits)

    def _snapshot(self, channels: list[int]) -> dict[str, Any]:
        """Every setting autoscale may change, as it is now."""
        assert self.owner is not None
        paths = [self._path(k, n) for n in channels for k in ("scale", "offset", "display")]
        paths += [str(self.param(k)) for k in ("timebase", "position") if self.param(k)]
        return {p: self.owner.get_state(p) for p in paths if p in self.owner.specs}

    def _restore(self, saved: dict[str, Any]) -> None:
        assert self.owner is not None
        count = int(self.owner.channel_count)
        offsets = {self._path("offset", n) for n in range(1, count + 1)} & saved.keys()
        for path in offsets:  # the offset's range follows the scale: at 0 first, restored last
            self.owner.set_state(path, 0.0, internal=True)
        for path, value in saved.items():
            if path not in offsets:
                self.owner.set_state(path, value, internal=True)
        for path in offsets:
            self.owner.set_state(path, saved[path], internal=True)

    def _apply(self, n: int, scale: float, offset: float) -> None:
        """Channel n's scale and offset: the offset at 0 first, since its range follows the scale."""
        assert self.owner is not None
        path = self._path("offset", n)
        self.owner.set_state(path, 0.0, internal=True)
        self.owner.set_state(self._path("scale", n), scale, internal=True)
        self.owner.set_state(path, self._limits(path, offset), internal=True)

    def _limits(self, path: str, value: float) -> float:
        assert self.owner is not None
        lo, hi = self.owner.limits(path)
        if lo is not None:
            value = max(value, float(lo))
        if hi is not None:
            value = min(value, float(hi))
        return value

    def _noise(self, n: int) -> float:
        """Channel n's noise at its current scale, V rms on the record's (displayed) scale."""
        vertical = self._acq().vertical_of(n)
        return float(vertical.input_noise_rms()) * float(vertical.param("probe_ratio"))

    def _fit(self, channels: list[int]) -> tuple[dict[int, Fit], int]:
        """Acquire the channels at their coarsest scale, then at the settings their records ask
        for, until those settle; what each record showed at the last pass."""
        assert self.owner is not None
        acq = self._acq()
        fill = float(self.param("fill_divs"))
        settings: dict[int, tuple[float, float]] = {}
        for n in channels:
            top = self.owner.limits(self._path("scale", n))[1]
            scale = float(top) if top is not None else float(self.owner.get_state(self._path("scale", n)))
            settings[n] = (scale, 0.0)
        fits: dict[int, Fit] = {}
        used = 0
        for _ in range(max(1, int(self.param("passes")))):
            for n, (scale, offset) in settings.items():
                self._apply(n, scale, offset)
            records, u, _ = acq.compute(channels)
            used += u
            settled = True
            for n in channels:
                v = np.asarray(records[n].volts, dtype=float)
                scale, offset = settings[n]
                fits[n] = self._read(n, v, scale, offset)
                new_scale = self._limits(
                    self._path("scale", n), nice_ceil(max(fits[n].high - fits[n].low, 1e-12) / fill)
                )
                new_offset = self._limits(self._path("offset", n), fits[n].middle)
                if (
                    not math.isclose(new_scale, scale, rel_tol=1e-9)
                    or abs(new_offset - offset) > 0.1 * new_scale
                ):
                    settled = False
                settings[n] = (new_scale, new_offset)
                fits[n].scale, fits[n].offset = new_scale, new_offset
            if settled:
                break
        return fits, used

    def _read(self, n: int, v: np.ndarray, scale: float, offset: float) -> Fit:
        """What one record shows: its spread, whether a waveform is there, whether it triggers."""
        if v.size == 0:
            return Fit(False, scale, offset, 0.0, 0.0, False)
        low, high = (float(x) for x in np.percentile(v, [_LOW_PERCENTILE, _HIGH_PERCENTILE]))
        sigma = self._noise(n)
        k = float(self.param("presence"))
        spread = math.sqrt(max(float(v.var()) - sigma * sigma, 0.0))
        mean = float(v.mean())
        swings = spread > k * sigma
        present = swings or abs(mean) > max(k * sigma, float(self.param("dc_floor")))
        middle = (low + high) / 2
        band = max(k * sigma, 0.1 * (high - low))
        triggerable = swings and bool(np.any(v > middle + band)) and bool(np.any(v < middle - band))
        return Fit(present, scale, offset, low, high, triggerable)

    def _place(self, found: list[int], fits: dict[int, Fit]) -> None:
        """STACk: each waveform its own band of the screen, top to bottom in channel order."""
        if self.choice("placement") != "STACk" or len(found) < 2:
            return
        divs = float(self.param("divisions"))
        band = divs / len(found)
        fill = float(self.param("fill_divs")) / divs * band
        for i, n in enumerate(found):
            fit = fits[n]
            fit.scale = self._limits(self._path("scale", n), nice_ceil(max(fit.high - fit.low, 1e-12) / fill))
            centre = divs / 2 - (i + 0.5) * band  # divisions above the screen's centre
            fit.offset = self._limits(self._path("offset", n), fit.middle - centre * fit.scale)

    def _fundamental(self, n: int) -> float | None:
        """Channel n's fundamental (Hz): the lowest of its spectral lines above a twentieth of the
        strongest; None for a waveform without lines (a DC level)."""
        lines = self._acq().vertical_of(n).output("out").spectral_lines(max_lines=32)
        if not lines:
            return None
        strongest = max(line.amplitude for line in lines)
        f = min(line.freq_hz for line in lines if line.amplitude >= strongest / 20)
        return f if f > 0 else None

    def _set_timebase(self, periods: float, f: float) -> None:
        assert self.owner is not None
        path = str(self.param("timebase") or "")
        if path:
            per_div = periods / f / float(self.param("h_divisions"))
            self.owner.set_state(path, self._limits(path, nice_ceil(per_div)), internal=True)

    def _window(self, channels: list[int]) -> None:
        """The timebase the vertical search acquires with: a period and a half of the slowest
        waveform on screen, so each record holds its swing (a square's top and base); the
        horizontal position at 0. Without lines anywhere it stays."""
        assert self.owner is not None
        if self.param("position"):
            self.owner.set_state(str(self.param("position")), 0.0, internal=True)
        found = [f for f in (self._fundamental(n) for n in channels) if f is not None]
        if found:
            self._set_timebase(1.5, min(found))

    def _timebase(self, n: int) -> None:
        """The timebase for ``periods`` periods of channel n's waveform (its fundamental)."""
        f = self._fundamental(n)
        if f is not None:
            self._set_timebase(float(self.param("periods")), f)

    def _write_source(self, path: str, n: int) -> None:
        """The trigger source setting to channel n, by the word its options spell it with."""
        assert self.owner is not None
        options = self.owner.specs[path].options or []
        word = next(
            (o for o in options if str(o).upper().removeprefix("CHANNEL").removeprefix("CHAN") == str(n)),
            f"CHANnel{n}",
        )
        self.owner.set_state(path, word, internal=True)

    def _refresh(self, acq: Any) -> int:
        """A fresh acquisition of the channels now on, so what is read next shows the new settings."""
        on = (
            [n for n in range(1, int(self.owner.channel_count) + 1) if n in acq.displayed()]
            if self.owner
            else []
        )
        if not on:
            return 0
        records, used, _ = acq.compute(on)
        return int(acq.store(records, used, forced=True))
