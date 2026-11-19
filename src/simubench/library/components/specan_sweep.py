"""The two evaluations of a sweep and the lines they draw (signal-chain.md 5.4, #954, #1524).

``Sweeper`` is the part of the swept analyzer's trace engine that makes one sweep: the lines the
display draws from the ones RF_IN carries (the front end's flatness, compression, distortion
products, image and spur responses), the analytic evaluation (every line drawn through the
resolution filter plus the receiver's noise) and the sampled one (the ADC's samples at each
tuning of the span). It reads the engine through :class:`~.specan_state.TraceHost`.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine import rfcascade
from ...engine.component import StreamStage
from ...engine.noise import gaussian, stream_key
from ...engine.signal import Signal
from ...engine.stream import MAX_REQUEST_FANOUT, FrameRequest, beyond_model
from .reading_math import axis
from .specan_backend import NBW_FACTOR
from .specan_frontend import SpecanMixer
from .specan_state import SweepPlan, TraceHost, TraceSettings

MAX_CELLS = 64
MAX_LINES_IN_WINDOW = 65_536
#: Most frequency points one sampled sweep may be put together from (signal-chain.md 5.4).
MAX_SWEEP_BINS = 1 << 22


class Sweeper:
    def __init__(self, host: TraceHost) -> None:
        self.host = host

    def front_end_lines(
        self, f: np.ndarray, p_mw: np.ndarray, st: TraceSettings
    ) -> tuple[np.ndarray, np.ndarray, str | None]:
        """The lines the display draws, from the ones RF_IN carries:
        the input lines scaled by the response's flatness and the chain's compression, the
        distortion products the chain's intercepts make of them, the image and spur responses,
        the residuals and the oscillator's feedthrough. Every derived line is display-referred —
        they are computed from the levels the mixer sees, so the flatness rides inside them —
        and the compression applies to every line the chain's own nonlinearity outputs: the
        input lines at the third-order model's multi-tone drive, the products at their parents'
        drive. Without any declared figure the input is returned unchanged."""
        input_block = self.host.input_block()
        mixer = self.mixer_block()
        flat = bool(input_block.param("flatness"))
        declared = flat or st.cascade is not None or mixer is not None
        if not declared:
            return f, p_mw, None
        approx: str | None = None
        # The response's flatness scales every input line; the derived responses are computed
        # from the scaled levels, so it rides inside them.
        p_drive = p_mw
        if flat:
            p_drive = p_mw * 10 ** (np.asarray(input_block.flatness_db(f)) / 10)
        out_f, out_p = [f], [p_drive]

        # The distortion products of the chain's intercepts: equivalent input lines (#954). Like
        # every line the chain outputs, a product rides the chain's compressed gain at the drive
        # of the lines it comes from.
        if st.cascade is not None and (
            math.isfinite(st.cascade.iip2_dbm) or math.isfinite(st.cascade.iip3_dbm)
        ):
            products = rfcascade.products(f, 10 * np.log10(np.maximum(p_drive, 1e-300)), st.cascade)
            if products.freq_hz.size:
                product_mw = 10 ** (products.power_dbm / 10)
                if math.isfinite(st.cascade.p1db_dbm):
                    over = 10 * np.log10(np.maximum(products.drive_mw, 1e-300)) - st.cascade.p1db_dbm
                    product_mw = product_mw * np.asarray(rfcascade.compress_ratio(over)) ** 2
                out_f.append(products.freq_hz)
                out_p.append(product_mw)
            if products.dropped:
                approx = "too many lines for one trace: the distortion products are approximate, only the strongest 64 lines take part"

        # Compression: the P1dB model's multi-tone drive — each line's gain from every line's
        # power, every other line counted twice (a third-order model's own contribution counts
        # once, the cross terms with every other line twice) — over the flatness-scaled drive.
        if st.cascade is not None and math.isfinite(st.cascade.p1db_dbm):
            total = float(p_drive.sum())
            over = 10 * np.log10(np.maximum(2.0 * total - p_drive, 1e-300)) - st.cascade.p1db_dbm
            out_p[0] = out_p[0] * np.asarray(rfcascade.compress_ratio(over)) ** 2

        if mixer is not None:
            if_hz = float(self.host.if_block().param("if_hz"))
            margin = 6 * st.rbw
            start, stop = st.start - margin, st.stop + margin
            # The image side: an input line below the oscillator folds to its mirror about the
            # oscillator, which is one intermediate frequency below the tuned point — a line at
            # f shows there, by the rejection ratio, when the receiver tunes to f + 2·if.
            rejection = mixer.param("image_rejection")
            if rejection is not None:
                image_f = f + 2 * if_hz
                lands = (image_f >= start) & (image_f <= stop)
                if lands.any():
                    out_f.append(image_f[lands])
                    out_p.append(p_drive[lands] * 10 ** (-float(rejection) / 10))
            # The declared spur responses: |m·f_rf − n·f_lo| = f_if has the two solutions
            # (m·f_rf + (n−1)·if)/n and (m·f_rf + (n+1)·if)/n — the difference product lands on
            # the IF from either side — so the receiver shows the line at both (#954).
            for row in mixer.param("spurs") or ():
                m_, n_ = float(row[0]), float(row[1])
                for f_spur in (m_ * f + (n_ - 1.0) * if_hz, m_ * f + (n_ + 1.0) * if_hz):
                    lands = (f_spur > 0) & (f_spur / n_ >= start) & (f_spur / n_ <= stop)
                    if lands.any():
                        out_f.append(f_spur[lands] / n_)
                        out_p.append(p_drive[lands] * 10 ** (float(row[2]) / 10))
            # The residuals and the oscillator's feedthrough: lines with no input at all.
            for row in mixer.param("residuals") or ():
                shown = float(self.host.lo_block().display(np.array([float(row[0])]))[0])
                if start <= shown <= stop:
                    out_f.append(np.array([float(row[0])]))
                    out_p.append(np.array([10 ** (float(row[1]) / 10)]))
            feedthrough = mixer.param("lo_feedthrough")
            if feedthrough is not None and start <= 0.0 <= stop:
                out_f.append(np.array([0.0]))
                out_p.append(np.array([10 ** (float(feedthrough) / 10)]))
        return np.concatenate(out_f), np.concatenate(out_p), approx

    def mixer_block(self) -> Any:
        """The specan_mixer on the wiring between the input block and the IF filter, if any."""
        for block in self.host.chain():
            if isinstance(block, SpecanMixer):
                return block
        return None

    def lines_in_window(self, sig: Signal, st: TraceSettings) -> tuple[np.ndarray, np.ndarray, str | None]:
        if self.host.lines.lines is not None and self.host.lines.sig is sig and self.host.lines.st is st:
            return self.host.lines.lines
        m = sig.merged()
        r = float(self.host.input_block().param("z_in"))
        pos = m.freq > 0
        f = m.freq[pos]
        p_mw = np.abs(m.amp[pos]) ** 2 / (2 * r) * 1e3
        dc = m.dc + float(np.real(m.amp[~pos]).sum())
        if dc:
            f = np.concatenate([[0.0], f])
            p_mw = np.concatenate([[dc * dc / r * 1e3], p_mw])
        f, p_mw, approx = self.front_end_lines(f, p_mw, st)
        # The analyzer's frequency axis runs off its reference: a true f displays at f / (1 + ε).
        f_disp = self.host.lo_block().display(f)
        margin = 6 * st.rbw
        sel = (f_disp >= st.start - margin) & (f_disp <= st.stop + margin)
        f_disp, p_mw = f_disp[sel], p_mw[sel]
        if f_disp.size > MAX_LINES_IN_WINDOW:
            keep = np.argsort(p_mw)[-MAX_LINES_IN_WINDOW:]
            f_disp, p_mw = f_disp[keep], p_mw[keep]
            reason = "too many lines for one trace: the weakest were dropped"
            approx = reason if approx is None else f"{approx}; {reason}"
        self.host.lines.sig, self.host.lines.st, self.host.lines.lines = sig, st, (f_disp, p_mw, approx)
        return f_disp, p_mw, approx

    def truncation(self, sig: Signal, st: TraceSettings) -> str | None:
        """Why the trace cannot be drawn completely, or None."""
        notes = [n for n in sig.notes if n.kind == "truncated"]
        if notes and sig.freq.size > 1:
            f = np.sort(sig.freq)
            f_next = self.host.lo_block().display(f[-1] + (f[-1] - f[-2]))
            if f_next <= st.stop + 3 * st.rbw:
                return f"too many lines for one trace: the weakest were dropped (lines above {f[-1]:.6g} Hz are not modeled)"
        if self.host.sampled():
            return None  # every line RF_IN carries is in the samples
        _, _, why = self.lines_in_window(sig, st)
        return why

    def sweep(self, s: int, st: TraceSettings) -> np.ndarray:
        """Trace of sweep ``s``. Finished sweeps used the signal at their start; the sweep in
        progress shows what RF_IN carries now, so a change reads at once."""
        current = s >= self.host.current_sweep(st)
        t_ns = (
            self.host.session.now_ns if current else self.host.history.restart_ns + round(s * st.sweep * 1e9)
        )
        version = self.host.history.index(t_ns)
        hit = self.host.history.sweeps.get((s, version))
        if hit is not None:
            return hit
        # Averaging and the hold modes take up to thousands of sweeps per read, and an analytic sweep
        # passes no frame: each one is a checkpoint of its own (D88, #1533).
        self.host.session.checkpoint(self.host, "sweep")
        if self.host.sampled():
            trace = self.sweep_sampled(s, st)
            if len(self.host.history.sweeps) > 4096:
                self.host.history.sweeps.clear()
            self.host.history.sweeps[(s, version)] = trace
            return trace
        sig = self.host.history.signals[version][1] if self.host.history.signals else Signal.zero()
        x = np.asarray(axis(st.start, st.stop, st.points, "LIN"))
        f, p, _ = self.lines_in_window(sig, st)
        line_mw = np.zeros(st.points)
        bucket = (st.stop - st.start) / max(st.points - 1, 1)
        if f.size:
            for i in range(0, f.size, 512):
                d = x[:, None] - f[None, i : i + 512]
                line_mw += (
                    p[None, i : i + 512] * self.host.rbw_block().response(d, st.rbw, bucket, st.detector)
                ).sum(axis=1)
        # The oscillator's phase noise skirts every converted line: at each point the line's
        # power times L(|f_point − f_line|) over the RBW's noise bandwidth, as noise (#954).
        # Within half the RBW of its line the sideband power is the line's own power — it does
        # not add again.
        skirt_mw = None
        if self.host.lo_block().param("phase_noise") and f.size:
            skirt_mw = np.zeros(st.points)
            for i in range(0, f.size, 512):
                d = np.abs(x[:, None] - f[None, i : i + 512])
                density = self.host.lo_block().phase_noise_density(d)
                density = np.where(d > st.rbw / 2, density, 0.0)
                skirt_mw += (p[None, i : i + 512] * density).sum(axis=1)
            skirt_mw *= NBW_FACTOR * st.rbw
        floor_mw = self.host.rbw_block().noise_floor_mw(st.floor_att, st.nf)
        cells = int(min(MAX_CELLS, max(1, round(bucket / st.rbw))))
        if st.detector == "SAMPle":
            cells = 1
        assert self.host.owner is not None
        key = stream_key(self.host.session.seed, self.host.owner.name, "specan", st.key())
        idx = (np.int64(s) * np.int64(st.points) + np.arange(st.points, dtype=np.int64))[:, None] * np.int64(
            MAX_CELLS
        ) + np.arange(cells, dtype=np.int64)[None, :]
        n = (gaussian(key, idx) + 1j * gaussian(key ^ 0x2545F491, idx)) * math.sqrt(floor_mw / 2)
        v = np.sqrt(line_mw)[:, None] + n
        if skirt_mw is not None:
            # The same per-resolution-cell draws, on their own stream: the thermal noise's values
            # do not move (#954).
            pkey = stream_key(self.host.session.seed, self.host.owner.name, "specan", "phase", st.key())
            v = (
                v
                + (gaussian(pkey, idx) + 1j * gaussian(pkey ^ 0x2545F491, idx))
                * np.sqrt(skirt_mw / 2)[:, None]
            )
        power = np.abs(v) ** 2
        trace = self.host.detector_block().detect(v, power, st.detector)
        if len(self.host.history.sweeps) > 4096:
            self.host.history.sweeps.clear()
        self.host.history.sweeps[(s, version)] = trace
        return trace

    def plan(self, start: float, stop: float, points: int) -> SweepPlan:
        """How the window is cut into tunings with the current settings."""
        assert self.host.owner is not None
        rbw = self.host.rbw_block()
        dt = rbw.interval()
        n = rbw.points(dt)
        df = 1.0 / (n * dt)
        # one tuning gives the usable half of the transform's rate, and no more than the flat
        # part of the analog IF
        per = max(2, 2 * int(min(0.5 / dt, self.host.if_block().flat_width()) / df / 2))
        bucket = (stop - start) / max(points - 1, 1)
        origin = start - bucket / 2 - df
        segments = max(1, math.ceil((stop + bucket / 2 + df - origin) / (per * df)))
        request = FrameRequest(0, n, dt)
        cost, port = n, "out"
        block: Any = rbw
        while isinstance(block, StreamStage):
            request = block.upstream_request(port, request)
            cost += request.count
            src = self.host.owner.wiring.source(block.name, "in")
            if src is None:
                break
            block, port = self.host.owner.components[src[0]], src[1]
        return SweepPlan(
            dt=dt,
            points=n,
            df=df,
            per=per,
            segments=segments,
            origin=origin,
            cost=cost,
            acquisition=request.count * request.dt_s,
        )

    def sweep_sampled(self, s: int, st: TraceSettings) -> np.ndarray:
        """Sweep ``s`` from the chain's samples: tune to each segment of the span in turn, take
        the resolution filter's transform there, keep its middle, lay the segments side by side
        and detect. Levels are referred to the input: the chain's gain and the IF passband's
        response are taken out, as the instrument's calibration does."""
        assert self.host.owner is not None
        plan = self.plan(st.start, st.stop, st.points)
        if plan.segments * plan.cost > MAX_REQUEST_FANOUT or plan.segments * plan.per > MAX_SWEEP_BINS:
            raise beyond_model(
                f"{self.host.uid}: this sweep takes {plan.segments} tunings of {plan.cost} samples "
                f"({plan.segments * plan.per} frequency points); at most {MAX_REQUEST_FANOUT} samples "
                f"and {MAX_SWEEP_BINS} points — widen the RBW or narrow the span"
            )
        rbw, lo, if_filter = self.host.rbw_block(), self.host.lo_block(), self.host.if_block()
        center = float(self.host.setting("freq.center"))
        with self.host.session.tuned(center):
            gain_db = rfcascade.cascade(block.stage(center) for block in self.host.chain()).gain_db
        half = plan.per // 2
        offsets = np.arange(-half, half) * plan.df
        # √mW at the input from volts at the ADC: the passband's response and the chain's gain out
        scale = math.sqrt(1e3 / (2 * float(self.host.input_block().param("z_in"))) * 10 ** (-gain_db / 10))
        calibration = scale / if_filter.passband(float(if_filter.param("if_hz")) + offsets)
        dt_real = plan.dt / (1.0 + lo.error())  # the ADC's clock runs off the reference
        t_sweep = self.host.history.restart_ns + round(s * st.sweep * 1e9)
        dwell = st.sweep / plan.segments
        key = (self.host.owner.name, "specan", "sampled", st.key(), s)
        lead = max(plan.points, 64)  # ahead of every filter's warm-up: the indexes stay positive
        middle = plan.points // 2
        bins = np.empty(plan.segments * plan.per, dtype=np.complex128)
        for i in range(plan.segments):
            request = FrameRequest(
                t0_ns=t_sweep + round(i * dwell * 1e9),
                count=plan.points,
                dt_s=dt_real,
                key=(*key, i),
                index0=lead,
                tune_hz=plan.origin + (i * plan.per + half) * plan.df,
            )
            frame = self.host.session.cached_frames(rbw, "out", request)
            bins[i * plan.per : (i + 1) * plan.per] = frame.data[middle - half : middle + half] * calibration
        x = np.asarray(axis(st.start, st.stop, st.points, "LIN"))
        bucket = (st.stop - st.start) / max(st.points - 1, 1)
        return self.host.detector_block().detect_bins(bins, plan.origin, plan.df, x, bucket, st.detector)
