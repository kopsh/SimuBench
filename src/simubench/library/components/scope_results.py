"""The measurement results list: the screen list, its statistics and the history behind them (the oscilloscope's measure menu). Part of ``ScopeMeasurements`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math
import re
from typing import Any

from ...engine.component import (
    OpResult,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .reading_math import value_stats
from .scope_measurements_host import MeasurementsHost


class ResultsList:
    def __init__(self, host: MeasurementsHost) -> None:
        self.host = host
        self.history: dict[tuple[str, int], list[float]] = {}
        self.installed: list[tuple[str, int]] = []  # the screen's measurements, oldest first (#373)
        self.names: dict[tuple[str, int], str] = {}  # a measurement's own name (:MEASure:NAME)
        self.screen: list[tuple[str, int]] = []  # top to bottom: the newest first unless moved

    def install(self, what: str, n: int) -> None:
        """Put a measurement on the screen: a second install of the same one moves it to the end;
        past ``slots`` the oldest leaves. On screen the newest goes to the top."""
        slots = int(self.host.param("slots") or 0)
        if slots <= 0:
            raise ScpiError(-113, f"{what}: this model has no measurement list")
        entry = (what, n)
        if entry in self.installed:
            self.installed.remove(entry)
        self.installed.append(entry)
        del self.installed[:-slots]
        if entry in self.screen:
            self.screen.remove(entry)
        self.screen.insert(0, entry)
        self.screen = [e for e in self.screen if e in self.installed]

    def _label(self, what: str, n: int) -> str:
        return self.names.get((what, n)) or (f"{what}(f{-n})" if n < 0 else f"{what}({n})")

    def op_results(self, order: str = "", **_: Any) -> OpResult:
        word = str(order or self.host.param("results_order") or "").strip().upper()
        entries = (
            list(reversed(self.installed))
            if word.startswith("AORD")
            else list(self.screen)
            if word.startswith("GORD")
            else list(self.installed)
        )
        parts, used = self._result_rows(entries)
        return OpResult(b",".join(parts), used)

    def _result_rows(self, entries: list[tuple[str, int]], every: bool = False) -> tuple[list[bytes], int]:
        """One row per measurement: label and every statistic, or the one ``statistics`` names
        (``every``: always the full row)."""
        stat = "" if every else str(self.host.param("statistics") or "").strip().upper()
        parts: list[bytes] = []
        used = 0
        for what, n in entries:
            value, u = self.host.values.value(what, n)
            used += u
            self.keep(what, n, value)
            stats = value_stats(self.history.get((what, n)) or [])
            count = int(stats["count"])
            summary = [stats[k] if count else math.nan for k in ("minimum", "maximum", "mean", "sdev")]
            if stat and not stat.startswith("ON"):
                pick = dict(zip(("CURR", "MIN", "MAX", "MEAN", "STDD"), (value, *summary), strict=True))
                key = next((k for k in pick if stat.startswith(k)), None)
                parts.append(str(count).encode() if key is None else self.host.number(pick[key]))
                continue
            fields = [self._label(what, n).encode(), self.host.number(value)]
            fields += [self.host.number(x) for x in summary]
            fields.append(str(count).encode())
            parts.append(b",".join(fields))
        return parts, used

    def op_store_results(self, name: str = "", **_: Any) -> OpResult:
        storage: Any = self.host.peer(str(self.host.param("storage")))
        if storage is None:
            raise ScpiError(-300, f"{self.host.uid}: no storage for measurement files")
        tail = re.split(r"[\\/:]", name)[-1]
        file = name if tail.lower().endswith(".csv") else name + ".csv"
        rows, used = self._result_rows(list(self.screen), every=True)
        text = "Measurement,Current,Min,Max,Mean,Std Dev,Count\r\n" + "".join(
            r.decode() + "\r\n" for r in rows
        )
        storage.store(file, text.encode())
        return OpResult(duration_ns=used)

    def entry(self, index: Any) -> tuple[str, int]:
        """The screen's measurement at a position from the top (1, or ``MEAS3``)."""
        text = str(index if not isinstance(index, bytes) else V.parse_string(index)).strip().upper()
        digits = text.removeprefix("MEASUREMENT").removeprefix("MEAS")
        if not digits.isdigit() or int(digits) < 1:
            raise ScpiError(-224, f"no measurement {index!r}")
        k = int(digits)
        if k > len(self.screen):
            raise ScpiError(-221, f"measurement {k} is not on the screen")
        return self.screen[k - 1]

    def op_entry(
        self,
        query: bool = False,
        what: str = "",
        index: Any = None,
        value: Any = None,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        if index is None and suffixes:
            index = next(iter(v for k, v in suffixes.items() if k != "ch"), None)
        entry = self.entry(index)
        k = self.screen.index(entry)
        if what == "name":
            if query:
                return OpResult(V.format_string(self._label(*entry)))
            self.names[entry] = V.parse_string(value) if isinstance(value, bytes) else str(value)
            return OpResult()
        if what == "clear":
            self.screen.remove(entry)
            self.installed.remove(entry)
            return OpResult()
        if what == "position":
            new = int(value)
            if not 1 <= new <= 40:
                raise ScpiError(-222, "position 1 to 40")
            self.screen.pop(k)
            self.screen.insert(min(new, len(self.screen) + 1) - 1, entry)
            return OpResult()
        if what == "source":
            if query:
                return OpResult(self.host.source_word(entry[1]).encode())
            n = self.host.channel_of(None, V.parse_string(value) if isinstance(value, bytes) else str(value))
            moved = (entry[0], n)
            self.screen[k] = moved
            self.installed[self.installed.index(entry)] = moved
            if entry in self.names:
                self.names[moved] = self.names.pop(entry)
            return OpResult()
        raise ScpiError(-300, f"{self.host.uid}: no entry action {what!r}")

    def op_clear_list(self, **_: Any) -> OpResult:
        self.installed = []
        self.screen = []
        self.names = {}
        self.history = {}
        return OpResult()

    def keep(self, name: str, n: int, value: float) -> None:
        if math.isfinite(value):
            self.history.setdefault((name, n), []).append(value)

    def statistics(
        self, kind: str | None = None, item: str = "mean", channel: int | None = None, **_: Any
    ) -> OpResult:
        if kind is None:
            raise ScpiError(-109)
        table = {str(k).upper(): v for k, v in (self.host.param("names") or {}).items()}
        name = kind.strip().upper()
        n = self.host.channel_of(channel)
        stats = value_stats(
            self.history.get((name, n)) or self.history.get((str(table.get(name, [name])[0]), n)) or []
        )
        if item == "count":
            return OpResult(str(stats["count"]).encode())
        if not stats["count"]:
            return OpResult(str(self.host.param("invalid_value")).encode())
        key = {"mean": "mean", "sdev": "sdev", "min": "minimum", "max": "maximum"}.get(item)
        if key is None:
            raise ScpiError(-224, f"no statistic {item!r}")
        return OpResult(self.host.format_number(stats[key]))

    def clear_statistics(self, **_: Any) -> OpResult:
        self.history = {}
        return OpResult()
