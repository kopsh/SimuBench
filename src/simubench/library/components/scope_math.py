"""The math functions of an oscilloscope (#1074): point-by-point operators, the FFT and its
measurements, over the records the acquisition keeps.

``scope_math`` holds a scope's math functions (``FUNCtion1..N``). A function is defined by its
operator and operands — channels, other functions or a constant — kept in the settings the
definition declares under one prefix per function (``function1.operator``, ``function1.source1``,
…), and its waveform is computed from the source records when it is read: a pure function of the
settings and the records, nothing kept between reads (D67). The operator vocabulary and behaviour
follow the Infiniium Programmer's Guide ch. 22 (``:FUNCtion<F>``):

* point-wise: ADD, SUBTract, MULTiply, DIVide, COMMonmode, ABSolute, INVert, SQUare, SQRT, MAGNify
  (a copy under its own scale), DELay (an X-origin shift) and GATing (a horizontal window);
* windowed: DIFFerentiate (central differences), INTegrate (cumulative trapezoid), AVERage and
  SMOoth (a centred running mean), MAXimum and MINimum (the extreme of each time bucket);
* filters: HIGHpass (single-pole) and LOWPass (a 4th-order Bessel-Thompson) — both computed over
  the whole source record, so a deep one is refused beyond the model rather than filtered tile by
  tile;
* transforms: FFTMagnitude, FFTPhase and PSDensity — the windowed FFT of the Infiniium set
  (RECTangular, HANNing, HAMMing, BHARris, FLATtop), as the one-sided magnitude in dBm/dBmV/dBµV/
  Watt/Vrms, phase in degrees, or power spectral density in dBm/Hz, Watt/Hz and V/√Hz. The
  transform's transform is the "effective memory depth" the manual names — the highest power of
  two not above the record — capped at ``fft_max_points``, and what cannot be met is refused,
  never silently truncated (#1101's rule). The windowed transform itself is
  ``library/components/specan_backend.py``'s convention (a coherent tone reads its own amplitude
  whatever the window); the five windows are this module's.

The FFT measurements of ``:MEASure:FFT`` (peak frequency and magnitude, deltas between peaks,
channel power, power spectral density, occupied bandwidth, total harmonic distortion) and the
annotated peak search (``:FUNCtion<F>:FFT:PEAK``) are answered from the same trace.

What this block does not model (the coverage tables note it per model): the amplitude
demodulation (ADEMod), the measurement trend and histogram functions (MTRend, MLOG, MHIStogram),
pattern averaging (PAVerage), the X-Y displays (VERSus, XYZ, XYZI) and the user-defined MATLAB
operators.
"""

from __future__ import annotations

from typing import Any

from ...engine.component import Acquisition, Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError

