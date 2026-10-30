"""Signal descriptions.

A net does not carry samples. It carries a :class:`Signal`: a sum of spectral lines plus a DC term,
each line an exact phasor. Linear operations (gain, transfer functions, network solving) act on the
lines exactly; readers evaluate the description as lines, as samples at chosen instants, or as DC
(architecture.md section 2).

Line convention: ``v(t) = dc + sum_k Re(a_k * exp(j*2*pi*f_k*(t - t0_k)))`` with ``a_k`` the complex
peak amplitude at the line's time origin ``t0_k`` (integer nanoseconds). Keeping a per-line time
origin keeps phase arithmetic precise when virtual time grows large.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import numpy as np

NS = 1e-9

#: Upper bound on the lines one signal may carry (architecture.md 2.3).
MAX_LINES = 4095


def phase_cycles(freq: np.ndarray, delta_ns: np.ndarray) -> np.ndarray:
    """Fractional cycles of ``freq * delta`` (delta in integer ns), precise for long run times.

    ``delta = q seconds + r ns``; ``frac(f * q) = frac(frac(f) * q)`` because ``int(f) * q`` is an
    integer number of cycles, so large virtual times do not erode phase precision.
    """
    d = np.asarray(delta_ns, dtype=np.int64)
    q, r = np.divmod(d, 1_000_000_000)
    f = np.asarray(freq, dtype=float)
    ff = f - np.floor(f)
    c = np.mod(ff * q.astype(float), 1.0) + np.mod(f * (r.astype(float) * NS), 1.0)
    return np.mod(c, 1.0)


@dataclass(frozen=True)
class FidelityNote:
    """Why a signal is not exact: truncation, a model limit, an approximation."""

    kind: str  # "truncated" | "limit" | "approximate"
    detail: str


@dataclass
class Signal:
    """A sum of phasor lines and a DC level."""

    freq: np.ndarray = field(default_factory=lambda: np.zeros(0))
    amp: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=complex))
    t0_ns: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    dc: float = 0.0
    notes: tuple[FidelityNote, ...] = ()

    # ---- construction -------------------------------------------------------------------------

    @staticmethod
    def zero() -> Signal:
        return Signal()

    @staticmethod
    def constant(value: float) -> Signal:
        return Signal(dc=float(value))

    @staticmethod
    def lines(
        freq: Iterable[float],
        amp: Iterable[complex],
        t0_ns: int = 0,
        dc: float = 0.0,
        notes: tuple[FidelityNote, ...] = (),
    ) -> Signal:
        f = np.asarray(list(freq), dtype=float)
        a = np.asarray(list(amp), dtype=complex)
        return Signal(f, a, np.full(f.shape, t0_ns, dtype=np.int64), float(dc), notes)

    # ---- algebra -----------------------------------------------------------------------------

    def __add__(self, other: Signal) -> Signal:
        return Signal(
            np.concatenate([self.freq, other.freq]),
            np.concatenate([self.amp, other.amp]),
            np.concatenate([self.t0_ns, other.t0_ns]),
            self.dc + other.dc,
            self.notes + tuple(n for n in other.notes if n not in self.notes),
        )

    def scale(self, k: complex | float) -> Signal:
        if isinstance(k, complex) and k.imag != 0:
            # A complex gain only makes sense for lines; DC takes the real part at f = 0.
            return Signal(self.freq, self.amp * k, self.t0_ns, self.dc * k.real, self.notes)
        kr = float(np.real(k))
        return Signal(self.freq, self.amp * kr, self.t0_ns, self.dc * kr, self.notes)

    def transfer(self, h: Callable[[np.ndarray], np.ndarray]) -> Signal:
        """Apply a linear time-invariant transfer function ``h(f)`` (complex, vectorized)."""
        hf = h(self.freq) if self.freq.size else np.zeros(0, dtype=complex)
        h0 = complex(h(np.zeros(1))[0])
        return Signal(self.freq, self.amp * hf, self.t0_ns, self.dc * h0.real, self.notes)

    def with_note(self, note: FidelityNote) -> Signal:
        if note in self.notes:
            return self
        return Signal(self.freq, self.amp, self.t0_ns, self.dc, (*self.notes, note))

    def compact(self, tol: float = 0.0) -> Signal:
        """Drop lines with zero (or below ``tol``) amplitude."""
        if not self.freq.size:
            return self
        keep = np.abs(self.amp) > tol
        return Signal(self.freq[keep], self.amp[keep], self.t0_ns[keep], self.dc, self.notes)

    # ---- evaluation --------------------------------------------------------------------------

    @property
    def is_exact(self) -> bool:
        return not self.notes

    def sample(self, t_ns: np.ndarray) -> np.ndarray:
        """Evaluate the signal at the instants ``t_ns`` (integer nanoseconds)."""
        t = np.asarray(t_ns, dtype=np.int64)
        out = np.full(t.shape, self.dc, dtype=float)
        if not self.freq.size:
            return out
        # Process in blocks so that very long records stay within memory.
        block = max(1, 4_000_000 // max(1, self.freq.size))
        for start in range(0, t.size, block):
            tt = t[start : start + block]
            cyc = phase_cycles(self.freq[None, :], tt[:, None] - self.t0_ns[None, :])
            out[start : start + block] += np.real(self.amp[None, :] * np.exp(2j * np.pi * cyc)).sum(axis=1)
        return out

    def sample_rel(self, base_ns: int, offsets_s: np.ndarray) -> np.ndarray:
        """Evaluate at ``base_ns + offsets_s`` with sub-nanosecond precision for fast sampling."""
        off = np.asarray(offsets_s, dtype=float)
        out = np.full(off.shape, self.dc, dtype=float)
        if not self.freq.size:
            return out
        base = phase_cycles(self.freq, int(base_ns) - self.t0_ns)  # per line, at the base instant
        a = self.amp * np.exp(2j * np.pi * base)
        uniform = _uniform_step(off)
        if uniform is not None and off.size * self.freq.size > 1_000_000:
            return out + _sum_uniform(a, self.freq, float(off[0]), uniform, off.size)
        block = max(1, 4_000_000 // max(1, self.freq.size))
        for start in range(0, off.size, block):
            o = off[start : start + block]
            out[start : start + block] += np.real(
                a[None, :] * np.exp(2j * np.pi * self.freq[None, :] * o[:, None])
            ).sum(axis=1)
        return out

    def rms(self) -> float:
        """True RMS over a long window: sqrt(dc^2 + sum |a|^2 / 2) for distinct non-zero lines."""
        merged = self.merged()
        ac = merged.amp[merged.freq > 0]
        dc = merged.dc + float(np.real(merged.amp[merged.freq == 0]).sum())
        return float(np.sqrt(dc**2 + np.sum(np.abs(ac) ** 2) / 2))

    def peak(self) -> float:
        """Largest |v(t)| over time.

        Lines on one harmonic grid (a periodic signal) are synthesized over a period by inverse
        FFT, so truncated squares show their real peak (with Gibbs overshoot) rather than Σ|a|.
        A few unrelated lines are sampled densely; many unrelated lines give the bound Σ|a|.
        """
        m = self.merged()
        pos = m.freq > 0
        dc = m.dc + float(np.real(m.amp[~pos]).sum())
        f, a = m.freq[pos], m.amp[pos]
        if not f.size:
            return abs(dc)
        f0 = float(f.min())
        k = f / f0
        if np.all(np.abs(k - np.round(k)) < 1e-9 * k) and k.max() < 1 << 20:
            kk = np.round(k).astype(np.int64)
            size = 1 << int(np.ceil(np.log2(8 * kk.max() + 1)))
            spec = np.zeros(size, dtype=complex)
            np.add.at(spec, kk, a / 2)
            np.add.at(spec, size - kk, np.conj(a) / 2)
            wave = np.fft.ifft(spec).real * size + dc
            return float(np.abs(wave).max())
        if f.size <= 64:
            t = np.linspace(0, 50 / f0, 200_001)
            wave = dc + np.real(a[None, :] * np.exp(2j * np.pi * f[None, :] * t[:, None])).sum(axis=1)
            return float(np.abs(wave).max())
        return abs(dc) + float(np.abs(a).sum())

    def mean(self) -> float:
        merged = self.merged()
        return merged.dc + float(np.real(merged.amp[merged.freq == 0]).sum())

    def merged(self) -> Signal:
        """Combine lines of identical frequency into one phasor referenced to t0 = 0."""
        if not self.freq.size:
            return self
        # Re-reference every line to t0 = 0 so that equal frequencies add as phasors.
        rot = np.exp(-2j * np.pi * phase_cycles(self.freq, self.t0_ns))
        a0 = self.amp * rot
        uniq, inv = np.unique(self.freq, return_inverse=True)
        acc = np.zeros(uniq.shape, dtype=complex)
        np.add.at(acc, inv, a0)
        return Signal(uniq, acc, np.zeros(uniq.shape, dtype=np.int64), self.dc, self.notes)

    def phasor_at(self, f: float, rel_tol: float = 1e-12) -> complex:
        """Complex peak amplitude of the component at frequency ``f`` referenced to t = 0."""
        m = self.merged()
        if not m.freq.size:
            return 0j
        idx = np.nonzero(np.abs(m.freq - f) <= rel_tol * max(abs(f), 1.0))[0]
        return complex(m.amp[idx].sum()) if idx.size else 0j


# ---- periodic waveform builders -----------------------------------------------------------------


def _uniform_step(off: np.ndarray) -> float | None:
    """The step of evenly spaced offsets (a scope record), or None."""
    if off.ndim != 1 or off.size < 1024:
        return None
    step = (off[-1] - off[0]) / (off.size - 1)
    # Offsets far from the base instant (a record after a trigger wait, G5) carry the rounding of
    # their magnitude in every difference: that is not an uneven step.
    tol = 1e-9 * step + 8 * np.finfo(float).eps * max(abs(float(off[0])), abs(float(off[-1])))
    if step <= 0 or np.max(np.abs(np.diff(off) - step)) > tol:
        return None
    return float(step)


def _sum_uniform(a: np.ndarray, freq: np.ndarray, o0: float, dt: float, n: int) -> np.ndarray:
    """``Re sum_l a_l exp(2 pi j f_l (o0 + k dt))`` for k < n, as one matrix product.

    With k = q * B + r the exponential splits into a block factor and an in-block factor, so the
    sum is ``Re((P * a) @ R.T)``: about 2 * sqrt(n) * lines exponentials instead of n * lines.
    """
    block = max(1, int(np.ceil(np.sqrt(n))))
    q = np.arange((n + block - 1) // block)
    r = np.arange(block)
    outer = np.exp(2j * np.pi * np.outer(o0 + q * block * dt, freq)) * a[None, :]
    inner = np.exp(2j * np.pi * np.outer(r * dt, freq))
    return np.real(outer @ inner.T).reshape(-1)[:n]


def sine(freq: float, peak: float, phase_rad: float = 0.0, t0_ns: int = 0) -> Signal:
    """``peak * sin(2*pi*f*(t - t0) + phase)`` as one line (cosine phasor convention)."""
    a = peak * np.exp(1j * (phase_rad - np.pi / 2))
    return Signal.lines([freq], [a], t0_ns=t0_ns)


def _harmonics(
    freq: float, f_max: float, step: int, what: str
) -> tuple[np.ndarray, tuple[FidelityNote, ...]]:
    """Harmonic numbers up to the generator's bandwidth ``f_max``.

    A waveform cut only by the generator's own bandwidth is exact (the generator outputs nothing
    above it). When the line limit cuts it lower, the model leaves content out, and says so.
    """
    n_band = int(f_max // freq) if freq > 0 else 1
    n = np.arange(1, max(n_band, 1) + 1, step)
    if n.size <= MAX_LINES:
        return n, ()
    n = n[:MAX_LINES]
    note = FidelityNote(
        "truncated", f"{what}: {n.size} harmonics up to {n[-1] * freq:.6g} Hz kept, the rest left out"
    )
    return n, (note,)


def square(freq: float, peak: float, f_max: float, phase_rad: float = 0.0, t0_ns: int = 0) -> Signal:
    """50 % duty square wave of amplitude ``peak`` (±peak), as odd harmonics up to ``f_max``."""
    n, notes = _harmonics(freq, f_max, 2, "square wave")
    a = 4 * peak / (np.pi * n) * np.exp(1j * (n * phase_rad - np.pi / 2))
    return Signal.lines(n * freq, a, t0_ns=t0_ns, notes=notes)


def ramp(freq: float, peak: float, f_max: float, phase_rad: float = 0.0, t0_ns: int = 0) -> Signal:
    """Rising sawtooth of amplitude ``peak``: rises from −peak to +peak, crossing zero at phase 0."""
    n, notes = _harmonics(freq, f_max, 1, "ramp")
    # x/pi on (-pi, pi) = (2/pi) * sum (-1)^(n+1) sin(n x) / n
    a = (2 * peak / np.pi) * ((-1.0) ** (n + 1)) / n * np.exp(1j * (n * phase_rad - np.pi / 2))
    return Signal.lines(n * freq, a, t0_ns=t0_ns, notes=notes)


def pulse_train(
    freq: float, peak: float, duty: float, f_max: float, phase_rad: float = 0.0, t0_ns: int = 0
) -> Signal:
    """Rectangular wave at ``duty`` (0..1): +peak for duty * T from the rising edge at phase 0, else
    -peak. Its mean is peak * (2 duty - 1); harmonics up to ``f_max``. Duty 0.5 is :func:`square`."""
    n, notes = _harmonics(freq, f_max, 1, "pulse train")
    x = 2 * np.pi * n * duty
    # f = P(2d-1) + sum 2P/(n pi) [sin(x) cos(n w t) + (1 - cos x) sin(n w t)]; a_n = c_n - j s_n.
    a = (2 * peak / (np.pi * n)) * (np.sin(x) - 1j * (1 - np.cos(x))) * np.exp(1j * n * phase_rad)
    keep = np.abs(a) > 1e-15 * peak
    return Signal.lines(n[keep] * freq, a[keep], t0_ns=t0_ns, notes=notes) + Signal.constant(
        peak * (2 * duty - 1)
    )


def triangle(
    freq: float, peak: float, symmetry: float, f_max: float, phase_rad: float = 0.0, t0_ns: int = 0
) -> Signal:
    """Ramp with ``symmetry`` (0..1, the rising share of the period) between -peak and +peak, crossing
    zero on the rise at phase 0. Symmetry 1 is :func:`ramp` (sawtooth); 0 a falling sawtooth."""
    if symmetry >= 1.0:
        return ramp(freq, peak, f_max, phase_rad, t0_ns)
    if symmetry <= 0.0:
        return ramp(freq, -peak, f_max, phase_rad + np.pi, t0_ns)
    n, notes = _harmonics(freq, f_max, 1, "ramp")
    th = 2 * np.pi * n * symmetry
    # From -P at t=0, rising to +P at s*T, falling back by T: a_n = -P (1 - e^-j th) / (pi^2 n^2 s (1-s)).
    a = -peak * (1 - np.exp(-1j * th)) / (np.pi**2 * n**2 * symmetry * (1 - symmetry))
    # Shift so the rising zero crossing (t = s*T/2) sits at phase 0.
    a = a * np.exp(1j * n * (np.pi * symmetry + phase_rad))
    keep = np.abs(a) > 1e-15 * peak
    return Signal.lines(n[keep] * freq, a[keep], t0_ns=t0_ns, notes=notes)


# ---- common transfer functions ------------------------------------------------------------------


def lowpass1(fc: float) -> Callable[[np.ndarray], np.ndarray]:
    def h(f: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + 1j * np.asarray(f) / fc)

    return h


def highpass1(fc: float) -> Callable[[np.ndarray], np.ndarray]:
    def h(f: np.ndarray) -> np.ndarray:
        x = 1j * np.asarray(f) / fc
        return x / (1.0 + x)

    return h
