"""Oscilloscope measurements as a component: the op pipeline, the screen list, statistics,
cursors and markers; the algorithms are ``measurements`` (split from ``scope``, #1386)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ...engine.component import (
    Component,
    MathSource,
    OpResult,
    RecordSource,
    component,
    op,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .measurements import (
    FREQ_HYSTERESIS,
    Levels,
)
from .scope import trigger_channel
from .scope_cursors import Cursors
from .scope_edges import Edges
from .scope_kmarkers import KMarkers
from .scope_math import function_index
from .scope_readouts import Readouts
from .scope_record import (
    Record,
    channel_index,
)
from .scope_results import ResultsList
from .scope_values import Values


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
        self.results = ResultsList(self)
        self.values = Values(self)
        self.edges = Edges(self)
        self.cursors = Cursors(self)
        self.readouts = Readouts(self)
        self.kmarkers = KMarkers(self)

    def reset(self) -> None:
        self.results.history = {}
        self.results.installed = []
        self.kmarkers.k_markers = {}
        self.results.names = {}
        self.results.screen = []

    # ---- the screen's measurement list (#373) ------------------------------------------------------

    def install(self, what: str, n: int) -> None:
        """Delegates to :meth:`ResultsList.install`."""
        return self.results.install(what, n)

    @op("results")
    def op_results(self, order: str = "", **_: Any) -> OpResult:
        """``:MEASure:RESults?``: each installed measurement as ``<label>,<current>,<min>,<max>,<mean>,
        <std dev>,<count>`` — the statistics of every value taken since the list was cleared (the
        current one included). Label: its name, else ``<name>(<channel>)``. Oldest first; ``order``
        AORDered: the newest first, GORDered: the screen's order (top to bottom, after any move).
        Nothing installed: an empty reply."""
        return self.results.op_results(order, **_)

    @op("store_results")
    def op_store_results(self, name: str = "", **_: Any) -> OpResult:
        """``:DISK:SAVE:MEASurements "<file>"``: the screen's measurements to a CSV file on
        ``storage`` (``.csv`` added): a header line, then one row per measurement, top to bottom —
        label, current, min, max, mean, std dev, count (SimuBench's layout)."""
        return self.results.op_store_results(name, **_)

    def _entry(self, index: Any) -> tuple[str, int]:
        """Delegates to :meth:`ResultsList.entry`."""
        return self.results.entry(index)

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
        return self.results.op_entry(query, what, index, value, suffixes, **_)

    def number(self, value: float) -> bytes:
        assert self.owner is not None
        if not math.isfinite(value) and self.param("invalid_value") != "UNDEF":
            return str(self.param("invalid_value")).encode()
        return self.owner.format_number(value)

    @op("clear_list")
    def op_clear_list(self, **_: Any) -> OpResult:
        """``:MEASure:CLEar``: no measurements on the screen, their statistics gone."""
        return self.results.op_clear_list(**_)

    def clip_flags(self, n: int) -> int:
        """Delegates to :meth:`Readouts.clip_flags`."""
        return self.readouts.clip_flags(n)

    @op("clipped")
    def op_clipped(self, channel: int | None = None, sides: bool = False, **_: Any) -> OpResult:
        """ADC clipping of the channel's current record: 1 when it reaches either limit, else 0;
        with ``sides`` 1 low, 2 high, 3 both (the Infiniium's :CHANnel<n>:ADC:CLIPped?)."""
        return self.readouts.op_clipped(channel, sides, **_)

    @op("overload")
    def op_overload(self, **_: Any) -> OpResult:
        """The Overload Event Register (:OVLRegister?): bit n-1 set for each channel n whose input
        went beyond its maximum input range since the last read; the read clears it (#1264)."""
        return self.readouts.op_overload(**_)

    @op("all")
    def op_all(self, source: Any = None, channel: int | None = None, **_: Any) -> OpResult:
        """``:MEASure:ALL?``: the current value of every measurement of ``all`` on one source, in
        that order (a snapshot; nothing is installed)."""
        return self.readouts.op_all(source, channel, **_)

    def acq(self) -> Any:
        assert self.owner is not None
        return self.ref("record")  # type: ignore[return-value]

    @staticmethod
    def _is_source(word: str) -> bool:
        """Whether a parameter word names a source: a channel (CHANnel<n>) or a math function
        (FUNCtion<F>, FUNC<F>, F<F>)."""
        return word.strip().upper().startswith("CH") or function_index(word) is not None

    def channel_of(self, channel: int | None, source: Any = None) -> int:
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
            return self.key(bound)
        return channel or 1

    @staticmethod
    def key(word: Any) -> int:
        """A setting's source word as a source key (CHANnel2, CH2, C2 -> 2; FUNCtion3 -> -3)."""
        f = function_index(word)
        return -f if f is not None else channel_index(word)

    @staticmethod
    def source_word(n: int) -> str:
        """A source key's short word: CHAN<n>, or FUNC<F> for a function."""
        return f"FUNC{-n}" if n < 0 else f"CHAN{n}"

    def record(self, n: int) -> tuple[Any, int]:
        """Delegates to :meth:`Values.record`."""
        return self.values.record(n)

    def value(self, what: str, n: int) -> tuple[float, int]:
        """Delegates to :meth:`Values.value`."""
        return self.values.value(what, n)

    def screen_values(self, records: Mapping[int, Record]) -> list[dict[str, Any]]:
        """Delegates to :meth:`Values.screen_values`."""
        return self.values.screen_values(records)

    def record_value(self, what: str, rec: Any) -> float:
        """Delegates to :meth:`Values.record_value`."""
        return self.values.record_value(what, rec)

    def threshold_spec(self, n: int, group: str) -> dict[str, Any]:
        """Delegates to :meth:`Values.threshold_spec`."""
        return self.values.threshold_spec(n, group)

    def levels(self, v: Any, rec: Any, group: str = "general") -> Levels | None:
        """Delegates to :meth:`Values.levels`."""
        return self.values.levels(v, rec, group)

    def sources(self, raw: list[bytes] | None) -> tuple[list[str], list[int]]:
        """Raw parameters split into the arguments and the channels named (CHANnel<n>). An item may
        also be a setting's value (a number or a word, op args ``[$bookmark.time, $bookmark.source]``)."""
        words = [
            (
                V.parse_string(b) if isinstance(b, bytes) else repr(b) if isinstance(b, float) else str(b)
            ).strip()
            for b in raw or []
        ]
        channels = [self.channel_of(None, w) for w in words if self._is_source(w)]
        return [w for w in words if not self._is_source(w)], channels

    def reply(self, value: float, used: int) -> OpResult:
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
        return self.edges.op_at(what, source, channel, **_)

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
        return self.edges.op_pair(what, source, define, **_)

    @op("auto_thresholds")
    def op_auto_thresholds(self, source: Any = None, channel: int | None = None, **_: Any) -> OpResult:
        """The Infiniium's :MEASure:THResholds:GENAUTO: the source's general hysteresis threshold set
        from its record — the level halfway between top and base, the range the counter's band
        (``hysteresis`` of top minus base, never below the band floor)."""
        return self.edges.op_auto_thresholds(source, channel, **_)

    @op("with_args")
    def op_with_args(self, what: str = "", source: list[bytes] | None = None, **_: Any) -> OpResult:
        """Measurements that take a number besides the source (the Infiniium's): ``PTOP? <source>,<%>``
        / ``PBASe?`` — the mean of the first positive / negative pulse within the centred window of
        that percent of its width; ``BWIDth? <source>,<idle>``, ``BPERiod?``, ``BINTerval?`` — the
        first complete burst's width, the start-to-start and the end-to-start time of the first two
        (bursts are runs of middle crossings closer than the idle time)."""
        return self.edges.op_with_args(what, source, **_)

    @op("auto_trigger_levels")
    def op_auto_trigger_levels(self, source_only: bool = False, **_: Any) -> OpResult:
        """``:TRIGger:LEVel:ASETup``: every displayed channel's trigger level to its waveform's 50 %
        (halfway between top and base; 0 V with AC coupling) — the levels of ``trigger_levels``,
        channel -> setting (#350). ``source_only`` (the Infiniium's ``:TRIGger:LEVel:FIFTy``): the
        level of the channel the trigger takes its source from now, whatever the trigger type."""
        return self.edges.op_auto_trigger_levels(source_only, **_)

    def follow(self, n: int, source: Any) -> None:
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
        return self.readouts.measure(
            what, source, channel, query, interval, direction, coupling, units, impedance, **_
        )

    @op("named", fields=("kind", "unit", "status"))
    def named(self, channel: int | None = None, kind: str | None = None, **_: Any) -> OpResult:
        """A measurement by the model's name (``names``): the value, or ``invalid_value`` when it
        cannot be measured; the reply template also gets {kind}, {unit} and {status}, e.g.
        '{header} {kind},{value} {unit},{status}'."""
        return self.readouts.named(channel, kind, **_)

    @op("statistics")
    def statistics(
        self, kind: str | None = None, item: str = "mean", channel: int | None = None, **_: Any
    ) -> OpResult:
        """The statistics of the values taken so far of a measurement: ``count``, ``mean``,
        ``sdev``, ``min`` or ``max`` (``item``, a constant argument or a parameter)."""
        return self.results.statistics(kind, item, channel, **_)

    @op("clear_statistics")
    def clear_statistics(self, **_: Any) -> OpResult:
        return self.results.clear_statistics(**_)

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

    @op("cursor")
    def cursor(self, which: str = "STARt", query: bool = True, **_: Any) -> OpResult:
        """``:MARKer:CURSor? {DELTa | STARt | STOP}``: the tracked positions as ``<x>,<y>``.
        The query also puts the markers in the waveform-tracking mode, whatever they were (the
        manual's caution: it changes the mode)."""
        return self.cursors.cursor(which, query, **_)

    @op("x_delta")
    def x_delta(self, query: bool = True, **_: Any) -> OpResult:
        """``:MARKer:XDELta?``: Bx − Ax, in the X cursors' units — the value at each cursor in
        that unit, minus (the 2000 X-series manual's Xdelta; #1155)."""
        return self.cursors.x_delta(query, **_)

    @op("y_delta")
    def y_delta(self, query: bool = True, **_: Any) -> OpResult:
        """``:MARKer:YDELta?``: By − Ay — the markers' settings, or the values at their x in the
        tracking modes."""
        return self.cursors.y_delta(query, **_)

    # ---- the markers' units (2000 X-series :MARKer:XUNits/:YUNits, #1155) ------------------------

    XUNITS = {"SEC": "s", "HERT": "hz", "DEGR": "deg", "PERC": "pct"}
    YUNITS = {"BASE": "base", "PERC": "pct"}

    @op("x_position", fields=("which",))
    def op_x_position(self, which: str = "X1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        """``:MARKer:X1Position`` / ``:MARKer:X2Position``: the cursor's position. A write puts
        the cursors in the MANual mode unless they track (the manual); the input and the reply
        are in the X cursors' units (:MARKer:XUNits)."""
        return self.cursors.op_x_position(which, value, query, **_)

    @op("y_position", fields=("which",))
    def op_y_position(self, which: str = "Y1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        """``:MARKer:Y1Position`` / ``:MARKer:Y2Position``: the cursor's position. A write is
        refused while the cursors track (the manual: Y positions cannot be set in the WAVeform
        mode) and otherwise puts them in the MANual mode."""
        return self.cursors.op_y_position(which, value, query, **_)

    @op("tracked_y", fields=("which",))
    def op_tracked_y(self, which: str = "Y1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        """The Infiniium's ``:MARKer:Y1Position`` / ``:MARKer:Y2Position`` (and VSTArt/VSTOp): the
        query answers "the level at the marker position" — where the marker is: in the WAVeform
        mode the source's value at the marker's x, in the MEASurement mode the level of the
        measurement it tracks (as ``:MARKer:YDELta?`` reads it), otherwise the setting; nothing to
        track answers the invalid value. A write stores the setting and leaves the mode alone (the
        guide names no mode change, unlike the 2000 X-series' ``y_position``)."""
        return self.cursors.op_tracked_y(which, value, query, **_)

    @op("units_use", fields=("axis",))
    def op_units_use(self, axis: str = "X", **_: Any) -> OpResult:
        """``:MARKer:XUNits:USE`` / ``:MARKer:YUNits:USE``: the current cursor locations become
        the unit's anchors — 0 and 360 degrees, or 0 and 100 percent (no query)."""
        return self.cursors.op_units_use(axis, **_)

    @op("marker_source", fields=("which",))
    def op_marker_source(
        self, which: str = "X1Y1", value: Any = None, query: bool = False, **_: Any
    ) -> OpResult:
        """``:MARKer:X1Y1source`` / ``:MARKer:X2Y2source``: the cursors' source. ``MATH`` is an
        alias stored as FUNCtion, so the query answers FUNC for either (the manual's note,
        Programmer's Guide 02.66 p. 361); the reply is the source's short form (CHAN1, FUNC)."""
        return self.cursors.op_marker_source(which, value, query, **_)

    @op("marker_measurements")
    def marker_measurements(
        self, measurement: Any = None, state: Any = None, query: bool = False, **_: Any
    ) -> OpResult:
        """``:MARKer:MEASurement:MEASurement``: which measurements the MEASurement mode tracks.
        Without ``state`` the list becomes that one measurement; with it, that measurement's
        tracking alone turns on or off. The query answers the tracked measurement, or empty."""
        return self.kmarkers.marker_measurements(measurement, state, query, **_)

    # ---- the numbered markers (:MARKer<K>, #1074) --------------------------------------------------

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
        return self.kmarkers.k_field(field, value, query, suffixes, **_)

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
        return self.kmarkers.marker_delta(other, state, query, suffixes, **_)
