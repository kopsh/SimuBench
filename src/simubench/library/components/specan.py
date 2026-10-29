"""Swept spectrum analyzer: lines through a Gaussian RBW filter, receiver noise, detectors, trace
modes in virtual time, markers and a 10 MHz reference input (validation-specan.md).

Lines are exact: every spectral line on RF_IN is drawn through the RBW filter at its power V²/2R.
Noise is the receiver's kT0·F·NBW plus the attenuation, drawn per resolution cell from a
counter-based generator keyed by (seed, sweep, point, cell), so a trace is a pure function of the
settings, the signals RF_IN carried, the seed and the sweep number: two reads at one virtual
instant are identical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.special import erf

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.noise import gaussian, stream_key
from ...engine.signal import Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError

F_MAX = 7.5e9
RBW_LADDER = [m * 10.0**k for k in range(0, 7) for m in (1.0, 3.0)]  # 1 Hz ... 3 MHz
RBW_LADDER = [r for r in RBW_LADDER if r <= 3e6]
NBW_FACTOR = math.sqrt(math.pi / (4 * math.log(2)))  # noise bandwidth of the Gaussian filter: 1.0645 RBW
KT0_DBM_HZ = -174.0
MAX_CELLS = 64
MAX_LINES_IN_WINDOW = 65_536


def line_response(d: np.ndarray, rbw: float, bucket: float, detector: str) -> np.ndarray:
    """Power a trace point shows of a unit line ``d`` Hz from the point, per detector.

    The Gaussian RBW filter swept across the point's bucket: the sample detector sees the filter
    at the point; the positive-peak detector the highest response within the bucket; average and
    rms detectors the mean over the bucket. When the bucket is narrower than the RBW all agree.
    """
    k = 4 * math.log(2) / rbw**2
    if detector == "SAMPle" or bucket <= rbw / 50:
        return np.exp(-k * d * d)
    if detector == "POSitive":
        e = np.maximum(np.abs(d) - bucket / 2, 0.0)
        return np.exp(-k * e * e)
    rk = math.sqrt(k)
    return (math.sqrt(math.pi) / (2 * rk * bucket)) * (
        erf(rk * (d + bucket / 2)) - erf(rk * (d - bucket / 2))
    )


def snap_rbw(value: float) -> float:
    return min(RBW_LADDER, key=lambda r: abs(math.log10(r) - math.log10(max(value, 1e-12))))


@dataclass
class _Settings:
    start: float
    stop: float
    points: int
    rbw: float
    sweep: float
    att: float
    detector: str
    nf: float

    def key(self) -> tuple[Any, ...]:
        return (self.start, self.stop, self.points, self.rbw, self.sweep, self.att, self.detector, self.nf)


@component("spectrum_analyzer")
class SpectrumAnalyzer(Component):
    """The analyzer core. Settings live in instrument state; this component applies the couplings."""

    ports = {"in": PortSpec("in", "signal", "RF_IN"), "ref": PortSpec("in", "signal", "REF_IN")}
    defaults = {
        "z_in": 50.0,  # input impedance, ohm
        "noise_figure": 24.0,  # receiver noise figure over kT0
        "mixer_limit": -10.0,  # dBm at the mixer: total input minus attenuation above it overloads
        "maxhold_limit": 999,  # sweeps max hold keeps (older ones are dropped, with a warning)
        "rosc": "INTernal",  # INTernal | EXTernal 10 MHz reference
    }
    units = {"noise_figure": "dB"}
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._restart_ns = 0
        self._key: tuple[Any, ...] | None = None
        self._seen = -1
        self._history: list[tuple[int, Signal]] = []  # RF_IN since the restart: (from, signal)
        self._cache: dict[tuple[int, int], np.ndarray] = {}
        self._held: np.ndarray | None = None
        self._held_upto = -1
        self._frozen: np.ndarray | None = None
        self._warned_hold = False
        self._ref_error = 0.0  # fractional frequency error of the reference in use
        self._ref_locked = False

    # ---- state helpers --------------------------------------------------------------------------

    def _st(self, path: str) -> Any:
        assert self.owner is not None
        return self.owner.get_state(path)

    def _set(self, path: str, value: Any) -> None:
        assert self.owner is not None
        self.owner.set_state(path, value)

    def rbw(self) -> float:
        if self._st("bw.res_auto"):
            return snap_rbw(float(self._st("freq.span")) / 100)
        return float(self._st("bw.res"))

    def vbw(self) -> float:
        return self.rbw() if self._st("bw.vid_auto") else float(self._st("bw.vid"))

    def auto_sweep(self) -> float:
        rbw = self.rbw()
        return max(1e-3, 2.5 * float(self._st("freq.span")) / (rbw * min(rbw, self.vbw())))

    def sweep_time(self) -> float:
        return self.auto_sweep() if self._st("sweep.time_auto") else float(self._st("sweep.time"))

    def attenuation(self) -> float:
        if self._st("power.att_auto"):
            # Keep the reference level at the mixer limit, in 2 dB steps.
            need = float(self._st("display.ref_level")) - float(self.param("mixer_limit"))
            return float(min(70.0, max(0.0, 2 * math.ceil(need / 2 - 1e-9))))
        return float(self._st("power.att"))

    def window(self) -> tuple[float, float]:
        c, s = float(self._st("freq.center")), float(self._st("freq.span"))
        return c - s / 2, c + s / 2

    def settings(self) -> _Settings:
        start, stop = self.window()
        return _Settings(
            start,
            stop,
            int(self._st("sweep.points")),
            self.rbw(),
            self.sweep_time(),
            self.attenuation(),
            str(self._st("detector")),
            float(self.param("noise_figure")),
        )

    # ---- restarts and signal history ------------------------------------------------------------

    def _trace_key(self) -> tuple[Any, ...]:
        return (*self.settings().key(), self._st("trace.type"), int(self._st("aver.count")), self._ref_error)

    def restart(self) -> None:
        now = self.session.now_ns
        self._restart_ns = now
        current = self._history[-1][1] if self._history else self.input("in")
        self._history = [(now, current)]
        self._cache.clear()
        self._held = None
        self._held_upto = -1
        self._warned_hold = False

    def reset(self) -> None:
        self._frozen = None
        self._seen = -1

    def sync(self) -> None:
        if self._seen == self.session._signal_version:
            return
        self._seen = self.session._signal_version
        self._update_reference()
        key = self._trace_key()
        if key != self._key:
            self._key = key
            self._history = [(self.session.now_ns, self.input("in"))]
            self.restart()
            return
        sig = self.input("in")
        last = self._history[-1][1] if self._history else None
        if last is None or not _same(last, sig):
            self._history.append((self.session.now_ns, sig))

    def _update_reference(self) -> None:
        if self._st("rosc.source") != "EXTernal":
            self._ref_error, self._ref_locked = 0.0, False
            return
        sig = self.input("ref").merged()
        ok = False
        if sig.freq.size:
            k = int(np.argmax(np.abs(sig.amp)))
            f = float(sig.freq[k])
            if abs(f - 10e6) <= 10e6 * 10e-6 and abs(sig.amp[k]) > 0.05:
                self._ref_error, ok = f / 10e6 - 1, True
        if not ok:
            self._ref_error = 0.0
        self._ref_locked = ok

    def ref_in_use(self) -> str:
        return "EXT" if self._ref_locked else "INT"

    def on_state(self, path: str, old: Any, new: Any) -> None:
        if path == self.bound_paths().get("rosc") and new == "EXTernal":
            self.session._sync()
            self._update_reference()
            if not self._ref_locked:
                assert self.owner is not None
                self.owner.push_error(
                    -221,
                    "external reference unusable: REF_IN needs a 10 MHz sine within 10 ppm, "
                    "the analyzer stays on its internal reference",
                )

    # ---- traces ---------------------------------------------------------------------------------

    def _history_index(self, t_ns: int) -> int:
        idx = 0
        for i, (t, _) in enumerate(self._history):
            if t <= t_ns:
                idx = i
            else:
                break
        return idx

    def _signal_at(self, t_ns: int) -> Signal:
        sig = self._history[0][1] if self._history else Signal.zero()
        for t, s in self._history:
            if t <= t_ns:
                sig = s
            else:
                break
        return sig

    def _lines_in_window(self, sig: Signal, st: _Settings) -> tuple[np.ndarray, np.ndarray, bool]:
        m = sig.merged()
        r = float(self.param("z_in"))
        pos = m.freq > 0
        f = m.freq[pos]
        p_mw = np.abs(m.amp[pos]) ** 2 / (2 * r) * 1e3
        dc = m.dc + float(np.real(m.amp[~pos]).sum())
        if dc:
            f = np.concatenate([[0.0], f])
            p_mw = np.concatenate([[dc * dc / r * 1e3], p_mw])
        # The analyzer's frequency axis runs off its reference: a true f displays at f / (1 + ε).
        f_disp = f / (1 + self._ref_error)
        margin = 6 * st.rbw
        sel = (f_disp >= st.start - margin) & (f_disp <= st.stop + margin)
        f_disp, p_mw = f_disp[sel], p_mw[sel]
        dropped = False
        if f_disp.size > MAX_LINES_IN_WINDOW:
            keep = np.argsort(p_mw)[-MAX_LINES_IN_WINDOW:]
            f_disp, p_mw, dropped = f_disp[keep], p_mw[keep], True
        return f_disp, p_mw, dropped

    def truncation(self, sig: Signal, st: _Settings) -> str | None:
        """Why the trace cannot be drawn completely, or None."""
        notes = [n for n in sig.notes if n.kind == "truncated"]
        if notes and sig.freq.size > 1:
            f = np.sort(sig.freq)
            f_next = (f[-1] + (f[-1] - f[-2])) / (1 + self._ref_error)
            if f_next <= st.stop + 3 * st.rbw:
                return f"too many lines for one trace: the weakest were dropped (lines above {f[-1]:.6g} Hz are not modeled)"
        _, _, dropped = self._lines_in_window(sig, st)
        if dropped:
            return "too many lines for one trace: the weakest were dropped"
        return None

    def _sweep(self, s: int, st: _Settings) -> np.ndarray:
        """Trace of sweep ``s``. Finished sweeps used the signal at their start; the sweep in
        progress shows what RF_IN carries now, so a change reads at once."""
        current = s >= self.current_sweep(st)
        t_ns = self.session.now_ns if current else self._restart_ns + round(s * st.sweep * 1e9)
        version = self._history_index(t_ns)
        hit = self._cache.get((s, version))
        if hit is not None:
            return hit
        sig = self._history[version][1] if self._history else Signal.zero()
        x = np.linspace(st.start, st.stop, st.points)
        f, p, _ = self._lines_in_window(sig, st)
        line_mw = np.zeros(st.points)
        bucket = (st.stop - st.start) / max(st.points - 1, 1)
        if f.size:
            for i in range(0, f.size, 512):
                d = x[:, None] - f[None, i : i + 512]
                line_mw += (p[None, i : i + 512] * line_response(d, st.rbw, bucket, st.detector)).sum(axis=1)
        floor_mw = 10 ** ((KT0_DBM_HZ + st.nf + st.att + 10 * math.log10(NBW_FACTOR * st.rbw)) / 10)
        cells = int(min(MAX_CELLS, max(1, round(bucket / st.rbw))))
        if st.detector == "SAMPle":
            cells = 1
        assert self.owner is not None
        key = stream_key(self.session.seed, self.owner.name, "specan", st.key())
        idx = (np.int64(s) * np.int64(st.points) + np.arange(st.points, dtype=np.int64))[:, None] * np.int64(
            MAX_CELLS
        ) + np.arange(cells, dtype=np.int64)[None, :]
        n = (gaussian(key, idx) + 1j * gaussian(key ^ 0x2545F491, idx)) * math.sqrt(floor_mw / 2)
        v = np.sqrt(line_mw)[:, None] + n
        power = np.abs(v) ** 2
        if st.detector == "POSitive":
            out = power.max(axis=1)
        elif st.detector == "AVERage":
            out = np.abs(v).mean(axis=1) ** 2
        elif st.detector == "RMS":
            out = power.mean(axis=1)
        else:
            out = power[:, 0]
        trace = 10 * np.log10(out)
        if len(self._cache) > 4096:
            self._cache.clear()
        self._cache[(s, version)] = trace
        return trace

    def current_sweep(self, st: _Settings) -> int:
        return max(0, int((self.session.now_ns - self._restart_ns) * 1e-9 / st.sweep + 1e-9))

    def trace(self) -> tuple[np.ndarray, np.ndarray]:
        """Frequencies and levels (dBm) of the trace as the display shows it now."""
        self.session._sync()
        st = self.settings()
        x = np.linspace(st.start, st.stop, st.points)
        if not self._st("init.continuous") and self._frozen is not None:
            return x, self._frozen
        return x, self._trace_at(self.current_sweep(st), st)

    def _trace_at(self, s_now: int, st: _Settings) -> np.ndarray:
        mode = self._st("trace.type")
        if mode == "AVERage":
            count = int(self._st("aver.count"))
            sweeps = range(max(0, s_now - count + 1), s_now + 1)
            return np.mean([self._sweep(s, st) for s in sweeps], axis=0)
        if mode == "MAXHold":
            limit = int(self.param("maxhold_limit"))
            first = max(0, s_now - limit + 1)
            if first > 0 and not self._warned_hold:
                self._warned_hold = True
                assert self.owner is not None
                self.owner.push_error(-221, f"max hold keeps the newest {limit} sweeps")
            if self._held is None or self._held_upto < first - 1 or self._held_upto > s_now:
                self._held, self._held_upto = None, first - 1
            for s in range(self._held_upto + 1, s_now + 1):
                tr = self._sweep(s, st)
                self._held = tr.copy() if self._held is None else np.maximum(self._held, tr)
                self._held_upto = s
            assert self._held is not None
            return self._held
        return self._sweep(s_now, st)

    # ---- overload -------------------------------------------------------------------------------

    def overload(self) -> bool:
        sig = self.input("in").merged()
        total_mw = float(np.sum(np.abs(sig.amp) ** 2)) / (2 * float(self.param("z_in"))) * 1e3
        total_mw += sig.dc**2 / float(self.param("z_in")) * 1e3
        if total_mw <= 0:
            return False
        return 10 * math.log10(total_mw) - self.attenuation() > float(self.param("mixer_limit"))

    # ---- readings (UI) --------------------------------------------------------------------------

    def readings(self) -> dict[str, Any]:
        x, y = self.trace()
        st = self.settings()
        mx = float(self._st("marker.x"))
        i = int(np.argmin(np.abs(x - mx)))
        return {
            "values": {
                "start": st.start,
                "stop": st.stop,
                "rbw": st.rbw,
                "vbw": self.vbw(),
                "sweep_time": st.sweep,
                "uncal": bool(st.sweep < self.auto_sweep() * (1 - 1e-9)),
                "attenuation": st.att,
                "overload": self.overload(),
                "ref_level": float(self._st("display.ref_level")),
                "marker_x": float(x[i]),
                "marker_y": float(y[i]),
                "reference": self.ref_in_use(),
                "sweep": self.current_sweep(st),
            },
            "traces": [
                {
                    "name": "trace1",
                    "t0_s": st.start,
                    "dt_s": (st.stop - st.start) / max(st.points - 1, 1),
                    "values": y.tolist(),
                }
            ],
        }

    # ---- operations: settings with couplings ----------------------------------------------------

    def _window_set(self, center: float, span: float) -> None:
        if not 10.0 <= span <= F_MAX or not 0.0 <= center <= F_MAX:
            raise ScpiError(-222)
        center = min(max(center, span / 2), F_MAX - span / 2)  # the window slides, no error
        self._set("freq.span", span)
        self._set("freq.center", center)

    @op("frequency")
    def op_frequency(
        self, query: bool = False, what: str = "center", value: float | None = None, **_: Any
    ) -> OpResult:
        c, s = float(self._st("freq.center")), float(self._st("freq.span"))
        start, stop = c - s / 2, c + s / 2
        if query:
            return OpResult(V.format_float({"center": c, "span": s, "start": start, "stop": stop}[what], 15))
        assert value is not None
        if what == "center":
            self._window_set(value, s)
        elif what == "span":
            self._window_set(c, value)
        elif what == "start":
            self._window_set((value + stop) / 2, stop - value)
        else:
            self._window_set((start + value) / 2, value - start)
        return OpResult()

    @op("coupled")
    def op_coupled(
        self, query: bool = False, what: str = "rbw", value: float | None = None, **_: Any
    ) -> OpResult:
        """RBW, VBW, sweep time and attenuation: a manual value turns that coupling off."""
        effective = {"rbw": self.rbw, "vbw": self.vbw, "sweep": self.sweep_time, "att": self.attenuation}[
            what
        ]
        if query:
            return OpResult(V.format_float(effective(), 10))
        assert value is not None
        if what in ("rbw", "vbw"):
            if not 1.0 <= value <= 3e6:
                raise ScpiError(-222)
            path = "bw.res" if what == "rbw" else "bw.vid"
            self._set(path, snap_rbw(value))
            self._set(path + "_auto", False)
        elif what == "sweep":
            if not 1e-3 <= value <= 10e3:
                raise ScpiError(-222)
            self._set("sweep.time", value)
            self._set("sweep.time_auto", False)
        else:
            if not 0 <= value <= 70:
                raise ScpiError(-222)
            self._set("power.att", 2 * round(value / 2))
            self._set("power.att_auto", False)
        return OpResult()

    @op("status")
    def op_status(self, what: str = "overload", **_: Any) -> OpResult:
        if what == "overload":
            return OpResult(V.format_bool(self.overload()))
        if what == "uncal":
            return OpResult(V.format_bool(self.sweep_time() < self.auto_sweep() * (1 - 1e-9)))
        if what == "locked":
            self.session._sync()
            return OpResult(V.format_bool(self._ref_locked))
        return OpResult(self.ref_in_use().encode())

    @op("trace_data")
    def op_trace_data(self, name: str | None = None, **_: Any) -> OpResult:
        if name and name.upper() not in ("TRACE1", "TRAC1", "1"):
            raise ScpiError(-224, f"unknown trace {name!r}")
        _, y = self.trace()
        why = self.truncation(self._signal_at(self.session.now_ns), self.settings())
        if why:
            assert self.owner is not None
            self.owner.push_error(-221, why)
        return OpResult(b",".join(V.format_float(v, 7) for v in y))

    @op("trace_clear")
    def op_trace_clear(self, **_: Any) -> OpResult:
        self.restart()
        self._frozen = None
        return OpResult()

    @op("initiate")
    def op_initiate(self, **_: Any) -> OpResult:
        """Single sweep(s): the averaging count in AVERage mode, else one; takes their sweep time."""
        st = self.settings()
        n = int(self._st("aver.count")) if self._st("trace.type") == "AVERage" else 1
        self.session._sync()
        start_sweep = self.current_sweep(st)
        traces = [self._sweep(s, st) for s in range(start_sweep, start_sweep + n)]
        mode = self._st("trace.type")
        if mode == "AVERage":
            self._frozen = np.mean(traces, axis=0)
        elif mode == "MAXHold":
            self._frozen = np.max(traces, axis=0)
        else:
            self._frozen = traces[-1]
        return OpResult(duration_ns=round(n * st.sweep * 1e9))

    # ---- markers --------------------------------------------------------------------------------

    def _peaks(self, y: np.ndarray) -> list[int]:
        thr = float(self._st("marker.threshold"))
        exc = float(self._st("marker.excursion"))
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

    @op("marker")
    def op_marker(
        self, query: bool = False, what: str = "x", value: float | None = None, **_: Any
    ) -> OpResult:
        x, y = self.trace()
        mx = float(self._st("marker.x"))
        i = int(np.argmin(np.abs(x - mx)))
        if what == "x":
            if query:
                return OpResult(V.format_float(float(x[i]), 15))
            assert value is not None
            j = int(np.argmin(np.abs(x - value)))
            self._set("marker.x", float(x[j]))
            return OpResult()
        if what == "y":
            return OpResult(V.format_float(float(y[i]), 7))
        if what == "count":
            return OpResult(V.format_float(self._count(float(x[i])), 12))
        peaks = self._peaks(y)
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
        best = cands[0]
        self._set("marker.x", float(x[best]))
        return OpResult()

    def _count(self, x: float) -> float:
        """An ideal counter: the strongest line near the marker, on the analyzer's frequency scale."""
        st = self.settings()
        f, p, _ = self._lines_in_window(self._signal_at(self.session.now_ns), st)
        bucket = (st.stop - st.start) / max(st.points - 1, 1)
        near = np.abs(f - x) <= max(st.rbw, bucket)
        if not near.any():
            return x
        k = int(np.argmax(np.where(near, p, -1)))
        return float(f[k])


def _same(a: Signal, b: Signal) -> bool:
    return (
        a.dc == b.dc
        and a.freq.shape == b.freq.shape
        and bool(np.array_equal(a.freq, b.freq))
        and bool(np.array_equal(a.amp, b.amp))
        and bool(np.array_equal(a.t0_ns, b.t0_ns))
    )
