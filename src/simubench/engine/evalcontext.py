"""The ambient inputs of an evaluation (signal-chain.md 5.2, D67, #1509).

What a signal or a frame is depends on the signals' version, on the question asked (the port, a
frame's request) and on a few things the asker sets around the whole read — a receiver's tuning
point, the instant a time-varying block is evaluated for. Those last are :class:`EvalContext`:
one frozen value, held by the session (``Session.context``), changed only by entering a block
(``Session.evaluating``, which ``tuned`` and ``at_instant`` are the named forms of).

The context is the tail of both evaluation caches' keys *as a whole*, so a field added here is
keyed by construction: no read point and key have to be kept in step by hand, which is the way
the frame cache once served one tuning's frame for another (#955, #1486).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EvalContext(BaseModel):
    """The ambient inputs of an evaluation; the default is "now, not inside a tuned read".

    Frozen, so hashable: the value itself is the tail of the caches' keys."""

    model_config = ConfigDict(frozen=True)

    #: A receiver's tuning point, Hz (``Session.tuned``); None: not inside a tuned read.
    tune_hz: float | None = None
    #: The instant evaluated for, ns (``Session.at_instant``); None: now.
    at_ns: int | None = None
