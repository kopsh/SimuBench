"""Sample streams: what the digital blocks inside an instrument pass to each other (D67).

The analog side of an instrument stays analytic (:class:`~simubench.engine.signal.Signal`: DC and
spectral lines). After an ADC the blocks pass *frames* of samples, pulled on demand: a downstream
block asks its upstream for "``count`` samples from ``t0_ns``, ``dt_s`` apart" (:class:`FrameRequest`)
and gets a :class:`SampleFrame` back. There is no background flow and no state between frames:

* a frame is a pure function of the request, the block's parameters, the upstream signal's current
  version and the bench seed — two reads at the same instant give the same frame, whoever asked
  first (signal-chain.md 2.2);
* a block that needs history (a filter, a window) asks its upstream for ``warmup`` extra samples
  and drops the matching outputs (:meth:`FrameRequest.upstream`), instead of remembering a tail;
* noise in a frame is drawn from the counter-based streams of ``engine/noise.py``, keyed by the
  request (:attr:`FrameRequest.key`, :attr:`FrameRequest.index0`): the block that starts a request
  decides the key, so a script's acquisition, the screen and an internal probe never share draws.

The limits below are model limits (architecture.md 2.3): a request beyond one is refused with
:class:`ModelLimit`, never answered with a shortened frame.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from ..scpi.errors import ScpiError
from .signal import FidelityNote

#: Most samples in one frame.
MAX_FRAME = 1 << 26
#: Most samples one read may ask for along a whole chain (every block's request, added up): a
#: decimate-by-1000 block in front of a 1 M-point frame asks its upstream for 1 G samples.
MAX_REQUEST_FANOUT = 1 << 28
#: Frames a session keeps (newest first to stay): enough for one read's repeated requests and the
#: internal probe, not a store.
FRAME_CACHE_SIZE = 64
#: Samples those frames hold together at most; a frame longer than this is not kept at all.
FRAME_CACHE_SAMPLES = 1 << 21

#: What the numbers in a frame are: real volts, ADC codes (integer values, kept as floats), complex
#: baseband samples, or the complex amplitude at each frequency point of a transform.
SampleKind = Literal["volts", "codes", "iq", "bins"]


class ModelLimit(ScpiError):
    """A request beyond what the model computes (architecture.md 2.3). Raised instead of a
    truncated answer; a component that maps it to its instrument's own code catches and re-raises.
    Build one with :func:`beyond_model`."""


def beyond_model(detail: str, code: int = -221) -> ModelLimit:
    return ModelLimit(code, f"beyond the model: {detail}")


@dataclass(frozen=True)
class SampleFormat:
    """How to read a frame's numbers."""

    kind: SampleKind = "volts"
    #: codes: the converter's resolution; 0 means not quantized.
    bits: int = 0
    #: codes: the input voltage one code step stands for, V.
    lsb: float = 0.0
    signed: bool = True


VOLTS = SampleFormat("volts")
IQ = SampleFormat("iq")
BINS = SampleFormat("bins")


@dataclass(frozen=True)
class FrameRequest:
    """What a block asks its upstream for. Hashable: it is part of the frame cache's key."""

    #: Virtual time the sample grid is anchored at: sample k is at ``t0_ns + (first + k) * dt_s``.
    t0_ns: int
    #: Number of samples.
    count: int
    #: Sample interval the requester assumes (nominal), s.
    dt_s: float
    #: The random stream's key, chosen by the block that starts the request (its uid, the stream's
    #: name, the acquisition number, the screen frame ...).
    key: tuple[object, ...] = ()
    #: Index of sample 0 in that random stream (the k-th acquisition of an average, a position on
    #: an absolute sample grid). Overlapping requests on one grid then draw the same noise.
    index0: int = 0
    #: A spectrum analyzer's local-oscillator tuning for this request, Hz; None elsewhere.
    tune_hz: float | None = None
    #: Index of sample 0 on the grid anchored at ``t0_ns`` (may be negative). Whole nanoseconds
    #: cannot place a record around a trigger found between two samples of a fast clock; an
    #: integer position on a float-interval grid can.
    first: int = 0

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError(f"a frame request needs at least one sample, not {self.count}")
        if not self.dt_s > 0.0:
            raise ValueError(f"a frame request needs a positive sample interval, not {self.dt_s!r}")

    def upstream(self, factor: int = 1, warmup: int = 0) -> FrameRequest:
        """The request a rate-changing block passes up: ``factor`` input samples per output sample,
        and ``warmup`` extra input samples before the first (the block's history, dropped from its
        output). Sample ``warmup`` of the answer falls on this request's sample 0; the random
        stream's index follows the finer grid, so the same instant draws the same noise."""
        if factor < 1 or warmup < 0:
            raise ValueError(f"upstream(factor={factor}, warmup={warmup}): factor >= 1, warmup >= 0")
        return replace(
            self,
            count=self.count * factor + warmup,
            dt_s=self.dt_s / factor,
            index0=self.index0 * factor - warmup,
            first=self.first * factor - warmup,
        )

    def offsets_s(self) -> np.ndarray:
        """Each sample's time after ``t0_ns``, s."""
        return (self.first + np.arange(self.count, dtype=np.float64)) * self.dt_s

    def indexes(self) -> np.ndarray:
        """Each sample's index in the random stream (``index0 + k``)."""
        return self.index0 + np.arange(self.count, dtype=np.int64)


@dataclass(frozen=True, eq=False)
class SampleFrame:
    """One frame of samples. ``data`` is read-only: frames are cached and shared."""

    #: float64 (volts, codes) or complex128 (iq, bins).
    data: np.ndarray
    t0_ns: int
    dt_s: float
    fmt: SampleFormat = VOLTS
    #: iq, bins: the radio or intermediate frequency this frame's 0 Hz (or middle point) stands for.
    center_hz: float = 0.0
    #: bins: spacing of the frequency points, Hz.
    df_hz: float = 0.0
    #: Whether a sample in this frame touched full scale.
    clipped: bool = False
    notes: tuple[FidelityNote, ...] = ()

    def __post_init__(self) -> None:
        want = np.complex128 if self.fmt.kind in ("iq", "bins") else np.float64
        data = np.array(self.data, dtype=want)  # a private copy: the caller's array stays writable
        if data.ndim != 1:
            raise ValueError(f"a frame is one row of samples, not shape {data.shape}")
        data.flags.writeable = False
        object.__setattr__(self, "data", data)

    @property
    def count(self) -> int:
        return int(self.data.size)

    @property
    def rate_hz(self) -> float:
        return 1.0 / self.dt_s


def check_request(request: FrameRequest) -> None:
    """Refuse a frame longer than the model computes."""
    if request.count > MAX_FRAME:
        raise beyond_model(f"a frame of {request.count} samples (at most {MAX_FRAME})")


def decimate(samples: np.ndarray, factor: int, warmup: int = 0) -> np.ndarray:
    """Keep every ``factor``-th sample after dropping ``warmup``: the plain rate change, without a
    filter. Blocks with a filter apply it first (their own pure function) and then call this."""
    return samples[warmup::factor]
