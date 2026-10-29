"""Lock-in amplifier components: reference oscillator and dual-phase demodulator.

The demodulator is exact. Every spectral line on the input, multiplied by the reference, becomes a
complex exponential; the cascade of n identical first-order low-pass stages has a closed-form
response to a sum of exponentials (a particular solution plus a homogeneous part
``e^(-t/T) * polynomial``). So settling after any change follows the order-n exponential in virtual
time without sampling anything (validation-lockin.md 1). Each change of signal or setting starts a
new regime from the filter state at that instant.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass
from functools import cache
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.noise import gaussian, stream_key
from ...engine.signal import NS, Signal, phase_cycles
from ...scpi import values as V
from ...scpi.errors import ScpiError

TWO_PI = 2 * math.pi
F_MAX = 500e3  # highest detection frequency, n * f


@cache
def settling_multiple(poles: int, remaining: float = 0.01) -> float:
    """x = t/T at which an n-pole step response is within ``remaining`` of its final value."""

    def unsettled(x: float) -> float:
        return math.exp(-x) * sum(x**k / math.factorial(k) for k in range(poles))

    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if unsettled(mid) > remaining else (lo, mid)
    return (lo + hi) / 2


def enbw(poles: int, tc: float) -> float:
    """Equivalent noise bandwidth of n identical RC stages: C(2n-2, n-1) / (4^n T)."""
    return math.comb(2 * poles - 2, poles - 1) / (4**poles * tc)


# ---- reference oscillator -----------------------------------------------------------------------


@component("lockin_reference")
class LockinReference(Component):
    """Internal oscillator or tracking of the fundamental on REF_IN; harmonic n; SINE_OUT drive.

    Outputs: ``ref``, a unit line at the detection frequency n·f whose phase is n·ψ + φ (the
    demodulator's reference), and ``sine``, the SINE_OUT drive at f (``amplitude`` in V rms).
    An external reference needs at least ``min_vpp`` peak to peak; when it disappears the
    oscillator keeps running at the last frequency and error 327 is queued once.
    """

    ports = {
        "ext": PortSpec("in", "signal", "REF_IN signal"),
        "ref": PortSpec("out", "reference"),
        "sine": PortSpec("out", "signal"),
    }
    defaults = {
        "source": "INTernal",  # INTernal | EXTernal: track the fundamental on REF_IN
        "frequency": 1000.0,  # Hz of the internal oscillator
        "phase": 0.0,  # degrees, phase of the detection reference
        "harmonic": 1,  # detection at harmonic n x f
        "amplitude": 1.0,  # V rms of the SINE_OUT drive
        "min_vpp": 0.4,  # V peak to peak an external reference needs to lock
    }
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._anchor_ns = 0  # ψ(t) = psi + 2π f (t − anchor)
        self._psi = 0.0
        self._freq = float(self.param("frequency"))
        self.locked = self.param("source") != "EXTernal"
        self._seen = -1

    # The phase of the oscillator at an instant, carried across frequency changes.
    def _psi_at(self, t_ns: int) -> float:
        return self._psi + TWO_PI * float(
            phase_cycles(np.array([self._freq]), np.array([t_ns - self._anchor_ns]))[0]
        )

    def _retune(self, freq: float) -> None:
        now = self.session.now_ns
        self._psi = self._psi_at(now) % TWO_PI
        self._anchor_ns = now
        self._freq = freq

    def reset(self) -> None:
        self._anchor_ns = self.session.now_ns
        self._psi = 0.0
        self._freq = float(self.param("frequency"))

    def on_state(self, path: str, old: Any, new: Any) -> None:
        bound = self.bound_paths()
        n = int(self.param("harmonic"))
        if path == bound.get("harmonic") and n * self._freq > F_MAX * (1 + 1e-12):
            raise ScpiError(-221, f"harmonic {n} x {self._freq:g} Hz exceeds {F_MAX:g} Hz")
        if path == bound.get("frequency"):
            f = float(new)
            if n * f > F_MAX * (1 + 1e-12):
                raise ScpiError(-221, f"harmonic {n} x {f:g} Hz exceeds {F_MAX:g} Hz")
            if self.param("source") != "EXTernal":
                self._retune(f)
        if path == bound.get("source"):
            self._seen = -1  # look at REF_IN (or go back to the internal frequency) now
            if new != "EXTernal":
                self.locked = True
                self._retune(float(self.param("frequency")))

    def sync(self) -> None:
        if self.param("source") != "EXTernal" or self._seen == self.session._signal_version:
            return
        self._seen = self.session._signal_version
        sig = self.input("ext").merged()
        line = None
        if sig.freq.size:
            k = int(np.argmax(np.abs(sig.amp) * (sig.freq > 0)))
            if sig.freq[k] > 0 and 2 * abs(sig.amp[k]) >= float(self.param("min_vpp")):
                line = (float(sig.freq[k]), complex(sig.amp[k]))
        if line is not None and 1e-3 <= line[0] <= F_MAX:
            freq, a = line
            # Lock to the fundamental: ψ(t) = arg(a) + 2π f t (merged lines are referenced to t = 0).
            psi = cmath.phase(a) % TWO_PI
            now = self.session.now_ns
            target = psi + TWO_PI * float(phase_cycles(np.array([freq]), np.array([now]))[0])
            drift = (self._psi_at(now) - target + math.pi) % TWO_PI - math.pi
            changed = not self.locked or freq != self._freq or abs(drift) > 1e-9
            self.locked = True
            if changed:
                self._freq, self._psi, self._anchor_ns = freq, psi, 0
                self.session.invalidate()
        elif self.locked:
            self.locked = False  # free-runs on the last frequency and phase
            assert self.owner is not None
            self.owner.push_error(327, "Reference unlock")

    def output(self, port: str) -> Signal:
        if port == "ref":
            n = int(self.param("harmonic"))
            # A reference phase φ shifts the reference, so θ = φ_signal − φ (θ falls as φ rises).
            phase = n * self._psi + math.radians(float(self.param("phase")))
            return Signal.lines([n * self._freq], [cmath.exp(1j * phase)], t0_ns=self._anchor_ns)
        amp = float(self.param("amplitude")) * math.sqrt(2)
        return Signal.lines([self._freq], [amp * cmath.exp(1j * self._psi)], t0_ns=self._anchor_ns)

    def readings(self) -> dict[str, Any]:
        n = int(self.param("harmonic"))
        return {
            "values": {
                "locked": self.locked,
                "frequency": self._freq,
                "detect_frequency": n * self._freq,
            }
        }

    @op("locked")
    def op_locked(self, **_: Any) -> OpResult:
        return OpResult(V.format_bool(self.locked))

    @op("frequency")
    def op_frequency(self, what: str = "actual", **_: Any) -> OpResult:
        f = self._freq * (int(self.param("harmonic")) if what == "detect" else 1)
        return OpResult(V.format_float(f, 10))


# ---- demodulator --------------------------------------------------------------------------------


@dataclass
class _Regime:
    """Filter input and state since ``t_ns``: input = Σ c_k exp(iΔ_k τ), state = particular + homogeneous."""

    t_ns: int
    tc: float
    poles: int
    c: np.ndarray  # complex coefficients
    delta: np.ndarray  # rad/s
    w: np.ndarray  # (poles, K): 1 / (1 + iΔT)^j
    e: np.ndarray  # homogeneous part at τ = 0, per stage
    source: tuple[Any, ...]

    def state(self, t_ns: int) -> np.ndarray:
        tau = (t_ns - self.t_ns) * NS
        rot = np.exp(1j * self.delta * tau) * self.c
        part = self.w @ rot if self.c.size else np.zeros(self.poles, dtype=complex)
        x = tau / self.tc
        decay = math.exp(-x) if x < 745 else 0.0
        hom = np.zeros(self.poles, dtype=complex)
        if decay:
            powers = [x**m / math.factorial(m) for m in range(self.poles)]
            for j in range(self.poles):
                hom[j] = decay * sum(self.e[j - m] * powers[m] for m in range(j + 1))
        return part + hom


@component("lockin_demodulator")
class LockinDemodulator(Component):
    """Coupling, front-end noise, dual-phase mixing, n-pole filter, overload detection.

    Readings are input-referred rms volts and degrees. ``sensitivity`` sets full scale for the
    overload flags and the auto-gain ladder, not the scaling of X, Y and R.
    """

    ports = {"in": PortSpec("in", "signal"), "ref": PortSpec("in", "reference")}
    defaults = {
        "sensitivity": 1.0,  # V rms full scale: overload flags and auto gain, not the scaling of X, Y, R
        "time_constant": 0.1,  # s, filter time constant T
        "slope": "24",  # dB/octave of the low-pass; 6 per pole (24: 4 poles)
        "coupling": "DC",  # DC | AC input coupling
        "ac_corner": 0.16,  # corner of the AC coupling high-pass
        "noise_density": 5e-9,  # V/√Hz, hidden state
        "input_limit": 1.4,  # V peak
        "reserve": 10.0,  # 20 dB of dynamic reserve over the sensitivity
        "truncation_limit": 1e-6,  # filter gain past which truncated input raises error 329
    }
    units = {"noise_density": "V/√Hz", "input_limit": "V", "ac_corner": "Hz"}
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._regime: _Regime | None = None
        self._seen = -1
        self._in_peak = 0.0
        self._flags = 0
        self._limit_flag = False
        self._zero_start = False

    # ---- settings -------------------------------------------------------------------------------

    def _tc(self) -> float:
        return float(self.param("time_constant"))

    def _poles(self) -> int:
        return round(float(self.param("slope")) / 6)

    def enbw(self) -> float:
        return enbw(self._poles(), self._tc())

    def settling(self) -> float:
        return settling_multiple(self._poles()) * self._tc()

    # ---- regimes --------------------------------------------------------------------------------

    def _coupled_input(self) -> Signal:
        sig = self.input("in")
        if self.param("coupling") == "AC":
            fc = float(self.param("ac_corner"))
            sig = sig.transfer(lambda f: (1j * np.asarray(f) / fc) / (1 + 1j * np.asarray(f) / fc))
        return sig

    def reset(self) -> None:
        self._regime = None  # the filter restarts from zero at the next sync
        self._seen = -1
        self._zero_start = True

    def sync(self) -> None:
        if self._seen == self.session._signal_version:
            return
        self._seen = self.session._signal_version
        now = self.session.now_ns
        sig = self._coupled_input()
        ref = self.input("ref")
        tc, poles = self._tc(), self._poles()
        source = (
            sig.freq.tobytes(),
            sig.amp.tobytes(),
            sig.t0_ns.tobytes(),
            sig.dc,
            ref.freq.tobytes(),
            ref.amp.tobytes(),
            ref.t0_ns.tobytes(),
            tc,
            poles,
        )
        old = self._regime
        if old is not None and old.source == source:
            return
        if old is None:
            # Power-on: settled long ago. After *RST: the filter starts from zero.
            start = np.zeros(poles, dtype=complex) if self._zero_start else None
        else:
            x = old.state(now)
            # Fewer stages keep the first ones; added stages start at the value of the last one.
            start = x[:poles] if poles <= x.size else np.concatenate([x, np.full(poles - x.size, x[-1])])
        self._zero_start = False
        self._regime = self._build(now, sig, ref, tc, poles, start, source)
        self._in_peak = sig.peak()
        self._check_truncation(sig, ref, tc, poles)
        self._update_flags()

    def _build(
        self,
        now: int,
        sig: Signal,
        ref: Signal,
        tc: float,
        poles: int,
        start: np.ndarray | None,
        source: tuple[Any, ...],
    ) -> _Regime:
        coefs: list[np.ndarray] = []
        deltas: list[np.ndarray] = []
        if ref.freq.size:
            fd = float(ref.freq[0])
            wd = TWO_PI * fd
            # e^{-iΦ(t_s)} with Φ(t) = arg(r) + ω_d (t − t0_r)
            phi = cmath.phase(complex(ref.amp[0])) + TWO_PI * float(
                phase_cycles(np.array([fd]), np.array([now - int(ref.t0_ns[0])]))[0]
            )
            back = cmath.exp(-1j * phi)
            if sig.freq.size:
                rot = np.exp(2j * np.pi * phase_cycles(sig.freq, now - sig.t0_ns))
                a_now = sig.amp * rot  # each line's phasor at t_s
                w = TWO_PI * sig.freq
                coefs += [(math.sqrt(2) / 2) * a_now * back, (math.sqrt(2) / 2) * np.conj(a_now) * back]
                deltas += [w - wd, -(w + wd)]
            if sig.dc:
                coefs.append(np.array([math.sqrt(2) * sig.dc * back]))
                deltas.append(np.array([-wd]))
        c = np.concatenate(coefs) if coefs else np.zeros(0, dtype=complex)
        delta = np.concatenate(deltas) if deltas else np.zeros(0)
        stages = np.arange(1, poles + 1)[:, None]
        w = (
            1.0 / (1.0 + 1j * delta[None, :] * tc) ** stages
            if c.size
            else np.zeros((poles, 0), dtype=complex)
        )
        part0 = w @ c if c.size else np.zeros(poles, dtype=complex)
        e = np.zeros(poles, dtype=complex) if start is None else start - part0
        return _Regime(now, tc, poles, c, delta, w, e, source)

    def _check_truncation(self, sig: Signal, ref: Signal, tc: float, poles: int) -> None:
        """329 when the filter passes more than a millionth of what a truncated input left out."""
        flag = False
        if ref.freq.size and sig.freq.size > 1 and any(n.kind == "truncated" for n in sig.notes):
            f = np.sort(sig.freq)
            f_next = f[-1] + (f[-1] - f[-2])
            gain = abs(1 / (1 + 1j * TWO_PI * (f_next - float(ref.freq[0])) * tc)) ** poles
            flag = gain > float(self.param("truncation_limit"))
        if flag and not self._limit_flag:
            assert self.owner is not None
            self.owner.push_error(329, "Input beyond the model's limits")
        self._limit_flag = flag

    # ---- readings -------------------------------------------------------------------------------

    def _clean(self) -> complex:
        self.session._sync()
        r = self._regime
        return complex(r.state(self.session.now_ns)[-1]) if r is not None else 0j

    def _noise(self) -> complex:
        """Filtered white front-end noise, σ = e_n·√ENBW per quadrature, a function of virtual time."""
        en = float(self.param("noise_density"))
        if en <= 0:
            return 0j
        tc, poles = self._tc(), self._poles()
        dt_ns = max(1, round(tc * 1e9 / 16))
        now = self.session.now_ns
        count = int((8 + 6 * poles) * 16)
        idx = now // dt_ns - np.arange(count, dtype=np.int64)
        tau = (now - idx * dt_ns) * NS
        g = tau ** (poles - 1) * np.exp(-tau / tc)
        norm = math.sqrt(float(np.sum(g * g)))
        sigma = en * math.sqrt(enbw(poles, tc))
        assert self.owner is not None
        key = stream_key(self.session.seed, self.owner.name, self.name, tc, poles)
        x = float(g @ gaussian(key, idx)) / norm
        y = float(g @ gaussian(key ^ 0x1F2E3D4C, idx)) / norm
        return sigma * complex(x, y)

    def xy(self) -> complex:
        return self._clean() + self._noise()

    def _update_flags(self) -> None:
        sens = float(self.param("sensitivity"))
        limit = min(math.sqrt(2) * float(self.param("reserve")) * sens, float(self.param("input_limit")))
        z = self._regime.state(self.session.now_ns)[-1] if self._regime is not None else 0j
        flags = (1 if self._in_peak > limit * (1 + 1e-12) else 0) | (
            2 if max(abs(z.real), abs(z.imag)) > sens * (1 + 1e-12) else 0
        )
        rising = flags & ~self._flags
        assert self.owner is not None
        if rising & 1:
            self.owner.push_error(325, "Input overload")
        if rising & 2:
            self.owner.push_error(326, "Output overload")
        self._flags = flags

    def _flags_now(self) -> int:
        sens = float(self.param("sensitivity"))
        limit = min(math.sqrt(2) * float(self.param("reserve")) * sens, float(self.param("input_limit")))
        z = self._clean()
        return (1 if self._in_peak > limit * (1 + 1e-12) else 0) | (
            2 if max(abs(z.real), abs(z.imag)) > sens * (1 + 1e-12) else 0
        )

    def readings(self) -> dict[str, Any]:
        """Outputs now, and where the filter is heading: R over the next 1.5 settling times."""
        z = self.xy()
        r = self._regime
        now = self.session.now_ns
        span = 1.5 * self.settling()
        points = 121
        dt = span / (points - 1)
        trace: list[float] = []
        if r is not None:
            for i in range(points):
                trace.append(abs(complex(r.state(now + round(i * dt * 1e9))[-1])))
        return {
            "values": {
                "X": z.real,
                "Y": z.imag,
                "R": abs(z),
                "THETA": math.degrees(math.atan2(z.imag, z.real)),
                "overload": self._flags_now(),
                "enbw": self.enbw(),
                "settling": self.settling(),
                "noise_sigma": float(self.param("noise_density")) * math.sqrt(self.enbw()),
            },
            "traces": [{"name": "R_projected", "t0_s": 0.0, "dt_s": dt, "values": trace}] if trace else [],
        }

    def overload(self) -> int:
        self.session._sync()
        self._update_flags()
        return self._flags

    # ---- operations -----------------------------------------------------------------------------

    def _value(self, name: str, z: complex) -> float:
        key = name.upper()
        if key == "X":
            return z.real
        if key == "Y":
            return z.imag
        if key == "R":
            return abs(z)
        if key in ("THETA", "THET", "TH", "T", "PHASE"):
            return math.degrees(math.atan2(z.imag, z.real))
        raise ScpiError(-224, f"unknown output {name!r}; use X, Y, R or THETa")

    @op("measure")
    def op_measure(self, what: str = "R", **_: Any) -> OpResult:
        self.overload()
        return OpResult(V.format_float(self._value(what, self.xy()), 8))

    @op("snap")
    def op_snap(self, names: list[bytes] | None = None, **_: Any) -> OpResult:
        items = [V.parse_string(r).strip() for r in names or []]
        items = [i for part in items for i in part.split(",") if i]
        if not 1 <= len(items) <= 4:
            raise ScpiError(-109, "SNAP? takes one to four of X, Y, R, THETa")
        self.overload()
        z = self.xy()  # one instant for every value
        return OpResult(b",".join(V.format_float(self._value(i, z), 8) for i in items))

    @op("filter_info")
    def op_filter_info(self, what: str = "enbw", **_: Any) -> OpResult:
        return OpResult(V.format_float(self.enbw() if what == "enbw" else self.settling(), 8))

    @op("overload")
    def op_overload(self, what: str = "all", **_: Any) -> OpResult:
        flags = self.overload()
        value = {"all": flags, "input": flags & 1, "output": (flags & 2) >> 1}[what]
        return OpResult(V.format_int(value))

    @op("auto_gain")
    def op_auto_gain(self, **_: Any) -> OpResult:
        """Smallest 1-2-5 sensitivity that keeps R under 90 % of full scale and the input in range."""
        if self._tc() > 1.0:
            raise ScpiError(-221, "auto gain needs a time constant of 1 s or less")
        assert self.owner is not None
        r = abs(self.xy())
        self.session._sync()
        spec = self.owner.specs.get("input.sensitivity")
        lo = spec.min if spec and spec.min is not None else 2e-9
        hi = spec.max if spec and spec.max is not None else 1.0
        rungs = sorted(
            {
                m * 10.0**k
                for k in range(-9, 1)
                for m in (1.0, 2.0, 5.0)
                if lo * 0.999 <= m * 10.0**k <= hi * 1.001
            }
        )
        limit_v = float(self.param("input_limit"))
        reserve = float(self.param("reserve"))
        choice = rungs[-1]
        for s in rungs:
            if r < 0.9 * s and self._in_peak <= min(math.sqrt(2) * reserve * s, limit_v):
                choice = s
                break
        self.owner.set_state("input.sensitivity", choice)
        self._flags = 0
        self._update_flags()
        return OpResult()

    @op("auto_phase")
    def op_auto_phase(self, **_: Any) -> OpResult:
        """Move the reference phase so that the settled output has θ = 0."""
        assert self.owner is not None
        theta = math.degrees(cmath.phase(self._clean()))
        phase = float(self.owner.get_state("reference.phase")) + theta
        phase = (phase + 180.0) % 360.0 - 180.0
        self.owner.set_state("reference.phase", phase)
        return OpResult()
