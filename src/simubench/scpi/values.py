"""Conversion of SCPI parameters to typed values and of typed values to responses."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from ..units import UnitError, format_nr3, parse_number
from .errors import ScpiError

SPECIAL_NUMERIC = {"MIN", "MINIMUM", "MAX", "MAXIMUM", "DEF", "DEFAULT", "INF", "NINF", "NAN"}


def short_form(word: str) -> str:
    """Short form of a mixed-case SCPI word: ``SINusoid`` -> ``SIN``."""
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
        if up == "INF":
            return math.inf
        if up == "NINF":
            return -math.inf
        return math.nan
    if unquote(raw) is not None:
        raise ScpiError(-104, f"expected a number, got a string {t!r}")
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
        return round(float(t)) != 0
    except ValueError:
        raise ScpiError(-224, f"expected ON, OFF, 1 or 0, got {t!r}") from None


def parse_enum(raw: bytes, options: Sequence[str]) -> str:
    t = unquote(raw)
    t = (t if t is not None else _text(raw)).upper()
    for opt in options:
        if t in (opt.upper(), short_form(opt)):
            return opt
    raise ScpiError(-224, f"{t!r} is not one of {', '.join(short_form(o) for o in options)}")


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


# ---- responses ----------------------------------------------------------------------------------


def format_float(v: float, digits: int = 6) -> bytes:
    return format_nr3(float(v), digits).encode()


def format_int(v: int) -> bytes:
    return str(int(v)).encode()


def format_bool(v: bool) -> bytes:
    return b"1" if v else b"0"


def format_enum(v: str) -> bytes:
    return short_form(v).encode()


def format_string(v: str) -> bytes:
    return ('"' + v.replace('"', '""') + '"').encode()


def format_block(payload: bytes) -> bytes:
    """IEEE 488.2 definite-length arbitrary block."""
    size = str(len(payload)).encode()
    return b"#" + str(len(size)).encode() + size + payload


def encode_words(codes: np.ndarray, little_endian: bool = True, unsigned: bool = False) -> bytes:
    dtype = ("<" if little_endian else ">") + ("u2" if unsigned else "i2")
    return np.asarray(codes).astype(dtype).tobytes()
