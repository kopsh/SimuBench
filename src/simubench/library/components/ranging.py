"""Range selection and reading-time rules shared by the meter core, the source-measure unit and
the DMM conditioning (metrology-authoring.md 2, 3.4).

The rules are the ones SCPI prescribes and #87 introduced on ``dmm_conditioning``: a manual range setting
takes the smallest range that can measure it; the auto range follows the true value, switching
when it leaves the hysteresis band ``[autorange_down, autorange_up] × full scale``. Choosing a
range takes no virtual time.
"""

from __future__ import annotations

from typing import Any, Literal


def ranges_of(
    spec: Any, function: str, *, missing: Literal["empty", "error"] = "empty", owner: str = ""
) -> list[float]:
    """The function's range full-scale values, ascending (empty: that function does not range).

    A ranges parameter may be one list for every function or a dict per function. A function the
    dict does not name does not range (``missing="empty"``); a meter whose ranges must cover
    every selectable function takes ``missing="error"`` and names itself in ``owner`` — the same
    refusal ``DmmConditioning`` has always raised."""
    if spec is None:
        return []
    if isinstance(spec, dict):
        if function not in spec:
            if missing == "error":
                raise ValueError(f"{owner}: ranges have no entry for the {function} function")
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
    """The auto-range decision for a true value (``ranges`` ascending, full scales).

    - **Stay**: while ``down × current <= |true| <= up × current`` (both ends inclusive) the
      range in use is kept, so a value near a boundary does not toggle between two ranges.
    - **Up**: above ``up × current`` (``up`` is over 1: the range's overrange, e.g. 1.2), and
    - **Down**: below ``down × current`` (``down`` under 1, e.g. 0.1),
      the smallest range that fits ``|true|`` is taken — in one step, however many ranges away,
      not one range at a time. It fits without overrange, so after a switch up the value sits at
      or under full scale; after a switch down it may sit under ``down ×`` the new range too
      (nothing smaller fits), and the next call keeps that range.
    - **None yet** (``current`` is None): the smallest range that fits.
    - A value beyond the largest range takes the largest range; no ranges gives None."""
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
