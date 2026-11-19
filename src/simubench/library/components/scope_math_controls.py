"""The math function controls: a function's definition, its operands, the H fields and the scale. Part of ``ScopeMath`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math
from typing import Any

from ...engine.component import OpResult
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope_math_host import MathHost, Operand, _suffix
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


class Controls:
    def __init__(self, host: MathHost) -> None:
        self.host = host

    def op_definition(self, suffixes: dict[str, int] | None = None, **_: Any) -> OpResult:
        f = self.host.function(_suffix(suffixes))
        parts = []
        for which in (1, 2):
            word = str(self.host.setting(f, f"source{which}", "") or "")
            if which == 2 and (self.host.operator(f) in UNARY or not word):
                continue
            parts.append(word)
        return OpResult(",".join(parts).encode())

    def op_operate(
        self,
        operator: str = "",
        source1: Any = None,
        source2: Any = None,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        f = self.host.function(_suffix(suffixes))
        word = CANONICAL.get(str(operator).strip().upper())
        if word is None:
            raise ScpiError(-224, f"{operator}: no such operator")
        words = [str(w).strip() for w in (source1, source2) if w is not None and str(w).strip()]
        if len(words) < 2:
            raise ScpiError(-109, f"{word} takes two operands")
        channels, functions = int(self.host.param("channels")), int(self.host.param("functions"))
        for w in words:
            operand = Operand(w, channels, functions)
            if operand.constant is not None and not math.isfinite(operand.constant):
                raise ScpiError(-224, f"{w}: a constant operand must be finite")
        assert self.host.owner is not None
        prefix = f"{self.host.param('prefix')}{f}"
        for name, value in (("operator", word), ("source1", words[0]), ("source2", words[1])):
            self.host.owner.set_state(f"{prefix}.{name}", value, source="scpi", internal=True)
        return OpResult()

    def op_h_field(
        self,
        field: str = "horizontal",
        value: Any = None,
        query: bool = False,
        suffixes: dict[str, int] | None = None,
        **_: Any,
    ) -> OpResult:
        f = self.host.function(_suffix(suffixes))
        operator = self.host.operator(f)
        if operator.upper() in _TIMEBASE_BOUND:
            raise ScpiError(
                -221,
                f"{operator} follows the source's timebase: use :TIMebase:POSition/:RANGe",
            )
        fft = operator in FFT_OPERATORS
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if field == "horizontal":
            if query or value is None:
                return OpResult(str(self.host.setting(f, "horizontal", "AUTO") or "AUTO").encode())
            word = V.parse_string(value if isinstance(value, bytes) else str(value).encode()).upper()
            if word not in ("AUTO", "MANUAL"):
                raise ScpiError(-224, f"{value}: AUTO or MANual")
            return self._set_h(f, "horizontal", "MANual" if word == "MANUAL" else "AUTO")
        # position/range: a frequency on a transform, seconds elsewhere
        name = {"position": "center" if fft else "h_position", "range": "span" if fft else "h_range"}[field]
        path_value = self.host.setting(f, name)
        if query or value is None:
            if fft and path_value is None:
                # an FFT without an explicit window answers its whole one-sided span
                pair = self.host.measure_function(f)
                g = pair[0].spec
                return OpResult(
                    self.host._number(
                        float(g["x_origin_s"])
                        if field == "position"
                        else float(g["points"]) * float(g["dt_s"])
                    )
                )
            if path_value is None:
                return OpResult(self.host._number(0.0))
            return OpResult(self.host._number(float(path_value)))
        if isinstance(value, bytes):
            number = float(V.parse_number(value.decode("ascii", errors="replace")))
        elif isinstance(value, str):
            number = float(V.parse_number(value))
        else:
            number = float(value)
        return self._set_h(f, name, number)

    def _set_h(self, f: int, name: str, value: Any) -> OpResult:
        """Write one of the function's horizontal settings, marking the mode manual when the
        manual says a position/range write does (``horizontal`` AUTO → MANual)."""
        assert self.host.owner is not None
        if name in ("center", "span", "h_position", "h_range"):
            h_path = f"{self.host.param('prefix')}{f}.horizontal"
            if h_path in self.host.owner.definition.state and str(
                self.host.owner.get_state(h_path) or "AUTO"
            ).upper().startswith("AUTO"):
                self.host.owner.set_state(h_path, "MANual", source="scpi", internal=True)
        self.host.owner.set_state(
            f"{self.host.param('prefix')}{f}.{name}", value, source="scpi", internal=True
        )
        return OpResult()

    def op_scale(
        self, value: Any = None, query: bool = False, suffixes: dict[str, int] | None = None, **_: Any
    ) -> OpResult:
        f = self.host.function(_suffix(suffixes))
        full = 8.0  # the range the scale is an eighth of
        rng = self.host.setting(f, "range", 8.0)
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if query:
            scale = float(rng if rng is not None else full) / full
            return OpResult(str(round(scale)).encode())
        if value is None:
            raise ScpiError(-109)
        text = value.decode("ascii", errors="replace") if isinstance(value, bytes) else value
        try:
            if isinstance(text, str) and text.strip().startswith("#"):
                number = float(V.parse_float(text.encode()))  # #H / #Q / #B: IEEE 488.2 7.7.4 (#1497, #1496)
            elif isinstance(text, str):
                number = float(V.parse_number(text))
            else:
                number = float(text)
        except ValueError:
            raise ScpiError(-104, f"{value!r}: not a number") from None
        return self._set_h(f, "range", number * full)
