"""The caches of an evaluation: the signals of output ports and the frames of sample streams
(D67), and the count of samples one read asks for along a chain (#1508).

Both caches are keyed by the signals' version, the port and the evaluation context
(:class:`~.evalcontext.EvalContext`, as a whole: #1486, #1509); a frame's key also has its request.
They hold no reference to the session: it passes the version and the context in, so what a result
may depend on is exactly the key's arguments and nothing else.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from .evalcontext import EvalContext
from .signal import Signal
from .stream import (
    FRAME_CACHE_SAMPLES,
    FRAME_CACHE_SIZE,
    MAX_REQUEST_FANOUT,
    FrameRequest,
    SampleFrame,
    beyond_model,
    check_request,
)

if TYPE_CHECKING:
    from .component import Component

#: Output signals kept before the cache starts over.
OUTPUT_CACHE_SIZE = 4096


class EvalCache:
    def __init__(self) -> None:
        #: (signal version, uid, port, context) -> the port's signal.
        self._outputs: dict[tuple[Any, ...], Signal] = {}
        #: Frames of the sample streams, newest last: (signal version, uid, port, request, context)
        #: -> (frame, samples asked for along the chain to make it).
        self._frames: OrderedDict[tuple[Any, ...], tuple[SampleFrame, int]] = OrderedDict()
        #: One entry per :meth:`frames` call in progress: the samples it has asked for so far.
        self._asks: list[int] = []
        self._cached_samples = 0  # samples the frame cache holds

    def output(self, version: int, comp: Component, port: str, ctx: EvalContext) -> Signal:
        key = (version, comp.uid, port, ctx)
        hit = self._outputs.get(key)
        if hit is None:
            if len(self._outputs) > OUTPUT_CACHE_SIZE:
                self._outputs.clear()
            hit = comp.output(port)
            self._outputs[key] = hit
        return hit

    def frames(
        self, version: int, comp: Component, port: str, request: FrameRequest, ctx: EvalContext
    ) -> SampleFrame:
        """A samples output port's frame for one request (signal-chain.md 3.2). The cache is small
        and only serves repeated requests within a read and the internal probe; a new signal
        version drops it. Every sample asked for along the chain counts toward
        ``MAX_REQUEST_FANOUT`` — a cached frame counts what it cost to make, so whether a read is
        refused never depends on what was read before."""
        check_request(request)
        key = (version, comp.uid, port, request, ctx)
        hit = self._frames.get(key)
        asked = sum(self._asks)
        if hit is None:
            self._charge(asked + request.count, comp, port)
            self._asks.append(request.count)
            try:
                frame = comp.frames(port, request)
            finally:
                cost = self._asks.pop()
            if frame.count != request.count:
                raise ValueError(
                    f"{comp.uid}.{port}: a frame of {frame.count} samples answers a request for {request.count}"
                )
            hit = (frame, cost)
            if self._frames and next(iter(self._frames))[0] != version:
                self._frames.clear()
                self._cached_samples = 0
            if frame.count <= FRAME_CACHE_SAMPLES:
                self._frames[key] = hit
                self._cached_samples += frame.count
            while len(self._frames) > FRAME_CACHE_SIZE or self._cached_samples > FRAME_CACHE_SAMPLES:
                self._cached_samples -= self._frames.popitem(last=False)[1][0].count
        else:
            self._charge(asked + hit[1], comp, port)
            self._frames.move_to_end(key)
        if self._asks:
            self._asks[-1] += hit[1]
        return hit[0]

    def reserve(self, comp: Component, port: str, samples: int) -> None:
        """Refuse now a read that is about to ask ``comp``'s upstream for ``samples`` samples in
        several requests (an average of many acquisitions), when they would take it beyond
        ``MAX_REQUEST_FANOUT``: before the first of them is computed, not when the running count
        gets there (#986). Nothing is reserved — the requests are counted as they come."""
        self._charge(sum(self._asks) + samples, comp, port)

    @staticmethod
    def _charge(total: int, comp: Component, port: str) -> None:
        if total > MAX_REQUEST_FANOUT:
            raise beyond_model(
                f"{comp.uid}.{port}: this read asks for {total} samples along the chain "
                f"(at most {MAX_REQUEST_FANOUT})"
            )
