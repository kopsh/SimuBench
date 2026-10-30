"""The scope's trigger path (issue 137, instrument-authoring.md 8.4, G5): the trigger is detected on
the analog signal with its own coupling and noise, interpolated between samples, and a normal or
single acquisition waits in virtual time for its edge."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from simubench.api import open_bench
from simubench.engine.signal import Signal, sine

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
        inst.components["adc"].overrides["bits"] = 0  # no quantization: the samples are the signal
        acq.overrides["trigger_jitter_s"] = jitter
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
        assert acq.trigger_ns is not None  # found, though a second of virtual time away
        # The edge from the bench's own signal: the 1 Hz sine's next rising zero crossing.
        net = s.net_signal(s.port_net["scope.CH1"])
        grid = np.arange(0, 2.0, 1e-4)
        v = net.sample_rel(start, grid)
        i = int(np.nonzero((v[:-1] < 0) & (v[1:] >= 0) & (grid[1:] > 5e-3))[0][0])
        edge = start + (grid[i] - v[i] / (v[i + 1] - v[i]) * 1e-4) * 1e9
        assert acq.trigger_ns == pytest.approx(edge, abs=1e3)  # to a microsecond
        assert acq.trigger_ns - start > 0.1e9
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
    """Time from one trigger to the next on a slow edge with trigger-path noise."""
    s = open_bench(CONTRACT)
    try:
        inst = s.instruments["scope"]
        trig = inst.components["trigger"]
        trig.overrides.update(noise_rms=0.05, hysteresis_divs=0.01, noise_reject=noise_reject)
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


def test_coupling_filters_reach_the_trigger_only():
    s = open_bench(CONTRACT)
    try:
        trig = s.instruments["scope"].components["trigger"]
        trig.overrides.update(filters={"AC": [["highpass", 8.0]], "HFREJect": [["lowpass", 500e3]]})
        sig = Signal.constant(2.0) + sine(1000.0, 0.5)
        trig.overrides["level"] = 1.0  # above the sine alone, crossed with its DC
        trig.overrides["coupling"] = "DC"
        assert trig.search(sig, 0, 1e-6, 10, 1000, None, 0.5) is None  # 1.5..2.5 V never falls below 1 V
        trig.overrides.update(coupling="AC", level=0.0)
        hit = trig.search(sig, 0, 1e-6, 10, 1000, None, 0.5)
        assert hit is not None  # without the DC the sine crosses 0 V
        fast = sine(5e6, 1.0)
        trig.overrides["coupling"] = "HFREJect"
        trig.overrides["level"] = 0.5
        assert trig.search(fast, 0, 1e-9, 10, 1000, None, 0.5) is None  # 5 MHz attenuated tenfold
    finally:
        s.close()


def test_line_and_external_sources():
    s = open_bench(CONTRACT)
    try:
        trig = s.instruments["scope"].components["trigger"]
        trig.overrides["source"] = "LINE"
        hit = trig.search(Signal.zero(), 0, 1e-5, 10, 1000, None, 0.5)
        assert hit.t_s == pytest.approx(0.02)  # the next 50 Hz zero crossing after the pre-trigger
        trig.overrides.update(source="EX", level=0.2)
        assert trig.source_kind() == "external"
    finally:
        s.close()


def test_holdoff_skips_edges_within_it():
    s = open_bench(CONTRACT)
    try:
        trig = s.instruments["scope"].components["trigger"]
        sig = sine(1000.0, 1.0)
        trig.overrides.update(source="C1", level=0.0, slope="POSitive")
        first = trig.search(sig, 0, 1e-6, 10, 1000, 1, 0.5)
        assert first.t_s == pytest.approx(1e-3, abs=1e-8)
        trig.last_ns = round(first.t_s * 1e9)
        trig.overrides["holdoff"] = 2.5e-3
        second = trig.search(sig, 0, 1e-6, 10, 1000, 1, 0.5, 1.0)
        assert second.t_s == pytest.approx(4e-3, abs=1e-8)  # 2 and 3 ms are within the holdoff
    finally:
        s.close()
