"""Conversion of SCPI parameters to typed values and of typed values to responses."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

from ..units import UnitError, format_nr3, parse_number
from .errors import ScpiError

if TYPE_CHECKING:
    from ..models.definition import StateSpec

#: The numeric keywords every numeric parameter takes (IEEE 488.2 / SCPI). INFinity and NINFinity are
#: the long forms of INF and NINF: both forms parse alike, so one word gets one error code (#360).
SPECIAL_NUMERIC = {"MIN", "MINIMUM", "MAX", "MAXIMUM", "DEF", "DEFAULT", "NAN"} | {
    "INF",
    "INFINITY",
    "NINF",
    "NINFINITY",
}


def short_form(word: str) -> str:
    """Short form of a mixed-case SCPI word: ``SINusoid`` -> ``SIN``. A compound word keeps its
    colons, each node shortened: ``VOLTage:AC`` -> ``VOLT:AC``."""
    if ":" in word:
        return ":".join(short_form(node) for node in word.split(":"))
    s = "".join(c for c in word if c.isupper() or c.isdigit())
    return s or word.upper()


def _text(raw: bytes) -> str:
    return raw.decode("ascii", errors="replace").strip()


def unquote(raw: bytes) -> str | None:
    t = _text(raw)
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        q = t[0]
        return t[1:-1].replace(q * 2, q)
    return None


#: The base of each IEEE 488.2 non-decimal numeric prefix letter (7.7.4).
_NONDECIMAL = {"H": 16, "Q": 8, "B": 2}


def parse_float(
    raw: bytes,
    unit: str | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    default: float | None = None,
) -> float:
    t = _text(raw)
    up = t.upper()
    if up in SPECIAL_NUMERIC:
        if up.startswith("MIN"):
            if minimum is None:
                raise ScpiError(-224, "no minimum defined")
            return minimum
        if up.startswith("MAX"):
            if maximum is None:
                raise ScpiError(-224, "no maximum defined")
            return maximum
        if up.startswith("DEF"):
            if default is None:
                raise ScpiError(-224, "no default defined")
            return default
        if up in ("INF", "INFINITY"):
            return math.inf
        if up in ("NINF", "NINFINITY"):
            return -math.inf
        return math.nan
    if unquote(raw) is not None:
        raise ScpiError(-104, f"expected a number, got a string {t!r}")
    if len(t) > 1 and t[0] == "#" and up[1] in _NONDECIMAL:
        # IEEE 488.2 7.7.4 non-decimal numeric data: #H hex, #Q octal, #B binary (#1497)
        base = _NONDECIMAL[up[1]]
        digits = up[2:]
        if not digits or any(ch not in "0123456789ABCDEF"[:base] for ch in digits):
            raise ScpiError(-104, f"expected a number, got {t!r}")
        return float(int(digits, base))
    if t.startswith("(") and t.endswith(")") and not t.startswith("(@"):
        t = t[1:-1].strip()  # a number in parentheses: expression data of one number (53230A "(200)", #385)
    try:
        return parse_number(t, unit)
    except UnitError as exc:
        raise ScpiError(-131, str(exc)) from None
    except ValueError:
        raise ScpiError(-104, f"expected a number, got {t!r}") from None


def parse_int(raw: bytes, **kw: float | None) -> int:
    v = parse_float(raw, **kw)  # type: ignore[arg-type]
    if not math.isfinite(v):
        raise ScpiError(-224, "integer expected")
    return round(v)


def parse_bool(raw: bytes) -> bool:
    t = _text(raw).upper()
    if t in ("ON", "1"):
        return True
    if t in ("OFF", "0"):
        return False
    try:
        v = float(t)
    except ValueError:
        raise ScpiError(-224, f"expected ON, OFF, 1 or 0, got {t!r}") from None
    if not math.isfinite(v):  # INF/NINF/NAN are not a boolean (#1450)
        raise ScpiError(-224, f"expected ON, OFF, 1 or 0, got {t!r}")
    return round(v) != 0


def flag(value: Any) -> bool:
    """A boolean-ish value as a plain bool: real booleans and numbers as themselves (so 1 is
    True, like the runtime's ``bool(value)``), ON/OFF/1/0 words as their meaning. Components read
    one boolean setting through this, not through five private spellings."""
    if isinstance(value, str):
        return value.strip().upper() in ("1", "ON", "TRUE")
    return bool(value)


def matches_word(text: str, word: str) -> bool:
    """Whether ``text`` is the short or the long form of a mixed-case SCPI ``word``; quotes around
    ``text`` do not count (a string parameter, ``"CAP"``). A compound word (``VOLTage:AC``, the
    8588A's function names) matches node by node, each node long or short (``VOLT:ACURRENT`` no,
    ``VOLTAGE:AC`` and ``VOLT:AC`` yes)."""
    q = unquote(text.encode(errors="replace"))
    t = (q if q is not None else text).strip().upper()
    if t in (word.upper(), short_form(word)):
        return True
    nodes, words = t.split(":"), word.split(":")
    return (
        len(words) > 1
        and len(nodes) == len(words)
        and all(n in (w.upper(), short_form(w)) for n, w in zip(nodes, words, strict=True))
    )


def parse_enum(raw: bytes, options: Sequence[str]) -> str:
    t = unquote(raw)
    t = (t if t is not None else _text(raw)).upper()
    for opt in options:
        if matches_word(t, opt):
            return opt
    raise ScpiError(-224, f"{t!r} is not one of {', '.join(short_form(o) for o in options)}")


def fit_enum(value: Any, options: Sequence[str]) -> str | None:
    """The option a written value denotes: the value itself when it is one of the ``options``,
    otherwise the option it names in any SCPI word form (``VOLT`` for ``VOLTage``, node by node
    for compounds) — the same rule the parse layer applies to a word arriving over SCPI. None
    when the value denotes no option."""
    if value in options:
        return value
    if isinstance(value, str):
        return next((o for o in options if matches_word(value, o)), None)
    return None


def fit_value(spec: StateSpec, value: Any) -> tuple[str | None, str | None]:
    """Whether a value written for a setting — a definition constant (``writes``, ``sets``,
    ``keywords``), a bench state or a panel value — fits the setting's declaration, judged the
    way the engine judges the same value at run time (``Instrument._validate``, plus the parse
    layer for words): an enum accepts the option in any SCPI word form, a special value is
    exempt from a number's range on both bounds, out of range is the same tolerance. Pure: no
    exception; returns ``(problem, fix)`` — both None when the value fits. ``fix`` is the
    suggestion for the report, and when a word form fits only by naming the option, the option
    as written (``VOLTage`` for ``VOLT``): a panel key must write the option itself, since the
    UI lights the key by comparing it with the stored value.

    The word forms are refused nowhere here; whether a site reports them is its own rule (the
    panel does, the definition's constants do not). The problems are messages for the report,
    value-centred so a site can use them as they are or prefix its own subject.
    """
    if spec.type == "enum":
        options = spec.options or []
        canonical = fit_enum(value, options)
        if canonical is None:
            return f"不接受 {value!r}", f"可选值：{', '.join(options)}"
        return None, canonical if canonical != value else None
    if spec.type == "bool":
        return (None, None) if isinstance(value, bool) else (f"{value!r} 不是 true / false", None)
    if spec.type in ("float", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{value!r} 不是数值", None
        if value in set((spec.special or {}).values()):
            return None, None
        # The same tolerance the engine applies (#407): a bound's float representation error
        # must not refuse a value the run time accepts. NaN is out of range there (-222), so
        # it is here too.
        if (
            value != value
            or (spec.min is not None and value < spec.min - abs(spec.min) * 1e-12 - 1e-300)
            or (spec.max is not None and value > spec.max + abs(spec.max) * 1e-12 + 1e-300)
        ):
            return f"{value!r} 超出范围 [{spec.min}, {spec.max}]", None
        return None, None
    if spec.type == "string":
        return (None, None) if isinstance(value, str) else (f"{value!r} 不是字符串", None)
    return None, None  # int_list / enum_list: the word layers of their own kinds check the items


def parse_string(raw: bytes) -> str:
    q = unquote(raw)
    return q if q is not None else _text(raw)


def parse_float_list(params: Sequence[bytes], unit: str | None = None) -> list[float]:
    """A list given either as one quoted string ``"1,2,3"`` or as separate parameters ``1,2,3`` (D13)."""
    if len(params) == 1:
        q = unquote(params[0])
        if q is not None:
            items = [x.strip() for x in q.split(",") if x.strip()]
            return [parse_float(x.encode(), unit) for x in items]
    return [parse_float(p, unit) for p in params]


def parse_block(raw: bytes) -> bytes:
    if not raw.startswith(b"#") or len(raw) < 2 or not raw[1:2].isdigit():
        raise ScpiError(-160, "expected a definite-length block")
    nd = int(raw[1:2])
    if nd == 0:
        return raw[2:]
    size = int(raw[2 : 2 + nd])
    payload = raw[2 + nd : 2 + nd + size]
    if len(payload) != size:
        raise ScpiError(-160, f"block says {size} bytes, got {len(payload)}")
    return payload


def parse_channel_list(
    raw: bytes,
    exists: Callable[[int], bool] | None = None,
    *,
    descending: bool = False,
    repeated: bool = False,
    empty: bool = False,
) -> list[int]:
    """A SCPI channel list ``(@1)``, ``(@1:3)``, ``(@101,102:110)`` — expanded, in list order.

    ``@`` is required; an empty list is an error unless the instrument declares it (``empty``,
    #418: the DAQ970A's ``ROUT:SCAN (@)``). A descending range (-222) and a given-twice
    channel (-108) are errors too unless the instrument declares them (#260): ``descending``
    expands ``(@2:1)`` in its written order, 2 then 1; ``repeated`` keeps every occurrence.
    ``exists`` is the instrument's channel-number domain (#207): an explicitly written number and
    both ends of a range must exist (-114), and numbers inside a range that the domain does not
    declare are skipped — the 34970A manual's rule for a range that spans slots.
    """
    t = _text(raw).strip()
    if not (t.startswith("(@") and t.endswith(")")):
        raise ScpiError(-102, f"expected a channel list (@…), got {t!r}")
    body = t[2:-1].strip()
    if not body:
        if empty:
            return []
        raise ScpiError(-108, "the channel list is empty")
    out: list[int] = []
    for part in (p.strip() for p in body.split(",")):
        first, sep, last = part.partition(":")
        try:
            lo = int(first.strip())
            hi = int(last.strip()) if sep else lo
        except ValueError:
            raise ScpiError(-102, f"bad channel {part!r}") from None
        if hi < lo and not descending:
            raise ScpiError(-222, f"channel range {part!r} descends")
        if exists is not None:
            for n in (lo, hi):
                if not exists(n):
                    raise ScpiError(-114, f"channel {n} does not exist")
        step = 1 if hi >= lo else -1
        for n in range(lo, hi + step, step):
            if exists is not None and not exists(n):
                continue  # a gap between declared channels, e.g. 121-199 of a 3-slot DAQ
            if n in out and not repeated:
                raise ScpiError(-108, f"channel {n} is given twice")
            out.append(n)
    return out


def format_channel_list(channels: Sequence[int], ranges: bool = True) -> bytes:
    """A stored channel list as ``(@101:110)``: consecutive numbers collapsed when `ranges`."""
    ch = [int(c) for c in channels]
    parts: list[str] = []
    i = 0
    while i < len(ch):
        j = i
        while ranges and j + 1 < len(ch) and ch[j + 1] == ch[j] + 1:
            j += 1
        parts.append(f"{ch[i]}:{ch[j]}" if j > i else str(ch[i]))
        i = j + 1
    return ("(@" + ",".join(parts) + ")").encode()


# ---- responses ----------------------------------------------------------------------------------


def format_float(
    v: float, digits: int = 6, *, style: str = "nr3", sign: bool = True, exponent_digits: int = 2
) -> bytes:
    """A number as a response: NR3 ``+1.000000E+03`` (default), NR2 ``+1000.000000``, NR1 ``+1000``
    or general (C ``%g``, ``digits`` significant: ``2.3346669042e-09``, ``19.749698639``).

    Infinity and NaN are written as the SCPI sentinels 9.9E+37 and 9.91E+37 in the same format.
    """
    v = float(v)
    if style == "nr3" and digits == 6 and sign and exponent_digits == 2:
        return format_nr3(v, digits).encode()  # the long-standing default, sentinels included
    if not math.isfinite(v):
        v = 9.91e37 if math.isnan(v) else math.copysign(9.9e37, v)
    lead = "-" if v < 0 else ("+" if sign else "")
    a = abs(v)
    if style == "nr1":
        return f"{lead}{round(a)}".encode()
    if style == "nr2":
        return f"{lead}{a:.{digits}f}".encode()
    if style == "general":  # C %g: `digits` significant, lowercase e, no trailing zeros (#383)
        return f"{lead}{a:.{max(1, digits)}g}".encode()
    mantissa, exp = f"{a:.{digits}E}".split("E")
    e = int(exp)
    return f"{lead}{mantissa}E{'+' if e >= 0 else '-'}{abs(e):0{exponent_digits}d}".encode()


_SI = {12: "T", 9: "G", 6: "M", 3: "k", 0: "", -3: "m", -6: "u", -9: "n", -12: "p"}


def _exp3_with_carry(
    v: float, mantissa_of: Callable[[float], str], *, cap: int | None = None
) -> tuple[int, str]:
    """The engineering exponent of ``v`` (a multiple of 3, floor of log10 of the magnitude) with
    the rounding carry corrected: a mantissa that rounds up to 1000 takes the next prefix
    (``999.5`` -> ``1.00E+03``), at most two steps. The mantissa text comes from
    ``mantissa_of(v / 10.0**exp)`` — the sign travels in it (``format_engineering`` spells
    ``-3.52334E+12``, not a leading minus on a positive mantissa); the carry test reads its
    magnitude. ``cap`` ends the ladder (``format_si``'s prefixes stop at T/p) — at the cap the
    mantissa stays at 1000 and the exponent does not grow."""

    def text_at(exp: int) -> str:
        return mantissa_of(v / 10.0**exp)

    a = abs(v)
    exp = 0 if a == 0 else math.floor(math.log10(a) / 3) * 3
    if cap is not None:
        exp = max(-cap, min(cap, exp))
    text = text_at(exp)
    for _ in range(2):
        if abs(float(text)) < 1000 or (cap is not None and exp >= cap):
            break
        exp += 3
        text = text_at(exp)
    return exp, text


def format_si(v: float, significant: int = 9, unit: str = "", *, sign: bool = False) -> bytes:
    """A number with an SI prefix and a unit, ``significant`` digits in all (#289): ``4.00000000MHz``
    (9), ``1.50000000000GHz`` (12), ``400.000000MHz`` (9). The prefix is the one that puts the
    mantissa in [1, 1000); infinity and NaN fall back to the SCPI sentinels in NR3."""
    v = float(v)
    if not math.isfinite(v):
        return format_float(v)
    lead = "-" if v < 0 else ("+" if sign else "")
    a = abs(v)

    def mantissa(m: float) -> str:
        whole = len(str(int(m))) if m >= 1 else 1
        return f"{m:.{max(0, significant - whole)}f}"

    exp3, text = _exp3_with_carry(a, mantissa, cap=12)
    return f"{lead}{text}{_SI[exp3]}{unit}".encode()


def format_engineering(v: float) -> bytes:
    """Engineering notation, shortest mantissa: ``200E-3``, ``10E+3``, ``1.5E+0`` (LeCroy-style replies)."""
    v = float(v)
    if v == 0 or not math.isfinite(v):
        return b"0E+0" if v == 0 else (b"9.9E+37" if v > 0 else b"-9.9E+37")
    exp, text = _exp3_with_carry(v, lambda m: f"{m:.6g}")
    return f"{text}E{exp:+d}".encode()


def format_engineering_digits(
    v: float, significant: int, *, sign: bool = True, exponent_digits: int = 2
) -> bytes:
    """Engineering notation with ``significant`` digits: ``+40.0E+00``, ``+0.00000E+00``,
    ``+60.0000000000000E-09`` (Keysight InfiniiVision replies). Not finite: ``+9.9E+37``."""
    v = float(v)
    lead = "-" if v < 0 else ("+" if sign else "")
    if not math.isfinite(v):
        return f"{'-' if v < 0 else lead}9.9E+{37:0{exponent_digits}d}".encode()
    a = abs(v)

    def mantissa(m: float) -> str:
        whole = 1 if m < 10 else 2 if m < 100 else 3
        return f"{m:.{max(0, significant - whole)}f}"

    exp, text = _exp3_with_carry(a, mantissa)  # the mantissa is unsigned; the lead carries the sign
    return f"{lead}{text}E{'+' if exp >= 0 else '-'}{abs(exp):0{exponent_digits}d}".encode()


def parse_multiplied(raw: bytes, multipliers: dict[str, float]) -> float | None:
    """A number with a multiplier suffix (``10K``, ``2.5MA``), or None when it has none of them."""
    text = _text(raw).strip().upper()
    for suffix in sorted(multipliers, key=len, reverse=True):
        if text.endswith(suffix.upper()) and len(text) > len(suffix):
            try:
                return float(text[: -len(suffix)]) * multipliers[suffix]
            except ValueError:
                return None
    return None


def format_int(v: int) -> bytes:
    return str(int(v)).encode()


def format_bool(v: bool, word: bool = False) -> bytes:
    if word:
        return b"ON" if v else b"OFF"
    return b"1" if v else b"0"


def format_enum(v: str, long: bool = False, as_defined: bool = False) -> bytes:
    if as_defined:
        return v.encode()
    return (v.upper() if long else short_form(v)).encode()


def format_string(v: str) -> bytes:
    return ('"' + v.replace('"', '""') + '"').encode()


def format_block(payload: bytes) -> bytes:
    """IEEE 488.2 definite-length arbitrary block."""
    size = str(len(payload)).encode()
    return b"#" + str(len(size)).encode() + size + payload


def encode_words(codes: np.ndarray, little_endian: bool = True, unsigned: bool = False) -> bytes:
    dtype = ("<" if little_endian else ">") + ("u2" if unsigned else "i2")
    return np.asarray(codes).astype(dtype).tobytes()
