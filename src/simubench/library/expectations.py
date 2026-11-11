"""What a case step expects of a reply, judged without regular expressions (D49, #926).

A reply is judged by what it means: a number is parsed and compared with the expected value ±
a tolerance, or kept within ``min`` / ``max``; text is compared exactly or by its start; a list
is split into fields and each field is judged on its own; an IEEE 488.2 block by its length,
header and contents. Where the manual fixes how a value is written (the digits of an NR3 reply,
a date), a ``format`` says so: a named format (``unsigned`` digits, ``integer`` / ``nr1``
with an optional sign, ``nr2``, ``nr3``, ``number``) or a picture, in which ``d`` is one digit, ``±`` a sign that must be there, ``~`` a
sign that may be there, and every other character itself (``±d.ddddddddE±dd``, ``dddd,dd,dd``).

A regular expression judged how a value happened to be printed, so it passed wrong magnitudes and
failed right ones when the digits moved (#924, #926); none of these does.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

NAMED_FORMATS = ("unsigned", "integer", "nr1", "nr2", "nr3", "number")
DIGITS = "0123456789"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _is_digits(text: str) -> bool:
    return bool(text) and all(c in DIGITS for c in text)


def _unsigned(text: str) -> str:
    return text[1:] if text[:1] in "+-" else text


def _nr2(text: str) -> bool:
    """IEEE 488.2 NR2: an optional sign, digits, a decimal point, digits (``-12.50``, ``+.5``)."""
    whole, point, frac = _unsigned(text).partition(".")
    return (
        bool(point)
        and (whole == "" or _is_digits(whole))
        and (frac == "" or _is_digits(frac))
        and bool(whole + frac)
    )


def _nr3(text: str) -> bool:
    """IEEE 488.2 NR3: an NR1 or NR2 mantissa, ``E`` or ``e``, a signed or unsigned exponent."""
    head, e, exponent = text.replace("e", "E").partition("E")
    mantissa = _unsigned(head)
    return bool(e) and (_is_digits(mantissa) or _nr2(head)) and _is_digits(_unsigned(exponent))


def matches_format(text: str, fmt: str) -> bool:
    """Whether ``text`` is written in ``fmt`` (a named format or a picture, see the module doc)."""
    if fmt == "unsigned":
        return _is_digits(text)
    if fmt in ("integer", "nr1"):
        return _is_digits(_unsigned(text))
    if fmt == "nr2":
        return _nr2(text)
    if fmt == "nr3":
        return _nr3(text)
    if fmt == "number":
        try:
            float(text)
        except ValueError:
            return False
        return True
    i = 0
    for p in fmt:
        c = text[i : i + 1]
        if p == "~":  # an optional sign
            if c and c in "+-":
                i += 1
            continue
        if p == "d":
            ok = c != "" and c in DIGITS
        elif p == "±":
            ok = c != "" and c in "+-"
        else:
            ok = c == p
        if not ok:
            return False
        i += 1
    return i == len(text)


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


class FieldSpec(_Model):
    """One field of a reply. Every key given must hold; a bare string is the exact field, a bare
    number the field as a number within the step's ``tolerance``."""

    value: float | None = Field(None, description="The field as a number, within tolerance")
    tolerance: float | None = Field(
        None, description="Absolute tolerance of value (default: the step's, else 0)"
    )
    min: float | None = Field(None, description="The field as a number is at least this")
    max: float | None = Field(None, description="The field as a number is at most this")
    unit: str | None = Field(None, description="The number is followed by a space and this unit (2.05E+0 V)")
    format: str | None = Field(None, description="How the field is written: a named format or a picture")
    one_of: list[str] | None = Field(None, description="The field is exactly one of these")
    prefix: str | None = Field(
        None, description='The field starts with this text; a number is read after it ("VOLT +1.0E+01)'
    )
    suffix: str | None = Field(None, description="The field ends with this text; a number is read before it")
    contains: list[str] | None = Field(None, description="The field contains each of these texts")
    length: int | None = Field(None, ge=0, description="The field has exactly this many characters")
    nonempty: bool = Field(False, description="The field is not empty (any value)")
    count: int = Field(1, ge=1, description="This spec stands for this many consecutive fields")

    @model_validator(mode="after")
    def _check(self) -> FieldSpec:
        if self.format is not None and not self.format:
            raise ValueError("format is empty")
        if self.tolerance is not None and self.value is None:
            raise ValueError("tolerance goes with value")
        return self

    def _written(self, field: str) -> str | None:
        """The number's text: the field without ``prefix``, ``suffix`` and `` <unit>``."""
        if self.prefix is not None:
            if not field.startswith(self.prefix):
                return None
            field = field[len(self.prefix) :]
        if self.suffix is not None:
            if not field.endswith(self.suffix):
                return None
            field = field[: len(field) - len(self.suffix)]
        if self.unit is not None:
            number, space, written = field.rpartition(" ")
            if not space or written != self.unit:
                return None
            field = number
        return field

    def judge(self, field: str, step_tolerance: float) -> bool:
        if self.nonempty and not field:
            return False
        if self.length is not None and len(field) != self.length:
            return False
        if self.one_of is not None and field not in self.one_of:
            return False
        if self.contains is not None and not all(part in field for part in self.contains):
            return False
        written = self._written(field)
        if written is None:  # the prefix, suffix or unit is not there
            return False
        if self.format is not None and not matches_format(written, self.format):
            return False
        if self.value is None and self.min is None and self.max is None:
            return True
        number = _to_float(written)
        if number is None:
            return False
        tol = self.tolerance if self.tolerance is not None else step_tolerance
        if self.value is not None and abs(number - self.value) > tol:
            return False
        if self.min is not None and number < self.min:
            return False
        return self.max is None or number <= self.max

    def describe(self) -> str:
        parts = []
        if self.value is not None:
            parts.append(f"{self.value!r}" + (f" ± {self.tolerance!r}" if self.tolerance is not None else ""))
        if self.min is not None or self.max is not None:
            parts.append(
                f"in [{self.min if self.min is not None else '-∞'}, {self.max if self.max is not None else '∞'}]"
            )
        if self.unit is not None:
            parts.append(f"in {self.unit}")
        if self.format is not None:
            parts.append(f"written {self.format}")
        if self.one_of is not None:
            parts.append(f"one of {self.one_of!r}")
        if self.prefix is not None:
            parts.append(f"after {self.prefix!r}")
        if self.suffix is not None:
            parts.append(f"before {self.suffix!r}")
        if self.contains is not None:
            parts.append(f"containing {self.contains!r}")
        if self.length is not None:
            parts.append(f"{self.length} characters")
        if self.nonempty and not parts:
            parts.append("any")
        text = ", ".join(parts) or "any"
        return f"{self.count} × ({text})" if self.count > 1 else text


