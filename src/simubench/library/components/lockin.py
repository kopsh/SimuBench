"""Lock-in amplifier as its real blocks (D50, D3 #189): input filter and amplifier, the phase
sensitive detector, the low-pass filter, the DC output, the reference oscillator, the data buffer
and the AUX ports.

The chain is data-level except the terminals: ``lockin_input_filter`` (AC coupling and the
line-frequency notches), ``lockin_input_amp`` (sensitivity, dynamic reserve, the input limit and
the front-end noise), ``lockin_mixer`` (multiplication by the reference: every input line becomes a
complex exponential at Δf), ``lockin_lowpass`` (the n-pole RC filter, its time constant and slope,
the settling and the truncation check), ``lockin_output`` (X/Y with offset and expansion, R/θ, the
overload flags, auto gain and auto phase, and the sampler the data buffer reads) and
``lockin_reference`` (the oscillator or the PLL tracking REF_IN). ``lockin_aux`` is the circuit
level AUX block: four ADC inputs and four DAC outputs on the bench. ``lockin_input_mode`` forms the
measured signal from the A and B terminals (A, A − B, or the current input, #273). The data buffer
is either the generic ``trigger_model`` + ``reading_buffer`` (#88) or ``curve_buffer`` (#273: traces
at a sample rate, read by bin), which reads ``trace_value`` from the output and AUX blocks.

The demodulator is exact: the cascade of n identical first-order stages has a closed-form response
to a sum of exponentials (a particular solution plus a homogeneous part ``e^(-t/T) · polynomial``),
so settling after any change follows the order-n exponential in virtual time without sampling
anything (validation-lockin.md 1). Each change of signal or setting starts a new regime from the
filter state at that instant; the output component samples that closed form at the instants the
data buffer asks for.

The split keeps the behaviour of the old monolith exactly (``tests/test_lockin_migration.py``).
"""

from __future__ import annotations

import cmath
import itertools
import math
from dataclasses import dataclass
from functools import cache
from typing import Any

import numpy as np

from ...engine.circuit import GROUND, Circuit
from ...engine.component import Component, OpResult, PortSpec, TraceSource, component, op
from ...engine.noise import gaussian, stream_key
from ...engine.signal import NS, Signal, phase_cycles
from ...scpi import values as V
from ...scpi.errors import ScpiError

