"""The data-level blocks behind the front end of the swept spectrum analyzer (D50): the detector (positive peak, sample, average, RMS and the log conversion) and the video filter's bandwidth."""

from __future__ import annotations

from typing import Any

import numpy as np

from ...engine.component import (
    Component,
    OpResult,
    component,
    op,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .specan_backend import (
    STEPS,
    snap_filter,
)

#: Lowest level a sampled trace point reads, dBm (exact zero has no logarithm).
BINS_FLOOR_DBM = -300.0


@component("specan_detector")
class SpecanDetector(Component):
    """The detector and log conversion: positive peak / sample / average / RMS / negative
    peak / normal per resolution cell, then the level in dBm.

    Normal (Rosenfell) shows the peak of a CW-like bucket; a noise-like bucket (its cells
    spread by more than ``normal_spread``) shows the maximum on even points and the minimum on odd
    ones, as the X-Series reference describes. With ``auto`` the detector follows the trace mode:
    max hold peak, min hold negative peak, otherwise normal (the X-Series preset rule); a detector
    chosen through :meth:`op_select` turns ``auto`` off."""

    label = "检波"
    label_en = "Detector"
    about = "每个分辨单元的检波方式（峰值、采样、平均、RMS 等）与对数换算。"
    about_en = "The detector per resolution cell (peak, sample, average, RMS, …) and log conversion."

    defaults = {
        "detector": "POSitive",  # POSitive | SAMPle | AVERage | RMS | NEGative | NORMal (a setting)
        "auto": False,  # the detector follows the trace mode (a setting such as $detector_auto)
        "trace_mode": "",  # the trace mode the automatic choice reads (a setting such as $trace.type)
        "normal_spread": 3.0,  # dB: a bucket whose cells spread more than this is noise-like for Normal
    }
    units = {"normal_spread": "dB"}

    def in_use(self) -> str:
        """The detector the trace uses now (the automatic choice when ``auto`` is on)."""
        if V.flag(self.param("auto")):
            mode = str(self.param("trace_mode")).upper()
            if mode.startswith("MAXH"):
                return "POSitive"
            if mode.startswith("MINH"):
                return "NEGative"
            return "NORMal"
        return str(self.param("detector"))

    def detect(self, v: np.ndarray, power: np.ndarray, mode: str | None = None) -> np.ndarray:
        mode = mode or self.in_use()
        if mode == "POSitive":
            out = power.max(axis=1)
        elif mode == "NEGative":
            out = power.min(axis=1)
        elif mode == "NORMal":
            hi, lo = power.max(axis=1), power.min(axis=1)
            spread = float(self.param("normal_spread"))
            noise_like = 10 * np.log10(hi / np.maximum(lo, 1e-300)) > spread
            odd = (np.arange(power.shape[0]) % 2) == 1
            out = np.where(noise_like & odd, lo, hi)
        elif mode == "AVERage":
            out = np.abs(v).mean(axis=1) ** 2
        elif mode == "RMS":
            out = power.mean(axis=1)
        else:
            out = power[:, 0]
        return 10 * np.log10(out)

    def detect_bins(
        self,
        bins: np.ndarray,
        origin: float,
        df: float,
        x: np.ndarray,
        bucket: float,
        mode: str | None = None,
    ) -> np.ndarray:
        """The display points ``x`` from a row of frequency points (signal-chain.md 5.3): ``bins``
        holds the complex amplitude, in √mW, every ``df`` Hz from ``origin``. Each display point
        takes the frequency points its bucket holds — the nearest one first, then outward on both
        sides — and combines them as :meth:`detect` combines the resolution cells of a swept
        point; the sample detector takes the nearest alone."""
        mode = mode or self.in_use()
        # an odd number, so the bucket is covered evenly on both sides of the point
        cells = 1 if mode == "SAMPle" else 2 * round(bucket / (2 * df)) + 1
        j = np.arange(cells)
        outward = ((j + 1) // 2) * np.where(j % 2 == 1, 1, -1)  # 0, +1, -1, +2, -2, ...
        nearest = np.rint((x - origin) / df).astype(np.int64)
        v = bins[np.clip(nearest[:, None] + outward[None, :], 0, bins.size - 1)]
        with np.errstate(divide="ignore"):  # a converter with nothing to convert gives all-zero codes
            level = self.detect(v, np.abs(v) ** 2, mode)
        return np.maximum(level, BINS_FLOOR_DBM)

    @op("select")
    def op_select(self, query: bool = False, value: str | None = None, **_: Any) -> OpResult:
        """The detector of the trace: the query answers the one in use (also when automatic); a
        choice is manual, so it turns the automatic selection off."""
        if query:
            return OpResult(V.format_enum(self.in_use()))
        assert value is not None and self.owner is not None
        path = self.bound_path("detector")
        auto = self.bound_path("auto")
        if path is None:
            raise ScpiError(-221, "the detector is not a setting of this instrument")
        self.owner.set_state(path, value)
        if auto is not None:
            self.owner.set_state(auto, False)
        return OpResult()


@component("specan_vbw")
class SpecanVbw(Component):
    """The video filter's bandwidth: auto follows the RBW; the manual value stretches the
    sweep the same way the old monolith did (its auto sweep time uses min(RBW, VBW)). Steps and
    limits as the resolution filter's; a ``vbw_max`` above the ladder is the wide-open setting."""

    label = "视频滤波"
    label_en = "Video filter"
    about = "视频带宽：自动跟随 RBW，手动时影响扫描时间。"
    about_en = "The video bandwidth: auto follows the RBW; a manual value stretches the sweep."

    refs = {"rbw": ("specan_rbw", "instrument")}
    defaults = {
        "vid": 3.0e6,  # Hz, manual VBW (a setting such as $bw.vid)
        "vid_auto": True,  # the VBW follows the RBW (a setting)
        "rbw": "",  # the specan_rbw it reads the RBW from
        "steps": "1-3-10",  # the filter steps: 1-3-10 | 10% (as the specan_rbw steps)
        "vbw_min": 1.0,  # Hz, the narrowest VBW
        "vbw_max": 3.0e6,  # Hz, the widest VBW (above 8 MHz: one more, wide-open setting, X-Series 50 MHz)
    }
    units = {"vbw_min": "Hz", "vbw_max": "Hz"}
    choices = {"steps": STEPS}

    def vbw(self) -> float:
        if bool(self.param("vid_auto")):
            return float(self.ref("rbw").rbw())  # type: ignore[union-attr]
        return float(self.param("vid"))

    @op("coupled_vbw")
    def op_coupled_vbw(self, query: bool = False, value: float | None = None, **_: Any) -> OpResult:
        if query:
            return OpResult(self.reply_number(self.vbw(), 10))
        assert value is not None
        lo, hi = float(self.param("vbw_min")), float(self.param("vbw_max"))
        if not lo <= value <= hi:
            raise ScpiError(-222)
        assert self.owner is not None
        self.owner.set_state("bw.vid", snap_filter(value, self.choice("steps") or "1-3-10", lo, hi))
        self.owner.set_state("bw.vid_auto", False)
        return OpResult()
