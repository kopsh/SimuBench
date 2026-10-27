"""Engineering units: SI prefixes, SCPI numeric suffixes and response formatting."""

from __future__ import annotations

import math
import re

# SCPI suffix multipliers (IEEE 488.2 / SCPI-99 7.7). "M" alone means milli in SCPI;
# "MA" is mega only for Hz ("MHZ") and ohm ("MOHM") where the unit makes it unambiguous.
_PREFIXES = {
    "EX": 1e18,
    "PE": 1e15,
    "T": 1e12,
    "G": 1e9,
    "MA": 1e6,
    "K": 1e3,
    "M": 1e-3,
    "U": 1e-6,
    "N": 1e-9,
    "P": 1e-12,
    "F": 1e-15,
    "A": 1e-18,
}

_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


class UnitError(ValueError):
    """A numeric parameter carries a suffix that does not fit the expected unit."""


def parse_number(text: str, unit: str | None = None) -> float:
    """Parse an SCPI numeric parameter with an optional unit suffix, e.g. ``1.5MHZ`` or ``20mV``.

    ``unit`` is the base unit the parameter expects (``HZ``, ``V``, ``S``, ``OHM``, ``A``...).
    Raises ``ValueError`` when the text is not a number and ``UnitError`` for a wrong suffix.
    """
    s = text.strip()
    m = _NUMBER.match(s)
    if not m:
        raise ValueError(f"not a number: {text!r}")
    value = float(m.group(0))
    suffix = s[m.end() :].strip().upper()
    if not suffix:
        return value
    base = (unit or "").upper()
    if base and suffix == base:
        return value
    # Mega for frequency and resistance: MHZ, MOHM.
    if base in ("HZ", "OHM") and suffix == "M" + base:
        return value * 1e6
    for prefix in sorted(_PREFIXES, key=len, reverse=True):
        if suffix.startswith(prefix):
            rest = suffix[len(prefix) :]
            if rest == base or (not base and rest):
                return value * _PREFIXES[prefix]
    raise UnitError(f"suffix {suffix!r} does not fit unit {unit!r}")


def format_nr3(value: float, digits: int = 6) -> str:
    """Format a float as an IEEE 488.2 NR3 value, e.g. ``+1.000000E+03``."""
    if math.isnan(value):
        return "+9.91000E+37"
    if math.isinf(value):
        return "+9.90000E+37" if value > 0 else "-9.90000E+37"
    return f"{value:+.{digits}E}"


_ENG_PREFIX = [
    (1e12, "T"),
    (1e9, "G"),
    (1e6, "M"),
    (1e3, "k"),
    (1.0, ""),
    (1e-3, "m"),
    (1e-6, "µ"),
    (1e-9, "n"),
    (1e-12, "p"),
]


def format_engineering(value: float, unit: str, digits: int = 4) -> str:
    """Human-readable engineering notation, e.g. ``1.592 kHz``; used in diagnostics and logs."""
    if value == 0 or not math.isfinite(value):
        return f"{value:g} {unit}".strip()
    mag = abs(value)
    for scale, prefix in _ENG_PREFIX:
        if mag >= scale:
            return f"{value / scale:.{digits}g} {prefix}{unit}".strip()
    scale, prefix = _ENG_PREFIX[-1]
    return f"{value / scale:.{digits}g} {prefix}{unit}".strip()
