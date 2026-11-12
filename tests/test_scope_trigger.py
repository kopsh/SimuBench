"""The scope's trigger path (issue 137, instrument-authoring.md 8.4, G5): the trigger is detected on
the analog signal with its own coupling and noise, interpolated between samples, and a normal or
single acquisition waits in virtual time for its edge."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from simubench.api import open_bench
from simubench.engine.signal import Signal, pulse_train, sine

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "models" / "contract-float-scope" / "bench.yaml"
SDS = ROOT / "tests" / "models" / "siglent-sds2104xplus" / "bench.yaml"
VALIDATION = [ROOT / "validation" / "instruments"]


def edge_times(rec, level: float = 0.0) -> float:
    """The rising crossing next to the trigger, in the record's own time (s after the trigger)."""
    v, i = rec.volts, rec.trigger_index
    k = max(j for j in range(i - 3, i + 4) if v[j - 1] < level <= v[j])
    frac = (level - v[k - 1]) / (v[k] - v[k - 1])
    return rec.origin + (k - 1 + frac) * rec.dt_s


def acquisitions(jitter: float, n: int = 120) -> np.ndarray:
    s = open_bench(CONTRACT)
    try:
        inst = s.instruments["scope"]
        acq = inst.components["acq"]
        s.set_parameter("scope", "adc", "bits", 0)  # no quantization: the samples are the signal
        s.set_parameter("scope", "acq", "trigger_jitter_s", jitter)
        times = []
        s.advance(0.01)  # the generator's output is on
        for _ in range(n):
            s.advance(0.0012345)  # the sample clock's phase against the signal moves on
            acq.acquire_now()
            times.append(edge_times(acq.records[1]))
        return np.array(times)
    finally:
        s.close()


def test_the_record_origin_carries_the_trigger_fraction_and_its_jitter():
    dt = 1e-3 / 10000  # 100 us/div over 10 000 points
    exact = acquisitions(0.0)
    # Without jitter the edge sits at the trigger to far better than a sample: the fraction of a
    # sample is in the origin (before G5 the edges scattered over a whole sample, ~dt/sqrt(12)).
    assert np.abs(exact).max() < dt / 100
    jittered = acquisitions(20e-9)
    assert np.std(jittered) == pytest.approx(20e-9, rel=0.2)


def test_a_single_acquisition_on_a_1_hz_signal_completes_at_the_next_edge():
    s = open_bench(SDS, definition_dirs=VALIDATION)
    try:
        s.configure("awg", "ch1.frequency", 1.0)
        for c in (":TIMebase:SCALe 1E-3", ":TRIGger:EDGE:LEVel 0", ":TRIGger:MODE SINGle"):
            s.write("scope", c.encode() + b"\n")
        start = s.now_ns
        acq = s.instruments["scope"].components["acq"]
        assert acq.pending.trigger_ns is not None  # found, though a second of virtual time away
        # The edge from the bench's own signal: the 1 Hz sine's next rising zero crossing.
        net = s.net_signal(s.port_net["scope.CH1"])
        grid = np.arange(0, 2.0, 1e-4)
        v = net.sample_rel(start, grid)
        i = int(np.nonzero((v[:-1] < 0) & (v[1:] >= 0) & (grid[1:] > 5e-3))[0][0])
        edge = start + (grid[i] - v[i] / (v[i + 1] - v[i]) * 1e-4) * 1e9
        assert acq.pending.trigger_ns == pytest.approx(edge, abs=1e3)  # to a microsecond
        assert acq.pending.trigger_ns - start > 0.1e9
        s.write("scope", b"*OPC?\n")
        assert s.read("scope").strip() == b"1"
        s.write("scope", b":TRIGger:STATus?\n")
        assert s.read("scope").strip() == b"Stop"
        # Done once the record after the trigger is full (5 div of 1 ms) and the overhead (1 ms).
        assert acq.records[1].triggered and acq.records[1].trigger_ns == pytest.approx(edge, abs=1e3)
        assert s.now_ns - edge == pytest.approx(6e6, abs=1e4)
    finally:
        s.close()


def noisy_retriggers(noise_reject: bool) -> float:
    """Time from one trigger to the next on a slow edge with trigger-path noise. The writes are
    actions (``set_parameter``), so the bench pins ``idle: off``: what it asserts is the edge's
    own math, not the real time the bench took to build (D47 idle belongs between commands)."""
    s = open_bench(CONTRACT, idle="off")
    try:
        inst = s.instruments["scope"]
        trig = inst.components["trigger"]
        s.set_parameter("scope", "trigger", "noise_rms", 0.05)
        s.set_parameter("scope", "trigger", "hysteresis_divs", 0.01)
        s.set_parameter("scope", "trigger", "noise_reject", noise_reject)
        sig = inst.components["acq"]._display_signal(1)
        t0, dt = s.now_ns, 1e-7
        first = trig.search(sig, t0, dt, 100, 10000, 1, 0.5)
        trig.last_ns = t0 + round(first.t_s * 1e9)
        second = trig.search(sig, t0, dt, 100, 10000, 1, 0.5)
        return second.t_s - first.t_s
    finally:
        s.close()


