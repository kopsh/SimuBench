"""What the ScopeMath collaborators ask of the component: the one interface they read it by."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from ...engine.component import Component
from ...scpi import values as V
from ...scpi.errors import ScpiError
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
from .scope_record import CHUNK, Points

if TYPE_CHECKING:
    from ...engine.component import Component
    from .scope_math_controls import Controls
    from .scope_math_fft import Fft


class Operand:
    """One operand of a function as its setting spells it: a channel, another function, the
    model's g(t) source (GOFT), a constant (a float word) or unset."""

    __slots__ = ("channel", "constant", "function", "goft", "word")

    def __init__(self, word: str, channels: int, functions: int) -> None:
        name = word.strip().upper()
        self.word = name
        self.channel: int | None = None
        self.function: int | None = None
        self.goft = name == "GOFT"
        self.constant: float | None = None
        if not name or self.goft:
            return
        for prefix in ("CHANNEL", "CHAN", "CH"):
            if name.startswith(prefix) and name.removeprefix(prefix).isdigit():
                n = int(name.removeprefix(prefix))
                if not 1 <= n <= channels:
                    raise ScpiError(-224, f"{word}: no channel {n}")
                self.channel = n
                return
        for prefix in ("FUNCTION", "FUNC", "F"):
            if name.startswith(prefix) and name.removeprefix(prefix).isdigit():
                f = int(name.removeprefix(prefix))
                if not 1 <= f <= functions:
                    raise ScpiError(-224, f"{word}: no function {f}")
                self.function = f
                return
        try:
            self.constant = float(word)
        except ValueError:
            raise ScpiError(-224, f"{word}: not a source") from None


def aligned(first: Any, second: Any) -> None:
    """Whether two records sit on one sample grid (one acquisition): else the point-wise
    operators over both are a settings conflict, -221."""
    if first.dt_s != second.dt_s or first.origin != second.origin or first.points != second.points:
        raise ScpiError(-221, "the operands are not on one sample grid (acquire them together)")


class MathRecord:
    """A math function's waveform: the operator over its operands' records, computed where it is
    read. It speaks the Record interface the transfer, the measurements and the screen use
    (``points``, ``volts``, ``span``, ``take``, ``chunks``, ``envelope``); a transform's trace
    carries frequencies — ``dt_s`` is a bin's width, the origin the first bin's frequency.

    ``spec`` is the operator fixed at the read that built it (the operator word, its parameters,
    the output's grid and scale); an operator with a bounded neighbourhood is computed span by
    span from the sources' spans, the others (the integral, the filters, the transforms) over the
    whole source record — a source deeper than the model holds is refused, never shortened."""

    def __init__(
        self,
        spec: dict[str, Any],
        sources: list[Any],
        constants: list[float | None],
        volts: np.ndarray | None = None,
    ) -> None:
        self.spec = spec
        self.sources = sources
        self.constants = constants  # one entry per source, the constant where the source is None
        self._volts = volts
        first = next((r for r in sources if r is not None), None)
        self.channel = 0  # a function, not a channel: nothing names one
        self.dt_s = float(spec["dt_s"])
        self.scale = float(spec["scale"])
        self.offset = float(spec["offset"])
        self.triggered = bool(getattr(first, "triggered", True)) if first is not None else True
        self.x_origin_s = float(spec["x_origin_s"])
        self.t_start_ns = int(getattr(first, "t_start_ns", 0)) if first is not None else 0
        self.trigger_index = int(getattr(first, "trigger_index", 0)) if first is not None else 0
        self.x_hz = bool(spec.get("x_hz", False))  # a frequency-domain trace

    @property
    def points(self) -> int:
        if self._volts is not None:
            return int(self._volts.size)
        return int(self.spec["points"])

    @property
    def origin(self) -> float:
        return self.x_origin_s

    @property
    def volts(self) -> np.ndarray:
        """Every sample, in memory (a lazy trace: computed whole)."""
        if self._volts is None:
            self._volts = self.span(0, self.points)
        return self._volts

    def span(self, a: int, b: int) -> np.ndarray:
        """Samples ``a`` to ``b`` (excluded): the operator over the sources' matching spans."""
        a, b = max(0, a), min(self.points, b)
        if b <= a:
            return np.zeros(0)
        if self._volts is not None:
            return self._volts[a:b]
        return apply_span(self, a, b)

    def take(self, index: Any) -> np.ndarray:
        if isinstance(index, Points):  # a transfer's selection: contiguous or strided
            r = index.range
            if not r:
                return np.zeros(0)
            if r.step == 1:
                return self.span(r.start, r.start + len(r))
            return self.volts[r.start : r.start + len(r) * r.step : r.step]
        index = np.asarray(index)
        if index.size == 0:
            return np.zeros(0)
        return self.volts[index]

    def chunks(self, a: int = 0, b: int | None = None) -> Any:
        b = self.points if b is None else b
        for lo in range(a, b, CHUNK):
            yield self.span(lo, min(b, lo + CHUNK))

    def envelope(self, bins: int, key: tuple[object, ...] = ()) -> list[float]:
        edges = np.linspace(0, self.points, bins + 1, dtype=int)
        values = self.volts
        pairs = [
            (float(c.min()), float(c.max()))
            for c in (values[edges[i] : edges[i + 1]] for i in range(bins))
            if c.size
        ]
        return [v for pair in pairs for v in pair]