Field_ = Annotated[str | float | FieldSpec, Field(union_mode="left_to_right")]


def _spec(field: str | float | FieldSpec) -> FieldSpec:
    if isinstance(field, FieldSpec):
        return field
    if isinstance(field, str):
        return FieldSpec(one_of=[field])
    return FieldSpec(value=float(field))


class Fields(_Model):
    """A reply cut into fields: ``prefix`` and ``suffix`` (exact) around them, ``separator``
    between them; there are exactly as many fields as the specs stand for."""

    fields: list[Field_]
    separator: str = ","
    prefix: str = ""
    suffix: str = ""

    @model_validator(mode="after")
    def _check(self) -> Fields:
        if not self.fields:
            raise ValueError("fields is empty")
        if not self.separator:
            raise ValueError("separator is empty")
        return self

    def specs(self) -> list[FieldSpec]:
        out: list[FieldSpec] = []
        for f in self.fields:
            spec = _spec(f)
            out.extend([spec] * spec.count)
        return out

    def judge(self, reply: str, tolerance: float) -> bool:
        if not (reply.startswith(self.prefix) and reply.endswith(self.suffix)):
            return False
        if len(reply) < len(self.prefix) + len(self.suffix):
            return False
        body = reply[len(self.prefix) : len(reply) - len(self.suffix)]
        parts = body.split(self.separator)
        specs = self.specs()
        return len(parts) == len(specs) and all(
            s.judge(p, tolerance) for s, p in zip(specs, parts, strict=True)
        )

    def describe(self) -> str:
        shown = "; ".join(_spec(f).describe() if not isinstance(f, str) else repr(f) for f in self.fields)
        around = f" between {self.prefix!r} and {self.suffix!r}" if self.prefix or self.suffix else ""
        return f"fields split by {self.separator!r}{around}: {shown}"


