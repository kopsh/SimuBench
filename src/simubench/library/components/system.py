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
    def reset_instrument(self, **_: Any) -> OpResult:
        """The same default setup as ``*RST`` (``:SYSTem:PRESet``)."""
        assert self.owner is not None
        self.owner.reset()
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
