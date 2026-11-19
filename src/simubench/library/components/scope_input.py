"""Oscilloscope front-end blocks a real model may split out of the vertical channel (#1579): the
input step attenuator ahead of the preamplifier, and the track-and-hold sampler between the
analog front end and the converter's lanes.

Both are in the signal path because the hardware is (Keysight UXR-B technical overview 3123-1371
p.11: electro-mechanical attenuators, an InP preamplifier, a 256 GSa/s InP sampler that feeds four
64 GSa/s SiGe samplers and sixteen 10-bit ADC lanes). A scope that does not split them keeps the
vertical and the converter as they were."""

from __future__ import annotations

import math
from typing import Any

from ...engine.component import Component, PortSpec, component
from ...engine.signal import Signal


@component("scope_attenuator")
class ScopeAttenuator(Component):
    """The channel's input step attenuator: one loss per range of input V/div (``steps``, rows of
    [up to V/div, loss dB], the first row whose V/div is not below the setting). The preamplifier
    after it (a ``scope_vertical`` naming it as its ``attenuator``) restores the gain, so the
    record is unchanged; what the attenuator decides is the level the preamplifier sees, which an
    internal probe on its output shows. The input V/div is the displayed ``scale`` over the
    ``probe_ratio`` the display includes, as the vertical takes it."""

    label = "输入衰减器"
    label_en = "Input attenuator"
    about = "通道输入端的步进衰减器：按输入 V/div 选一档衰减，前置放大器把增益补回。"
    about_en = (
        "The channel's input step attenuator: a loss per input V/div range, made up by the preamplifier."
    )

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    defaults = {
        "steps": [],  # [[up to input V/div, loss dB], ...] in rising V/div; [] or a scale above the last row: 0 dB
        "scale": "$ch.scale",  # V/div as displayed
        "probe_ratio": 1.0,  # the probe setting the displayed scale includes
    }

    def input_scale(self) -> float:
        """V/div at the input connector."""
        return float(self.param("scale")) / float(self.param("probe_ratio"))

    def loss_db(self) -> float:
        """The loss of the step the current input V/div selects, dB."""
        scale = self.input_scale()
        for upto, loss in sorted((float(r[0]), float(r[1])) for r in self.param("steps") or ()):
            if scale <= upto * (1 + 1e-9):
                return loss
        return 0.0

    def gain(self) -> float:
        """Output volts per input volt (1 at 0 dB)."""
        loss = self.loss_db()
        return 1.0 if loss == 0.0 else 10 ** (-loss / 20)

    def output(self, port: str) -> Signal:
        sig = self.input("in")
        gain = self.gain()
        return sig if gain == 1.0 else sig.scale(gain)


@component("scope_sampler")
class ScopeSampler(Component):
    """The channel's track-and-hold sampler: a primary sampler at the converter's top rate that
    hands its samples, in turn, to ``paths`` slower samplers, each feeding a group of the
    converter's lanes. In track mode its output is its input (the analog path is unchanged); its
    errors act at the sample instants, where the converter (a ``scope_adc`` naming it as its
    ``sampler``) applies them: each path's gain error, offset (fraction of the full scale) and
    timing skew (s) repeat every ``paths`` samples of the top-rate grid, and
    ``aperture_jitter_s`` is this channel's own jitter, drawn per sample and per channel (the
    clock all channels share is the timebase's ``clock_jitter_s``)."""

    label = "采样器"
    label_en = "Sampler"
    about = "通道的采样保持：主采样器按最高采样率取样，再轮流分给几路较慢的采样器；各路的增益、偏置、时延失配与本通道的孔径抖动在采样时刻起作用。"
    about_en = (
        "The channel's track-and-hold: a primary sampler at the top rate hands its samples in turn to slower "
        "path samplers; their gain, offset and timing mismatch and the channel's own aperture jitter act at the "
        "sample instants."
    )

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    #: The trigger comparator splits off ahead of it (the trigger's ``select_taps``).
    track_and_hold = True
    defaults = {
        "rate": 0.0,  # the primary sampler's rate, Sa/s (the converter's top rate)
        "paths": 1,  # the slower samplers it hands its samples to in turn (1: none)
        "path_gain_errors": None,  # each path's gain error, relative, one value per path (None: none)
        "path_offsets_fs": None,  # each path's offset, fraction of the full scale (None: none)
        "path_skews_s": None,  # each path's sampling-time skew, s (None: none)
        "aperture_jitter_s": 0.0,  # this channel's own aperture jitter, s rms, independent of the others
    }
    units = {"rate": "Sa/s", "aperture_jitter_s": "s"}

    @classmethod
    def literal_problems(cls, params: dict[str, Any]) -> list[tuple[str, str]]:
        """A per-path list has one value for each of the ``paths`` samplers."""
        count = params.get("paths", cls.defaults["paths"])
        if not isinstance(count, int):
            return []
        out = []
        for name in ("path_gain_errors", "path_offsets_fs", "path_skews_s"):
            values = params.get(name)
            if isinstance(values, list) and len(values) != count:
                out.append((name, f"{name} 有 {len(values)} 个值，paths 是 {count}：每路采样器一个值"))
        return out

    def output(self, port: str) -> Signal:
        return self.input("in")

    def per_path(self, name: str) -> tuple[float, ...]:
        """A per-path list, one value per path (empty when unset)."""
        values = self.param(name)
        if not values:
            return ()
        n = int(self.param("paths"))
        if len(values) != n:
            raise ValueError(f"{self.uid}: {name} has {len(values)} values, paths is {n}")
        return tuple(float(v) for v in values)

    def tiled(self, name: str, lanes: int) -> tuple[float, ...]:
        """A per-path list laid over ``lanes`` converter lanes: lane i belongs to path i mod
        ``paths`` (the primary sampler deals its samples out in turn). Empty when unset."""
        values = self.per_path(name)
        if not values:
            return ()
        if lanes % len(values):
            raise ValueError(f"{self.uid}: {lanes} converter lanes are not a multiple of {len(values)} paths")
        return tuple(values[i % len(values)] for i in range(lanes))

    def jitter(self) -> float:
        """This channel's own aperture jitter, s rms."""
        return float(self.param("aperture_jitter_s"))


def combined(lanes: tuple[float, ...], paths: tuple[float, ...]) -> tuple[float, ...]:
    """Each lane's error plus its path's (both already one value per lane; either may be empty)."""
    if not paths:
        return lanes
    if not lanes:
        return paths
    return tuple(a + b for a, b in zip(lanes, paths, strict=True))


def quadrature(*sigmas: float) -> float:
    """Independent rms terms combined."""
    return math.sqrt(sum(s * s for s in sigmas))
