"""Oscilloscope measurements as a component: the op pipeline, the screen list, statistics,
cursors and markers; the algorithms are ``measurements`` (split from ``scope``, #1386)."""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Mapping
from typing import Any

import numpy as np

from ...engine.component import (
    Component,
    MathSource,
    OpResult,
    RecordSource,
    component,
    op,
)
from ...engine.stream import (
    MAX_FRAME,
    beyond_model,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from ...units import parse_number
from .measurements import (
    DEFAULT_THRESHOLDS,
    FREQ_HYSTERESIS,
    LEVEL_MEASUREMENTS,
    TIME_MEASUREMENTS,
    Levels,
    burst_measure,
    crossing_voltage,
    crossings,
    edge_spec,
    measure,
    mid_crossings,
    pulse_window_mean,
    threshold_levels,
    top_base,
)
from .reading_math import value_stats
from .scope import SCREEN_MEASUREMENTS, short_source, trigger_channel
from .scope_math import function_index
from .scope_record import (
    CHUNKED_MEASUREMENTS,
    Record,
    channel_index,
    chunked_value,
)


@component("scope_measurements")
class ScopeMeasurements(Component):
    """Automatic measurements on a scope's records (instrument-authoring.md 8.4).

    The engine's measurements are VPP, VMAX, VMIN, VAVerage, VRMS, VAMPlitude (top - base),
    FREQuency and PERiod. ``names`` maps the model's names to them and to a unit (``C1:PAVA? AMPL``,
    ``:MEASure:SIMPle:VALue? PKPK``). How they are taken is declared:

    * the top and the base: the modes of the record's histogram (``histogram``) or its extremes;
    * the crossing level a period is counted on: ``mid`` percent of top minus base, or volts with
      ``levels: absolute``; ``hysteresis`` is half the counter's band, of top minus base;
    * ``min_cycles`` complete cycles a period needs, else it cannot be measured;
    * ``clipped: invalid``: a record that reaches the ADC's limits cannot be measured;
    * ``edge_min_samples``: the samples a rise or fall time needs on its edge (0: none);
      ``shoot``: overshoot and preshoot from the extremes or from the first edge (#1332);
    * ``gate``: [from, to] divisions of the record (0 to 10) the measurement looks at;
    * what cannot be measured answers ``invalid_value`` with the second ``status`` word.

    Every value taken is kept for ``statistics`` (count, mean, sdev, min, max) until cleared.
    """

    label = "自动测量"
    label_en = "Automatic measurements"
    about = "对示波器记录做自动测量。"
    about_en = "Automatic measurements on a scope's records."

    refs = {"record": (RecordSource, "instrument"), "math": (MathSource, "instrument")}
    optional_refs = frozenset({"math"})
    defaults = {
        "record": "acq",  # the acquisition component holding the records (RecordSource: a real-time or sampling one)
        "math": "",  # the math block a FUNCtion<F> source reads from (#1074); empty: channels only
        "source": "",  # the channel measured (a setting such as $measure.source); empty: the command's
        "names": {},  # model name -> [engine measurement, unit], e.g. {AMPL: [VAMPlitude, V]}
        "top_base": "histogram",  # histogram | extremes
        "levels": "percent",  # percent | absolute: what mid is
        "mid": 50.0,  # the crossing level a period is counted on
        "hysteresis": FREQ_HYSTERESIS,  # half the counter's band, of top minus base
        "min_cycles": 1,  # complete cycles a period needs
        "hysteresis_floor_divs": 0.16,  # the counter's band never drops below this fraction of a
        # division of the record's scale (#745): noise alone counts no crossings, so a flat trace
        # of noise measures as nothing, like the real counter's screen-referenced band — an
        # assumed value, no manual states a counter's minimum amplitude (#781); 0 turns it off
        "clipped": "measure",  # measure | invalid: a record at the ADC's limits
        # rise and fall times need this many samples on the edge, one beyond each threshold
        # (the Infiniium's four, #1332); 0: no rule
        "edge_min_samples": 0,
        # overshoot / preshoot: the record's extremes (extremes, InfiniiVision) or the first edge
        # on the screen and its local extreme (first_edge, Infiniium, #1332)
        "shoot": "extremes",
        "gate": None,  # [from, to] divisions of the record; None: all of it
        "invalid_value": "UNDEF",  # what an unmeasurable value answers
        "status": ["OK", "UF"],  # measured, cannot be measured
        "sources": [],  # the two settings DELay?/PHASe? use without sources, e.g. [measure.source, ...]
        "trigger_levels": {},  # channel -> its trigger level setting, for :TRIGger:LEVel:ASETup
        # The screen's measurement list (#373): a measurement command without '?' installs one;
        # past `slots` the oldest leaves. 0: no list (the query only answers).
        "slots": 0,
        "all": [],  # the measurements :MEASure:ALL? answers, in order (engine names)
        # ---- the markers (cursors, #1074): the settings the definition binds the :MARKer words to;
        # a model without them has no marker commands.
        "marker_mode": "",  # the marker mode setting, e.g. $marker.mode: OFF/MANual/XONly/YONly/WAVeform/MEASurement
        "marker_x1": None,  # the Ax marker's x, in the source's x unit (s, or Hz on a transform), e.g. $marker.x1
        "marker_x2": None,  # the Bx marker's x
        "marker_y1": None,  # the Ay marker's y, in the source's vertical unit
        "marker_y2": None,  # the By marker's y
        "marker_source1": "",  # the Ax/Ay markers' source word, e.g. $marker.source1 (CHANnel1, FUNCtion1, …)
        "marker_source2": "",  # the Bx/By markers' source word
        "marker_delta": None,  # whether the deltas are drawn on screen (a bool setting); None: no command
        "marker_measurement": None,  # the measurement the MEASurement mode tracks, 1-based (an int setting)
        "markers": 0,  # how many of the numbered :MARKer<K> markers the model has (1..60); 0: none
        # ---- the markers' units (2000 X-series :MARKer:XUNits/:YUNits, #1155): the words the
        # positions and deltas answer in, and the anchors a :UNits:USE write sets (None: no
        # command, the values pass through unscaled).
        "marker_x_units": "",  # the X cursors' unit word: SEConds/HERTz/DEGRees/PERCent
        "marker_y_units": "",  # the Y cursors' unit word: BASE/PERCent
        "marker_xa1": None,  # the X unit's 0 anchor (a position in the source's x unit)
        "marker_xa2": None,  # the X unit's full anchor (360 degrees, 100 percent)
        "marker_ya1": None,  # the Y unit's 0 anchor
        "marker_ya2": None,  # the Y unit's full anchor
        # The model's measurement thresholds, by setting: "<prefix>.<group><n>.<field>" for channel n,
        # groups "general" (every measurement but rise and fall) and "rfall" (rise and fall times),
        # fields those of measurements.DEFAULT_THRESHOLDS (method, upper, middle, lower, abs_*,
        # hyst_range, hyst_level, tb_method, tb_top, tb_base). Empty: the fixed 10/50/90 % levels.
        "thresholds": "",
        # :MEASure:RESults? without an order: "" oldest first (InfiniiVision), AORDered newest
        # first (the Infiniium: "AORDered (or no option)")
        "results_order": "",
        # what :MEASure:RESults? gives per measurement (a setting such as $measure.statistics): ON
        # (or empty) the label and every statistic; CURRent, MINimum, MAXimum, MEAN, STDDev or
        # COUNt that value alone
        "statistics": "",
        "storage": "",  # the file_system component :DISK:SAVE:MEASurements writes to (empty: none)
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.history: dict[tuple[str, int], list[float]] = {}
        self.installed: list[tuple[str, int]] = []  # the screen's measurements, oldest first (#373)
        self.k_markers: dict[int, dict[str, Any]] = {}  # the numbered markers' state, #1074
        self.names: dict[tuple[str, int], str] = {}  # a measurement's own name (:MEASure:NAME)
        self.screen: list[tuple[str, int]] = []  # top to bottom: the newest first unless moved

    def reset(self) -> None:
        self.history = {}
        self.installed = []
        self.k_markers = {}
        self.names = {}
        self.screen = []

    # ---- the screen's measurement list (#373) ------------------------------------------------------

    def install(self, what: str, n: int) -> None:
        """Put a measurement on the screen: a second install of the same one moves it to the end;
        past ``slots`` the oldest leaves. On screen the newest goes to the top."""
        slots = int(self.param("slots") or 0)
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

    @op("results")
    def op_results(self, order: str = "", **_: Any) -> OpResult:
        """``:MEASure:RESults?``: each installed measurement as ``<label>,<current>,<min>,<max>,<mean>,
        <std dev>,<count>`` — the statistics of every value taken since the list was cleared (the
        current one included). Label: its name, else ``<name>(<channel>)``. Oldest first; ``order``
        AORDered: the newest first, GORDered: the screen's order (top to bottom, after any move).
        Nothing installed: an empty reply."""
        assert self.owner is not None
        word = str(order or self.param("results_order") or "").strip().upper()
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
        assert self.owner is not None
        stat = "" if every else str(self.param("statistics") or "").strip().upper()
        parts: list[bytes] = []
        used = 0
        for what, n in entries:
            value, u = self.value(what, n)
            used += u
            self._keep(what, n, value)
            stats = value_stats(self.history.get((what, n)) or [])
            count = int(stats["count"])
            summary = [stats[k] if count else math.nan for k in ("minimum", "maximum", "mean", "sdev")]
            if stat and not stat.startswith("ON"):
                pick = dict(zip(("CURR", "MIN", "MAX", "MEAN", "STDD"), (value, *summary), strict=True))
                key = next((k for k in pick if stat.startswith(k)), None)
                parts.append(str(count).encode() if key is None else self._number(pick[key]))
                continue
            fields = [self._label(what, n).encode(), self._number(value)]
            fields += [self._number(x) for x in summary]
            fields.append(str(count).encode())
            parts.append(b",".join(fields))
        return parts, used

    @op("store_results")
    def op_store_results(self, name: str = "", **_: Any) -> OpResult:
        """``:DISK:SAVE:MEASurements "<file>"``: the screen's measurements to a CSV file on
        ``storage`` (``.csv`` added): a header line, then one row per measurement, top to bottom —
        label, current, min, max, mean, std dev, count (SimuBench's layout)."""
        assert self.owner is not None
        storage: Any = self.peer(str(self.param("storage")))
        if storage is None:
            raise ScpiError(-300, f"{self.uid}: no storage for measurement files")
        tail = re.split(r"[\\/:]", name)[-1]
        file = name if tail.lower().endswith(".csv") else name + ".csv"
        rows, used = self._result_rows(list(self.screen), every=True)
        text = "Measurement,Current,Min,Max,Mean,Std Dev,Count\r\n" + "".join(
            r.decode() + "\r\n" for r in rows
        )
        storage.store(file, text.encode())
        return OpResult(duration_ns=used)

    def _entry(self, index: Any) -> tuple[str, int]:
        """The screen's measurement at a position from the top (1, or ``MEAS3``)."""
        text = str(index if not isinstance(index, bytes) else V.parse_string(index)).strip().upper()
        digits = text.removeprefix("MEASUREMENT").removeprefix("MEAS")
        if not digits.isdigit() or int(digits) < 1:
            raise ScpiError(-224, f"no measurement {index!r}")
        k = int(digits)
        if k > len(self.screen):
            raise ScpiError(-221, f"measurement {k} is not on the screen")
        return self.screen[k - 1]

    @op("entry")
    def op_entry(
        self,
        query: bool = False,
        what: str = "",
        index: Any = None,
        value: Any = None,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """One measurement of the screen, by its position from the top (``index`` ``MEAS<n>``, or the
        header's ``<n>``): ``name`` (set or read its name, quoted), ``clear`` (take it off),
        ``position`` (move it to another position), ``source`` (read or change its channel)."""
        assert self.owner is not None
        if index is None and suffixes:
            index = next(iter(v for k, v in suffixes.items() if k != "ch"), None)
        entry = self._entry(index)
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
                return OpResult(self._source_word(entry[1]).encode())
            n = self._channel(None, V.parse_string(value) if isinstance(value, bytes) else str(value))
            moved = (entry[0], n)
            self.screen[k] = moved
            self.installed[self.installed.index(entry)] = moved
            if entry in self.names:
                self.names[moved] = self.names.pop(entry)
            return OpResult()
        raise ScpiError(-300, f"{self.uid}: no entry action {what!r}")

    def _number(self, value: float) -> bytes:
        assert self.owner is not None
        if not math.isfinite(value) and self.param("invalid_value") != "UNDEF":
            return str(self.param("invalid_value")).encode()
        return self.owner.format_number(value)

    @op("clear_list")
    def op_clear_list(self, **_: Any) -> OpResult:
        """``:MEASure:CLEar``: no measurements on the screen, their statistics gone."""
        self.installed = []
        self.screen = []
        self.names = {}
        self.history = {}
        return OpResult()

    def clip_flags(self, n: int) -> int:
        """Whether channel n's record reaches the ADC's limits: 1 low, 2 high, 3 both, 0 neither
        (a channel without a record or an unquantized ADC: 0)."""
        acq = self._acq()
        if n < 0:
            return 0  # a math function has no ADC to reach
        try:
            rec, _ = acq.measure_record(n)
        except ScpiError:
            return 0
        if getattr(rec, "clip", None) is not None:
            return int(rec.clip)  # the converter's own, a filter after it notwithstanding (#1262)
        v = np.asarray(rec.volts, dtype=float)
        if not v.size or not acq.adc_unit().param("bits"):
            return 0
        high, low = acq.adc_unit().rails(rec)
        return (1 if v.min() <= low else 0) | (2 if v.max() >= high else 0)

    @op("clipped")
    def op_clipped(self, channel: int | None = None, sides: bool = False, **_: Any) -> OpResult:
        """ADC clipping of the channel's current record: 1 when it reaches either limit, else 0;
        with ``sides`` 1 low, 2 high, 3 both (the Infiniium's :CHANnel<n>:ADC:CLIPped?)."""
        flags = self.clip_flags(channel or 1)  # the header's channel, not the measurement source
        return OpResult(str(flags if sides else int(bool(flags))).encode())

    @op("overload")
    def op_overload(self, **_: Any) -> OpResult:
        """The Overload Event Register (:OVLRegister?): bit n-1 set for each channel n whose input
        went beyond its maximum input range since the last read; the read clears it (#1264)."""
        return OpResult(str(self._acq().overload_bits()).encode())

    @op("all")
    def op_all(self, source: Any = None, channel: int | None = None, **_: Any) -> OpResult:
        """``:MEASure:ALL?``: the current value of every measurement of ``all`` on one source, in
        that order (a snapshot; nothing is installed)."""
        n = self._channel(channel, source)
        used = 0
        parts = []
        for what in self.param("all") or []:
            value, u = self.value(str(what), n)
            used += u
            parts.append(self._number(value))
        return OpResult(b",".join(parts), used)

    def _acq(self) -> Any:
        assert self.owner is not None
        return self.ref("record")  # type: ignore[return-value]

    @staticmethod
    def _is_source(word: str) -> bool:
        """Whether a parameter word names a source: a channel (CHANnel<n>) or a math function
        (FUNCtion<F>, FUNC<F>, F<F>)."""
        return word.strip().upper().startswith("CH") or function_index(word) is not None

    def _channel(self, channel: int | None, source: Any = None) -> int:
        """The source key a measurement reads: channel n as n, math function F as -F (#1074) —
        the key the statistics, the screen's list and the labels carry."""
        if isinstance(source, list):  # raw parameters: [<interval>][,<type>][,<source>]
            words = [V.parse_string(b).strip().upper() for b in source]
            source = next((w for w in words if self._is_source(w)), None)
        if source:
            name = (source.decode() if isinstance(source, bytes) else str(source)).strip().upper()
            f = function_index(name)
            if f is not None:
                return -f
            m = name.removeprefix("CHANNEL").removeprefix("CHAN").removeprefix("C")
            if not m.isdigit():
                raise ScpiError(-224, f"unknown source {source!r}")
            return int(m)
        bound = self.param("source")
        if bound not in (None, ""):
            return self._key(bound)
        return channel or 1

    @staticmethod
    def _key(word: Any) -> int:
        """A setting's source word as a source key (CHANnel2, CH2, C2 -> 2; FUNCtion3 -> -3)."""
        f = function_index(word)
        return -f if f is not None else channel_index(word)

    @staticmethod
    def _source_word(n: int) -> str:
        """A source key's short word: CHAN<n>, or FUNC<F> for a function."""
        return f"FUNC{-n}" if n < 0 else f"CHAN{n}"

    def record(self, n: int) -> tuple[Any, int]:
        """The record a measurement on source key ``n`` reads: channel n's acquisition, or math
        function -n's waveform over its operands (#1074) — a model without math functions refuses
        the source (-224), a function without an operator is -230."""
        if n >= 0:
            return self._acq().measure_record(n)
        block = self.ref("math")
        if block is None or not isinstance(block, MathSource):
            raise ScpiError(-224, f"FUNCtion{-n}: this model has no math functions to measure")
        rec, used = block.read_function(-n)
        if rec is None:
            raise ScpiError(-230, f"function {-n} is not defined")
        return rec, used

    def value(self, what: str, n: int) -> tuple[float, int]:
        """The engine measurement ``what`` on source n's record (NaN: cannot be measured), and the
        virtual time getting the record took."""
        rec, used = self.record(n)
        return self.record_value(what, rec), used

    def screen_values(self, records: Mapping[int, Record]) -> list[dict[str, Any]]:
        """The screen's measurement panel (the :class:`~simubench.engine.component.ScreenMeasurer`
        protocol), one row per record by channel: the engine measurements the screen shows, by the
        view's key, each ``None`` where they cannot be measured. A deep record's measurements read
        the record (#1012) and the screen does not — its row carries the channel alone."""
        out = []
        for n, rec in sorted(records.items()):
            row: dict[str, Any] = {"channel": n}
            if rec.deep is None:
                for key, what in SCREEN_MEASUREMENTS.items():
                    value = float(self.record_value(what, rec))
                    row[key] = value if math.isfinite(value) else None
            out.append(row)
        return out

    @staticmethod
    def _impedance(value: Any) -> float:
        """A dBm result's reference impedance: AUTO (or none) 50 Ω, else 10 to 200 Ω."""
        if isinstance(value, list):  # a raw parameter: the words after the units
            value = value[0] if value else None
        if value is None:
            return 50.0
        word = (V.parse_string(value) if isinstance(value, bytes) else str(value)).strip()
        if word.upper().startswith("AUTO"):
            return 50.0
        ohms = V.parse_float(word.encode(), "OHM")
        if not 10.0 <= ohms <= 200.0:
            raise ScpiError(-222, "the reference impedance is 10 to 200 ohms")
        return ohms

    def record_value(self, what: str, rec: Any) -> float:
        """The engine measurement ``what`` on a record by this model's rules (gate, clipping, top
        and base, crossing level): what :MEASure? answers, and what the screen's measurement
        panel shows for the record on screen (#280). A ``:DBM<ohms>`` suffix gives an RMS as dBm
        into that impedance."""
        base, dbm, ohms = what.partition(":DBM")
        if dbm:
            volts = self._record_value(base, rec)
            if not math.isfinite(volts) or volts <= 0:
                return math.nan
            return 10.0 * math.log10(volts * volts / float(ohms) / 1e-3)
        return self._record_value(what, rec)

    def _record_value(self, what: str, rec: Any) -> float:
        acq = self._acq()
        if rec.points > MAX_FRAME:
            return self._deep_value(what, rec)
        v = np.asarray(rec.volts, dtype=float)
        gate = self.param("gate")
        if gate:
            lo, hi = (max(0, min(v.size, round(float(g) / 10 * v.size))) for g in gate)
            v = v[lo:hi]
        if v.size and str(self.param("clipped")) == "invalid" and rec.channel > 0:  # a function: no ADC
            rails = acq.adc_unit().rails(rec)
            if acq.adc_unit().param("bits") and (v.max() >= rails[0] or v.min() <= rails[1]):
                return math.nan
        if what in TIME_MEASUREMENTS:
            return self._time_value(what, v, rec)
        if self.param("thresholds"):
            gen = self.levels(v, rec, "general")
            if gen is None:
                return math.nan  # HISTONLY on a record whose histogram shows no levels
            return measure(
                what,
                v,
                rec.dt_s,
                min_cycles=int(self.param("min_cycles")),
                general=gen,
                rise_fall=self.levels(v, rec, "rfall"),
                edge_min_samples=int(self.param("edge_min_samples")),
                shoot=str(self.param("shoot")),
            )
        mid = None
        if v.size and what in ("FREQuency", "PERiod"):
            m = float(self.param("mid"))
            if str(self.param("levels")) == "absolute":
                mid = m
            elif m != 50.0:
                levels = top_base(v) if self.param("top_base") == "histogram" else None
                top, base = levels or (float(v.max()), float(v.min()))
                mid = base + m / 100 * (top - base)
        value = measure(
            what,
            v,
            rec.dt_s,
            top_base=str(self.param("top_base")),
            mid=mid,
            hysteresis=float(self.param("hysteresis")),
            min_cycles=int(self.param("min_cycles")),
            min_band=self._band_floor(rec),
            edge_min_samples=int(self.param("edge_min_samples")),
            shoot=str(self.param("shoot")),
        )
        return value

    def _deep_value(self, what: str, rec: Record) -> float:
        """A measurement on a record too deep to hold in memory (#1012): those that reduce it chunk
        by chunk (``CHUNKED_MEASUREMENTS``) read it in chunks; the others need every sample at once
        and are refused as beyond the model."""
        if what not in CHUNKED_MEASUREMENTS:
            raise beyond_model(
                f"{what} on a record of {rec.points} points needs the whole record in memory "
                f"(at most {MAX_FRAME} points)"
            )
        lo, hi = 0, rec.points
        gate = self.param("gate")
        if gate:
            lo, hi = (max(0, min(rec.points, round(float(g) / 10 * rec.points))) for g in gate)
        acq = self._acq()
        if str(self.param("clipped")) == "invalid" and rec.channel > 0 and acq.adc_unit().param("bits"):
            rails = acq.adc_unit().rails(rec)
            if (
                chunked_value("VMAX", rec, lo, hi) >= rails[0]
                or chunked_value("VMIN", rec, lo, hi) <= rails[1]
            ):
                return math.nan
        return chunked_value(what, rec, lo, hi)

    def threshold_spec(self, n: int, group: str) -> dict[str, Any]:
        """Channel n's threshold set ``group`` (general / rfall) from the settings ``thresholds``
        names; a field without a setting keeps the default."""
        assert self.owner is not None
        prefix = str(self.param("thresholds"))
        out: dict[str, Any] = {}
        for key in DEFAULT_THRESHOLDS:
            path = f"{prefix}.{group}{n}.{key}"
            if path in self.owner.specs:
                out[key] = self.owner.get_state(path)
        return out

    def levels(self, v: Any, rec: Any, group: str = "general") -> Levels | None:
        """The record's top, base and reference levels by its channel's threshold set (or the
        fixed 90/50/10 % without ``thresholds``)."""
        spec = self.threshold_spec(rec.channel, group) if self.param("thresholds") else {}
        return threshold_levels(
            np.asarray(v, dtype=float), spec, float(self.param("hysteresis")), self._band_floor(rec)
        )

    def _crossings_of(self, v: Any, rec: Any, position: str = "MIDDle") -> list[tuple[float, int]]:
        """The record's crossings of a reference level: the threshold set's with ``thresholds``,
        else the middle as before."""
        if not self.param("thresholds"):
            return mid_crossings(v, rec.dt_s, float(self.param("hysteresis")))
        lv = self.levels(v, rec, "general")
        if lv is None:
            return []
        return crossings(np.asarray(v, dtype=float), rec.dt_s, lv.at(position), lv.band)

    def _band_floor(self, rec: Any) -> float:
        """The crossing band's absolute floor: divisions of the record's scale (#745)."""
        return float(self.param("hysteresis_floor_divs")) * rec.scale

    def _time_value(self, what: str, v: np.typing.NDArray[np.float64], rec: Any) -> float:
        """XMAX/XMIN: the time of the first maximum / minimum, from the trigger; BRATe: the bit rate,
        1 / the narrowest pulse (positive or negative) at the middle level (#350)."""
        if v.size == 0:
            return math.nan
        if what in ("XMAX", "XMIN"):
            k = int(np.argmax(v) if what == "XMAX" else np.argmin(v))
            return rec.origin + k * rec.dt_s
        cross = mid_crossings(v, rec.dt_s, float(self.param("hysteresis")), self._band_floor(rec))
        widths = [u - t for (t, a), (u, b) in itertools.pairwise(cross) if b == -a and u > t]
        return 1.0 / min(widths) if widths else math.nan

    def _sources(self, raw: list[bytes] | None) -> tuple[list[str], list[int]]:
        """Raw parameters split into the arguments and the channels named (CHANnel<n>). An item may
        also be a setting's value (a number or a word, op args ``[$bookmark.time, $bookmark.source]``)."""
        words = [
            (
                V.parse_string(b) if isinstance(b, bytes) else repr(b) if isinstance(b, float) else str(b)
            ).strip()
            for b in raw or []
        ]
        channels = [self._channel(None, w) for w in words if self._is_source(w)]
        return [w for w in words if not self._is_source(w)], channels

    def _reply(self, value: float, used: int) -> OpResult:
        assert self.owner is not None
        if not math.isfinite(value) and self.param("invalid_value") != "UNDEF":
            return OpResult(str(self.param("invalid_value")).encode(), used)
        return OpResult(self.owner.format_number(value), used)

    @op("at")
    def op_at(
        self, what: str = "", source: list[bytes] | None = None, channel: int | None = None, **_: Any
    ) -> OpResult:
        """Measurements with arguments (#350), times from the trigger:

        * ``VTIMe? <time>[,<source>]`` — the voltage at that time (linear between samples);
        * ``TEDGe? <slope><occurrence>[,<source>]`` — the time of that edge at the middle level;
        * ``TVALue? <value>[,<slope><occurrence>][,<source>]`` — the time the record crosses
          ``value`` that way.
        """
        args, channels = self._sources(source)
        if any(a.upper() == "NONE" for a in args):  # a bookmark on no waveform has no value
            return self._reply(math.nan, 0)
        n = channels[0] if channels else self._channel(channel)
        if channels:
            self._follow(n, self._source_word(n))
        rec, used = self.record(n)
        dt, origin = rec.dt_s, rec.origin
        if what == "VTIMe":
            if not args:
                raise ScpiError(-109, "VTIMe? takes a time")
            x = (V.parse_float(args[0].encode(), "S") - origin) / dt
            size = rec.points
            if size == 0 or not 0 <= x <= size - 1:
                return self._reply(math.nan, used)
            k = min(int(x), size - 2) if size > 1 else 0
            frac = x - k
            pair = rec.span(k, k + 2)  # the two samples around the time: a deep record computes no more
            value = float(pair[0]) if size == 1 else float(pair[0] + (pair[1] - pair[0]) * frac)
            return self._reply(value, used)
        v = np.asarray(rec.volts, dtype=float)
        if what == "TEDGe":
            position = "MIDDle"
            if args and args[0].strip().upper().startswith(("UPP", "MIDD", "LOW")):
                position, args = args[0], args[1:]  # the Infiniium's <meas_thres_txt> first
            sign, occurrence = edge_spec(args[0] if args else None)
            cross = [t for t, d in self._crossings_of(v, rec, position) if d == sign]
        elif what == "TVALue":
            if not args:
                raise ScpiError(-109, "TVALue? takes a value")
            level = V.parse_float(args[0].encode(), "V")
            sign, occurrence = edge_spec(args[1] if len(args) > 1 else None)
            span = float(v.max() - v.min()) if v.size else 0.0
            cross = [
                t for t, d in crossings(v, dt, level, float(self.param("hysteresis")) * span) if d == sign
            ]
        else:
            raise ScpiError(-224, f"measurement {what!r} takes no arguments here")
        value = origin + cross[occurrence - 1] if len(cross) >= occurrence else math.nan
        return self._reply(value, used)

    @op("pair")
    def op_pair(
        self,
        what: str = "",
        source: list[bytes] | None = None,
        define: list[Any] | None = None,
        **_: Any,
    ) -> OpResult:
        """``DELay? [<source1>][,<source2>]``: the time from source 1's first rising edge to source 2's
        first rising edge at their middle levels; ``PHASe?``: that delay over source 1's period, in
        degrees folded into -180..+180 (#350), with a trailing RISing / FALLing taking that edge on both. Without sources,
        :MEASure:SOURce's two (``sources``). ``CROSsing?``: the voltage where the two records cross
        (nearest the centre). ``DELTatime?`` (the Infiniium's): from source 1's edge ``define`` names
        to source 2's — ``define`` = [start direction, number, position, stop direction, number,
        position] (RISing / FALLing / EITHer, from 1, UPPer / MIDDle / LOWer); one source: both on it."""
        assert self.owner is not None
        words, channels = self._sources(source)
        if what == "DELTatime" and len(channels) == 1:
            channels = channels * 2
        if len(channels) < 2:
            pair = [self._key(self.owner.get_state(p)) for p in (self.param("sources") or [])]
            channels = (channels + pair[len(channels) :])[:2]
        if len(channels) < 2:
            raise ScpiError(-109, f"{what}? needs two sources")
        direction = next((w.upper() for w in words if w.upper().startswith(("RIS", "FALL"))), "RISING")
        sign = -1 if direction.startswith("FALL") else 1
        hyst = float(self.param("hysteresis"))
        recs, used = [], 0
        for n in channels:
            rec, u = self.record(n)
            used += u
            recs.append(rec)
        if what == "CROSsing":
            a, b = (np.asarray(r.volts, dtype=float) for r in recs)
            return self._reply(crossing_voltage(a, b), used)
        if what == "DELTatime":
            d = list(define or ["RISing", 1, "MIDDle", "RISing", 1, "MIDDle"])
            times = []
            for rec, (dirn, number, position) in zip(recs, (d[0:3], d[3:6]), strict=True):
                want = str(dirn).upper()
                edges = [
                    t
                    for t, e in self._crossings_of(np.asarray(rec.volts, dtype=float), rec, str(position))
                    if want.startswith("EITH") or e == (-1 if want.startswith("FALL") else 1)
                ]
                k = int(number)
                times.append(rec.origin + edges[k - 1] if len(edges) >= k else math.nan)
            return self._reply(times[1] - times[0], used)
        times = []
        for rec in recs:
            v = np.asarray(rec.volts, dtype=float)
            edges = [t for t, e in self._crossings_of(v, rec) if e == sign]
            times.append(rec.origin + edges[0] if edges else math.nan)
        delay = times[1] - times[0]
        if what == "DELay":
            return self._reply(delay, used)
        first = np.asarray(recs[0].volts, dtype=float)
        period = measure("PERiod", first, recs[0].dt_s, hysteresis=hyst)
        if not (math.isfinite(period) and period > 0):
            return self._reply(math.nan, used)
        # folded into one period, -180..+180 degrees: the first edges may lie whole periods apart
        return self._reply((delay / period * 360.0 + 180.0) % 360.0 - 180.0, used)

    @op("auto_thresholds")
    def op_auto_thresholds(self, source: Any = None, channel: int | None = None, **_: Any) -> OpResult:
        """The Infiniium's :MEASure:THResholds:GENAUTO: the source's general hysteresis threshold set
        from its record — the level halfway between top and base, the range the counter's band
        (``hysteresis`` of top minus base, never below the band floor)."""
        assert self.owner is not None
        if not self.param("thresholds"):
            raise ScpiError(-300, f"{self.uid}: thresholds names no settings")
        n = self._channel(channel, source)
        if n < 0:
            raise ScpiError(-224, "the threshold sets are per channel")
        rec, used = self.record(n)
        v = np.asarray(rec.volts, dtype=float)
        lv = threshold_levels(
            v, {"method": "PERCent"}, float(self.param("hysteresis")), self._band_floor(rec)
        )
        if lv is None:
            return OpResult(duration_ns=used)
        prefix = str(self.param("thresholds"))
        self.owner.set_state(f"{prefix}.general{n}.hyst_level", lv.middle)
        self.owner.set_state(f"{prefix}.general{n}.hyst_range", lv.band)
        return OpResult(duration_ns=used)

    @op("with_args")
    def op_with_args(self, what: str = "", source: list[bytes] | None = None, **_: Any) -> OpResult:
        """Measurements that take a number besides the source (the Infiniium's): ``PTOP? <source>,<%>``
        / ``PBASe?`` — the mean of the first positive / negative pulse within the centred window of
        that percent of its width; ``BWIDth? <source>,<idle>``, ``BPERiod?``, ``BINTerval?`` — the
        first complete burst's width, the start-to-start and the end-to-start time of the first two
        (bursts are runs of middle crossings closer than the idle time)."""
        words, channels = self._sources(source)
        n = channels[0] if channels else self._channel(None)
        if not words:
            raise ScpiError(-109, f"{what}? takes a number after the source")
        number = V.parse_float(words[0].encode(), "S" if what.startswith("B") else None)
        rec, used = self.record(n)
        v = np.asarray(rec.volts, dtype=float)
        lv = self.levels(v, rec, "general")
        if lv is None or v.size == 0:
            return self._reply(math.nan, used)
        if what in ("PTOP", "PBASe"):
            return self._reply(pulse_window_mean(v, rec.dt_s, lv, number, what == "PTOP"), used)
        if what in ("BWIDth", "BPERiod", "BINTerval"):
            if number <= 0:
                raise ScpiError(-222, "the idle time must be positive")
            return self._reply(burst_measure(what, v, rec.dt_s, lv, number), used)
        raise ScpiError(-224, f"measurement {what!r} takes no number here")

    @op("auto_trigger_levels")
    def op_auto_trigger_levels(self, source_only: bool = False, **_: Any) -> OpResult:
        """``:TRIGger:LEVel:ASETup``: every displayed channel's trigger level to its waveform's 50 %
        (halfway between top and base; 0 V with AC coupling) — the levels of ``trigger_levels``,
        channel -> setting (#350). ``source_only`` (the Infiniium's ``:TRIGger:LEVel:FIFTy``): the
        level of the channel the trigger takes its source from now, whatever the trigger type."""
        assert self.owner is not None
        acq = self._acq()
        only = None
        if source_only:
            trig = getattr(acq, "ref", lambda _n: None)("trigger")
            only = trigger_channel(trig.param("source")) if trig is not None else None
            if only is None:
                return OpResult()  # an external or line source: no waveform to take 50 % of
        for key, path in (self.param("trigger_levels") or {}).items():
            n = int(key)
            if (n != only) if only is not None else (n not in acq.displayed()):
                continue  # FIFTy: the trigger source's level, shown on screen or not
            coupling = str(self.owner.state.get(f"ch{n}.coupling", "DC")).upper()
            if coupling.startswith("AC"):
                level = 0.0
            else:
                rec, _ = acq.measure_record(n)
                v = np.asarray(rec.volts, dtype=float)
                levels = top_base(v) if v.size else None
                top, base = levels if levels else ((float(v.max()), float(v.min())) if v.size else (0.0, 0.0))
                level = (top + base) / 2
            self.owner.set_state(str(path), level)
        return OpResult()

    def _follow(self, n: int, source: Any) -> None:
        """A measurement that names its source makes it the default one (the bound ``source``)."""
        path = self.bound_path("source")
        if not source or path is None or self.owner is None:
            return
        options = self.owner.specs[path].options or []
        match = next(
            (o for o in options if (function_index(o) == -n if n < 0 else trigger_channel(o) == n)), None
        )
        if match is not None:
            self.owner.set_state(path, match)

    def _keep(self, name: str, n: int, value: float) -> None:
        if math.isfinite(value):
            self.history.setdefault((name, n), []).append(value)

    @op("measure")
    def measure(
        self,
        what: str = "",
        source: Any = None,
        channel: int | None = None,
        query: bool = True,
        interval: str | None = None,
        direction: str | None = None,
        coupling: str | None = None,
        units: str | None = None,
        impedance: Any = None,
        **_: Any,
    ) -> OpResult:
        """``:MEASure:VPP? [CHANnel1]``: the engine measurement ``what`` (a constant argument of the
        command) in the definition's number format; what cannot be measured is NaN's sentinel.
        Without '?' (``:MEASure:VPP CHANnel1``) the measurement goes on the screen's list (#373).
        For the level measurements (``LEVEL_MEASUREMENTS``) an ``interval`` CYCLe and a ``direction``
        FALLing parameter select their variants (``AREA:CYCLe``, ``VOVershoot:FALLing``); for
        VAVerage and VRMS the CYCLe interval is the first period on screen, a ``coupling`` AC takes
        the DC component out (``VRMS:CYCLe:AC``), ``units`` DBM gives the power into ``impedance``
        ohms (AUTO or none: 50 Ω, the guide's reference for single-ended channels and functions)."""
        assert self.owner is not None
        if interval and str(interval).upper().startswith("CYCL") and f"{what}:CYCLe" in LEVEL_MEASUREMENTS:
            what += ":CYCLe"
        if coupling and str(coupling).upper().startswith("AC") and f"{what}:AC" in LEVEL_MEASUREMENTS:
            what += ":AC"
        if what in LEVEL_MEASUREMENTS and direction and str(direction).upper().startswith("FALL"):
            what += ":FALLing"
        if units and str(units).upper().startswith("DBM"):
            what += f":DBM{self._impedance(impedance):g}"
        n = self._channel(channel, source)
        self._follow(n, source)
        if not query:
            self.install(what, n)
            return OpResult()
        value, used = self.value(what, n)
        self._keep(what, n, value)
        if not math.isfinite(value) and self.param("invalid_value") != "UNDEF":
            return OpResult(str(self.param("invalid_value")).encode(), used)
        return OpResult(self.owner.format_number(value), used)  # NaN: the SCPI sentinel 9.91E+37

    @op("named", fields=("kind", "unit", "status"))
    def named(self, channel: int | None = None, kind: str | None = None, **_: Any) -> OpResult:
        """A measurement by the model's name (``names``): the value, or ``invalid_value`` when it
        cannot be measured; the reply template also gets {kind}, {unit} and {status}, e.g.
        '{header} {kind},{value} {unit},{status}'."""
        assert self.owner is not None
        if kind is None:
            raise ScpiError(-109)
        table = {str(k).upper(): v for k, v in (self.param("names") or {}).items()}
        name = kind.strip().upper()
        if name not in table:
            raise ScpiError(-224, f"measurement {kind} not implemented in this definition")
        engine_name, unit = table[name]
        n = self._channel(channel)
        value, used = self.value(str(engine_name), n)
        self._keep(name, n, value)
        good, bad = self.param("status")
        ok = math.isfinite(value)
        text = self.owner.format_number(value) if ok else str(self.param("invalid_value")).encode()
        return OpResult(text, used, {"kind": name, "unit": str(unit), "status": str(good if ok else bad)})

    @op("statistics")
    def statistics(
        self, kind: str | None = None, item: str = "mean", channel: int | None = None, **_: Any
    ) -> OpResult:
        """The statistics of the values taken so far of a measurement: ``count``, ``mean``,
        ``sdev``, ``min`` or ``max`` (``item``, a constant argument or a parameter)."""
        assert self.owner is not None
        if kind is None:
            raise ScpiError(-109)
        table = {str(k).upper(): v for k, v in (self.param("names") or {}).items()}
        name = kind.strip().upper()
        n = self._channel(channel)
        stats = value_stats(
            self.history.get((name, n)) or self.history.get((str(table.get(name, [name])[0]), n)) or []
        )
        if item == "count":
            return OpResult(str(stats["count"]).encode())
        if not stats["count"]:
            return OpResult(str(self.param("invalid_value")).encode())
        key = {"mean": "mean", "sdev": "sdev", "min": "minimum", "max": "maximum"}.get(item)
        if key is None:
            raise ScpiError(-224, f"no statistic {item!r}")
        return OpResult(self.owner.format_number(stats[key]))

    @op("clear_statistics")
    def clear_statistics(self, **_: Any) -> OpResult:
        self.history = {}
        return OpResult()

    # ---- the markers (cursors, #1074) ------------------------------------------------------------

    #: What a marker mode word stands for, by its first letters (the settings hold SCPI words).
    MODES = {
        "OFF": "off",
        "MAN": "manual",
        "XON": "x_only",
        "YON": "y_only",
        "WAV": "waveform",
        "MEAS": "measurement",
    }

    def _marker_mode(self) -> str:
        """The marker mode as one of ``MODES``' values, ``off`` when the model has none."""
        word = str(self.param("marker_mode") or "").upper()
        for key, mode in self.MODES.items():
            if word.startswith(key):
                return mode
        return "off"

    def _marker_record(self, which: int) -> tuple[Any, int]:
        """The record the A (``which`` 1) or B (2) markers read: their source's, a channel's or a
        math function's (#1074). A numberless ``FUNCtion`` — or its alias ``MATH`` (2000 X-series
        Programmer's Guide 02.66 p. 361) — names the one function a single-function layout has."""
        return self._source_record(str(self.param(f"marker_source{which}") or ""))

    def _source_record(self, word: str) -> tuple[Any, int]:
        """The record a marker's source word names (see ``_marker_record``); empty: channel 1."""
        f = function_index(word) if word else None
        if f is None and word.strip().upper() in ("FUNCTION", "FUNC", "F", "MATH"):
            f = 1
        if f is not None:
            block = self.ref("math")
            if block is None or not isinstance(block, MathSource):
                raise ScpiError(-224, f"{word}: this model has no math functions to mark")
            return block.read_function(f)  # type: ignore[return-value]
        n = self._channel(None, word.encode() if word else None)
        return self._acq().measure_record(n)  # type: ignore[return-value]

    def _value_at(self, rec: Any, x: float) -> float:
        """The record's value at x (its grid's unit — seconds, or Hz on a transform), the sample
        nearest x: the markers of the real instrument read the point the screen draws."""
        if rec.points < 1:
            return math.nan
        k = (x - rec.origin) / rec.dt_s
        i = int(min(max(round(k), 0), rec.points - 1))
        return float(rec.span(i, i + 1)[0])

    def _marker_y(self, which: int) -> tuple[float, int]:
        """The A (1) or B (2) marker's y: in the manual modes its setting, in the waveform mode
        the value its source's record has at the marker's x; the measurement mode tracks the
        measurement ``marker_measurement`` names."""
        mode = self._marker_mode()
        if mode in ("off", "x_only"):
            return math.nan, 0
        if mode == "measurement":
            n = self.param("marker_measurement")
            if n in (None, ""):
                # no tracked-measurement command: the cursors track the most recent measurement
                # on screen (the 2000 X-series semantics, Programmer's Guide 02.66 p. 359)
                if not self.installed:
                    return math.nan, 0
                what, channel = self.installed[-1]
                return self.value(what, channel)
            which_meas = int(n)
            if 1 <= which_meas <= len(self.installed):
                what, channel = self.installed[which_meas - 1]
                return self.value(what, channel)
            return math.nan, 0
        if mode == "waveform":
            rec, used = self._marker_record(which)
            x = self.param(f"marker_x{which}")
            return self._value_at(rec, float(x) if x is not None else 0.0), used
        y = self.param(f"marker_y{which}")  # manual and y_only: the marker is its setting
        return (float(y) if y is not None else math.nan), 0

    def _marker_word(self, mode: str) -> str:
        """The SCPI word a mode's setting carries, as the definition's options spell it."""
        assert self.owner is not None
        path = self.bound_path("marker_mode")
        if path is None:
            return mode
        for option in self.owner.specs[path].options or []:
            if option.upper().startswith(mode[:3]):
                return str(option)
        return mode

    @op("cursor")
    def cursor(self, which: str = "STARt", query: bool = True, **_: Any) -> OpResult:
        """``:MARKer:CURSor? {DELTa | STARt | STOP}``: the tracked positions as ``<x>,<y>``.
        The query also puts the markers in the waveform-tracking mode, whatever they were (the
        manual's caution: it changes the mode)."""
        assert self.owner is not None
        word = str(which).strip().upper()
        if not word.startswith(("DEL", "STA", "STO")):
            raise ScpiError(-224, f"{which}: DELTa, STARt or STOP")
        mode_path = self.bound_path("marker_mode")
        if self._marker_mode() != "waveform" and mode_path is not None:
            self.owner.set_state(mode_path, self._marker_word("WAV"), source="scpi", internal=True)
        if word.startswith("DEL"):
            x1, x2 = (float(self.param(k) or 0.0) for k in ("marker_x1", "marker_x2"))
            ya, yb, used = self._marker_ys()
            return OpResult(b",".join(self.owner.format_number(v) for v in (x2 - x1, yb - ya)), used)
        n = 1 if word.startswith("STA") else 2
        y, used = self._marker_y(n)
        x = float(self.param(f"marker_x{n}") or 0.0)
        return OpResult(b",".join(self.owner.format_number(v) for v in (x, y)), used)

    @op("x_delta")
    def x_delta(self, query: bool = True, **_: Any) -> OpResult:
        """``:MARKer:XDELta?``: Bx − Ax, in the X cursors' units — the value at each cursor in
        that unit, minus (the 2000 X-series manual's Xdelta; #1155)."""
        assert self.owner is not None
        x1, x2 = (float(self.param(k) or 0.0) for k in ("marker_x1", "marker_x2"))
        shown = self._marker_diff("x", x1, x2)
        if not math.isfinite(shown):
            return OpResult(str(self.param("invalid_value")).encode())
        return OpResult(self.owner.format_number(shown))

    @op("y_delta")
    def y_delta(self, query: bool = True, **_: Any) -> OpResult:
        """``:MARKer:YDELta?``: By − Ay — the markers' settings, or the values at their x in the
        tracking modes."""
        assert self.owner is not None
        ya, yb, used = self._marker_ys()
        value = self._marker_diff("y", ya, yb)
        if not math.isfinite(value) and self.param("invalid_value") != "UNDEF":
            return OpResult(str(self.param("invalid_value")).encode(), used)
        return OpResult(self.owner.format_number(value), used)

    def _marker_ys(self) -> tuple[float, float, int]:
        """Both markers' y and the virtual time reading them took. In the MEASurement mode both
        sit on the one result of the measurement they track: it is taken once, so their
        difference is that result's, not two acquisitions' noise apart."""
        if self._marker_mode() == "measurement":
            y, used = self._marker_y(1)
            return y, y, used
        ya, ua = self._marker_y(1)
        yb, ub = self._marker_y(2)
        return ya, yb, ua + ub

    # ---- the markers' units (2000 X-series :MARKer:XUNits/:YUNits, #1155) ------------------------

    XUNITS = {"SEC": "s", "HERT": "hz", "DEGR": "deg", "PERC": "pct"}
    YUNITS = {"BASE": "base", "PERC": "pct"}

    def _marker_units(self, axis: str) -> str:
        """The X or Y cursors' unit as this block spells it, from the setting's word."""
        word = str(self.param(f"marker_{axis}_units") or "").upper()
        table = self.XUNITS if axis == "x" else self.YUNITS
        for key, unit in table.items():
            if word.startswith(key):
                return unit
        return table[next(iter(table))]  # the first: seconds, or base units

    def _marker_scaled(self, axis: str, value: float) -> float:
        """A position in the axis's own units expressed in its cursors' unit: the degrees or
        percent from the unit's 0 anchor (:MARKer:XUNits:USE, :MARKer:YUNits:USE set the
        anchors); without anchors the value passes through (the unit has nothing to scale
        against). A difference between two positions scales the same way without the anchor's
        offset — see ``_marker_diff``."""
        if self._marker_units(axis) not in ("deg", "pct"):
            return value
        a1 = self.param(f"marker_{axis}a1")
        a2 = self.param(f"marker_{axis}a2")
        if a1 is None or a2 is None or float(a2) == float(a1):
            return value
        span = float(a2) - float(a1)
        full = 360.0 if self._marker_units(axis) == "deg" else 100.0
        return full * (value - float(a1)) / span

    def _marker_diff(self, axis: str, one: float, two: float) -> float:
        """The cursors' difference in their unit: the value at each cursor in the unit, minus —
        the 2000 X-series manual's Xdelta/Ydelta (degrees and percent scale the difference)."""
        unit = self._marker_units(axis)
        if unit == "s" or unit == "base":
            return two - one
        if unit == "hz":
            if one <= 0.0 or two <= 0.0:
                return math.nan
            return 1.0 / two - 1.0 / one
        a1 = self.param(f"marker_{axis}a1")
        a2 = self.param(f"marker_{axis}a2")
        if a1 is None or a2 is None or float(a2) == float(a1):
            return two - one
        span = float(a2) - float(a1)
        full = 360.0 if unit == "deg" else 100.0
        return full * (two - one) / span

    @op("x_position", fields=("which",))
    def op_x_position(self, which: str = "X1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        """``:MARKer:X1Position`` / ``:MARKer:X2Position``: the cursor's position. A write puts
        the cursors in the MANual mode unless they track (the manual); the input and the reply
        are in the X cursors' units (:MARKer:XUNits)."""
        return self._position(which, value, query, "x")

    @op("y_position", fields=("which",))
    def op_y_position(self, which: str = "Y1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        """``:MARKer:Y1Position`` / ``:MARKer:Y2Position``: the cursor's position. A write is
        refused while the cursors track (the manual: Y positions cannot be set in the WAVeform
        mode) and otherwise puts them in the MANual mode."""
        return self._position(which, value, query, "y")

    @op("tracked_y", fields=("which",))
    def op_tracked_y(self, which: str = "Y1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        """The Infiniium's ``:MARKer:Y1Position`` / ``:MARKer:Y2Position`` (and VSTArt/VSTOp): the
        query answers "the level at the marker position" — where the marker is: in the WAVeform
        mode the source's value at the marker's x, in the MEASurement mode the level of the
        measurement it tracks (as ``:MARKer:YDELta?`` reads it), otherwise the setting; nothing to
        track answers the invalid value. A write stores the setting and leaves the mode alone (the
        guide names no mode change, unlike the 2000 X-series' ``y_position``)."""
        assert self.owner is not None
        n = 1 if str(which).strip().upper().endswith("1") else 2
        param = f"marker_y{n}"
        if query:
            if self._marker_mode() in ("waveform", "measurement"):
                shown, used = self._marker_y(n)
                if not math.isfinite(shown):
                    return OpResult(str(self.param("invalid_value")).encode(), used)
                return OpResult(self.owner.format_number(shown), used)
            stored = self.param(param)
            return OpResult(self.owner.format_number(float(stored) if stored is not None else 0.0))
        path = self.bound_path(param)
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the {param} setting is not bound")
        self.owner.set_state(path, self._marker_number("y", value), source="scpi", internal=True)
        return OpResult()

    def _position(self, which: str, value: Any, query: bool, axis: str) -> OpResult:
        """One X or Y cursor's read or write, in the cursors' unit, with the mode moves the
        manual gives a position write (MANual unless tracking; a tracking Y is refused)."""
        assert self.owner is not None
        n = 1 if str(which).strip().upper().endswith("1") else 2
        param = f"marker_{axis}{n}"
        mode_path = self.bound_path("marker_mode")
        mode = self._marker_mode()
        if query:
            if mode == "off":
                raise ScpiError(-222, "the cursors are off: their positions are not defined")
            used = 0
            if axis == "y" and mode in ("waveform", "measurement"):
                shown, used = self._marker_y(n)
                if not math.isfinite(shown) and self.param("invalid_value") != "UNDEF":
                    return OpResult(str(self.param("invalid_value")).encode(), used)
            else:
                shown = float(self.param(param) if self.param(param) is not None else 0.0)
            unit = self._marker_units(axis)
            if unit == "hz":
                if shown <= 0.0:
                    return OpResult(str(self.param("invalid_value")).encode(), used)
                shown = 1.0 / shown
            elif unit in ("deg", "pct"):
                shown = self._marker_scaled(axis, shown)
            return OpResult(self.owner.format_number(shown), used)
        if axis == "y" and mode == "waveform":
            raise ScpiError(-222, "the cursors track the waveform: Y positions cannot be set")
        if mode != "waveform" and mode_path is not None:
            self.owner.set_state(mode_path, self._marker_word("MAN"), source="scpi", internal=True)
        path = self.bound_path(param)
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the {param} setting is not bound")
        number = self._marker_number(axis, value)
        self.owner.set_state(path, number, source="scpi", internal=True)
        return OpResult()

    def _marker_number(self, axis: str, value: Any) -> float:
        """A cursor position write in the cursors' unit, stored in the axis's own units (seconds
        or volts): Hz is its inverse, degrees and percent ride the anchors (:UNits:USE)."""
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if value is None:
            raise ScpiError(-109)
        text = value.decode("ascii", errors="replace") if isinstance(value, bytes) else str(value)
        v = float(V.parse_number(text))
        unit = self._marker_units(axis)
        if unit == "s" or unit == "base":
            return v
        if unit == "hz":
            if v <= 0.0:
                raise ScpiError(-222, f"{v:g} Hz: a cursor's frequency is positive")
            return 1.0 / v
        a1 = self.param(f"marker_{axis}a1")
        a2 = self.param(f"marker_{axis}a2")
        if a1 is None or a2 is None or float(a2) == float(a1):
            return v  # nothing to scale against: the value passes through (the model's convention)
        full = 360.0 if unit == "deg" else 100.0
        return float(a1) + (float(a2) - float(a1)) * v / full

    @op("units_use", fields=("axis",))
    def op_units_use(self, axis: str = "X", **_: Any) -> OpResult:
        """``:MARKer:XUNits:USE`` / ``:MARKer:YUNits:USE``: the current cursor locations become
        the unit's anchors — 0 and 360 degrees, or 0 and 100 percent (no query)."""
        assert self.owner is not None
        axis = axis.strip().lower()
        if axis not in ("x", "y"):
            raise ScpiError(-224, f"{axis}: X or Y")
        one = float(self.param(f"marker_{axis}1") or 0.0)
        two = float(self.param(f"marker_{axis}2") or 0.0)
        for name, value in ((f"marker_{axis}a1", one), (f"marker_{axis}a2", two)):
            path = self.bound_path(name)
            if path is None:
                raise ScpiError(-300, f"{self.uid}: the {name} setting is not bound")
            self.owner.set_state(path, value, source="scpi", internal=True)
        return OpResult()

    @op("marker_source", fields=("which",))
    def op_marker_source(
        self, which: str = "X1Y1", value: Any = None, query: bool = False, **_: Any
    ) -> OpResult:
        """``:MARKer:X1Y1source`` / ``:MARKer:X2Y2source``: the cursors' source. ``MATH`` is an
        alias stored as FUNCtion, so the query answers FUNC for either (the manual's note,
        Programmer's Guide 02.66 p. 361); the reply is the source's short form (CHAN1, FUNC)."""
        assert self.owner is not None
        param = f"marker_source{2 if str(which).strip().upper().endswith('2') else 1}"
        if query or value is None:
            return OpResult(short_source(self.param(param) or "NONE").encode())
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if value is None:
            raise ScpiError(-109)
        word = V.parse_string(value if isinstance(value, bytes) else str(value).encode())
        if word.strip().upper() == "MATH":
            word = "FUNCtion"
        path = self.bound_path(param)
        if path is None:
            raise ScpiError(-300, f"{self.uid}: the {param} setting is not bound")
        self.owner.set_state(path, word, source="scpi", internal=True)
        return OpResult()

    @op("marker_measurements")
    def marker_measurements(
        self, measurement: Any = None, state: Any = None, query: bool = False, **_: Any
    ) -> OpResult:
        """``:MARKer:MEASurement:MEASurement``: which measurements the MEASurement mode tracks.
        Without ``state`` the list becomes that one measurement; with it, that measurement's
        tracking alone turns on or off. The query answers the tracked measurement, or empty."""
        if isinstance(measurement, list):  # a raw parameter takes all the remaining ones
            measurement = measurement[0] if measurement else None
        if measurement is None:
            if not query:
                raise ScpiError(-109)
            tracked = sorted(m["measurement"] for m in self.k_markers.values() if m.get("tracking"))
            return OpResult((",".join(f"MEAS{n}" for n in tracked)).encode()) if tracked else OpResult()
        word = V.parse_string(measurement if isinstance(measurement, bytes) else str(measurement).encode())
        digits = word.upper().removeprefix("MEAS").removeprefix("UREMENT").removeprefix("UREMENT")
        # MEASurement<N> rides its own short spellings (MEAS1, MEASurement1); a bare number too
        if not (digits.startswith("1") or digits.isdigit()):
            digits = digits.lstrip("0")
        if not digits.isdigit():
            raise ScpiError(-224, f"{word}: not a measurement number")
        n = int(digits)
        slot = next((k for k, m in self.k_markers.items() if m.get("measurement") == n), None)
        if state:  # an omitted parameter arrives as an empty list, not None
            on = V.flag(state)
            if slot is None and on:
                slot = max(self.k_markers, default=0) + 1
            if slot is not None:
                if on:
                    self.k_markers[slot] = {"tracking": True, "measurement": n}
                else:
                    self.k_markers.pop(slot, None)
            return OpResult()
        self.k_markers = {k: m for k, m in self.k_markers.items() if not m.get("tracking")}
        self.k_markers[max(self.k_markers, default=0) + 1] = {"tracking": True, "measurement": n}
        meas_path = self.bound_path("marker_measurement")
        if meas_path is not None:
            assert self.owner is not None
            self.owner.set_state(meas_path, n, source="scpi", internal=True)
        return OpResult()

    # ---- the numbered markers (:MARKer<K>, #1074) --------------------------------------------------

    def _k(self, suffixes: dict[str, int] | None) -> int:
        """The ``<K>`` a numbered-marker command carried, checked against the model's count."""
        most = int(self.param("markers") or 0)
        k = int(next(iter(suffixes.values()))) if suffixes else 1
        if most <= 0 or not 1 <= k <= most:
            raise ScpiError(-114, f"marker {k} is not 1..{most}")
        return k

    def _k_marker(self, k: int) -> dict[str, Any]:
        """Marker k's state, its defaults in: XMANual type, channel 1, off."""
        return self.k_markers.setdefault(
            k, {"type": "XMANual", "source": "CHANnel1", "enable": False, "x": 0.0, "y": 0.0}
        )

    @op("k_field", fields=("field",))
    def k_field(
        self,
        field: str = "type",
        value: Any = None,
        query: bool = False,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """One field of a numbered marker — its type, source, enable, name, color, colour mode, x
        or y position — as ``:MARKer<K>:<field>`` reads and writes it. A type change needs the
        marker off (the manual: -221); what the marker's type has no place for answers
        ``invalid_value``; a TRACk or RF marker's y is its source's value at its x. The query
        answers the short forms the manual's Returned Format names (XMAN, TRAC, CHAN1, CUST)."""
        assert self.owner is not None
        k = self._k(suffixes)
        marker = self._k_marker(k)
        key = {
            "type": "type",
            "source": "source",
            "enable": "enable",
            "name": "name",
            "color": "color",
            "cmode": "cmode",
            "x": "x",
            "y": "y",
        }[field]
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if query:
            if key == "x" and marker["type"] == "YMANual":
                return OpResult(str(self.param("invalid_value")).encode())
            if key == "y" and marker["type"] == "XMANual":
                return OpResult(str(self.param("invalid_value")).encode())
            if key == "y" and marker["type"] in ("TRACk", "RF"):
                # a tracking marker marks its source's value at its x (the guide's TRACk / RF)
                rec, used = self._source_record(str(marker.get("source") or ""))
                y = self._value_at(rec, float(marker.get("x") or 0.0))
                if not math.isfinite(y):
                    return OpResult(str(self.param("invalid_value")).encode(), used)
                return OpResult(self.owner.format_number(y), used)
            current = marker.get(key)
            if key == "enable":
                return OpResult(V.format_int(int(bool(current))))
            if isinstance(current, (int, float)) and key in ("x", "y"):
                return OpResult(self.owner.format_number(float(current)))
            if current in (None, ""):
                return OpResult(b'""')
            if key == "type":
                return OpResult(
                    {"XMANual": "XMAN", "YMANual": "YMAN", "TRACk": "TRAC", "RF": "RF"}[current].encode()
                )
            if key == "source":
                return OpResult(short_source(current).encode())
            if key == "cmode":
                return OpResult(b"CUST" if current == "CUSTom" else b"SOUR")
            if key in ("name", "color"):  # a quoted string, as the manual's Returned Format has it
                return OpResult(f'"{current}"'.encode())
            return OpResult(str(current).encode())
        if value is None:
            raise ScpiError(-109)
        raw = value if isinstance(value, bytes) else str(value).encode()
        if key == "type":
            word = V.parse_string(raw).upper()
            if word not in ("XMANUAL", "YMANUAL", "TRACK", "RF"):
                raise ScpiError(-224, f"{value}: XMANual, YMANual, TRACk or RF")
            if marker["enable"]:
                raise ScpiError(-221, f"marker {k} is on: turn it off before changing its type")
            marker["type"] = {"XMANUAL": "XMANual", "YMANUAL": "YMANual", "TRACK": "TRACk", "RF": "RF"}[word]
            return OpResult()
        if key == "enable":
            marker["enable"] = V.flag(raw)
            return OpResult()
        if key == "cmode":
            word = V.parse_string(raw).upper()
            if word not in ("CUSTOM", "SOURCE"):
                raise ScpiError(-224, f"{value}: CUSTom or SOURce")
            marker["cmode"] = "CUSTom" if word == "CUSTOM" else "SOURce"
            return OpResult()
        if key in ("source", "name", "color"):
            marker[key] = V.parse_string(raw)
            return OpResult()
        if key == "x" and marker["type"] == "YMANual":
            raise ScpiError(-221, "a Y-only marker has no x position")
        if key == "y" and marker["type"] in ("XMANual", "TRACk", "RF"):
            raise ScpiError(-221, "an X-only or tracking marker has no y position")
        try:
            number = parse_number(raw.decode("ascii", errors="replace"))
        except ValueError:
            raise ScpiError(-104, f"{value!r}: not a number") from None
        marker[key] = number
        return OpResult()

    @op("marker_delta")
    def marker_delta(
        self,
        other: Any = None,
        state: Any = None,
        query: bool = True,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """``:MARKer:DELTa`` and ``:MARKer<K>:DELTa``, which share a header: without a ``MARKer<L>``
        word the command is the screen's delta-markers switch (``{ON | OFF}``, the query answers
        0/1); with one, it is marker K's delta-to relationship with marker L — the query answers
        ``<state>,<Δx>,<1/Δx>,<Δy>,<Δy/Δx>`` (``invalid_value`` where not applicable)."""
        assert self.owner is not None
        if isinstance(other, list):  # a raw parameter takes all the remaining ones: the ON/OFF
            extra = other[1:]  # state of a :MARKer<K>:DELTa MARKer<L>,{ON | OFF} rides here
            other = other[0] if other else None
            if not state and extra:  # an omitted parameter arrives as an empty list
                state = extra[0]
        if isinstance(state, list):
            state = state[0] if state else None
        raw = other if isinstance(other, bytes) else (str(other).encode() if other is not None else b"")
        word = V.parse_string(raw).upper() if raw else ""
        if word.startswith("MARKER") and word.removeprefix("MARKER").isdigit():
            k = self._k(suffixes)
            other_k = int(word.removeprefix("MARKER"))
            if not 1 <= other_k <= int(self.param("markers") or 0) or other_k == k:
                raise ScpiError(-224, f"{other!r}: not another marker of this model")
            first, second = self._k_marker(k), self._k_marker(other_k)
            if not query:
                word = (
                    ""
                    if state is None
                    else V.parse_string(state if isinstance(state, bytes) else str(state).encode())
                )
                first["delta_to"] = other_k if state is None or V.flag(word) else None
                return OpResult()
            on = first.get("delta_to") == other_k
            dx = float(second["x"]) - float(first["x"])
            invalid = str(self.param("invalid_value")).encode()
            parts = [V.format_int(int(on)), self.owner.format_number(dx)]
            parts.append(self.owner.format_number(1.0 / dx) if dx else invalid)
            if first["type"] == second["type"] == "YMANual":
                dy = float(second["y"]) - float(first["y"])
                parts.append(self.owner.format_number(dy))
                parts.append(self.owner.format_number(dy / dx) if dx else invalid)
            else:
                parts += [invalid, invalid]
            return OpResult(b",".join(parts))
        if not query:  # :MARKer:DELTa {ON | OFF}: the screen's delta-markers check box
            path = self.bound_path("marker_delta")
            if path is None:
                raise ScpiError(-113, "this model has no delta-marker display setting")
            self.owner.set_state(path, V.flag(raw), source="scpi", internal=True)
            return OpResult()
        path = self.bound_path("marker_delta")
        if path is None:
            raise ScpiError(-113, "this model has no delta-marker display setting")
        return OpResult(V.format_int(int(V.flag(self.owner.get_state(path)))))
