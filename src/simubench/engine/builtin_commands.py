"""Instrument runtime: state, components, signal wiring and the SCPI command layer (architecture.md 3)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..scpi import values as V
from ..scpi.errors import ScpiError
from ..scpi.parse import Token, Unit
from .command_runner import (
    _HANDLED,
)
from .commands import (
    SYST_BUILTINS,
    builtin_allowed,
)

if TYPE_CHECKING:
    from .instrument import Instrument


class BuiltinCommands:
    """The IEEE 488.2 common commands and the model's built-in headers of one instrument (#895): *IDN?, *STB?, *TRG, the clock commands, and which built-ins the model answers."""

    def __init__(self, inst: Instrument) -> None:
        self.inst = inst

    def _common(self, unit: Unit) -> bytes | None:
        word = unit.tokens[0].keyword
        q = unit.query
        if word == "IDN" and q:
            return self.idn.encode()
        if word == "OPT" and q and self.inst.definition.identity.options_reply is not None:
            return self.opt().encode()
        if word == "RST" and not q:
            self.inst.reset()
            return None
        if word == "CLS" and not q:
            self.inst.errors.clear()
            self.inst.status.clear()
            self.inst.ops.opc_armed = False
            return None
        if word == "OPC" and not q:
            self.inst.arm_opc()
            return None
        if word == "ESR" and q:
            return str(self.inst.status.read_esr()).encode()
        if word == "ESE":
            if q:
                return str(self.inst.ese).encode()
            self.inst.ese = V.parse_int(unit.params[0]) & 0xFF if unit.params else 0
            return None
        if word == "SRE":
            if q:
                return str(self.inst.sre).encode()
            self.inst.sre = V.parse_int(unit.params[0]) & 0xFF if unit.params else 0
            return None
        if word == "STB" and q:
            return str(self.stb()).encode()
        if word == "TST" and q:
            return b"0"
        raise self.inst.errs._undefined(unit, unit.tokens)

    def opt(self) -> str:
        """The *OPT? reply of a definition with ``identity.options_reply`` (D68): the options this
        unit has, in the definition's order, or the reply for none."""
        ident = self.inst.definition.identity
        have = [o.id for o in self.inst.definition.options if o.id in self.inst.options]
        if not have:
            return ident.options_none
        return (ident.options_reply or "{options}").replace("{options}", ident.options_separator.join(have))

    def _bus_trigger(self, unit: Unit) -> tuple[bytes | None, int]:
        """``*TRG``: the bus trigger of every component with a ``bus_trigger`` operation (each
        channel's trigger model of a multi-channel SMU, #382). A component that refuses it (not
        waiting for a trigger) is left out while another one takes it; when none takes it, the
        first one's error stands — a single trigger model answers exactly as before."""
        fns = [fn for comp in self.inst.components.values() if (fn := comp.operation("bus_trigger"))]
        if not fns:
            raise self.inst.errs._undefined(unit, unit.tokens)
        response: bytes | None = None
        duration = 0
        first_error: ScpiError | None = None
        taken = False
        for fn in fns:
            try:
                result = fn()
            except ScpiError as e:
                first_error = first_error or e
                continue
            taken = True
            response = response if response is not None else result.response
            duration = max(duration, result.duration_ns)
        if not taken and first_error is not None:
            raise first_error
        return response, duration

    def _builtin(self, unit: Unit, tokens: tuple[Token, ...]) -> bytes | None:
        """A built-in command's reply; ``_HANDLED`` for one without a reply, None when not built in."""
        err, count, all_, date, time_ = (p for _h, p, _q, _s in SYST_BUILTINS)
        if unit.query and err.match(tokens) is not None:
            return self.inst.errs._error_reply(self.inst.errors.pop())
        if unit.query and count.match(tokens) is not None:
            return str(len(self.inst.errors)).encode()
        if unit.query and all_.match(tokens) is not None:
            items = []
            while len(self.inst.errors):
                items.append(self.inst.errs._error_reply(self.inst.errors.pop()))
            return b",".join(items or [self.inst.errs._error_reply(ScpiError(0))])
        if date.match(tokens) is not None or time_.match(tokens) is not None:
            return self._clock_command(unit, date=date.match(tokens) is not None)
        return None

    def _clock_command(self, unit: Unit, *, date: bool) -> bytes:
        """:SYSTem:DATE yyyy,mm,dd and :SYSTem:TIME hh,mm,ss (IEEE 488.2 style), on the session calendar."""
        now = self.inst.clock()
        if unit.query:
            if date:
                return f"{now.year:04d},{now.month:02d},{now.day:02d}".encode()
            return f"{now.hour:02d},{now.minute:02d},{now.second + now.microsecond / 1e6:06.3f}".encode()
        if len(unit.params) != 3:
            raise ScpiError(-109 if len(unit.params) < 3 else -108)
        try:
            a, b, c = (float(V.parse_float(p)) for p in unit.params)
            if date:
                target = now.replace(year=int(a), month=int(b), day=int(c))
            else:
                sec = int(c)
                target = now.replace(
                    hour=int(a), minute=int(b), second=sec, microsecond=round((c - sec) * 1e6)
                )
        except ValueError:
            raise ScpiError(-222) from None
        self.inst.clock_offset += target - now
        return _HANDLED

    def _builtin_allowed(self, header: str) -> bool:
        return builtin_allowed(self.inst.definition, header)

    def echo_mode(self) -> str:
        """off, short or long: whether and how replies repeat the command header (dialect.echo_header)."""
        mode = self.inst.definition.dialect.echo_header
        if mode.startswith("$"):
            value = self.inst.state.get(mode[1:], "off")
            if isinstance(value, bool):  # a header switch (Infiniium :SYSTem:HEADer, #602)
                value = "short" if value else "off"
            mode = str(value)
        mode = mode.lower()
        if mode == "short" and self.long_form():
            return "long"
        return mode

    def long_form(self) -> bool:
        """Whether replies are long: long headers and spelled-out enumerations (dialect.long_form)."""
        path = self.inst.definition.dialect.long_form
        return bool(path) and bool(self.inst.state.get(path[1:], False))

    @property
    def idn(self) -> str:
        d = self.inst.definition
        template = self.inst.instance.idn or d.identity.idn
        # {$path}: a setting's text (the Infiniium's :SYSTem:PERSona changes the manufacturer and
        # model *IDN? answers); braces in the value cannot reach format().
        template = re.sub(
            r"\{\$([A-Za-z0-9_.]+)\}",
            lambda m: str(self.inst.state.get(m.group(1), "")).replace("{", "{{").replace("}", "}}"),
            template,
        )
        return template.format(
            manufacturer=d.manufacturer.upper(),
            model=d.model,
            serial=self.inst.serial,
            firmware=self.inst.firmware,
        )

    def stb(self, commit: bool = True) -> int:
        """The status byte. ``commit=False`` only looks: the condition transitions the status
        registers have not latched yet stay unlatched, so an observer cannot change what a later
        ``*ESR?`` says."""
        return self.inst.status.stb(
            errors=len(self.inst.errors), output=bool(self.inst._output), commit=commit
        )

    def status_event(self, name: str) -> None:
        """A momentary event (a trigger, a range change) for the instrument's status registers:
        every ``status_register`` with a bit of that name latches it (#248, #273)."""
        self.inst.status.event(name)
