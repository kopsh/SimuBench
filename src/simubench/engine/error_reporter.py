"""Instrument runtime: state, components, signal wiring and the SCPI command layer (architecture.md 3)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..models.definition import ParamSpec, StateSpec
from ..scpi.errors import ESR_BITS, ScpiError
from ..scpi.parse import Token, Unit
from .command_runner import (
    NOT_MODELED,
)

if TYPE_CHECKING:
    from .instrument import Instrument


class _Ignored(Exception):
    """An unknown header on an instrument whose definition ignores unknown commands."""


#: Parameter errors a definition's declaration explains (tui-design.md 1.1): out of range (-222),
#: wrong type (-104), a value outside the declared options (-224). -224 with the "not modeled by
#: SimuBench" marker is a declared modeling gap instead, not a bad parameter.
BAD_PARAMETER_CODES = frozenset({-104, -222, -224})


def _declared_range(spec: StateSpec | ParamSpec | None) -> str | None:
    """The range or options a definition declares, compactly: '-210 … 210 V' or 'SINusoid, SQUARE'."""
    if spec is None:
        return None
    options = getattr(spec, "options", None)
    if options:
        return ", ".join(options)
    lo, hi = getattr(spec, "min", None), getattr(spec, "max", None)
    if lo is None and hi is None:
        return None
    unit = getattr(spec, "unit", None) or ""
    low = "-∞" if lo is None else f"{lo:g}"
    high = "∞" if hi is None else f"{hi:g}"
    return f"{low} … {high}{f' {unit}' if unit else ''}"


class ErrorReporter:
    """Reports what went wrong for one instrument (#895): pushes device errors on the queue, formats the ``SYST:ERR?`` reply, and classifies a header or parameter the definition does not accept (tui-design.md 1.1)."""

    def __init__(self, inst: Instrument) -> None:
        self.inst = inst

    def _push(self, err: ScpiError) -> None:
        detail = err.detail
        if err.text is None:
            table = self.inst.definition.errors.get(err.code)
            if table is not None or self.inst.real:
                # A real model answers with its manual's text only; the detail stays in the event.
                err = replace(err, text=table if table is not None else err.standard_text)
        self.inst.errors.push(err)
        self.inst.status.set_bit(ESR_BITS[err.esr_class])
        data: dict[str, Any] = {"code": err.code, "message": err.message}
        if err.text is not None and detail and detail not in err.message:
            data["message"] = f"{err.message} ({detail})"
        reason = err.reason
        if reason is None and (
            (err.text is not None and err.text.endswith(NOT_MODELED)) or NOT_MODELED in detail
        ):
            reason = "not_modeled"  # a command, or a parameter value, this definition does not implement
        if reason is not None:
            data["reason"] = reason
        for key in ("origin", "closest", "param", "range", "gap", "blocked_by", "note"):
            value = getattr(err, key)
            if value is not None:
                data[key] = value
        self.inst.session.instrument_event(self.inst, "error", data)

    def _error_reply(self, err: ScpiError) -> bytes:
        """An error queue entry as :SYSTem:ERRor? writes it, in the manual's form for a real model."""
        if err.code == 0 and 0 in self.inst.definition.errors:
            err = ScpiError(0, text=self.inst.definition.errors[0])
        sign = "+" if self.inst.definition.format.error_code == "signed" and err.code >= 0 else ""
        return f'{sign}{err.code},"{err.message}"'.encode()

    def _undefined(self, unit: Unit, tokens: tuple[Token, ...], *, builtin: bool = False) -> ScpiError:
        """-113 for a header the definition does not have, classified by where its declaration
        lives (tui-design.md 1.1): the coverage table, the definition, or the model's ``builtins``.
        The reply says the header is not implemented *in this definition*: the basis (a vendor
        manual, a design specification or a contract) belongs to the definition's author (#172).

        ``builtin``: a built-in header the definition's ``builtins`` leaves out. Raises
        :class:`_Ignored` instead when the definition ignores unknown commands.
        """
        header = unit.raw_header
        if self.inst.definition.unknown_commands == "ignore":
            raise _Ignored(header)
        if builtin:
            return ScpiError(
                -113,
                header,
                text=self._undefined_text(header),
                reason="builtin_excluded",
                origin=self.inst.source_file,
            )
        reason, origin, closest = self.inst.coverage.classify(unit, tokens)
        # A command the coverage table lists as not modeled carries the table's why (#1037).
        listed = self.inst.coverage.listed(unit, tokens) if reason == "not_modeled" else None
        return ScpiError(
            -113,
            header,
            text=self._undefined_text(header),
            reason=reason,
            origin=origin,
            closest=closest,
            gap=listed.gap if listed else None,
            blocked_by=listed.blocked_by if listed else None,
            note=listed.note if listed else None,
        )

    def _undefined_text(self, header: str) -> str | None:
        """The -113 reply: a definition with a reference says the header is not implemented in it."""
        if not self.inst.real:
            return None
        base = self.inst.definition.errors.get(-113, ScpiError(-113).standard_text)
        return f"{base};{NOT_MODELED}"

    def push_error(self, code: int, detail: str = "") -> None:
        """Queue a device error from a component (e.g. an overload)."""
        self._push(ScpiError(code, detail))

    def _parameter_error(self, err: ScpiError) -> ScpiError:
        """A parameter error a command raised without naming its parameter (a component's check):
        still classified, with the declared range left out (tui-design.md 1.1). Device errors never
        come through here — they are queued with :meth:`push_error` instead."""
        if err.reason is None and err.code in BAD_PARAMETER_CODES and NOT_MODELED not in err.detail:
            return replace(err, reason="bad_parameter")
        return err

    def _bad_parameter(
        self, err: ScpiError, param: str | None, spec: StateSpec | ParamSpec | None
    ) -> ScpiError:
        """A parameter error raised where the definition's declaration is known: name the parameter
        and its declared range. The "not implemented in this definition" refusals keep their own category."""
        if err.reason is not None or err.code not in BAD_PARAMETER_CODES or NOT_MODELED in err.detail:
            return err
        return replace(
            err,
            reason="bad_parameter",
            origin=self.inst.source_file,
            param=param,
            range=_declared_range(spec),
        )
