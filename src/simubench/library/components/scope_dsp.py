"""A scope's digital bandwidth filter: a linear filter on the converter's samples (#1262, D67).

Where a scope's bandwidth limit is DSP after the ADC (the Infiniium UXR's ``:ACQuire:BANDwidth``
and its InfiniiSim channel filters), the filter belongs in the sample domain: the converter's noise
is added at the ADC over the analog bandwidth, and a narrower filter after it lowers the noise as
the hardware's does (D46: white noise falls with √bandwidth). A scope whose limit is an analog
filter writes it on the ``scope_vertical`` instead.
"""

from __future__ import annotations

import math
import re
from typing import Any, cast

from ...engine.component import PortSpec, component
from ...engine.stream import FrameRequest, SampleFrame
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope import _SAMPLES_IN, _SAMPLES_OUT, ScopeAdc, _channel_of, _ChannelBound, _displayed
from .scope_record import FilterView

#: The filter shapes, the ``scope_vertical`` ``response`` words that are DSP filters.
SHAPES = ("brickwall", "gaussian", "bessel4", "butterworth4")


@component("scope_dsp_filter")
class ScopeDspFilter(_ChannelBound):
    """A digital low-pass on each channel's samples, between the ADC and the decimator.

    ``in<n>`` takes channel n's samples (the converter's, or an earlier filter's), ``out<n>`` gives
    them filtered: a zero-phase ``response`` (``brickwall``: flat magnitude, linear phase with the
    delay removed — ringing on a step; ``gaussian``; ``bessel4``; ``butterworth4``) with -3 dB at
    ``bandwidth`` (-6 dB for the brick wall's edge), while ``enabled``. Off, or at a ``bandwidth`` of
    ``passthrough_hz`` or more (the analog bandwidth already limits the signal), the samples pass
    unchanged, bit for bit. Each parameter may name a channel's own setting (``$ch{n}.isim_type``).

    The filter is a FIR kernel (the shape's impulse response, a Blackman window, unit gain at
    0 Hz) reaching 32 periods of the cutoff on either side, at most 32768 converter samples: a frame
    asks its upstream for that much more on either side and keeps the middle (no history
    is kept). A record slower than the converter (``adc``'s top rate) is filtered at up
    to ``max_oversample`` times its own rate and then decimated — the hardware filters at the
    converter's rate, so the noise above the record's Nyquist frequency is filtered too.
    Limits: the window widens the brick wall's edge to about a fifth of the cutoff (stop band
    below -70 dB); a cutoff so far below the rate that the reach is cut at 32768 samples gets a
    wider edge in proportion; below the converter's rate by more than ``max_oversample`` the
    noise between the record's Nyquist frequency and the analog bandwidth is not filtered.
    """

    label = "数字带宽滤波"
    label_en = "Digital bandwidth filter"
    about = "ADC 之后的数字低通：砖墙、高斯、Bessel 或 Butterworth 形，关时直通。"
    about_en = (
        "A digital low-pass after the ADC: brick wall, Gaussian, Bessel or Butterworth; off: a pass-through."
    )

    port_pattern = r"(in|out)\d+"  # in1..inN and out1..outN are created by the instrument wiring
    refs = {"adc": ("scope_adc", "instrument")}
    optional_refs = frozenset({"adc"})
    choices = {"response": SHAPES}
    defaults = {
        "enabled": True,  # whether the filter applies (a channel's limit switch: $ch{n}.isim_bwlimit)
        "bandwidth": math.inf,  # the cutoff, Hz (per channel: $ch{n}.isim_bandwidth)
        "response": "brickwall",  # the filter's shape: brickwall | gaussian | bessel4 | butterworth4
        "passthrough_hz": math.inf,  # a cutoff at or above this passes the samples unchanged, Hz
        "adc": "",  # the scope_adc whose top rate a slower record is filtered at
        "max_oversample": 16,  # converter samples per record sample the filter works on at most
    }
    units = {"bandwidth": "Hz", "passthrough_hz": "Hz"}

    @classmethod
    def spec_of(cls, port: str) -> PortSpec | None:
        if cls.port_pattern and re.fullmatch(cls.port_pattern, port):
            return _SAMPLES_IN if port.startswith("in") else _SAMPLES_OUT
        return None

    def _shape(self, n: int) -> str:
        word = str(self.channel_param("response", n) or "brickwall")
        return V.parse_enum(word.encode(), SHAPES)

    def view(self, n: int, dt: float) -> FilterView:
        """Channel n's filter now, for output samples ``dt`` apart, as values (a deep record keeps it)."""
        if not V.flag(self.channel_param("enabled", n)):
            return FilterView()
        fc = float(self.channel_param("bandwidth", n))
        if not math.isfinite(fc) or fc >= float(self.param("passthrough_hz")) * (1 - 1e-12):
            return FilterView()
        shape, m = self._shape(n), self._oversample(dt)
        if shape == "brickwall" and fc * dt / m >= 0.5:
            return FilterView()  # at or beyond the Nyquist frequency the brick wall keeps everything
        return FilterView(shape, fc, m)

    def _oversample(self, dt: float) -> int:
        name = str(self.param("adc") or "")
        if not name:
            return 1
        adc = self.peer(name, required=True)
        if not isinstance(adc, ScopeAdc):
            return 1
        top = float(
            cast(Any, adc).top_rate(set(_displayed(self)))
        )  # ty sees the @component class as Component
        if not math.isfinite(top) or top * dt <= 1.0:
            return 1
        return max(1, min(int(self.param("max_oversample")), round(top * dt)))

    def frames(self, port: str, request: FrameRequest) -> SampleFrame:
        n = _channel_of(port)
        return self.view(n, request.dt_s).frame(request, lambda r: self.input_frames(f"in{n}", r))

    def sample_interval(self, port: str) -> float:
        """The interval the block reading ``port`` asks for: the filter keeps its input's rate."""
        assert self.owner is not None
        for src, dst in self.owner.wiring.wires():
            if src == f"{self.name}.{port}":
                reader, rport = dst.split(".", 1)
                return float(self.owner.components[reader].input_interval(rport))
        raise ScpiError(-221, f"{self.uid}.{port} is not read by any block")

    def input_interval(self, port: str) -> float:
        return self.sample_interval(f"out{_channel_of(port)}")