# ---- the point-wise operators (pure) ---------------------------------------------------------------
from .scope_math_controls import Controls
from .scope_math_fft import Fft
from .scope_math_host import MathRecord as MathRecord
from .scope_math_host import Operand as Operand
from .scope_math_host import _in_dbm as _in_dbm
from .scope_math_host import _source_function as _source_function
from .scope_math_host import _suffix as _suffix
from .scope_math_host import aligned as aligned
from .scope_math_host import apply_span as apply_span
from .scope_math_host import function_index as function_index
from .scope_math_ops import (
    _TIMEBASE_BOUND as _TIMEBASE_BOUND,
)
from .scope_math_ops import (
    BESSEL4_SECTIONS as BESSEL4_SECTIONS,
)
from .scope_math_ops import (
    CANONICAL as CANONICAL,
)
from .scope_math_ops import (
    FFT_OPERATORS as FFT_OPERATORS,
)
from .scope_math_ops import (
    GUIDE_UNIT_WORDS as GUIDE_UNIT_WORDS,
)
from .scope_math_ops import (
    MIN_FFT_POINTS as MIN_FFT_POINTS,
)
from .scope_math_ops import (
    OPERATORS as OPERATORS,
)
from .scope_math_ops import (
    UNARY as UNARY,
)
from .scope_math_ops import (
    VUNIT_WORDS as VUNIT_WORDS,
)
from .scope_math_ops import (
    VUNITS as VUNITS,
)
from .scope_math_ops import (
    WHOLE_OPERATORS as WHOLE_OPERATORS,
)
from .scope_math_ops import (
    WINDOW_WORDS as WINDOW_WORDS,
)
from .scope_math_ops import (
    WINDOWS as WINDOWS,
)
from .scope_math_ops import (
    _bessel4_sections as _bessel4_sections,
)
from .scope_math_ops import (
    as_density as as_density,
)
from .scope_math_ops import (
    band_power as band_power,
)
from .scope_math_ops import (
    bin_power as bin_power,
)
from .scope_math_ops import (
    bucket_extreme as bucket_extreme,
)
from .scope_math_ops import (
    derivative as derivative,
)
from .scope_math_ops import (
    find_peaks as find_peaks,
)
from .scope_math_ops import (
    highpass as highpass,
)
from .scope_math_ops import (
    integral as integral,
)
from .scope_math_ops import (
    lowpass as lowpass,
)
from .scope_math_ops import (
    magnitude as magnitude,
)
from .scope_math_ops import (
    occupied_bandwidth as occupied_bandwidth,
)
from .scope_math_ops import (
    one_sided_fft as one_sided_fft,
)
from .scope_math_ops import (
    phase as phase,
)
from .scope_math_ops import (
    pointwise as pointwise,
)
from .scope_math_ops import (
    running_mean as running_mean,
)
from .scope_math_ops import (
    window as window,
)

# ---- the transform (pure) ---------------------------------------------------------------------------


# ---- the record --------------------------------------------------------------------------------------


