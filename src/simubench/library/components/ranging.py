"""Range selection and reading-time rules shared by the meter core and the source-measure unit
(metrology-authoring.md 2, 3.4).

The rules are the ones SCPI prescribes and #87 introduced on ``dmm_core``: a manual range setting
takes the smallest range that can measure it; the auto range follows the true value, switching
when it leaves the hysteresis band ``[autorange_down, autorange_up] × full scale``. Choosing a
range takes no virtual time.
"""

from __future__ import annotations

from typing import Any


def ranges_of(spec: Any, function: str) -> list[float]:
    """The function's range full-scale values, ascending (empty: that function does not range).

    A ranges parameter may be one list for every function or a dict per function; a function the
    dict does not name does not range."""
    if spec is None:
        return []
    if isinstance(spec, dict):
        if function not in spec:
            return []
        spec = spec[function]
    return sorted(float(x) for x in spec)


def fitting_range(ranges: list[float], value: float) -> float | None:
    """The smallest range that can measure ``value`` (the largest range when none can)."""
    for r in ranges:
        if r >= abs(value) * (1 - 1e-12):
            return r
    return ranges[-1] if ranges else None


def range_of_setting(ranges: list[float], setting: Any) -> float | None:
    """The manual range: the smallest range that can measure the range setting (SCPI's rule).

    ``None`` when nothing usable is set (the instrument autoranges)."""
    if isinstance(setting, bool) or not isinstance(setting, (int, float)) or setting <= 0:
        return None
    return fitting_range(ranges, setting)


def auto_range(
    ranges: list[float], current: float | None, true: float, up: float, down: float
) -> float | None:
    """The auto-range decision for a true value: within the hysteresis band
    ``down .. up ×`` the range's full scale the range stays, outside it (or without one yet) the
    smallest range that fits the value is taken."""
    high = up * current if current is not None else None
    low = down * current if current is not None else None
    if current is None or not low <= abs(true) <= high:
        return fitting_range(ranges, true)
    return current


def reading_time(nplc: float, line_freq: float, autozero: bool) -> float:
    """Seconds of one reading at ``nplc`` power line cycles: 50 µs of settling, the integration
    (again for the autozero phase), 185 µs per phase of range and settle, firmware."""
    phases, firmware = (3, 1.8e-3) if autozero else (1, 0.3e-3)
    return 50e-6 + phases * (nplc / line_freq + 185e-6) + firmware
