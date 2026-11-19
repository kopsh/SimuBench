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
    STATUS_DEFAULTS,
    SYST_BUILTINS,
    builtin_allowed,
)
from .help_text import headers_reply, syntax_all_reply, syntax_reply

if TYPE_CHECKING:
    from .instrument import Instrument


class BuiltinCommands:
    """The IEEE 488.2 common commands and the model's built-in headers of one instrument (#895): *IDN?, *STB?, *TRG, the clock commands, and which built-ins the model answers."""

    def __init__(self, inst: Instrument) -> None:
        self.inst = inst

    @staticmethod
    def check_params(unit: Unit) -> None:
        """The parameter count of a common command (IEEE 488.2 10.10, 10.34, 7.4; #1453): ``*ESE``,
        ``*SRE`` and ``*PSC`` take exactly one parameter when written (-109 for none, -108 for more) and
        none as a query; every other common command takes none (-108). Raised before the command runs,
        so a refused command changes nothing."""
        word = unit.tokens[0].keyword
        n = len(unit.params)
        if word in ("ESE", "SRE", "PSC") and not unit.query:
            if n == 0:
                raise ScpiError(-109)
            if n > 1:
                raise ScpiError(-108)
        elif n:
            raise ScpiError(-108)

    @staticmethod
    def _register_mask(raw: bytes) -> int:
        """The value written to ``*ESE`` / ``*SRE``: an integer 0-255, else -222 (not wrapped to 8 bits)."""
        value = V.parse_int(raw)
        if not 0 <= value <= 255:
            raise ScpiError(-222, f"register mask {value} outside 0-255")
        return value

    def _common(self, unit: Unit) -> bytes | None:
        self.check_params(unit)
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
            self.inst._ops.disarm_opc()
            return None
        if word == "OPC" and not q:
            self.inst.arm_opc()
            return None
        if word == "ESR" and q:
            return str(self.inst.status.read_esr()).encode()
        if word == "ESE":
            if q:
                return str(self.inst.ese).encode()
            self.inst.ese = self._register_mask(unit.params[0])
            return None
        if word == "SRE":
            if q:
                return str(self.inst.sre).encode()
            self.inst.sre = self._register_mask(unit.params[0])
            return None
        if word == "STB" and q:
            return str(self.stb()).encode()
        if word == "TST" and q:
            return b"0"
        if word == "PSC":
            if q:
                return b"1" if self.inst.psc else b"0"
            if not unit.params:
                raise ScpiError(-109)
            self.inst.psc = V.parse_bool(unit.params[0])
            return None
        raise self.inst._errs._undefined(unit, unit.tokens)

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
            raise self.inst._errs._undefined(unit, unit.tokens)
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
        p = {h: pat for h, pat, _q, _s in SYST_BUILTINS}
        err, count, all_ = p[":SYSTem:ERRor[:NEXT]"], p[":SYSTem:ERRor:COUNt"], p[":SYSTem:ERRor:ALL"]
        date, time_ = p[":SYSTem:DATE"], p[":SYSTem:TIME"]
        help_headers, help_syntax, help_syntax_all = (
            p[":SYSTem:HELP:HEADers"],
            p[":SYSTem:HELP:SYNTax"],
            p[":SYSTem:HELP:SYNTax:ALL"],
        )
        if unit.query and err.match(tokens) is not None:
            return self.inst._errs._error_reply(self.inst.errors.pop())
        if unit.query and count.match(tokens) is not None:
            return str(len(self.inst.errors)).encode()
        if unit.query and all_.match(tokens) is not None:
            items = []
            while len(self.inst.errors):
                items.append(self.inst._errs._error_reply(self.inst.errors.pop()))
            return b",".join(items or [self.inst._errs._error_reply(ScpiError(0))])
        if date.match(tokens) is not None or time_.match(tokens) is not None:
            return self._clock_command(unit, date=date.match(tokens) is not None)
        if unit.query and p[":SYSTem:VERSion"].match(tokens) is not None:
            return b"1999.0"  # the SCPI version every instrument here follows
        if unit.query and help_headers.match(tokens) is not None:
            return headers_reply(self.inst.definition, self.inst.options)
        if unit.query and help_syntax_all.match(tokens) is not None:
            return syntax_all_reply(self.inst.definition, self.inst.options)
        if unit.query and help_syntax.match(tokens) is not None:
            if len(unit.params) != 1:
                raise ScpiError(-109 if not unit.params else -108)
            return syntax_reply(self.inst.definition, self.inst.options, unit.params[0])
        for header, register, part, q_ok, s_ok in STATUS_DEFAULTS:
            if p[header].match(tokens) is not None and (q_ok if unit.query else s_ok):
                return self._status_default(unit, register, part)
        return None

    def _status_default(self, unit: Unit, register: str, part: str) -> bytes:
        """The default STATus registers (#1560): enable and transition registers hold what is written
        (0..65535), the event and condition registers read 0, STATus:PRESet restores the presets."""
        status = self.inst.status
        if part == "preset":
            status.default_preset()
            return _HANDLED
        if unit.query:
            return str(status.default_read(register, part)).encode()
        if not unit.params:
            raise ScpiError(-109)
        value = V.parse_int(unit.params[0])
        if not 0 <= value <= 0xFFFF:
            raise ScpiError(-222)
        status.default_write(register, part, value)
        return _HANDLED

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
        """off, short or long: whether and how replies repeat the command header (syntax.echo_header)."""
        mode = self.inst.definition.syntax.echo_header
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
        """Whether replies are long: long headers and spelled-out enumerations (syntax.long_form)."""
        path = self.inst.definition.syntax.long_form
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
        return self.inst.status.stb(errors=len(self.inst.errors), output=self.inst.has_output, commit=commit)

    def status_event(self, name: str) -> None:
        """A momentary event (a trigger, a range change) for the instrument's status registers:
        every ``status_register`` with a bit of that name latches it (#248, #273)."""
        self.inst.status.event(name)
