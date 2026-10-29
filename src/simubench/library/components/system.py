"""Instrument-wide commands a definition points at when its model spells them its own way."""

from __future__ import annotations

from typing import Any

from ...engine.component import Component, OpResult, component, op


@component("system")
class System(Component):
    """``*CLS`` with a reply to its query form, a preset that is ``*RST`` under another header."""

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