TWO_PI = 2 * math.pi
F_MAX = 500e3  # highest detection frequency, n * f
HISTORY = 256  # filter regimes kept for sampling instants in the past


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
    demodulator's reference), and ``sine``, the SINE_OUT drive at f (``amplitude`` in V rms) on
    the dc ``dc_level``.
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
        "dc_level": 0.0,  # V, the dc level added to the SINE_OUT drive (the SR86x's SOFF, #407)
        "min_vpp": 0.4,  # V peak to peak an external reference needs to lock
        "f_max": F_MAX,  # Hz, the highest detection frequency n x f (#273: the SR830's 102 kHz)
        # Past f_max: "refuse" the setting (-221), or "clamp" the harmonic to the largest that fits
        # (the SR830 manual's rule for HARM and FREQ).
        "over_limit": "refuse",
        # What on REF_IN defines zero phase (#273, the SR830's RSLP): SINE the positive-going zero
        # crossing of a sine; RISE a TTL rising edge (on a sine, the same crossing); FALL a TTL
        # falling edge, half a period later (the reference moves by 180°).
        "ext_slope": "SINE",
    }
    units = {"f_max": "Hz"}
    choices = {"over_limit": ("refuse", "clamp"), "ext_slope": ("SINE", "RISE", "FALL")}
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._anchor_ns = 0  # ψ(t) = psi + 2π f (t − anchor)
        self._psi = 0.0
        self._freq = float(self.param("frequency"))
        self.locked = self.param("source") != "EXTernal"

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

    def _largest_harmonic(self, f: float) -> int:
        return max(1, int(float(self.param("f_max")) * (1 + 1e-12) // f)) if f > 0 else 1

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> Any:
        n = int(self.param("harmonic"))
        f_max = float(self.param("f_max"))
        clamp = self.choice("over_limit") == "clamp"
        if key == "harmonic" and n * self._freq > f_max * (1 + 1e-12):
            if clamp:
                return self._largest_harmonic(self._freq)  # the largest harmonic that fits (#273)
            raise ScpiError(-221, f"harmonic {n} x {self._freq:g} Hz exceeds {f_max:g} Hz")
        if key == "frequency":
            f = float(new)
            if n * f > f_max * (1 + 1e-12):
                if not clamp:
                    raise ScpiError(-221, f"harmonic {n} x {f:g} Hz exceeds {f_max:g} Hz")
                assert self.owner is not None
                self.owner.set_state(self.bound_path("harmonic"), self._largest_harmonic(f))
            if self.param("source") != "EXTernal":
                self._retune(f)
        if key == "source" and new != "EXTernal":
            self.locked = True
            self._retune(float(self.param("frequency")))

    def sync(self) -> None:
        if self.param("source") != "EXTernal" or not self.session.signal_changed(self):
            return
        sig = self.input("ext").merged()
        line = None
        if sig.freq.size:
            k = int(np.argmax(np.abs(sig.amp) * (sig.freq > 0)))
            if sig.freq[k] > 0 and 2 * abs(sig.amp[k]) >= float(self.param("min_vpp")):
                line = (float(sig.freq[k]), complex(sig.amp[k]))
        if line is not None and 1e-3 <= line[0] <= float(self.param("f_max")):
            freq, a = line
            # Lock to the fundamental: ψ(t) = arg(a) + 2π f t (merged lines are referenced to t = 0).
            edge = math.pi if self.choice("ext_slope") == "FALL" else 0.0
            psi = (cmath.phase(a) + edge) % TWO_PI
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

    def status_flags(self) -> dict[str, bool]:
        """UNLK while an external reference is not locked (the SRS LIA status bit 3, #273)."""
        return {"UNLK": self.param("source") == "EXTernal" and not self.locked}

    def output(self, port: str) -> Signal:
        if port == "ref":
            n = int(self.param("harmonic"))
            # A reference phase φ shifts the reference, so θ = φ_signal − φ (θ falls as φ rises).
            phase = n * self._psi + math.radians(float(self.param("phase")))
            return Signal.lines([n * self._freq], [cmath.exp(1j * phase)], t0_ns=self._anchor_ns)
        amp = float(self.param("amplitude")) * math.sqrt(2)
        sine = Signal.lines([self._freq], [amp * cmath.exp(1j * self._psi)], t0_ns=self._anchor_ns)
        dc = float(self.param("dc_level") or 0.0)
        return sine + Signal.constant(dc) if dc else sine

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

    def trace_value(self, quantity: str, t_ns: int) -> float:
        """``FREQUENCY`` (the reference frequency in use: internal, or the measured external one)
        or ``DETECT`` (times the harmonic), for a readings table or a trace (#383)."""
        key = quantity.upper()
        if key == "DETECT":
            return self._freq * int(self.param("harmonic"))
        if key == "FREQUENCY":
            return self._freq
        raise ScpiError(-300, f"{self.uid}: no reference quantity {quantity!r}")

    @op("frequency")
    def op_frequency(self, what: str = "actual", **_: Any) -> OpResult:
        f = self._freq * (int(self.param("harmonic")) if what == "detect" else 1)
        return OpResult(self.reply_number(f, 10))


# ---- input configuration ------------------------------------------------------------------------


@component("lockin_input_mode")
class LockinInputMode(Component):
    """The input configuration (#273, the SR830's ISRC): single-ended A, differential A − B, or the
    current input.

    The terminals are ``input_port`` blocks in front (their impedance is the circuit part: 10 MΩ
    for the voltage inputs; the current input is ``current_z`` to a virtual ground, so the
    definition binds the A port's ``z_in`` to the same setting). This block forms the signal the
    chain measures: A, A − B, or the current into A (its voltage over ``current_z``) times
    ``current_scale`` V/A, so the voltage sensitivities read as currents scaled by 1/current_scale
    (``lockin_output.unit_scale``). Shield grounding is not a circuit element here: bench nets
    are referred to one ground, so floating or grounding the shield changes no reading.
    """

    ports = {
        "a": PortSpec("in", "signal", "the A input"),
        "b": PortSpec("in", "signal", "the B input"),
        "out": PortSpec("out", "signal", "the signal the chain measures"),
    }
    defaults = {
        "mode": "A",  # A | A-B | I: single-ended, differential, or the current input on A
        "current_z": 1.0e3,  # ohm, the current input's impedance to its virtual ground
        "current_scale": 1.0e6,  # V the chain sees per A in the current input (1 µA <-> 1 V)
    }
    units = {"current_z": "OHM"}
    choices = {"mode": ("A", "A-B", "I")}

    def output(self, port: str) -> Signal:
        if port != "out":
            raise NotImplementedError(f"{self.type_name} has no signal output {port!r}")
        mode = self.choice("mode")
        a = self.input("a")
        if mode == "A-B":
            return a + self.input("b").scale(-1.0)
        if mode == "I":
            return a.scale(float(self.param("current_scale")) / float(self.param("current_z")))
        return a


# ---- input filter -------------------------------------------------------------------------------


@component("lockin_input_filter")
class LockinInputFilter(Component):
    """The input coupling and the line-frequency notches (D3 #189, SR830 block diagram).

    AC coupling is a first-order high-pass at ``ac_corner``; the line notch (and its second
    harmonic) is a second-order band-stop of quality ``notch_q`` at ``line_frequency``. Both act on
    the spectral lines (data level, like the coupling the old monolith applied); the input
    impedance, shielding and the differential pair are the circuit part of the input terminal.
    """

    ports = {
        "in": PortSpec("in", "signal"),
        "out": PortSpec("out", "signal"),
    }
    defaults = {
        "coupling": "DC",  # DC | AC input coupling
        "ac_corner": 0.16,  # Hz, corner of the AC coupling high-pass
        "line_notch": False,  # the line-frequency notch on
        "notch_2x": False,  # the notch at twice the line frequency on
        "line_frequency": 50.0,  # Hz the notches sit on
        "notch_q": 10.0,  # quality factor of the notches (a model value)
    }
    units = {"ac_corner": "Hz", "line_frequency": "Hz"}

    def _notch(self, f0: float) -> Any:
        q = float(self.param("notch_q"))
        w0 = TWO_PI * f0

        def response(f: Any) -> Any:
            f = np.asarray(f, dtype=float)
            w = TWO_PI * f
            return (w0**2 - w**2) / (w0**2 - w**2 + 1j * w * w0 / q)

        return response

    def output(self, port: str) -> Signal:
        if port != "out":
            raise NotImplementedError(f"{self.type_name} has no signal output {port!r}")
        sig = self.input("in")
        if self.param("coupling") == "AC":
            fc = float(self.param("ac_corner"))
            sig = sig.transfer(lambda f: (1j * np.asarray(f) / fc) / (1 + 1j * np.asarray(f) / fc))
        line = float(self.param("line_frequency"))
        if self.param("line_notch"):
            sig = sig.transfer(self._notch(line))
        if self.param("notch_2x"):
            sig = sig.transfer(self._notch(2 * line))
        return sig


# ---- input amplifier ----------------------------------------------------------------------------


@component("lockin_input_amp")
class LockinInputAmp(Component):
    """The low-noise front-end amplifier (D3 #189): the sensitivity full scale, the dynamic reserve,
    the absolute input limit and the front-end noise density.

    Readings are input-referred (the gain does not scale X, Y or R); sensitivity sets full scale for
    the overload limits and the auto-gain ladder, and the reserve widens the limit over it. The
    signal itself passes through unchanged, which is what makes the outputs input-referred.
    """

    ports = {
        "in": PortSpec("in", "signal"),
        "out": PortSpec("out", "signal"),
    }
    defaults = {
        "sensitivity": 1.0,  # V rms full scale: overload flags and auto gain, not the scaling of X, Y, R
        "reserve": 10.0,  # 20 dB of dynamic reserve over the sensitivity
        # The reserve as a table of dB by full-scale sensitivity (#273, the SR830's p.4-8 table per
        # RMOD): {sensitivity V: dB}; between entries the dB follow log(sensitivity). Set, it
        # replaces `reserve`.
        "reserve_db": None,
        "input_limit": 1.4,  # V peak
        "noise_density": 5e-9,  # V/√Hz, hidden state
    }
    units = {"noise_density": "V/√Hz", "input_limit": "V"}

    def output(self, port: str) -> Signal:
        if port != "out":
            raise NotImplementedError(f"{self.type_name} has no signal output {port!r}")
        return self.input("in")

    def sensitivity(self) -> float:
        return float(self.param("sensitivity"))

    def peak(self) -> float:
        """Peak of the signal at the amplifier's input, V."""
        return self.input("in").peak()

    def reserve(self, sensitivity: float | None = None) -> float:
        """The dynamic reserve as a voltage ratio at a sensitivity (the current one by default)."""
        table = self.param("reserve_db")
        if not table:
            return float(self.param("reserve"))
        s = self.sensitivity() if sensitivity is None else float(sensitivity)
        points = sorted((math.log10(float(k)), float(v)) for k, v in table.items())
        x = math.log10(s)
        if x <= points[0][0]:
            db = points[0][1]
        elif x >= points[-1][0]:
            db = points[-1][1]
        else:
            db = next(
                y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                for (x0, y0), (x1, y1) in itertools.pairwise(points)
                if x0 <= x <= x1
            )
        return 10.0 ** (db / 20.0)

    def limit(self) -> float:
        """Peak above which the front end overloads, V."""
        return min(math.sqrt(2) * self.reserve() * self.sensitivity(), float(self.param("input_limit")))

    def noise_density(self) -> float:
        return float(self.param("noise_density"))


# ---- mixer --------------------------------------------------------------------------------------


@component("lockin_mixer")
class LockinMixer(Component):
    """The phase-sensitive detector (D3 #189): the input's spectral lines multiplied by the
    reference, each becoming a complex exponential at Δf = f − f_detect, plus the input's DC term.
    The low-pass filter is applied to those exponentials by ``lockin_lowpass``."""

    ports = {"ref": PortSpec("in", "reference")}

    def mix(self, sig: Signal, now: int) -> tuple[np.ndarray, np.ndarray]:
        """(coefficients, angular frequencies) of the mixed signal at ``now``."""
        ref = self.input("ref")
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
        return c, delta


# ---- low-pass filter ----------------------------------------------------------------------------


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


@component("lockin_lowpass")
class LockinLowpass(Component):
    """The n-pole RC low-pass (D3 #189): time constant, slope (6 dB/oct per pole), the closed-form
    settling from a new regime, the equivalent noise bandwidth, and the truncation check that
    queues 329 when a truncated input still passes the filter."""

    ports = {
        "in": PortSpec("in", "signal"),
        "ref": PortSpec("in", "reference"),
    }
    refs = {"mixer": ("lockin_mixer", "instrument")}
    defaults = {
        "time_constant": 0.1,  # s, filter time constant T
        "slope": "24",  # dB/octave of the low-pass; 6 per pole (24: 4 poles)
        "truncation_limit": 1e-6,  # filter gain past which truncated input raises error 329
        "mixer": "",  # the lockin_mixer that multiplies the input by the reference
    }
    units = {"time_constant": "s"}
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._regime: _Regime | None = None
        self._history: list[_Regime] = []  # regimes since the restart, for sampling past instants
        self._limit_flag = False
        self._zero_start = False
        self._serial = 0  # regimes built so far: the output updates its flags when this moves

    # ---- settings -------------------------------------------------------------------------------

    def _tc(self) -> float:
        return float(self.param("time_constant"))

    def _poles(self) -> int:
        return round(float(self.param("slope")) / 6)

    def _mixer(self) -> LockinMixer:
        return self.ref("mixer")  # type: ignore[return-value]

    def enbw(self) -> float:
        return enbw(self._poles(), self._tc())

    def settling(self) -> float:
        return settling_multiple(self._poles()) * self._tc()

    def regime(self) -> _Regime | None:
        return self._regime

    def serial(self) -> int:
        """How many regimes have been built since the restart (the output's overload trigger)."""
        return self._serial

    # ---- regimes --------------------------------------------------------------------------------

    def reset(self) -> None:
        self._regime = None  # the filter restarts from zero at the next sync
        self._history.clear()
        self._zero_start = True

    def sync(self) -> None:
        if not self.session.signal_changed(self):
            return
        now = self.session.now_ns
        sig = self.input("in")
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
        self._regime = self._build(now, sig, tc, poles, start, source)
        self._serial += 1
        self._history.append(self._regime)
        if len(self._history) > HISTORY:
            del self._history[:-HISTORY]
        self._check_truncation(sig, ref, tc, poles)

    def _build(
        self,
        now: int,
        sig: Signal,
        tc: float,
        poles: int,
        start: np.ndarray | None,
        source: tuple[Any, ...],
    ) -> _Regime:
        c, delta = self._mixer().mix(sig, now)
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

    # ---- future and past instants ---------------------------------------------------------------

    def _regime_for(self, t_ns: int) -> _Regime | None:
        """The regime in force at ``t_ns``: the latest one built at or before it."""
        chosen = None
        for r in self._history:
            if r.t_ns <= t_ns:
                chosen = r
        return chosen or self._regime

    def state_at(self, t_ns: int) -> np.ndarray:
        r = self._regime_for(t_ns)
        return r.state(t_ns) if r is not None else np.zeros(self._poles(), dtype=complex)

    def value_at(self, t_ns: int) -> complex:
        """The filter's output at ``t_ns`` (past, present or future)."""
        return complex(self.state_at(t_ns)[-1])

    @op("filter_info")
    def op_filter_info(self, what: str = "enbw", **_: Any) -> OpResult:
        return OpResult(self.reply_number(self.enbw() if what == "enbw" else self.settling(), 8))


# ---- output -------------------------------------------------------------------------------------


@component("lockin_output")
class LockinOutput(Component):
    """The DC output (D3 #189): X/Y with offset and expansion, R/θ, the overload flags and their
    errors, the front-end noise of the reading, auto gain and auto phase — and the sampler the data
    buffer reads (X/Y/R/θ at the buffer's instants).

    Offset and expansion are display-level: ``offset_x``/``offset_y`` are percent of full scale
    (subtracted) and ``expand`` multiplies the deviation from the offset; R and θ always show the
    raw outputs. The defaults (0 %, x1) leave X and Y untouched.
    """

    refs = {
        "filter": ("lockin_lowpass", "instrument"),
        "amp": ("lockin_input_amp", "instrument"),
    }
    defaults = {
        "filter": "",  # the lockin_lowpass (the settling output)
        "amp": "",  # the lockin_input_amp (sensitivity, reserve, noise)
        "sample_rate": 10.0,  # Hz, the data buffer's sampling rate (SRAT)
        "offset_x": 0.0,  # percent of full scale subtracted from X (display level)
        "offset_y": 0.0,  # percent of full scale subtracted from Y
        "expand": 1.0,  # 1 | 10 | 100: the deviation from the offset is multiplied by this
        # Per quantity (#273, the SR830's OEXP): X, Y and R each with their own expand (None:
        # `expand`); R's offset and expand act on the displayed R only, the R output stays raw.
        "expand_x": None,  # X's own expand (None: expand)
        "expand_y": None,  # Y's own expand (None: expand)
        "offset_r": 0.0,  # percent of full scale subtracted from the displayed R
        "expand_r": None,  # the displayed R's expand (None: expand)
        # The unit of X, Y and R per volt the chain measures (#273): 1 for a voltage input; the
        # current input's 1/current_scale (1e-6: the readings and the sensitivity are in A).
        "unit_scale": 1.0,
        # The readings table (#383, the SR86x OUTP?/SNAP? parameter list): entry k is index k,
        # {word: X, source: X}. A source is X, Y, R, THETA, XNOISE or YNOISE (this output), $path
        # (a setting's value) or <component>.<quantity> (that block's trace_value: an AUX input,
        # the reference frequency). Empty: OUTP?/SNAP? by X, Y, R, THETa as before.
        "readings": [],
    }
    units = {"sample_rate": "Hz"}
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._flags = 0
        self._seen_serial = -1
        self._run_start_ns: int | None = None
        self._run_point: dict[str, float] | None = None

    # ---- the blocks -----------------------------------------------------------------------------

    def _filter(self) -> LockinLowpass:
        return self.ref("filter")  # type: ignore[return-value]

    def _amp(self) -> LockinInputAmp:
        return self.ref("amp")  # type: ignore[return-value]

    # ---- the reading ----------------------------------------------------------------------------

    def _clean(self) -> complex:
        self.session.sync()
        return self._filter().value_at(self.session.now_ns)

    def _noise(self, now_ns: int | None = None) -> complex:
        """Filtered white front-end noise, σ = e_n·√ENBW per quadrature, a function of virtual time."""
        en = self._amp().noise_density()
        if en <= 0:
            return 0j
        filter = self._filter()
        tc, poles = filter._tc(), filter._poles()
        dt_ns = max(1, round(tc * 1e9 / 16))
        now = self.session.now_ns if now_ns is None else now_ns
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

    def _scale(self) -> float:
        return float(self.param("unit_scale"))

    def xy_at(self, t_ns: int) -> complex:
        return (self._filter().value_at(t_ns) + self._noise(t_ns)) * self._scale()

    def xy(self) -> complex:
        return (self._clean() + self._noise()) * self._scale()

    def settling(self) -> float:
        return self._filter().settling()

    def enbw(self) -> float:
        return self._filter().enbw()

    # ---- display transform ----------------------------------------------------------------------

    def _display(self, value: float, kind: str) -> float:
        show = kind.upper()
        if show not in ("X", "Y"):
            return value
        offset = float(self.param("offset_x" if show == "X" else "offset_y"))
        return (value - offset / 100 * self._amp().sensitivity() * self._scale()) * self._expand(show)

    def _expand(self, show: str) -> float:
        own = self.param(f"expand_{show.lower()}")
        return float(own if own is not None else self.param("expand"))

    def displayed_r(self, z: complex) -> float:
        """R as the display shows it: its own offset and expand (the R output stays raw, #273)."""
        offset = float(self.param("offset_r"))
        return (abs(z) - offset / 100 * self._amp().sensitivity() * self._scale()) * self._expand("R")

    def _value(self, name: str, z: complex) -> float:
        key = name.upper()
        if key == "X":
            return self._display(z.real, "X")
        if key == "Y":
            return self._display(z.imag, "Y")
        if key == "R":
            return abs(z)
        if key in ("THETA", "THET", "TH", "T", "PHASE"):
            return math.degrees(math.atan2(z.imag, z.real))
        raise ScpiError(-224, f"unknown output {name!r}; use X, Y, R or THETa")

    # ---- overload flags -------------------------------------------------------------------------

    def _update_flags(self) -> None:
        sens = self._amp().sensitivity()
        z = self._filter().value_at(self.session.now_ns)
        flags = (1 if self._amp().peak() > self._amp().limit() * (1 + 1e-12) else 0) | (
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
        sens = self._amp().sensitivity()
        z = self._clean()
        return (1 if self._amp().peak() > self._amp().limit() * (1 + 1e-12) else 0) | (
            2 if max(abs(z.real), abs(z.imag)) > sens * (1 + 1e-12) else 0
        )

    def sync(self) -> None:
        """Update the overload flags when the filter started a new regime (a signal or filter
        change); a setting such as sensitivity alone reaches them at the next reading, as before."""
        serial = self._filter().serial()
        if serial == self._seen_serial:
            return
        self._seen_serial = serial
        self._update_flags()

    def readings(self) -> dict[str, Any]:
        """Outputs now, and where the filter is heading: R over the next 1.5 settling times."""
        z = self.xy()
        r = self._filter().regime()
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
                "X": self._display(z.real, "X"),
                "Y": self._display(z.imag, "Y"),
                "R": abs(z),
                "R_display": self.displayed_r(z),
                "THETA": math.degrees(math.atan2(z.imag, z.real)),
                "overload": self._flags_now(),
                "enbw": self.enbw(),
                "settling": self.settling(),
                "noise_sigma": self._amp().noise_density() * math.sqrt(self.enbw()),
            },
            "traces": [{"name": "R_projected", "t0_s": 0.0, "dt_s": dt, "values": trace}] if trace else [],
        }

    def overload(self) -> int:
        self.session.sync()
        self._update_flags()
        return self._flags

    def status_flags(self) -> dict[str, bool]:
        """INPUT (input or reserve overload) and OUTPT (output overload) for the SRS LIA status
        byte (#273)."""
        flags = self._flags_now()
        return {"INPUT": bool(flags & 1), "OUTPT": bool(flags & 2)}

    # ---- operations -----------------------------------------------------------------------------

    @op("measure")
    def op_measure(self, what: str = "R", **_: Any) -> OpResult:
        self.overload()
        return OpResult(self.reply_number(self._value(what, self.xy()), 8))

    @op("measure_display")
    def op_measure_display(self, target: str = "", ratio: str = "", **_: Any) -> OpResult:
        """OUTR? i (SR830 manual 5-16): the value display channel i shows — the quantity its DDEF
        picks. ``target`` is the definition's ``<component>.<quantity>`` for that setting, the same
        sources a ``curve_buffer`` trace reads (X/R/XNoise and AUX1/2 on CH1, Y/θ/YNoise and
        AUX3/4 on CH2). ``ratio`` (#390): the quantity the display divides by (DDEF's ratio, an AUX
        input; empty: none); a denominator of 0 V shows 0."""
        self.overload()
        value = self._display_quantity(target)
        if ratio and str(ratio) not in ("None", "0"):
            den = self._display_quantity(ratio)
            value = value / den if den else 0.0
        return OpResult(self.reply_number(value, 8))

    def _display_quantity(self, spec: Any) -> float:
        name, _, quantity = str(spec).partition(".")
        comp = self.peer(name)
        if not isinstance(comp, TraceSource):
            raise ScpiError(-224, f"{spec!r} is not a display quantity")
        return float(comp.trace_value(quantity, self.session.now_ns))

    @op("snap")
    def op_snap(self, names: list[bytes] | None = None, **_: Any) -> OpResult:
        items = [V.parse_string(r).strip() for r in names or []]
        items = [i for part in items for i in part.split(",") if i]
        if not 1 <= len(items) <= 4:
            raise ScpiError(-109, "SNAP? takes one to four of X, Y, R, THETa")
        self.overload()
        z = self.xy()  # one instant for every value
        return OpResult(b",".join(self.reply_number(self._value(i, z), 8) for i in items))

    # ---- the readings table (#383) ----------------------------------------------------------------

    def _table(self) -> list[dict[str, str]]:
        return list(self.param("readings") or [])

    def _entry(self, item: Any) -> dict[str, str]:
        """A table entry by index (an integer) or by word (the CAPS short form or the full word)."""
        table = self._table()
        text = (V.parse_string(item) if isinstance(item, bytes) else str(item)).strip()
        try:
            number = float(text)  # an index; an int setting's value arrives as 13.0 (SNAPD?, OUTR?)
            if not number.is_integer():
                raise ScpiError(-224, f"parameter index {text!r} is not an integer")
            k = int(number)
        except ValueError:
            for entry in table:
                if V.matches_word(text, str(entry["word"])):
                    return entry
            raise ScpiError(-224, f"no lock-in parameter {text!r}") from None
        if not 0 <= k < len(table):
            raise ScpiError(-222, f"parameter index {k} is out of 0..{len(table) - 1}")
        return table[k]

    def _reading(self, source: str, z: complex) -> float:
        if source.startswith("$"):
            assert self.owner is not None
            return float(self.owner.get_state(source[1:]))
        if "." in source:
            name, quantity = source.rsplit(".", 1)
            comp = self.peer(name)
            if not isinstance(comp, TraceSource):
                raise ScpiError(-300, f"{self.uid}: reading source {source!r} has no such block")
            return float(comp.trace_value(quantity, self.session.now_ns))
        key = source.upper()
        if key in ("XNOISE", "YNOISE"):
            return self._amp().noise_density()
        return self._value(key, z)

    @op("readout")
    def op_readout(
        self, names: list[Any] | None = None, minimum: int = 1, maximum: int = 1, **_: Any
    ) -> OpResult:
        """``OUTP? j`` / ``SNAP? j,k{,l}`` / ``SNAPD?``: the table's parameters by index or word, all at
        one instant, comma separated in the order asked (#383)."""
        items: list[Any] = []
        for raw in names or []:
            if isinstance(raw, bytes):
                items += [p for p in V.parse_string(raw).split(",") if p.strip()]
            else:
                items.append(raw)
        if not int(minimum) <= len(items) <= int(maximum):
            raise ScpiError(-109, f"{minimum} to {maximum} parameters, got {len(items)}")
        entries = [self._entry(i) for i in items]
        self.overload()
        z = self.xy()  # one instant for every value
        return OpResult(b",".join(self.reply_number(self._reading(str(e["source"]), z), 8) for e in entries))

    @op("overload")
    def op_overload(self, what: str = "all", **_: Any) -> OpResult:
        flags = self.overload()
        value = {"all": flags, "input": flags & 1, "output": (flags & 2) >> 1}[what]
        return OpResult(V.format_int(value))

    @op("auto_gain")
    def op_auto_gain(self, **_: Any) -> OpResult:
        """Smallest 1-2-5 sensitivity that keeps R under 90 % of full scale and the input in range."""
        if self._filter()._tc() > 1.0:
            raise ScpiError(-221, "auto gain needs a time constant of 1 s or less")
        assert self.owner is not None
        r = abs(self.xy()) / self._scale()  # in the chain's volts, like the sensitivity ladder
        self.session.sync()
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
        limit_v = float(self._amp().param("input_limit"))
        choice = rungs[-1]
        for s in rungs:
            reserve = self._amp().reserve(s)
            if r < 0.9 * s and self._amp().peak() <= min(math.sqrt(2) * reserve * s, limit_v):
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

    # ---- the curve buffer's trace source (#273) ----------------------------------------------------

    def trace_value(self, quantity: str, t_ns: int) -> float:
        """A displayed quantity at an instant, as a ``curve_buffer`` trace stores it: X and Y with
        their offset and expand, R as displayed, θ in degrees; XNOISE/YNOISE are the front end's
        noise density (V/√Hz, the model's estimate of what the noise display shows)."""
        key = quantity.upper()
        if key in ("XNOISE", "YNOISE"):
            return self._amp().noise_density()
        z = self.xy_at(t_ns)
        if key == "R":
            return self.displayed_r(z)
        return self._value(key, z)

    # ---- the data buffer's sampler --------------------------------------------------------------

    def sample_time_ns(self) -> int:
        """One buffer reading: the interval the sampling rate sets."""
        return max(1, round(1e9 / float(self.param("sample_rate"))))

    def run_begin(self) -> None:
        """A buffer run starts now; the readings are taken at the sampler's own instants."""
        self._run_start_ns = self.session.now_ns
        self._run_point = None

    def sample(self, index: int) -> float:
        """The R at this reading's instant (the buffer keeps READ = R; X, Y, θ are fields)."""
        start = self._run_start_ns if self._run_start_ns is not None else self.session.now_ns
        step = self.sample_time_ns()
        t = start + (index + 1) * step
        z = self.xy_at(t)
        theta = math.degrees(math.atan2(z.imag, z.real))
        self._run_point = {
            "X": self._display(z.real, "X"),
            "Y": self._display(z.imag, "Y"),
            "R": abs(z),
            "THETA": theta,
            "TIME": (index + 1) * step * 1e-9,
        }
        return abs(z)

    def sample_fields(self, index: int) -> dict[str, float]:
        return dict(self._run_point) if self._run_point is not None else {}


# ---- AUX ----------------------------------------------------------------------------------------


@component("lockin_aux")
class LockinAux(Component):
    """The AUX block (D3 #189): four ADC inputs and four DAC outputs on the bench, circuit level.

    Each input is a high-impedance (``z_in`` to ground) ADC terminal; each output is a Thevenin
    source of ``dac`` volts behind ``z_out``. The inputs read the solved voltage of their nets, so a
    wire to another instrument or a DUT matters exactly as it does for any other circuit block.
    """

    ports = {
        **{f"in{n}": PortSpec("in", "electrical") for n in range(1, 5)},
        **{f"out{n}": PortSpec("out", "electrical") for n in range(1, 5)},
    }
    defaults = {
        "dac1": 0.0,  # V, DAC output 1
        "dac2": 0.0,  # V, DAC output 2
        "dac3": 0.0,  # V, DAC output 3
        "dac4": 0.0,  # V, DAC output 4
        "z_in": 1e6,  # input resistance to ground, ohm
        "z_out": 1e3,  # series output impedance of a DAC, ohm
    }
    units = {"z_in": "OHM", "z_out": "OHM"}

    def _source_id(self, n: int) -> str:
        return f"{self.uid}.out{n}"

    def stamp(self, circuit: Circuit, node: Any) -> None:
        for n in range(1, 5):
            circuit.resistor(node(f"in{n}"), GROUND, float(self.param("z_in")))
        for n in range(1, 5):
            circuit.source(node(f"out{n}"), self._source_id(n), float(self.param("z_out")))

    def emf(self) -> dict[str, Signal]:
        return {self._source_id(n): Signal.constant(float(self.param(f"dac{n}"))) for n in range(1, 5)}

    def read(self, index: str | int = "1") -> float:
        """The DC voltage on an AUX input, V."""
        return float(self.pin_signal(f"in{int(index)}").dc)

    @op("read")
    def op_read(self, index: str | int = "1", **_: Any) -> OpResult:
        return OpResult(self.reply_number(self.read(index), 8))

    def trace_value(self, quantity: str, t_ns: int) -> float:
        """``AUX1`` .. ``AUX4``: an AUX input's voltage, for a ``curve_buffer`` trace (#273)."""
        return self.read(quantity.upper().removeprefix("AUX") or "1")

    @op("set_dac")
    def op_set_dac(self, index: int = 1, voltage: float = 0.0, **_: Any) -> OpResult:
        """``AUXV n,v``: set DAC output ``index`` (1..4) to ``voltage`` through the definition's
        ``aux.output<n>`` state, so the value is a setting of the instrument like any other."""
        n = int(index)
        if not 1 <= n <= 4:
            raise ScpiError(-222, "an AUX output is 1 to 4")
        assert self.owner is not None
        self.owner.set_state(f"aux.output{n}", float(voltage))
        return OpResult()

    def readings(self) -> dict[str, Any]:
        return {"values": {f"in{n}": self.read(str(n)) for n in range(1, 5)}}
