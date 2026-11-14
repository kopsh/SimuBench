"""Instrument-wide commands a definition points at when its model spells them its own way."""

from __future__ import annotations

from typing import Any

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError


@component("system")
class System(Component):
    """``*CLS`` with a reply to its query form, a preset that is ``*RST`` under another header, and
    the error queue under the model's own headers (metrology-authoring.md 5.3): ``next_error`` and
    ``error_count`` let a definition declare an error query that is not ``:SYSTem:ERRor?`` (Fluke's
    ``ERR?``), with the reply format given by the command's ``reply`` template (``{code},{text}``)."""

    defaults = {
        "clear_reply": "0",  # what the query form of the clear-status command answers (*CLS? on LeCroy)
    }

    @op("clear_status")
    def clear_status(self, query: bool = False, **_: Any) -> OpResult:
        """Clear the error queue and the event status; a query also answers ``clear_reply``."""
        assert self.owner is not None
        self.owner.errors.clear()
        self.owner.esr = 0
        return OpResult(str(self.param("clear_reply")).encode() if query else None)

    @op("reset")
    def reset_instrument(self, then: dict[str, Any] | None = None, **_: Any) -> OpResult:
        """The same default setup as ``*RST`` (``:SYSTem:PRESet``); ``then`` (the command's
        ``args``) names settings the preset leaves differently from ``*RST``, written after it — the
        X-Series preset turns continuous sweep on where ``*RST`` turns it off (#616)."""
        assert self.owner is not None
        self.owner.reset()
        for path, value in (then or {}).items():
            self.owner.set_state(path, value, internal=True)
        return OpResult()

    @op("next_error", fields=("code", "text"))
    def next_error(self, **_: Any) -> OpResult:
        """The next entry of the error queue, as the ``{code}`` and ``{text}`` fields of the
        command's reply template; an empty queue gives code 0. The code is written like
        ``:SYSTem:ERRor?`` writes it (a ``+`` when the definition's ``format.error_code`` is
        signed), the text is the definition's ``errors`` entry when it has one."""
        assert self.owner is not None
        err = self.owner.errors.pop()
        if err.code == 0 and 0 in self.owner.definition.errors:
            err = ScpiError(0, text=self.owner.definition.errors[0])
        sign = "+" if self.owner.definition.format.error_code == "signed" and err.code >= 0 else ""
        return OpResult(b"", fields={"code": f"{sign}{err.code}", "text": err.message})

    @op("error_count")
    def error_count(self, **_: Any) -> OpResult:
        """How many entries the error queue holds (a model's own error count query)."""
        assert self.owner is not None
        return OpResult(V.format_int(len(self.owner.errors)))

    @op("accept")
    def accept(self, **_: Any) -> OpResult:
        """A command the model parses and stores without an effect on the simulation (an accepted
        command, #1068): the parameters' ``state`` writes carry the values, the op itself does
        nothing. The empty response lets the command's ``reply`` template answer a query from the
        stored settings."""
        return OpResult(b"")

    @op("capability")
    def capability(self, what: str = "", **answers: Any) -> OpResult:
        """``:SYSTem:CAPability:*?`` (the guide, pp. 1437-1439, #1067): the definition's ``answers``
        carry each capability word's reply, the query names one of them."""
        word = V.parse_string(what).upper() if isinstance(what, bytes) else str(what).upper()
        table = {str(k).upper(): v for k, v in answers.items()}
        answer = table.get(word)
        if answer is None:
            raise ScpiError(-224, f"{word}: no such capability")
        return OpResult(f'"{answer}"'.encode())

    @op("limits")
    def limits(self, path: str = "", kind: str = "numeric", words: str = "", channel: int | None = None, **_: Any) -> OpResult:  # fmt: skip
        """``…:TESTLIMITS?`` (the guide's ``<n>,<type><limits>`` shape, pp. 324/340/352/496/552/554,
        #1067): a numeric setting answers its declared min:max, an enumeration its valid settings
        (the definition's ``words``). A ``ch.``-prefixed path resolves on the command's channel."""
        assert self.owner is not None
        if str(kind).lower() == "enum":
            return OpResult(f"1,<enum>{words}".encode())
        resolved = f"ch{channel}.{path[3:]}" if path.startswith("ch.") and channel is not None else path
        lo, hi = self.owner.limits(resolved)
        if lo is None or hi is None:
            raise ScpiError(-300, f"{resolved}: the definition declares no limits")
        lo_word = self.owner.format_number(lo).decode().strip()
        hi_word = self.owner.format_number(hi).decode().strip()
        return OpResult(f"1,<numeric>{lo_word}:{hi_word}".encode())

    @op("cal_output")
    def cal_output(self, query: bool = False, signal: str = "", dc: float | None = None, path: str = "", **_: Any) -> OpResult:  # fmt: skip
        """``:CALibrate:OUTPut``: the front-panel output's signal (the guide, p. 473) — the DC form
        carries the level and the query answers them together, so the setting at ``path`` stores
        the composed reply and the command's reply template answers a query. The signal itself is
        not modeled (#1170)."""
        assert self.owner is not None
        if query:
            return OpResult(b"")
        if not signal:
            raise ScpiError(-109, "a signal is required")
        owner = self.owner
        word = V.parse_string(signal).upper() if isinstance(signal, bytes) else str(signal).upper()
        word = {"PROBECOMP": "PROB", "DPULSE": "DPUL", "NDPULSE": "NDPUL"}.get(word, word)
        level = "" if dc is None else "," + owner.format_number(float(dc)).decode().strip()
        value = f"{word}{level}"
        self.owner.set_state(path, value)
        return OpResult(b"")
