"""Signal sources."""

from __future__ import annotations

import math
from typing import Any

from ...engine.component import Component, PortSpec, component
from ...engine.signal import Signal, ramp, sine, square
from ...scpi.errors import ScpiError


@component("function_generator_core")
class FunctionGeneratorCore(Component):
    """Standard waveforms with phase-continuous frequency changes.

    Parameters: ``function`` (SINusoid, SQUare, RAMP, DC), ``frequency`` (Hz), ``amplitude`` (Vpp),
    ``offset`` (V), ``phase`` (degrees), ``bandwidth`` (Hz, harmonic limit for square and ramp),
    ``square_max`` (Hz, highest square/ramp frequency).
    """

    ports = {"out": PortSpec("out", "signal")}
    defaults = {
        "function": "SINusoid",
        "frequency": 1000.0,
        "amplitude": 0.1,
        "offset": 0.0,
        "phase": 0.0,
        "bandwidth": 100e6,
        "square_max": math.inf,
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._t0 = 0
        self._phase0 = 0.0

    def reset(self) -> None:
        self._t0 = self.session.now_ns
        self._phase0 = 0.0

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = self.bound_paths()
        if path == bound.get("frequency") and old is not None:
            now = self.session.now_ns
            self._phase0 = (self._phase0 + 2 * math.pi * float(old) * (now - self._t0) * 1e-9) % (2 * math.pi)
            self._t0 = now
        fn = self.param("function")
        freq = float(self.param("frequency"))
        if fn in ("SQUare", "RAMP") and freq > float(self.param("square_max")):
            raise ScpiError(-221, f"{fn} is limited to {self.param('square_max'):g} Hz")

    def output(self, port: str) -> Signal:
        fn = self.param("function")
        offset = float(self.param("offset"))
        if fn == "DC":
            return Signal.constant(offset)
        freq = float(self.param("frequency"))
        peak = float(self.param("amplitude")) / 2.0
        phase = self._phase0 + math.radians(float(self.param("phase")))
        if fn == "SINusoid":
            sig = sine(freq, peak, phase, self._t0)
        elif fn == "SQUare":
            sig = square(freq, peak, float(self.param("bandwidth")), phase, self._t0)
        elif fn == "RAMP":
            sig = ramp(freq, peak, float(self.param("bandwidth")), phase, self._t0)
        else:
            raise ScpiError(-224, f"function {fn!r} not supported")
        return sig + Signal.constant(offset)
