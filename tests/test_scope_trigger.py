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
DSOX = ROOT / "tests" / "models" / "keysight-dsox2024a" / "bench.yaml"
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


def pulses_with_a_glitch() -> Signal:
    """A 1 kHz, 50 % pulse train of +-1 V in which, every 10 ms, one gap carries a 50 us pulse
    (0.70 to 0.75 ms) and another is filled (5.4 to 6.1 ms: one pulse of 1.5 ms, 5.0 to 6.5 ms)."""
    f_max = 40e3
    narrow = pulse_train(100.0, 1.0, 0.005, f_max, phase_rad=-2 * np.pi * 0.07)
    wide = pulse_train(100.0, 1.0, 0.07, f_max, phase_rad=-2 * np.pi * 0.54)
    return pulse_train(1000.0, 1.0, 0.5, f_max) + narrow + wide + Signal.constant(2.0)


def crossing(sig: Signal, near_s: float, rising: bool) -> float:
    """The signal's own crossing of 0 V next to ``near_s`` (within 20 us), by bisection."""
    lo, hi = near_s - 20e-6, near_s + 20e-6
    for _ in range(60):
        mid = (lo + hi) / 2
        if (float(sig.sample_rel(0, np.array([mid]))[0]) >= 0.0) == rising:
            hi = mid
        else:
            lo = mid
    return hi


def test_the_glitch_trigger_flow():
    """Pulse-width trigger (#956): among the pulses of a train only the one whose width meets the
    qualifier triggers — less and range at the edge that ends it, greater once it has lasted the
    time — and the other polarity's pulses do not."""
    s = open_bench(CONTRACT, idle="off")
    try:
        trig = s.instruments["scope"].components["trigger"]
        sig, dt = pulses_with_a_glitch(), 1e-6
        for key, value in (("mode", "glitch"), ("source", "C1"), ("level", 0.0), ("polarity", "positive")):
            s.set_parameter("scope", "trigger", key, value)

        def search(qualifier: str, low: float, high: float, horizon: float | None = None):
            s.set_parameter("scope", "trigger", "qualifier", qualifier)
            s.set_parameter("scope", "trigger", "width_low", low)
            s.set_parameter("scope", "trigger", "width_high", high)
            return trig.search(sig, 0, dt, 10, 1000, 1, 0.5, horizon)

        end = crossing(sig, 0.75e-3, rising=False)  # the edge that ends the 50 us pulse
        # -- narrower than 100 us: the 50 us pulse alone (the 500 us pulse before it does not)
        less = search("less", 20e-9, 100e-6)
        assert abs(less.t_s - end) < dt / 10
        assert less.index == int(np.ceil(end / dt))
        # -- 40 to 60 us: the same pulse; 60 to 400 us: no pulse of the train
        within = search("range", 40e-6, 60e-6)
        assert abs(within.t_s - end) < dt / 10
        assert search("range", 60e-6, 400e-6, 0.03) is None
        # -- wider than 600 us: the 1.5 ms pulse, at the instant it has lasted 600 us
        begin = crossing(sig, 5.0e-3, rising=True)
        greater = search("greater", 600e-6, 30e-9, 0.03)
        assert abs(greater.t_s - (begin + 600e-6)) < dt / 10
        # -- the holdoff is the edge trigger's: after a trigger, the next period's 50 us pulse
        trig.last_ns = round(less.t_s * 1e9)
        s.set_parameter("scope", "trigger", "holdoff", 1e-3)
        again = search("less", 20e-9, 100e-6, 0.03)
        assert abs(again.t_s - (end + 10e-3)) < dt / 10
        trig.last_ns = None
        # -- the other polarity: the gaps are 200, 250 and 500 us wide, none under 100 us or 40-60 us
        s.set_parameter("scope", "trigger", "polarity", "negative")
        assert search("less", 20e-9, 100e-6, 0.03) is None
        assert search("range", 40e-6, 60e-6, 0.03) is None
        gap = search("range", 180e-6, 220e-6, 0.03)  # the 200 us gap before the 50 us pulse
        assert abs(gap.t_s - crossing(sig, 0.70e-3, rising=True)) < dt / 10
    finally:
        s.close()


def test_a_glitch_condition_never_met_waits_in_normal_and_times_out_in_auto():
    """DSO-X 2024A on a 1 kHz sine (500 us pulses), pulse width under 100 us: a NORMal single
    acquisition stays pending, an AUTO one gives its record untriggered after the auto timeout."""
    s = open_bench(DSOX, idle="off")
    try:
        acq = s.instruments["scope"].components["acq"]
        s.write("scope", b":TIMebase:SCALe 200E-6;:TRIGger:MODE GLITch;:TRIGger:GLITch:LESSthan 100E-6\n")
        s.write("scope", b":TRIGger:SWEep NORMal;:SINGle\n")
        s.advance(1.0)
        assert acq.pending.waiting()  # a second on, still no pulse under 100 us
        assert 1 not in acq.records
        s.write("scope", b":TRIGger:GLITch:LESSthan 600E-6\n")  # now the sine's 500 us pulses qualify
        s.write("scope", b"*OPC?\n")
        assert s.read("scope").strip() == b"1"
        s.write("scope", b":TER?\n")
        assert s.read("scope").strip() == b"1"  # it triggered
        rec = acq.records[1]
        assert rec.triggered
        # At the falling zero crossing (the 10:1 probe setting shows the sine as +-10 V).
        assert abs(float(rec.volts[rec.trigger_index])) < 0.5
        assert rec.volts[rec.trigger_index - 5000] > 5  # 100 us before
        assert rec.volts[rec.trigger_index + 5000] < -5  # 100 us after
        s.write("scope", b":TRIGger:GLITch:LESSthan 100E-6;:TRIGger:SWEep AUTO;:DIGitize CHANnel1\n")
        s.write("scope", b"*OPC?\n")
        assert s.read("scope").strip() == b"1"
        assert not acq.records[1].triggered  # the auto timeout's record
    finally:
        s.close()
