"""Number and time formatting for the TUI (tui-screens.md 1.4).

Engineering prefixes ``p n µ m (none) k M G``; settings at 5 significant digits, readings at the
panel's digit count; units via the state variable's display unit (``HZ`` → ``Hz``, ``OHM`` → ``Ω``).
Virtual time reads ``HH:MM:SS.mmm``.
"""

from __future__ import annotations

import math
from datetime import datetime

UNIT_FIXES = {"OHM": "Ω", "HZ": "Hz", "S": "s"}

_PREFIXES: list[tuple[float, str]] = [
    (1e9, "G"),
    (1e6, "M"),
    (1e3, "k"),
    (1.0, ""),
    (1e-3, "m"),
    (1e-6, "µ"),
    (1e-9, "n"),
    (1e-12, "p"),
]


def fix_unit(unit: str | None) -> str:
    """``HZ`` → ``Hz``, ``OHM`` → ``Ω``, ``S`` → ``s``; anything else unchanged."""
    return UNIT_FIXES.get((unit or "").upper(), unit) if unit else ""


def fmt(value: float | int | None, unit: str | None = None, digits: int = 5) -> str:
    """One value with its unit: ``15.000 V``, ``10.000 mA``, ``1.0000 kHz``.

    ``digits`` significant digits, engineering prefix, one space before the unit. An infinite
    resistance is a high-impedance load (高阻); other infinities and NaN read ``∞`` / ``—``.
    """
    unit = fix_unit(unit)
    if value is None:
        return "—"
    if isinstance(value, bool):  # a bool never reaches here (enums and bools render by name)
        return "1" if value else "0"
    value = float(value)
    if math.isnan(value):
        return "—"
    if math.isinf(value):
        if unit == "Ω" and value > 0:
            return "高阻"
        return ("∞" if value > 0 else "-∞") + (f" {unit}" if unit else "")
    scale, prefix = 1.0, ""
    if value != 0:
        for s, p in _PREFIXES:
            if abs(value) >= s * 0.9999995:
                scale, prefix = s, p
                break
        else:
            return f"{value:.3e}" + (f" {unit}" if unit else "")
    mantissa = value / scale
    whole = len(str(int(abs(mantissa)))) if abs(mantissa) >= 1 else 1
    decimals = max(0, min(digits - whole, 12))
    text = f"{mantissa:.{decimals}f}"
    if unit:
        text += f" {prefix}{unit}"
    elif prefix:
        text = f"{mantissa:.{decimals}f} {prefix}"  # a bare prefix keeps its spacing
    return text


def fmt_ns(now_ns: int | float | None) -> str:
    """Virtual time ``HH:MM:SS.mmm`` (now_ns truncated to milliseconds)."""
    if now_ns is None:
        return "—"
    ms = int(now_ns) // 1_000_000
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def fmt_wall(wall: float | None) -> str:
    """Real time ``HH:MM:SS.mmm`` in the local time zone."""
    if wall is None:
        return "—"
    dt = datetime.fromtimestamp(wall).astimezone()
    return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def fmt_seconds(seconds: float) -> str:
    """A duration for the log: ``推进 <dt>`` reads ``10 ms``, ``1 s``."""
    return fmt(seconds, "S")


def fit(text: str, width: int) -> str:
    """Truncate to ``width`` terminal cells with ``…`` (CJK characters count 2)."""
    if cell_len(text) <= width:
        return text + " " * (width - cell_len(text))
    out, cells = "", 0
    for ch in text:
        w = cell_len(ch)
        if cells + w > width - 1:
            return out + "…" + " " * max(0, width - cells - 1)
        out += ch
        cells += w
    return out


def cell_len(text: str) -> int:
    """Display width in terminal cells: East Asian wide characters count 2."""
    return sum(2 if is_wide(ch) else 1 for ch in text)


def is_wide(ch: str) -> bool:
    """Whether the character occupies two terminal cells (East Asian Width W or F)."""
    import unicodedata

    return unicodedata.east_asian_width(ch) in ("W", "F")


def pad(text: str, width: int) -> str:
    """Pad to ``width`` cells with spaces (right-align with ``pad`` on a pre-raligned string)."""
    return text + " " * max(0, width - cell_len(text))


def ralign(text: str, width: int) -> str:
    """Right-align ``text`` in ``width`` cells."""
    return " " * max(0, width - cell_len(text)) + text


#: Braille dot bits per row: (left column, right column); rows top to bottom (U+2800–U+28FF).
_BRAILLE_ROWS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def _braille_column(chunk: list[float], lo: float, span: float, side: int) -> int:
    """Dot bits of one 1-dot-wide column (side 0 = left dots, 1 = right dots)."""
    bits = 0
    if chunk:
        cmin, cmax = min(chunk), max(chunk)
        top = round((1 - (cmax - lo) / span) * 3)  # row 0 is the top
        bottom = round((1 - (cmin - lo) / span) * 3)
        top, bottom = min(top, bottom), max(top, bottom)
        for row in range(max(0, top), min(3, bottom) + 1):
            bits |= _BRAILLE_ROWS[row][side]
    return bits


def braille(wave: list[float], cols: int) -> str:
    """A waveform thumbnail ``cols`` braille characters wide, 4 dot rows high.

    Each character covers two columns of samples (left and right dots); the dots between a
    column's minimum and maximum are lit, so peaks stay visible at this size.
    """
    if not wave:
        return "⠀" * cols
    lo, hi = min(wave), max(wave)
    span = (hi - lo) or 1.0
    per = max(1, math.ceil(len(wave) / (cols * 2)))
    columns = [wave[c * per : (c + 1) * per] for c in range(cols * 2)]
    out = []
    for c in range(cols):
        bits = _braille_column(columns[2 * c], lo, span, 0)
        bits |= _braille_column(columns[2 * c + 1], lo, span, 1)
        out.append(chr(0x2800 + bits))
    return "".join(out)