def test_noise_reject_stops_false_triggers_on_a_noisy_slow_edge():
    assert noisy_retriggers(False) < 1e-4  # the same edge again, within its noise
    assert noisy_retriggers(True) == pytest.approx(1e-3, rel=0.05)  # the next period's edge


def test_the_trigger_source_flow():
    """What the trigger fires on: the coupling filters reach the trigger path only, the LINE
    source fires on the mains and EXternal is its own kind, and the holdoff skips the edges
    within it."""
    s = open_bench(CONTRACT)
    try:
        trig = s.instruments["scope"].components["trigger"]
        # -- the coupling filters reach the trigger only (was test_coupling_filters_reach_the_trigger_only)
        s.set_parameter(
            "scope", "trigger", "filters", {"AC": [["highpass", 8.0]], "HFREJect": [["lowpass", 500e3]]}
        )
        sig = Signal.constant(2.0) + sine(1000.0, 0.5)
        s.set_parameter("scope", "trigger", "level", 1.0)  # above the sine alone, crossed with its DC
        s.set_parameter("scope", "trigger", "coupling", "DC")
        assert trig.search(sig, 0, 1e-6, 10, 1000, None, 0.5) is None  # 1.5..2.5 V never falls below 1 V
        s.set_parameter("scope", "trigger", "coupling", "AC")
        s.set_parameter("scope", "trigger", "level", 0.0)
        hit = trig.search(sig, 0, 1e-6, 10, 1000, None, 0.5)
        assert hit is not None  # without the DC the sine crosses 0 V
        fast = sine(5e6, 1.0)
        s.set_parameter("scope", "trigger", "coupling", "HFREJect")
        s.set_parameter("scope", "trigger", "level", 0.5)
        assert trig.search(fast, 0, 1e-9, 10, 1000, None, 0.5) is None  # 5 MHz attenuated tenfold
        # -- line and external sources (was test_line_and_external_sources; the LINE search is the
        # mains' own timing, the level and the coupling filters do not touch it)
        s.set_parameter("scope", "trigger", "source", "LINE")
        hit = trig.search(Signal.zero(), 0, 1e-5, 10, 1000, None, 0.5)
        assert hit.t_s == pytest.approx(0.02)  # the next 50 Hz zero crossing after the pre-trigger
        s.set_parameter("scope", "trigger", "source", "EX")
        s.set_parameter("scope", "trigger", "level", 0.2)
        assert trig.source_kind() == "external"
        # -- the holdoff skips edges within it (was test_holdoff_skips_edges_within_it)
        s.set_parameter("scope", "trigger", "coupling", "DC")  # bridge: the filters act left HFREJect
        sig = sine(1000.0, 1.0)
        s.set_parameter("scope", "trigger", "source", "C1")
        s.set_parameter("scope", "trigger", "level", 0.0)
        s.set_parameter("scope", "trigger", "slope", "POSitive")
        first = trig.search(sig, 0, 1e-6, 10, 1000, 1, 0.5)
        assert first.t_s == pytest.approx(1e-3, abs=1e-8)
        trig.last_ns = round(first.t_s * 1e9)
        s.set_parameter("scope", "trigger", "holdoff", 2.5e-3)
        second = trig.search(sig, 0, 1e-6, 10, 1000, 1, 0.5, 1.0)
        assert second.t_s == pytest.approx(4e-3, abs=1e-8)  # 2 and 3 ms are within the holdoff
    finally:
        s.close()


# -- the pulse-width (glitch) trigger (#956) ------------------------------------------------------

EDGE_S = 1e-6  # 10-90 % edges of the test pulses: ten record samples at 100 ns
GLITCH_AT, GLITCH_W = 8.7e-3, 50e-6  # one narrow pulse every 10 ms, in a low half of the 1 kHz train


def glitchy_train() -> Signal:
    """A 1 kHz, 50 % pulse train (±1 V) with one 50 us pulse every 10 ms, in the low half that
    runs from 8.5 to 9 ms (its lows: 200 us before it, 250 us after it)."""
    base = pulse_train(1e3, 1.0, 0.5, 5e6, lead_s=EDGE_S, trail_s=EDGE_S)
    period = 10e-3
    glitch = pulse_train(
        100.0, 1.0, GLITCH_W / period, 2e6, phase_rad=-2 * np.pi * GLITCH_AT / period,
        lead_s=EDGE_S, trail_s=EDGE_S,
    ) + Signal.constant(1.0)  # fmt: skip
    return base + glitch


def crossing(sig: Signal, lo: float, hi: float, rising: bool) -> float:
    """The signal's own level-0 crossing in (lo, hi], bisected on the continuous signal."""
    while hi - lo > 1e-13:
        mid = (lo + hi) / 2
        if (float(sig.sample_rel(0, np.array([mid]))[0]) >= 0.0) == rising:
            hi = mid
        else:
            lo = mid
    return hi


