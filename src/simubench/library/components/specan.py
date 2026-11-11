"""Swept spectrum analyzer as its real blocks (D50, D1 #187).

The analog front end passes signals from block to block (signal-chain.md 5.2, #952): the generic
``input_port`` (the 50 Ω termination, the analyzer's only circuit-layer part) feeds ``specan_input``
(the step attenuator and the mixer-overload check), ``specan_mixer`` (the conversion to the IF,
against ``specan_lo``) and ``specan_if`` (the Gaussian RBW filter, the IF gain); each has an ``out``
an internal probe can read. ``specan_lo`` is the local oscillator: where the receiver is tuned, the
±10 ppm 10 MHz reference (``reference_clock``) its frequency scale runs off, and how fast it sweeps.
The mixer's output and what follows it depend on that tuning: they read ``Session.tune_hz``, set
by whoever starts the read (the trace, a probe), and are the center frequency's outside one.

The blocks behind are data-level: ``specan_detector`` (positive peak / sample / average / RMS and
the log conversion), ``specan_vbw`` (the video filter's bandwidth) and ``specan_trace`` (sweep
timing, the RF history, the trace modes in virtual time, markers and the settings couplings the
scan owns). The trace is evaluated analytically: the lines at the head of the front end's chain,
drawn through the RBW response at every point, plus the noise of the chain's cascaded stages
(``engine.rfcascade``) — the display is calibrated to the input, so an ideal chain's gains cancel.

Lines are exact: every spectral line on RF_IN is drawn through the RBW filter at its power V²/2R.
Noise is the receiver's kT0·F·NBW plus the attenuation, drawn per resolution cell from a
counter-based generator keyed by (seed, sweep, point, cell), so a trace is a pure function of the
settings, the signals RF_IN carried, the seed and the sweep number: two reads at one virtual
instant are identical. The split keeps that property and every reply of the old monolith (see
``tests/test_specan_migration.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.special import erf

from ...engine import rfcascade
from ...engine.component import Component, OpResult, PortSpec, RfStage, RfStageSpec, component, op
from ...engine.noise import gaussian, stream_key
from ...engine.signal import FidelityNote, Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError

F_MAX = 7.5e9
RBW_LADDER = [m * 10.0**k for k in range(0, 7) for m in (1.0, 3.0)]  # 1 Hz ... 3 MHz
RBW_LADDER = [r for r in RBW_LADDER if r <= 3e6]
# The "10%" filter steps of analyzers with a digital IF (Keysight X-Series, #616): the E24 values
# from 1 Hz to 3 MHz, then 4, 5, 6 and 8 MHz.
_E24 = (
    1.0,
    1.1,
    1.2,
    1.3,
    1.5,
    1.6,
    1.8,
    2.0,
    2.2,
    2.4,
    2.7,
    3.0,
    3.3,
    3.6,
    3.9,
    4.3,
    4.7,
    5.1,
    5.6,
    6.2,
    6.8,
    7.5,
    8.2,
    9.1,
)
TEN_PCT_LADDER = [round(m * 10.0**k, 6) for k in range(0, 7) for m in _E24 if m * 10.0**k <= 3e6 + 1] + [
    4e6,
    5e6,
    6e6,
    8e6,
]
STEPS = ("1-3-10", "10%")
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
    if detector in ("POSitive", "NORMal"):
        e = np.maximum(np.abs(d) - bucket / 2, 0.0)
        return np.exp(-k * e * e)
    if detector == "NEGative":
        e = np.abs(d) + bucket / 2
        return np.exp(-k * e * e)
    rk = math.sqrt(k)
    return (math.sqrt(math.pi) / (2 * rk * bucket)) * (
        erf(rk * (d + bucket / 2)) - erf(rk * (d - bucket / 2))
    )


def snap_rbw(value: float) -> float:
    """The nearest 1-3-10 RBW setting."""
    return min(RBW_LADDER, key=lambda r: abs(math.log10(r) - math.log10(max(value, 1e-12))))


def filter_ladder(steps: str, lo: float, hi: float) -> list[float]:
    """The bandwidths a filter offers between ``lo`` and ``hi``: the 1-3-10 ladder, or the "10%"
    steps (E24 to 3 MHz, then 4/5/6/8 MHz). A ``hi`` above the ladder's top is one more setting,
    the wide-open position of a video filter (the X-Series 50 MHz VBW, #616)."""
    base = TEN_PCT_LADDER if steps == "10%" else RBW_LADDER
    out = [b for b in base if lo - 1e-9 <= b <= hi + 1e-9]
    if hi > base[-1] * (1 + 1e-9):
        out.append(hi)
    return out


def snap_filter(value: float, steps: str, lo: float, hi: float) -> float:
    """The setting a bandwidth entry selects: the nearest on a log scale for the 1-3-10 ladder;
    for the "10%" steps the nearest on a linear scale, a tie rounding up (the X-Series rule)."""
    ladder = filter_ladder(steps, lo, hi)
    if steps != "10%":
        return min(ladder, key=lambda r: abs(math.log10(r) - math.log10(max(value, 1e-12))))
    return min(ladder, key=lambda r: (abs(r - value), -r))


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
    #: The attenuation the noise floor adds on top of the figure: the real one in the earlier way
    #: (the figure is behind the attenuator), 0 when the input block's own figure is the whole
    #: receiver's (#954).
    floor_att: float = 0.0
    #: The chain's cascaded figures at the center frequency, when any stage declares intercepts:
    #: the distortion products and the compression the display draws (#954). None: an ideal chain.
    cascade: RfStageSpec | None = None

    def key(self) -> tuple[Any, ...]:
        cascade = self.cascade
        figures = (cascade.gain_db, cascade.iip2_dbm, cascade.iip3_dbm, cascade.p1db_dbm) if cascade else ()
        return (
            self.start,
            self.stop,
            self.points,
            self.rbw,
            self.sweep,
            self.att,
            self.detector,
            self.nf,
            *figures,
        )


@component("specan_input")
class SpecanInput(Component):
    """The input attenuator and overload check (D1 #187): the RF input port is the generic
    ``input_port`` (50 Ω to ground, the only circuit-layer part of the analyzer). The step
    attenuator is automatic (reference level kept at the mixer limit, 2 dB steps) or manual; the
    receiver overloads when the total input power minus the attenuation passes the maximum mixer
    level. ``out`` is the input attenuated (and amplified by the preamplifier, 0 dB unless
    declared)."""

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    defaults = {
        "z_in": 50.0,  # input impedance, ohm
        "max_mixer_level": -10.0,  # dBm at the mixer: total input minus attenuation above it overloads
        "att": 10.0,  # dB, manual attenuation (a setting such as $power.att)
        "att_auto": True,  # the attenuation is automatic (a setting)
        "ref_level": 0.0,  # dBm, the reference level the automatic attenuation holds (a setting)
        "preamp_gain": 0.0,  # dB, the preamplifier's gain (0: none, or off)
        # The preamplifier's own figures at its input, from the data sheet (signal-chain.md 5.2,
        # #954); None: ideal. The block's stage refers them to the analyzer's input through the
        # attenuator ahead of the preamplifier.
        "preamp_noise_figure": None,  # dB
        "iip2_dbm": None,  # dBm
        "iip3_dbm": None,  # dBm
        "p1db_dbm": None,  # dBm
        # The frequency response's deviation from its calibration, dB (signal-chain.md 5.2, #954):
        # piecewise linear over [[frequency Hz, dB], ...], the end points held; empty: flat.
        "flatness": [],
    }
    units = {
        "max_mixer_level": "dBm",
        "preamp_gain": "dB",
        "preamp_noise_figure": "dB",
        "iip2_dbm": "dBm",
        "iip3_dbm": "dBm",
        "p1db_dbm": "dBm",
    }

    def signal(self) -> Signal:
        return self.input("in")

    def gain_db(self) -> float:
        return float(self.param("preamp_gain")) - self.attenuation()

    def flatness_db(self, f_hz: float | np.ndarray) -> float | np.ndarray:
        """The response's deviation at ``f_hz``, dB: piecewise linear over the flatness table, the
        end points held beyond it; 0.0 without a table."""
        table = self.param("flatness") or ()
        if not table:
            return 0.0
        edges = np.array([float(row[0]) for row in table])
        deviations = np.array([float(row[1]) for row in table])
        return np.interp(f_hz, edges, deviations)

    def output(self, port: str) -> Signal:
        sig = self.signal().scale(10 ** (self.gain_db() / 20))
        if self.param("flatness"):
            sig = sig.transfer(lambda f: 10 ** (np.asarray(self.flatness_db(f)) / 20))
        return sig

    def stage(self, f_hz: float) -> RfStageSpec:
        """The block as one stage: the attenuator folded into the figures (referred ahead of it,
        its loss adds the same dB to the noise figure and to every intercept)."""
        att = self.attenuation()
        nf = self.param("preamp_noise_figure")
        ip2, ip3, p1 = (self.param(k) for k in ("iip2_dbm", "iip3_dbm", "p1db_dbm"))
        return RfStageSpec(
            gain_db=self.gain_db(),
            noise_figure_db=None if nf is None else float(nf) + att,
            iip2_dbm=math.inf if ip2 is None else float(ip2) + att,
            iip3_dbm=math.inf if ip3 is None else float(ip3) + att,
            p1db_dbm=math.inf if p1 is None else float(p1) + att,
        )

    def attenuation(self) -> float:
        if bool(self.param("att_auto")):
            # Keep the reference level at the mixer limit, in 2 dB steps.
            need = float(self.param("ref_level")) - float(self.param("max_mixer_level"))
            return float(min(70.0, max(0.0, 2 * math.ceil(need / 2 - 1e-9))))
        return float(self.param("att"))

    def overload(self) -> bool:
        sig = self.signal().merged()
        total_mw = float(np.sum(np.abs(sig.amp) ** 2)) / (2 * float(self.param("z_in"))) * 1e3
        total_mw += sig.dc**2 / float(self.param("z_in")) * 1e3
        if total_mw <= 0:
            return False
        return 10 * math.log10(total_mw) - self.attenuation() > float(self.param("max_mixer_level"))

    @op("coupled_att")
    def op_coupled_att(self, query: bool = False, value: float | None = None, **_: Any) -> OpResult:
        """The attenuation: a manual value turns the automatic coupling off."""
        if query:
            return OpResult(self.reply_number(self.attenuation(), 10))
        assert value is not None
        if not 0 <= value <= 70:
            raise ScpiError(-222)
        assert self.owner is not None
        self.owner.set_state("power.att", 2 * round(value / 2))
        self.owner.set_state("power.att_auto", False)
        return OpResult()

    @op("overload")
    def op_overload(self, query: bool = False, **_: Any) -> OpResult:
        return OpResult(V.format_bool(self.overload()))


@component("specan_lo")
class SpecanLo(Component):
    """The local oscillator (signal-chain.md 5.1): where the receiver is tuned, the reference its
    frequency runs off, and how fast it sweeps.

    The tuning point is the frequency whose signal the mixer puts on the IF: the one of the read
    in progress (``Session.tuned``, set by the trace or an internal probe), else the center
    frequency. The 10 MHz reference is a ``reference_clock``: its fractional error ε scales the
    oscillator, so a true f displays at f / (1 + ε), and the ``:ROSCillator`` status says whether
    the external reference is in use. Switching to EXTernal without a usable 10 MHz sine keeps the
    internal oscillator and reports -221. Its SSB phase noise (``phase_noise``) rides the display
    as noise around every line the mixer converts (the trace, #954); without the table there is
    none."""

    refs = {"clock": ("reference_clock", "instrument"), "if_filter": ("specan_if", "instrument")}
    defaults = {
        "clock": "",  # the reference_clock on REF_IN
        "source": "INTernal",  # watched: $rosc.source (INTernal | EXTernal)
        "if_filter": "",  # the specan_if whose intermediate frequency the oscillator tunes to
        "center": 0.0,  # Hz, where the receiver is tuned outside a sweep (a setting such as $freq.center)
        "lo_rate": 0.0,  # Hz/s, the fastest sweep (0: no limit); sets a floor on the auto sweep time
        "sweep_overhead": 0.0,  # s added to the sweep-rate floor (retrace and settling)
        # SSB phase noise, dBc/Hz (signal-chain.md 5.2, #954): [[offset Hz, dBc/Hz], ...],
        # log-log interpolation, the end offsets held beyond the table; empty: not modeled.
        "phase_noise": [],
    }
    units = {"center": "Hz", "lo_rate": "Hz/s", "sweep_overhead": "S"}

    def phase_noise_density(self, offset_hz: float | np.ndarray) -> np.ndarray | None:
        """The SSB phase noise at ``offset_hz`` from the carrier, linear (1/Hz): log-log
        interpolation over the table, the end offsets held beyond it. None: not modeled."""
        table = self.param("phase_noise") or ()
        if not table:
            return None
        offsets = np.log10([float(row[0]) for row in table])
        levels = np.array([float(row[1]) for row in table])
        asked = np.log10(np.maximum(np.abs(offset_hz), 10 ** offsets[0]))  # below the table, the first holds
        return 10 ** (np.interp(asked, offsets, levels) / 10)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._error = 0.0  # fractional frequency error of the reference in use
        self._locked = False

    def tuned(self) -> float:
        """The frequency the receiver is tuned to, on its own frequency scale, Hz."""
        tune = self.session.tune_hz
        return float(self.param("center")) if tune is None else tune

    def frequency(self) -> float:
        """The oscillator's true frequency at the tuning point: the intermediate frequency below
        the tuned frequency, scaled by the reference's error."""
        if_filter: Any = self.ref("if_filter")
        if_hz = float(if_filter.param("if_hz"))
        return (self.tuned() - if_hz) * (1 + self._error)

    def sweep_floor(self, span: float) -> float:
        """The shortest time the oscillator sweeps ``span`` in, s (0 when it has no rate limit)."""
        rate = float(self.param("lo_rate"))
        return span / rate + float(self.param("sweep_overhead")) if rate > 0 else 0.0

    def refresh(self) -> None:
        """Read the reference's error and lock state (called when the receiver syncs)."""
        clock: Any = self.ref("clock")
        self._error = float(clock.error())
        self._locked = bool(clock.locked())

    def error(self) -> float:
        return self._error

    def locked(self) -> bool:
        return self._locked

    def in_use(self) -> str:
        return "EXT" if self._locked else "INT"

    def display(self, f: Any) -> Any:
        return f / (1 + self._error)

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        if key == "source" and new == "EXTernal":
            self.session.sync()
            self.refresh()
            if not self._locked:
                assert self.owner is not None
                self.owner.push_error(
                    -221,
                    "external reference unusable: REF_IN needs a 10 MHz sine within 10 ppm, "
                    "the analyzer stays on its internal reference",
                )

    @op("status")
    def op_status(self, what: str = "locked", **_: Any) -> OpResult:
        self.session.sync()
        if what == "locked":
            return OpResult(V.format_bool(self.locked()))
        return OpResult(self.in_use().encode())


@component("specan_mixer")
class SpecanMixer(Component):
    """The mixer (signal-chain.md 5.2): ``out`` is the input converted to the IF against the local
    oscillator, f_if = f_rf − f_lo, less the conversion loss. What lies below the oscillator (the
    image side) is rejected by ``image_rejection`` — completely without it, as before; the
    oscillator's leak (``lo_feedthrough``) sits at DC in the IF frame; the declared ``spurs``
    (m·f_rf ± n·f_lo responses) and the distortion products of the mixer's own intercepts appear
    as further lines; the ``residuals`` are there with no input at all. The output depends on
    where the oscillator is tuned.

    ``noise_figure`` is the whole receiver's (the earlier way to declare it): with no other stage
    declaring one, the chain's cascaded figure is exactly this."""

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    tuned_ports = frozenset({"out"})
    refs = {"lo": ("specan_lo", "instrument")}
    defaults = {
        "noise_figure": 24.0,  # receiver noise figure over kT0, dB
        "lo": "",  # the specan_lo it converts against
        "conversion_loss": 0.0,  # dB lost from the RF line to its IF line
        # The mixer's own figures at its input, from the data sheet (signal-chain.md 5.2, #954).
        "iip2_dbm": None,  # dBm (None: ideal)
        "iip3_dbm": None,  # dBm
        "p1db_dbm": None,  # dBm
        "image_rejection": None,  # dB, the image side's rejection (None: complete)
        "lo_feedthrough": None,  # dBm, the oscillator's leak reaching the IF (None: none)
        "spurs": [],  # [[m, n, dBc], ...]: the m·f_rf − n·f_lo and m·f_rf + n·f_lo responses
        "residuals": [],  # [[frequency Hz, dBm], ...]: responses with no input
    }
    units = {
        "noise_figure": "dB",
        "conversion_loss": "dB",
        "iip2_dbm": "dBm",
        "iip3_dbm": "dBm",
        "p1db_dbm": "dBm",
        "image_rejection": "dB",
        "lo_feedthrough": "dBm",
    }

    def output(self, port: str) -> Signal:
        sig = self.input("in").merged()
        lo: Any = self.ref("lo")
        f_lo = float(lo.frequency())
        k = 10 ** (-float(self.param("conversion_loss")) / 20)
        # DC is a line at 0 Hz, so the image fold treats it like every other one.
        freq = np.concatenate([[0.0], sig.freq]) if sig.dc else sig.freq
        amp = np.concatenate([[sig.dc], sig.amp]) if sig.dc else sig.amp
        notes = list(sig.notes)
        f_if = freq - f_lo
        keep = f_if > 0
        out_f, out_a = [f_if[keep]], [amp[keep] * k]
        rejection = self.param("image_rejection")
        if rejection is not None:
            # The image side folds to its mirror about the oscillator, by the rejection ratio.
            folded = ~keep
            out_f.insert(0, -f_if[folded])
            out_a.insert(0, amp[folded] * k * 10 ** (-float(rejection) / 20))
        if freq.size:
            f_rf, power_mw = freq, np.abs(amp) ** 2 / (2 * 50.0) * 1e3
            # The declared spur responses: m·f_rf − n·f_lo and m·f_rf + n·f_lo, dBc of the line.
            for row in self.param("spurs") or ():
                m_, n_, dbc = float(row[0]), float(row[1]), float(row[2])
                ks = k * 10 ** (dbc / 20)
                for sign in (-1.0, 1.0):
                    f_spur = m_ * f_rf + sign * n_ * f_lo
                    ok = f_spur > 0
                    out_f.append(f_spur[ok])
                    out_a.append(amp[ok] * ks)
            # The distortion products the mixer's own intercepts make of the input lines
            # (#954): equivalent lines at the mixer's input, times the mixer's gain at its output.
            spec = self.stage(float(lo.tuned()))
            if math.isfinite(spec.iip2_dbm) or math.isfinite(spec.iip3_dbm):
                products = rfcascade.products(f_rf, 10 * np.log10(np.maximum(power_mw, 1e-300)), spec)
                if products.freq_hz.size:
                    amp_prod = np.sqrt(
                        2 * 50.0 * 10 ** (products.power_dbm / 10) * 1e-3 * 10 ** (spec.gain_db / 10)
                    )
                    out_f.append(products.freq_hz - f_lo)
                    out_a.append(amp_prod.astype(complex))
                if products.dropped:
                    notes = (
                        *notes,
                        FidelityNote(
                            "approximate",
                            "only the strongest 64 lines take part in the mixer's distortion products",
                        ),
                    )
        feedthrough = self.param("lo_feedthrough")
        dc = 0.0
        if feedthrough is not None:
            dc = math.sqrt(10 ** (float(feedthrough) / 10) * 1e-3 * 50.0)  # DC power is V²/R
        freq_out = np.concatenate(out_f)
        return Signal(freq_out, np.concatenate(out_a), np.zeros(freq_out.shape, dtype=np.int64), dc, notes)

    def stage(self, f_hz: float) -> RfStageSpec:
        ip2, ip3, p1 = (self.param(k) for k in ("iip2_dbm", "iip3_dbm", "p1db_dbm"))
        return RfStageSpec(
            gain_db=-float(self.param("conversion_loss")),
            noise_figure_db=float(self.param("noise_figure")),
            iip2_dbm=math.inf if ip2 is None else float(ip2),
            iip3_dbm=math.inf if ip3 is None else float(ip3),
            p1db_dbm=math.inf if p1 is None else float(p1),
        )


@component("specan_if")
class SpecanIf(Component):
    """The IF filter (D1 #187): the resolution bandwidth (auto: span / ``auto_ratio``, snapped to
    the model's filter steps and held at or below ``auto_max``; manual otherwise), the Gaussian
    passband lines are drawn through, and the receiver noise floor kT0·F·NBW at the RBW in use.
    The steps and limits are the model's (#616): the 1-3-10 ladder to 3 MHz by default, the
    X-Series "10%" steps to 8 MHz. ``out`` is the mixer's output through that passband, centered
    on the intermediate frequency, times the IF gain."""

    ports = {"in": PortSpec("in", "signal"), "out": PortSpec("out", "signal")}
    tuned_ports = frozenset({"out"})
    defaults = {
        "rbw": 3.0e6,  # Hz, manual RBW (a setting such as $bw.res)
        "rbw_auto": True,  # the RBW follows the span (a setting)
        "span": 7.5e9,  # Hz, the span the automatic RBW uses (a setting)
        "steps": "1-3-10",  # the filter steps: 1-3-10 (nearest on a log scale) | 10% (E24 to 3 MHz, then 4/5/6/8 MHz)
        "rbw_min": 1.0,  # Hz, the narrowest RBW
        "rbw_max": 3.0e6,  # Hz, the widest RBW a manual entry reaches
        "auto_ratio": 100.0,  # span / RBW the automatic coupling aims at (X-Series: 106)
        "auto_max": 3.0e6,  # Hz, the widest RBW the automatic coupling picks
        "if_hz": 21.4e6,  # Hz, the intermediate frequency the filter is centered on
        "gain": 0.0,  # dB, the IF amplifier's gain
        # The IF amplifier's own figures at its input, from the data sheet (#954); None: ideal.
        "iip2_dbm": None,  # dBm
        "iip3_dbm": None,  # dBm
        "p1db_dbm": None,  # dBm
    }
    units = {
        "rbw_min": "Hz",
        "rbw_max": "Hz",
        "auto_max": "Hz",
        "if_hz": "Hz",
        "gain": "dB",
        "iip2_dbm": "dBm",
        "iip3_dbm": "dBm",
        "p1db_dbm": "dBm",
    }
    choices = {"steps": STEPS}

    def _steps(self) -> str:
        return self.choice("steps") or "1-3-10"

    def snap(self, value: float, hi: float | None = None) -> float:
        top = float(self.param("rbw_max")) if hi is None else hi
        return snap_filter(value, self._steps(), float(self.param("rbw_min")), top)

    def rbw(self) -> float:
        if bool(self.param("rbw_auto")):
            ratio = float(self.param("auto_ratio"))
            top = min(float(self.param("auto_max")), float(self.param("rbw_max")))
            return self.snap(min(float(self.param("span")) / ratio, top), top)
        return float(self.param("rbw"))

    def response(self, d: np.ndarray, rbw: float, bucket: float, detector: str) -> np.ndarray:
        return line_response(d, rbw, bucket, detector)

    def noise_floor_mw(self, att: float, nf: float) -> float:
        return 10 ** ((KT0_DBM_HZ + nf + att + 10 * math.log10(NBW_FACTOR * self.rbw())) / 10)

    def output(self, port: str) -> Signal:
        k = 2 * math.log(2) / self.rbw() ** 2  # the Gaussian's voltage response: half the power exponent
        if_hz, gain = float(self.param("if_hz")), 10 ** (float(self.param("gain")) / 20)
        return self.input("in").transfer(lambda f: gain * np.exp(-k * (f - if_hz) ** 2))

    def stage(self, f_hz: float) -> RfStageSpec:
        ip2, ip3, p1 = (self.param(k) for k in ("iip2_dbm", "iip3_dbm", "p1db_dbm"))
        return RfStageSpec(
            gain_db=float(self.param("gain")),
            iip2_dbm=math.inf if ip2 is None else float(ip2),
            iip3_dbm=math.inf if ip3 is None else float(ip3),
            p1db_dbm=math.inf if p1 is None else float(p1),
        )

    @op("coupled_rbw")
    def op_coupled_rbw(self, query: bool = False, value: float | None = None, **_: Any) -> OpResult:
        if query:
            return OpResult(self.reply_number(self.rbw(), 10))
        assert value is not None
        if not float(self.param("rbw_min")) <= value <= float(self.param("rbw_max")):
            raise ScpiError(-222)
        assert self.owner is not None
        self.owner.set_state("bw.res", self.snap(value))
        self.owner.set_state("bw.res_auto", False)
        return OpResult()


@component("specan_detector")
class SpecanDetector(Component):
    """The detector and log conversion (D1 #187): positive peak / sample / average / RMS / negative
    peak / normal per resolution cell, then the level in dBm.

    Normal (Rosenfell, #616) shows the peak of a CW-like bucket; a noise-like bucket (its cells
    spread by more than ``normal_spread``) shows the maximum on even points and the minimum on odd
    ones, as the X-Series reference describes. With ``auto`` the detector follows the trace mode:
    max hold peak, min hold negative peak, otherwise normal (the X-Series preset rule); a detector
    chosen through :meth:`op_select` turns ``auto`` off."""

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
    """The video filter's bandwidth (D1 #187): auto follows the RBW; the manual value stretches the
    sweep the same way the old monolith did (its auto sweep time uses min(RBW, VBW)). Steps and
    limits as the IF filter's (#616); a ``vbw_max`` above the ladder is the wide-open setting."""

    refs = {"if_filter": ("specan_if", "instrument")}
    defaults = {
        "vid": 3.0e6,  # Hz, manual VBW (a setting such as $bw.vid)
        "vid_auto": True,  # the VBW follows the RBW (a setting)
        "if_filter": "",  # the specan_if it reads the RBW from
        "steps": "1-3-10",  # the filter steps: 1-3-10 | 10% (as the specan_if steps)
        "vbw_min": 1.0,  # Hz, the narrowest VBW
        "vbw_max": 3.0e6,  # Hz, the widest VBW (above 8 MHz: one more, wide-open setting, X-Series 50 MHz)
    }
    units = {"vbw_min": "Hz", "vbw_max": "Hz"}
    choices = {"steps": STEPS}

    def vbw(self) -> float:
        if bool(self.param("vid_auto")):
            return float(self.ref("if_filter").rbw())  # type: ignore[union-attr]
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


@component("specan_trace")
class SpecanTrace(Component):
    """The trace and scan engine (D1 #187): the analysis window and its settings couplings, the
    sweep timing in virtual time, the RF history, the trace modes (write / average / max / min hold), the
    markers, and the SCPI operations of the display. It reads the analog front end by its wiring:
    from the IF filter upstream to the input, the lines at the head of that chain and the stages'
    cascaded noise figure (the analytic evaluation of signal-chain.md 5.4)."""

    refs = {
        "input": ("specan_input", "instrument"),
        "lo": ("specan_lo", "instrument"),
        "if_filter": ("specan_if", "instrument"),
        "detector": ("specan_detector", "instrument"),
        "vbw": ("specan_vbw", "instrument"),
    }
    defaults = {
        "input": "",  # the specan_input (attenuation, overload)
        "lo": "",  # the specan_lo (tuning, reference error, sweep rate)
        "if_filter": "",  # the specan_if (RBW): the end of the analog front end's chain
        "detector": "",  # the specan_detector
        "vbw": "",  # the specan_vbw
        "max_hold_sweeps": 999,  # sweeps max or min hold keeps (older ones are dropped, with a warning)
        # A center the window cannot hold at the present span (#292): "slide" keeps the span and
        # moves the center inside the band; "span" keeps the center and narrows the span to fit.
        "center_fit": "slide",
        "hold_count": False,  # a single sweep in max/min hold takes the average/hold count of sweeps (X-Series), not one
        "marker_off_reply": "error",  # a read of an off marker: error (-221) | nan (9.91E+37, the X-Series)
        "delta_x": "absolute",  # X of a delta marker: absolute | offset (from the reference marker, the X-Series)
        "f_max": F_MAX,  # Hz, the highest frequency the window reaches (the model's frequency option, #616)
        "span_min": 10.0,  # Hz, the narrowest swept span (zero span is not modeled)
        "sweep_min": 1.0e-3,  # s, the shortest swept sweep time
        "sweep_max": 10.0e3,  # s, the longest swept sweep time (X-Series: 4000 s)
    }
    units = {
        "f_max": "Hz",
        "span_min": "Hz",
        "sweep_min": "S",
        "sweep_max": "S",
    }
    choices = {"marker_off_reply": ("error", "nan"), "delta_x": ("absolute", "offset")}
    dynamic = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._restart_ns = 0
        self._key: tuple[Any, ...] | None = None
        self._history: list[tuple[int, Signal]] = []  # RF_IN since the restart: (from, signal)
        self._cache: dict[tuple[int, int], np.ndarray] = {}
        self._held: np.ndarray | None = None
        self._held_upto = -1
        self._frozen: np.ndarray | None = None
        self._warned_hold = False

    # ---- the blocks ------------------------------------------------------------------------------

    def _block(self, key: str) -> Any:
        return self.ref(key)

    def _input(self) -> SpecanInput:
        return self._block("input")

    def _lo(self) -> SpecanLo:
        return self._block("lo")

    def _if(self) -> SpecanIf:
        return self._block("if_filter")

    def _detector(self) -> SpecanDetector:
        return self._block("detector")

    def _vbw(self) -> SpecanVbw:
        return self._block("vbw")

    def _st(self, path: str) -> Any:
        assert self.owner is not None
        return self.owner.get_state(path)

    def _set(self, path: str, value: Any) -> None:
        assert self.owner is not None
        self.owner.set_state(path, value)

    def _opt(self, path: str, default: Any) -> Any:
        """A setting the definition may leave out (the #274 marker group): its value, else the
        behaviour of an analyzer with one always-on normal marker."""
        assert self.owner is not None
        return self.owner.get_state(path) if path in self.owner.specs else default

    def rbw(self) -> float:
        return self._if().rbw()

    def vbw(self) -> float:
        return self._vbw().vbw()

    def auto_sweep(self) -> float:
        """The coupled sweep time: what the RBW/VBW need to settle over the span, and no faster
        than the LO can sweep it (``lo_rate``, #292)."""
        rbw = self.rbw()
        span = float(self._st("freq.span"))
        settle = 2.5 * span / (rbw * min(rbw, self.vbw()))
        return max(float(self.param("sweep_min")), settle, self._lo().sweep_floor(span))

    def sweep_time(self) -> float:
        return self.auto_sweep() if self._st("sweep.time_auto") else float(self._st("sweep.time"))

    def attenuation(self) -> float:
        return self._input().attenuation()

    def window(self) -> tuple[float, float]:
        c, s = float(self._st("freq.center")), float(self._st("freq.span"))
        return c - s / 2, c + s / 2

    def chain(self) -> list[Any]:
        """The analog front end's stages, input first: the IF filter and every ``RfStage`` found
        walking the wiring upstream from it."""
        assert self.owner is not None
        blocks: list[Any] = []
        block: Any = self._if()
        while isinstance(block, RfStage) and block not in blocks:
            blocks.append(block)
            src = self.owner.wiring.source(block.name, "in")
            if src is None:
                break
            block = self.owner.components[src[0]]
        return blocks[::-1]

    def noise_figure(self) -> float:
        """The receiver's noise figure the display's floor runs on, dB. The earlier way: the
        cascade of the stages behind the input block, at the center frequency, and the attenuation
        is added where the floor is computed — the display is referred to the input. When the
        input block declares its preamplifier's own figure (#954), its stage already carries the
        attenuator, and the whole chain's cascade is the input-referred figure."""
        center = float(self._st("freq.center"))
        with self.session.tuned(center):
            stages = [block.stage(center) for block in self.chain()]
        if stages and stages[0].noise_figure_db is not None:
            nf = rfcascade.cascade(stages).noise_figure_db
        else:
            behind = stages[1:]
            nf = rfcascade.cascade(behind).noise_figure_db if behind else None
        if nf is None:
            raise ScpiError(-300, "no stage of the analyzer's front end declares a noise figure")
        return nf

    def _cascade(self) -> RfStageSpec | None:
        """The chain's cascaded figures at the center frequency, or None when no stage declares an
        intercept — then the display draws no distortion products and no compression (#954)."""
        center = float(self._st("freq.center"))
        with self.session.tuned(center):
            stages = [block.stage(center) for block in self.chain()]
        spec = rfcascade.cascade(stages)
        return (
            spec
            if (math.isfinite(spec.iip2_dbm) or math.isfinite(spec.iip3_dbm) or math.isfinite(spec.p1db_dbm))
            else None
        )

    def settings(self) -> _Settings:
        start, stop = self.window()
        att = self.attenuation()
        # The floor's attenuation: 0 when the input block's stage is the whole receiver's figure
        # (it already carries the attenuator), the real one in the earlier way.
        center = float(self._st("freq.center"))
        head_nf = self._input().stage(center).noise_figure_db
        floor_att = 0.0 if head_nf is not None else att
        return _Settings(
            start,
            stop,
            int(self._st("sweep.points")),
            self.rbw(),
            self.sweep_time(),
            att,
            self._detector().in_use(),
            self.noise_figure(),
            floor_att,
            self._cascade(),
        )

    # ---- restarts and signal history ------------------------------------------------------------

    def _trace_key(self) -> tuple[Any, ...]:
        return (
            *self.settings().key(),
            self._st("trace.type"),
            int(self._st("aver.count")),
            self._lo().error(),
        )

    def restart(self) -> None:
        now = self.session.now_ns
        self._restart_ns = now
        current = self._history[-1][1] if self._history else self._input().signal()
        self._history = [(now, current)]
        self._cache.clear()
        self._held = None
        self._held_upto = -1
        self._warned_hold = False

    def reset(self) -> None:
        self._frozen = None

    def sync(self) -> None:
        if not self.session.signal_changed(self):
            return
        self._lo().refresh()
        key = self._trace_key()
        if key != self._key:
            self._key = key
            self._history = [(self.session.now_ns, self._input().signal())]
            self.restart()
            return
        sig = self._input().signal()
        last = self._history[-1][1] if self._history else None
        if last is None or not _same(last, sig):
            self._history.append((self.session.now_ns, sig))

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

    def _front_end_lines(
        self, f: np.ndarray, p_mw: np.ndarray, st: _Settings
    ) -> tuple[np.ndarray, np.ndarray, str | None]:
        """The lines the display draws, from the ones RF_IN carries (signal-chain.md 5.2, #954):
        the input lines scaled by the response's flatness and the chain's compression, the
        distortion products the chain's intercepts make of them, the image and spur responses,
        the residuals and the oscillator's feedthrough. Every derived line is display-referred —
        they are computed from the levels the mixer sees, so the flatness rides inside them —
        and the compression applies to the input lines' drive. Without any declared figure the
        input is returned unchanged."""
        input_block = self._input()
        mixer = self._mixer_block()
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

        # The distortion products of the chain's intercepts: equivalent input lines (#954).
        if st.cascade is not None and (
            math.isfinite(st.cascade.iip2_dbm) or math.isfinite(st.cascade.iip3_dbm)
        ):
            products = rfcascade.products(f, 10 * np.log10(np.maximum(p_drive, 1e-300)), st.cascade)
            if products.freq_hz.size:
                out_f.append(products.freq_hz)
                out_p.append(10 ** (products.power_dbm / 10))
            if products.dropped:
                approx = "too many lines for one trace: the distortion products are approximate, only the strongest 64 lines take part"

        # Compression: the chain's gain is down by the P1dB model at each line's drive level.
        if st.cascade is not None and math.isfinite(st.cascade.p1db_dbm):
            over = 10 * np.log10(np.maximum(p_mw, 1e-300)) - st.cascade.p1db_dbm
            out_p[0] = out_p[0] * np.asarray(rfcascade.compress_ratio(over)) ** 2

        if mixer is not None:
            if_hz = float(self._if().param("if_hz"))
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
            # The declared spur responses: m·f_rf − n·f_lo and m·f_rf + n·f_lo land on the IF
            # when the receiver tunes to (m·f_rf + (n−1)·if)/n and ((n+1)·if − m·f_rf)/n.
            for row in mixer.param("spurs") or ():
                m_, n_ = float(row[0]), float(row[1])
                for f_spur in (m_ * f + (n_ - 1.0) * if_hz, (n_ + 1.0) * if_hz - m_ * f):
                    lands = (f_spur > 0) & (f_spur / n_ >= start) & (f_spur / n_ <= stop)
                    if lands.any():
                        out_f.append(f_spur[lands] / n_)
                        out_p.append(p_drive[lands] * 10 ** (float(row[2]) / 10))
            # The residuals and the oscillator's feedthrough: lines with no input at all.
            for row in mixer.param("residuals") or ():
                shown = float(self._lo().display(np.array([float(row[0])]))[0])
                if start <= shown <= stop:
                    out_f.append(np.array([float(row[0])]))
                    out_p.append(np.array([10 ** (float(row[1]) / 10)]))
            feedthrough = mixer.param("lo_feedthrough")
            if feedthrough is not None and start <= 0.0 <= stop:
                out_f.append(np.array([0.0]))
                out_p.append(np.array([10 ** (float(feedthrough) / 10)]))
        return np.concatenate(out_f), np.concatenate(out_p), approx

    def _mixer_block(self) -> Any:
        """The specan_mixer on the wiring between the input block and the IF filter, if any."""
        for block in self.chain():
            if isinstance(block, SpecanMixer):
                return block
        return None

    def _lines_in_window(self, sig: Signal, st: _Settings) -> tuple[np.ndarray, np.ndarray, str | None]:
        m = sig.merged()
        r = float(self._input().param("z_in"))
        pos = m.freq > 0
        f = m.freq[pos]
        p_mw = np.abs(m.amp[pos]) ** 2 / (2 * r) * 1e3
        dc = m.dc + float(np.real(m.amp[~pos]).sum())
        if dc:
            f = np.concatenate([[0.0], f])
            p_mw = np.concatenate([[dc * dc / r * 1e3], p_mw])
        f, p_mw, approx = self._front_end_lines(f, p_mw, st)
        # The analyzer's frequency axis runs off its reference: a true f displays at f / (1 + ε).
        f_disp = self._lo().display(f)
        margin = 6 * st.rbw
        sel = (f_disp >= st.start - margin) & (f_disp <= st.stop + margin)
        f_disp, p_mw = f_disp[sel], p_mw[sel]
        if approx is None and f_disp.size > MAX_LINES_IN_WINDOW:
            keep = np.argsort(p_mw)[-MAX_LINES_IN_WINDOW:]
            f_disp, p_mw = f_disp[keep], p_mw[keep]
            approx = "too many lines for one trace: the weakest were dropped"
        return f_disp, p_mw, approx

    def truncation(self, sig: Signal, st: _Settings) -> str | None:
        """Why the trace cannot be drawn completely, or None."""
        notes = [n for n in sig.notes if n.kind == "truncated"]
        if notes and sig.freq.size > 1:
            f = np.sort(sig.freq)
            f_next = self._lo().display(f[-1] + (f[-1] - f[-2]))
            if f_next <= st.stop + 3 * st.rbw:
                return f"too many lines for one trace: the weakest were dropped (lines above {f[-1]:.6g} Hz are not modeled)"
        _, _, why = self._lines_in_window(sig, st)
        return why

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
                line_mw += (p[None, i : i + 512] * self._if().response(d, st.rbw, bucket, st.detector)).sum(
                    axis=1
                )
        # The oscillator's phase noise skirts every converted line: at each point the line's
        # power times L(|f_point − f_line|) over the RBW's noise bandwidth, as noise (#954).
        # Within half the RBW of its line the sideband power is the line's own power — it does
        # not add again.
        skirt_mw = None
        if self._lo().param("phase_noise") and f.size:
            skirt_mw = np.zeros(st.points)
            for i in range(0, f.size, 512):
                d = np.abs(x[:, None] - f[None, i : i + 512])
                density = self._lo().phase_noise_density(d)
                density = np.where(d > st.rbw / 2, density, 0.0)
                skirt_mw += (p[None, i : i + 512] * density).sum(axis=1)
            skirt_mw *= NBW_FACTOR * st.rbw
        floor_mw = self._if().noise_floor_mw(st.floor_att, st.nf)
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
        if skirt_mw is not None:
            # The same per-resolution-cell draws, on their own stream: the thermal noise's values
            # do not move (#954).
            pkey = stream_key(self.session.seed, self.owner.name, "specan", "phase", st.key())
            v = (
                v
                + (gaussian(pkey, idx) + 1j * gaussian(pkey ^ 0x2545F491, idx))
                * np.sqrt(skirt_mw / 2)[:, None]
            )
        power = np.abs(v) ** 2
        trace = self._detector().detect(v, power, st.detector)
        if len(self._cache) > 4096:
            self._cache.clear()
        self._cache[(s, version)] = trace
        return trace

    def current_sweep(self, st: _Settings) -> int:
        return max(0, int((self.session.now_ns - self._restart_ns) * 1e-9 / st.sweep + 1e-9))

    def trace(self) -> tuple[np.ndarray, np.ndarray]:
        """Frequencies and levels (dBm) of the trace as the display shows it now."""
        self.session.sync()
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
        if mode in ("MAXHold", "MINHold"):
            pick = np.maximum if mode == "MAXHold" else np.minimum
            limit = int(self.param("max_hold_sweeps"))
            first = max(0, s_now - limit + 1)
            if first > 0 and not self._warned_hold:
                self._warned_hold = True
                assert self.owner is not None
                self.owner.push_error(
                    -221, f"{'max' if mode == 'MAXHold' else 'min'} hold keeps the newest {limit} sweeps"
                )
            if self._held is None or self._held_upto < first - 1 or self._held_upto > s_now:
                self._held, self._held_upto = None, first - 1
            for s in range(self._held_upto + 1, s_now + 1):
                tr = self._sweep(s, st)
                self._held = tr.copy() if self._held is None else pick(self._held, tr)
                self._held_upto = s
            assert self._held is not None
            return self._held
        return self._sweep(s_now, st)

    # ---- overload -------------------------------------------------------------------------------

    def overload(self) -> bool:
        return self._input().overload()

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
                "reference": self._lo().in_use(),
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
        f_max, span_min = float(self.param("f_max")), float(self.param("span_min"))
        if not span_min <= span <= f_max or not 0.0 <= center <= f_max:
            raise ScpiError(-222)
        if str(self.param("center_fit")) == "span":
            # keep the center, narrow the span to what the band leaves around it
            span = max(span_min, min(span, 2 * center, 2 * (f_max - center)))
        center = min(max(center, span / 2), f_max - span / 2)  # the window slides, no error
        self._set("freq.span", span)
        self._set("freq.center", center)

    @op("frequency")
    def op_frequency(
        self, query: bool = False, what: str = "center", value: float | None = None, **_: Any
    ) -> OpResult:
        c, s = float(self._st("freq.center")), float(self._st("freq.span"))
        start, stop = c - s / 2, c + s / 2
        if query:
            return OpResult(
                self.reply_number({"center": c, "span": s, "start": start, "stop": stop}[what], 15)
            )
        assert value is not None
        if what in ("start", "stop") and not 0.0 <= value <= float(self.param("f_max")):
            raise ScpiError(-222)  # an edge past the tuning range (#616)
        if what == "center":
            self._window_set(value, s)
        elif what == "span":
            self._window_set(c, value)
        elif what == "start":
            self._window_set((value + stop) / 2, stop - value)
        else:
            self._window_set((start + value) / 2, value - start)
        return OpResult()

    @op("coupled_sweep")
    def op_coupled_sweep(self, query: bool = False, value: float | None = None, **_: Any) -> OpResult:
        """The sweep time: a manual value turns the automatic coupling off."""
        if query:
            return OpResult(self.reply_number(self.sweep_time(), 10))
        assert value is not None
        if not float(self.param("sweep_min")) <= value <= float(self.param("sweep_max")):
            raise ScpiError(-222)
        self._set("sweep.time", value)
        self._set("sweep.time_auto", False)
        return OpResult()

    @op("status")
    def op_status(self, what: str = "uncal", **_: Any) -> OpResult:
        return OpResult(V.format_bool(self.sweep_time() < self.auto_sweep() * (1 - 1e-9)))

    @op("trace_data")
    def op_trace_data(self, name: str | None = None, **_: Any) -> OpResult:
        if name and name.upper() not in ("TRACE1", "TRAC1", "1"):
            raise ScpiError(-224, f"unknown trace {name!r}")
        _x, y = self.trace()
        why = self.truncation(self._signal_at(self.session.now_ns), self.settings())
        if why:
            assert self.owner is not None
            self.owner.push_error(-221, why)
        return OpResult(b",".join(self.reply_number(v, 7) for v in y))

    @op("trace_clear")
    def op_trace_clear(self, **_: Any) -> OpResult:
        self.restart()
        self._frozen = None
        return OpResult()

    @op("initiate")
    def op_initiate(self, **_: Any) -> OpResult:
        """Single sweep(s): the averaging count in AVERage mode (and in the hold modes with
        ``hold_count``), else one; takes their sweep time."""
        st = self.settings()
        counted = ("AVERage", "MAXHold", "MINHold") if V.flag(self.param("hold_count")) else ("AVERage",)
        n = int(self._st("aver.count")) if self._st("trace.type") in counted else 1
        self.session.sync()
        start_sweep = self.current_sweep(st)
        traces = [self._sweep(s, st) for s in range(start_sweep, start_sweep + n)]
        mode = self._st("trace.type")
        if mode == "AVERage":
            self._frozen = np.mean(traces, axis=0)
        elif mode == "MAXHold":
            self._frozen = np.max(traces, axis=0)
        elif mode == "MINHold":
            self._frozen = np.min(traces, axis=0)
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

    watches = ("marker.mode", "marker.enabled")

    # The marker group (#274), on settings the definition may declare: marker.enabled (on/off;
    # absent: always on), marker.mode (POSition | DELTa), marker.ref_x / marker.ref_y (the fixed
    # reference a delta marker reads against, set when DELTa is chosen), marker.count (the counter
    # switch; absent: always counting) and marker.search (PARameter | MAXimum: whether peak search
    # applies the threshold and excursion). X-Series markers (#616): marker.mode may offer OFF
    # (the marker is off, as with marker.enabled false); ``marker_off_reply`` "nan" answers a read
    # of an off marker with 9.91E+37 instead of -221; ``delta_x`` "offset" makes X the offset from
    # the reference marker in delta mode.

    def _mode(self) -> str:
        return str(self._opt("marker.mode", "POSition")).upper()

    def _marker_on(self) -> bool:
        return bool(self._opt("marker.enabled", True)) and not self._mode().startswith("OFF")

    def _turn_on(self) -> None:
        assert self.owner is not None  # a component's settings change through its instrument
        if "marker.enabled" in self.owner.specs and not self._st("marker.enabled"):
            self._set("marker.enabled", True)
        if self._mode().startswith("OFF"):
            self._set("marker.mode", "POSition")

    def _delta(self) -> bool:
        return self._marker_on() and self._mode().startswith("DELT")

    def on_state(self, key: str | None, path: str, old: Any, new: Any) -> None:
        if (
            path == "marker.mode"
            and str(old).upper().startswith("OFF")
            and not str(new).upper().startswith("OFF")
        ):
            # A marker turned on through its mode starts at the center of the screen.
            self._set("marker.x", float(self._st("freq.center")))
        if path == "marker.mode" and str(new).upper().startswith("DELT") and new != old:
            # DELTa fixes a reference marker where the marker is now; the marker itself moves on.
            # A marker that is off turns on first — it starts at the screen center, so the
            # reference is fixed at the same point and the delta pair starts at 0/0, not at the
            # stale position the marker had while it was off (issue #349).
            self._turn_on()
            x, y = self.trace()
            i = int(np.argmin(np.abs(x - float(self._st("marker.x")))))
            self._set("marker.ref_x", float(x[i]))
            self._set("marker.ref_y", float(y[i]))
        if path == "marker.enabled" and new and not old:
            # A marker turned on starts at the center of the screen, as the analyzers do.
            self._set("marker.x", float(self._st("freq.center")))

    @op("marker")
    def op_marker(
        self, query: bool = False, what: str = "x", value: float | None = None, **_: Any
    ) -> OpResult:
        x, y = self.trace()
        mx = float(self._st("marker.x"))
        i = int(np.argmin(np.abs(x - mx)))
        reads = query if what == "x" else what in ("y", "count", "dx")
        if reads and not self._marker_on():
            if self.choice("marker_off_reply") == "nan":
                return OpResult(self.reply_number(9.91e37, 7))  # SCPI "not a number"
            raise ScpiError(-221, "the marker is off")
        offset = self._delta() and self.choice("delta_x") == "offset"
        if what == "x":
            if query:
                ref = float(self._st("marker.ref_x")) if offset else 0.0
                return OpResult(self.reply_number(float(x[i]) - ref, 15))
            assert value is not None
            if offset:
                value += float(self._st("marker.ref_x"))  # a delta marker is placed by its offset
            j = int(np.argmin(np.abs(x - value)))
            self._turn_on()
            self._set("marker.x", float(x[j]))
            return OpResult()
        if what == "y":
            level = float(y[i])
            if self._delta():
                level -= float(self._st("marker.ref_y"))  # dB from the fixed reference
            return OpResult(self.reply_number(level, 7))
        if what == "dx":
            ref = float(self._st("marker.ref_x")) if self._delta() else float(x[i])
            return OpResult(self.reply_number(float(x[i]) - ref, 15))
        if what == "count":
            if not bool(self._opt("marker.count", True)):
                return OpResult(self.reply_number(0.0, 12))  # the counter is off
            return OpResult(self.reply_number(self._count(float(x[i])), 12))
        best = int(np.argmin(y)) if what == "min" else self._search(what, x, y, i)
        self._turn_on()
        self._set("marker.x", float(x[best]))
        return OpResult()

    def _search(self, what: str, x: np.ndarray, y: np.ndarray, i: int) -> int:
        """Peak search: max, next (by level), left / right (the nearest peak that way)."""
        if what == "max" and str(self._opt("marker.search", "PARameter")).upper().startswith("MAX"):
            return int(np.argmax(y))  # MAXimum mode: the highest point, criteria ignored
        peaks = self._peaks(y)
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

    @op("marker_off")
    def op_marker_off(self, **_: Any) -> OpResult:
        """:CALCulate:MARKer:AOFF — every marker off (and back to normal mode)."""
        assert self.owner is not None  # an SCPI op runs on a live instrument
        if "marker.enabled" in self.owner.specs:
            self._set("marker.enabled", False)
        if "marker.mode" in self.owner.specs:
            options = self.owner.specs["marker.mode"].options or []
            self._set("marker.mode", "OFF" if "OFF" in options else "POSition")
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


__all__ = [
    "F_MAX",
    "SpecanDetector",
    "SpecanIf",
    "SpecanInput",
    "SpecanLo",
    "SpecanMixer",
    "SpecanTrace",
    "SpecanVbw",
    "line_response",
    "snap_rbw",
]