def apply_span(rec: MathRecord, a: int, b: int) -> np.ndarray:
    """The operator over one span of a lazy record (the whole recipe is in ``rec.spec``)."""
    operator = rec.spec["operator"]
    params = rec.spec["params"]
    src = rec.sources[0]
    if operator in ("ADD", "SUBTract", "MULTiply", "DIVide", "COMMonmode"):
        second = rec.sources[1]
        if src is not None and second is not None:
            aligned(src, second)
        va = src.span(a, b) if src is not None else np.full(b - a, rec.constants[0])
        vb = second.span(a, b) if second is not None else np.full(b - a, rec.constants[1])
        return pointwise(operator, va, vb)
    if operator in ("ABSolute", "INVert", "SQUare", "SQRT", "MAGNify", "LN", "LOG", "EXP", "TEN"):
        return pointwise(operator, src.span(a, b))
    if operator == "LINear":
        return src.span(a, b) * float(params["gain"]) + float(params["offset"])
    if operator in ("DELay", "GATing"):
        off = int(rec.spec["index0"])
        return src.span(a + off, b + off)
    if operator == "DIFF":
        w = 1  # one neighbour each side; the ends are one-sided, as over the whole record
        lo, hi = max(0, a - w), min(src.points, b + w)
        out = derivative(src.span(lo, hi), rec.dt_s)
        return out[a - lo : a - lo + (b - a)]
    if operator in ("SMOoth", "AVERage"):
        w = max(1, int(params["window"])) // 2
        lo, hi = max(0, a - w), min(src.points, b + w)
        out = running_mean(src.span(lo, hi), max(1, int(params["window"])))
        return out[a - lo : a - lo + (b - a)]
    if operator in ("MAXimum", "MINimum"):
        m = max(1, int(params["bucket"]))
        if m == 1:
            return src.span(a, b)
        return bucket_extreme(src.span(a * m, b * m), m, operator == "MAXimum")
    raise ScpiError(-300, f"operator {operator!r} has no span")


def _suffix(suffixes: dict[str, int] | None) -> int:
    """The ``<f>`` a command carried; 1 when it carried none."""
    if not suffixes:
        return 1
    for key in ("f", "F"):
        if key in suffixes:
            return int(suffixes[key])
    return int(next(iter(suffixes.values())))


def function_index(source: Any) -> int | None:
    """The function a source word names (FUNCtion<F>/FUNC<F>/F<F>), None when it names no
    function — how a transfer or a measurement tells a math source from a channel."""
    if isinstance(source, bytes):
        source = V.parse_string(source)
    word = str(source or "").strip().upper()
    for prefix in ("FUNCTION", "FUNC", "F"):
        if word.startswith(prefix) and word.removeprefix(prefix).isdigit():
            return int(word.removeprefix(prefix))
    return None


def _source_function(source: Any, functions: int) -> int:
    """The function a measurement's ``<source>`` names (FUNCtion<F>/FUNC<F>/F<F>); -224 else."""
    f = function_index(source)
    if f is not None and 1 <= f <= functions:
        return f
    raise ScpiError(-224, f"{source!r}: not a function")


def _in_dbm(value: float, unit: str, impedance: float) -> float:
    """A trace value turned into dBm, whatever its unit (the peak threshold is in dBm)."""
    if unit in ("WATT", "WATTHZ"):
        return 10.0 * math.log10(value / 1e-3) if value > 0 else -math.inf
    if unit in ("VRMS", "VRTHZ"):
        p = value * value / impedance
        return 10.0 * math.log10(p / 1e-3) if p > 0 else -math.inf
    if unit == "DBV":
        return value - 10.0 * math.log10(impedance) + 30.0
    if unit == "DBMV":
        return value - 60.0 + 30.0 - 10.0 * math.log10(impedance)
    if unit == "DBUV":
        return value - 120.0 + 30.0 - 10.0 * math.log10(impedance)
    return value  # DB and DBMHZ are dBm already


class MathHost(Protocol):
    """What the math family's blocks (controls, fft) read of their component. The instrument's
    own state and services are members here, not an ``owner`` to reach through (#1724): a block
    reads a setting by :meth:`get_state`, writes one by :meth:`set_state`, asks whether the
    definition declares a path by :meth:`has_state` and formats a reply number by
    :meth:`format_number`."""

    def format_number(self, value: float) -> bytes: ...
    def function(self, f: int) -> int: ...
    def get_state(self, path: str) -> Any: ...
    def has_state(self, path: str) -> bool: ...
    def measure_function(self, f: int) -> tuple[MathRecord, int]: ...
    def operator(self, f: int) -> str: ...

    def param(self, key: str) -> Any: ...
    def ref(self, key: str) -> Component | None: ...
    def resolve_path(self, path: str) -> str: ...
    def set_state(self, path: str, value: Any, *, source: str = "scpi", internal: bool = False) -> None: ...
    def setting(self, f: int, name: str, default: Any = None) -> Any: ...
    @property
    def uid(self) -> str: ...

    fft: Fft
    controls: Controls