@component("scope_math")
class ScopeMath(Component):
    """A scope's math functions (``FUNCtion1..N``): the operators, the transforms and the
    measurements over their traces, computed from the acquisition's records when they are read.

    Each function's definition lives in the settings under ``prefix`` + its number —
    ``function1.operator``, ``function1.source1``, ``function1.display``, ``function1.range``, …
    — declared by the definition; the block reads them by path, so one block serves every
    function a model has. A source names a channel, another function (a cycle is -221) or a
    constant. Model limits: a transform longer than ``fft_max_points`` points or a span beyond
    ``fft_span_max`` is refused (-221/-222), never truncated; an unknown operand is -224."""

    label = "数学运算"
    label_en = "Math functions"
    about = "示波器的数学函数：运算、变换以及对其迹线的测量。"
    about_en = "A scope's math functions: operators, transforms and measurements on their traces."

    refs = {"record": (Acquisition, "instrument")}
    defaults = {
        "record": "acq",  # the acquisition component holding the source records
        "prefix": "function",  # function f's settings live under <prefix><f>., e.g. function1.operator
        "functions": 2,  # how many math functions the model has (F1..Fn; another number is -114)
        "channels": 4,  # the channel operands the sources may name (CHANnel1..N)
        "operators": (),  # the operator words this model accepts; empty: every one this block knows
        "fft_max_points": 1 << 20,  # most points a transform is taken of; beyond: -221, not truncated
        "fft_span_max": None,  # most transform span the model sets, Hz; None: the sample rate's Nyquist
        "impedance": 50.0,  # Ω, the power units' reference when the model has no impedance setting
        "digits": 6,  # digits of a number a measurement of this block answers
        "invalid_value": "9.99E+37",  # what a measurement that cannot be made answers
        "peak_sort": "DMAG",  # the order the annotated peaks list in (a setting): DMAG, IMAG, IFR, DFR
        "resolution_scale": "",  # the timebase scale setting a :FFT:RESolution write follows, e.g. timebase.scale
        "goft_prefix": "",  # the g(t) source's settings (<prefix>.operator/source1/source2); "": no GOFT
    }

    # ---- the functions' definitions ----------------------------------------------------------

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fft = Fft(self)
        self.controls = Controls(self)

    def setting(self, f: int, name: str, default: Any = None) -> Any:
        assert self.owner is not None
        path = f"{self.param('prefix')}{f}.{name}"
        if path not in self.owner.definition.state:
            return default
        value = self.owner.get_state(path)
        return default if value is None else value

    def format_number(self, value: float) -> bytes:
        """A number in the definition's own reply format (the instrument's NR3, not the
        component's): the manual's sample replies are the response format's."""
        assert self.owner is not None
        return self.owner.format_number(value)

    def get_state(self, path: str) -> Any:
        """A setting's value, read from the instrument (#1724: the blocks' name for it)."""
        assert self.owner is not None
        return self.owner.get_state(path)

    def set_state(self, path: str, value: Any, *, source: str = "scpi", internal: bool = False) -> None:
        """A setting's value written on the instrument: ``internal`` for one the commands' own
        writes fill in (a function's operator and sources)."""
        assert self.owner is not None
        self.owner.set_state(path, value, source=source, internal=internal)

    def has_state(self, path: str) -> bool:
        """Whether the definition declares the setting ``path``."""
        assert self.owner is not None
        return path in self.owner.definition.state

    def function(self, f: int) -> int:
        if not 1 <= f <= int(self.param("functions")):
            raise ScpiError(-114, f"function {f} is not 1..{self.param('functions')}")
        return f

    def operator(self, f: int) -> str:
        word = str(self.setting(f, "operator", "NONE") or "NONE").strip().upper()
        allowed = tuple(str(a).upper() for a in (self.param("operators") or ()))
        if word in ("NONE", ""):
            return "NONE"
        if word not in CANONICAL:
            raise ScpiError(-224, f"{word}: no such operator")
        if allowed and word not in allowed:
            raise ScpiError(-224, f"{word}: no such operator on this model")
        return CANONICAL[word]

    def _operand(self, f: int, which: int) -> Operand:
        return Operand(
            str(self.setting(f, f"source{which}", "") or ""),
            int(self.param("channels")),
            int(self.param("functions")),
        )

    # ---- the records ---------------------------------------------------------------------------

    def function_record(
        self,
        f: int,
        start: bool = True,
        seen: frozenset[int] = frozenset(),
        shown: dict[int, Any] | None = None,
    ) -> tuple[MathRecord | None, int]:
        """Function ``f``'s waveform over its sources' records as they are read now (``start``:
        the read starts a transfer), and the virtual time getting the sources took. None: the
        function has no operator.

        ``shown``: the channel records the screen draws (a live frame or the last acquisition),
        taken as they are — nothing is acquired, no virtual time passes; a function over a
        channel the screen has no record of, or over the g(t) source, is None."""
        f = self.function(f)
        if f in seen:
            raise ScpiError(-221, f"function {f} takes its own output as a source (a cycle)")
        operator = self.operator(f)
        if operator in ("NONE", ""):
            return None, 0
        acq = self.ref("record")
        if acq is None or not isinstance(acq, Acquisition):
            raise ScpiError(-300, f"{self.uid}: no acquisition to take sources from")
        records: list[Any] = [None, None]
        constants: list[float | None] = [None, None]
        used = 0
        for which in (1, 2):
            operand = self._operand(f, which)
            if which == 2 and (operator in UNARY or not operand.word):
                break
            if not operand.word:
                raise ScpiError(-221, f"function {f} has no source")
            if operand.function is not None:
                rec, u = self.function_record(operand.function, start, seen | {f}, shown)
                if rec is None:
                    if shown is not None:
                        return None, 0
                    raise ScpiError(-221, f"function {operand.function} is not defined")
                records[which - 1] = rec
                used += u
            elif shown is not None and (operand.goft or operand.channel is not None):
                rec = None if operand.goft else shown.get(operand.channel)
                if rec is None:
                    return None, 0
                records[which - 1] = rec
            elif operand.goft:
                rec, u = self.fft.goft_record(start)
                records[which - 1] = rec
                used += u
            elif operand.channel is not None:
                rec, u = acq.read_record(operand.channel, start)
                used += u
                if rec is None:
                    raise ScpiError(-230, f"channel {operand.channel} has no record")
                records[which - 1] = rec
            else:
                constants[which - 1] = operand.constant
        if records[0] is None and records[1] is None and constants[0] is None:
            raise ScpiError(-221, f"function {f} has no source")
        spec = self.fft.spec(f, operator, records)
        if operator in WHOLE_OPERATORS:
            return self.fft.whole(spec, records, constants), used
        return MathRecord(spec, records, constants), used

    def screen_functions(self, shown: dict[int, Any]) -> list[tuple[int, str, MathRecord]]:
        """The functions the screen draws over ``shown`` (its channel records): each one turned
        on, with an operator and a record to compute it from, as (number, operator, record). A
        function the model cannot compute there (a settings conflict, a limit) is left out, as
        the scope leaves its trace blank."""
        out = []
        for f in range(1, int(self.param("functions")) + 1):
            if not V.flag(self.setting(f, "display", False)):
                continue
            try:
                operator = self.operator(f)
                rec, _ = self.function_record(f, start=False, shown=shown)
                if rec is not None:
                    _ = rec.volts  # computed here, so a refusal (a model limit too) leaves it out
            except ScpiError:
                continue
            if rec is not None:
                out.append((f, operator, rec))
        return out

    def read_function(self, f: int, start: bool = True) -> tuple[MathRecord | None, int]:
        """The record a read of function ``f`` uses (the transfer's ``Acquisition.read_record``)."""
        return self.function_record(f, start)

    def transfer_function(self, f: int, first: int) -> tuple[MathRecord | None, int]:
        """The record a waveform transfer of function ``f`` reads (``first``: its first point)."""
        return self.function_record(f, first == 0)

    def measure_function(self, f: int) -> tuple[MathRecord, int]:
        """The record a measurement on function ``f`` uses; a function without one is -230."""
        rec, used = self.function_record(f)
        if rec is None:
            raise ScpiError(-230, f"function {f} is not defined")
        return rec, used

    def function_units(self, f: int) -> tuple[str, str]:
        """The function's X and Y axis unit words (``:WAVeform:XUNits?``/``:YUNits?``, #1066): the
        transform's axes are frequency against its vertical unit's word (the guide's lists, pp.
        1641/1647); the phase trace has no word in the guide's Y list, so it reads UNKNown. Every
        other operator's trace is time and volts."""
        f = self.function(f)
        operator = self.operator(f)
        if operator not in FFT_OPERATORS:
            return "SECond", "VOLT"
        if operator == "FFTPhase":
            return "HERTz", "UNKNown"
        unit = str(self.setting(f, "vunits", "DB") or "DB").upper()
        return "HERTz", GUIDE_UNIT_WORDS.get(unit, "VOLT")

    # ---- building the operator ------------------------------------------------------------------

    def _spec(self, f: int, operator: str, records: list[Any]) -> dict[str, Any]:
        """Delegates to :meth:`Fft.spec`."""
        return self.fft.spec(f, operator, records)

    def _vertical(self, f: int, source: Any) -> tuple[float, float]:
        """Delegates to :meth:`Fft.vertical`."""
        return self.fft.vertical(f, source)

    def _whole(self, spec: dict[str, Any], records: list[Any], constants: list[float | None]) -> MathRecord:
        """Delegates to :meth:`Fft.whole`."""
        return self.fft.whole(spec, records, constants)

    # ---- the transform --------------------------------------------------------------------------

    # ---- the FFT measurements ---------------------------------------------------------------------

    @op("fft_peak", fields=("kind",))
    def op_fft_peak(
        self, kind: str = "FREQuency", suffixes: dict[str, int] | None = None, **_: Any
    ) -> OpResult:
        """``:FUNCtion<f>:FFT:PEAK:FREQuency?`` / ``…:MAGNitude?``: the annotated peaks (above
        the function's peak level, at most its peak count), comma-separated in the ``peak_sort``
        order; the annotation off (``peak_state``) or none found: ``invalid_value``."""
        return self.fft.op_fft_peak(kind, suffixes, **_)

    @op("fft_measure", fields=("kind",))
    def op_fft_measure(
        self,
        kind: str = "FREQuency",
        source: Any = None,
        peak: int | None = None,
        peak1: int | None = None,
        peak2: int | None = None,
        level: float | None = -60.0,
        center: float | None = 0.0,
        bandwidth: float | None = 0.0,
        percent: float | None = 99.0,
        mode: str | None = None,
        value: float | None = None,
        **_: Any,
    ) -> OpResult:
        """``:MEASure:FFT:<kind>? <source>,…``: FREQuency and MAGNitude of a peak, DFRequency and
        DMAGnitude between two peaks (numbered low to high above ``level`` dBm), CPOWer of a band
        and its PSD, OBW at ``percent`` of the power, THDistortion about the fundamental. The guide
        gives every argument; an omitted one arrives as None and takes SimuBench's default: peak
        (FREQuency/MAGNitude, ``peak`` or ``peak1``) 1, the second peak 2, level -60 dBm, center and
        bandwidth 0, percent 99. THDistortion's ``value`` is what its ``mode`` makes it: the
        fundamental in Hz (MANual) or the peak level in dBm (PEAK: the ``peak``-th peak above it,
        numbered low to high like the other FFT peak measurements, whatever the annotation sort)."""
        return self.fft.op_fft_measure(
            kind, source, peak, peak1, peak2, level, center, bandwidth, percent, mode, value, **_
        )

    @op("definition")
    def op_definition(self, suffixes: dict[str, int] | None = None, **_: Any) -> OpResult:
        """``:FUNCtion<f>?``: the function's operands as set — the operator word rides the reply
        header (":FUNC1:ADD CHAN1,CHAN2") when headers are on, the definition's reply template."""
        return self.controls.op_definition(suffixes, **_)

    @op("operate", fields=("operator",))
    def op_operate(
        self,
        operator: str = "",
        source1: Any = None,
        source2: Any = None,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """``:FUNCtion<f>:ADD|SUBTract|MULTiply|DIVide <operand>,<operand>``: the operator and both
        operands, checked when written — a missing operand is -109, one that is not a source
        this model knows (or a constant that is not finite) -224 — and the function is left as it
        was; the manual gives two operands for each of these."""
        return self.controls.op_operate(operator, source1, source2, suffixes, **_)

    @op("h_field", fields=("field",))
    def op_h_field(
        self,
        field: str = "horizontal",
        value: Any = None,
        query: bool = False,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """``:FUNCtion<f>:HORizontal``, ``…:POSition`` and ``…:RANGe``: the function's horizontal
        scaling. The operators whose horizontal scaling follows the source's timebase (ADD,
        SUBTract, MULTiply, DIVide, COMMonmode, ABSolute, DIFF, INTegrate, INVert, HIGHpass,
        LOWPass, MAXimum, MINimum, SMOoth, AVERage, SQUare, SQRT — the manual's list) refuse the
        write with -221; on an FFTMagnitude the position and range are the center frequency and
        the span, on the others they are the function's own."""
        return self.controls.op_h_field(field, value, query, suffixes, **_)

    @op("fft_resolution")
    def op_fft_resolution(
        self,
        query: bool = False,
        resolution: float | None = None,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        """``:FUNCtion<f>:FFT:RESolution``: the bandwidth the trace's bins answer — the bin width
        times the window's equivalent noise bandwidth, as the manual defines the resolution. A
        write moves the timebase's scale (the setting ``resolution_scale`` names), as the
        instrument reaches a resolution by sampling longer."""
        return self.fft.op_fft_resolution(query, resolution, suffixes, **_)

    @op("scale")
    def op_scale(
        self, value: Any = None, query: bool = False, suffixes: dict[str, int] | None = None, **_: Any
    ) -> OpResult:
        """``:FUNCtion:SCALe``: the function's vertical scale, units per division — the same
        setup the ``range`` setting holds at full scale, eight divisions (the 2000 X-series
        commands both views of it, Programmer's Guide 02.66 p. 331). The manual's reply is an
        integer in NR1 format."""
        return self.controls.op_scale(value, query, suffixes, **_)