def as_fields(value: list[Any] | Fields) -> Fields:
    return value if isinstance(value, Fields) else Fields(fields=value)


class Block(_Model):
    """An IEEE 488.2 block reply: ``#<n><length><data>`` (definite) or ``#0<data>`` (indefinite),
    then one of the terminators in ``end``."""

    header: str | None = Field(
        None, description="The exact block header (#800001000; #0 for an indefinite block)"
    )
    bytes: int | None = Field(None, ge=0, description="The number of data bytes")
    indefinite: bool = Field(False, description="An indefinite-length block (#0), ended by the terminator")
    starts: str | None = Field(None, description="The data starts with this text")
    contains: list[str] | None = Field(None, description="The data contains each of these texts")
    fields: list[Field_] | Fields | None = Field(None, description="The data (ASCII) judged as fields")
    end: str | list[str] = Field(
        ["", "\n"], description="What follows the data: the terminator, or a list of accepted ones"
    )

    def _ends(self) -> list[str]:
        return [self.end] if isinstance(self.end, str) else list(self.end)

    def splits(self, reply: str) -> list[tuple[str, str, str]]:
        """Every reading of the reply as (header, data, terminator) this block allows."""
        if not reply.startswith("#") or len(reply) < 2 or reply[1] not in DIGITS:
            return []
        n = int(reply[1])
        if n == 0:
            if not self.indefinite:
                return []
            return [
                ("#0", reply[2 : len(reply) - len(t)], t)
                for t in self._ends()
                if reply.endswith(t) and len(reply) - len(t) >= 2
            ]
        if self.indefinite:
            return []
        digits = reply[2 : 2 + n]
        if len(digits) != n or not _is_digits(digits):
            return []
        start = 2 + n
        data = reply[start : start + int(digits)]
        if len(data) != int(digits):
            return []
        rest = reply[start + len(data) :]
        return [(reply[:start], data, rest)] if rest in self._ends() else []

    def _data_ok(self, data: str, tolerance: float) -> bool:
        if self.bytes is not None and len(data) != self.bytes:
            return False
        if self.starts is not None and not data.startswith(self.starts):
            return False
        if self.contains is not None and not all(part in data for part in self.contains):
            return False
        return self.fields is None or as_fields(self.fields).judge(data, tolerance)

    def judge(self, reply: str, tolerance: float) -> bool:
        return any(
            (self.header is None or header == self.header) and self._data_ok(data, tolerance)
            for header, data, _ in self.splits(reply)
        )

    def describe(self) -> str:
        parts = ["an indefinite block" if self.indefinite else "a definite block"]
        if self.header is not None:
            parts.append(f"header {self.header!r}")
        if self.bytes is not None:
            parts.append(f"{self.bytes} bytes")
        if self.starts is not None:
            parts.append(f"starting {self.starts!r}")
        if self.contains is not None:
            parts.append(f"containing {self.contains!r}")
        if self.fields is not None:
            parts.append(as_fields(self.fields).describe())
        parts.append(f"then {self._ends()!r}")
        return ", ".join(parts)
