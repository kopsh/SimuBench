"""The marker and its peak search (#274, #616, #1524).

``Markers`` is the part of the swept analyzer's trace engine that places a marker on the trace.
The marker group is on settings the definition may declare: marker.enabled (on/off; absent:
always on), marker.mode (POSition | DELTa), marker.ref_x / marker.ref_y (the fixed reference a
delta marker reads against, set when DELTa is chosen), marker.count (the counter switch; absent:
always counting) and marker.search (PARameter | MAXimum: whether peak search applies the threshold
and excursion). X-Series markers (#616): marker.mode may offer OFF (the marker is off, as with
marker.enabled false); ``marker_off_reply`` "nan" answers a read of an off marker with 9.91E+37
instead of -221; ``delta_x`` "offset" makes X the offset from the reference marker in delta mode.
It reads the engine through :class:`~.specan_state.TraceHost`.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np

from ...engine.component import OpResult
from ...scpi.errors import ScpiError
from .specan_state import TraceHost


class Markers:
    def __init__(self, host: TraceHost) -> None:
        self.host = host

    def peaks(self, y: np.ndarray) -> list[int]:
        thr = float(self.host.setting("marker.threshold"))
        exc = float(self.host.setting("marker.excursion"))
        out = []
        n = y.size
        for i in range(n):
            if y[i] <= thr:
                continue
            if (i > 0 and y[i - 1] > y[i]) or (i < n - 1 and y[i + 1] > y[i]):
                continue
            if i > 0 and y[i - 1] == y[i]:
                continue  # one index per flat top
            left = i
            lo_l = y[i]
            while left > 0 and y[left - 1] <= y[i]:
                left -= 1
                lo_l = min(lo_l, y[left])
            right = i
            lo_r = y[i]
            while right < n - 1 and y[right + 1] <= y[i]:
                right += 1
                lo_r = min(lo_r, y[right])
            if y[i] - lo_l >= exc and y[i] - lo_r >= exc:
                out.append(i)
        return out

    def mode(self) -> str:
        return str(self.host.option("marker.mode", "POSition")).upper()

    def marker_on(self) -> bool:
        return bool(self.host.option("marker.enabled", True)) and not self.mode().startswith("OFF")

    def turn_on(self) -> None:
        assert self.host.owner is not None  # a component's settings change through its instrument
        if "marker.enabled" in self.host.owner.specs and not self.host.setting("marker.enabled"):
            self.host.set_setting("marker.enabled", True)
        if self.mode().startswith("OFF"):
            self.host.set_setting("marker.mode", "POSition")

    def delta(self) -> bool:
        return self.marker_on() and self.mode().startswith("DELT")

    def search(self, what: str, x: np.ndarray, y: np.ndarray, i: int) -> int:
        """Peak search: max, next (by level), left / right (the nearest peak that way)."""
        if what == "max" and str(self.host.option("marker.search", "PARameter")).upper().startswith("MAX"):
            return int(np.argmax(y))  # MAXimum mode: the highest point, criteria ignored
        peaks = self.peaks(y)
        if what in ("left", "right"):
            side = [p for p in peaks if (p < i if what == "left" else p > i)]
            if not side:
                raise ScpiError(-200, "no peak on that side")
            return max(side) if what == "left" else min(side)
        # Peaks in display order: level, then frequency. MAXimum takes the first; NEXT the one after
        # the marker's, so equal-level twins (two sidebands) are both visited.
        order = sorted(peaks, key=lambda p: (-y[p], p))
        if what == "max":
            cands = order
        elif i in order:
            cands = order[order.index(i) + 1 :]
        else:
            cands = [p for p in order if (-y[p], p) > (-y[i], i)]
        if not cands:
            raise ScpiError(-200, "")
        return cands[0]

    def count(self, x: float) -> float:
        """An ideal counter: the strongest line near the marker, on the analyzer's frequency scale."""
        st = self.host.settings()
        f, p, _ = self.host.sweeper.lines_in_window(self.host.history.signal_at(self.host.session.now_ns), st)
        bucket = (st.stop - st.start) / max(st.points - 1, 1)
        near = np.abs(f - x) <= max(st.rbw, bucket)
        if not near.any():
            return x
        k = int(np.argmax(np.where(near, p, -1)))
        return float(f[k])

    def tune_marks(self) -> list[tuple[Literal["marker", "reference"], float]]:
        """The marker while it is on, and the fixed reference a delta marker reads against."""
        if not self.marker_on():
            return []
        marks: list[tuple[Literal["marker", "reference"], float]] = [
            ("marker", float(self.host.setting("marker.x")))
        ]
        if self.delta():
            marks.append(("reference", float(self.host.setting("marker.ref_x"))))
        return marks

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        if (
            path == "marker.mode"
            and str(old).upper().startswith("OFF")
            and not str(new).upper().startswith("OFF")
        ):
            # A marker turned on through its mode starts at the center of the screen.
            self.host.set_setting("marker.x", float(self.host.setting("freq.center")))
        if path == "marker.mode" and str(new).upper().startswith("DELT") and new != old:
            # DELTa fixes a reference marker where the marker is now; the marker itself moves on.
            # A marker that is off turns on first — it starts at the screen center, so the
            # reference is fixed at the same point and the delta pair starts at 0/0, not at the
            # stale position the marker had while it was off (issue #349).
            self.turn_on()
            x, y = self.host.trace()
            i = int(np.argmin(np.abs(x - float(self.host.setting("marker.x")))))
            self.host.set_setting("marker.ref_x", float(x[i]))
            self.host.set_setting("marker.ref_y", float(y[i]))
        if path == "marker.enabled" and new and not old:
            # A marker turned on starts at the center of the screen, as the analyzers do.
            self.host.set_setting("marker.x", float(self.host.setting("freq.center")))

    def op_marker(
        self, query: bool = False, what: str = "x", value: float | None = None, **_: Any
    ) -> OpResult:
        x, y = self.host.trace()
        mx = float(self.host.setting("marker.x"))
        i = int(np.argmin(np.abs(x - mx)))
        reads = query if what == "x" else what in ("y", "count", "dx")
        if reads and not self.marker_on():
            if self.host.choice("marker_off_reply") == "nan":
                return OpResult(self.host.reply_number(9.91e37, 7))  # SCPI "not a number"
            raise ScpiError(-221, "the marker is off")
        offset = self.delta() and self.host.choice("delta_x") == "offset"
        if what == "x":
            if query:
                ref = float(self.host.setting("marker.ref_x")) if offset else 0.0
                return OpResult(self.host.reply_number(float(x[i]) - ref, 15))
            assert value is not None
            if offset:
                value += float(self.host.setting("marker.ref_x"))  # a delta marker is placed by its offset
            j = int(np.argmin(np.abs(x - value)))
            self.turn_on()
            self.host.set_setting("marker.x", float(x[j]))
            return OpResult()
        if what == "y":
            level = float(y[i])
            if self.delta():
                level -= float(self.host.setting("marker.ref_y"))  # dB from the fixed reference
            return OpResult(self.host.reply_number(level, 7))
        if what == "dx":
            ref = float(self.host.setting("marker.ref_x")) if self.delta() else float(x[i])
            return OpResult(self.host.reply_number(float(x[i]) - ref, 15))
        if what == "count":
            if not bool(self.host.option("marker.count", True)):
                return OpResult(self.host.reply_number(0.0, 12))  # the counter is off
            return OpResult(self.host.reply_number(self.count(float(x[i])), 12))
        best = int(np.argmin(y)) if what == "min" else self.search(what, x, y, i)
        self.turn_on()
        self.host.set_setting("marker.x", float(x[best]))
        return OpResult()

    def op_marker_off(self, **_: Any) -> OpResult:
        """:CALCulate:MARKer:AOFF — every marker off (and back to normal mode)."""
        assert self.host.owner is not None  # an SCPI op runs on a live instrument
        if "marker.enabled" in self.host.owner.specs:
            self.host.set_setting("marker.enabled", False)
        if "marker.mode" in self.host.owner.specs:
            options = self.host.owner.specs["marker.mode"].options or []
            self.host.set_setting("marker.mode", "OFF" if "OFF" in options else "POSition")
        return OpResult()