def test_the_pulse_width_trigger_flow():
    """The glitch mode on the trigger path: the pulse qualifies by its width between the rising and
    the falling crossing, the trigger is at the trailing edge, and the polarity picks which way
    round a pulse is."""
    s = open_bench(CONTRACT)
    try:
        trig = s.instruments["scope"].components["trigger"]
        for key, value in {"source": "C1", "level": 0.0, "mode": "glitch", "polarity": "positive"}.items():
            s.set_parameter("scope", "trigger", key, value)
        sig, dt = glitchy_train(), 1e-7

        def hit(qualifier: str, low: float = 0.0, high: float = 10.0, on: Signal = sig):
            s.set_parameter("scope", "trigger", "qualifier", qualifier)
            s.set_parameter("scope", "trigger", "width_low", low)
            s.set_parameter("scope", "trigger", "width_high", high)
            return trig.search(on, 0, dt, 100, 100_000, 1, 0.5)

        # -- less than 100 us: only the narrow pulse, at its trailing edge to a tenth of a sample
        less = hit("less", high=100e-6)
        end = crossing(
            sig, GLITCH_AT + GLITCH_W - 2 * EDGE_S, GLITCH_AT + GLITCH_W + 2 * EDGE_S, rising=False
        )
        assert less is not None and abs(less.t_s - end) < dt / 10
        assert less.index == int(np.ceil(less.t_s / dt - 1e-9))  # the record grid's sample at or after it
        # -- a range of 40-60 us takes the same pulse; the 500 us pulses before it are outside it
        ranged = hit("range", low=40e-6, high=60e-6)
        assert ranged is not None and abs(ranged.t_s - end) < dt / 10
        # -- reversed polarity: no low stretch is narrower than 100 us (200 us, 250 us, 500 us)
        s.set_parameter("scope", "trigger", "polarity", "negative")
        assert hit("less", high=100e-6) is None
        assert hit("range", low=40e-6, high=60e-6) is None
        # -- greater than 600 us: a 70 % train's 700 us highs qualify, at their trailing edge
        # (the first full pulse after the pre-trigger: it rose at 1 ms, it falls at 1.7 ms)
        wide = pulse_train(1e3, 1.0, 0.7, 5e6, lead_s=EDGE_S, trail_s=EDGE_S)
        s.set_parameter("scope", "trigger", "polarity", "positive")
        greater = hit("greater", low=600e-6, on=wide)
        assert (
            greater is not None
            and abs(greater.t_s - crossing(wide, 1.69e-3, 1.71e-3, rising=False)) < dt / 10
        )
        # its 300 us lows do not, nor do the 50 % train's 500 us pulses
        s.set_parameter("scope", "trigger", "polarity", "negative")
        assert hit("greater", low=600e-6, on=wide) is None
        assert hit("greater", low=600e-6) is None
    finally:
        s.close()


def test_an_unmet_pulse_width_waits_in_normal_and_times_out_in_auto():
    """DSO-X 2024A, a 1 kHz square at 5 % duty (50 us high, 950 us low) and a negative pulse
    narrower than 100 us, which never comes: a single acquisition stays armed and *OPC? does not
    answer; the auto sweep gives up after its auto timeout with an untriggered record. The
    positive pulse of the same width triggers at its trailing edge."""
    s = open_bench(ROOT / "tests" / "models" / "keysight-dsox2024a" / "bench.yaml")
    try:
        s.write("awg", b"C1:BSWV WVTP,SQUARE,FRQ,1000,DUTY,5\n")
        acq = s.instruments["scope"].components["acq"]
        s.write(
            "scope",
            b":CHANnel1:PROBe 1;:TIMebase:SCALe 2E-4;:TRIGger:MODE GLITch;:TRIGger:GLITch:QUALifier LESSthan;"
            b":TRIGger:GLITch:LESSthan 1E-4;:TRIGger:GLITch:POLarity NEGative;:TRIGger:SWEep NORMal;:SINGle\n",
        )
        assert acq.pending.waiting() and acq.pending.trigger_ns is None
        s.write("scope", b"*OPC?\n")
        assert s.read("scope") is None  # still waiting for its pulse
        s.write("scope", b":STOP;:TRIGger:SWEep AUTO;:DIGitize CHANnel1\n")
        s.write("scope", b"*OPC?\n")
        assert s.read("scope").strip() == b"1"
        assert not acq.records[1].triggered
        s.write("scope", b":TRIGger:GLITch:POLarity POSitive;:DIGitize CHANnel1\n")
        s.write("scope", b":MEASure:TEDGe? +1,CHANnel1\n")
        rise = float(s.read("scope"))
        assert acq.records[1].triggered and rise == pytest.approx(-50e-6, abs=1e-6)
    finally:
        s.close()
