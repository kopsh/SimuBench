"""What a trigger looks at: its source channel and kind, the level and threshold it compares with, the signal at the front end and its noise. Part of ``ScopeTrigger`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math

from ...engine.signal import Signal, highpass1, lowpass1
from ...scpi import values as V
from .scope import _upper, trigger_channel
from .scope_trigger_host import TriggerHost


class Sources:
    def __init__(self, host: TriggerHost) -> None:
        self.host = host
        self.source_scale: float | None = None  # the division of the source being searched
        self.noise_factor = 1.0  # the source channel's noise-bandwidth factor (#1427)

    def source_channel(self, source: str | None = None) -> int | None:
        """The channel the source names, None for external, line and the like."""
        return trigger_channel(self.host.param("source") if source is None else source)

    def source_kind(self, source: str | None = None) -> str:
        """channel | external | line | none."""
        src = str(self.host.param("source") if source is None else source).upper()
        if self.source_channel(src) is not None:
            return "channel"
        if src in _upper(self.host.param("external")):
            return "external"
        if src in _upper(self.host.param("line")):
            return "line"
        return "none"

    def family_source(self) -> str:
        """The source word the current mode's family looks at: each advanced family keeps its own
        source setting (#1184), the edge and glitch families share the main one."""
        key = {
            "runt": "runt_source",
            "transition": "transition_source",
            "window": "window_source",
            "timeout": "timeout_source",
            "nedge": "nedge_source",
        }.get(str(self.host.choice("mode")))
        if key is None:
            return str(self.host.param("source"))
        own = self.host.param(key)
        return str(self.host.param("source")) if own in (None, "") else str(own)

    def threshold(self, which: str, source: str) -> float | None:
        """The ``high`` or ``low`` threshold of a source in display volts: the source's own
        setting (``high_by_source``/``low_by_source``, like the level's), else the fallback
        (``high_threshold``/``low_threshold``), else None — unset, nothing to compare against."""
        by = {str(k).upper(): str(v) for k, v in dict(self.host.param(f"{which}_by_source") or {}).items()}
        if by:
            src = str(source).upper()
            for word, path in by.items():
                if src in (word, V.short_form(word)):
                    return float(self.host.get_state(path))
        value = self.host.param(f"{which}_threshold")
        return None if value is None else float(value)

    def level(self) -> float:
        """The trigger level in display volts (the scope screen draws it). With ``level_by_source``
        each source keeps its own level (#248: :TRIGger:LEVel <level>,<source>)."""
        by_source = {
            str(k).upper(): str(v) for k, v in dict(self.host.param("level_by_source") or {}).items()
        }
        if by_source:
            src = str(self.host.param("source")).upper()
            for word, path in by_source.items():
                if src in (word, V.short_form(word)):
                    return float(self.host.get_state(path))
        level = self.host.channel_param("level", self.source_channel())
        return 0.0 if level is None else float(level)

    def source_level(self, n: int | None) -> float:
        """The level of the source a search looks at: channel ``n`` (None: the edge source, e.g.
        external). With ``level_by_source`` the channel's own level setting, the same one
        :meth:`level` draws (#1361: a level bound to one channel's setting answered CH1's for
        every source); otherwise ``level`` resolved for the channel."""
        if n is None:
            return self.level()
        by_source = dict(self.host.param("level_by_source") or {})
        if by_source:
            for word, path in by_source.items():
                if trigger_channel(word) == n:
                    return float(self.host.get_state(str(path)))
        level = self.host.channel_param("level", n)
        return 0.0 if level is None else float(level)

    def path_signal(self, sig: Signal) -> Signal:
        """The source's signal as the trigger sees it: through the coupling's filters and the trigger
        path's own ``bandwidth`` (#1263)."""
        table = {str(k).upper(): v for k, v in (self.host.param("filters") or {}).items()}
        keys = [str(self.host.param("coupling")).upper(), str(self.host.param("reject") or "").upper()]
        if self.host.param("hf_reject") in (True, 1, "1", "ON", "on"):
            keys.append("HF")
        for key in keys:
            for kind, corner in table.get(key, []) if key else []:
                sig = sig.transfer(
                    (highpass1 if str(kind).lower() == "highpass" else lowpass1)(float(corner))
                )
        bandwidth = float(self.host.param("bandwidth"))
        if math.isfinite(bandwidth):
            sig = sig.transfer(lowpass1(bandwidth))
        return sig

    def noise_sigma(self) -> float:
        """The trigger path's noise, display volts rms: ``noise_rms`` and the ``noise_table`` row of
        the source's division (the search sets it, #1263), scaled as the source channel's front end
        scales its own table to its bandwidth (the trigger taps that front end, #1427)."""
        sigma = float(self.host.param("noise_rms") or 0.0)
        rows = sorted((float(r[0]), float(r[1])) for r in self.host.param("noise_table") or ())
        if rows:
            scale = rows[0][0] if self.source_scale is None else self.source_scale
            row = next((rms for div, rms in reversed(rows) if div <= scale * (1 + 1e-9)), rows[0][1])
            sigma += row * self.noise_factor
        return sigma
