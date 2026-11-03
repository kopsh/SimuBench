"""The per-source decomposition (D16/D37, #463): ``Solution.source_contributions`` says why a net
reads what it reads. Two Thevenin sources share one node through 50 Ω each into 100 Ω, so every
transfer is 0.4 and the numbers are hand-calculable."""

import numpy as np
import pytest

from simubench.engine.circuit import Circuit, SourceContribution
from simubench.engine.signal import Signal, sine


def two_source_circuit() -> tuple[Circuit, dict[str, Signal]]:
    c = Circuit()
    # Ideal sources (z = 0) behind 50 Ω each into the node, which carries 100 Ω to ground: every
    # source-to-node transfer is (1/50) / (1/50 + 1/50 + 1/100) = 0.4.
    c.source("A", "i1/src", 0.0)
    c.source("B", "i2/src", 0.0)
    c.resistor("A", "N", 50.0)
    c.resistor("B", "N", 50.0)
    c.resistor("N", "0", 100.0)
    emf = {
        "i1/src": Signal.constant(1.0) + sine(1e3, 0.5),
        "i2/src": Signal.constant(2.0),
        "i3/absent": Signal.constant(5.0),  # solved nowhere: not in the circuit
    }
    sol = c.solve(np.array([0.0, 1e3]))
    return sol, emf


def test_each_sources_dc_and_ac_share_matches_hand_calculation():
    sol, emf = two_source_circuit()
    out = sol.source_contributions("N", emf)
    assert [s.source for s in out] == ["i2/src", "i1/src"]  # by RMS contribution, largest first
    s2, s1 = out
    # V2 = 2 V DC through 50 Ω: gain 0.4, contribution 0.8 V, no AC lines.
    assert s2.instrument == "i2" and s2.component == "src"
    assert s2.dc_emf == pytest.approx(2.0)
    assert s2.dc_gain == pytest.approx(0.4)
    assert s2.dc_contribution == pytest.approx(0.8)
    assert s2.lines == []
    assert s2.rms_contribution == pytest.approx(0.8)
    # V1 = 1 V DC + 0.5 V peak at 1 kHz: the same 0.4 gain at both.
    assert s1.dc_contribution == pytest.approx(0.4)
    assert len(s1.lines) == 1
    line = s1.lines[0]
    assert line.freq_hz == pytest.approx(1e3)
    assert line.emf == pytest.approx(0.5)
    assert line.gain == pytest.approx(0.4)
    assert line.phase_deg == pytest.approx(0.0, abs=1e-9)  # resistive network: no phase shift
    assert line.contribution == pytest.approx(0.2)
    assert s1.rms_contribution == pytest.approx(np.sqrt(0.4**2 + 0.2**2 / 2))


def test_a_source_that_reaches_nowhere_is_left_out():
    sol, emf = two_source_circuit()
    out = sol.source_contributions("N", {"i3/absent": emf["i3/absent"]})
    assert out == []


def test_max_lines_truncates_the_strongest_lines():
    c = Circuit()
    c.source("A", "i1/src", 50.0)
    c.resistor("A", "N", 50.0)
    c.resistor("N", "0", 50.0)
    sol = c.solve(np.array([1e3, 2e3, 3e3, 4e3]))
    emf = {"i1/src": sine(1e3, 0.4) + sine(2e3, 0.3) + sine(3e3, 0.2) + sine(4e3, 0.1)}
    out = sol.source_contributions("N", emf, max_lines=2)
    assert [line.freq_hz for line in out[0].lines] == [1e3, 2e3]  # by contribution, strongest first
    assert isinstance(out[0], SourceContribution)
